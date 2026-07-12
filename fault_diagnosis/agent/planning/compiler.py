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
        "semantic_intent": effective_request_frame.semantic_intent,
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
            "stale_refresh_required": effective_request_frame.stale_evidence_disclosure_required,
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
        and effective_request_frame.target_artifact_type in {"report_artifact", "analysis_artifact", "structured_analysis_artifact"}
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
    by_type = {str(node.get("node_type") or ""): str(node.get("node_id") or "") for node in nodes}
    edges: list[dict[str, Any]] = []
    for upstream in ("sql", "rag", "kg"):
        if upstream in by_type and "analysis" in by_type:
            edges.append({"from": by_type[upstream], "to": by_type["analysis"]})
    if "analysis" in by_type and "report" in by_type:
        edges.append({"from": by_type["analysis"], "to": by_type["report"]})
    if "analysis" in by_type and "workorder" in by_type:
        edges.append({"from": by_type["analysis"], "to": by_type["workorder"], "condition": {"requires": "evidence_quality"}})
    if "report" in by_type and "workorder" in by_type:
        edges.append(
            {
                "from": by_type["report"],
                "to": by_type["workorder"],
                "condition": {"requires": "analysis_or_report_artifact"},
            }
        )
    if "workorder" in by_type and "approval" in by_type:
        edges.append({"from": by_type["workorder"], "to": by_type["approval"]})
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


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []
