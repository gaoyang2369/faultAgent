"""Real workorder suggestion/draft runtime node for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.artifacts import (
    AnalysisArtifactPayload,
    KnowledgeArtifactPayload,
    SqlArtifactPayload,
    WorkorderArtifactPayload,
)
from fault_diagnosis.domain.diagnosis.contracts import AnalysisStepArtifact, KnowledgeStepArtifact, WorkOrderSuggestion
from fault_diagnosis.domain.diagnosis.workorder.drafts import (
    build_pending_workorder_draft_action,
    build_workorder_draft_artifact,
    validate_pending_workorder_draft_action,
)
from fault_diagnosis.domain.diagnosis.workorder.suggestions import build_workorder_suggestion
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from .base import auth_context, build_request, input_value, model_to_dict
from ...artifacts import ArtifactPayloadError, hydrate_artifact_lineage, require_payload, source_envelopes


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
        access_error = str(input_value(node, "artifact_access_error", "") or "")
        devices = [str(item) for item in (input_value(node, "device_refs", []) or []) if str(item)]
        if access_error:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "artifact_access_error": access_error},
                error={"code": access_error, "message": "目标 Artifact 精确读取或 lineage 校验失败。"},
            )
        if len(devices) != 1:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "device_refs": devices},
                error={"code": "workorder_requires_exactly_one_device", "message": "工单草稿必须明确绑定一台设备。"},
            )
        target_id = str(input_value(node, "target_artifact_id", "") or "")
        if action_type == "confirm_workorder_draft":
            return _confirm_draft(node=node, state=state, target_id=target_id, devices=devices)
        reuse_id = str(input_value(node, "reuse_existing_artifact_id", "") or "")
        if reuse_id:
            return _reuse_existing_draft(
                node=node,
                state=state,
                reuse_id=reuse_id,
                source_id=target_id,
                devices=devices,
            )
        if target_id and not hydrate_artifact_lineage(state, target_id):
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "artifact_access_error": "artifact_payload_invalid"},
                error={"code": "artifact_payload_invalid", "message": "目标 Artifact typed payload 无法加载。"},
            )
        sources = source_envelopes("workorder", node=node, state=state)
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
    if not hydrate_artifact_lineage(state, target_id):
        return NodeExecutionOutput(
            status="blocked",
            output={"success": False, "artifact_access_error": "artifact_payload_invalid"},
            error={"code": "artifact_payload_invalid", "message": "工单草稿必须按 thread_id + artifact_id 精确读取。"},
        )
    envelope = state.artifact_registry.get(target_id)
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
    if not hydrate_artifact_lineage(state, reuse_id):
        return NodeExecutionOutput(
            status="blocked",
            output={"success": False, "artifact_access_error": "artifact_payload_invalid"},
            error={"code": "artifact_payload_invalid", "message": "既有工单草稿无法按精确 Artifact ID 读取。"},
        )
    envelope = state.artifact_registry.get(reuse_id)
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
    sql_sources = [item for item in sources if item.artifact_type == "sql_artifact"]
    if len(sql_sources) != 1:
        raise ValueError("artifact_payload_invalid" if not sql_sources else "artifact_source_ambiguous")
    sql_artifact = require_payload(sql_sources[0], SqlArtifactPayload).sql_artifact
    analysis_sources = [item for item in sources if item.artifact_type == "analysis_artifact"]
    if len(analysis_sources) > 1:
        raise ValueError("artifact_source_ambiguous")
    analysis_artifact = (
        require_payload(analysis_sources[0], AnalysisArtifactPayload).structured_analysis.analysis_artifact
        if analysis_sources
        else AnalysisStepArtifact(success=False, conclusion="缺少分析产物", error="missing_analysis_artifact")
    )
    knowledge_sources = [item for item in sources if item.artifact_type == "knowledge_artifact"]
    knowledge_artifact = (
        require_payload(knowledge_sources[0], KnowledgeArtifactPayload).knowledge_artifact
        if len(knowledge_sources) == 1
        else KnowledgeStepArtifact(success=False, query="", error="missing_knowledge_artifact")
    )
    return build_workorder_suggestion(
        request=build_request(state, node, goal="工单建议"),
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
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
    target_evidence_bundle_id = str(input_value(node, "target_evidence_bundle_id", "") or "").strip()
    source_refs = input_value(node, "source_artifact_refs", []) or []
    if not isinstance(source_refs, list):
        source_refs = []
    supporting_refs = input_value(node, "supporting_evidence_refs", []) or []
    if not isinstance(supporting_refs, list):
        supporting_refs = []
    stale_required = bool(
        input_value(node, "stale_evidence_disclosure_required", False)
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
        "evidence_freshness": evidence_freshness,
        "generated_from_previous_artifact": bool(target_evidence_bundle_id or source_refs),
        "manual_confirmation_required": manual_confirmation_required,
        "draft_only": manual_confirmation_required,
        "dispatch_forbidden": True,
        "approval_requirements": list(state.plan.approval_requirements),
        "selected_findings_summary": input_value(node, "selected_findings_summary", ""),
        "risk_level": input_value(node, "risk_level", "") or suggestion.risk_level,
        "diagnosis_summary": input_value(node, "diagnosis_summary", "") or suggestion.diagnosis_conclusion,
        "report_url": input_value(node, "report_url", ""),
    }
