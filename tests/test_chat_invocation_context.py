from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("fastapi.testclient")
FastAPI = fastapi.FastAPI
TestClient = testclient.TestClient

from fault_diagnosis.agent_runtime.sse_adapter import adapt_sse_chunk
from fault_diagnosis.api import chat as chat_api
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.api.chat import router as chat_router
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.diagnosis.artifact_store import clear_all_artifacts, save_thread_artifact
from fault_diagnosis.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType
from fault_diagnosis.runtime.dev_mode import init_dev_state, record_dev_exchange


def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("chat-invocation-context-test-secret")
    init_dev_state(app)
    app.include_router(auth_router)
    app.include_router(chat_router)
    return app


def _save_report_artifact(thread_id: str) -> None:
    save_thread_artifact(
        DiagnosisArtifactEnvelope(
            workflow_type=DiagnosisArtifactType.FAULT_DIAGNOSIS,
            thread_id=thread_id,
            created_at="2026-06-23T00:00:00",
            request_summary="查询故障代码F01002的含义",
            final_answer="已生成报告",
            report_filename="demo-report.html",
            payload={
                "report_artifact": {
                    "report_filename": "demo-report.html",
                    "save_result": "报告已保存至：demo-report.html",
                }
            },
        )
    )


def _install_fake_stream(monkeypatch, calls: list[dict[str, object]]) -> None:
    async def fake_stream_events(app, message, thread_id, user_identity, **kwargs):
        complete_payload = {
            "type": "chat_complete",
            "thread_id": thread_id,
            "final_content": f"reply:{message}",
        }
        calls.append(
            {
                "message": message,
                "thread_id": thread_id,
                "user_identity": user_identity,
                "auth_context": kwargs["auth_context"].audit_summary() if kwargs.get("auth_context") else None,
                "history_count": len(kwargs.get("history_messages") or []),
                "replace_history": kwargs.get("replace_history"),
                "metadata_channel": getattr(kwargs.get("auth_context"), "auth_method", None),
            }
        )
        chunk = adapt_sse_chunk(
            "event: complete\ndata: " + json.dumps(complete_payload, ensure_ascii=False) + "\n\n",
            None,
            thread_id=thread_id,
            complete_payload_enricher=kwargs.get("complete_payload_enricher"),
        )
        yield chunk

    monkeypatch.setattr(chat_api, "token_stream_events", fake_stream_events)


def test_text_and_voice_share_same_thread_history_and_complete_payload(monkeypatch) -> None:
    clear_all_artifacts()
    calls: list[dict[str, object]] = []
    _install_fake_stream(monkeypatch, calls)

    app = _build_app()
    thread_id = "thread.chat-context-test"
    record_dev_exchange(
        app,
        thread_id,
        "查询故障代码F01002的含义",
        "上一轮已经回答过故障码含义",
        [],
    )
    _save_report_artifact(thread_id)

    with TestClient(app) as client:
        text_response = client.get(
            "/chat/stream",
            params={"message": "基于刚才生成报告", "thread_id": thread_id},
        )
        voice_response = client.post(
            "/agent/chat",
            json={"message": "继续刚才", "thread_id": thread_id},
        )

    assert text_response.status_code == 200
    assert voice_response.status_code == 200

    assert len(calls) == 2
    assert calls[0]["thread_id"] == thread_id
    assert calls[1]["thread_id"] == thread_id
    assert calls[0]["history_count"] >= 2
    assert calls[1]["history_count"] >= 2
    assert calls[0]["auth_context"] == calls[1]["auth_context"]

    assert '"auth_context"' in text_response.text
    assert '"workflow_result"' in text_response.text
    assert 'demo-report.html' in text_response.text
    assert voice_response.json()["thread_id"] == thread_id
    assert voice_response.json()["metadata"]["thread_id"] == thread_id
    assert voice_response.json()["metadata"]["auth_context"] == calls[1]["auth_context"]


def test_explicit_thread_id_is_reused_by_both_entrypoints(monkeypatch) -> None:
    clear_all_artifacts()
    calls: list[dict[str, object]] = []
    _install_fake_stream(monkeypatch, calls)

    app = _build_app()
    thread_id = "thread.voice-first-test"
    record_dev_exchange(app, thread_id, "语音先问", "语音先答", [])

    with TestClient(app) as client:
        voice_response = client.post(
            "/agent/chat",
            json={"message": "基于刚才生成报告", "thread_id": thread_id},
        )
        text_response = client.get(
            "/chat/stream",
            params={"message": "继续刚才", "thread_id": thread_id},
        )

    assert voice_response.status_code == 200
    assert text_response.status_code == 200

    assert [call["thread_id"] for call in calls] == [thread_id, thread_id]
    assert [call["history_count"] for call in calls] == [2, 2]
    assert voice_response.json()["thread_id"] == thread_id
    assert '"auth_context"' in text_response.text

