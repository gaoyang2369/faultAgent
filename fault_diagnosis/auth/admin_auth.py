"""管理员最小认证 cookie 与身份解析工具。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import Request, Response

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


ADMIN_AUTH_COOKIE_NAME = "fd_admin_auth"
ADMIN_AUTH_COOKIE_SALT = "fd-admin-auth-v1"
VISITOR_IDENTITY = {
    "user_id": "guest",
    "user_role": "访客",
    "is_admin": False,
    "auth_method": None,
    "available_auth_methods": ["password", "voice_pending"],
}

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


def resolve_identity_payload(request: Request, session_id: str) -> dict[str, str | bool | None | list[str]]:
    token = request.cookies.get(ADMIN_AUTH_COOKIE_NAME)
    identity = verify_admin_auth_token(token, session_id)
    if identity:
        return identity
    return dict(VISITOR_IDENTITY)
