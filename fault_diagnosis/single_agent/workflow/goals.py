"""Deterministic IntentGoal / GoalSet construction."""

from __future__ import annotations

from typing import Any

from .contracts import GoalSet, IntentGoal, WorkflowObjects

GOAL_PRIORITY = {
    "clarify_missing_context": 0,
    "refresh_current_status": 1,
    "check_runtime_status": 2,
    "explain_fault_code": 3,
    "diagnose_fault": 4,
    "assess_severity": 5,
    "recommend_resolution": 6,
    "decide_workorder": 7,
    "generate_report": 8,
    "answer_meta_question": 9,
}

GOAL_TO_INTENT = {
    "explain_fault_code": "explain_alarm_code",
    "check_runtime_status": "check_current_status",
    "refresh_current_status": "check_current_status",
    "diagnose_fault": "fault_diagnosis",
    "assess_severity": "severity_assessment",
    "recommend_resolution": "resolution_recommendation",
    "generate_report": "report_generation",
    "decide_workorder": "workorder_decision",
    "answer_meta_question": "permission_scope_query",
}

_FAULT_CODE_WORDS = ("故障码", "告警码", "报警码", "是什么意思", "什么含义", "解释")
_STATUS_WORDS = ("现在", "当前", "有没有故障", "是否故障", "状态", "还在报警", "还异常")
_DIAGNOSIS_WORDS = ("诊断", "故障", "原因", "根因", "为什么", "异常")
_SEVERITY_WORDS = ("严重", "影响", "风险", "后果")
_RESOLUTION_WORDS = ("怎么处理", "如何处理", "怎么解决", "如何解决", "建议", "处置", "解决")
_REPORT_WORDS = ("报告", "导出", "生成报告", "出报告")
_WORKORDER_WORDS = ("工单", "派单", "派人", "要不要处理", "是否处理", "要不要生成工单", "是不是要生成工单")
_PERMISSION_WORDS = ("权限", "身份", "能访问", "可以访问", "哪些设备", "哪些数据")


def build_goal_set(
    *,
    message: str,
    payload: dict[str, Any],
    resolved_context: Any,
    route_hint: dict[str, Any] | None = None,
) -> GoalSet:
    """Build deterministic structured goals without selecting tools."""

    route_hint = dict(route_hint or {})
    text = (message or payload.get("user_message") or "").strip()
    compact = text.replace(" ", "").lower()
    context = _context_dict(resolved_context)
    relation = str(context.get("relation_to_previous") or "new_case")
    referenced_artifact_id = str(context.get("referenced_artifact_id") or "").strip()
    stale = bool(context.get("stale_evidence"))
    missing_context = [str(item) for item in context.get("missing_context") or [] if str(item)]
    inherited_slots = context.get("inherited_slots") if isinstance(context.get("inherited_slots"), dict) else {}
    current_payload_device = str(payload.get("equipment_hint") or "").strip()
    context_refs = [referenced_artifact_id] if referenced_artifact_id and not _explicit_new_device_without_inheritance(current_payload_device, inherited_slots) else []
    legacy_candidates = [str(item) for item in route_hint.get("legacy_candidates") or [] if str(item)]
    requested_output = str(route_hint.get("requested_output") or "answer")
    task_type = _task_type_value(route_hint.get("task_type"))
    objects = route_hint.get("objects")
    has_fault_code = bool(payload.get("fault_code_hint") or _object_values(objects, "alarm_codes"))
    has_device = bool(payload.get("equipment_hint") or _object_values(objects, "device_ids"))

    specs: list[dict[str, Any]] = []

    if relation == "ambiguous":
        specs.append(_goal_spec("clarify_missing_context", text, source="inferred_from_context", status="ready", missing_slots=missing_context, reason="上下文指代存在歧义，需要用户确认对象。"))
        if _has_any(compact, _SEVERITY_WORDS):
            specs.append(_goal_spec("assess_severity", text, source="inferred_from_context", status="blocked", missing_slots=missing_context or ["device_or_fault_context"], reason="需要先明确要评估的设备或故障。"))
        return _finalize_goal_set(specs, route_hint=route_hint)

    if relation == "report_handoff" or requested_output == "report" or _has_any(compact, _REPORT_WORDS):
        specs.append(_goal_spec("generate_report", text, source=_source_for_relation(relation), expected_output="report", context_refs=context_refs, reason="用户要求生成或导出报告。"))

    if relation == "action_followup" and (_has_any(compact, _WORKORDER_WORDS) or "workorder_decision" in legacy_candidates):
        if stale:
            specs.append(_goal_spec("refresh_current_status", text, source="inferred_from_context", required_slots=["device"], required_evidence=["latest_realtime_status"], context_refs=context_refs, reason="上一轮证据已滞后，工单判断前需要刷新当前状态。"))
            specs.append(_goal_spec("decide_workorder", text, source="inferred_from_context", expected_output="workorder_decision", risk_level="requires_confirmation", required_evidence=["diagnosis_summary", "severity_or_status_level", "latest_realtime_status"], context_refs=context_refs, reason="基于上一轮结果判断是否生成待确认工单草稿，不能直接派发。"))
        else:
            specs.extend(_workorder_followup_specs(text, context_refs=context_refs, source="inferred_from_context"))

    if relation == "continuation" and _has_any(compact, _SEVERITY_WORDS):
        specs.append(_goal_spec("assess_severity", text, source="inferred_from_context", required_evidence=["diagnosis_summary", "severity_or_status_level"], context_refs=context_refs, reason="用户基于上一轮结果询问严重程度。"))

    if relation == "refresh_current_status":
        specs.append(_goal_spec("refresh_current_status", text, source="inferred_from_context", required_slots=["device"], required_evidence=["latest_realtime_status"], context_refs=context_refs, reason="用户要求刷新当前状态。"))

    if _has_any(compact, _PERMISSION_WORDS) or task_type == "permission_scope_query":
        specs.append(_goal_spec("answer_meta_question", text, expected_output="answer", reason="用户询问身份或权限范围。"))
    if has_fault_code and (_has_any(compact, _FAULT_CODE_WORDS) or "explain_alarm_code" in legacy_candidates):
        specs.append(_goal_spec("explain_fault_code", text, required_slots=["fault_code"], required_evidence=["manual_or_fault_code_reference"], reason="用户需要解释故障码。"))
    if _has_any(compact, _STATUS_WORDS) or "check_current_status" in legacy_candidates:
        specs.append(_goal_spec("check_runtime_status", text, required_slots=["device"], missing_slots=[] if has_device else ["device"], status="ready" if has_device else "blocked", required_evidence=["latest_realtime_status"], reason="用户需要查询当前运行状态。"))
    if (_has_any(compact, _DIAGNOSIS_WORDS) or "fault_diagnosis" in legacy_candidates) and not _only_fault_code_explanation(compact):
        specs.append(_goal_spec("diagnose_fault", text, required_slots=["device"], missing_slots=[] if has_device else ["device"], status="ready" if has_device or has_fault_code else "blocked", required_evidence=["runtime_status", "fault_code_or_symptom"], reason="用户需要判断是否存在故障或形成诊断。"))
    if _has_any(compact, _SEVERITY_WORDS) or "severity_assessment" in legacy_candidates:
        specs.append(_goal_spec("assess_severity", text, required_evidence=["diagnosis_summary", "severity_or_status_level"], reason="用户需要评估严重性。"))
    if _has_any(compact, _RESOLUTION_WORDS) or "resolution_recommendation" in legacy_candidates:
        specs.append(_goal_spec("recommend_resolution", text, required_evidence=["diagnosis_summary", "manual_or_policy_reference"], reason="用户需要处置建议。"))
    if _has_any(compact, _WORKORDER_WORDS) or "workorder_decision" in legacy_candidates:
        status, missing, reason = _followup_status(missing_context, inherited_slots, relation)
        specs.append(_goal_spec("decide_workorder", text, status=status, expected_output="workorder_decision", risk_level="requires_confirmation", missing_slots=missing, required_evidence=["diagnosis_summary", "severity_or_status_level", "recommended_action_policy"], context_refs=context_refs if status != "blocked" else [], reason=reason))

    if missing_context and not inherited_slots and relation in {"action_followup", "continuation", "report_handoff"}:
        for spec in specs:
            if spec["goal_type"] in {"decide_workorder", "generate_report", "assess_severity", "diagnose_fault", "recommend_resolution"}:
                spec["status"] = "blocked"
                spec["missing_slots"] = _dedupe([*spec.get("missing_slots", []), *missing_context])
                spec["context_refs"] = []
                spec["reason"] = "当前身份或上下文不足，不能继承上一轮结果。"

    if not specs:
        specs.append(_fallback_spec(text, task_type, requested_output, missing_context))

    return _finalize_goal_set(specs, route_hint=route_hint)


def summarize_goal_set(value: Any) -> dict[str, Any]:
    """Return compact GoalSet debug payload for plan, complete and trace."""

    data = _goal_set_dict(value)
    goals = _goal_dicts(data.get("goals"))
    return {
        "primary_goal_id": data.get("primary_goal_id"),
        "goal_types": [str(goal.get("goal_type")) for goal in goals if goal.get("goal_type")],
        "execution_order": list(data.get("execution_order") or []),
        "blocked_goals": list(data.get("blocked_goals") or []),
        "legacy_intent_projection": list(data.get("legacy_intent_projection") or []),
        "goal_summary": str(data.get("goal_summary") or ""),
    }


def _finalize_goal_set(specs: list[dict[str, Any]], *, route_hint: dict[str, Any]) -> GoalSet:
    deduped: list[dict[str, Any]] = []
    seen_types: set[str] = set()
    for spec in sorted(specs, key=lambda item: GOAL_PRIORITY.get(str(item.get("goal_type")), 99)):
        goal_type = str(spec.get("goal_type") or "")
        if not goal_type or goal_type in seen_types:
            continue
        seen_types.add(goal_type)
        deduped.append(spec)
    if not deduped:
        deduped.append(_fallback_spec("", _task_type_value(route_hint.get("task_type")), str(route_hint.get("requested_output") or "answer"), []))

    goals: list[IntentGoal] = []
    type_to_id: dict[str, str] = {}
    for index, spec in enumerate(deduped, start=1):
        goal_type = str(spec["goal_type"])
        goal_id = f"goal_{index}_{goal_type}"
        type_to_id[goal_type] = goal_id
        goals.append(
            IntentGoal(
                goal_id=goal_id,
                goal_type=goal_type,
                description=str(spec.get("description") or _description_for_goal(goal_type)),
                status=spec.get("status") or "ready",
                depends_on=[],
                required_slots=list(spec.get("required_slots") or []),
                missing_slots=list(spec.get("missing_slots") or []),
                required_evidence=list(spec.get("required_evidence") or []),
                expected_output=spec.get("expected_output") or _expected_output(goal_type),
                risk_level=spec.get("risk_level") or _risk_level(goal_type),
                source=spec.get("source") or "explicit_user_request",
                context_refs=list(spec.get("context_refs") or []),
                reason=str(spec.get("reason") or ""),
            )
        )
    refresh_id = type_to_id.get("refresh_current_status")
    if refresh_id:
        for goal in goals:
            if goal.goal_type == "decide_workorder" and refresh_id != goal.goal_id:
                goal.depends_on = _dedupe([*goal.depends_on, refresh_id])

    execution_order = _execution_order(goals)
    blocked_goals = [goal.goal_id for goal in goals if goal.status == "blocked"]
    primary_goal_id = _primary_goal_id(goals, execution_order)
    projection = _intent_projection(goals)
    summary = _goal_summary(goals, primary_goal_id)
    return GoalSet(
        primary_goal_id=primary_goal_id,
        goals=goals,
        execution_order=execution_order,
        blocked_goals=blocked_goals,
        legacy_intent_projection=projection,
        goal_summary=summary,
    )


def _goal_spec(
    goal_type: str,
    text: str,
    *,
    status: str = "ready",
    depends_on: list[str] | None = None,
    required_slots: list[str] | None = None,
    missing_slots: list[str] | None = None,
    required_evidence: list[str] | None = None,
    expected_output: str | None = None,
    risk_level: str | None = None,
    source: str = "explicit_user_request",
    context_refs: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "goal_type": goal_type,
        "description": _description_for_goal(goal_type, text),
        "status": status,
        "depends_on": depends_on or [],
        "required_slots": required_slots or [],
        "missing_slots": missing_slots or [],
        "required_evidence": required_evidence or [],
        "expected_output": expected_output or _expected_output(goal_type),
        "risk_level": risk_level or _risk_level(goal_type),
        "source": source,
        "context_refs": context_refs or [],
        "reason": reason,
    }


def _workorder_followup_specs(text: str, *, context_refs: list[str], source: str) -> list[dict[str, Any]]:
    return [
        _goal_spec("assess_severity", text, source=source, required_evidence=["diagnosis_summary", "severity_or_status_level"], context_refs=context_refs, reason="工单判断前需要确认严重程度。"),
        _goal_spec("decide_workorder", text, source=source, expected_output="workorder_decision", risk_level="requires_confirmation", required_evidence=["diagnosis_summary", "severity_or_status_level", "recommended_action_policy"], context_refs=context_refs, reason="判断是否生成待确认工单草稿，不能直接派发。"),
    ]


def _followup_status(missing_context: list[str], inherited_slots: dict[str, Any], relation: str) -> tuple[str, list[str], str]:
    if missing_context and not inherited_slots and relation in {"action_followup", "continuation", "report_handoff"}:
        return "blocked", missing_context, "当前身份或上下文不足，不能基于上一轮结果判断工单。"
    return "ready", [], "用户要求判断是否生成待确认工单草稿。"


def _fallback_spec(text: str, task_type: str, requested_output: str, missing_context: list[str]) -> dict[str, Any]:
    if missing_context:
        return _goal_spec("clarify_missing_context", text, status="ready", expected_output="clarification", missing_slots=missing_context, reason="缺少必要上下文，需要澄清。")
    if requested_output == "report":
        return _goal_spec("generate_report", text, expected_output="report", reason="按请求输出报告。")
    if task_type == "permission_scope_query":
        return _goal_spec("answer_meta_question", text, reason="回答元信息或权限问题。")
    return _goal_spec("check_runtime_status", text, required_slots=["device"], reason="默认按运行状态查询处理。")


def _execution_order(goals: list[IntentGoal]) -> list[str]:
    remaining = {goal.goal_id: goal for goal in goals}
    ordered: list[str] = []
    while remaining:
        ready = [
            goal
            for goal in remaining.values()
            if all(dep in ordered or dep not in remaining for dep in goal.depends_on)
        ]
        if not ready:
            ready = list(remaining.values())
        ready.sort(key=lambda goal: (GOAL_PRIORITY.get(goal.goal_type, 99), goal.goal_id))
        goal = ready[0]
        ordered.append(goal.goal_id)
        remaining.pop(goal.goal_id, None)
    return ordered


def _primary_goal_id(goals: list[IntentGoal], execution_order: list[str]) -> str | None:
    by_id = {goal.goal_id: goal for goal in goals}
    ready_goals = [goal for goal in goals if goal.status == "ready"]
    ready_types = {goal.goal_type for goal in ready_goals}
    if not ready_goals and len(goals) == 1 and goals[0].goal_type == "clarify_missing_context":
        return goals[0].goal_id
    for goal_type in ("generate_report", "decide_workorder"):
        for goal in ready_goals:
            if goal.goal_type == goal_type:
                return goal.goal_id
    for goal in ready_goals:
        if goal.goal_type == "diagnose_fault":
            return goal.goal_id
    for goal_id in execution_order:
        goal = by_id.get(goal_id)
        if goal and goal.status == "ready":
            return goal.goal_id
    if ready_types == {"clarify_missing_context"}:
        return ready_goals[0].goal_id
    return None


def _intent_projection(goals: list[IntentGoal]) -> list[str]:
    return _dedupe([GOAL_TO_INTENT.get(goal.goal_type) for goal in goals if goal.status != "skipped"])


def _goal_summary(goals: list[IntentGoal], primary_goal_id: str | None) -> str:
    types = ", ".join(goal.goal_type for goal in goals)
    blocked = [goal.goal_type for goal in goals if goal.status == "blocked"]
    parts = [f"goals: {types or 'none'}"]
    if primary_goal_id:
        parts.append(f"primary: {primary_goal_id}")
    if blocked:
        parts.append(f"blocked: {', '.join(blocked)}")
    return "；".join(parts)


def _description_for_goal(goal_type: str, text: str = "") -> str:
    labels = {
        "explain_fault_code": "解释故障码含义",
        "check_runtime_status": "查询当前运行状态",
        "diagnose_fault": "判断并诊断故障",
        "assess_severity": "评估故障严重程度",
        "recommend_resolution": "给出处置建议",
        "generate_report": "生成或导出报告",
        "decide_workorder": "判断是否生成待确认工单草稿",
        "refresh_current_status": "刷新当前实时状态",
        "clarify_missing_context": "澄清缺失上下文",
        "answer_meta_question": "回答权限或元信息问题",
    }
    return labels.get(goal_type, text or goal_type)


def _expected_output(goal_type: str) -> str:
    if goal_type == "generate_report":
        return "report"
    if goal_type == "decide_workorder":
        return "workorder_decision"
    if goal_type == "clarify_missing_context":
        return "clarification"
    return "answer"


def _risk_level(goal_type: str) -> str:
    return "requires_confirmation" if goal_type == "decide_workorder" else "read_only"


def _source_for_relation(relation: str) -> str:
    return "inferred_from_context" if relation in {"action_followup", "report_handoff", "continuation", "refresh_current_status"} else "explicit_user_request"


def _context_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _goal_set_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _goal_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if hasattr(item, "model_dump"):
            result.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            result.append(dict(item))
    return result


def _task_type_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _object_values(objects: Any, key: str) -> list[str]:
    if isinstance(objects, WorkflowObjects):
        values = getattr(objects, key, [])
    elif isinstance(objects, dict):
        values = objects.get(key, [])
    else:
        values = []
    return [str(item) for item in values or [] if str(item)]


def _explicit_new_device_without_inheritance(current_device: str, inherited_slots: dict[str, Any]) -> bool:
    inherited_device = str(inherited_slots.get("device") or "").strip()
    return bool(current_device and inherited_device and current_device != inherited_device)


def _only_fault_code_explanation(text: str) -> bool:
    return _has_any(text, _FAULT_CODE_WORDS) and not _has_any(text, _STATUS_WORDS + _RESOLUTION_WORDS + _SEVERITY_WORDS)


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword.lower() in text for keyword in keywords if keyword)


def _dedupe(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))
