"""HTTP API 共享的会话 scope 与管理员身份辅助函数。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from fault_diagnosis.server.auth.admin_auth import (
    DEV_AUTH_COOKIE_NAME,
    attach_admin_auth_cookie,
    attach_dev_auth_cookie,
    attach_user_auth_cookie,
    clear_admin_auth_cookie,
    clear_dev_auth_cookie,
    clear_user_auth_cookie,
    resolve_auth_context,
    resolve_identity_payload,
    verify_dev_auth_token,
)
from fault_diagnosis.server.auth.session_scope import SessionScopeManager, resolve_request_scope


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
        clear_user_auth_cookie(response)
        clear_dev_auth_cookie(response)
        attach_admin_auth_cookie(response, session_id, admin_username)
    return response


def json_response_with_scope_and_user(
    request: Request,
    content: Any,
    *,
    user_id: str | None = None,
    auth_method: str = "password",
    clear_auth_cookies_after_response: bool = False,
    status_code: int = 200,
) -> JSONResponse:
    manager, session_id, _, legacy_bindings = resolve_request_scope(request)
    response = JSONResponse(status_code=status_code, content=content)
    manager.attach_scope_cookies(response, session_id, legacy_bindings)
    if clear_auth_cookies_after_response:
        clear_user_auth_cookie(response)
        clear_admin_auth_cookie(response)
        clear_dev_auth_cookie(response)
    elif user_id:
        attach_user_auth_cookie(response, session_id, user_id, auth_method=auth_method)
        clear_admin_auth_cookie(response)
        clear_dev_auth_cookie(response)
    return response


def json_response_with_scope_and_dev(
    request: Request,
    content: Any,
    *,
    role: str,
    user_id: str | None = None,
    asset_scope: list[str] | None = None,
    allowed_tables: list[str] | None = None,
    status_code: int = 200,
) -> JSONResponse:
    manager, current_session_id, _, legacy_bindings = resolve_request_scope(request)
    current_dev_identity = verify_dev_auth_token(
        request.cookies.get(DEV_AUTH_COOKIE_NAME),
        current_session_id,
    )

    # Preserve the currently active role's session on first migration from the
    # old single-session implementation.  Subsequent switches can then restore
    # that role's own history instead of creating a fresh, unreachable scope.
    if current_dev_identity:
        current_role = str(current_dev_identity.get("role") or "").strip().lower()
        if current_role in {"guest", "engineer", "admin"}:
            current_cookie_name = manager.dev_session_cookie_name(current_role)
            if manager.verify_session_token(request.cookies.get(current_cookie_name)) != current_session_id:
                request_current_role_session = current_session_id
            else:
                request_current_role_session = None
        else:
            request_current_role_session = None
    else:
        current_role = ""
        request_current_role_session = None

    selected_cookie_name = manager.dev_session_cookie_name(role)
    session_id = manager.verify_session_token(request.cookies.get(selected_cookie_name))
    if not session_id:
        # Preserve the active session when the role cookie is being lazily
        # backfilled (including same-role login after upgrading from the old
        # single-session cookie format).
        if current_dev_identity and current_role == str(role).lower():
            session_id = current_session_id
        else:
            session_id = manager.issue_session_id()
    if session_id == current_session_id:
        legacy_bindings = legacy_bindings if current_role == str(role).lower() else {}
    else:
        legacy_bindings = {}
    response = JSONResponse(status_code=status_code, content=content)
    manager.attach_scope_cookies(response, session_id, legacy_bindings)
    manager.attach_dev_session_cookie(response, role, session_id)
    if request_current_role_session:
        manager.attach_dev_session_cookie(response, current_role, request_current_role_session)
    clear_user_auth_cookie(response)
    clear_admin_auth_cookie(response)
    attach_dev_auth_cookie(
        response,
        session_id,
        role,
        user_id=user_id,
        asset_scope=asset_scope,
        allowed_tables=allowed_tables,
    )
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


def resolve_request_auth_context(request: Request):
    session_manager, session_id, _, legacy_bindings = resolve_request_scope(request)
    auth_context = resolve_auth_context(request, session_id)
    return session_manager, session_id, legacy_bindings, auth_context
