"""Server-side validation for Agent Engine V2 candidate plans."""

from __future__ import annotations

import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from fault_diagnosis.domain.security.contracts import AuthContext, AuthorizationDecision
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.domain.security.policy_engine import authorize_workflow
from fault_diagnosis.domain.security.tool_gateway import authorize_tool_call

from ..contracts import (
    ApprovalNodeInputs,
    ExecutionPlan,
    IntentFrame,
    PlanGoal,
    PlanNode,
    RagNodeInputs,
    ReportNodeInputs,
    SkillRoute,
    SqlNodeInputs,
    WorkorderNodeInputs,
)
from ..skills import SkillMetadata
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
        require_runtime_inputs: bool = False,
    ) -> PlanValidationResult:
        auth = auth_context or build_auth_context(role="guest")
        validated = candidate_plan.model_copy(deep=True)
        issues: list[PlanValidationIssue] = []
        removed_tools: list[str] = []

        primary_skill = self.bridge.primary_skill(skill_route, candidate_plan)
        skill_names = list(skill_route.selected_skills or [primary_skill])
        if _is_guest_report_runtime_status_fallback(candidate_plan):
            primary_skill = "runtime_status"
            skill_names = ["runtime_status"]
        policy = self.bridge.policy_for_skill(primary_skill)
        metadata_items = self.bridge.skill_metadata(skill_names)

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
            if _can_degrade_guest_report(
                auth=auth,
                authorization=authorization,
                issues=issues,
                primary_skill=primary_skill,
                assets=assets,
            ):
                denied_tools = _tools_denied_by_auth(validated.allowed_tools, authorization)
                removed_tools.extend(denied_tools)
                validated = _degrade_guest_report_plan(validated, intent_frame=intent_frame)
                runtime_metadata = self.bridge.skill_metadata(["runtime_status"])
                if runtime_metadata:
                    validated.output_contract = {
                        "required_fields": list(runtime_metadata[0].output_contract.required_fields),
                        "forbidden_claims": list(runtime_metadata[0].output_contract.forbidden_claims),
                        "required_claim_types": ["runtime_status_assessment"],
                    }
                authorization = _guest_report_degraded_authorization(authorization)
                issues.append(
                    PlanValidationIssue(
                        code="report_permission_denied_degraded_to_status",
                        severity="warning",
                        message=(
                            "当前身份无法生成正式报告，已降级为授权设备最近一小时运行状态摘要。"
                        ),
                    )
                )
            else:
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
        _validate_node_inputs(validated, issues, require_runtime_inputs=require_runtime_inputs)
        contract_metadata = (
            self.bridge.skill_metadata(["runtime_status"])
            if _is_guest_report_runtime_status_fallback(validated)
            else metadata_items
        )
        _validate_skill_contracts(validated, contract_metadata, issues)
        removed_tools = _dedupe(removed_tools)
        validated.plan_id = validated.plan_id or f"validated_{uuid4().hex[:12]}"
        if ".validated" not in validated.plan_version:
            validated.plan_version = f"{validated.plan_version}.validated"

        status = _status(issues, removed_tools)
        if status == "validated" and _is_guest_report_runtime_status_fallback(validated):
            status = "degraded"
        if status == "degraded" and ".degraded" not in validated.plan_version:
            validated.plan_version = f"{validated.plan_version}.degraded"
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
        denied.extend(["workorder.propose_draft", "workorder.create"])
    if authorization.denied_reason_code in {"report_permission_denied", "diagnosis_permission_denied", "missing_workflow_permission"}:
        denied.extend(["report.write_draft", "workorder.propose_draft", "workorder.create"])
    return [tool for tool in _dedupe(denied) if tool in tools]


def _can_degrade_guest_report(
    *,
    auth: AuthContext,
    authorization: AuthorizationDecision,
    issues: list[PlanValidationIssue],
    primary_skill: str,
    assets: list[str],
) -> bool:
    if auth.role != "guest":
        return False
    if primary_skill != "report_generation":
        return False
    if authorization.denied_reason_code != "report_permission_denied":
        return False
    if not assets:
        return False
    blocking_codes = {"asset_out_of_scope", "table_out_of_scope"}
    return not any(issue.severity == "error" and issue.code in blocking_codes for issue in issues)


def _is_guest_report_runtime_status_fallback(plan: ExecutionPlan) -> bool:
    for item in plan.fallbacks:
        if not isinstance(item, dict):
            continue
        if item.get("from") == "report_generation" and item.get("to") == "runtime_status":
            return True
    return False


def _guest_report_degraded_authorization(authorization: AuthorizationDecision) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        mode="degrade",
        reason=authorization.reason or "报告生成权限不足，降级为状态查询。",
        denied_reason_code=authorization.denied_reason_code,
        allowed_nodes={"sql": True},
        denied_nodes={**authorization.denied_nodes, "report": "missing_report_permission"},
        runtime_tools=["sql_db_query"],
        data_scope=dict(authorization.data_scope),
        kb_scope=dict(authorization.kb_scope),
        user_message=(
            "游客不能生成正式报告；以下为 G120电机1 最近一小时运行状态摘要。"
        ),
    )


def _degrade_guest_report_plan(plan: ExecutionPlan, *, intent_frame: IntentFrame) -> ExecutionPlan:
    degraded = plan.model_copy(deep=True)
    device_refs = list(intent_frame.device_refs or [])
    base_inputs = {
        "device_refs": device_refs,
        "fault_code_refs": list(intent_frame.fault_code_refs or []),
        "context_relation": "new_case",
        "requested_output_mode": "concise",
        "semantic_intent": "check_runtime_status",
        "requested_action": "",
        "requested_tables": ["real_data_01"],
        "degraded_notice": "游客不能生成正式报告；以下为 G120电机1 最近一小时运行状态摘要。",
    }
    sql_nodes: list[PlanNode] = []
    for node in degraded.nodes:
        if str(node.get("node_type") or "") != "sql":
            continue
        copied = node.model_copy(deep=True) if isinstance(node, PlanNode) else PlanNode.model_validate(node)
        copied.skill = "runtime_status"
        copied.goal_id = "goal_1_runtime_status"
        copied.required_tools = ["sql.read"]
        copied.inputs = {
            **base_inputs,
            **dict(copied.inputs or {}),
            "semantic_intent": "check_runtime_status",
            "requested_action": "",
            "requested_output_mode": "concise",
            "requested_tables": ["real_data_01"],
            "degraded_notice": base_inputs["degraded_notice"],
        }
        sql_nodes.append(copied)
    if not sql_nodes:
        sql_nodes.append(
            PlanNode(
                node_id="sql_1",
                node_type="sql",
                skill="runtime_status",
                goal_id="goal_1_runtime_status",
                inputs=base_inputs,
                required_tools=["sql.read"],
                requested_tables=["real_data_01"],
            )
        )
    degraded.goals = [
        PlanGoal(
            goal_id="goal_1_runtime_status",
            goal="check_runtime_status",
            goal_type="check_runtime_status",
            skill="runtime_status",
            description="Degraded guest report request to one-hour runtime status summary.",
            device_refs=device_refs,
            fault_code_refs=list(intent_frame.fault_code_refs or []),
            expected_outputs=["status_brief", "answer"],
        )
    ]
    degraded.nodes = sql_nodes
    degraded.edges = []
    degraded.allowed_tools = ["sql.read"]
    degraded.expected_outputs = ["status_brief", "answer"]
    degraded.required_evidence = ["recent_runtime_sample"]
    degraded.fallbacks = [
        *degraded.fallbacks,
        {
            "from": "report_generation",
            "to": "runtime_status",
            "reason": "report_permission_denied",
            "scope": "guest_last_1_hour",
        },
    ]
    return degraded


def _approval_requirements(plan: ExecutionPlan, dangerous_requested_tools: list[str]) -> list[dict[str, Any]]:
    approvals: list[dict[str, Any]] = []
    tools = set(plan.allowed_tools)
    node_types = {str(node.get("node_type") or "") for node in plan.nodes}
    outputs = set(plan.expected_outputs)

    if tools.intersection({"workorder.create", "workorder.propose_draft"}) or "workorder" in node_types or outputs.intersection({"workorder_decision", "workorder_draft"}):
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


def _validate_node_inputs(
    plan: ExecutionPlan,
    issues: list[PlanValidationIssue],
    *,
    require_runtime_inputs: bool,
) -> None:
    for node in plan.nodes:
        node_id = str(node.get("node_id") or "")
        node_type = str(node.get("node_type") or "")
        if not node_id:
            issues.append(
                PlanValidationIssue(
                    code="plan_node_missing_node_id",
                    severity="error",
                    message="Plan node must include node_id.",
                    node_id=node_id,
                )
            )
        if not node_type:
            issues.append(
                PlanValidationIssue(
                    code="plan_node_missing_node_type",
                    severity="error",
                    message="Plan node must include node_type.",
                    node_id=node_id,
                )
            )
            continue
        inputs = dict(node.get("inputs") or {})
        try:
            _coerce_node_inputs(node_type, inputs)
        except ValidationError as exc:
            issues.append(
                PlanValidationIssue(
                    code=f"{node_type}_inputs_schema_invalid",
                    severity="error",
                    message=f"{node_type} node inputs do not match schema: {exc.errors()[0].get('msg')}",
                    node_id=node_id,
                )
            )
            continue
        if not require_runtime_inputs:
            continue
        missing = _missing_runtime_inputs(node_type, inputs)
        for field_name in missing:
            issues.append(
                PlanValidationIssue(
                    code=f"missing_{field_name}",
                    severity="error",
                    message=f"{node_type} node requires inputs.{field_name} before runtime execution.",
                    node_id=node_id,
                )
            )


def _validate_skill_contracts(
    plan: ExecutionPlan,
    metadata_items: list[SkillMetadata],
    issues: list[PlanValidationIssue],
) -> None:
    node_types = {str(node.get("node_type") or "") for node in plan.nodes}
    for metadata in metadata_items:
        for node_type in metadata.node_policy.required_nodes:
            if node_type not in node_types:
                issues.append(
                    PlanValidationIssue(
                        code="skill_required_node_missing",
                        severity="error",
                        message=f"Skill {metadata.name} requires node type: {node_type}",
                    )
                )

        owned_nodes = [node for node in plan.nodes if str(node.get("skill") or "") == metadata.name]
        for node in owned_nodes:
            node_type = str(node.get("node_type") or "")
            if node_type in set(metadata.node_policy.forbidden_nodes):
                issues.append(
                    PlanValidationIssue(
                        code="skill_forbidden_node_requested",
                        severity="error",
                        message=f"Skill {metadata.name} forbids node type: {node_type}",
                        node_id=str(node.get("node_id") or ""),
                    )
                )

        action_values = _skill_action_values(owned_nodes)
        for action in metadata.safety_contract.forbidden_actions:
            if any(_matches_action(action, value) for value in action_values):
                issues.append(
                    PlanValidationIssue(
                        code="skill_forbidden_action_requested",
                        severity="error",
                        message=f"Skill {metadata.name} forbids action: {action}",
                    )
                )

        if metadata.safety_contract.draft_only or metadata.safety_contract.manual_confirmation_required:
            for node in plan.nodes:
                if str(node.get("node_type") or "") != "workorder":
                    continue
                inputs = dict(node.get("inputs") or {})
                if metadata.safety_contract.draft_only and inputs.get("draft_only") is not True:
                    issues.append(
                        PlanValidationIssue(
                            code="skill_draft_only_required",
                            severity="error",
                            message=f"Skill {metadata.name} requires draft-only workorder output.",
                            node_id=str(node.get("node_id") or ""),
                        )
                    )
                if metadata.safety_contract.manual_confirmation_required and inputs.get("manual_confirmation_required") is not True:
                    issues.append(
                        PlanValidationIssue(
                            code="skill_manual_confirmation_required",
                            severity="error",
                            message=f"Skill {metadata.name} requires manual confirmation.",
                            node_id=str(node.get("node_id") or ""),
                        )
                    )


def _skill_action_values(nodes: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for node in nodes:
        values.append(str(node.get("node_type") or ""))
        values.extend(_as_list(node.get("required_tools")))
        inputs = dict(node.get("inputs") or {})
        for key in ("requested_action", "action_type", "workorder_action"):
            if inputs.get(key):
                values.append(str(inputs[key]))
    return values


def _matches_action(action: str, value: str) -> bool:
    action_tokens = re.findall(r"[a-z0-9]+", action.lower())
    value_tokens = re.findall(r"[a-z0-9]+", value.lower())
    if not action_tokens:
        return False
    width = len(action_tokens)
    return any(value_tokens[index : index + width] == action_tokens for index in range(len(value_tokens) - width + 1))


def _coerce_node_inputs(node_type: str, inputs: dict[str, Any]) -> Any:
    model_by_node = {
        "sql": SqlNodeInputs,
        "rag": RagNodeInputs,
        "report": ReportNodeInputs,
        "workorder": WorkorderNodeInputs,
        "approval": ApprovalNodeInputs,
    }
    model = model_by_node.get(node_type)
    return model.model_validate(inputs) if model is not None else inputs


def _missing_runtime_inputs(node_type: str, inputs: dict[str, Any]) -> list[str]:
    if node_type == "sql" and not str(inputs.get("sql_query") or "").strip():
        return ["sql_query"]
    if node_type == "rag" and not str(inputs.get("query") or inputs.get("kb_query") or "").strip():
        return ["query"]
    if node_type == "report" and not str(inputs.get("operation_report_payload") or "").strip():
        return ["operation_report_payload"]
    return []


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
