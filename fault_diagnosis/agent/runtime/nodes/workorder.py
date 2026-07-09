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
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from .base import auth_context, build_request, input_value, model_to_dict


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

        request = build_request(state, node, goal="工单建议")
        sql_artifact = _model(
            state.artifacts.get("sql_artifact") or input_value(node, "previous_sql_artifact", {}),
            SqlStepArtifact,
            SqlStepArtifact(success=False, summary="无 SQL 数据"),
        )
        knowledge_artifact = _model(
            state.artifacts.get("knowledge_artifact") or input_value(node, "previous_knowledge_artifact", {}),
            KnowledgeStepArtifact,
            KnowledgeStepArtifact(success=False, query="", error="missing_knowledge_artifact"),
        )
        analysis_artifact = _model(
            state.artifacts.get("analysis_artifact") or input_value(node, "previous_analysis_artifact", {}),
            AnalysisStepArtifact,
            AnalysisStepArtifact(success=False, conclusion="缺少分析产物", error="missing_analysis_artifact"),
        )
        if "sql_artifact" not in state.artifacts and sql_artifact.success:
            state.artifacts["sql_artifact"] = sql_artifact
        if "knowledge_artifact" not in state.artifacts and knowledge_artifact.success:
            state.artifacts["knowledge_artifact"] = knowledge_artifact
        if "analysis_artifact" not in state.artifacts and analysis_artifact.success:
            state.artifacts["analysis_artifact"] = analysis_artifact
        suggestion = build_workorder_suggestion(
            request=request,
            sql_artifact=sql_artifact,
            knowledge_artifact=knowledge_artifact,
            analysis_artifact=analysis_artifact,
        )
        state.artifacts["workorder_suggestion"] = suggestion
        output: dict[str, Any] = {"success": True, "suggestion": model_to_dict(suggestion)}

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
    return any(word in text for word in ("dispatch", "device_control.write", "config.write", "device_action"))


def _model(value: Any, model_type: Any, default: Any) -> Any:
    if isinstance(value, model_type):
        return value
    if isinstance(value, dict) and value:
        return model_type.model_validate(value)
    return default


def _report_artifact_id(state: RuntimeState) -> str | None:
    report = state.artifacts.get("report_artifact")
    if hasattr(report, "report_url"):
        return report.report_url
    if isinstance(report, dict):
        return report.get("report_url") or report.get("report_filename")
    return None
