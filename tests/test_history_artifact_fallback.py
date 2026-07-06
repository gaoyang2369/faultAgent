from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.diagnosis.artifact_backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.diagnosis.artifact_store import configure_artifact_store_backend, save_thread_artifact
from fault_diagnosis.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType
from fault_diagnosis.repositories.history_index import MemoryHistoryIndexRepository
from fault_diagnosis.services.history_service import HistoryService


def _service_with_thread(thread_id: str, session_id: str, manager: SessionScopeManager) -> HistoryService:
    app = SimpleNamespace(state=SimpleNamespace(dev_mode=False, checkpointer=None))
    return HistoryService(
        app=app,
        session_manager=manager,
        session_id=session_id,
        legacy_bindings={},
        history_index_repository=MemoryHistoryIndexRepository(),
    )


def test_history_detail_falls_back_to_thread_artifact_without_checkpoint() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    manager = SessionScopeManager("history-artifact-fallback-test-secret")
    session_id = manager.issue_session_id()
    thread_id = manager.issue_thread_id(session_id)
    save_thread_artifact(
        DiagnosisArtifactEnvelope(
            workflow_type=DiagnosisArtifactType.FAULT_DIAGNOSIS,
            thread_id=thread_id,
            created_at="2026-07-02T15:18:05",
            request_summary="从结果来看貌似有故障呀？是不是要生成工单？",
            final_answer="建议先确认 J1 当前告警是否仍持续，再决定是否生成维修工单。",
            report_filename="demo.html",
            payload={
                "request": {
                    "user_message": "从结果来看貌似有故障呀？是不是要生成工单？",
                },
                "report_artifact": {
                    "report_url": "/reports/demo.html",
                },
            },
        )
    )

    messages = asyncio.run(
        _service_with_thread(thread_id, session_id, manager).get_messages(
            history_type="service",
            chat_id=thread_id,
        )
    )

    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "从结果来看貌似有故障呀？是不是要生成工单？"
    assert messages[1]["content"].startswith("建议先确认 J1 当前告警")
    assert messages[1]["streamState"] == "completed"
    assert messages[1]["report_url"] == "/reports/demo.html"

