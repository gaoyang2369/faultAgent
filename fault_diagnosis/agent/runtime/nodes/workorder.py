"""Real workorder suggestion/draft runtime node for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import AnalysisStepArtifact, KnowledgeStepArtifact, SqlStepArtifact, WorkOrderSuggestion
from fault_diagnosis.domain.diagnosis.workorder.drafts import (
    build_pending_workorder_draft_action,
    build_workorder_draft_artifact,
    validate_pending_workorder_draft_action,
)
from fault_diagnosis.domain.diagnosis.workorder.suggestions import build_workorder_suggestion
from fault_diagnosis.domain.diagnosis.workorder.suggestions import build_workorder_suggestion_from_artifact
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import get_thread_artifact
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from .base import auth_context, build_decision_stub, build_request, input_value, model_to_dict


class WorkorderNode:
    node_type = "workorder"

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        action_type = str(input_value(node, "action_type", "") or input_value(node, "workorder_action", "") or "")
        if _is_forbidden_action(action_type, node):
            output = {
                "success": False,
                "lifecycle_status": "dispatch_forbidden",
                "allowed_next_step": "deny",
                "reason": "V2 runtime cannot dispatch workorders or execute device/config actions.",
            }
            return NodeExecutionOutput(
                status="blocked",
                output=output,
                error={"code": "workorder_dispatch_forbidden", "message": output["reason"]},
            )

        suggestion = _suggestion_from_target_artifact(node, state)
        if suggestion is None:
            request = build_request(state, node, goal="工单建议")
            sql_artifact = _model(
                state.artifacts.get("sql_artifact"),
                SqlStepArtifact,
                SqlStepArtifact(success=False, summary="无 SQL 数据"),
            )
            knowledge_artifact = _model(
                state.artifacts.get("knowledge_artifact"),
                KnowledgeStepArtifact,
                KnowledgeStepArtifact(success=False, query="", error="missing_knowledge_artifact"),
            )
            analysis_artifact = _model(
                state.artifacts.get("analysis_artifact"),
                AnalysisStepArtifact,
                AnalysisStepArtifact(success=False, conclusion="缺少分析产物", error="missing_analysis_artifact"),
            )
            suggestion = build_workorder_suggestion(
                request=request,
                sql_artifact=sql_artifact,
                knowledge_artifact=knowledge_artifact,
                analysis_artifact=analysis_artifact,
            )
        state.artifacts["workorder_suggestion"] = suggestion
        output: dict[str, Any] = {
            "success": True,
            "suggestion": model_to_dict(suggestion),
            **_source_projection(node=node, state=state, suggestion=suggestion),
        }

        if suggestion.lifecycle_status == "recommended_draft" and bool(input_value(node, "create_draft", True)):
            pending = build_pending_workorder_draft_action(
                thread_id=state.thread_id or state.trace_id or "v2_runtime",
                suggestion=suggestion,
                source_diagnosis_artifact_id=state.trace_id or state.request_id or "v2_runtime",
                source_report_artifact_id=_report_artifact_id(state),
                recommendation_artifact_id=f"workorder_recommendation:{state.trace_id or state.request_id or 'v2_runtime'}",
                stale_refresh_required=bool(input_value(node, "stale_refresh_required", False)),
            )
            ok, reason = validate_pending_workorder_draft_action(
                pending_action=model_to_dict(pending),
                auth_context=auth_context(state),
                device=suggestion.equipment_object,
            )
            if not ok:
                output["pending_action"] = model_to_dict(pending)
                output["blocked_reason"] = reason
                return NodeExecutionOutput(
                    status="blocked",
                    output=output,
                    error={"code": "workorder_draft_not_authorized", "message": reason},
                    artifacts={"workorder_suggestion": suggestion},
                )
            draft = build_workorder_draft_artifact(
                thread_id=state.thread_id or state.trace_id or "v2_runtime",
                suggestion=suggestion,
                pending_action=model_to_dict(pending),
                source_diagnosis_artifact_id=state.trace_id or state.request_id or "v2_runtime",
                source_report_artifact_id=_report_artifact_id(state),
                stale=bool(input_value(node, "stale_refresh_required", False)),
            )
            state.artifacts["workorder_pending_action"] = pending
            state.artifacts["workorder_draft"] = draft
            output["pending_action"] = model_to_dict(pending)
            output["draft"] = model_to_dict(draft)
        return NodeExecutionOutput(
            output=output,
            artifacts={
                key: value
                for key, value in {
                    "workorder_suggestion": suggestion,
                    "workorder_pending_action": state.artifacts.get("workorder_pending_action"),
                    "workorder_draft": state.artifacts.get("workorder_draft"),
                }.items()
                if value is not None
            },
        )


def _is_forbidden_action(action_type: str, node: dict[str, Any]) -> bool:
    text = " ".join(
        [
            action_type,
            str(node.get("node_id") or ""),
            " ".join(str(item) for item in node.get("required_tools", []) or []),
        ]
    ).casefold()
    return any(word in text for word in ("dispatch", "device_control.write", "config.write", "device_action")) or any(
        marker in text
        for marker in ("workorder.assign", "workorder.execute", "workorder.close", "action_type=assign", "action_type=execute", "action_type=close")
    )


def _model(value: Any, model_type: Any, default: Any) -> Any:
    if isinstance(value, model_type):
        return value
    if isinstance(value, dict) and value:
        return model_type.model_validate(value)
    return default


def _suggestion_from_target_artifact(node: dict[str, Any], state: RuntimeState) -> WorkOrderSuggestion | None:
    if any(key in state.artifacts for key in ("sql_artifact", "analysis_artifact", "structured_analysis_artifact")):
        return None
    target_id = str(input_value(node, "target_artifact_id", "") or "")
    source_refs = input_value(node, "source_artifact_refs", []) or []
    if not target_id and not source_refs:
        return None
    envelope = get_thread_artifact(state.thread_id or "")
    if envelope is None:
        return None
    try:
        return build_workorder_suggestion_from_artifact(
            envelope=envelope,
            decision=build_decision_stub(node),
            user_identity=(auth_context(state).display_name or auth_context(state).user_id or auth_context(state).role),
        )
    except Exception:
        return None


def _report_artifact_id(state: RuntimeState) -> str | None:
    report = state.artifacts.get("report_artifact")
    if hasattr(report, "report_url"):
        return report.report_url
    if isinstance(report, dict):
        return report.get("report_url") or report.get("report_filename")
    return None


def _source_projection(*, node: dict[str, Any], state: RuntimeState, suggestion: WorkOrderSuggestion) -> dict[str, Any]:
    target_evidence_bundle_id = str(input_value(node, "target_evidence_bundle_id", "") or "").strip()
    source_refs = input_value(node, "source_artifact_refs", []) or []
    if not isinstance(source_refs, list):
        source_refs = []
    supporting_refs = input_value(node, "supporting_evidence_refs", []) or []
    if not isinstance(supporting_refs, list):
        supporting_refs = []
    stale_required = bool(
        input_value(node, "stale_evidence_disclosure_required", False)
        or input_value(node, "stale_refresh_required", False)
    )
    evidence_freshness = str(input_value(node, "evidence_freshness", "") or ("stale" if stale_required else "unknown"))
    manual_confirmation_required = bool(
        suggestion.lifecycle_status == "recommended_draft" or suggestion.need_workorder is True
    )
    return {
        "source_artifact_refs": list(source_refs),
        "target_evidence_bundle_id": target_evidence_bundle_id,
        "supporting_evidence_refs": list(supporting_refs),
        "stale_evidence_disclosure_required": stale_required,
        "stale_refresh_required": bool(input_value(node, "stale_refresh_required", False)),
        "evidence_freshness": evidence_freshness,
        "generated_from_previous_artifact": bool(target_evidence_bundle_id or source_refs),
        "manual_confirmation_required": manual_confirmation_required,
        "draft_only": manual_confirmation_required,
        "dispatch_forbidden": True,
        "approval_requirements": list(state.plan.approval_requirements),
        "selected_findings_summary": input_value(node, "selected_findings_summary", ""),
        "risk_level": input_value(node, "risk_level", "") or suggestion.risk_level,
        "diagnosis_summary": input_value(node, "diagnosis_summary", "") or suggestion.diagnosis_conclusion,
        "report_url": input_value(node, "report_url", "") or _report_artifact_id(state),
    }
