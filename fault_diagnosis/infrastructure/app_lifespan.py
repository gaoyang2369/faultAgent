from __future__ import annotations

import inspect
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langchain.agents import create_agent
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from ..agent_runtime.middleware import build_middleware
from ..common.logger import get_logger
from ..config import (
    APP_ENV,
    FRONTEND_ORIGINS,
    HAS_EXPLICIT_SESSION_SECRET,
    HAS_STABLE_SESSION_SECRET,
    IS_PRODUCTION,
    LOCAL_DEV_MODE,
    SESSION_SECRET_FINGERPRINT,
    SESSION_SECRET_SOURCE,
)
from .db_pool import close_pool, init_pool
from ..knowledge.base import get_knowledge_base_status, get_knowledge_retriever, has_knowledge_base_index
from ..runtime.dev_mode import init_dev_state
from ..tools import get_runtime_tools
from ..tools.sql_tools import get_sqltools
from ..prompts.dynamic_prompt import Context

_log = get_logger("app.lifespan")


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    manager = getattr(app.state, "session_scope_manager", None)
    session_secret_source = getattr(manager, "secret_source", SESSION_SECRET_SOURCE or "unknown")
    stable_session_secret = HAS_STABLE_SESSION_SECRET and not bool(
        getattr(manager, "uses_ephemeral_secret", not HAS_STABLE_SESSION_SECRET)
    )

    _log.info(
        "SESSION_SECRET status",
        configured=HAS_STABLE_SESSION_SECRET,
        explicit_configured=HAS_EXPLICIT_SESSION_SECRET,
        source=session_secret_source,
        stable_after_restart=stable_session_secret,
        fingerprint=SESSION_SECRET_FINGERPRINT,
    )

    if IS_PRODUCTION and not HAS_EXPLICIT_SESSION_SECRET:
        raise RuntimeError("生产环境必须显式配置 SESSION_SECRET，不能使用进程级临时密钥")

    if not stable_session_secret:
        _log.warning(
            "未配置 SESSION_SECRET，当前使用进程级临时密钥；请设置固定长随机值，否则服务重启后旧 cookie 与旧 thread 映射无法恢复",
            app_env=APP_ENV,
            secret_source=session_secret_source,
        )
    elif not HAS_EXPLICIT_SESSION_SECRET and session_secret_source == "local_dev_file":
        _log.info("开发环境未显式配置 SESSION_SECRET，已自动使用稳定的本地回退文件", secret_source=session_secret_source, fingerprint=SESSION_SECRET_FINGERPRINT)

    if IS_PRODUCTION and not FRONTEND_ORIGINS:
        _log.warning("生产环境未配置 FRONTEND_ORIGINS，跨域前端将无法携带凭据访问，仅支持同源部署")
    elif IS_PRODUCTION:
        localhost_origins = [origin for origin in FRONTEND_ORIGINS if "localhost" in origin or "127.0.0.1" in origin]
        if localhost_origins:
            _log.warning("生产环境 FRONTEND_ORIGINS 仍包含本地调试地址", origins=localhost_origins)

    if LOCAL_DEV_MODE:
        _log.info("Local development mode enabled; skipping external service initialization")
        init_dev_state(app)
        app.state.checkpointer = None
        app.state.agent = None
        app.state.pool = None
        yield
        _log.info("本地开发模式已关闭")
        return

    db_uri = f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"

    try:
        _log.info("真实模式启动：开始初始化应用", app_env=APP_ENV, local_dev_mode=False)
        await init_pool()

        try:
            if has_knowledge_base_index():
                get_knowledge_retriever(build_if_missing=False)
                kb_status = get_knowledge_base_status(load_check=True)
                _log.info(
                    "Knowledge base index preloaded",
                    build_mode=kb_status.get("build_mode"),
                    build_mode_source=kb_status.get("build_mode_source"),
                    document_count=kb_status.get("document_count"),
                    source_count=kb_status.get("source_count"),
                    detail=kb_status.get("detail"),
                )
            else:
                _log.warning("未检测到本地知识库索引；query_knowledge_base 将提示先执行 python rebuild_kb.py")
        except Exception as kb_error:
            _log.warning("知识库预加载失败", error=str(kb_error))

        async with AsyncConnectionPool(conninfo=db_uri, min_size=2, max_size=10, kwargs={"autocommit": True, "prepare_threshold": 0}) as pool:
            checkpointer = AsyncPostgresSaver(pool)
            setup_result = checkpointer.setup()
            if inspect.isawaitable(setup_result):
                await setup_result
            _log.info("PostgreSQL schema setup completed")

            middleware_list = build_middleware(app.state.summary_model)
            runtime_tools = get_runtime_tools()
            runtime_tools.extend(get_sqltools())

            agent = create_agent(
                model=app.state.chat_model,
                tools=runtime_tools,
                checkpointer=checkpointer,
                middleware=middleware_list,
                context_schema=Context,
            )
            _log.info("Agent initialized successfully")

            app.state.checkpointer = checkpointer
            app.state.agent = agent
            app.state.pool = pool

            _log.info("服务完成初始化并启动")
            yield

    except Exception as exc:
        _log.error("Application initialization failed", error=str(exc))
        raise RuntimeError(f"初始化检查点保存器失败: {str(exc)}") from exc
    finally:
        await close_pool()
        _log.info("服务已关闭，资源清理完成")
