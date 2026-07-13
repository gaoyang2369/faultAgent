"""Candidate ExecutionPlan compiler for Agent Engine V2."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..contracts import ContextFrame, EffectiveRequestFrame, ExecutionPlan, IntentFrame, SkillRoute
from .policy_bridge import NODE_REQUIRED_TOOL, PlanPolicyBridge, tables_for_assets


class PlanCompiler:
    """Compile selected skills into a side-effect-free candidate plan."""

    def __init__(self, bridge: PlanPolicyBridge | None = None) -> None:
        self.bridge = bridge or PlanPolicyBridge()

    def compile(
        self,
        *,
        skill_route: SkillRoute,
        intent_frame: IntentFrame,
        context_frame: ContextFrame,
        effective_request_frame: EffectiveRequestFrame | None = None,
        llm_candidate_plan: ExecutionPlan | dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        if llm_candidate_plan is not None:
            return _coerce_plan(llm_candidate_plan)

        skill_names = list(skill_route.selected_skills or ([skill_route.primary_skill] if skill_route.primary_skill else []))
        metadata_items = self.bridge.skill_metadata(skill_names)
        if effective_request_frame is not None:
            assets = list(dict.fromkeys(effective_request_frame.effective_device_refs))
            fault_codes = list(dict.fromkeys(effective_request_frame.effective_fault_code_refs))
        else:
            assets = list(dict.fromkeys(intent_frame.device_refs + _as_list(skill_route.skill_inputs.get("device_refs"))))
            fault_codes = list(dict.fromkeys(intent_frame.fault_code_refs + _as_list(skill_route.skill_inputs.get("fault_code_refs"))))
        requested_tables = tables_for_assets(assets)

        goals: list[dict[str, Any]] = []
        fallbacks: list[dict[str, Any]] = []
        required_evidence: list[str] = []
        allowed_tools: list[str] = []
        forbidden_tools: list[str] = []
        expected_outputs: list[str] = []
        risk_levels: list[str] = []

        goal_by_skill: dict[str, str] = {}
        for skill_index, metadata in enumerate(metadata_items, start=1):
            goal_id = f"goal_{skill_index}_{metadata.name}"
            goal_by_skill[metadata.name] = goal_id
            goals.append(
                {
                    "goal_id": goal_id,
                    "skill": metadata.name,
                    "goal_type": self.bridge.goal_type_for_skill(metadata.name),
                    "description": metadata.description,
                    "device_refs": list(assets),
                    "fault_code_refs": list(fault_codes),
                    "expected_outputs": list(metadata.output_variants),
                    "risk_level": metadata.risk_level,
                    "source": "llm_candidate" if intent_frame.model_trace.get("llm_used") else "skill_compiler",
                }
            )
            if metadata.fallback_policy:
                fallbacks.append({"skill": metadata.name, "policy": metadata.fallback_policy})
            required_evidence.extend(metadata.required_evidence)
            allowed_tools.extend(metadata.allowed_tools)
            forbidden_tools.extend(metadata.forbidden_tools)
            expected_outputs.extend(metadata.output_variants)
            risk_levels.append(metadata.risk_level)

        if effective_request_frame is not None and effective_request_frame.requested_goal_set.goals:
            authorized = set(effective_request_frame.authorized_goal_ids)
            effective_goals = list(effective_request_frame.requested_goal_set.goals)
            goals = [
                {
                    "goal_id": goal.goal_id,
                    "goal": goal.capability,
                    "skill": _skill_for_capability(goal.capability),
                    "goal_type": goal.capability,
                    "description": goal.capability,
                    "device_refs": list(effective_request_frame.target_scope.resolved_devices),
                    "fault_code_refs": list(fault_codes),
                    "expected_outputs": list(goal.requested_deliverables),
                    "risk_level": "high" if goal.capability == "create_workorder_draft" else "medium",
                    "source": "effective_goal_set",
                    "capability": goal.capability,
                    "target_scope_id": goal.target_scope_id,
                    "requested_deliverables": list(goal.requested_deliverables),
                    "required_slots": list(goal.required_slots),
                    "optional_slots": list(goal.optional_slots),
                    "evidence_requirements": list(goal.evidence_requirements),
                    "source_policy": goal.source_policy,
                    "priority": goal.priority,
                    "confidence": goal.confidence,
                    "depends_on_goal_ids": list(goal.depends_on_goal_ids),
                    "authorization_status": "authorized" if not authorized or goal.goal_id in authorized else "denied",
                    "drop_reason": next(
                        (
                            str(item.get("reason") or "")
                            for item in effective_request_frame.dropped_goals
                            if item.get("goal_id") == goal.goal_id
                        ),
                        "",
                    ),
                }
                for goal in effective_goals
            ]

        nodes = _normalized_nodes(
            skill_names=skill_names,
            required_node_types=[
                node_type
                for metadata in metadata_items
                for node_type in metadata.node_policy.required_nodes
            ],
            forbidden_node_types=[
                node_type
                for metadata in metadata_items
                for node_type in metadata.node_policy.forbidden_nodes
            ],
            goal_by_skill=goal_by_skill,
            assets=assets,
            fault_codes=list(fault_codes),
            requested_tables=requested_tables,
            context_frame=context_frame,
            effective_request_frame=effective_request_frame,
        )
        edges = _normalized_edges(nodes)
        approval_requirements = _candidate_approvals(nodes, allowed_tools, expected_outputs)
        # Standalone skill prohibitions must not erase an explicitly selected sibling
        # capability; global policy still rejects dangerous tools in PlanValidator.
        forbidden_tools = [tool for tool in forbidden_tools if tool not in set(allowed_tools)]

        return ExecutionPlan(
            plan_id=f"candidate_{uuid4().hex[:12]}",
            plan_version="v2.candidate.phase4",
            goals=goals,
            nodes=nodes,
            edges=edges,
            required_evidence=list(dict.fromkeys(required_evidence)),
            allowed_tools=list(dict.fromkeys(allowed_tools)),
            forbidden_tools=list(dict.fromkeys(forbidden_tools)),
            risk_level=self.bridge.max_risk(risk_levels),
            interrupts=[],
            approval_requirements=approval_requirements,
            expected_outputs=list(dict.fromkeys(expected_outputs)),
            fallbacks=fallbacks,
            execution_capability=(effective_request_frame.effective_semantic_intent if effective_request_frame else ""),
            output_contract=_output_contract(metadata_items),
        )


def _candidate_approvals(
    nodes: list[dict[str, Any]],
    allowed_tools: list[str],
    expected_outputs: list[str],
) -> list[dict[str, Any]]:
    has_workorder = (
        bool({"workorder.create", "workorder.propose_draft"}.intersection(allowed_tools))
        or any(node.get("node_type") == "workorder" for node in nodes)
        or any(str(output).startswith("workorder") for output in expected_outputs)
    )
    if not has_workorder:
        return []
    return [
        {
            "requirement_id": "approval_workorder_draft",
            "type": "workorder_draft",
            "required": True,
            "required_role": "engineer",
            "allowed_next_step": "draft_only",
            "reason": "工单草稿必须由人工确认后继续。",
        }
    ]


def _normalized_nodes(
    *,
    skill_names: list[str],
    required_node_types: list[str],
    forbidden_node_types: list[str],
    goal_by_skill: dict[str, str],
    assets: list[str],
    fault_codes: list[str],
    requested_tables: list[str],
    context_frame: ContextFrame,
    effective_request_frame: EffectiveRequestFrame | None = None,
) -> list[dict[str, Any]]:
    if effective_request_frame is not None and effective_request_frame.requested_goal_set.goals:
        return _goal_scoped_nodes(
            effective_request_frame=effective_request_frame,
            context_frame=context_frame,
            fault_codes=fault_codes,
        )
    wanted = _wanted_node_types(
        skill_names,
        assets=assets,
        fault_codes=fault_codes,
        context_frame=context_frame,
        effective_request_frame=effective_request_frame,
    )
    wanted = list(dict.fromkeys([*wanted, *required_node_types]))
    forbidden = set(forbidden_node_types) - set(required_node_types)
    wanted = [node_type for node_type in wanted if node_type not in forbidden]
    nodes: list[dict[str, Any]] = []
    for node_type in wanted:
        owner = _node_owner(node_type, skill_names)
        goal_id = goal_by_skill.get(owner) or next(iter(goal_by_skill.values()), "")
        node_id = f"{node_type}_1"
        inputs = compile_node_inputs(
            node_type=node_type,
            assets=assets,
            fault_codes=fault_codes,
            requested_tables=requested_tables,
            context_frame=context_frame,
            effective_request_frame=effective_request_frame,
        )
        node: dict[str, Any] = {
            "node_id": node_id,
            "node_type": node_type,
            "skill": owner,
            "goal_id": goal_id,
            "status": "pending",
            "inputs": inputs,
            "required_tools": [],
            "retry": {},
            "condition": {},
        }
        required_tool = NODE_REQUIRED_TOOL.get(node_type)
        if required_tool:
            node["required_tools"] = [required_tool]
        if node_type == "sql" and requested_tables:
            node["requested_tables"] = list(requested_tables)
        nodes.append(node)
    return nodes


def _goal_scoped_nodes(
    *,
    effective_request_frame: EffectiveRequestFrame,
    context_frame: ContextFrame,
    fault_codes: list[str],
) -> list[dict[str, Any]]:
    authorized = set(effective_request_frame.authorized_goal_ids)
    goals = [
        goal
        for goal in effective_request_frame.requested_goal_set.goals
        if not authorized or goal.goal_id in authorized
    ]
    by_capability = {goal.capability: goal for goal in goals}
    query_by_goal = {spec.goal_id: spec for spec in effective_request_frame.goal_query_specs}
    devices = list(effective_request_frame.target_scope.resolved_devices)
    if effective_request_frame.needs_clarification:
        goal_ids = [goal.goal_id for goal in goals]
        inputs = compile_node_inputs(
            node_type="clarification",
            assets=devices,
            fault_codes=fault_codes,
            requested_tables=[],
            context_frame=context_frame,
            effective_request_frame=effective_request_frame,
        )
        inputs.update({"goal_ids": goal_ids, "target_scope_id": effective_request_frame.target_scope.scope_id})
        return [
            {
                "node_id": "clarification_1",
                "node_type": "clarification",
                "skill": "clarification",
                "goal_id": goal_ids[0] if goal_ids else "",
                "goal_ids": goal_ids,
                "target_scope_id": effective_request_frame.target_scope.scope_id,
                "failure_policy": "continue_independent_goals",
                "status": "pending",
                "inputs": inputs,
                "required_tools": [],
                "retry": {},
                "condition": {},
            }
        ]
    nodes: list[dict[str, Any]] = []

    def append_node(
        node_type: str,
        goal_ids: list[str],
        *,
        device: str = "",
        failure_policy: str = "block_dependents",
        suffix: str = "1",
    ) -> None:
        if any(str(item.get("node_id")) == f"{node_type}_{suffix}" for item in nodes):
            existing = next(item for item in nodes if str(item.get("node_id")) == f"{node_type}_{suffix}")
            existing["goal_ids"] = list(dict.fromkeys([*existing.get("goal_ids", []), *goal_ids]))
            return
        primary_goal_id = goal_ids[0] if goal_ids else ""
        spec = query_by_goal.get(primary_goal_id)
        node_devices = [device] if device else list(devices)
        inputs = compile_node_inputs(
            node_type=node_type,
            assets=node_devices,
            fault_codes=fault_codes,
            requested_tables=tables_for_assets(node_devices),
            context_frame=context_frame,
            effective_request_frame=effective_request_frame,
        )
        inputs.update(
            {
                "goal_ids": goal_ids,
                "query_spec_id": spec.query_spec_id if spec else "",
                "target_scope_id": effective_request_frame.target_scope.scope_id,
            }
        )
        if node_type not in {"workorder", "approval"}:
            inputs["requested_action"] = ""
        node = {
            "node_id": f"{node_type}_{suffix}",
            "node_type": node_type,
            "skill": _skill_for_capability(next((goal.capability for goal in goals if goal.goal_id == primary_goal_id), "")),
            "goal_id": primary_goal_id,
            "goal_ids": goal_ids,
            "query_spec_id": spec.query_spec_id if spec else "",
            "target_scope_id": effective_request_frame.target_scope.scope_id,
            "failure_policy": failure_policy,
            "status": "pending",
            "inputs": inputs,
            "required_tools": [NODE_REQUIRED_TOOL[node_type]] if node_type in NODE_REQUIRED_TOOL else [],
            "retry": {},
            "condition": {},
            "requested_tables": tables_for_assets(node_devices) if node_type == "sql" else [],
        }
        nodes.append(node)

    explain = by_capability.get("explain_fault_code")
    diagnose = by_capability.get("diagnose_fault")
    recommend = by_capability.get("resolution_recommendation")
    status = by_capability.get("check_runtime_status")
    compare = by_capability.get("compare_runtime_status")
    report = by_capability.get("generate_report")
    workorder = by_capability.get("create_workorder_draft")
    confirm_workorder = by_capability.get("confirm_workorder_draft")

    if explain or (fault_codes and (diagnose or recommend)):
        rag_goals = [goal.goal_id for goal in (explain, diagnose, recommend) if goal is not None]
        append_node("rag", rag_goals, failure_policy="continue_degraded")

    sql_goal_ids = [goal.goal_id for goal in (status, compare, diagnose) if goal is not None]
    target_is_runtime = effective_request_frame.target_artifact_type in {"sql_artifact", "status_query", "status_inspection"}
    requires_sql = bool(status or compare or (diagnose and not target_is_runtime))
    if requires_sql:
        for index, device in enumerate(devices, start=1):
            append_node("sql", sql_goal_ids, device=device, suffix=str(index), failure_policy="block_dependents")

    needs_analysis = bool(
        diagnose
        or recommend
        or (explain and status)
        or (report and effective_request_frame.target_artifact_type not in {"analysis_artifact", "report_artifact", "report_generation"})
        or (workorder and not effective_request_frame.target_artifact_id)
    )
    if needs_analysis and not target_is_runtime and not any(node.get("node_type") == "sql" for node in nodes) and devices:
        upstream_goal_ids = [goal.goal_id for goal in (status, diagnose, report, workorder) if goal is not None]
        for index, device in enumerate(devices, start=1):
            append_node("sql", upstream_goal_ids, device=device, suffix=str(index), failure_policy="block_dependents")
    if needs_analysis:
        analysis_goals = [goal.goal_id for goal in (diagnose, recommend) if goal is not None]
        if not analysis_goals:
            analysis_goals = [goal.goal_id for goal in (status, report, workorder) if goal is not None]
        append_node(
            "analysis",
            analysis_goals,
            failure_policy="block_all" if effective_request_frame.target_artifact_id else "block_dependents",
        )
    if compare:
        append_node("comparison", [compare.goal_id], failure_policy="block_dependents")
    if report:
        append_node("report", [report.goal_id], failure_policy="block_dependents")
    if workorder:
        append_node("workorder", [workorder.goal_id], failure_policy="block_all")
        append_node("approval", [workorder.goal_id], failure_policy="block_all")
    if confirm_workorder:
        append_node("workorder", [confirm_workorder.goal_id], failure_policy="block_all")
        append_node("approval", [confirm_workorder.goal_id], failure_policy="block_all")
    return nodes


def _skill_for_capability(capability: str) -> str:
    if capability == "explain_fault_code":
        return "fault_code_explain"
    if capability in {"check_runtime_status", "compare_runtime_status"}:
        return "runtime_status"
    if capability in {"diagnose_fault", "resolution_recommendation"}:
        return "alarm_triage"
    if capability == "generate_report":
        return "report_generation"
    if capability in {"create_workorder_draft", "confirm_workorder_draft"}:
        return "workorder_decision"
    return ""


def compile_node_inputs(
    *,
    node_type: str,
    assets: list[str],
    fault_codes: list[str],
    requested_tables: list[str],
    context_frame: ContextFrame,
    effective_request_frame: EffectiveRequestFrame | None = None,
) -> dict[str, Any]:
    common = {
        "device_refs": list(assets),
        "fault_code_refs": list(fault_codes),
        "context_relation": context_frame.relation_to_previous,
    }
    if effective_request_frame is None:
        if node_type == "workorder":
            return {**common, "create_draft": True, "draft_only": True, "manual_confirmation_required": True}
        if node_type == "approval":
            return {**common, "approval_requirements": []}
        if node_type == "sql":
            return {**common, "requested_tables": list(requested_tables)}
        return common

    target = {
        "requested_output_mode": effective_request_frame.requested_output_mode,
        "semantic_intent": effective_request_frame.effective_semantic_intent or effective_request_frame.semantic_intent,
        "target_artifact_id": effective_request_frame.target_artifact_id,
        "target_artifact_type": effective_request_frame.target_artifact_type,
        "target_evidence_bundle_id": effective_request_frame.target_evidence_bundle_id,
        "target_report_id": effective_request_frame.target_report_id,
        "stale_evidence_disclosure_required": effective_request_frame.stale_evidence_disclosure_required,
    }
    action = effective_request_frame.requested_action or effective_request_frame.semantic_intent
    if node_type == "sql":
        return {
            **common,
            **target,
            "requested_action": effective_request_frame.requested_action,
            "requested_tables": list(requested_tables),
        }
    if node_type == "rag":
        return {
            **common,
            **target,
            "requested_action": effective_request_frame.requested_action,
        }
    if node_type == "report":
        return {
            **common,
            **target,
            "requested_action": effective_request_frame.requested_action,
        }
    if node_type == "workorder":
        return {
            **common,
            **target,
            "create_draft": True,
            "action_type": action,
            "workorder_action": action,
            "source_artifact_refs": _source_artifact_refs(effective_request_frame),
            "manual_confirmation_required": True,
            "draft_only": True,
        }
    if node_type == "approval":
        return {
            **common,
            **target,
            "requested_action": effective_request_frame.requested_action,
            "approval_requirements": [],
        }
    if node_type == "clarification":
        ambiguity = dict(effective_request_frame.ambiguity or {})
        slot = str(ambiguity.get("slot") or "")
        return {
            **common,
            "clarification_question": effective_request_frame.clarification_question,
            "missing_slots": [slot] if slot else list(context_frame.missing_context),
            "candidate_targets": list(ambiguity.get("candidate_targets") or []),
            "reason": "authorized_but_incomplete",
        }
    return common


def _source_artifact_refs(frame: EffectiveRequestFrame) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if frame.target_artifact_id:
        refs.append(
            {
                "artifact_id": frame.target_artifact_id,
                "artifact_type": str(frame.target_artifact_type or ""),
            }
        )
    return refs


def _wanted_node_types(
    skill_names: list[str],
    *,
    assets: list[str],
    fault_codes: list[str],
    context_frame: ContextFrame,
    effective_request_frame: EffectiveRequestFrame | None = None,
) -> list[str]:
    selected = set(skill_names)
    wanted: list[str] = []
    if "clarification" in selected:
        return ["clarification"]
    if (
        "workorder_decision" in selected
        and effective_request_frame is not None
        and effective_request_frame.target_artifact_id
        and effective_request_frame.target_artifact_type in {"report_artifact", "analysis_artifact"}
    ):
        return ["workorder", "approval"]
    if "report_generation" in selected and (
        context_frame.relation_to_previous == "report_handoff"
        or (effective_request_frame is not None and (effective_request_frame.target_artifact_id or effective_request_frame.target_evidence_bundle_id))
    ):
        if "workorder_decision" not in selected:
            return ["report"]
    if selected.intersection({"runtime_status", "alarm_triage", "root_cause"}):
        wanted.append("sql")
    if selected.intersection({"fault_code_explain", "alarm_triage", "root_cause"}):
        wanted.append("rag")
    if "fault_code_explain" in selected and not selected.intersection({"alarm_triage", "root_cause", "workorder_decision", "report_generation"}):
        wanted.append("kg")
    needs_analysis = bool(selected.intersection({"alarm_triage", "root_cause", "workorder_decision"}))
    if "report_generation" in selected and (assets or fault_codes or selected.intersection({"runtime_status", "alarm_triage", "root_cause"})):
        needs_analysis = True
        wanted.append("sql")
        if fault_codes:
            wanted.append("rag")
    if "workorder_decision" in selected:
        needs_analysis = True
        if assets:
            wanted.append("sql")
        if fault_codes:
            wanted.append("rag")
    if needs_analysis:
        wanted.append("analysis")
    if "report_generation" in selected:
        wanted.append("report")
    if "workorder_decision" in selected:
        wanted.extend(["workorder", "approval"])
    if not wanted and context_frame.relation_to_previous == "report_handoff":
        wanted.append("report")
    return list(dict.fromkeys(wanted))


def _node_owner(node_type: str, skill_names: list[str]) -> str:
    owner_priority = {
        "sql": ("runtime_status", "alarm_triage", "root_cause", "report_generation", "workorder_decision"),
        "rag": ("fault_code_explain", "alarm_triage", "root_cause", "workorder_decision", "report_generation"),
        "kg": ("fault_code_explain",),
        "analysis": ("alarm_triage", "root_cause", "workorder_decision", "report_generation"),
        "report": ("report_generation",),
        "workorder": ("workorder_decision",),
        "approval": ("workorder_decision",),
        "clarification": ("clarification",),
    }
    selected = set(skill_names)
    for skill in owner_priority.get(node_type, ()):
        if skill in selected:
            return skill
    return skill_names[0] if skill_names else ""


def _normalized_edges(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_type: dict[str, list[str]] = {}
    for node in nodes:
        by_type.setdefault(str(node.get("node_type") or ""), []).append(str(node.get("node_id") or ""))
    edges: list[dict[str, Any]] = []
    for upstream in ("sql", "rag", "kg"):
        for source in by_type.get(upstream, []):
            for target in by_type.get("analysis", []):
                edges.append(
                    {"from": source, "to": target, "condition": {"optional_on_failure": upstream in {"rag", "kg"}}}
                )
    for source in by_type.get("sql", []):
        for target in by_type.get("comparison", []):
            edges.append({"from": source, "to": target})
    for source in by_type.get("analysis", []):
        for target in by_type.get("report", []):
            edges.append({"from": source, "to": target})
    if "report" in by_type and "analysis" not in by_type:
        for source in by_type.get("sql", []):
            for target in by_type.get("report", []):
                edges.append({"from": source, "to": target})
    for source in by_type.get("analysis", []):
        for target in by_type.get("workorder", []):
            edges.append({"from": source, "to": target, "condition": {"requires": "evidence_quality"}})
    if "report" in by_type and "workorder" in by_type:
        edges.append(
            {
                "from": by_type["report"][0],
                "to": by_type["workorder"][0],
                "condition": {"requires": "analysis_or_report_artifact"},
            }
        )
    if "workorder" in by_type and "approval" in by_type:
        edges.append({"from": by_type["workorder"][0], "to": by_type["approval"][0]})
    if not edges:
        return [
            {"from": nodes[index]["node_id"], "to": nodes[index + 1]["node_id"]}
            for index in range(len(nodes) - 1)
        ]
    return edges


def _coerce_plan(plan: ExecutionPlan | dict[str, Any]) -> ExecutionPlan:
    if isinstance(plan, ExecutionPlan):
        return plan.model_copy(deep=True)
    return ExecutionPlan.model_validate(plan)


def _output_contract(metadata_items: list[Any]) -> dict[str, Any]:
    required_fields: list[str] = []
    forbidden_claims: list[str] = []
    required_claim_types: list[str] = []
    for metadata in metadata_items:
        if metadata.name != "runtime_status":
            continue
        required_fields.extend(metadata.output_contract.required_fields)
        forbidden_claims.extend(metadata.output_contract.forbidden_claims)
        required_claim_types.append("runtime_status_assessment")
    return {
        "required_fields": list(dict.fromkeys(required_fields)),
        "forbidden_claims": list(dict.fromkeys(forbidden_claims)),
        "required_claim_types": list(dict.fromkeys(required_claim_types)),
    }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []
