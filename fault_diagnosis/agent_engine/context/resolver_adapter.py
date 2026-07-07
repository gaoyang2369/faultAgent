"""Adapter from legacy context resolution to Agent Engine V2 ContextFrame."""

from __future__ import annotations

from typing import Any

from ...context.contracts import ConversationDiagnosisState, ResolvedContext
from ...context.manager import ContextManager
from ...security.contracts import AuthContext
from ...security.permissions import build_auth_context
from ..contracts import ContextFrame, IntentFrame


class ContextFrameAdapter:
    """Project existing deterministic context resolution into V2 contracts.

    The adapter is intentionally read-only: it does not create artifacts, execute
    tools, or mutate the legacy workflow. The only mutation performed by the
    legacy resolver is to annotate the ephemeral current_payload dict.
    """

    def __init__(self, context_manager: ContextManager | None = None) -> None:
        self.context_manager = context_manager or ContextManager()

    def resolve(
        self,
        *,
        thread_id: str,
        raw_message: str,
        intent_frame: IntentFrame,
        auth_context: AuthContext | None = None,
        state: ConversationDiagnosisState | None = None,
        conversation_context: dict[str, Any] | None = None,
        recent_context_signals: dict[str, Any] | None = None,
    ) -> ContextFrame:
        effective_auth = auth_context or build_auth_context()
        current_payload = _payload_from_intent(intent_frame)
        resolved = self.context_manager.resolve(
            thread_id=thread_id,
            message=raw_message,
            auth_context=effective_auth,
            current_payload=current_payload,
            state=state,
            conversation_context=conversation_context,
            recent_context_signals=recent_context_signals,
        )
        return _to_context_frame(resolved, effective_auth)


def _payload_from_intent(intent_frame: IntentFrame) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "user_message": intent_frame.raw_message,
        "analysis_goal": intent_frame.normalized_message or intent_frame.raw_message,
    }
    if intent_frame.device_refs:
        payload["equipment_hint"] = intent_frame.device_refs[0]
    if intent_frame.fault_code_refs:
        payload["fault_code_hint"] = intent_frame.fault_code_refs[0]
    if intent_frame.time_window:
        payload["time_range_hint"] = dict(intent_frame.time_window)
    if "report" in intent_frame.requested_outputs:
        payload["needs_report"] = True
    if "workorder_decision" in intent_frame.requested_outputs:
        payload["needs_workorder_decision"] = True
    return payload


def _to_context_frame(resolved: ResolvedContext, auth_context: AuthContext) -> ContextFrame:
    reuse_blockers = _reuse_blockers(resolved)
    permission_context = auth_context.audit_summary()
    permission_context["context_resolution_reason"] = resolved.context_resolution_reason
    permission_context["source"] = resolved.source

    return ContextFrame(
        relation_to_previous=resolved.relation_to_previous,
        active_case_id=resolved.active_case_id,
        referenced_artifact_id=(
            resolved.referenced_artifact_id
            or resolved.referenced_report_id
            or resolved.selected_artifact_id
            or resolved.last_evidence_bundle_id
        ),
        inherited_slots=dict(resolved.inherited_slots),
        stale_evidence=["previous_evidence"] if resolved.stale_evidence else [],
        missing_context=list(resolved.missing_context),
        permission_context=permission_context,
        reuse_decision=resolved.evidence_mode or "collect_new",
        reuse_blockers=reuse_blockers,
    )


def _reuse_blockers(resolved: ResolvedContext) -> list[str]:
    blockers = list(resolved.missing_context)
    reason = resolved.context_resolution_reason or ""
    if resolved.relation_to_previous == "ambiguous" and reason:
        blockers.append(reason)
    if not resolved.inherited_slots and any(word in reason for word in ("权限", "身份", "授权")):
        blockers.append(reason)
    if resolved.stale_evidence:
        blockers.append("上一轮证据可能已过期，后续阶段需要刷新运行数据。")
    return list(dict.fromkeys(item for item in blockers if item))
