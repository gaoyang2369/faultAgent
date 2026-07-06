from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("fastapi.testclient")
FastAPI = fastapi.FastAPI
TestClient = testclient.TestClient

from fault_diagnosis.agent_runtime.sse_adapter import adapt_sse_chunk
from fault_diagnosis.api import chat as chat_api
from fault_diagnosis.api.chat import router as chat_router
from fault_diagnosis.api.history import router as history_router
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.repositories.conversation_store import (
    MemoryConversationRepository,
    SQLiteConversationRepository,
    messages_to_history_payload,
)
from fault_diagnosis.repositories.history_index import MemoryHistoryIndexRepository


def test_sqlite_conversation_repository_persists_messages_and_supersedes_edits(tmp_path) -> None:
    repository = SQLiteConversationRepository(tmp_path / "conversations.sqlite3")
    repository.ensure_thread(
        thread_id="thread.test",
        session_id="session-a",
        owner_user_id="guest",
        title="J1 fault",
    )

    user_message = repository.append_message(
        thread_id="thread.test",
        role="user",
        content_text="查询 J1 当前故障",
        status="accepted",
        request_id="req-1",
        stream_id="stream-1",
    )
    repository.append_message(
        thread_id="thread.test",
        role="assistant",
        content_text="J1 存在 A07089 告警",
        status="completed",
        request_id="req-1",
        stream_id="stream-1",
        parent_message_id=user_message["id"],
        turn_index=user_message["turn_index"],
        content_json={"report_filename": "j1-report.html"},
    )

    messages = repository.list_messages(thread_id="thread.test")
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages_to_history_payload(messages)[1]["report_filename"] == "j1-report.html"

    kept_messages = repository.supersede_from_user_turn(thread_id="thread.test", user_turn_index=0)
    assert kept_messages == []
    superseded_messages = repository.list_messages(thread_id="thread.test", include_superseded=True)
    assert {message["status"] for message in superseded_messages} == {"superseded"}


def test_stream_chat_persists_user_and_assistant_messages_for_history(monkeypatch) -> None:
    async def fake_stream_events(app, message, thread_id, user_identity, **kwargs):
        complete_payload = {
            "type": "chat_complete",
            "thread_id": thread_id,
            "final_content": f"reply:{message}",
            "report_filename": "persisted-report.html",
            "artifact": {"created_at": "2026-07-02T00:00:00", "thread_id": thread_id},
        }
        chunk = adapt_sse_chunk(
            "event: complete\ndata: " + json.dumps(complete_payload, ensure_ascii=False) + "\n\n",
            None,
            thread_id=thread_id,
            complete_payload_enricher=kwargs.get("complete_payload_enricher"),
        )
        yield chunk

    monkeypatch.setattr(chat_api, "token_stream_events", fake_stream_events)

    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("conversation-persistence-test-secret")
    app.state.checkpointer = None
    app.state.conversation_repository = MemoryConversationRepository()
    app.state.history_index_repository = MemoryHistoryIndexRepository()
    app.include_router(chat_router)
    app.include_router(history_router)

    with TestClient(app) as client:
        stream_response = client.get("/chat/stream", params={"message": "查询 J1 当前故障"})
        assert stream_response.status_code == 200
        assert "persisted-report.html" in stream_response.text

        history_response = client.get("/ai/history/service")
        assert history_response.status_code == 200
        thread_ids = history_response.json()
        assert len(thread_ids) == 1

        messages_response = client.get(f"/ai/history/service/{thread_ids[0]}")
        assert messages_response.status_code == 200
        messages = messages_response.json()

    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "查询 J1 当前故障"
    assert messages[1]["content"] == "reply:查询 J1 当前故障"
    assert messages[1]["report_filename"] == "persisted-report.html"
