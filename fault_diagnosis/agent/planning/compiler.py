"""Compile canonical Goals and decisions into the production execution DAG."""

from __future__ import annotations

import hashlib
from uuid import uuid4
from typing import Any

from fault_diagnosis.domain.canonical_turn import (
    CanonicalTurnRequest,
    GoalAuthorizationDecision,
    GoalReadinessDecision,
    GoalSourceResolution,
)

from ..contracts import ArtifactRoleBinding, ExecutionPlan
from ..skills.router import skill_for_capability
from .policy_bridge import NODE_REQUIRED_TOOL, PlanPolicyBridge, tables_for_assets
from .versions import CANONICAL_PLAN_VERSION


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
                "device_refs": _goal_devices(request, goal.goal_id, source[goal.goal_id]),
                "fault_code_refs": _goal_fault_codes(request, goal, source[goal.goal_id]),
                "expected_outputs": _deliverables(goal.capability),
                "requested_deliverables": _deliverables(goal.capability),
                "risk_level": "high" if goal.capability in {"create_workorder_draft", "dispatch_workorder"} else "medium",
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
                "source_artifact_id": source[goal.goal_id].artifact_id or "",
                "source_artifact_type": source[goal.goal_id].artifact_type or "",
                "source_freshness": source[goal.goal_id].source_freshness,
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
        _bind_artifact_roles(nodes, edges=edges, source_by_goal=source)
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
            plan_version=CANONICAL_PLAN_VERSION,
            goals=plan_goals,
            nodes=nodes,
            edges=edges,
            required_evidence=required_evidence,
            allowed_tools=allowed_tools,
            forbidden_tools=forbidden_tools,
            risk_level="high" if any(node.node_type == "workorder" and node.inputs.get("create_draft") for node in nodes) else "medium" if nodes else "low",
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
    devices = _goal_devices(request, goal.goal_id, source)
    codes = _goal_fault_codes(request, goal, source)
    if goal.dependencies and capability == "generate_report":
        dependency_capabilities = {
            item.capability for item in request.goals if item.goal_id in goal.dependencies
        }
        return [("analysis", ""), ("report", "")] if dependency_capabilities.intersection(
            {"check_runtime_status", "compare_runtime_status"}
        ) else [("report", "")]
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
            return [("analysis", ""), ("report", "")] if source.artifact_type == "sql_artifact" else [("report", "")]
        return [*(("sql", device) for device in devices), ("analysis", ""), ("report", "")]
    if capability == "create_workorder_draft":
        if source.status == "source_for_execution":
            return [("workorder", ""), ("approval", "")]
        return [*(("sql", device) for device in devices), ("analysis", ""), ("workorder", ""), ("approval", "")]
    if capability == "evaluate_workorder_need":
        return [("workorder", "")]
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
    node_id = f"{node_type}_{spec['ordinal']}"
    planned_output_artifact_id = _planned_artifact_id(request.request_id, node_id, node_type)
    resolved_slots = {**primary.resolved_slots, **(source.resolved_slots if source is not None else {})}
    fault_codes = list(dict.fromkeys(
        code
        for goal_id in goal_ids
        for code in _goal_fault_codes(
            request,
            next(item for item in request.goals if item.goal_id == goal_id),
            source_by_goal[goal_id],
        )
    ))
    inputs = {
        "goal_id": primary.goal_id,
        "node_id": node_id,
        "goal_ids": goal_ids,
        "query_spec_id": f"query_{primary.goal_id}",
        "target_scope_id": "scope_current",
        "artifact_role_bindings": [],
        "device_refs": devices,
        "fault_code_refs": fault_codes,
        "context_relation": "canonical_turn",
        "requested_output_mode": "report" if primary.capability == "generate_report" else "concise",
        "canonical_capability": primary.capability,
        "canonical_raw_message": request.raw_message,
        "canonical_resolved_slots": resolved_slots,
        "source_freshness": source.source_freshness if source is not None else "unknown",
        "artifact_id": planned_output_artifact_id,
    }
    if node_type == "sql":
        inputs["requested_tables"] = tables_for_assets(devices)
    if node_type == "rag":
        inputs["query"] = request.raw_message
    if node_type == "workorder":
        create_draft = primary.capability == "create_workorder_draft"
        inputs.update(
            create_draft=create_draft,
            draft_only=create_draft,
            manual_confirmation_required=create_draft,
            action_type=primary.capability,
        )
        if source is not None:
            inputs.update(
                source_policy="reuse_verified_artifact",
                source_selection_reason=source.reason,
                idempotency_key=source.idempotency_key or "",
                reuse_existing_artifact_id=source.reusable_result_artifact_id or "",
            )
    if node_type == "approval":
        inputs["approval_requirements"] = []
    required_tool = NODE_REQUIRED_TOOL.get(node_type)
    return PlanNode(
        node_id=node_id,
        node_type=node_type,
        skill=skill_for_capability(primary.capability),
        goal_id=primary.goal_id,
        goal_ids=goal_ids,
        query_spec_id=f"query_{primary.goal_id}",
        target_scope_id="scope_current",
        failure_policy="continue_degraded" if node_type == "rag" else "block_all" if node_type == "approval" else "block_dependents",
        condition=primary.execution_condition.model_dump(mode="json") if primary.execution_condition else {},
        inputs=inputs,
        required_tools=[required_tool] if required_tool else [],
        requested_tables=tables_for_assets(devices) if node_type == "sql" else [],
        planned_output_artifact_id=planned_output_artifact_id,
    )


def _planned_artifact_id(request_id: str, node_id: str, node_type: str) -> str:
    digest = hashlib.sha256(f"{request_id}|{node_id}|{node_type}".encode("utf-8")).hexdigest()[:32]
    return f"art_{node_type}_{digest}"


def _bind_artifact_roles(nodes, *, edges: list[dict], source_by_goal: dict[str, GoalSourceResolution]) -> None:
    """Bind exact external or planned dependency artifacts before runtime starts."""

    by_id = {node.node_id: node for node in nodes}
    incoming: dict[str, list] = {node.node_id: [] for node in nodes}
    for edge in edges:
        source = by_id.get(str(edge.get("from") or ""))
        target = by_id.get(str(edge.get("to") or ""))
        if source is not None and target is not None:
            incoming[target.node_id].append(source)

    for node in nodes:
        bindings: list[ArtifactRoleBinding] = []
        external = next(
            (
                source_by_goal[goal_id]
                for goal_id in node.goal_ids
                if source_by_goal[goal_id].artifact_id
            ),
            None,
        )
        if node.node_type == "analysis":
            for producer in incoming[node.node_id]:
                role = "runtime_sql_source" if producer.node_type == "sql" else "knowledge_source" if producer.node_type == "rag" else None
                if role:
                    bindings.append(_producer_binding(node, producer, role=role, required=role == "runtime_sql_source"))
            if external is not None and external.artifact_type in {"sql_artifact", "knowledge_artifact"}:
                role = "runtime_sql_source" if external.artifact_type == "sql_artifact" else "knowledge_source"
                bindings.append(_external_binding(node, external, role=role, required=role == "runtime_sql_source"))
        elif node.node_type == "comparison":
            device_order = [str(item) for item in node.inputs.get("device_refs", [])]
            for producer in incoming[node.node_id]:
                if producer.node_type != "sql":
                    continue
                producer_devices = [str(item) for item in producer.inputs.get("device_refs", [])]
                device = producer_devices[0] if len(producer_devices) == 1 else ""
                order = device_order.index(device) if device in device_order else len(bindings)
                binding = _producer_binding(node, producer, role="comparison_member", required=True)
                bindings.append(binding.model_copy(update={"device_ref": device or None, "member_order": order}))
        elif node.node_type == "report":
            analysis = next((item for item in incoming[node.node_id] if item.node_type == "analysis"), None)
            if analysis is not None:
                bindings.append(_producer_binding(node, analysis, role="report_source", required=True))
            elif external is not None and external.artifact_type == "analysis_artifact":
                bindings.append(_external_binding(node, external, role="report_source", required=True))
                if external.tabular_source_artifact_id:
                    bindings.append(
                        ArtifactRoleBinding(
                            goal_id=node.goal_id,
                            node_id=node.node_id,
                            role="tabular_source",
                            artifact_id=external.tabular_source_artifact_id,
                            artifact_type="sql_artifact",
                            required=False,
                        )
                    )
        elif node.node_type == "workorder":
            producer = next(
                (item for item in incoming[node.node_id] if item.node_type == "report"),
                next((item for item in incoming[node.node_id] if item.node_type == "analysis"), None),
            )
            if producer is not None:
                bindings.append(_producer_binding(node, producer, role="workorder_source", required=True))
            elif external is not None and external.artifact_type in {"analysis_artifact", "report_artifact"}:
                bindings.append(_external_binding(node, external, role="workorder_source", required=True))
        node.inputs["artifact_role_bindings"] = [item.model_dump(mode="json") for item in bindings]


def _producer_binding(node, producer, *, role: str, required: bool) -> ArtifactRoleBinding:
    return ArtifactRoleBinding(
        goal_id=node.goal_id,
        node_id=node.node_id,
        role=role,
        artifact_id=producer.planned_output_artifact_id,
        artifact_type=_artifact_type_for_node(producer.node_type),
        producer_goal_id=producer.goal_id,
        producer_node_id=producer.node_id,
        required=required,
    )


def _external_binding(node, source: GoalSourceResolution, *, role: str, required: bool) -> ArtifactRoleBinding:
    return ArtifactRoleBinding(
        goal_id=node.goal_id,
        node_id=node.node_id,
        role=role,
        artifact_id=str(source.artifact_id or ""),
        artifact_type=str(source.artifact_type or ""),
        required=required,
    )


def _artifact_type_for_node(node_type: str) -> str:
    return {
        "sql": "sql_artifact",
        "rag": "knowledge_artifact",
        "analysis": "analysis_artifact",
        "comparison": "comparison_artifact",
        "report": "report_artifact",
        "workorder": "workorder_artifact",
    }.get(node_type, "")


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
    connect("sql", "report")
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
                if goal.capability == "generate_report":
                    for producer in nodes_by_goal[dependency]:
                        if producer.node_type != "sql":
                            continue
                        tabular_edge = {"from": producer.node_id, "to": nodes_by_goal[goal.goal_id][0].node_id}
                        if tabular_edge not in edges:
                            edges.append(tabular_edge)
    return edges


def _approval_requirements(nodes) -> list[dict]:
    if not any(node.node_type == "workorder" and node.inputs.get("create_draft") for node in nodes):
        return []
    return [{"requirement_id": "approval_workorder_draft", "type": "workorder_draft", "required": True, "required_role": "engineer", "allowed_next_step": "draft_only", "reason": "工单草稿必须由人工确认后继续。"}]


def _fault_codes(request: CanonicalTurnRequest) -> list[str]:
    return list(dict.fromkeys(entity.value for entity in request.current_parse.entities if entity.kind == "fault_code"))


def _goal_fault_codes(request: CanonicalTurnRequest, goal, source: GoalSourceResolution | None) -> list[str]:  # noqa: ANN001
    values = _slot_values(goal.resolved_slots.get("fault_code"))
    if not values and source is not None:
        values = _slot_values(source.resolved_slots.get("fault_code"))
    return values or _fault_codes(request)


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
        "evaluate_workorder_need": ["workorder_need_assessment"],
    }.get(capability, [])
