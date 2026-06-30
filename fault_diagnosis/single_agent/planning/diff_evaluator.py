"""Deterministic Phase 4.2 policy diff evaluator."""

from __future__ import annotations

from typing import Any

from .diff_contracts import (
    DiffSeverity,
    EvidenceDiff,
    NodeDiff,
    OutputDiff,
    PlanningDiff,
    SafetyDiff,
    ToolDiff,
)

_SEVERITY_RANK: dict[str, int] = {"none": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
_SAFETY_NODES = {"permission_check", "risk_check", "audit_log", "output_guardrail", "evidence_validation"}
_ACTION_NODES = {"workorder_decision", "permission_check", "risk_check", "audit_log"}
_READ_ONLY_NODES = {"sql", "knowledge", "analysis", "report", "resolution_recommendation"}
_COMPLETED_ACTION_WORDS = (
    "executed",
    "dispatched",
    "applied",
    "reset_done",
    "已执行",
    "已派发",
    "已下发",
    "已复位",
    "已重启",
    "已创建工单",
    "已生成工单",
)
_MIGRATION_REASON = "shadow evaluation only; no execution migration in Phase 4.2"


def build_planning_diff(
    legacy_plan: dict[str, Any] | None,
    shadow_plan: Any,
    decision: Any | None = None,
    route: Any | None = None,
) -> PlanningDiff:
    """Compare legacy workflow policy output with the shadow planner without side effects."""

    try:
        legacy = _legacy_view(legacy_plan, decision, route)
        shadow = _to_dict(shadow_plan)
        projection = _to_dict(shadow.get("legacy_projection"))
        legacy = _merge_projection(legacy, projection)
        node_diffs = _node_diffs(legacy, shadow)
        tool_diffs = _tool_diffs(legacy, shadow)
        evidence_diffs = _evidence_diffs(legacy, shadow)
        output_diffs = _output_diffs(legacy, shadow)
        safety_diffs = _safety_diffs(legacy, shadow, node_diffs, tool_diffs, evidence_diffs, output_diffs)
        diff = PlanningDiff(
            node_diffs=node_diffs,
            tool_diffs=tool_diffs,
            evidence_diffs=evidence_diffs,
            output_diffs=output_diffs,
            safety_diffs=safety_diffs,
        )
        return _finalize(diff, legacy=legacy, shadow=shadow)
    except Exception as exc:  # noqa: BLE001 - diff must not interrupt the main chain
        diff = PlanningDiff(
            evidence_diffs=[
                EvidenceDiff(
                    evidence_key="planning_diff_build",
                    diff_type="diff_build_failed",
                    severity="warning",
                    reason=f"planning diff build failed: {_short_text(str(exc))}",
                )
            ],
        )
        return _finalize(diff, legacy={}, shadow={})


def _legacy_view(legacy_plan: dict[str, Any] | None, decision: Any | None, route: Any | None) -> dict[str, Any]:
    legacy = dict(legacy_plan or {})
    if decision is not None:
        for key, attr in (
            ("primary_task_type", "primary_task_type"),
            ("intent_stack", "intent_stack"),
            ("enabled_nodes", "enabled_nodes"),
            ("runtime_tools", "runtime_tools"),
            ("evidence_mode", "evidence_mode"),
            ("should_refresh_runtime_data", "should_refresh_runtime_data"),
            ("requested_output", "requested_output"),
            ("workorder_decision_enabled", "enabled_nodes"),
            ("report_enabled", "enabled_nodes"),
            ("permission_check_enabled", "enabled_nodes"),
            ("risk_check_enabled", "enabled_nodes"),
            ("output_guardrail_enabled", "enabled_nodes"),
            ("workflow_policy", "workflow_policy"),
            ("resolved_context", "resolved_context"),
            ("goal_set", "goal_set"),
            ("task_family", "task_family"),
            ("plan_mode", "plan_mode"),
        ):
            if key in legacy:
                continue
            value = getattr(decision, attr, None)
            if attr == "enabled_nodes":
                nodes = _bool_dict(value)
                if key == "workorder_decision_enabled":
                    value = nodes.get("workorder_decision", False)
                elif key == "report_enabled":
                    value = nodes.get("report", False)
                elif key == "permission_check_enabled":
                    value = nodes.get("permission_check", False)
                elif key == "risk_check_enabled":
                    value = nodes.get("risk_check", False)
                elif key == "output_guardrail_enabled":
                    value = nodes.get("output_guardrail", False)
                else:
                    value = nodes
            legacy[key] = value
    if route is not None:
        for key in ("primary_task_type", "intent_stack", "task_family", "plan_mode"):
            legacy.setdefault(key, getattr(route, key, None))
    policy = _to_dict(legacy.get("workflow_policy"))
    legacy.setdefault("policy_enabled_nodes", _boolish_policy_nodes(policy.get("enabled_nodes")))
    legacy.setdefault("policy_evidence_requirements", _to_dict(policy.get("evidence_requirements")))
    legacy.setdefault("policy_guardrails", _strings(policy.get("guardrails")))
    legacy.setdefault("output_schema", legacy.get("output_schema") or policy.get("output_schema"))
    return legacy


def _merge_projection(legacy: dict[str, Any], projection: dict[str, Any]) -> dict[str, Any]:
    merged = dict(legacy)
    mapping = {
        "primary_task_type": "legacy_primary_task_type",
        "intent_stack": "legacy_intent_stack",
        "enabled_nodes": "legacy_enabled_nodes",
        "runtime_tools": "legacy_runtime_tools",
        "requested_output": "legacy_requested_output",
        "evidence_mode": "legacy_evidence_mode",
        "should_refresh_runtime_data": "legacy_should_refresh_runtime_data",
    }
    for key, projection_key in mapping.items():
        if _missing(merged.get(key)) and projection_key in projection:
            merged[key] = projection.get(projection_key)
    return merged


def _node_diffs(legacy: dict[str, Any], shadow: dict[str, Any]) -> list[NodeDiff]:
    missing = []
    if "enabled_nodes" not in legacy:
        missing.append(
            NodeDiff(
                node="enabled_nodes",
                legacy_state="unknown",
                shadow_state="unknown",
                diff_type="unknown",
                severity="warning",
                reason="legacy field missing: enabled_nodes",
            )
        )
    legacy_nodes = _bool_dict(legacy.get("enabled_nodes"))
    shadow_nodes = _shadow_nodes(shadow)
    names = sorted(set(legacy_nodes) | set(shadow_nodes))
    diffs: list[NodeDiff] = [*missing]
    stale = _stale_evidence(legacy, shadow)
    clarify = _is_clarification(shadow)
    for node in names:
        legacy_state = "enabled" if legacy_nodes.get(node) else "skipped"
        shadow_state = shadow_nodes.get(node, "skipped")
        if legacy_state == shadow_state or (not legacy_nodes.get(node) and shadow_state in {"skipped", "shadow_only"}):
            diffs.append(
                NodeDiff(
                    node=node,
                    legacy_state=legacy_state,
                    shadow_state=shadow_state,
                    diff_type="exact_match",
                    severity="none",
                    reason="legacy and shadow node state match",
                )
            )
            continue
        if shadow_state == "blocked" and clarify:
            diffs.append(
                NodeDiff(
                    node=node,
                    legacy_state=legacy_state,
                    shadow_state=shadow_state,
                    diff_type="shadow_blocks_for_clarification",
                    severity="info",
                    reason="clarification blocks shadow business node",
                )
            )
            continue
        if not legacy_nodes.get(node) and shadow_state == "enabled":
            severity: DiffSeverity = "info" if node == "sql" and stale else "warning"
            if node in _ACTION_NODES:
                severity = "warning"
            diffs.append(
                NodeDiff(
                    node=node,
                    legacy_state=legacy_state,
                    shadow_state=shadow_state,
                    diff_type="shadow_extra",
                    severity=severity,
                    reason="shadow wants node not enabled by legacy policy",
                    safety_related=node in _ACTION_NODES,
                )
            )
            continue
        if legacy_nodes.get(node) and shadow_state != "enabled":
            if node in _SAFETY_NODES:
                diffs.append(
                    NodeDiff(
                        node=node,
                        legacy_state=legacy_state,
                        shadow_state=shadow_state,
                        diff_type="missing_required_node",
                        severity="critical",
                        reason="shadow skipped legacy safety guardrail node",
                        safety_related=True,
                    )
                )
            else:
                diffs.append(
                    NodeDiff(
                        node=node,
                        legacy_state=legacy_state,
                        shadow_state=shadow_state,
                        diff_type="legacy_extra",
                        severity="warning" if node in _READ_ONLY_NODES else "error",
                        reason="legacy enables node not enabled by shadow plan",
                        safety_related=False,
                    )
                )
            continue
        diffs.append(
            NodeDiff(
                node=node,
                legacy_state=legacy_state,
                shadow_state=shadow_state,
                diff_type="state_mismatch",
                severity="warning",
                reason="legacy and shadow node states differ",
            )
        )
    return diffs


def _tool_diffs(legacy: dict[str, Any], shadow: dict[str, Any]) -> list[ToolDiff]:
    diffs: list[ToolDiff] = []
    if "runtime_tools" not in legacy:
        diffs.append(
            ToolDiff(
                tool="runtime_tools",
                diff_type="unknown",
                severity="warning",
                reason="legacy field missing: runtime_tools",
            )
        )
    legacy_tools = set(_strings(legacy.get("runtime_tools")))
    tool_plan = _to_dict(shadow.get("tool_plan"))
    candidate_tools = set(_strings(tool_plan.get("candidate_tools")))
    authorized_tools = set(_strings(tool_plan.get("authorized_runtime_tools")))
    for tool in sorted(legacy_tools | candidate_tools | authorized_tools):
        in_legacy = tool in legacy_tools
        in_candidate = tool in candidate_tools
        in_authorized = tool in authorized_tools
        if in_legacy and in_authorized:
            diff_type = "exact_match"
            severity: DiffSeverity = "none"
            reason = "shadow authorized tool is present in legacy runtime tools"
        elif in_authorized and not in_legacy:
            diff_type = "shadow_authorized_extra"
            severity = "critical"
            reason = "shadow authorized runtime tool exceeds legacy runtime_tools"
        elif in_candidate and not in_authorized:
            diff_type = "shadow_candidate_only"
            severity = "info"
            reason = "shadow candidate is not authorized for runtime execution"
        elif in_legacy and not in_authorized:
            diff_type = "legacy_only"
            severity = "info"
            reason = "legacy runtime tool is more conservative than shadow authorization"
        else:
            diff_type = "unauthorized_shadow_tool"
            severity = "critical"
            reason = "shadow tool state is not authorized"
        diffs.append(
            ToolDiff(
                tool=tool,
                in_legacy_runtime_tools=in_legacy,
                in_shadow_candidate_tools=in_candidate,
                in_shadow_authorized_tools=in_authorized,
                diff_type=diff_type,
                severity=severity,
                reason=reason,
                safety_related=severity == "critical",
            )
        )
    return diffs


def _evidence_diffs(legacy: dict[str, Any], shadow: dict[str, Any]) -> list[EvidenceDiff]:
    diffs: list[EvidenceDiff] = []
    evidence = _to_dict(shadow.get("evidence_plan"))
    output = _to_dict(shadow.get("output_plan"))
    legacy_requirements = _to_dict(legacy.get("policy_evidence_requirements"))
    shadow_required = set(_strings(evidence.get("required_evidence")))
    shadow_missing = set(_strings(evidence.get("missing_evidence")))
    disclosures = set(_strings(evidence.get("disclosure_required")) + _strings(output.get("required_disclosures")))
    legacy_refresh = bool(legacy.get("should_refresh_runtime_data"))
    shadow_refresh = bool(evidence.get("refresh_required"))
    stale = _stale_evidence(legacy, shadow)
    workorder = _is_workorder(legacy, shadow)

    if "evidence_mode" not in legacy:
        diffs.append(
            EvidenceDiff(
                evidence_key="evidence_mode",
                diff_type="unknown",
                severity="warning",
                reason="legacy field missing: evidence_mode",
            )
        )
    if "should_refresh_runtime_data" not in legacy:
        diffs.append(
            EvidenceDiff(
                evidence_key="should_refresh_runtime_data",
                diff_type="unknown",
                severity="warning",
                reason="legacy field missing: should_refresh_runtime_data",
            )
        )
    if stale:
        if shadow_refresh or "evidence_stale" in disclosures:
            diffs.append(
                EvidenceDiff(
                    evidence_key="stale_evidence",
                    legacy_requirement=str(legacy_refresh),
                    shadow_requirement=str(shadow_refresh),
                    diff_type="shadow_requires_more",
                    severity="info" if shadow_refresh else "warning",
                    reason="stale evidence is refreshed or disclosed by shadow plan",
                )
            )
        else:
            diffs.append(
                EvidenceDiff(
                    evidence_key="stale_evidence",
                    legacy_requirement=str(legacy_refresh),
                    shadow_requirement=str(shadow_refresh),
                    diff_type="stale_refresh_mismatch",
                    severity="critical" if workorder else "error",
                    reason="stale evidence lacks shadow refresh or disclosure",
                    safety_related=workorder,
                )
            )
    if legacy_refresh and not shadow_refresh and "evidence_stale" not in disclosures:
        diffs.append(
            EvidenceDiff(
                evidence_key="runtime_refresh",
                legacy_requirement="refresh_required",
                shadow_requirement="refresh_not_required",
                diff_type="legacy_requires_more",
                severity="warning",
                reason="legacy refresh requirement is not mirrored by shadow plan",
            )
        )
    for key, required in sorted(legacy_requirements.items()):
        if not required:
            continue
        normalized = _evidence_key(str(key))
        if normalized and normalized not in shadow_required and normalized not in shadow_missing:
            diffs.append(
                EvidenceDiff(
                    evidence_key=str(key),
                    legacy_requirement="required",
                    shadow_requirement=None,
                    diff_type="legacy_requires_more",
                    severity="warning",
                    reason="legacy evidence requirement is not visible in shadow plan",
                )
            )
    for key in sorted(shadow_required):
        if not legacy_requirements:
            continue
        if key not in {_evidence_key(str(item)) for item, required in legacy_requirements.items() if required}:
            diffs.append(
                EvidenceDiff(
                    evidence_key=key,
                    legacy_requirement=None,
                    shadow_requirement="required",
                    diff_type="shadow_requires_more",
                    severity="info",
                    reason="shadow asks for additional evidence",
                )
            )
    return diffs or [
        EvidenceDiff(
            evidence_key="evidence_requirements",
            legacy_requirement="not_compared",
            shadow_requirement="not_compared",
            diff_type="exact_match",
            severity="none",
            reason="no evidence mismatch detected",
        )
    ]


def _output_diffs(legacy: dict[str, Any], shadow: dict[str, Any]) -> list[OutputDiff]:
    diffs: list[OutputDiff] = []
    output = _to_dict(shadow.get("output_plan"))
    expected = str(output.get("expected_output") or "answer")
    requested = str(legacy.get("requested_output") or legacy.get("output_schema") or "answer")
    report_boundary = output.get("report_boundary")
    workorder_boundary = output.get("workorder_boundary")
    final_guardrails = " ".join(_strings(output.get("final_answer_guardrails")) + [str(workorder_boundary or "")])

    if "requested_output" not in legacy:
        diffs.append(
            OutputDiff(
                output_key="requested_output",
                diff_type="unknown",
                severity="warning",
                reason="legacy field missing: requested_output",
            )
        )
    if ("report" in requested or legacy.get("report_enabled")) and expected != "report" and not report_boundary:
        diffs.append(
            OutputDiff(
                output_key="report_boundary",
                legacy_boundary=requested,
                shadow_boundary=expected,
                diff_type="report_boundary_mismatch",
                severity="warning",
                reason="legacy report output is not mirrored by shadow output boundary",
            )
        )
    if _is_workorder(legacy, shadow):
        boundary_text = str(workorder_boundary or "")
        if not boundary_text:
            diffs.append(
                OutputDiff(
                    output_key="workorder_boundary",
                    legacy_boundary="guarded_workorder",
                    shadow_boundary=None,
                    diff_type="workorder_boundary_mismatch",
                    severity="critical",
                    reason="workorder/action output lacks draft or confirmation boundary",
                    safety_related=True,
                )
            )
        elif any(word in boundary_text for word in ("executed", "dispatched", "applied")):
            diffs.append(
                OutputDiff(
                    output_key="workorder_boundary",
                    legacy_boundary="draft_or_confirmation",
                    shadow_boundary=boundary_text,
                    diff_type="workorder_boundary_mismatch",
                    severity="critical",
                    reason="workorder/action boundary uses completed action semantics",
                    safety_related=True,
                )
            )
    if any(word in final_guardrails for word in _COMPLETED_ACTION_WORDS):
        diffs.append(
            OutputDiff(
                output_key="final_answer_guardrails",
                legacy_boundary="no_action_completion_claim",
                shadow_boundary=_short_text(final_guardrails),
                diff_type="action_completion_semantics",
                severity="critical",
                reason="shadow output plan contains completed action semantics",
                safety_related=True,
            )
        )
    return diffs or [
        OutputDiff(
            output_key="output_boundary",
            legacy_boundary=requested,
            shadow_boundary=expected,
            diff_type="exact_match",
            severity="none",
            reason="no output boundary mismatch detected",
        )
    ]


def _safety_diffs(
    legacy: dict[str, Any],
    shadow: dict[str, Any],
    node_diffs: list[NodeDiff],
    tool_diffs: list[ToolDiff],
    evidence_diffs: list[EvidenceDiff],
    output_diffs: list[OutputDiff],
) -> list[SafetyDiff]:
    diffs: list[SafetyDiff] = []
    authorized_extra = [item.tool for item in tool_diffs if item.diff_type in {"shadow_authorized_extra", "unauthorized_shadow_tool"}]
    if authorized_extra:
        diffs.append(
            SafetyDiff(
                safety_key="authorized_runtime_tools",
                legacy_value=_strings(legacy.get("runtime_tools")),
                shadow_value=authorized_extra,
                diff_type="unauthorized_tool",
                severity="critical",
                reason="shadow authorized tools exceed legacy runtime tools",
            )
        )
    missing_guardrails = [item.node for item in node_diffs if item.severity == "critical" and item.node in _SAFETY_NODES]
    if missing_guardrails:
        diffs.append(
            SafetyDiff(
                safety_key="guardrail_nodes",
                legacy_value=missing_guardrails,
                shadow_value="skipped_or_missing",
                diff_type="missing_guardrail",
                severity="critical",
                reason="shadow plan does not preserve legacy safety guardrail nodes",
            )
        )
    if any(item.severity == "critical" and item.safety_related for item in [*evidence_diffs, *output_diffs]):
        diffs.append(
            SafetyDiff(
                safety_key="action_or_stale_boundary",
                legacy_value="guarded",
                shadow_value="unsafe_or_missing",
                diff_type="action_boundary_violation",
                severity="critical",
                reason="shadow evidence or output boundary violates action/stale safety rules",
            )
        )
    context = _to_dict(legacy.get("resolved_context"))
    if context.get("unauthorized_artifact_reference") or context.get("unauthorized_report_reference"):
        diffs.append(
            SafetyDiff(
                safety_key="authorization_scoped_context",
                legacy_value="authorized_scope_only",
                shadow_value="unauthorized_reference",
                diff_type="unauthorized_reference",
                severity="critical",
                reason="shadow or context references unauthorized artifact/report/fault code",
            )
        )
    return diffs or [
        SafetyDiff(
            safety_key="safety_boundary",
            legacy_value="guarded",
            shadow_value="no_violation_detected",
            diff_type="exact_match",
            severity="none",
            reason="no safety mismatch detected",
        )
    ]


def _finalize(diff: PlanningDiff, *, legacy: dict[str, Any], shadow: dict[str, Any]) -> PlanningDiff:
    all_items = [*diff.node_diffs, *diff.tool_diffs, *diff.evidence_diffs, *diff.output_diffs, *diff.safety_diffs]
    highest = "none"
    for item in all_items:
        if _SEVERITY_RANK.get(item.severity, 0) > _SEVERITY_RANK[highest]:
            highest = item.severity
    critical_count = sum(1 for item in all_items if item.severity == "critical")
    warning_count = sum(1 for item in all_items if item.severity == "warning")
    error_count = sum(1 for item in all_items if item.severity == "error")
    info_count = sum(1 for item in all_items if item.severity == "info")
    non_exact = [item for item in all_items if item.diff_type != "exact_match" or item.severity != "none"]
    safety_warning = any(item.severity == "warning" and getattr(item, "safety_related", False) for item in all_items)
    if critical_count:
        status = "unsafe_mismatch"
    elif warning_count or error_count:
        if error_count == 0 and not safety_warning and _only_allowed_non_safety_warnings(non_exact):
            status = "acceptable_diff"
        else:
            status = "needs_review"
    elif info_count:
        status = "acceptable_diff"
    else:
        status = "aligned"
    counters = {
        "node_diff_count": _meaningful_count(diff.node_diffs),
        "tool_diff_count": _meaningful_count(diff.tool_diffs),
        "evidence_diff_count": _meaningful_count(diff.evidence_diffs),
        "output_diff_count": _meaningful_count(diff.output_diffs),
        "safety_diff_count": _meaningful_count(diff.safety_diffs),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "error_count": error_count,
        "info_count": info_count,
    }
    diff.severity = highest  # type: ignore[assignment]
    diff.overall_status = status  # type: ignore[assignment]
    diff.counters = counters
    diff.migration_readiness = {
        "read_only_candidate": bool(_read_only_candidate(legacy, shadow) and critical_count == 0),
        "safe_to_migrate": False,
        "reason": _MIGRATION_REASON,
    }
    diff.summary = _summary(status, highest, counters)
    return diff


def _only_allowed_non_safety_warnings(items: list[Any]) -> bool:
    if not items:
        return True
    for item in items:
        if item.severity not in {"info", "warning", "none"}:
            return False
        if getattr(item, "safety_related", False):
            return False
        if item.severity == "warning" and item.diff_type not in {
            "shadow_extra",
            "legacy_extra",
            "legacy_requires_more",
            "report_boundary_mismatch",
        }:
            return False
    return True


def _summary(status: str, severity: str, counters: dict[str, int]) -> str:
    if status == "aligned":
        return "legacy policy and shadow planner are aligned"
    return (
        f"planning diff {status} with severity={severity}; "
        f"critical={counters.get('critical_count', 0)}, warning={counters.get('warning_count', 0)}"
    )


def _meaningful_count(items: list[Any]) -> int:
    return sum(1 for item in items if item.diff_type != "exact_match" or item.severity != "none")


def _read_only_candidate(legacy: dict[str, Any], shadow: dict[str, Any]) -> bool:
    task_family = str(legacy.get("task_family") or "")
    expected_output = str(_to_dict(shadow.get("output_plan")).get("expected_output") or "")
    return task_family in {"knowledge_lookup", "runtime_status", "reporting"} or expected_output in {"answer", "report"}


def _shadow_nodes(shadow: dict[str, Any]) -> dict[str, str]:
    nodes: dict[str, str] = {}
    for item in shadow.get("nodes") or []:
        data = _to_dict(item)
        node = str(data.get("node") or "").strip()
        state = str(data.get("desired_state") or "skipped")
        if node:
            nodes[node] = state if state in {"enabled", "skipped", "blocked", "shadow_only"} else "unknown"
    return nodes


def _is_clarification(shadow: dict[str, Any]) -> bool:
    output = _to_dict(shadow.get("output_plan"))
    return str(output.get("expected_output") or "") == "clarification"


def _is_workorder(legacy: dict[str, Any], shadow: dict[str, Any]) -> bool:
    output = _to_dict(shadow.get("output_plan"))
    nodes = _shadow_nodes(shadow)
    return bool(
        legacy.get("action_target")
        or str(legacy.get("task_family") or "") == "action_or_workorder"
        or nodes.get("workorder_decision") == "enabled"
        or output.get("expected_output") == "workorder_decision"
        or output.get("workorder_boundary")
    )


def _stale_evidence(legacy: dict[str, Any], shadow: dict[str, Any]) -> bool:
    context = _to_dict(legacy.get("resolved_context"))
    evidence = _to_dict(shadow.get("evidence_plan"))
    return bool(
        context.get("stale_evidence")
        or legacy.get("should_refresh_runtime_data")
        or evidence.get("stale_evidence")
    )


def _evidence_key(value: str) -> str:
    lowered = value.lower()
    if "runtime" in lowered or "current" in lowered or "status" in lowered:
        return "runtime_data"
    if "knowledge" in lowered or "alarm" in lowered or "source" in lowered:
        return "knowledge_source"
    if "report" in lowered:
        return "report_evidence"
    if "permission" in lowered:
        return "permission_result"
    if "risk" in lowered:
        return "risk_result"
    return lowered.replace("need_", "")


def _bool_dict(value: Any) -> dict[str, bool]:
    return {str(key): bool(item) for key, item in _to_dict(value).items()}


def _boolish_policy_nodes(value: Any) -> dict[str, bool]:
    nodes: dict[str, bool] = {}
    for key, item in _to_dict(value).items():
        nodes[str(key)] = item is True
    return nodes


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _short_text(value: str, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[:limit]}..."
