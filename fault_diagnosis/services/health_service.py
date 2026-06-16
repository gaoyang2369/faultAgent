"""真实依赖健康检查；浅检查不触发 LLM 推理。"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import (
    ALLOW_DEFAULT_ADMIN_PASSWORD,
    AGENT_TRACE_BACKEND,
    AGENT_TRACE_CAPTURE_CONTENT,
    AGENT_TRACE_CONSOLE,
    AGENT_TRACE_CONSOLE_PREVIEW_CHARS,
    AGENT_TRACE_CONSOLE_VERBOSE,
    AGENT_TRACE_FLUSH_ON_RUN,
    AGENT_TRACE_LOCAL_LOG,
    AGENT_TRACE_LOCAL_LOG_PATH,
    ADMIN_PASSWORD,
    ADMIN_PASSWORD_IS_DEFAULT,
    ADMIN_UPLOAD_DIR,
    DEFAULT_ADMIN_PASSWORD,
    EMBEDDING_MODEL,
    FAISS_PATH,
    HAS_EXPLICIT_SESSION_SECRET,
    HAS_STABLE_SESSION_SECRET,
    HEALTHCHECK_TIMEOUT_SECONDS,
    MYSQL_USER,
    OLLAMA_BASE_URL,
    SESSION_SECRET_FINGERPRINT,
    SESSION_SECRET_SOURCE,
    UPLOADED_PDF_KB_ENABLE_VECTOR_INDEX,
)
from ..agent_runtime.error_classification import classify_model_gateway_error, model_error_code
from ..common.paths import REPORTS_DIR


def _redact(value: Any) -> str:
    text = str(value)
    for secret_name in (
        "OPENAI_API_KEY",
        "MYSQL_PW",
        "POSTGRES_PASSWORD",
        "SESSION_SECRET",
        "ADMIN_PASSWORD",
    ):
        secret = os.getenv(secret_name, "")
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[已脱敏]")
    return text[:500]


def _mask_host(host: str | None) -> str:
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        return ".".join(parts[:3] + ["xxx"])
    return host[:24] + ("..." if len(host) > 24 else "")


def _mask_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    host = _mask_host(parsed.hostname)
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{host}{port}{path}" if parsed.scheme else _mask_host(url)


def _configured(*names: str) -> bool:
    return all(bool(os.getenv(name, "").strip()) for name in names)


def _check_result(status: str, **kwargs: Any) -> dict[str, Any]:
    return {"status": status, **kwargs}


async def _with_timeout(label: str, timeout_seconds: float, coro):
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return _check_result("failed", failure_type="timeout", detail=f"{label} 超过 {timeout_seconds}s 未返回")
    except Exception as exc:
        return _check_result("failed", failure_type="exception", detail=_redact(exc))


async def _check_mysql(timeout_seconds: float, deep: bool) -> dict[str, Any]:
    configured = bool(MYSQL_USER.strip()) and _configured("HOST", "MYSQL_PW", "DB_NAME", "PORT")
    if not configured:
        return _check_result("not_configured", configured=False)
    if not deep:
        return _check_result(
            "available",
            configured=True,
            host=_mask_host(os.getenv("HOST")),
            database=os.getenv("DB_NAME", ""),
        )

    async def probe():
        from ..infrastructure.db_pool import get_pool

        pool = get_pool()
        conn = await pool.acquire()
        try:
            cursor = await conn.cursor()
            try:
                await cursor.execute("SELECT 1")
                row = await cursor.fetchone()
            finally:
                await cursor.close()
        finally:
            pool.release(conn)
        ok = bool(row and row[0] == 1)
        return _check_result(
            "available" if ok else "failed",
            configured=True,
            host=_mask_host(os.getenv("HOST")),
            database=os.getenv("DB_NAME", ""),
        )

    return await _with_timeout("MySQL 健康检查", timeout_seconds, probe())


async def _check_postgres(app, timeout_seconds: float, deep: bool) -> dict[str, Any]:
    configured = _configured("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    if not configured:
        return _check_result("not_configured", configured=False)
    if not deep:
        return _check_result(
            "available",
            configured=True,
            host=_mask_host(os.getenv("POSTGRES_HOST")),
            database=os.getenv("POSTGRES_DB", ""),
        )

    pool = getattr(app.state, "pool", None)
    if pool is None:
        return _check_result("degraded", configured=True, detail="PostgreSQL 连接池尚未初始化")

    async def probe():
        async with pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                row = await cursor.fetchone()
        ok = bool(row and row[0] == 1)
        return _check_result(
            "available" if ok else "failed",
            configured=True,
            host=_mask_host(os.getenv("POSTGRES_HOST")),
            database=os.getenv("POSTGRES_DB", ""),
        )

    return await _with_timeout("PostgreSQL 健康检查", timeout_seconds, probe())


async def _check_ollama(timeout_seconds: float, deep: bool) -> dict[str, Any]:
    configured = bool(OLLAMA_BASE_URL)
    if not configured:
        return _check_result("not_configured", configured=False, model=EMBEDDING_MODEL)
    if not deep:
        return _check_result(
            "available",
            configured=True,
            base_url=_mask_url(OLLAMA_BASE_URL),
            model=EMBEDDING_MODEL,
        )

    async def probe():
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(f"{OLLAMA_BASE_URL.rstrip('/')}/api/version")
        return _check_result(
            "available" if response.status_code < 500 else "failed",
            configured=True,
            base_url=_mask_url(OLLAMA_BASE_URL),
            model=EMBEDDING_MODEL,
            http_status=response.status_code,
        )

    return await _with_timeout("Ollama 健康检查", timeout_seconds, probe())


async def _check_llm(timeout_seconds: float, deep: bool) -> dict[str, Any]:
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("MODEL_NAME", "").strip()
    configured = bool(base_url and api_key and model)
    result = {
        "configured": configured,
        "base_url": _mask_url(base_url),
        "model_configured": bool(model),
        "api_key_configured": bool(api_key),
    }
    if not configured:
        return _check_result("not_configured", **result)
    if not deep:
        return _check_result("available", **result)

    async def probe():
        headers = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            models_response = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
            if models_response.status_code in {401, 403}:
                classified = classify_model_gateway_error(f"{models_response.status_code} {models_response.text}")
                category, message = classified or ("model_auth", "网关可达，但鉴权未通过；未执行 LLM 推理")
                return _check_result(
                    "failed",
                    **result,
                    reachable=True,
                    inference_checked=False,
                    http_status=models_response.status_code,
                    failure_type=category,
                    code=model_error_code(category),
                    detail=message,
                )

            reachable = models_response.status_code < 500
            if not reachable:
                return _check_result(
                    "failed",
                    **result,
                    reachable=False,
                    inference_checked=False,
                    http_status=models_response.status_code,
                    detail="模型网关不可达；未执行 LLM 推理",
                )

            inference_response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                    "stream": False,
                },
            )

        if inference_response.status_code >= 400:
            classified = classify_model_gateway_error(f"{inference_response.status_code} {inference_response.text}")
            if classified:
                category, message = classified
                return _check_result(
                    "failed",
                    **result,
                    reachable=True,
                    inference_checked=True,
                    http_status=inference_response.status_code,
                    failure_type=category,
                    code=model_error_code(category),
                    detail=message,
                )
            return _check_result(
                "failed",
                **result,
                reachable=True,
                inference_checked=True,
                http_status=inference_response.status_code,
                failure_type="inference_failed",
                detail=f"LLM 推理探测失败，HTTP {inference_response.status_code}",
            )

        return _check_result(
            "available",
            **result,
            reachable=True,
            inference_checked=True,
            http_status=inference_response.status_code,
            detail="网关可达；最小推理探测通过，复杂请求额度以实际调用结果为准",
        )

    return await _with_timeout("LLM 网关健康检查", timeout_seconds, probe())


def _check_session_secret(app) -> dict[str, Any]:
    manager = getattr(app.state, "session_scope_manager", None)
    source = getattr(manager, "secret_source", SESSION_SECRET_SOURCE or "unknown")
    ephemeral = bool(getattr(manager, "uses_ephemeral_secret", not HAS_STABLE_SESSION_SECRET))
    stable_after_restart = HAS_STABLE_SESSION_SECRET and not ephemeral
    if stable_after_restart and source == "local_dev_file":
        detail = "已使用稳定的本地开发 SESSION_SECRET 文件；默认启动重启后可继续验签旧 cookie / thread"
    elif stable_after_restart:
        detail = "已配置固定 SESSION_SECRET"
    else:
        detail = "未配置固定 SESSION_SECRET，重启后旧 cookie / thread 签名不可恢复"
    return _check_result(
        "available" if stable_after_restart else "degraded",
        configured=HAS_STABLE_SESSION_SECRET,
        explicit_configured=HAS_EXPLICIT_SESSION_SECRET,
        source=source,
        stable_after_restart=stable_after_restart,
        fingerprint=SESSION_SECRET_FINGERPRINT,
        detail=detail,
    )


def _check_faiss(deep: bool) -> dict[str, Any]:
    from ..knowledge.base import get_knowledge_base_status

    status = get_knowledge_base_status(load_check=deep)
    exists = bool(status["exists"])
    loadable = status.get("loadable")
    result_status = "not_configured"
    if exists and (not deep or loadable is True):
        result_status = "available"
    elif exists and loadable is False:
        result_status = "failed"
    return _check_result(
        result_status,
        **status,
    )


def _check_medicine_ocr() -> dict[str, Any]:
    try:
        from ..integrations.medicine_ocr_runtime import get_medicine_ocr_status

        status = get_medicine_ocr_status(load_tested=False)
        if status["backend_available"] and status["heavy_model_enabled"]:
            result_status = "available"
        elif status["heavy_model_enabled"] and not status["backend_available"]:
            result_status = "degraded"
        elif status["configured"] or status["root_exists"]:
            result_status = "degraded"
        else:
            result_status = "not_configured"
        return _check_result(result_status, **status)
    except Exception as exc:
        return _check_result(
            "failed",
            provider="medicine_ocr",
            detail=f"OCR 依赖加载失败：{_redact(exc)}",
        )


def _check_reports_directory() -> dict[str, Any]:
    exists = os.path.exists(REPORTS_DIR)
    is_dir = os.path.isdir(REPORTS_DIR)
    writable = os.access(REPORTS_DIR, os.W_OK) if exists and is_dir else False
    if not exists:
        status = "not_configured"
        detail = "报告目录不存在，报告保存和 /reports 静态访问可能不可用"
    elif not is_dir:
        status = "failed"
        detail = "报告路径存在但不是目录"
    elif not writable:
        status = "failed"
        detail = "报告目录不可写"
    else:
        status = "available"
        detail = "报告目录可用"
    return _check_result(
        status,
        path=REPORTS_DIR,
        exists=exists,
        is_dir=is_dir,
        writable=writable,
        public_mount="/reports",
        detail=detail,
    )


def _check_uploaded_pdf_kb() -> dict[str, Any]:
    from ..knowledge.uploaded_pdf_kb import get_uploaded_pdf_kb_status, has_uploaded_pdf_corpus, has_uploaded_pdf_index

    status = get_uploaded_pdf_kb_status()
    corpus_exists = has_uploaded_pdf_corpus()
    vector_index_exists = has_uploaded_pdf_index()
    meta_exists = os.path.exists(os.path.join(ADMIN_UPLOAD_DIR, "uploaded_pdf_kb", "kb_meta.json"))
    mode = str(status.get("mode") or ("faiss" if vector_index_exists else "lexical_corpus" if corpus_exists else "empty"))
    vector_error = _redact(status.get("vector_error", ""))

    if vector_index_exists:
        result_status = "available"
        detail = "上传 PDF 向量索引可用"
    elif corpus_exists:
        result_status = "degraded" if UPLOADED_PDF_KB_ENABLE_VECTOR_INDEX else "available"
        detail = "上传 PDF 词法检索兜底可用"
    elif meta_exists or mode not in {"empty", "missing"}:
        result_status = "degraded"
        detail = "上传 PDF 知识库元信息存在，但当前没有可查询索引或词法语料"
    else:
        result_status = "not_configured"
        detail = "尚未构建上传 PDF 知识库"

    payload = {
        key: value
        for key, value in status.items()
        if key not in {"vector_error", "mode", "detail", "configured"}
    }
    return _check_result(
        result_status,
        **payload,
        configured=meta_exists or corpus_exists or vector_index_exists,
        upload_dir=ADMIN_UPLOAD_DIR,
        vector_index_enabled=UPLOADED_PDF_KB_ENABLE_VECTOR_INDEX,
        vector_index_exists=vector_index_exists,
        lexical_corpus_exists=corpus_exists,
        mode=mode,
        vector_error_present=bool(vector_error),
        detail=detail,
    )


def _check_history_index(app) -> dict[str, Any]:
    try:
        from ..repositories.history_index import get_history_index_repository

        repository = getattr(app.state, "history_index_repository", None) or get_history_index_repository()
        payload = repository.health_check()
        status = payload.get("status") or "available"
        return _check_result(status, **{key: value for key, value in payload.items() if key != "status"})
    except Exception as exc:
        return _check_result("failed", detail=_redact(exc))


def _check_diagnosis_artifact_store() -> dict[str, Any]:
    try:
        from ..diagnosis.artifact_store import check_artifact_store_health

        payload = check_artifact_store_health()
        status = payload.get("status") or "available"
        return _check_result(status, **{key: value for key, value in payload.items() if key != "status"})
    except Exception as exc:
        return _check_result("failed", detail=_redact(exc))


def _check_governance_repository() -> dict[str, Any]:
    try:
        from ..repositories.governance_repository import FileGovernanceRepository

        payload = FileGovernanceRepository().health_check()
        status = payload.get("status") or "available"
        return _check_result(status, **{key: value for key, value in payload.items() if key != "status"})
    except Exception as exc:
        return _check_result("failed", detail=_redact(exc))


def _check_admin_pdf_registry() -> dict[str, Any]:
    try:
        from ..repositories.admin_pdf_repository import get_admin_pdf_repository

        payload = get_admin_pdf_repository().health_check()
        status = payload.get("status") or "available"
        return _check_result(status, **{key: value for key, value in payload.items() if key != "status"})
    except Exception as exc:
        return _check_result("failed", detail=_redact(exc))


def _check_admin_password() -> dict[str, Any]:
    explicit_configured = _configured("ADMIN_PASSWORD")
    uses_default_password = ADMIN_PASSWORD_IS_DEFAULT
    if uses_default_password:
        status = "degraded" if ALLOW_DEFAULT_ADMIN_PASSWORD else "failed"
        detail = (
            "管理员密码仍为默认值，仅允许本地开发使用"
            if ALLOW_DEFAULT_ADMIN_PASSWORD
            else "管理员默认密码已被当前运行环境拒绝，请通过 ADMIN_PASSWORD 覆盖"
        )
    elif not explicit_configured:
        status = "not_configured"
        detail = "未检测到显式 ADMIN_PASSWORD 配置"
    else:
        status = "available"
        detail = "管理员密码已显式配置且不是默认值"
    return _check_result(
        status,
        configured=explicit_configured,
        default_password_active=uses_default_password,
        default_password_allowed=ALLOW_DEFAULT_ADMIN_PASSWORD,
        username_configured=bool(os.getenv("ADMIN_USERNAME", "").strip()),
        detail=detail,
    )


def _check_trace_exporter() -> dict[str, Any]:
    backend = AGENT_TRACE_BACKEND
    if backend != "langfuse":
        return _check_result(
            "not_configured",
            backend=backend,
            capture_content=AGENT_TRACE_CAPTURE_CONTENT,
            flush_on_run=AGENT_TRACE_FLUSH_ON_RUN,
            local_log=AGENT_TRACE_LOCAL_LOG,
            local_log_path=AGENT_TRACE_LOCAL_LOG_PATH,
            console_log=AGENT_TRACE_CONSOLE,
            console_verbose=AGENT_TRACE_CONSOLE_VERBOSE,
            console_preview_chars=AGENT_TRACE_CONSOLE_PREVIEW_CHARS,
            detail="trace export disabled",
        )

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    host = os.getenv("LANGFUSE_HOST", "").strip()
    base_url = os.getenv("LANGFUSE_BASE_URL", "").strip()
    if not (public_key and secret_key):
        return _check_result(
            "not_configured",
            backend=backend,
            public_key_configured=bool(public_key),
            secret_key_configured=bool(secret_key),
            host_configured=bool(host or base_url),
            capture_content=AGENT_TRACE_CAPTURE_CONTENT,
            flush_on_run=AGENT_TRACE_FLUSH_ON_RUN,
            local_log=AGENT_TRACE_LOCAL_LOG,
            local_log_path=AGENT_TRACE_LOCAL_LOG_PATH,
            console_log=AGENT_TRACE_CONSOLE,
            console_verbose=AGENT_TRACE_CONSOLE_VERBOSE,
            console_preview_chars=AGENT_TRACE_CONSOLE_PREVIEW_CHARS,
            detail="LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 未配置",
        )
    try:
        import langfuse  # noqa: F401
    except Exception as exc:
        return _check_result(
            "failed",
            backend=backend,
            public_key_configured=True,
            secret_key_configured=True,
            host_configured=bool(host or base_url),
            capture_content=AGENT_TRACE_CAPTURE_CONTENT,
            flush_on_run=AGENT_TRACE_FLUSH_ON_RUN,
            local_log=AGENT_TRACE_LOCAL_LOG,
            local_log_path=AGENT_TRACE_LOCAL_LOG_PATH,
            console_log=AGENT_TRACE_CONSOLE,
            console_verbose=AGENT_TRACE_CONSOLE_VERBOSE,
            console_preview_chars=AGENT_TRACE_CONSOLE_PREVIEW_CHARS,
            detail=_redact(exc),
        )
    return _check_result(
        "available",
        backend=backend,
        public_key_configured=True,
        secret_key_configured=True,
        host_configured=bool(host or base_url),
        capture_content=AGENT_TRACE_CAPTURE_CONTENT,
        flush_on_run=AGENT_TRACE_FLUSH_ON_RUN,
        local_log=AGENT_TRACE_LOCAL_LOG,
        local_log_path=AGENT_TRACE_LOCAL_LOG_PATH,
        console_log=AGENT_TRACE_CONSOLE,
        console_verbose=AGENT_TRACE_CONSOLE_VERBOSE,
        console_preview_chars=AGENT_TRACE_CONSOLE_PREVIEW_CHARS,
        detail="Langfuse SDK 已可用，等待运行时 trace 写入",
    )


def _overall_status(checks: dict[str, dict[str, Any]]) -> str:
    statuses = {check.get("status") for check in checks.values()}
    if "failed" in statuses:
        return "failed"
    if statuses & {"degraded", "not_configured"}:
        return "degraded"
    return "available"


async def build_dependencies_health(app, deep: bool = True, timeout_seconds: float | None = None) -> dict[str, Any]:
    """汇总真实依赖状态；浅检查不发起 LLM 推理，深检查会做极小 LLM 探测。"""

    timeout_seconds = timeout_seconds or HEALTHCHECK_TIMEOUT_SECONDS
    checks = {
        "session_secret": _check_session_secret(app),
        "history_index": _check_history_index(app),
        "diagnosis_artifact_store": _check_diagnosis_artifact_store(),
        "faiss": _check_faiss(deep=deep),
        "reports_directory": _check_reports_directory(),
        "governance_repository": _check_governance_repository(),
        "admin_pdf_registry": _check_admin_pdf_registry(),
        "uploaded_pdf_kb": _check_uploaded_pdf_kb(),
        "admin_password": _check_admin_password(),
        "trace_exporter": _check_trace_exporter(),
        "medicine_ocr": _check_medicine_ocr(),
        "mysql": await _check_mysql(timeout_seconds, deep=deep),
        "postgresql": await _check_postgres(app, timeout_seconds, deep=deep),
        "ollama": await _check_ollama(timeout_seconds, deep=deep),
        "llm": await _check_llm(timeout_seconds, deep=deep),
    }
    return {
        "status": _overall_status(checks),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "deep": deep,
        "timeout_seconds": timeout_seconds,
        "checks": checks,
    }
