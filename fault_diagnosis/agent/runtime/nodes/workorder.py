"""Real workorder suggestion/draft runtime node for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.artifacts import (
    AnalysisArtifactPayload,
    ReportArtifactPayload,
    WorkorderArtifactPayload,
)
from fault_diagnosis.domain.diagnosis.contracts import WorkOrderSuggestion
from fault_diagnosis.domain.diagnosis.runtime_status import RuntimeStatusAssessment
from fault_diagnosis.domain.diagnosis.workorder.drafts import (
    build_pending_workorder_draft_action,
    build_workorder_draft_artifact,
    validate_pending_workorder_draft_action,
)
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from .base import auth_context, input_value, model_to_dict
from ...artifacts import (
    ArtifactPayloadError,
    artifact_role_bindings,
    load_bound_artifacts,
    load_exact_artifact,
    require_payload,
)


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
        devices = [str(item) for item in (input_value(node, "device_refs", []) or []) if str(item)]
        if len(devices) != 1:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "device_refs": devices},
                error={"code": "workorder_requires_exactly_one_device", "message": "工单草稿必须明确绑定一台设备。"},
            )
        try:
            sources = load_bound_artifacts(node=node, state=state, roles=("workorder_source",))
        except ArtifactPayloadError as exc:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "artifact_access_error": exc.code},
                error={"code": exc.code, "message": exc.message},
            )
        if len(sources) != 1:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "source_count": len(sources)},
                error={"code": "workorder_source_cardinality", "message": "工单需要唯一的 Analysis 或 Report 来源。"},
            )
        source_id = sources[0].artifact_id
        reuse_id = str(input_value(node, "reuse_existing_artifact_id", "") or "")
        if reuse_id:
            return _reuse_existing_draft(
                node=node,
                state=state,
                reuse_id=reuse_id,
                source_id=source_id,
                devices=devices,
            )
        try:
            suggestion = _build_typed_suggestion(node=node, state=state, sources=sources)
        except ArtifactPayloadError as exc:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "artifact_access_error": exc.code},
                error={"code": exc.code, "message": exc.message},
            )
        except ValueError as exc:
            code = str(exc) or "artifact_payload_invalid"
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "artifact_access_error": code},
                error={"code": code, "message": "工单草稿需要唯一、完整的 typed Analysis/SQL 来源。"},
            )
        output: dict[str, Any] = {
            "success": True,
            "suggestion": model_to_dict(suggestion),
            **_source_projection(node=node, state=state, suggestion=suggestion),
        }

        if suggestion.lifecycle_status == "recommended_draft" and bool(input_value(node, "create_draft", True)):
            pending = build_pending_workorder_draft_action(
                thread_id=state.thread_id or state.trace_id or "v2_runtime",
                suggestion=suggestion,
                source_diagnosis_artifact_id=_diagnosis_source_id(sources),
                source_report_artifact_id=_report_artifact_id(sources),
                recommendation_artifact_id=str(input_value(node, "artifact_id", "") or node.get("artifact_id") or ""),
                stale_refresh_required=False,
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
                )
            draft = build_workorder_draft_artifact(
                thread_id=state.thread_id or state.trace_id or "v2_runtime",
                suggestion=suggestion,
                pending_action=model_to_dict(pending),
                source_diagnosis_artifact_id=_diagnosis_source_id(sources),
                source_report_artifact_id=_report_artifact_id(sources),
                stale=bool(input_value(node, "stale_evidence_disclosure_required", False)),
            )
            canonical_id = str(input_value(node, "artifact_id", "") or node.get("artifact_id") or "")
            if canonical_id:
                draft.draft_id = canonical_id
            output["pending_action"] = model_to_dict(pending)
            output["draft"] = model_to_dict(draft)
            return NodeExecutionOutput(
                output=output,
                artifact_payload=WorkorderArtifactPayload(
                    workorder_suggestion=suggestion,
                    workorder_draft=draft,
                    pending_action=pending,
                ),
            )
        return NodeExecutionOutput(output=output)


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


def _confirm_draft(*, node: dict[str, Any], state: RuntimeState, target_id: str, devices: list[str]) -> NodeExecutionOutput:
    try:
        envelope = load_exact_artifact(
            state=state,
            artifact_id=target_id,
            expected_type="workorder_artifact",
            expected_devices=devices,
        )
    except ArtifactPayloadError as exc:
        return NodeExecutionOutput(
            status="blocked",
            output={"success": False, "artifact_access_error": exc.code},
            error={"code": exc.code, "message": exc.message},
        )
    if envelope is None or envelope.artifact_type != "workorder_artifact" or set(envelope.lineage.subject_device_refs) != set(devices):
        return NodeExecutionOutput(
            status="blocked",
            output={"success": False, "artifact_access_error": "workorder_draft_payload_invalid"},
            error={"code": "workorder_draft_payload_invalid", "message": "目标 Artifact 不含可确认的结构化草稿。"},
        )
    try:
        payload = require_payload(envelope, WorkorderArtifactPayload)
    except ArtifactPayloadError as exc:
        return NodeExecutionOutput(
            status="blocked",
            output={"success": False, "artifact_access_error": exc.code},
            error={"code": exc.code, "message": exc.message},
        )
    draft = payload.workorder_draft
    pending = payload.pending_action.model_copy(
        update={
            "status": "confirmed_pending_dispatch_forbidden",
            "consumed_by_artifact_id": str(input_value(node, "artifact_id", "") or node.get("artifact_id") or ""),
        }
    )
    return NodeExecutionOutput(
        output={
            "success": True,
            "status": pending.status,
            "draft": model_to_dict(draft),
            "pending_action": model_to_dict(pending),
            "source_artifact_refs": [{"artifact_id": target_id, "artifact_type": "workorder_artifact"}],
            "manual_confirmation_required": False,
            "dispatch_performed": False,
            "dispatch_forbidden": True,
        },
        artifact_payload=WorkorderArtifactPayload(
            workorder_suggestion=payload.workorder_suggestion,
            workorder_draft=draft,
            pending_action=pending,
        ),
    )


def _reuse_existing_draft(
    *,
    node: dict[str, Any],
    state: RuntimeState,
    reuse_id: str,
    source_id: str,
    devices: list[str],
) -> NodeExecutionOutput:
    try:
        envelope = load_exact_artifact(
            state=state,
            artifact_id=reuse_id,
            expected_type="workorder_artifact",
            expected_devices=devices,
        )
    except ArtifactPayloadError as exc:
        return NodeExecutionOutput(
            status="blocked",
            output={"success": False, "artifact_access_error": exc.code},
            error={"code": exc.code, "message": exc.message},
        )
    if envelope is None or envelope.artifact_type != "workorder_artifact" or set(envelope.lineage.subject_device_refs) != set(devices):
        return NodeExecutionOutput(
            status="blocked",
            output={"success": False, "artifact_access_error": "workorder_draft_payload_invalid"},
            error={"code": "workorder_draft_payload_invalid", "message": "既有工单 Artifact 类型或设备不匹配。"},
        )
    if source_id not in envelope.lineage.source_artifact_ids:
        return NodeExecutionOutput(
            status="blocked",
            output={"success": False, "artifact_access_error": "workorder_idempotency_source_mismatch"},
            error={"code": "workorder_idempotency_source_mismatch", "message": "既有工单草稿与当前来源不一致。"},
        )
    try:
        payload = require_payload(envelope, WorkorderArtifactPayload)
    except ArtifactPayloadError as exc:
        return NodeExecutionOutput(
            status="blocked",
            output={"success": False, "artifact_access_error": exc.code},
            error={"code": exc.code, "message": exc.message},
        )
    draft = payload.workorder_draft
    pending = payload.pending_action
    return NodeExecutionOutput(
        output={
            "success": True,
            "draft": model_to_dict(draft),
            "pending_action": model_to_dict(pending),
            "source_artifact_refs": [
                {
                    "artifact_id": source_id,
                    "artifact_type": str(input_value(node, "target_artifact_type", "") or ""),
                }
            ],
            "manual_confirmation_required": True,
            "draft_only": True,
            "dispatch_forbidden": True,
            "idempotency_key": str(input_value(node, "idempotency_key", "") or ""),
            "idempotency_result": "reused",
            "reused_artifact_id": reuse_id,
        },
        reused_artifact_envelope=envelope,
    )


def _build_typed_suggestion(*, node: dict[str, Any], state: RuntimeState, sources: list[Any]) -> WorkOrderSuggestion:
    if len(sources) != 1:
        raise ValueError("workorder_source_cardinality")
    source = sources[0]
    if source.artifact_type == "analysis_artifact":
        payload = require_payload(source, AnalysisArtifactPayload)
        snapshot = payload.report_input_snapshot
    elif source.artifact_type == "report_artifact":
        payload = require_payload(source, ReportArtifactPayload)
        snapshot = payload.report_input_snapshot
    else:
        raise ValueError("artifact_role_type_mismatch")
    if snapshot is None:
        raise ValueError("legacy_analysis_missing_report_input_snapshot")
    runtime = RuntimeStatusAssessment.model_validate(
        {
            key: value
            for key, value in snapshot.runtime_summary.items()
            if key in RuntimeStatusAssessment.model_fields
        }
    )
    severity_rank = {"unknown": 0, "normal": 1, "notice": 2, "warning": 3, "high": 4, "critical": 5}
    needs_draft = bool(
        runtime.runtime_status in {"attention", "abnormal"}
        or snapshot.fault_codes
        or severity_rank.get(str(snapshot.severity or "unknown"), 0) >= 3
    )
    device = snapshot.device_refs[0] if len(snapshot.device_refs) == 1 else ""
    code = snapshot.fault_codes[0] if snapshot.fault_codes else None
    recommendations = list(snapshot.recommendations) or ["复核现场状态并确认异常是否持续"]
    return WorkOrderSuggestion(
        lifecycle_status="recommended_draft" if needs_draft else "not_recommended",
        need_workorder=needs_draft,
        reason="ReportInputSnapshot 显示需进一步处置。" if needs_draft else "ReportInputSnapshot 未达到工单建议条件。",
        workorder_type="运行异常排查" if needs_draft else "",
        priority="P1" if runtime.runtime_status == "abnormal" or snapshot.severity in {"high", "critical"} else "P2",
        priority_label="高优先级" if runtime.runtime_status == "abnormal" or snapshot.severity in {"high", "critical"} else "中优先级",
        risk_level="高" if runtime.runtime_status == "abnormal" or snapshot.severity in {"high", "critical"} else "中" if needs_draft else "低",
        assignee_role="电气维护人员" if needs_draft else "",
        suggested_completion_window="4小时内" if runtime.runtime_status == "abnormal" else "24小时内" if needs_draft else "",
        diagnosis_conclusion=snapshot.diagnosis_summary,
        key_evidence=[str(item.get("summary") or "") for item in snapshot.structured_findings if item.get("summary")][:6],
        processing_steps=recommendations,
        acceptance_criteria=[f"{code} 不再持续出现" if code else "运行状态恢复正常", "复测结果已记录"],
        task_mappings=[{"evidence": snapshot.diagnosis_summary, "tasks": recommendations}],
        equipment_object=device,
        fault_code=code,
        title=f"{device} {code or '运行异常'} 排查" if needs_draft else "",
        trigger_source="故障诊断 Agent",
        status="待确认" if needs_draft else "不建议",
    )


def _diagnosis_source_id(sources: list[Any]) -> str:
    analysis = [item.artifact_id for item in sources if item.artifact_type == "analysis_artifact"]
    if len(analysis) == 1:
        return analysis[0]
    sql = [item.artifact_id for item in sources if item.artifact_type == "sql_artifact"]
    return sql[0] if len(sql) == 1 else ""


def _report_artifact_id(sources: list[Any]) -> str | None:
    values = [item.artifact_id for item in sources if item.artifact_type == "report_artifact"]
    return values[0] if len(values) == 1 else None


def _source_projection(*, node: dict[str, Any], state: RuntimeState, suggestion: WorkOrderSuggestion) -> dict[str, Any]:
    bindings = artifact_role_bindings(node, "workorder_source")
    source_refs = [
        {"artifact_id": item.artifact_id, "artifact_type": item.artifact_type}
        for item in bindings
    ]
    evidence_freshness = str(input_value(node, "source_freshness", "") or "unknown")
    manual_confirmation_required = bool(
        suggestion.lifecycle_status == "recommended_draft" or suggestion.need_workorder is True
    )
    return {
        "source_artifact_refs": list(source_refs),
        "supporting_evidence_refs": [],
        "stale_evidence_disclosure_required": evidence_freshness == "stale",
        "evidence_freshness": evidence_freshness,
        "generated_from_previous_artifact": bool(source_refs),
        "manual_confirmation_required": manual_confirmation_required,
        "draft_only": manual_confirmation_required,
        "dispatch_forbidden": True,
        "approval_requirements": list(state.plan.approval_requirements),
        "selected_findings_summary": input_value(node, "selected_findings_summary", ""),
        "risk_level": input_value(node, "risk_level", "") or suggestion.risk_level,
        "diagnosis_summary": input_value(node, "diagnosis_summary", "") or suggestion.diagnosis_conclusion,
        "report_url": input_value(node, "report_url", ""),
    }
