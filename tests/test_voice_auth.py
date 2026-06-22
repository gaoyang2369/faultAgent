from __future__ import annotations

import json
from types import SimpleNamespace

from fault_diagnosis.auth.admin_auth import resolve_auth_context
from fault_diagnosis.repositories.user_repository import FileUserRepository
from fault_diagnosis.security.policy_engine import authorize_workflow
from fault_diagnosis.security.voice_auth import VoiceNonceCache, sign_voice_identity
from fault_diagnosis.single_agent.contracts import SingleAgentDecision


SECRET = "voice-test-shared-secret"
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
                },
                {
                    "user_id": "engineer_01",
                    "voice_name": "维修工程师01",
                    "display_name": "维修工程师01",
                    "role": "engineer",
                    "permissions": [],
                    "asset_scope": ["J1号机"],
                    "allowed_tables": ["real_data_01", "device_alarm"],
                    "kb_scopes": ["public", "internal"],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return FileUserRepository(path=path)


def _request(*, user="陈冠任", role="admin", timestamp=NOW, nonce="nonce-1", signature=None):
    if signature is None:
        signature = sign_voice_identity(
            user=user,
            role=role,
            timestamp=timestamp,
            nonce=nonce,
            secret=SECRET,
        )
    return SimpleNamespace(
        cookies={},
        headers={
            "X-Voice-User": user,
            "X-Voice-Role": role,
            "X-Voice-Timestamp": str(timestamp),
            "X-Voice-Nonce": nonce,
            "X-Voice-Signature": signature,
        },
    )


def _resolve(monkeypatch, tmp_path, request, cache):
    monkeypatch.setenv("VOICE_AUTH_SHARED_SECRET", SECRET)
    monkeypatch.setattr("fault_diagnosis.security.voice_auth.time.time", lambda: NOW)
    return resolve_auth_context(
        request,
        "session-1",
        user_repository=_repository(tmp_path),
        voice_nonce_cache=cache,
    )


def test_unsigned_request_defaults_to_guest(monkeypatch, tmp_path) -> None:
    auth = _resolve(monkeypatch, tmp_path, SimpleNamespace(cookies={}, headers={}), VoiceNonceCache())

    assert auth.role == "guest"
    assert auth.auth_method is None


def test_bad_signature_degrades_to_guest(monkeypatch, tmp_path) -> None:
    auth = _resolve(
        monkeypatch,
        tmp_path,
        _request(signature="0" * 64),
        VoiceNonceCache(),
    )

    assert auth.role == "guest"


def test_expired_timestamp_is_rejected(monkeypatch, tmp_path) -> None:
    auth = _resolve(
        monkeypatch,
        tmp_path,
        _request(timestamp=NOW - 61),
        VoiceNonceCache(),
    )

    assert auth.role == "guest"


def test_repeated_nonce_is_rejected(monkeypatch, tmp_path) -> None:
    cache = VoiceNonceCache()
    first = _resolve(monkeypatch, tmp_path, _request(), cache)
    second = _resolve(monkeypatch, tmp_path, _request(), cache)

    assert first.role == "admin"
    assert second.role == "guest"


def test_voice_role_is_trimmed_and_chen_guanren_maps_to_admin(monkeypatch, tmp_path) -> None:
    auth = _resolve(
        monkeypatch,
        tmp_path,
        _request(role="管理员 ", nonce="trimmed-role"),
        VoiceNonceCache(),
    )

    assert auth.user_id == "chen_guanren"
    assert auth.display_name == "陈冠任"
    assert auth.role == "admin"
    assert auth.auth_method == "voice"
    assert "legacy.value.is.not.trusted" not in auth.permissions
    identity = auth.identity_payload()
    assert identity["role"] == "admin"
    assert identity["auth_method"] == "voice"
    assert identity["asset_scope"] == []
    assert "permissions" in identity
    assert "allowed_tables" in identity


def test_voice_engineer_can_only_access_assigned_assets(monkeypatch, tmp_path) -> None:
    auth = _resolve(
        monkeypatch,
        tmp_path,
        _request(user="维修工程师01", role="engineer", nonce="engineer-nonce"),
        VoiceNonceCache(),
    )
    assigned = authorize_workflow(
        auth,
        SingleAgentDecision(primary_task_type="fault_diagnosis", objects={"device_ids": ["J1号机"]}),
    )
    outside_scope = authorize_workflow(
        auth,
        SingleAgentDecision(primary_task_type="fault_diagnosis", objects={"device_ids": ["J2号机"]}),
    )

    assert auth.asset_scope == ["J1号机"]
    assert auth.table_scope == ["real_data_01", "device_alarm"]
    assert assigned.allowed is True
    assert outside_scope.allowed is False
    assert outside_scope.denied_reason_code == "asset_out_of_scope"
