"""Project V2 runtime output into the existing SSE/frontend contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fault_diagnosis.domain.diagnosis.contracts import EvidenceBundle
from fault_diagnosis.agent.output.diagnosis_payload import build_diagnosis_contract_payload
from ..contracts import ExecutionPlan, NodeResult, OutputFrame
from ..evidence import project_ledger_to_evidence_bundle
from .answer import build_output_frame
from .artifact_projection import project_artifact_envelope

if TYPE_CHECKING:
    from ..runtime.state import RuntimeState, RuntimeStatus

_PRIMARY_TASK_TYPE = "primary_" "task_type"
_CANDIDATE_TASK_TYPES = "candidate_" "task_types"
_INTENT_STACK = "intent_" "stack"


def project_start(*, thread_id: str, stream_id: str | None = None, trace_id: str = "") -> dict[str, Any]:
    return {
        "type": "chat_start",
        "thread_id": thread_id,
        "stream_id": stream_id,
        "trace_id": trace_id,
        "stage": "understand",
        "message": "Agent Engine V2 已开始处理请求。",
    }


def project_task_update(*, state: "RuntimeState", current_stage: str | None = None) -> dict[str, Any]:
    todos = _todos_from_plan_and_results(state.plan, state.node_results)
    return {
        "type": "task_update",
        "thread_id": state.thread_id,
        "trace_id": state.trace_id,
        "current_stage": current_stage or _current_stage(todos),
        "todos": todos,
        "summary": _todo_summary(todos),
    }


def project_tool_start(*, state: "RuntimeState", node: dict[str, Any], tool: str | None = None) -> dict[str, Any]:
    node_id = str(node.get("node_id") or "")
    node_type = str(node.get("node_type") or node.get("type") or "tool")
    return {
        "type": "tool_start",
        "tool": tool or _tool_name(node_type),
        "input": _safe_tool_input(node),
        "run_id": f"{node_id or node_type}:start",
        "trace_id": state.trace_id,
        "stage": node_type,
        "current_stage": node_type,
    }


def project_tool_end(*, state: "RuntimeState", result: NodeResult, tool: str | None = None) -> dict[str, Any]:
    return {
        "type": "tool_end",
        "tool": tool or _tool_name(result.node_type),
        "result_preview": _result_preview(result.output),
        "truncated": False,
        "run_id": f"{result.node_id or result.node_type}:end",
        "trace_id": state.trace_id,
        "stage": result.node_type,
        "current_stage": result.node_type,
        "stage_duration_ms": result.duration_ms,
        "evidence_count": len(result.evidence_refs),
        "evidence_ids": list(result.evidence_refs),
    }


def project_token(output_frame: OutputFrame) -> dict[str, Any]:
    return {"type": "token", "content": output_frame.final_answer}


def project_complete(
    *,
    state: "RuntimeState",
    status: "RuntimeStatus",
    output_frame: OutputFrame | None = None,
    evidence_bundle: EvidenceBundle | None = None,
    artifact: Any | None = None,
    cancelled: bool = False,
    cancel_reason: str | None = None,
) -> dict[str, Any]:
    """Build the existing chat_complete payload from V2-native structures."""

    bundle = evidence_bundle or project_ledger_to_evidence_bundle(
        state.evidence_ledger,
        trace_id=state.trace_id,
        task={"plan_id": state.plan.plan_id},
    )
    frame = output_frame or build_output_frame(
        status=status,
        artifacts=state.artifacts,
        evidence_bundle=bundle,
        node_results=state.node_results,
        error=state.errors[-1] if state.errors else None,
        cancelled=cancelled,
        cancel_reason=cancel_reason,
        output_contract=state.plan.output_contract,
    )
    artifact_envelope = artifact or project_artifact_envelope(
        thread_id=state.thread_id,
        output_frame=frame,
        evidence_bundle=bundle,
        artifacts=state.artifacts,
        node_results=state.node_results,
        trace=state.trace_payload(),
        request_summary=_request_summary(state.plan),
        auth_summary=state.auth_context.audit_summary() if state.auth_context else {},
    )
    workorder_payload = _workorder_payload(frame, state.plan)
    final_text = "" if cancelled else frame.final_answer
    todos = [] if cancelled else _todos_from_plan_and_results(state.plan, state.node_results)
    complete: dict[str, Any] = {
        "type": "chat_complete",
        "thread_id": state.thread_id,
        "trace_id": state.trace_id,
        "request_id": state.request_id,
        "runtime": "agent_engine_v2",
        "status": status,
        "task_family": _task_family(state.plan),
        "policy_id": _policy_id(state.plan),
        "final_content": final_text,
        "content": final_text,
        "report_filename": _report_artifact(state.artifacts).get("report_filename"),
        "report_url": _report_artifact(state.artifacts).get("report_url"),
        "decision": _decision_payload(state.plan),
        "resolved_context": {},
        "goal_set": _goal_set(state.plan),
        "composite_output": frame.composite_output.model_dump(mode="json", exclude_none=True),
        "readiness": {"diagnosis": {}, "workorder_action": {}},
        "diagnosis_readiness": {},
        "workorder_action_readiness": {},
        "manual_confirmation": _manual_confirmation_payload(workorder_payload),
        "approval_requirements": workorder_payload.get("approval_requirements", list(state.plan.approval_requirements)),
        "authorization": state.auth_context.audit_summary() if state.auth_context else {},
        "sql_artifact": _artifact_dict(state.artifacts, "sql_artifact"),
        "knowledge_artifact": _artifact_dict(state.artifacts, "knowledge_artifact"),
        "analysis_artifact": _artifact_dict(state.artifacts, "analysis_artifact"),
        "workorder_decision": _artifact_dict(state.artifacts, "workorder_suggestion"),
        "workorder_pending_action": _artifact_dict(state.artifacts, "workorder_pending_action"),
        "workorder_draft": _artifact_dict(state.artifacts, "workorder_draft"),
        "workorder_draft_payload": workorder_payload,
        "report_artifact": _artifact_dict(state.artifacts, "report_artifact"),
        "evidence_bundle": bundle.model_dump(mode="json", exclude_none=True),
        "output_guardrail": frame.guardrail_result,
        "rendered_answer": frame.model_dump(mode="json", exclude_none=True),
        "produced_artifacts": _produced_artifacts(artifact_envelope),
        "referenced_artifacts": [],
        "ui_payload": _ui_payload(frame),
        "workflow_route": _workflow_route(state.plan),
        "workflow_policy": _workflow_policy(state.plan),
        "workflow_result": _workflow_result(state.node_results, status=status),
        "workflow_envelope": {
            "engine": "agent_engine_v2",
            "plan_id": state.plan.plan_id,
            "trace_id": state.trace_id,
            "status": status,
        },
        "todos": todos,
        "artifact": artifact_envelope.model_dump(mode="json", exclude_none=True),
        "node_results": [item.model_dump(mode="json") for item in state.node_results],
        "evidence_ledger": state.evidence_ledger.model_dump(mode="json"),
        "trace": state.trace_payload(),
        "event_count": len(state.trace_events),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if cancelled:
        complete.update({"cancelled": True, "cancel_reason": cancel_reason or "user_stop", "final_content": ""})
    _merge_missing_contract_fields(complete, build_diagnosis_contract_payload(artifact_envelope))
    return complete


def _decision_payload(plan: ExecutionPlan) -> dict[str, Any]:
    node_types = [str(node.get("node_type") or node.get("type") or "") for node in plan.nodes]
    enabled = {node_type: True for node_type in node_types if node_type}
    primary = _primary_task_type(plan)
    return {
        "engine": "agent_engine_v2",
        "plan_id": plan.plan_id,
        "plan_version": plan.plan_version,
        "goal_set": _goal_set(plan),
        "enabled_nodes": enabled,
        "runtime_tools": list(plan.allowed_tools),
        "required_evidence": list(plan.required_evidence),
        "risk_level": plan.risk_level,
        _PRIMARY_TASK_TYPE: primary,
        _CANDIDATE_TASK_TYPES: [primary],
        _INTENT_STACK: [{"intent": primary, "source": "v2_projection"}],
    }


def _workflow_route(plan: ExecutionPlan) -> dict[str, Any]:
    decision = _decision_payload(plan)
    return {
        _PRIMARY_TASK_TYPE: decision[_PRIMARY_TASK_TYPE],
        _CANDIDATE_TASK_TYPES: decision[_CANDIDATE_TASK_TYPES],
        _INTENT_STACK: decision[_INTENT_STACK],
        "task_family": _task_family(plan),
        "policy_id": _policy_id(plan),
        "goal_set": decision["goal_set"],
        "required_evidence": list(plan.required_evidence),
        "risk_level": plan.risk_level,
        "requested_output": _requested_output(plan),
    }


def _workflow_policy(plan: ExecutionPlan) -> dict[str, Any]:
    return {
        "policy_id": _policy_id(plan),
        "allowed_tools": list(plan.allowed_tools),
        "forbidden_tools": list(plan.forbidden_tools),
        "approval_requirements": list(plan.approval_requirements),
        "interrupts": list(plan.interrupts),
    }


def _workflow_result(node_results: list[NodeResult], *, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "nodes": [item.model_dump(mode="json") for item in node_results],
    }


def _todos_from_plan_and_results(plan: ExecutionPlan, node_results: list[NodeResult]) -> list[dict[str, Any]]:
    by_id = {item.node_id: item.status for item in node_results}
    todos: list[dict[str, Any]] = []
    for index, node in enumerate(plan.nodes, start=1):
        node_id = str(node.get("node_id") or f"node_{index}")
        node_type = str(node.get("node_type") or node.get("type") or "node")
        todos.append(
            {
                "id": node_id,
                "title": _node_title(node_type),
                "status": _todo_status(by_id.get(node_id, "pending")),
            }
        )
    return todos


def _todo_status(status: str) -> str:
    if status in {"completed", "skipped"}:
        return "completed"
    if status in {"running", "pending"}:
        return status
    if status in {"blocked", "failed", "cancelled"}:
        return "interrupted"
    return "pending"


def _todo_summary(todos: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(todos),
        "pending": sum(1 for item in todos if item.get("status") == "pending"),
        "in_progress": sum(1 for item in todos if item.get("status") == "running"),
        "completed": sum(1 for item in todos if item.get("status") == "completed"),
        "interrupted": sum(1 for item in todos if item.get("status") == "interrupted"),
    }


def _primary_task_type(plan: ExecutionPlan) -> str:
    requested = _requested_output(plan)
    node_types = {str(node.get("node_type") or node.get("type") or "") for node in plan.nodes}
    if "workorder" in node_types:
        return "workorder_decision"
    if requested == "report" or "report" in node_types:
        return "report_generation"
    if "clarification" in node_types:
        return "clarification"
    if "analysis" in node_types:
        return "fault_diagnosis"
    if "sql" in node_types:
        return "status_query"
    if "rag" in node_types:
        return "knowledge_qa"
    return "knowledge_qa"


def _task_family(plan: ExecutionPlan) -> str:
    primary = _primary_task_type(plan)
    if primary in {"report_generation"}:
        return "report"
    if primary in {"workorder_decision"}:
        return "action"
    if primary in {"clarification", "knowledge_qa"}:
        return "knowledge"
    return "diagnosis"


def _requested_output(plan: ExecutionPlan) -> str:
    return str((plan.expected_outputs or [""])[0] or "")


def _policy_id(plan: ExecutionPlan) -> str:
    return f"agent_engine_v2:{plan.plan_version or 'unknown'}"


def _goal_set(plan: ExecutionPlan) -> dict[str, Any]:
    return {
        "goals": [_dump_model(goal) for goal in plan.goals],
        "expected_outputs": list(plan.expected_outputs),
    }


def _request_summary(plan: ExecutionPlan) -> str:
    for goal in plan.goals:
        text = str(goal.get("description") or goal.get("goal") or "").strip()
        if text:
            return text
    return plan.plan_id or "Agent Engine V2 output"


def _report_artifact(artifacts: dict[str, Any]) -> dict[str, Any]:
    return _artifact_dict(artifacts, "report_artifact")


def _artifact_dict(artifacts: dict[str, Any], key: str) -> dict[str, Any]:
    value = artifacts.get(key)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return dict(value) if isinstance(value, dict) else {}


def _dump_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return dict(value) if isinstance(value, dict) else value


def _produced_artifacts(artifact: Any) -> list[dict[str, Any]]:
    envelope = artifact.model_dump(mode="json", exclude_none=True) if hasattr(artifact, "model_dump") else {}
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
    manifests = payload.get("artifact_manifests") if isinstance(payload.get("artifact_manifests"), list) else []
    if manifests:
        return [
            {
                "artifact_id": str(item.get("artifact_id") or "").strip(),
                "artifact_type": str(item.get("artifact_type") or "artifact").strip(),
                "role": "produced",
                "created_in_current_turn": True,
                "display_policy": "hide_card",
                "title": item.get("diagnosis_summary") or item.get("artifact_type") or "V2 诊断产物",
                "url": item.get("report_url") or item.get("report_filename"),
                "source_artifact_id": item.get("linked_analysis_artifact_id") or item.get("linked_sql_artifact_id"),
                "followupable": bool(item.get("followupable")),
                "actionable": bool(item.get("actionable")),
                "reportable": bool(item.get("reportable")),
                "manifest": item,
            }
            for item in manifests
            if str(item.get("artifact_id") or "").strip()
        ]
    return [
        {
            "artifact_id": envelope.get("created_at"),
            "artifact_type": envelope.get("workflow_type"),
            "role": "produced",
            "created_in_current_turn": True,
            "display_policy": "hide_card",
            "title": envelope.get("request_summary") or "V2 诊断产物",
            "url": envelope.get("report_filename"),
        }
    ]


def _ui_payload(frame: OutputFrame) -> dict[str, Any]:
    if frame.answer_variant == "report_ready":
        ui_type = "report_status"
    elif frame.answer_variant in {"workorder_draft_ready", "workorder_suggestion"}:
        ui_type = "workorder_draft_status"
    elif frame.answer_variant == "diagnosis_answer":
        ui_type = "diagnosis_card"
    elif frame.answer_variant in {"status_brief", "status_brief_v2", "status_incomplete"}:
        ui_type = "status_card"
    elif frame.answer_variant in {"blocked", "permission_denied"}:
        ui_type = "access_denied"
    else:
        ui_type = "text_only"
    return {
        "type": ui_type,
        "task_type": frame.answer_variant,
        "report_generated": frame.answer_variant == "report_ready",
        "workorder_draft_ready": frame.answer_variant == "workorder_draft_ready",
        "workorder_suggestion": frame.answer_variant == "workorder_suggestion",
    }


def _workorder_payload(frame: OutputFrame, plan: ExecutionPlan) -> dict[str, Any]:
    payload = dict(frame.workorder_draft_payload or {})
    if not payload and frame.answer_variant not in {"workorder_draft_ready", "workorder_suggestion"}:
        return {}
    requirements = payload.get("approval_requirements")
    if not requirements and plan.approval_requirements:
        payload["approval_requirements"] = list(plan.approval_requirements)
    payload.setdefault("manual_confirmation_required", True)
    payload.setdefault("draft_only", True)
    payload.setdefault("dispatch_forbidden", True)
    return payload


def _manual_confirmation_payload(workorder_payload: dict[str, Any]) -> dict[str, Any]:
    if not workorder_payload:
        return {}
    return {
        "required": bool(workorder_payload.get("manual_confirmation_required", True)),
        "manual_confirmation_required": bool(workorder_payload.get("manual_confirmation_required", True)),
        "draft_only": bool(workorder_payload.get("draft_only", True)),
        "dispatch_forbidden": bool(workorder_payload.get("dispatch_forbidden", True)),
        "approval_requirements": list(workorder_payload.get("approval_requirements") or []),
    }


def _merge_missing_contract_fields(payload: dict[str, Any], contract_payload: dict[str, Any]) -> None:
    for key, value in contract_payload.items():
        if key not in payload or payload.get(key) in (None, [], {}):
            payload[key] = value


def _current_stage(todos: list[dict[str, Any]]) -> str:
    for item in todos:
        if item.get("status") in {"running", "pending"}:
            return str(item.get("id") or "runtime")
    return "complete"


def _node_title(node_type: str) -> str:
    return {
        "sql": "查询运行数据",
        "rag": "检索知识库",
        "kg": "查询知识图谱",
        "analysis": "分析诊断结论",
        "report": "生成报告",
        "workorder": "生成工单建议",
        "approval": "等待人工确认",
        "clarification": "澄清问题",
    }.get(node_type, node_type or "执行节点")


def _tool_name(node_type: str) -> str:
    return {
        "sql": "sql_db_query",
        "rag": "query_knowledge_base",
        "report": "save_report",
    }.get(node_type, node_type or "v2_node")


def _safe_tool_input(node: dict[str, Any]) -> dict[str, Any]:
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    safe = {}
    for key, value in inputs.items():
        if "password" in str(key).lower() or "token" in str(key).lower():
            safe[str(key)] = "***"
        else:
            safe[str(key)] = value
    return safe


def _result_preview(output: Any) -> str:
    if isinstance(output, dict):
        for key in ("summary", "result_preview", "error"):
            if output.get(key):
                return str(output[key])[:240]
        return str(output)[:240]
    return str(output or "")[:240]
