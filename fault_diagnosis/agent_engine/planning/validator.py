"""Server-side validation for Agent Engine V2 candidate plans."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from fault_diagnosis.security.contracts import AuthContext, AuthorizationDecision
from fault_diagnosis.security.permissions import build_auth_context
from fault_diagnosis.security.policy_engine import authorize_workflow
from fault_diagnosis.security.tool_gateway import authorize_tool_call

from ..contracts import ExecutionPlan, IntentFrame, SkillRoute
from .policy_bridge import (
    BLOCKING_FORBIDDEN_TOOLS,
    GLOBAL_FORBIDDEN_TOOLS,
    NODE_REQUIRED_TOOL,
    PlanPolicyBridge,
    requested_assets,
    requested_tables,
    tables_for_assets,
    unauthorized_assets,
    unauthorized_tables,
)

ValidationStatus = Literal["validated", "degraded", "blocked"]


class PlanValidationIssue(BaseModel):
    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    message: str
    node_id: str | None = None
    tool: str | None = None


class PlanValidationResult(BaseModel):
    candidate_plan: ExecutionPlan
    validated_plan: ExecutionPlan
    status: ValidationStatus
    issues: list[PlanValidationIssue] = Field(default_factory=list)
    authorization: dict[str, Any] = Field(default_factory=dict)
    removed_tools: list[str] = Field(default_factory=list)
    approval_requirements: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


class PlanValidator:
    """Apply deterministic policy, scope, risk, evidence, and tool checks."""

    def __init__(self, bridge: PlanPolicyBridge | None = None) -> None:
        self.bridge = bridge or PlanPolicyBridge()

    def validate(
        self,
        *,
        candidate_plan: ExecutionPlan,
        skill_route: SkillRoute,
        intent_frame: IntentFrame,
        auth_context: AuthContext | None = None,
    ) -> PlanValidationResult:
        auth = auth_context or build_auth_context(role="guest")
        validated = candidate_plan.model_copy(deep=True)
        issues: list[PlanValidationIssue] = []
        removed_tools: list[str] = []

        primary_skill = self.bridge.primary_skill(skill_route, candidate_plan)
        skill_names = list(skill_route.selected_skills or [primary_skill])
        policy = self.bridge.policy_for_skill(primary_skill)

        validated.required_evidence = _dedupe(
            [
                *validated.required_evidence,
                *self.bridge.required_evidence_for_skills(skill_names),
                *[key for key, required in policy.evidence_requirements.items() if required],
            ]
        )
        validated.forbidden_tools = _dedupe([*validated.forbidden_tools, *self.bridge.forbidden_tools_for_skills(skill_names)])
        validated.risk_level = self.bridge.max_risk([validated.risk_level, policy.risk_level])

        allowed_by_skill = set(self.bridge.allowed_tools_for_skills(skill_names))
        if not allowed_by_skill and primary_skill == "clarification":
            allowed_by_skill = set()
        allowed_tools: list[str] = []
        for tool in validated.allowed_tools:
            if tool in GLOBAL_FORBIDDEN_TOOLS or tool in set(validated.forbidden_tools):
                removed_tools.append(tool)
                issues.append(
                    PlanValidationIssue(
                        code="forbidden_tool_requested",
                        severity="error" if tool in BLOCKING_FORBIDDEN_TOOLS else "warning",
                        message=f"Candidate plan requested forbidden tool: {tool}",
                        tool=tool,
                    )
                )
                continue
            if allowed_by_skill and tool not in allowed_by_skill:
                removed_tools.append(tool)
                issues.append(
                    PlanValidationIssue(
                        code="tool_not_allowed_by_skill",
                        message=f"Tool is not allowed by selected skill policy: {tool}",
                        tool=tool,
                    )
                )
                continue
            allowed_tools.append(tool)
        validated.allowed_tools = _dedupe(allowed_tools)

        assets = requested_assets(plan=validated, intent_frame=intent_frame, skill_route=skill_route)
        tables = _dedupe([*requested_tables(validated), *tables_for_assets(assets)])
        for asset in unauthorized_assets(assets, auth):
            issues.append(
                PlanValidationIssue(
                    code="asset_out_of_scope",
                    severity="error",
                    message=f"Requested asset is outside current account scope: {asset}",
                )
            )
        for table in unauthorized_tables(tables, auth):
            issues.append(
                PlanValidationIssue(
                    code="table_out_of_scope",
                    severity="error",
                    message=f"Requested table is outside current account scope: {table}",
                )
            )

        decision = self.bridge.authorization_decision_object(
            plan=validated,
            skill_route=skill_route,
            intent_frame=intent_frame,
        )
        authorization = authorize_workflow(auth, decision)
        if not authorization.allowed:
            issues.append(
                PlanValidationIssue(
                    code=authorization.denied_reason_code or "workflow_authorization_denied",
                    severity="error",
                    message=authorization.reason or authorization.user_message or "Workflow authorization denied.",
                )
            )
            denied_tools = _tools_denied_by_auth(validated.allowed_tools, authorization)
            removed_tools.extend(denied_tools)
            validated.allowed_tools = [tool for tool in validated.allowed_tools if tool not in set(denied_tools)]
        else:
            validated.allowed_tools = self._filter_tool_permissions(
                auth=auth,
                tools=validated.allowed_tools,
                decision=decision,
                removed_tools=removed_tools,
                issues=issues,
            )

        dangerous_requested_tools = [
            issue.tool
            for issue in issues
            if issue.code == "forbidden_tool_requested"
            and issue.tool in {"device_control.write", "config.write", "workorder.dispatch"}
        ]
        approvals = _dedupe_approvals(
            [*validated.approval_requirements, *_approval_requirements(validated, dangerous_requested_tools)]
        )
        validated.approval_requirements = approvals
        validated.interrupts = _dedupe_interrupts([*validated.interrupts, *_approval_interrupts(approvals)])

        if validated.risk_level in {"high", "critical"} and not validated.required_evidence:
            validated.required_evidence = ["risk_precondition_evidence"]
            issues.append(
                PlanValidationIssue(
                    code="high_risk_missing_evidence_requirement",
                    message="High-risk plan must declare evidence requirements.",
                )
            )

        validated.nodes = _sanitize_nodes(validated.nodes, removed_tools, issues)
        removed_tools = _dedupe(removed_tools)
        validated.plan_id = validated.plan_id or f"validated_{uuid4().hex[:12]}"
        if not validated.plan_version.endswith(".validated"):
            validated.plan_version = f"{validated.plan_version}.validated"

        status = _status(issues, removed_tools)
        if status == "blocked" and not validated.plan_version.endswith(".blocked"):
            validated.plan_version = f"{validated.plan_version}.blocked"
        return PlanValidationResult(
            candidate_plan=candidate_plan,
            validated_plan=validated,
            status=status,
            issues=issues,
            authorization=authorization.model_dump(),
            removed_tools=removed_tools,
            approval_requirements=approvals,
        )

    def _filter_tool_permissions(
        self,
        *,
        auth: AuthContext,
        tools: list[str],
        decision: Any,
        removed_tools: list[str],
        issues: list[PlanValidationIssue],
    ) -> list[str]:
        allowed: list[str] = []
        for tool in tools:
            legacy_tools = self.bridge.v2_to_legacy_tools([tool])
            denied: AuthorizationDecision | None = None
            for legacy_tool in legacy_tools:
                result = authorize_tool_call(auth, legacy_tool, decision=decision)
                if not result.allowed:
                    denied = result
                    break
            if denied is not None:
                removed_tools.append(tool)
                issues.append(
                    PlanValidationIssue(
                        code=denied.denied_reason_code or "tool_authorization_denied",
                        severity="error" if tool in BLOCKING_FORBIDDEN_TOOLS else "warning",
                        message=denied.reason,
                        tool=tool,
                    )
                )
                continue
            allowed.append(tool)
        return _dedupe(allowed)


def _tools_denied_by_auth(tools: list[str], authorization: AuthorizationDecision) -> list[str]:
    denied_nodes = set(authorization.denied_nodes)
    denied: list[str] = []
    if "report" in denied_nodes:
        denied.append("report.write_draft")
    if "workorder_decision" in denied_nodes or "action_request" in denied_nodes:
        denied.append("workorder.create")
    if authorization.denied_reason_code in {"report_permission_denied", "diagnosis_permission_denied", "missing_workflow_permission"}:
        denied.extend(["report.write_draft", "workorder.create"])
    return [tool for tool in _dedupe(denied) if tool in tools]


def _approval_requirements(plan: ExecutionPlan, dangerous_requested_tools: list[str]) -> list[dict[str, Any]]:
    approvals: list[dict[str, Any]] = []
    tools = set(plan.allowed_tools)
    node_types = {str(node.get("node_type") or "") for node in plan.nodes}
    outputs = set(plan.expected_outputs)

    if "workorder.create" in tools or "workorder" in node_types or outputs.intersection({"workorder_decision", "workorder_draft"}):
        approvals.append(
            {
                "requirement_id": "approval_workorder_draft",
                "type": "workorder_draft",
                "required": True,
                "required_role": "engineer",
                "allowed_next_step": "draft_only",
                "reason": "工单草稿必须由人工确认后继续。",
            }
        )
    if (
        node_types.intersection({"device_action", "config_write"})
        or set(dangerous_requested_tools).intersection({"device_control.write", "config.write", "workorder.dispatch"})
        or tools.intersection({"device_control.write", "config.write", "workorder.dispatch"})
    ):
        approvals.append(
            {
                "requirement_id": "approval_device_action_denied",
                "type": "device_action",
                "required": True,
                "required_role": "admin",
                "allowed_next_step": "deny",
                "reason": "设备动作、配置写入和派单执行不允许由 V2 plan 自动执行。",
            }
        )
    return approvals


def _approval_interrupts(approvals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "interrupt_id": f"interrupt_{item.get('requirement_id')}",
            "type": "approval_required",
            "approval_requirement_id": item.get("requirement_id"),
            "allowed_next_step": item.get("allowed_next_step"),
        }
        for item in approvals
    ]


def _sanitize_nodes(
    nodes: list[dict[str, Any]],
    removed_tools: list[str],
    issues: list[PlanValidationIssue],
) -> list[dict[str, Any]]:
    removed = set(removed_tools)
    sanitized: list[dict[str, Any]] = []
    for node in nodes:
        copied = dict(node)
        required_tools = [tool for tool in _as_list(copied.get("required_tools")) if tool not in removed]
        if "required_tools" in copied:
            copied["required_tools"] = required_tools
        node_type = str(copied.get("node_type") or "")
        natural_tool = NODE_REQUIRED_TOOL.get(node_type)
        if natural_tool in removed:
            issues.append(
                PlanValidationIssue(
                    code="node_removed_with_denied_tool",
                    message=f"Node removed because its required tool was denied: {node_type}",
                    node_id=str(copied.get("node_id") or ""),
                    tool=natural_tool,
                )
            )
            continue
        sanitized.append(copied)
    return sanitized


def _status(issues: list[PlanValidationIssue], removed_tools: list[str]) -> ValidationStatus:
    if any(issue.severity == "error" for issue in issues):
        return "blocked"
    if removed_tools or issues:
        return "degraded"
    return "validated"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _dedupe_approvals(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for value in values:
        key = str(value.get("requirement_id") or value.get("type") or len(keyed))
        keyed[key] = value
    return list(keyed.values())


def _dedupe_interrupts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for value in values:
        key = str(value.get("interrupt_id") or value.get("type") or len(keyed))
        keyed[key] = value
    return list(keyed.values())


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple | set):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []
