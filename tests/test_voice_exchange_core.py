from __future__ import annotations

import json
from types import SimpleNamespace

from fault_diagnosis.auth.admin_auth import USER_AUTH_COOKIE_NAME, issue_user_auth_token, resolve_auth_context
from fault_diagnosis.auth.voice_exchange import resolve_voice_exchange_auth_context
from fault_diagnosis.repositories.user_repository import FileUserRepository
from fault_diagnosis.security.voice_auth import VoiceNonceCache, sign_voice_identity


SECRET = "voice-exchange-core-test-secret"
NOW = 1_750_000_000


def _repository(tmp_path) -> FileUserRepository:
    path = tmp_path / "users.json"
    path.write_text(
        json.dumps(
            [
                {
                    "user_id": "chen_guanren",
                    "voice_name": "陈冠任",
                    "display_name": "陈冠任",
                    "role": "admin",
                    "permissions": ["legacy.value.is.not.trusted"],
                    "asset_scope": [],
                    "allowed_tables": [],
                    "kb_scopes": ["public", "internal", "restricted"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return FileUserRepository(path=path)


def _signature(*, user="陈冠任", role="admin", timestamp=NOW, nonce="exchange-core-nonce") -> str:
    return sign_voice_identity(
        user=user,
        role=role,
        timestamp=timestamp,
        nonce=nonce,
        secret=SECRET,
    )


def _exchange(
    monkeypatch,
    tmp_path,
    *,
    user="陈冠任",
    role="admin",
    timestamp=NOW,
    nonce="exchange-core-nonce",
    signature=None,
    cache=None,
):
    monkeypatch.setenv("VOICE_AUTH_SHARED_SECRET", SECRET)
    monkeypatch.setattr("fault_diagnosis.security.voice_auth.time.time", lambda: NOW)
    return resolve_voice_exchange_auth_context(
        user=user,
        role=role,
        timestamp=timestamp,
        nonce=nonce,
        signature=signature
        if signature is not None
        else _signature(user=user, role=role, timestamp=timestamp, nonce=nonce),
        user_repository=_repository(tmp_path),
        nonce_cache=cache or VoiceNonceCache(),
    )


def test_voice_exchange_cookie_identity_returns_admin(monkeypatch, tmp_path) -> None:
    session_id = "session-voice-exchange"
    exchanged = _exchange(monkeypatch, tmp_path, nonce="exchange-core-ok")
    token = issue_user_auth_token(session_id, exchanged.user_id, auth_method=exchanged.auth_method or "")
    request = SimpleNamespace(cookies={USER_AUTH_COOKIE_NAME: token}, headers={})
    identity = resolve_auth_context(request, session_id, user_repository=_repository(tmp_path))

    assert exchanged.role == "admin"
    assert exchanged.auth_method == "voice_exchange"
    assert exchanged.identity_payload()["display_name"] == "陈冠任"
    assert "legacy.value.is.not.trusted" not in exchanged.permissions
    assert identity.role == "admin"
    assert identity.auth_method == "voice_exchange"


def test_voice_exchange_rejects_bad_signature(monkeypatch, tmp_path) -> None:
    exchanged = _exchange(
        monkeypatch,
        tmp_path,
        nonce="exchange-core-bad-signature",
        signature="0" * 64,
    )

    assert exchanged is None


def test_voice_exchange_rejects_expired_timestamp(monkeypatch, tmp_path) -> None:
    exchanged = _exchange(monkeypatch, tmp_path, timestamp=NOW - 61, nonce="exchange-core-expired")

    assert exchanged is None


def test_voice_exchange_rejects_replayed_nonce(monkeypatch, tmp_path) -> None:
    cache = VoiceNonceCache()
    first = _exchange(monkeypatch, tmp_path, nonce="exchange-core-replay", cache=cache)
    second = _exchange(monkeypatch, tmp_path, nonce="exchange-core-replay", cache=cache)

    assert first is not None
    assert second is None


def test_frontend_user_identity_cannot_escalate_without_cookie() -> None:
    request = SimpleNamespace(cookies={}, headers={}, query_params={"user_identity": "管理员"})
    identity = resolve_auth_context(request, "session-without-cookie")

    assert identity.role == "guest"
    assert identity.auth_method is None


def test_voice_exchange_role_body_cannot_override_user_mapping(monkeypatch, tmp_path) -> None:
    exchanged = _exchange(monkeypatch, tmp_path, role="engineer", nonce="exchange-core-role-mismatch")

    assert exchanged is None
