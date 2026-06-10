"""HTTP API 共享的会话 scope 与管理员身份辅助函数。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from ..auth.admin_auth import attach_admin_auth_cookie, clear_admin_auth_cookie, resolve_identity_payload
from ..auth.session_scope import SessionScopeManager, resolve_request_scope


def json_response_with_scope(
    request: Request,
    content: Any,
    status_code: int = 200,
    background: Any | None = None,
) -> JSONResponse:
    manager, session_id, _, legacy_bindings = resolve_request_scope(request)
    response = JSONResponse(status_code=status_code, content=content, background=background)
    manager.attach_scope_cookies(response, session_id, legacy_bindings)
    return response


def json_response_with_scope_and_admin(
    request: Request,
    content: Any,
    status_code: int = 200,
    admin_username: str | None = None,
    clear_admin_cookie_after_response: bool = False,
) -> JSONResponse:
    manager, session_id, _, legacy_bindings = resolve_request_scope(request)
    response = JSONResponse(status_code=status_code, content=content)
    manager.attach_scope_cookies(response, session_id, legacy_bindings)
    if clear_admin_cookie_after_response:
        clear_admin_auth_cookie(response)
    elif admin_username:
        attach_admin_auth_cookie(response, session_id, admin_username)
    return response


def resolve_request_identity(request: Request) -> tuple[SessionScopeManager, str, dict, dict]:
    session_manager, session_id, _, legacy_bindings = resolve_request_scope(request)
    identity = resolve_identity_payload(request, session_id)
    return session_manager, session_id, legacy_bindings, identity


def require_admin_identity(request: Request) -> tuple[SessionScopeManager, str, dict, dict]:
    session_manager, session_id, legacy_bindings, identity = resolve_request_identity(request)
    if not identity.get("is_admin"):
        raise HTTPException(status_code=403, detail="当前请求需要管理员身份。")
    return session_manager, session_id, legacy_bindings, identity
