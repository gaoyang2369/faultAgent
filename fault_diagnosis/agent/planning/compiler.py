"""Compile canonical Goals and decisions into the Phase 2 execution DAG."""

from __future__ import annotations

from uuid import uuid4
from typing import Any

from fault_diagnosis.domain.canonical_turn import (
    CanonicalTurnRequest,
    GoalAuthorizationDecision,
    GoalReadinessDecision,
    GoalSourceResolution,
)

from ..contracts import ExecutionPlan
from ..skills.router import skill_for_capability
from .policy_bridge import NODE_REQUIRED_TOOL, PlanPolicyBridge, tables_for_assets


_EXECUTABLE_SOURCES = {"requires_execution", "source_for_execution"}
_NODE_ORDER = {"rag": 0, "sql": 1, "analysis": 2, "comparison": 3, "report": 4, "workorder": 5, "approval": 6}


class PlanCompiler:
    """Compile without interpreting text, legacy frames, skills or node intent."""

    def __init__(self, bridge: PlanPolicyBridge | None = None) -> None:
        self.bridge = bridge or PlanPolicyBridge()

    def compile(
        self,
        *,
        request: CanonicalTurnRequest | None = None,
        authorization: list[GoalAuthorizationDecision] | None = None,
        readiness: list[GoalReadinessDecision] | None = None,
        sources: list[GoalSourceResolution] | None = None,
        **legacy: Any,
    ) -> ExecutionPlan:
        if request is None:
            from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator
            from fault_diagnosis.domain.canonical_turn import TurnCommand
            from fault_diagnosis.domain.security.permissions import build_auth_context

            intent = legacy.get("intent_frame")
            raw = str(getattr(intent, "raw_message", "") or getattr(intent, "normalized_message", ""))
            preview = ConversationTurnCoordinator().preview_turn(
                TurnCommand(command="preview", thread_id="legacy-unit", user_id="legacy-unit", turn_id="legacy-unit", message_id="legacy-unit", idempotency_key=raw or "legacy-unit", raw_message=raw),
                auth_context=build_auth_context(role="admin"),
            )
            return self.compile(request=preview.request, authorization=preview.authorization, readiness=preview.readiness, sources=preview.source_resolutions)
        if authorization is None or readiness is None or sources is None:
            raise TypeError("per-goal decisions are required")
        auth = {item.goal_id: item for item in authorization}
        ready = {item.goal_id: item for item in readiness}
        source = {item.goal_id: item for item in sources}
        plan_goals = [
            {
                "goal_id": goal.goal_id,
                "goal": goal.capability,
                "skill": skill_for_capability(goal.capability),
                "goal_type": goal.capability,
                "description": goal.capability,
                "device_refs": _slot_values(goal.resolved_slots.get("device")),
                "fault_code_refs": _fault_codes(request),
                "expected_outputs": _deliverables(goal.capability),
                "requested_deliverables": _deliverables(goal.capability),
                "risk_level": "high" if goal.capability == "create_workorder_draft" else "medium",
                "source": "canonical_turn_request",
                "capability": goal.capability,
                "required_slots": list(goal.required_slots),
                "source_policy": "reuse_verified_artifact" if source[goal.goal_id].artifact_id else "collect_new",
                "depends_on_goal_ids": list(goal.dependencies),
                "authorization_status": auth[goal.goal_id].status,
                "drop_reason": auth[goal.goal_id].reason_code if auth[goal.goal_id].status == "denied" else "",
                "origin": goal.origin,
                "clause_index": goal.clause_index,
                "user_requested": goal.user_requested,
                "user_visible": goal.user_visible,
                "readiness_status": ready[goal.goal_id].status,
                "source_resolution_status": source[goal.goal_id].status,
            }
            for goal in request.goals
        ]
        node_specs: list[dict] = []
        for goal in request.goals:
            if auth[goal.goal_id].status != "authorized" or ready[goal.goal_id].status != "ready":
                continue
            if source[goal.goal_id].status not in _EXECUTABLE_SOURCES:
                continue
            for node_type, device in _nodes_for_goal(goal, request, source[goal.goal_id]):
                _merge_node_spec(node_specs, node_type=node_type, device=device, goal_id=goal.goal_id)
        node_specs.sort(key=lambda item: (_NODE_ORDER[item["node_type"]], item["ordinal"]))
        nodes = [
            _compile_node(
                spec,
                request=request,
                source_by_goal=source,
            )
            for spec in node_specs
        ]
        edges = _compile_edges(nodes, request)
        selected_skills = list(dict.fromkeys(node.skill for node in nodes if node.skill))
        metadata = self.bridge.skill_metadata(selected_skills)
        allowed_tools = list(dict.fromkeys(tool for item in metadata for tool in item.allowed_tools))
        required_evidence = list(dict.fromkeys(value for item in metadata for value in item.required_evidence))
        forbidden_tools = [
            tool
            for tool in dict.fromkeys(tool for item in metadata for tool in item.forbidden_tools)
            if tool not in set(allowed_tools)
        ]
        user_goals = [goal for goal in request.goals if goal.user_requested]
        execution_capability = user_goals[0].capability if len(user_goals) == 1 else "composite" if user_goals else ""
        approvals = _approval_requirements(nodes)
        return ExecutionPlan(
            plan_id=f"candidate_{uuid4().hex[:12]}",
            plan_version="v2.canonical-phase2.validated",
            goals=plan_goals,
            nodes=nodes,
            edges=edges,
            required_evidence=required_evidence,
            allowed_tools=allowed_tools,
            forbidden_tools=forbidden_tools,
            risk_level="high" if any(node.node_type == "workorder" for node in nodes) else "medium" if nodes else "low",
            approval_requirements=approvals,
            expected_outputs=list(dict.fromkeys(value for goal in user_goals for value in _deliverables(goal.capability))),
            execution_capability=execution_capability,
            output_contract={
                "required_fields": list(dict.fromkeys(value for item in metadata for value in item.output_contract.required_fields)),
                "forbidden_claims": list(dict.fromkeys(value for item in metadata for value in item.output_contract.forbidden_claims)),
            },
        )


def _nodes_for_goal(goal, request: CanonicalTurnRequest, source: GoalSourceResolution) -> list[tuple[str, str]]:
    capability = goal.capability
    devices = _slot_values(goal.resolved_slots.get("device"))
    codes = _fault_codes(request)
    if goal.dependencies and capability == "generate_report":
        return [("report", "")]
    if goal.dependencies and capability == "create_workorder_draft":
        return [("workorder", ""), ("approval", "")]
    if capability == "explain_fault_code":
        return [("rag", "")]
    if capability == "check_runtime_status":
        return [("sql", device) for device in devices]
    if capability == "compare_runtime_status":
        return [*(("sql", device) for device in devices), ("comparison", "")]
    if capability in {"diagnose_fault", "resolution_recommendation"}:
        if source.status == "source_for_execution":
            return [*(("rag", "") for _ in [0] if codes), ("analysis", "")]
        return [*(("rag", "") for _ in [0] if codes), *(("sql", device) for device in devices), ("analysis", "")]
    if capability == "generate_report":
        if source.status == "source_for_execution":
            return [("report", "")]
        return [*(("sql", device) for device in devices), ("analysis", ""), ("report", "")]
    if capability == "create_workorder_draft":
        if source.status == "source_for_execution":
            return [("workorder", ""), ("approval", "")]
        return [*(("sql", device) for device in devices), ("analysis", ""), ("workorder", ""), ("approval", "")]
    return []


def _merge_node_spec(specs: list[dict], *, node_type: str, device: str, goal_id: str) -> None:
    same_type = [item for item in specs if item["node_type"] == node_type]
    if node_type == "sql":
        existing = next((item for item in same_type if item["device"] == device), None)
    else:
        existing = same_type[0] if same_type else None
    if existing is not None:
        if goal_id not in existing["goal_ids"]:
            existing["goal_ids"].append(goal_id)
        return
    specs.append({"node_type": node_type, "device": device, "goal_ids": [goal_id], "ordinal": len(same_type) + 1})


def _compile_node(spec: dict, *, request: CanonicalTurnRequest, source_by_goal: dict[str, GoalSourceResolution]):
    from ..contracts import PlanNode

    node_type = spec["node_type"]
    goal_ids = list(spec["goal_ids"])
    primary = next(goal for goal in request.goals if goal.goal_id == goal_ids[0])
    source = next((source_by_goal[goal_id] for goal_id in goal_ids if source_by_goal[goal_id].artifact_id), None)
    devices = [spec["device"]] if spec["device"] else list(dict.fromkeys(device for goal_id in goal_ids for device in _goal_devices(request, goal_id, source_by_goal[goal_id])))
    inputs = {
        "goal_ids": goal_ids,
        "query_spec_id": f"query_{primary.goal_id}",
        "target_scope_id": "scope_current",
        "device_refs": devices,
        "fault_code_refs": _fault_codes(request),
        "context_relation": "canonical_turn",
        "requested_output_mode": "report" if primary.capability == "generate_report" else "concise",
        "requested_action": primary.capability if node_type in {"workorder", "approval"} else "",
        "semantic_intent": primary.capability,
        "stale_evidence_disclosure_required": bool(source and source.status == "stale"),
    }
    if source is not None:
        inputs.update(
            target_artifact_id=source.artifact_id,
            target_artifact_type=source.artifact_type,
            source_freshness=source.source_freshness,
        )
        if node_type in {"rag", "workorder"}:
            inputs["source_artifact_refs"] = [{"artifact_id": source.artifact_id, "artifact_type": source.artifact_type}]
    if node_type == "sql":
        inputs["requested_tables"] = tables_for_assets(devices)
    if node_type == "rag":
        inputs["query"] = request.raw_message
    if node_type == "report" and source is None:
        inputs["operation_report_payload"] = "__runtime_artifacts__"
    if node_type == "workorder":
        inputs.update(create_draft=True, draft_only=True, manual_confirmation_required=True)
        if source is not None:
            inputs.update(
                source_policy="reuse_verified_artifact",
                source_selection_reason=source.reason,
                idempotency_key=source.idempotency_key or "",
                reuse_existing_artifact_id=source.reusable_result_artifact_id or "",
                artifact_id=source.reusable_result_artifact_id or "",
            )
    if node_type == "approval":
        inputs["approval_requirements"] = []
    required_tool = NODE_REQUIRED_TOOL.get(node_type)
    return PlanNode(
        node_id=f"{node_type}_{spec['ordinal']}",
        node_type=node_type,
        skill=skill_for_capability(primary.capability),
        goal_id=primary.goal_id,
        goal_ids=goal_ids,
        query_spec_id=f"query_{primary.goal_id}",
        target_scope_id="scope_current",
        failure_policy="continue_degraded" if node_type == "rag" else "block_all" if node_type in {"workorder", "approval"} else "block_dependents",
        inputs=inputs,
        required_tools=[required_tool] if required_tool else [],
        requested_tables=tables_for_assets(devices) if node_type == "sql" else [],
    )


def _compile_edges(nodes, request: CanonicalTurnRequest) -> list[dict]:
    edges: list[dict] = []
    by_type: dict[str, list] = {}
    for node in nodes:
        by_type.setdefault(node.node_type, []).append(node)
    def connect(upstream: str, downstream: str) -> None:
        for left in by_type.get(upstream, []):
            for right in by_type.get(downstream, []):
                if set(left.goal_ids).intersection(right.goal_ids):
                    edges.append({"from": left.node_id, "to": right.node_id})
    for upstream in ("rag", "sql"):
        connect(upstream, "analysis")
    connect("sql", "comparison")
    connect("analysis", "report")
    connect("analysis", "workorder")
    connect("report", "workorder")
    connect("workorder", "approval")
    nodes_by_goal = {
        goal_id: [node for node in nodes if goal_id in node.goal_ids]
        for goal_id in {goal_id for node in nodes for goal_id in node.goal_ids}
    }
    for goal in request.goals:
        for dependency in goal.dependencies:
            if dependency in nodes_by_goal and goal.goal_id in nodes_by_goal:
                edge = {"from": nodes_by_goal[dependency][-1].node_id, "to": nodes_by_goal[goal.goal_id][0].node_id}
                if edge not in edges:
                    edges.append(edge)
    return edges


def _approval_requirements(nodes) -> list[dict]:
    if not any(node.node_type == "workorder" for node in nodes):
        return []
    return [{"requirement_id": "approval_workorder_draft", "type": "workorder_draft", "required": True, "required_role": "engineer", "allowed_next_step": "draft_only", "reason": "工单草稿必须由人工确认后继续。"}]


def _fault_codes(request: CanonicalTurnRequest) -> list[str]:
    return list(dict.fromkeys(entity.value for entity in request.current_parse.entities if entity.kind == "fault_code"))


def _slot_values(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)] if value else []


def _goal_devices(request: CanonicalTurnRequest, goal_id: str, source: GoalSourceResolution | None = None) -> list[str]:
    goal = next(goal for goal in request.goals if goal.goal_id == goal_id)
    values = _slot_values(goal.resolved_slots.get("device"))
    if values:
        return values
    return _slot_values(source.resolved_slots.get("device")) if source is not None else []


def _deliverables(capability: str) -> list[str]:
    return {
        "explain_fault_code": ["fault_code_explanation"],
        "check_runtime_status": ["runtime_status"],
        "compare_runtime_status": ["runtime_comparison"],
        "diagnose_fault": ["diagnosis"],
        "resolution_recommendation": ["recommendations"],
        "generate_report": ["report"],
        "create_workorder_draft": ["workorder_draft"],
    }.get(capability, [])
