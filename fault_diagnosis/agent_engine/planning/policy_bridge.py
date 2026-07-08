"""Policy and tool-name bridge for Agent Engine V2 planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from fault_diagnosis.security.assets import asset_is_in_scope, resolve_asset
from fault_diagnosis.security.contracts import AuthContext
from fault_diagnosis.security.permissions import AUTHORIZED_BUSINESS_TABLES

from ..contracts import ContextFrame, ExecutionPlan, IntentFrame, SkillRoute
from ..skills import SkillMetadata, SkillRegistry


GLOBAL_FORBIDDEN_TOOLS = {
    "sql.write",
    "config.write",
    "device_control.write",
    "workorder.dispatch",
    "alarm.acknowledge",
    "alarm.close",
}

BLOCKING_FORBIDDEN_TOOLS = {
    "sql.write",
    "config.write",
    "device_control.write",
    "workorder.dispatch",
}

V2_TO_LEGACY_TOOLS = {
    "sql.read": ["sql_db_query_checker", "sql_db_query"],
    "kb.search": ["query_knowledge_base"],
    "kg.lookup": ["query_knowledge_base"],
    "report.write_draft": ["save_report"],
    "workorder.create": ["create_workorder"],
}

LEGACY_TO_V2_TOOLS = {
    legacy: v2 for v2, legacy_names in V2_TO_LEGACY_TOOLS.items() for legacy in legacy_names
}

SKILL_TO_POLICY_ID = {
    "clarification": "permission_scope_query_v1",
    "fault_code_explain": "knowledge_qa_v1",
    "runtime_status": "status_query_v1",
    "alarm_triage": "alarm_triage_v1",
    "root_cause": "root_cause_analysis_v1",
    "report_generation": "report_generation_v1",
    "workorder_decision": "action_request_v1",
}

SKILL_TO_TASK_FAMILY = {
    "clarification": "meta",
    "fault_code_explain": "knowledge_lookup",
    "runtime_status": "runtime_status",
    "alarm_triage": "diagnosis",
    "root_cause": "diagnosis",
    "report_generation": "reporting",
    "workorder_decision": "action_or_workorder",
}

SKILL_TO_GOAL_TYPE = {
    "clarification": "clarify_missing_context",
    "fault_code_explain": "explain_fault_code",
    "runtime_status": "check_runtime_status",
    "alarm_triage": "diagnose_fault",
    "root_cause": "diagnose_fault",
    "report_generation": "generate_report",
    "workorder_decision": "decide_workorder",
}

NODE_TO_LEGACY_NODE = {
    "rag": "knowledge",
    "kg": "knowledge",
    "sql": "sql",
    "analysis": "analysis",
    "report": "report",
    "workorder": "workorder_decision",
    "approval": "permission_check",
    "clarification": "analysis",
}

NODE_REQUIRED_TOOL = {
    "sql": "sql.read",
    "rag": "kb.search",
    "kg": "kg.lookup",
    "report": "report.write_draft",
    "workorder": "workorder.create",
}

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
RISK_FROM_LEGACY = {
    "read_only": "low",
    "requires_confirmation": "high",
    "write_action": "critical",
    "high_risk": "critical",
}


@dataclass(frozen=True)
class V2Policy:
    policy_id: str
    task_family: str
    risk_level: str = "low"
    evidence_requirements: dict[str, bool] = field(default_factory=dict)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        return {
            "policy_id": self.policy_id,
            "task_family": self.task_family,
            "risk_level": self.risk_level,
            "evidence_requirements": dict(self.evidence_requirements),
        }


POLICIES_BY_ID: dict[str, V2Policy] = {
    "permission_scope_query_v1": V2Policy("permission_scope_query_v1", "meta"),
    "knowledge_qa_v1": V2Policy(
        "knowledge_qa_v1",
        "knowledge_lookup",
        evidence_requirements={"need_manual_reference": True},
    ),
    "status_query_v1": V2Policy(
        "status_query_v1",
        "runtime_status",
        evidence_requirements={
            "need_asset_identity": True,
            "need_current_status": True,
            "need_metric_timestamp": True,
        },
    ),
    "alarm_triage_v1": V2Policy(
        "alarm_triage_v1",
        "diagnosis",
        evidence_requirements={
            "need_alarm_definition": True,
            "need_alarm_severity": True,
            "need_current_alarm_status_if_device_provided": True,
        },
    ),
    "fault_diagnosis_v1": V2Policy(
        "fault_diagnosis_v1",
        "diagnosis",
        evidence_requirements={
            "need_runtime_data": True,
            "need_supporting_evidence_for_each_cause": True,
            "need_missing_evidence_disclosure": True,
        },
    ),
    "root_cause_analysis_v1": V2Policy(
        "root_cause_analysis_v1",
        "diagnosis",
        evidence_requirements={
            "need_event_timeline": True,
            "need_causal_support": True,
            "need_unknowns": True,
        },
    ),
    "report_generation_v1": V2Policy(
        "report_generation_v1",
        "reporting",
        evidence_requirements={"need_reportable_artifact": True},
    ),
    "action_request_v1": V2Policy(
        "action_request_v1",
        "action_or_workorder",
        risk_level="high",
        evidence_requirements={
            "need_current_status": True,
            "need_human_confirmation": True,
        },
    ),
}


class PlanPolicyBridge:
    """Translate between V2 plan vocabulary and existing policy contracts."""

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self.registry = registry or SkillRegistry()

    def skill_metadata(self, skill_names: list[str]) -> list[SkillMetadata]:
        discovered = self.registry.discover()
        return [discovered[name] for name in skill_names if name in discovered]

    def primary_skill(self, skill_route: SkillRoute, plan: ExecutionPlan | None = None) -> str:
        if skill_route.primary_skill:
            return skill_route.primary_skill
        if plan is not None:
            for goal in plan.goals:
                skill = str(goal.get("skill") or "").strip()
                if skill:
                    return skill
        return "clarification"

    def policy_id_for_skill(self, skill_name: str) -> str:
        return SKILL_TO_POLICY_ID.get(skill_name, "fault_diagnosis_v1")

    def policy_for_skill(self, skill_name: str) -> Any:
        return POLICIES_BY_ID.get(self.policy_id_for_skill(skill_name), POLICIES_BY_ID["fault_diagnosis_v1"])

    def task_family_for_skill(self, skill_name: str) -> str:
        return SKILL_TO_TASK_FAMILY.get(skill_name, "diagnosis")

    def goal_type_for_skill(self, skill_name: str) -> str:
        return SKILL_TO_GOAL_TYPE.get(skill_name, "diagnose_fault")

    def v2_to_legacy_tools(self, tools: list[str]) -> list[str]:
        legacy: list[str] = []
        for tool in tools:
            legacy.extend(V2_TO_LEGACY_TOOLS.get(tool, [tool]))
        return list(dict.fromkeys(legacy))

    def legacy_to_v2_tools(self, tools: list[str]) -> list[str]:
        return list(dict.fromkeys(LEGACY_TO_V2_TOOLS.get(tool, tool) for tool in tools))

    def legacy_enabled_nodes(self, plan: ExecutionPlan) -> dict[str, bool]:
        enabled: dict[str, bool] = {}
        for node in plan.nodes:
            node_type = str(node.get("node_type") or node.get("type") or "").strip()
            legacy_node = NODE_TO_LEGACY_NODE.get(node_type, node_type)
            if legacy_node:
                enabled[legacy_node] = True
        return enabled

    def authorization_decision_object(
        self,
        *,
        plan: ExecutionPlan,
        skill_route: SkillRoute,
        intent_frame: IntentFrame,
    ) -> Any:
        primary_skill = self.primary_skill(skill_route, plan)
        policy = self.policy_for_skill(primary_skill)
        goal_type = self.goal_type_for_skill(primary_skill)
        device_ids = requested_assets(plan=plan, intent_frame=intent_frame, skill_route=skill_route)
        alarm_codes = list(dict.fromkeys(intent_frame.fault_code_refs + _input_list(skill_route, "fault_code_refs")))
        return SimpleNamespace(
            task_family=self.task_family_for_skill(primary_skill),
            requested_output="report" if "report" in plan.expected_outputs else "answer",
            workflow_policy=policy.model_dump(),
            enabled_nodes=self.legacy_enabled_nodes(plan),
            runtime_tools=self.v2_to_legacy_tools(plan.allowed_tools),
            goal_set={"goals": [{"goal_type": goal_type}]},
            goals=[],
            objects={"device_ids": device_ids, "alarm_codes": alarm_codes},
            action_target="workorder" if primary_skill == "workorder_decision" else None,
            action_type="create_workorder_draft" if primary_skill == "workorder_decision" else None,
        )

    def allowed_tools_for_skills(self, skill_names: list[str]) -> list[str]:
        tools: list[str] = []
        for metadata in self.skill_metadata(skill_names):
            tools.extend(metadata.allowed_tools)
        return list(dict.fromkeys(tools))

    def forbidden_tools_for_skills(self, skill_names: list[str]) -> list[str]:
        tools: list[str] = []
        for metadata in self.skill_metadata(skill_names):
            tools.extend(metadata.forbidden_tools)
        tools.extend(sorted(GLOBAL_FORBIDDEN_TOOLS))
        return list(dict.fromkeys(tools))

    def required_evidence_for_skills(self, skill_names: list[str]) -> list[str]:
        required: list[str] = []
        for metadata in self.skill_metadata(skill_names):
            required.extend(metadata.required_evidence)
        return list(dict.fromkeys(required))

    def max_risk(self, values: list[str]) -> str:
        normalized = [RISK_FROM_LEGACY.get(value, value) for value in values if value]
        return max(normalized or ["low"], key=lambda item: RISK_ORDER.get(item, 0))


def requested_assets(
    *,
    plan: ExecutionPlan,
    intent_frame: IntentFrame,
    skill_route: SkillRoute,
) -> list[str]:
    assets: list[str] = []
    assets.extend(intent_frame.device_refs)
    assets.extend(_input_list(skill_route, "device_refs"))
    for goal in plan.goals:
        assets.extend(_as_list(goal.get("device_refs")))
    for node in plan.nodes:
        assets.extend(_as_list(node.get("device_refs")))
        inputs = node.get("inputs")
        if isinstance(inputs, dict):
            assets.extend(_as_list(inputs.get("device_refs")))
    return list(dict.fromkeys(str(value).strip() for value in assets if str(value).strip()))


def requested_tables(plan: ExecutionPlan) -> list[str]:
    tables: list[str] = []
    for node in plan.nodes:
        tables.extend(_as_list(node.get("requested_tables")))
        inputs = node.get("inputs")
        if isinstance(inputs, dict):
            tables.extend(_as_list(inputs.get("requested_tables")))
    return list(dict.fromkeys(str(value).strip() for value in tables if str(value).strip()))


def tables_for_assets(assets: list[str]) -> list[str]:
    tables: list[str] = []
    for asset in assets:
        record = resolve_asset(asset)
        if record is None:
            continue
        tables.extend(source.table for source in record.data_sources if source.table)
    return list(dict.fromkeys(tables))


def unauthorized_assets(assets: list[str], auth: AuthContext) -> list[str]:
    if auth.role not in {"guest", "engineer"}:
        return []
    return [asset for asset in assets if not asset_is_in_scope(asset, auth.asset_scope)]


def unauthorized_tables(tables: list[str], auth: AuthContext) -> list[str]:
    if auth.role == "admin":
        return []
    allowed = set(auth.table_scope)
    if auth.role == "guest":
        allowed = {"real_data_01"}
    if auth.role == "engineer":
        allowed = allowed.intersection(AUTHORIZED_BUSINESS_TABLES)
    return sorted({table for table in tables if table not in allowed})


def _input_list(skill_route: SkillRoute, key: str) -> list[str]:
    return _as_list(skill_route.skill_inputs.get(key))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple | set):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []
