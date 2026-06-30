from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("fastapi.testclient")
FastAPI = fastapi.FastAPI
TestClient = testclient.TestClient

from fault_diagnosis.api import chat as chat_api
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.api.chat import router as chat_router
from fault_diagnosis.auth.admin_auth import USER_AUTH_COOKIE_NAME
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.security.permissions import AUTHORIZED_BUSINESS_TABLES
from fault_diagnosis.security.voice_auth import sign_voice_identity


SECRET = "voice-exchange-test-secret"
NOW = 1_750_000_000


def _write_users(path) -> None:
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


def _payload(*, user="陈冠任", role="admin", timestamp=NOW, nonce="exchange-nonce-1", signature=None) -> dict:
    if signature is None:
        signature = sign_voice_identity(
            user=user,
            role=role,
            timestamp=timestamp,
            nonce=nonce,
            secret=SECRET,
        )
    return {
        "user": user,
        "role": role,
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": signature,
    }


def _client(monkeypatch, tmp_path, *, include_chat: bool = False) -> TestClient:
    users_path = tmp_path / "users.json"
    _write_users(users_path)
    monkeypatch.setenv("USER_STORE_PATH", str(users_path))
    monkeypatch.setenv("VOICE_AUTH_SHARED_SECRET", SECRET)
    monkeypatch.setattr("fault_diagnosis.security.voice_auth.time.time", lambda: NOW)

    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("voice-exchange-session-secret")
    app.include_router(auth_router)
    if include_chat:
        app.include_router(chat_router)
    return TestClient(app)


def test_voice_exchange_then_identity_returns_admin(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        response = client.post("/auth/voice/exchange", json=_payload(nonce="exchange-ok"))
        identity = client.get("/auth/identity").json()

    assert response.status_code == 200
    assert response.json()["user_id"] == "chen_guanren"
    assert response.json()["display_name"] == "陈冠任"
    assert response.json()["role"] == "admin"
    assert response.json()["auth_method"] == "voice_exchange"
    assert USER_AUTH_COOKIE_NAME in client.cookies
    assert identity["role"] == "admin"
    assert identity["auth_method"] == "voice_exchange"
    assert identity["allowed_tables"] == AUTHORIZED_BUSINESS_TABLES
    assert "legacy.value.is.not.trusted" not in identity["permissions"]


def test_voice_exchange_rejects_bad_signature(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/auth/voice/exchange",
            json=_payload(nonce="exchange-bad-signature", signature="0" * 64),
        )

    assert response.status_code == 403
    assert USER_AUTH_COOKIE_NAME not in client.cookies


def test_voice_exchange_rejects_expired_timestamp(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/auth/voice/exchange",
            json=_payload(timestamp=NOW - 61, nonce="exchange-expired"),
        )

    assert response.status_code == 403
    assert USER_AUTH_COOKIE_NAME not in client.cookies


def test_voice_exchange_rejects_replayed_nonce(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        payload = _payload(nonce="exchange-replay")
        first = client.post("/auth/voice/exchange", json=payload)
        second = client.post("/auth/voice/exchange", json=payload)

    assert first.status_code == 200
    assert second.status_code == 403


def test_chat_stream_frontend_user_identity_cannot_escalate(monkeypatch, tmp_path) -> None:
    async def fake_stream_events(app, message, thread_id, user_identity, **kwargs):
        auth_context = kwargs["auth_context"]
        yield (
            "data: "
            + json.dumps(
                {
                    "type": "auth_probe",
                    "role": auth_context.role,
                    "user_identity": user_identity,
                    "auth_method": auth_context.auth_method,
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )

    monkeypatch.setattr(chat_api, "token_stream_events", fake_stream_events)
    with _client(monkeypatch, tmp_path, include_chat=True) as client:
        response = client.get(
            "/chat/stream",
            params={"message": "诊断 J1号机", "user_identity": "管理员"},
        )

    assert response.status_code == 200
    assert '"role": "guest"' in response.text
    assert '"user_identity": "访客"' in response.text
    assert '"auth_method": null' in response.text


def test_voice_exchange_role_body_cannot_override_user_mapping(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/auth/voice/exchange",
            json=_payload(role="engineer", nonce="exchange-role-mismatch"),
        )
        identity = client.get("/auth/identity").json()

    assert response.status_code == 403
    assert USER_AUTH_COOKIE_NAME not in client.cookies
    assert identity["role"] == "guest"
