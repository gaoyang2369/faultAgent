"""管理员身份相关 HTTP 路由。"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..auth.admin_auth import ADMIN_USERNAME, is_valid_admin_credentials
from ._shared import (
    json_response_with_scope,
    json_response_with_scope_and_admin,
    resolve_request_identity,
)

router = APIRouter()


class AdminLoginPayload(BaseModel):
    username: str
    password: str


@router.get("/auth/identity")
async def get_current_identity(request: Request):
    """返回当前会话的管理员身份状态。"""
    _, _, _, identity = resolve_request_identity(request)
    return json_response_with_scope(request, identity)


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

    response_payload = {
        "user_id": ADMIN_USERNAME,
        "user_role": "管理员",
        "is_admin": True,
        "auth_method": "password",
        "available_auth_methods": ["password", "voice_pending"],
    }
    return json_response_with_scope_and_admin(
        request,
        response_payload,
        admin_username=ADMIN_USERNAME,
    )


@router.post("/auth/logout")
async def admin_logout(request: Request):
    """退出当前管理员态，仅清理管理员认证 cookie。"""
    response_payload = {
        "user_id": "guest",
        "user_role": "访客",
        "is_admin": False,
        "auth_method": None,
        "available_auth_methods": ["password", "voice_pending"],
    }
    return json_response_with_scope_and_admin(
        request,
        response_payload,
        clear_admin_cookie_after_response=True,
    )
