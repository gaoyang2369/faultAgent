"""后端服务启动兼容层。"""

from __future__ import annotations

import asyncio
import selectors
import sys
from typing import Any

import uvicorn

from ..common.encoding import ensure_utf8_stdio

_WINDOWS = sys.platform == "win32"


def configure_windows_event_loop_policy() -> None:
    """在 Windows 下切换为 psycopg 兼容的事件循环策略。"""

    if not _WINDOWS:
        return
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def run_backend_server(
    app: Any,
    *,
    app_import_path: str,
    host: str,
    port: int,
    reload: bool,
    reload_excludes: list[str] | None = None,
    log_level: str = "info",
) -> None:
    """统一后端启动逻辑，Windows 走兼容 Runner，其它平台保持原行为。"""

    ensure_utf8_stdio()

    if _WINDOWS:
        config = uvicorn.Config(app, host=host, port=port, log_level=log_level, reload=False, access_log=False)
        server = uvicorn.Server(config)
        with asyncio.Runner(loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) as runner:
            runner.run(server.serve())
        return

    run_kwargs = {
        "host": host,
        "port": port,
        "log_level": log_level,
        "access_log": False,
    }
    if reload:
        run_kwargs["reload"] = True
        if reload_excludes:
            run_kwargs["reload_excludes"] = reload_excludes
        uvicorn.run(app_import_path, **run_kwargs)
        return

    uvicorn.run(app, **run_kwargs)
