"""管理员身份相关 HTTP 路由。"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config
from ..auth.admin_auth import ADMIN_USERNAME, is_valid_admin_credentials
from ..repositories.user_repository import FileUserRepository
from ..security.contracts import Role
from ..security.permissions import build_auth_context, build_dev_auth_context
from ._shared import (
    json_response_with_scope,
    json_response_with_scope_and_admin,
    json_response_with_scope_and_dev,
    json_response_with_scope_and_user,
    resolve_request_identity,
)

router = APIRouter()


class AdminLoginPayload(BaseModel):
    username: str
    password: str


class LoginPayload(BaseModel):
    username: str
    password: str


class DevLoginPayload(BaseModel):
    role: Role


@router.get("/auth/identity")
async def get_current_identity(request: Request):
    """返回当前会话的管理员身份状态。"""
    _, _, _, identity = resolve_request_identity(request)
    return json_response_with_scope(request, identity)


@router.post("/auth/login")
async def login(request: Request, payload: LoginPayload):
    """Authenticate a file-backed engineer account and issue a signed cookie."""

    user = FileUserRepository().authenticate(payload.username, payload.password)
    if user is None:
        return json_response_with_scope(
            request,
            {"detail": "用户名或密码错误。"},
            status_code=401,
        )
    auth_context = build_auth_context(
        user_id=user.user_id,
        display_name=user.display_name,
        role=user.role,
        asset_scope=user.asset_scope,
        table_scope=user.allowed_tables,
        system_scope=user.system_scope,
        location_scope=user.location_scope,
        kb_scopes=user.kb_scopes,
        auth_method="password",
    )
    return json_response_with_scope_and_user(
        request,
        auth_context.identity_payload(),
        user_id=user.user_id,
    )


@router.post("/auth/dev-login")
async def dev_login(request: Request):
    """Issue a signed, server-defined identity for local authorization acceptance."""

    if not config.DEV_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        payload = DevLoginPayload.model_validate(await request.json())
    except Exception as exc:
        raise HTTPException(status_code=422, detail="role must be guest, engineer or admin") from exc
    auth_context = build_dev_auth_context(payload.role)
    return json_response_with_scope_and_dev(
        request,
        auth_context.identity_payload(),
        role=payload.role,
    )


@router.post("/auth/admin/login")
async def admin_login(request: Request, payload: AdminLoginPayload):
    """使用用户名和密码建立最小管理员会话。"""
    if not is_valid_admin_credentials(payload.username, payload.password):
        return json_response_with_scope(
            request,
            {
                "detail": "用户名或密码错误。",
            },
            status_code=401,
        )

    response_payload = build_auth_context(
        user_id=ADMIN_USERNAME,
        display_name="管理员",
        role="admin",
        auth_method="password",
    ).identity_payload()
    return json_response_with_scope_and_admin(
        request,
        response_payload,
        admin_username=ADMIN_USERNAME,
    )


@router.post("/auth/logout")
async def admin_logout(request: Request):
    """Clear both legacy admin and common user authentication cookies."""
    response_payload = build_auth_context(user_id="guest", display_name="访客").identity_payload()
    return json_response_with_scope_and_user(
        request,
        response_payload,
        clear_auth_cookies_after_response=True,
    )
