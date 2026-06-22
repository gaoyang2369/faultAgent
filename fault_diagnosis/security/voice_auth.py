"""Authenticate identity assertions made by the trusted voice backend."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any


VOICE_USER_HEADER = "X-Voice-User"
VOICE_ROLE_HEADER = "X-Voice-Role"
VOICE_TIMESTAMP_HEADER = "X-Voice-Timestamp"
VOICE_NONCE_HEADER = "X-Voice-Nonce"
VOICE_SIGNATURE_HEADER = "X-Voice-Signature"
DEFAULT_MAX_AGE_SECONDS = 60


@dataclass(frozen=True)
class VoiceIdentityAssertion:
    voice_name: str
    role: str
    timestamp: int
    nonce: str


class VoiceNonceCache:
    """Process-local replay guard; replace with Redis for multi-worker deployments."""

    def __init__(self) -> None:
        self._expires_at: dict[str, float] = {}
        self._lock = RLock()

    def consume(self, nonce: str, *, now: float, ttl_seconds: int) -> bool:
        with self._lock:
            expired = [key for key, expiry in self._expires_at.items() if expiry <= now]
            for key in expired:
                self._expires_at.pop(key, None)
            if nonce in self._expires_at:
                return False
            self._expires_at[nonce] = now + ttl_seconds
            return True

    def clear(self) -> None:
        with self._lock:
            self._expires_at.clear()


_NONCE_CACHE = VoiceNonceCache()


def normalize_voice_role(role: str) -> str | None:
    normalized = role.strip().casefold()
    aliases = {
        "guest": "guest",
        "visitor": "guest",
        "访客": "guest",
        "游客": "guest",
        "engineer": "engineer",
        "工程师": "engineer",
        "维修工程师": "engineer",
        "admin": "admin",
        "administrator": "admin",
        "管理员": "admin",
    }
    return aliases.get(normalized)


def canonical_voice_payload(user: str, role: str, timestamp: str | int, nonce: str) -> str:
    """Return the v1 signing payload. Role whitespace is normalized by contract."""

    return "\n".join((user.strip(), role.strip(), str(timestamp).strip(), nonce.strip()))


def sign_voice_identity(
    *,
    user: str,
    role: str,
    timestamp: str | int,
    nonce: str,
    secret: str,
) -> str:
    payload = canonical_voice_payload(user, role, timestamp, nonce)
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_voice_signed_request(
    request: Any,
    *,
    secret: str | None = None,
    max_age_seconds: int | None = None,
    nonce_cache: VoiceNonceCache | None = None,
    now: float | None = None,
) -> VoiceIdentityAssertion | None:
    """Verify voice headers and atomically consume the nonce on success."""

    shared_secret = secret if secret is not None else os.getenv("VOICE_AUTH_SHARED_SECRET", "").strip()
    if not shared_secret:
        return None

    headers = getattr(request, "headers", {})
    user = str(headers.get(VOICE_USER_HEADER) or "").strip()
    role = str(headers.get(VOICE_ROLE_HEADER) or "").strip()
    timestamp_text = str(headers.get(VOICE_TIMESTAMP_HEADER) or "").strip()
    nonce = str(headers.get(VOICE_NONCE_HEADER) or "").strip()
    signature = str(headers.get(VOICE_SIGNATURE_HEADER) or "").strip()
    if signature.lower().startswith("sha256="):
        signature = signature.split("=", 1)[1].strip()
    normalized_role = normalize_voice_role(role)
    if not all((user, role, timestamp_text, nonce, signature)) or normalized_role is None:
        return None

    try:
        timestamp = int(timestamp_text)
        window = int(
            max_age_seconds
            if max_age_seconds is not None
            else os.getenv("VOICE_AUTH_MAX_AGE_SECONDS", str(DEFAULT_MAX_AGE_SECONDS))
        )
    except (TypeError, ValueError):
        return None
    if window <= 0:
        return None

    current_time = time.time() if now is None else now
    if abs(current_time - timestamp) > window:
        return None

    expected = sign_voice_identity(
        user=user,
        role=role,
        timestamp=timestamp_text,
        nonce=nonce,
        secret=shared_secret,
    )
    if not hmac.compare_digest(signature.lower(), expected):
        return None

    cache = nonce_cache or _NONCE_CACHE
    if not cache.consume(nonce, now=current_time, ttl_seconds=window):
        return None
    return VoiceIdentityAssertion(voice_name=user, role=normalized_role, timestamp=timestamp, nonce=nonce)
