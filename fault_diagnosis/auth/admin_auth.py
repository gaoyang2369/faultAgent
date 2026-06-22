"""管理员最小认证 cookie 与身份解析工具。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import Request, Response
else:
    Request = Any
    Response = Any

from ..config import (
    ADMIN_AUTH_MAX_AGE,
    ADMIN_PASSWORD_IS_DEFAULT,
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    ALLOW_DEFAULT_ADMIN_PASSWORD,
    SESSION_COOKIE_DOMAIN,
    SESSION_COOKIE_PATH,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
    SESSION_SECRET,
)
from .. import config
from ..repositories.user_repository import FileUserRepository
from ..security.contracts import AuthContext
from ..security.permissions import build_auth_context, build_dev_auth_context


ADMIN_AUTH_COOKIE_NAME = "fd_admin_auth"
ADMIN_AUTH_COOKIE_SALT = "fd-admin-auth-v1"
USER_AUTH_COOKIE_NAME = "fd_user_auth"
USER_AUTH_COOKIE_SALT = "fd-user-auth-v1"
DEV_AUTH_COOKIE_NAME = "fd_dev_auth"
DEV_AUTH_COOKIE_SALT = "fd-dev-auth-v1"

_AUTH_SECRET = (SESSION_SECRET or "fd-admin-auth-local-fallback").encode("utf-8")


def _sign(payload: str) -> str:
    return hmac.new(_AUTH_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _encode_payload(data: dict[str, str | int]) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_payload(token: str) -> dict[str, str | int]:
    padded = token + "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    decoded = json.loads(raw.decode("utf-8"))
    return decoded if isinstance(decoded, dict) else {}


def is_valid_admin_credentials(username: str, password: str) -> bool:
    if ADMIN_PASSWORD_IS_DEFAULT and not ALLOW_DEFAULT_ADMIN_PASSWORD:
        return False
    normalized_username = (username or "").strip()
    normalized_password = password or ""
    return (
        hmac.compare_digest(normalized_username, ADMIN_USERNAME)
        and hmac.compare_digest(normalized_password, ADMIN_PASSWORD)
    )


def issue_admin_auth_token(session_id: str, username: str, auth_method: str = "password") -> str:
    issued_at = int(time.time())
    payload = _encode_payload(
        {
            "sid": session_id,
            "uid": username,
            "method": auth_method,
            "iat": issued_at,
        }
    )
    signature = _sign(f"{ADMIN_AUTH_COOKIE_SALT}:{payload}")
    return f"{payload}.{signature}"


def verify_admin_auth_token(token: str | None, session_id: str) -> dict[str, str] | None:
    if not token or "." not in token:
        return None

    payload, signature = token.rsplit(".", 1)
    expected = _sign(f"{ADMIN_AUTH_COOKIE_SALT}:{payload}")
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        decoded = _decode_payload(payload)
    except Exception:
        return None

    token_session_id = str(decoded.get("sid", "")).strip()
    token_username = str(decoded.get("uid", "")).strip()
    auth_method = str(decoded.get("method", "")).strip() or "password"
    issued_at = int(decoded.get("iat", 0) or 0)

    if token_session_id != session_id:
        return None
    if token_username != ADMIN_USERNAME:
        return None
    if issued_at <= 0 or int(time.time()) - issued_at > ADMIN_AUTH_MAX_AGE:
        return None

    return {
        "user_id": token_username,
        "user_role": "管理员",
        "is_admin": True,
        "auth_method": auth_method,
        "available_auth_methods": ["password", "voice_pending"],
    }


def issue_user_auth_token(session_id: str, user_id: str, auth_method: str = "password") -> str:
    issued_at = int(time.time())
    payload = _encode_payload({"sid": session_id, "uid": user_id, "method": auth_method, "iat": issued_at})
    signature = _sign(f"{USER_AUTH_COOKIE_SALT}:{payload}")
    return f"{payload}.{signature}"


def verify_user_auth_token(token: str | None, session_id: str) -> dict[str, str] | None:
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    expected = _sign(f"{USER_AUTH_COOKIE_SALT}:{payload}")
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        decoded = _decode_payload(payload)
        issued_at = int(decoded.get("iat", 0) or 0)
    except (TypeError, ValueError):
        return None
    if str(decoded.get("sid", "")).strip() != session_id:
        return None
    if issued_at <= 0 or int(time.time()) - issued_at > ADMIN_AUTH_MAX_AGE:
        return None
    user_id = str(decoded.get("uid", "")).strip()
    if not user_id:
        return None
    return {
        "user_id": user_id,
        "auth_method": str(decoded.get("method", "")).strip() or "password",
    }


def issue_dev_auth_token(session_id: str, role: str) -> str:
    if role not in {"guest", "engineer", "admin"}:
        raise ValueError("unsupported development role")
    payload = _encode_payload(
        {"sid": session_id, "role": role, "method": "dev-login", "iat": int(time.time())}
    )
    signature = _sign(f"{DEV_AUTH_COOKIE_SALT}:{payload}")
    return f"{payload}.{signature}"


def verify_dev_auth_token(token: str | None, session_id: str) -> dict[str, str] | None:
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    expected = _sign(f"{DEV_AUTH_COOKIE_SALT}:{payload}")
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        decoded = _decode_payload(payload)
        issued_at = int(decoded.get("iat", 0) or 0)
    except (TypeError, ValueError):
        return None
    role = str(decoded.get("role", "")).strip()
    if str(decoded.get("sid", "")).strip() != session_id or role not in {"guest", "engineer", "admin"}:
        return None
    if issued_at <= 0 or int(time.time()) - issued_at > ADMIN_AUTH_MAX_AGE:
        return None
    return {"role": role, "auth_method": "dev-login"}


def attach_admin_auth_cookie(
    response: Response,
    session_id: str,
    username: str,
    auth_method: str = "password",
) -> None:
    response.set_cookie(
        key=ADMIN_AUTH_COOKIE_NAME,
        value=issue_admin_auth_token(session_id, username, auth_method=auth_method),
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
        max_age=ADMIN_AUTH_MAX_AGE,
        domain=SESSION_COOKIE_DOMAIN,
        path=SESSION_COOKIE_PATH,
    )


def clear_admin_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=ADMIN_AUTH_COOKIE_NAME,
        domain=SESSION_COOKIE_DOMAIN,
        path=SESSION_COOKIE_PATH,
    )


def attach_user_auth_cookie(
    response: Response,
    session_id: str,
    user_id: str,
    auth_method: str = "password",
) -> None:
    response.set_cookie(
        key=USER_AUTH_COOKIE_NAME,
        value=issue_user_auth_token(session_id, user_id, auth_method=auth_method),
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
        max_age=ADMIN_AUTH_MAX_AGE,
        domain=SESSION_COOKIE_DOMAIN,
        path=SESSION_COOKIE_PATH,
    )


def clear_user_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=USER_AUTH_COOKIE_NAME,
        domain=SESSION_COOKIE_DOMAIN,
        path=SESSION_COOKIE_PATH,
    )


def attach_dev_auth_cookie(response: Response, session_id: str, role: str) -> None:
    response.set_cookie(
        key=DEV_AUTH_COOKIE_NAME,
        value=issue_dev_auth_token(session_id, role),
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
        max_age=ADMIN_AUTH_MAX_AGE,
        domain=SESSION_COOKIE_DOMAIN,
        path=SESSION_COOKIE_PATH,
    )


def clear_dev_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=DEV_AUTH_COOKIE_NAME,
        domain=SESSION_COOKIE_DOMAIN,
        path=SESSION_COOKIE_PATH,
    )


def resolve_auth_context(
    request: Request,
    session_id: str,
    *,
    user_repository: FileUserRepository | None = None,
) -> AuthContext:
    """Resolve a trusted identity; client role fields never participate."""

    if config.DEV_AUTH_ENABLED:
        dev_identity = verify_dev_auth_token(request.cookies.get(DEV_AUTH_COOKIE_NAME), session_id)
        if dev_identity:
            return build_dev_auth_context(dev_identity["role"], session_id=session_id)

    admin_token = request.cookies.get(ADMIN_AUTH_COOKIE_NAME)
    admin_identity = verify_admin_auth_token(admin_token, session_id)
    if admin_identity:
        return build_auth_context(
            user_id=admin_identity["user_id"],
            display_name="管理员",
            role="admin",
            session_id=session_id,
            auth_method=admin_identity.get("auth_method"),
        )

    user_token = request.cookies.get(USER_AUTH_COOKIE_NAME)
    token_payload = verify_user_auth_token(user_token, session_id)
    if token_payload:
        repository = user_repository or FileUserRepository()
        user = repository.find_by_user_id(token_payload["user_id"])
        if user is not None:
            return build_auth_context(
                user_id=user.user_id,
                display_name=user.display_name,
                role=user.role,
                asset_scope=user.asset_scope,
                table_scope=user.table_scope,
                system_scope=user.system_scope,
                location_scope=user.location_scope,
                kb_scopes=user.kb_scopes,
                session_id=session_id,
                auth_method=token_payload.get("auth_method"),
            )
    return build_auth_context(user_id="guest", display_name="访客", role="guest", session_id=session_id)


def resolve_identity_payload(request: Request, session_id: str) -> dict[str, str | bool | None | list[str]]:
    return resolve_auth_context(request, session_id).identity_payload()
