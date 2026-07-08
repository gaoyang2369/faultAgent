"""Candidate ExecutionPlan compiler for Agent Engine V2."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..contracts import ContextFrame, ExecutionPlan, IntentFrame, SkillRoute
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
        llm_candidate_plan: ExecutionPlan | dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        if llm_candidate_plan is not None:
            return _coerce_plan(llm_candidate_plan)

        skill_names = list(skill_route.selected_skills or ([skill_route.primary_skill] if skill_route.primary_skill else []))
        metadata_items = self.bridge.skill_metadata(skill_names)
        assets = list(dict.fromkeys(intent_frame.device_refs + _as_list(skill_route.skill_inputs.get("device_refs"))))
        requested_tables = tables_for_assets(assets)

        goals: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = []
        fallbacks: list[dict[str, Any]] = []
        required_evidence: list[str] = []
        allowed_tools: list[str] = []
        forbidden_tools: list[str] = []
        expected_outputs: list[str] = []
        risk_levels: list[str] = []

        for skill_index, metadata in enumerate(metadata_items, start=1):
            goal_id = f"goal_{skill_index}_{metadata.name}"
            goals.append(
                {
                    "goal_id": goal_id,
                    "skill": metadata.name,
                    "goal_type": self.bridge.goal_type_for_skill(metadata.name),
                    "description": metadata.description,
                    "device_refs": list(assets),
                    "fault_code_refs": list(intent_frame.fault_code_refs),
                    "expected_outputs": list(metadata.output_variants),
                    "risk_level": metadata.risk_level,
                    "source": "llm_candidate" if intent_frame.model_trace.get("llm_used") else "skill_compiler",
                }
            )
            for node_name in metadata.allowed_nodes:
                node_id = f"{node_name}_{len(nodes) + 1}"
                required_tool = NODE_REQUIRED_TOOL.get(node_name)
                node: dict[str, Any] = {
                    "node_id": node_id,
                    "node_type": node_name,
                    "skill": metadata.name,
                    "goal_id": goal_id,
                    "status": "pending",
                    "inputs": {
                        "device_refs": list(assets),
                        "fault_code_refs": list(intent_frame.fault_code_refs),
                        "context_relation": context_frame.relation_to_previous,
                    },
                }
                if required_tool:
                    node["required_tools"] = [required_tool]
                if node_name == "sql" and requested_tables:
                    node["requested_tables"] = list(requested_tables)
                    node["inputs"]["requested_tables"] = list(requested_tables)
                nodes.append(node)
            if metadata.fallback_policy:
                fallbacks.append({"skill": metadata.name, "policy": metadata.fallback_policy})
            required_evidence.extend(metadata.required_evidence)
            allowed_tools.extend(metadata.allowed_tools)
            forbidden_tools.extend(metadata.forbidden_tools)
            expected_outputs.extend(metadata.output_variants)
            risk_levels.append(metadata.risk_level)

        edges = [
            {"from": nodes[index]["node_id"], "to": nodes[index + 1]["node_id"]}
            for index in range(len(nodes) - 1)
        ]
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
        "workorder.create" in allowed_tools
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
