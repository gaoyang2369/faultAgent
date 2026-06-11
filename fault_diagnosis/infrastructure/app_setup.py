from __future__ import annotations

import re
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..auth.session_scope import SessionScopeManager
from ..common.logger import bind_request_id, get_logger, new_request_id
from ..config import FRONTEND_ORIGINS, SESSION_SECRET, SESSION_SECRET_SOURCE

_http_log = get_logger("http")
_QUIET_PATH_PREFIXES = ("/static", "/assets", "/images", "/reports", "/favicon.ico")
_REQUEST_ID_RE = re.compile(r"[^A-Za-z0-9._:-]+")


def build_session_scope_manager() -> SessionScopeManager:
    return SessionScopeManager(
        SESSION_SECRET or None,
        secret_source=SESSION_SECRET_SOURCE if SESSION_SECRET else None,
    )


def configure_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def configure_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_logging_middleware(request, call_next):
        request_id = (
            _normalize_incoming_request_id(request.headers.get("x-request-id"))
            or new_request_id()
        )
        bind_request_id(request_id)
        request.state.request_id = request_id

        started_at = time.monotonic()
        path = request.url.path
        quiet_path = path.startswith(_QUIET_PATH_PREFIXES)
        client_host = request.client.host if request.client else None

        if not quiet_path:
            _http_log.info(
                "HTTP request started",
                method=request.method,
                path=path,
                client=client_host,
            )

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.monotonic() - started_at) * 1000, 1)
            _http_log.exception(
                "HTTP request failed",
                method=request.method,
                path=path,
                client=client_host,
                duration_ms=duration_ms,
                error=str(exc),
            )
            raise

        duration_ms = round((time.monotonic() - started_at) * 1000, 1)
        response.headers["X-Request-ID"] = request_id

        if not quiet_path:
            log_method = _http_log.warning if response.status_code >= 500 else _http_log.info
            log_method(
                "HTTP request completed",
                method=request.method,
                path=path,
                status_code=response.status_code,
                duration_ms=duration_ms,
                client=client_host,
            )
        return response


def _normalize_incoming_request_id(value: str | None) -> str:
    normalized = _REQUEST_ID_RE.sub("", str(value or "").strip())
    return normalized[:64]
