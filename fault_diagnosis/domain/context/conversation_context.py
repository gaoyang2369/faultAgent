"""Conversation context assembly from durable messages and artifacts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope
from fault_diagnosis.domain.security.contracts import AuthContext
from .case_store import ArtifactBackedCaseStore

ArtifactGetter = Callable[[str], DiagnosisArtifactEnvelope | None]
ArtifactManifestGetter = Callable[[str, str], dict[str, Any] | None]


class ConversationContextAssembler:
    """Build the context package passed into the runtime before each turn."""

    def __init__(
        self,
        *,
        conversation_repository: Any,
        case_store: ArtifactBackedCaseStore | None = None,
        artifact_getter: ArtifactGetter | None = None,
        artifact_manifest_getter: ArtifactManifestGetter | None = None,
        recent_message_limit: int = 8,
    ) -> None:
        self.conversation_repository = conversation_repository
        self.case_store = case_store or ArtifactBackedCaseStore()
        self.artifact_getter = artifact_getter or _empty_artifact_getter
        self.artifact_manifest_getter = artifact_manifest_getter or _empty_manifest_getter
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
        recent_artifact_ids = self._recent_artifact_ids(recent_messages)
        artifact_manifests = _latest_artifact_manifests(
            thread_id,
            artifact_getter=self.artifact_getter,
            manifest_getter=self.artifact_manifest_getter,
            seed_artifact_ids=recent_artifact_ids,
        )
        artifact_refs = _latest_artifact_refs(artifact_manifests)
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
            "recent_turns": self._recent_turn_summaries(recent_messages),
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
            "recent_turn_summary_count": len(package["recent_turns"]),
            "has_case_state": active_case is not None,
            "artifact_ref_count": len(artifact_refs),
            "artifact_manifest_count": len(artifact_manifests),
            "previous_assistant_artifact_ref_count": len(previous_assistant_turn.get("produced_artifacts") or []),
        }
        return package

    @staticmethod
    def _recent_turn_summaries(recent_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """只读取持久化的安全摘要；绝不从历史聊天文本重新抽取。"""

        summaries: list[dict[str, Any]] = []
        for item in recent_messages:
            if item.get("role") != "assistant" or item.get("status") == "superseded":
                continue
            payload = item.get("content_json") if isinstance(item.get("content_json"), dict) else {}
            summary = payload.get("semantic_turn_summary") if isinstance(payload.get("semantic_turn_summary"), dict) else {}
            if not summary:
                continue
            summaries.append({"turn_index": item.get("turn_index"), **summary})
        return summaries

    def _recent_artifact_ids(self, recent_messages: list[dict[str, Any]]) -> list[str]:
        artifact_ids: list[str] = []
        for item in reversed(recent_messages):
            message_id = str(item.get("id") or "")
            if not message_id:
                continue
            try:
                refs = self.conversation_repository.list_message_artifact_refs(message_id=message_id)
            except Exception:
                continue
            artifact_ids.extend(
                str(ref.get("artifact_id") or "")
                for ref in refs
                if isinstance(ref, dict) and str(ref.get("artifact_id") or "").strip()
            )
        return list(dict.fromkeys(artifact_ids))

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
            payload = item.get("content_json") if isinstance(item.get("content_json"), dict) else {}
            authorization = payload.get("authorization") if isinstance(payload.get("authorization"), dict) else {}
            ui_payload = payload.get("ui_payload") if isinstance(payload.get("ui_payload"), dict) else {}
            return {
                "message_id": item.get("id"),
                "turn_index": item.get("turn_index"),
                "status": item.get("status"),
                "created_at": item.get("created_at"),
                "produced_artifacts": produced,
                "context_unavailable": (
                    str(payload.get("status") or "") in {"blocked", "failed", "denied"}
                    or authorization.get("mode") == "deny"
                    or ui_payload.get("type") == "access_denied"
                ),
            }
        return {}


def _empty_artifact_getter(thread_id: str) -> DiagnosisArtifactEnvelope | None:  # noqa: ARG001
    return None


def _empty_manifest_getter(thread_id: str, artifact_id: str) -> dict[str, Any] | None:  # noqa: ARG001
    return None


def _latest_artifact_refs(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": str(item["artifact_id"]),
            "artifact_type": str(item["artifact_type"]),
            "artifact_backend": "diagnosis_artifact_store",
            "ref_role": "context_source",
        }
        for item in manifests
        if item.get("artifact_id") and item.get("artifact_type")
    ]


def _latest_artifact_manifests(
    thread_id: str,
    *,
    artifact_getter: ArtifactGetter,
    manifest_getter: ArtifactManifestGetter,
    seed_artifact_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    pending = list(seed_artifact_ids or [])
    try:
        envelope = artifact_getter(thread_id)
    except Exception:
        envelope = None
    manifests = envelope.payload.get("artifact_manifests") if envelope and isinstance(envelope.payload, dict) else []
    if isinstance(manifests, list):
        pending.extend(
            str(item.get("artifact_id") or "")
            for item in manifests
            if isinstance(item, dict) and str(item.get("artifact_id") or "").strip()
        )
    verified: list[dict[str, Any]] = []
    visited: set[str] = set()
    while pending:
        artifact_id = pending.pop(0)
        if not artifact_id or artifact_id in visited:
            continue
        visited.add(artifact_id)
        try:
            manifest = manifest_getter(thread_id, artifact_id)
        except Exception:
            manifest = None
        if (
            isinstance(manifest, dict)
            and manifest.get("artifact_status") == "complete"
            and manifest.get("persistence_status") == "committed"
            and manifest.get("readback_verified") is True
            and (manifest.get("lineage") or {}).get("lineage_status") == "complete"
        ):
            verified.append(manifest)
            lineage = manifest.get("lineage") if isinstance(manifest.get("lineage"), dict) else {}
            pending.extend(
                str(item)
                for item in lineage.get("source_artifact_ids", [])
                if str(item or "").strip() and str(item) not in visited
            )
    return verified
