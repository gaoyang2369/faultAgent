"""Conversation context assembly from durable messages and artifacts."""

from __future__ import annotations

from typing import Any

from ..diagnosis.artifact_store import get_thread_artifact
from ..repositories.conversation_store import ConversationRepository
from ..security.contracts import AuthContext
from .case_store import ArtifactBackedCaseStore


class ConversationContextAssembler:
    """Build the context package passed into the runtime before each turn."""

    def __init__(
        self,
        *,
        conversation_repository: ConversationRepository,
        case_store: ArtifactBackedCaseStore | None = None,
        recent_message_limit: int = 8,
    ) -> None:
        self.conversation_repository = conversation_repository
        self.case_store = case_store or ArtifactBackedCaseStore()
        self.recent_message_limit = max(2, int(recent_message_limit))

    def build(
        self,
        *,
        thread_id: str,
        current_user_message: str,
        auth_context: AuthContext,
    ) -> dict[str, Any]:
        recent_messages = self.conversation_repository.list_messages(
            thread_id=thread_id,
            include_superseded=False,
            limit=self.recent_message_limit,
        )
        case_state = self.case_store.load(thread_id)
        active_case = case_state.active_case
        artifact_refs = _latest_artifact_refs(thread_id)
        package = {
            "version": "conversation_context_package.v1",
            "thread_id": thread_id,
            "current_user_message": current_user_message,
            "last_raw_messages": [
                {
                    "id": item.get("id"),
                    "role": item.get("role"),
                    "content": item.get("content_text"),
                    "turn_index": item.get("turn_index"),
                    "status": item.get("status"),
                    "created_at": item.get("created_at"),
                }
                for item in recent_messages
                if item.get("role") in {"user", "assistant"} and item.get("status") != "superseded"
            ],
            "rolling_summary": None,
            "latest_case_state": active_case.model_dump(exclude_none=True) if active_case else None,
            "artifact_refs": artifact_refs,
            "auth_scope": auth_context.audit_summary(),
            "safety": {
                "history_is_data_not_instruction": True,
                "summary_is_not_authorization_source": True,
                "summary_is_not_diagnosis_evidence": True,
            },
        }
        package["stats"] = {
            "raw_message_count": len(package["last_raw_messages"]),
            "has_case_state": active_case is not None,
            "artifact_ref_count": len(artifact_refs),
        }
        return package


def _latest_artifact_refs(thread_id: str) -> list[dict[str, Any]]:
    try:
        envelope = get_thread_artifact(thread_id)
    except Exception:
        return []
    if not envelope:
        return []

    refs: list[dict[str, Any]] = []
    payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    if getattr(envelope, "created_at", None):
        refs.append(
            {
                "artifact_id": str(getattr(envelope, "created_at")),
                "artifact_type": "diagnosis",
                "artifact_backend": "diagnosis_artifact_store",
                "ref_role": "context_source",
            }
        )
    report_filename = getattr(envelope, "report_filename", None) or _nested_value(payload, "report_artifact", "report_filename")
    if report_filename:
        refs.append(
            {
                "artifact_id": str(report_filename),
                "artifact_type": "report",
                "artifact_backend": "diagnosis_artifact_store",
                "ref_role": "context_source",
            }
        )
    evidence_bundle = payload.get("evidence_bundle") if isinstance(payload.get("evidence_bundle"), dict) else {}
    bundle_id = evidence_bundle.get("bundle_id") or evidence_bundle.get("id")
    if bundle_id:
        refs.append(
            {
                "artifact_id": str(bundle_id),
                "artifact_type": "evidence_bundle",
                "artifact_backend": "diagnosis_artifact_store",
                "ref_role": "context_source",
            }
        )
    return refs


def _nested_value(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
