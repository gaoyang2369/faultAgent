"""Conversation context assembly from durable messages and artifacts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope
from fault_diagnosis.domain.security.contracts import AuthContext
from .case_store import ArtifactBackedCaseStore

ArtifactGetter = Callable[[str], DiagnosisArtifactEnvelope | None]


class ConversationContextAssembler:
    """Build the context package passed into the runtime before each turn."""

    def __init__(
        self,
        *,
        conversation_repository: Any,
        case_store: ArtifactBackedCaseStore | None = None,
        artifact_getter: ArtifactGetter | None = None,
        recent_message_limit: int = 8,
    ) -> None:
        self.conversation_repository = conversation_repository
        self.case_store = case_store or ArtifactBackedCaseStore()
        self.artifact_getter = artifact_getter or _empty_artifact_getter
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
        artifact_refs = _latest_artifact_refs(thread_id, artifact_getter=self.artifact_getter)
        artifact_manifests = _latest_artifact_manifests(thread_id, artifact_getter=self.artifact_getter)
        previous_assistant_turn = self._previous_assistant_turn(recent_messages)
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
            "artifact_manifests": artifact_manifests,
            "latest_artifact_manifests": artifact_manifests,
            "immediately_previous_assistant_turn": previous_assistant_turn,
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
            "artifact_manifest_count": len(artifact_manifests),
            "previous_assistant_artifact_ref_count": len(previous_assistant_turn.get("produced_artifacts") or []),
        }
        return package

    def _previous_assistant_turn(self, recent_messages: list[dict[str, Any]]) -> dict[str, Any]:
        for item in reversed(recent_messages):
            if item.get("role") != "assistant" or item.get("status") == "superseded":
                continue
            refs = []
            try:
                refs = self.conversation_repository.list_message_artifact_refs(message_id=str(item.get("id") or ""))
            except Exception:
                refs = []
            produced = [
                ref for ref in refs
                if str(ref.get("ref_role") or "").strip() in {"produced", "produced_by"}
            ]
            return {
                "message_id": item.get("id"),
                "turn_index": item.get("turn_index"),
                "status": item.get("status"),
                "created_at": item.get("created_at"),
                "produced_artifacts": produced,
            }
        return {}


def _empty_artifact_getter(thread_id: str) -> DiagnosisArtifactEnvelope | None:  # noqa: ARG001
    return None


def _latest_artifact_refs(thread_id: str, *, artifact_getter: ArtifactGetter) -> list[dict[str, Any]]:
    try:
        envelope = artifact_getter(thread_id)
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


def _latest_artifact_manifests(thread_id: str, *, artifact_getter: ArtifactGetter) -> list[dict[str, Any]]:
    try:
        envelope = artifact_getter(thread_id)
    except Exception:
        return []
    if not envelope or not isinstance(envelope.payload, dict):
        return []
    manifests = envelope.payload.get("artifact_manifests")
    if not isinstance(manifests, list):
        return []
    return [dict(item) for item in manifests if isinstance(item, dict)]


def _nested_value(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
