"""会话作用域、legacy thread 兼容映射与 cookie 签发工具。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from typing import Iterable

from fastapi import FastAPI, Request, Response

from ..config import (
    SESSION_COOKIE_DOMAIN,
    SESSION_COOKIE_PATH,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
    SESSION_SECRET,
    SESSION_SECRET_SOURCE,
)


SESSION_COOKIE_NAME = "fd_session"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 7
SESSION_COOKIE_SALT = "fd-session-v1"
THREAD_ID_PREFIX = "thread"
LEGACY_THREAD_COOKIE_NAME = "fd_legacy_threads"
LEGACY_THREAD_COOKIE_SALT = "fd-legacy-v1"
LEGACY_THREAD_COOKIE_MAX_AGE = SESSION_COOKIE_MAX_AGE
LEGACY_THREAD_BINDING_LIMIT = 24


def _sign_payload(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _encode_json(data: dict[str, str]) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_json(payload: str) -> dict[str, str]:
    padded = payload + "=" * (-len(payload) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    decoded = json.loads(raw.decode("utf-8"))
    return decoded if isinstance(decoded, dict) else {}


class SessionScopeManager:
    """基于签名 cookie、签名 thread_id 与 legacy 映射 cookie 的最小会话隔离实现。"""

    def __init__(self, secret: str | None = None, secret_source: str | None = None):
        env_secret = os.getenv("SESSION_SECRET", "").strip()
        if secret:
            seed_value = secret
            resolved_secret_source = secret_source or "argument"
        elif env_secret:
            seed_value = env_secret
            resolved_secret_source = "environment"
        elif SESSION_SECRET:
            seed_value = SESSION_SECRET
            resolved_secret_source = SESSION_SECRET_SOURCE or "config"
        else:
            seed_value = secrets.token_hex(32)
            resolved_secret_source = "ephemeral"
        seed = seed_value.encode("utf-8")
        self._secret = seed
        self.secret_source = resolved_secret_source
        self.uses_ephemeral_secret = resolved_secret_source == "ephemeral"
        self._cookie_secure = SESSION_COOKIE_SECURE
        self._cookie_samesite = SESSION_COOKIE_SAMESITE
        self._cookie_domain = SESSION_COOKIE_DOMAIN
        self._cookie_path = SESSION_COOKIE_PATH

    def _sign(self, payload: str) -> str:
        return _sign_payload(self._secret, payload)

    def issue_session_id(self) -> str:
        return uuid.uuid4().hex

    def issue_session_token(self, session_id: str) -> str:
        payload = f"{SESSION_COOKIE_SALT}:{session_id}"
        signature = self._sign(payload)
        return f"{session_id}.{signature}"

    def verify_session_token(self, token: str | None) -> str | None:
        if not token or "." not in token:
            return None

        session_id, signature = token.rsplit(".", 1)
        if not session_id or len(session_id) != 32:
            return None

        payload = f"{SESSION_COOKIE_SALT}:{session_id}"
        expected = self._sign(payload)
        if not hmac.compare_digest(signature, expected):
            return None
        return session_id

    def resolve_session_id(self, token: str | None) -> tuple[str, bool]:
        session_id = self.verify_session_token(token)
        if session_id:
            return session_id, False
        return self.issue_session_id(), True

    def issue_thread_id(self, session_id: str) -> str:
        nonce = uuid.uuid4().hex
        payload = f"{session_id}:{nonce}"
        signature = self._sign(payload)[:24]
        return f"{THREAD_ID_PREFIX}.{session_id}.{nonce}.{signature}"

    def is_signed_thread_id(self, thread_id: str | None) -> bool:
        if not thread_id:
            return False
        parts = thread_id.split(".")
        return len(parts) == 4 and parts[0] == THREAD_ID_PREFIX

    def is_legacy_thread_id(self, thread_id: str | None) -> bool:
        return bool(thread_id) and not self.is_signed_thread_id(thread_id)

    def is_thread_owned_by_session(self, thread_id: str | None, session_id: str) -> bool:
        if not thread_id:
            return False

        parts = thread_id.split(".")
        if len(parts) != 4 or parts[0] != THREAD_ID_PREFIX:
            return False

        _, owner_session_id, nonce, signature = parts
        if owner_session_id != session_id:
            return False

        payload = f"{owner_session_id}:{nonce}"
        expected = self._sign(payload)[:24]
        return hmac.compare_digest(signature, expected)

    def _normalize_legacy_bindings(self, session_id: str, bindings: dict[str, str] | None) -> dict[str, str]:
        if not isinstance(bindings, dict):
            return {}

        normalized: dict[str, str] = {}
        items = list(bindings.items())[-LEGACY_THREAD_BINDING_LIMIT:]
        for legacy_thread_id, thread_id in items:
            if not self.is_legacy_thread_id(legacy_thread_id):
                continue
            if not self.is_thread_owned_by_session(thread_id, session_id):
                continue
            normalized[str(legacy_thread_id)] = thread_id
        return normalized

    def issue_legacy_thread_token(self, session_id: str, bindings: dict[str, str] | None) -> str:
        normalized = self._normalize_legacy_bindings(session_id, bindings)
        payload = _encode_json(normalized)
        signature = self._sign(f"{LEGACY_THREAD_COOKIE_SALT}:{session_id}:{payload}")
        return f"{payload}.{signature}"

    def verify_legacy_thread_token(self, token: str | None, session_id: str) -> dict[str, str]:
        if not token or "." not in token:
            return {}

        payload, signature = token.rsplit(".", 1)
        expected = self._sign(f"{LEGACY_THREAD_COOKIE_SALT}:{session_id}:{payload}")
        if not hmac.compare_digest(signature, expected):
            return {}

        try:
            decoded = _decode_json(payload)
        except Exception:
            return {}
        return self._normalize_legacy_bindings(session_id, decoded)

    def resolve_thread_id(
        self,
        session_id: str,
        requested_thread_id: str | None,
        legacy_bindings: dict[str, str] | None = None,
    ) -> tuple[str, bool, dict[str, str], str | None]:
        bindings = self._normalize_legacy_bindings(session_id, legacy_bindings)

        if self.is_thread_owned_by_session(requested_thread_id, session_id):
            return requested_thread_id, False, bindings, None

        if self.is_legacy_thread_id(requested_thread_id):
            mapped_thread_id = bindings.get(requested_thread_id)
            if mapped_thread_id and self.is_thread_owned_by_session(mapped_thread_id, session_id):
                return mapped_thread_id, False, bindings, requested_thread_id

            rebound_thread_id = self.issue_thread_id(session_id)
            bindings[requested_thread_id] = rebound_thread_id
            return rebound_thread_id, True, bindings, requested_thread_id

        return self.issue_thread_id(session_id), True, bindings, None

    def resolve_history_thread_id(
        self,
        session_id: str,
        requested_thread_id: str | None,
        legacy_bindings: dict[str, str] | None = None,
    ) -> str | None:
        if self.is_thread_owned_by_session(requested_thread_id, session_id):
            return requested_thread_id

        if self.is_legacy_thread_id(requested_thread_id):
            bindings = self._normalize_legacy_bindings(session_id, legacy_bindings)
            mapped_thread_id = bindings.get(requested_thread_id)
            if mapped_thread_id and self.is_thread_owned_by_session(mapped_thread_id, session_id):
                return mapped_thread_id

        return None

    def filter_owned_thread_ids(self, session_id: str, thread_ids: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        owned: list[str] = []
        for thread_id in thread_ids:
            if thread_id in seen:
                continue
            if self.is_thread_owned_by_session(thread_id, session_id):
                seen.add(thread_id)
                owned.append(thread_id)
        return owned

    def attach_session_cookie(self, response: Response, session_id: str) -> None:
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=self.issue_session_token(session_id),
            httponly=True,
            samesite=self._cookie_samesite,
            secure=self._cookie_secure,
            max_age=SESSION_COOKIE_MAX_AGE,
            domain=self._cookie_domain,
            path=self._cookie_path,
        )

    def attach_legacy_thread_cookie(self, response: Response, session_id: str, bindings: dict[str, str] | None) -> None:
        normalized = self._normalize_legacy_bindings(session_id, bindings)
        if normalized:
            response.set_cookie(
                key=LEGACY_THREAD_COOKIE_NAME,
                value=self.issue_legacy_thread_token(session_id, normalized),
                httponly=True,
                samesite=self._cookie_samesite,
                secure=self._cookie_secure,
                max_age=LEGACY_THREAD_COOKIE_MAX_AGE,
                domain=self._cookie_domain,
                path=self._cookie_path,
            )
            return

        response.delete_cookie(
            key=LEGACY_THREAD_COOKIE_NAME,
            domain=self._cookie_domain,
            path=self._cookie_path,
        )

    def attach_scope_cookies(self, response: Response, session_id: str, bindings: dict[str, str] | None = None) -> None:
        self.attach_session_cookie(response, session_id)
        self.attach_legacy_thread_cookie(response, session_id, bindings)


def get_session_scope_manager(app: FastAPI) -> SessionScopeManager:
    manager = getattr(app.state, "session_scope_manager", None)
    if manager is None:
        manager = SessionScopeManager()
        app.state.session_scope_manager = manager
    return manager


def resolve_request_scope(request: Request) -> tuple[SessionScopeManager, str, bool, dict[str, str]]:
    manager = get_session_scope_manager(request.app)
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    session_id, is_new = manager.resolve_session_id(session_token)
    legacy_token = request.cookies.get(LEGACY_THREAD_COOKIE_NAME)
    legacy_bindings = manager.verify_legacy_thread_token(legacy_token, session_id)
    return manager, session_id, is_new, legacy_bindings


def resolve_request_session(request: Request) -> tuple[SessionScopeManager, str, bool]:
    manager, session_id, is_new, _ = resolve_request_scope(request)
    return manager, session_id, is_new
