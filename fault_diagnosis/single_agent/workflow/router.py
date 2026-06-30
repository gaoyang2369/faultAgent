"""Rule-first task routing and subgoal decomposition."""

from __future__ import annotations

import re
from typing import Any

from .contracts import GoalSet, TaskRoute, TaskType, WorkflowObjects, WorkflowSubgoal, WorkflowTimeWindow
from .goals import build_goal_set
from .task_family import resolve_task_family

_ALARM_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]\d{3,5})(?![A-Z0-9])", re.IGNORECASE)
_DEVICE_RE = re.compile(
    r"([A-Za-z]+[_-]\d{2,}|[A-Z]{2,}(?:-\d{1,})+|J\d+|\d+号机|[A-Z]+\d+电机\d+)",
    re.IGNORECASE,
)

_REPORT_KEYWORDS = ("报告", "出报告", "生成报告", "导出报告", "日报", "周报", "总结成文档")
_RCA_KEYWORDS = ("rca", "根因分析", "根因", "复盘", "事故分析", "恢复后又复发")
_ACTION_KEYWORDS = (
    "重启",
    "停机",
    "关机",
    "修改",
    "改成",
    "关闭告警",
    "屏蔽告警",
    "确认创建工单",
    "创建工单草稿",
    "生成工单草稿",
    "确认派发",
    "派发工单",
    "直接派发",
    "下发工单",
    "下发",
    "执行",
)
_WORKORDER_KEYWORDS = ("创建工单", "生成工单", "派人", "派单", "维修单", "巡检", "工单")
_WORKORDER_DECISION_KEYWORDS = (
    "要不要生成工单",
    "是否生成工单",
    "是不是要生成工单",
    "要不要工单",
    "是否需要工单",
    "应不应该派单",
    "要不要派单",
    "是否派人处理",
    "要不要派人",
)
_CREATE_WORKORDER_DRAFT_KEYWORDS = ("生成工单草稿", "创建工单草稿", "待确认工单草稿", "工单草稿")
_DISPATCH_WORKORDER_KEYWORDS = ("直接派发", "派给维修", "下发工单", "派发工单", "确认派发", "派单")
_HEALTH_KEYWORDS = ("健康", "风险", "劣化", "趋势", "预测", "评分", "寿命")
_ALARM_KEYWORDS = ("故障码", "告警码", "报警码", "告警", "报警", "异常码")
_TRIAGE_KEYWORDS = ("现在", "当前", "是否故障", "还在", "严重", "怎么处理", "如何处理", "解决", "要不要派人")
_KNOWLEDGE_KEYWORDS = ("是什么意思", "含义", "定义", "手册", "sop", "SOP", "步骤", "怎么操作", "如何更换", "校准")
_FAULT_DIAGNOSIS_KEYWORDS = ("为什么", "原因", "诊断", "异常", "故障", "停机", "高温", "过热", "过载")
_STATUS_KEYWORDS = ("现在", "当前", "多少", "是否在线", "状态", "运行情况", "在线", "离线")
_PERMISSION_SCOPE_KEYWORDS = (
    "身份",
    "权限",
    "访问",
    "可访问",
    "能访问",
    "能看",
    "可以看",
    "账号",
    "角色",
    "哪些设备",
    "哪些数据",
    "生成报告吗",
    "能生成报告",
)
_RESOLUTION_KEYWORDS = ("怎么处理", "如何处理", "解决", "处置", "建议", "排查", "维修")
_CURRENT_STATUS_KEYWORDS = ("现在", "当前", "还在", "是否", "状态", "在线", "active")
_SEVERITY_KEYWORDS = ("严重", "严不严重", "风险", "影响", "危险", "要不要停机", "要不要派人")

_METRIC_KEYWORDS = {
    "温度": ("温度", "高温", "过热"),
    "振动": ("振动",),
    "电流": ("电流",),
    "转速": ("转速", "速度"),
    "负载": ("负载", "过载"),
    "压力": ("压力",),
}


def route_task(
    *,
    payload: dict[str, Any],
    message: str,
    report_from_previous_artifact: bool = False,
    resolved_context: Any | None = None,
) -> TaskRoute:
    """Build a structured route from rule-based and model understanding payloads."""

    normalized = (message or payload.get("user_message") or "").strip()
    resolved_context_payload = _resolved_context_payload(resolved_context)
    effective_report_from_previous_artifact = report_from_previous_artifact or (
        resolved_context_payload.get("relation_to_previous") == "report_handoff"
    )
    objects = _extract_objects(payload, normalized)
    task_type, confidence = _classify_task(normalized, objects, effective_report_from_previous_artifact)
    requested_output = _requested_output(task_type, normalized)
    time_window = _time_window(task_type, payload, normalized)
    missing_slots = _missing_slots(task_type, objects, time_window, normalized)
    risk_level = _risk_level(task_type, normalized)
    legacy_intent_candidates = _intent_stack(
        task_type,
        normalized,
        objects,
        requested_output,
        effective_report_from_previous_artifact,
    )
    goal_set = build_goal_set(
        message=normalized,
        payload=payload,
        resolved_context=resolved_context_payload,
        route_hint={
            "task_type": task_type,
            "requested_output": requested_output,
            "objects": objects,
            "missing_slots": missing_slots,
            "risk_level": risk_level,
            "legacy_intent_candidates": legacy_intent_candidates,
        },
    )
    intent_stack = _dedupe([*goal_set.intent_stack_projection, *legacy_intent_candidates])
    candidate_task_types = _candidate_task_types(task_type, intent_stack)
    flags = _flags_for_task(task_type, normalized, objects, requested_output, effective_report_from_previous_artifact)
    projection_mismatch = set(goal_set.intent_stack_projection) != set(legacy_intent_candidates)
    if projection_mismatch:
        flags["goal_projection_mismatch"] = True
        goal_set = _with_projection_mismatch_summary(goal_set, legacy_intent_candidates)
    _apply_intent_flags(flags, intent_stack, objects)
    subgoals = _subgoals(task_type, objects, flags, missing_slots)
    action_type = _action_type(normalized) if task_type == TaskType.ACTION_REQUEST else None
    task_family = resolve_task_family(
        task_type=task_type,
        requested_output=requested_output,
        goals=list(goal_set.goals),
        resolved_context=resolved_context_payload,
        intent_stack=intent_stack,
    )

    return TaskRoute(
        primary_task_type=task_type,
        task_family=task_family.task_family,
        task_family_reason=task_family.reason,
        task_family_source=task_family.source,
        task_family_warnings=task_family.warnings,
        candidate_task_types=candidate_task_types,
        intent_stack=intent_stack,
        goals=list(goal_set.goals),
        goal_set=goal_set.model_dump(exclude_none=True),
        goal_summary=goal_set.goal_summary,
        resolved_context=resolved_context_payload,
        context_resolution=dict(payload.get("context_resolution") or {}),
        relation_to_previous=str(resolved_context_payload.get("relation_to_previous") or "new_task"),
        evidence_mode=str(resolved_context_payload.get("evidence_mode") or "collect_new"),
        referenced_artifact_id=resolved_context_payload.get("referenced_artifact_id"),
        referenced_case_id=resolved_context_payload.get("referenced_case_id")
        or resolved_context_payload.get("active_case_id"),
        should_refresh_runtime_data=bool(resolved_context_payload.get("should_refresh_runtime_data")),
        action_target=_action_target(intent_stack),
        route_confidence=confidence,
        user_goal=str(payload.get("analysis_goal") or normalized or task_type.value),
        objects=objects,
        time_window=time_window,
        subgoals=subgoals,
        missing_slots=missing_slots,
        risk_level=risk_level,
        requested_output=requested_output,
        flags=flags,
        action_type=action_type,
    )


def _resolved_context_payload(resolved_context: Any | None) -> dict[str, Any]:
    if resolved_context is None:
        return {}
    if hasattr(resolved_context, "model_dump"):
        return resolved_context.model_dump(exclude_none=True)
    return dict(resolved_context or {}) if isinstance(resolved_context, dict) else {}


def _with_projection_mismatch_summary(goal_set: GoalSet, legacy_intents: list[str]) -> GoalSet:
    data = goal_set.model_dump(exclude_none=True)
    projected = ", ".join(goal_set.intent_stack_projection) or "none"
    legacy = ", ".join(legacy_intents) or "none"
    suffix = f"projection differs from legacy intents: projected=[{projected}], legacy=[{legacy}]"
    summary = str(data.get("goal_summary") or "").strip()
    data["goal_summary"] = f"{summary}；{suffix}" if summary else suffix
    return GoalSet.model_validate(data)


def _classify_task(
    text: str,
    objects: WorkflowObjects,
    report_from_previous_artifact: bool,
) -> tuple[TaskType, float]:
    compact = text.replace(" ", "").lower()
    if report_from_previous_artifact:
        return TaskType.REPORT_GENERATION, 0.95
    if _has_any(compact, _PERMISSION_SCOPE_KEYWORDS):
        return TaskType.PERMISSION_SCOPE_QUERY, 0.9
    if _has_any(compact, _ACTION_KEYWORDS):
        return TaskType.ACTION_REQUEST, 0.92
    if _has_any(compact, _REPORT_KEYWORDS):
        return TaskType.REPORT_GENERATION, 0.9
    if _has_any(compact, _RCA_KEYWORDS):
        return TaskType.ROOT_CAUSE_ANALYSIS, 0.9
    if _has_any(compact, _HEALTH_KEYWORDS):
        return TaskType.HEALTH_ASSESSMENT, 0.86
    has_alarm = bool(objects.alarm_codes) or _has_any(compact, _ALARM_KEYWORDS)
    if _has_any(compact, _WORKORDER_DECISION_KEYWORDS):
        return TaskType.FAULT_DIAGNOSIS, 0.72
    if has_alarm and _has_any(compact, _TRIAGE_KEYWORDS):
        return TaskType.ALARM_TRIAGE, 0.88
    if _has_any(compact, _SEVERITY_KEYWORDS):
        return TaskType.HEALTH_ASSESSMENT, 0.74
    if _has_any(compact, _KNOWLEDGE_KEYWORDS) and not _has_any(compact, _CURRENT_STATUS_KEYWORDS):
        return TaskType.KNOWLEDGE_QA, 0.84
    if has_alarm and not objects.device_ids:
        return TaskType.KNOWLEDGE_QA, 0.8
    if _has_any(compact, _FAULT_DIAGNOSIS_KEYWORDS):
        return TaskType.FAULT_DIAGNOSIS, 0.82
    if _has_any(compact, _STATUS_KEYWORDS):
        return TaskType.STATUS_QUERY, 0.78
    return TaskType.STATUS_QUERY, 0.58


def _intent_stack(
    task_type: TaskType,
    text: str,
    objects: WorkflowObjects,
    requested_output: str,
    report_from_previous_artifact: bool,
) -> list[str]:
    compact = text.replace(" ", "").lower()
    if report_from_previous_artifact:
        return ["report_generation"]
    intents: list[str] = []
    has_alarm = bool(objects.alarm_codes) or _has_any(compact, _ALARM_KEYWORDS)
    if task_type == TaskType.ACTION_REQUEST:
        intents.append("action_request")
    if _has_any(compact, _DISPATCH_WORKORDER_KEYWORDS):
        intents.append("dispatch_workorder")
    elif _has_any(compact, _CREATE_WORKORDER_DRAFT_KEYWORDS):
        intents.append("create_workorder_draft")
    elif _has_any(compact, _WORKORDER_DECISION_KEYWORDS):
        intents.append("workorder_decision")
    if requested_output == "report" or report_from_previous_artifact:
        intents.append("report_generation")
    if task_type == TaskType.PERMISSION_SCOPE_QUERY:
        intents.append("permission_scope_query")
    if has_alarm and (_has_any(compact, _KNOWLEDGE_KEYWORDS + _ALARM_KEYWORDS) or objects.alarm_codes):
        intents.append("explain_alarm_code")
    if _has_any(compact, _CURRENT_STATUS_KEYWORDS) or task_type == TaskType.STATUS_QUERY:
        intents.append("check_current_status")
    if _has_any(compact, ("影响", "范围", "后果")):
        intents.append("fault_impact")
    if _has_any(compact, _SEVERITY_KEYWORDS):
        intents.append("severity_assessment")
    if _has_any(compact, _RESOLUTION_KEYWORDS):
        intents.append("resolution_recommendation")
    if (
        task_type in {TaskType.FAULT_DIAGNOSIS, TaskType.ROOT_CAUSE_ANALYSIS}
        or _has_any(compact, _FAULT_DIAGNOSIS_KEYWORDS)
    ):
        intents.append("fault_diagnosis")
    if not intents:
        intents.append("check_current_status")
    return _dedupe(intents)


def _candidate_task_types(primary: TaskType, intent_stack: list[str]) -> list[TaskType]:
    candidates = [primary]
    intent_map: dict[str, list[TaskType]] = {
        "explain_alarm_code": [TaskType.KNOWLEDGE_QA, TaskType.ALARM_TRIAGE],
        "check_current_status": [TaskType.STATUS_QUERY],
        "fault_impact": [TaskType.FAULT_DIAGNOSIS, TaskType.HEALTH_ASSESSMENT],
        "severity_assessment": [TaskType.HEALTH_ASSESSMENT, TaskType.ALARM_TRIAGE],
        "resolution_recommendation": [TaskType.KNOWLEDGE_QA, TaskType.FAULT_DIAGNOSIS],
        "report_generation": [TaskType.REPORT_GENERATION],
        "action_request": [TaskType.ACTION_REQUEST],
        "workorder_decision": [TaskType.FAULT_DIAGNOSIS],
        "create_workorder_draft": [TaskType.ACTION_REQUEST],
        "dispatch_workorder": [TaskType.ACTION_REQUEST],
        "fault_diagnosis": [TaskType.FAULT_DIAGNOSIS],
        "permission_scope_query": [TaskType.PERMISSION_SCOPE_QUERY],
    }
    for intent in intent_stack:
        candidates.extend(intent_map.get(intent, []))
    return list(dict.fromkeys(candidates))


def _apply_intent_flags(flags: dict[str, bool], intent_stack: list[str], objects: WorkflowObjects) -> None:
    intents = set(intent_stack)
    if "explain_alarm_code" in intents:
        flags["need_knowledge"] = True
    if "check_current_status" in intents and objects.device_ids:
        flags["need_sql"] = True
    if intents.intersection({"fault_impact", "severity_assessment"}):
        flags["need_analysis"] = True
        flags["need_knowledge"] = True
        if objects.device_ids:
            flags["need_sql"] = True
    if "resolution_recommendation" in intents:
        flags["need_knowledge"] = True
        flags["need_analysis"] = True
        flags["need_resolution"] = True
    if "report_generation" in intents:
        flags["need_report"] = True
    if "workorder_decision" in intents:
        flags["need_workorder_decision"] = True
    if "create_workorder_draft" in intents:
        flags.update(
            need_workorder_decision=True,
            need_permission_check=True,
            need_risk_check=True,
            may_involve_write_action=True,
        )
    if "dispatch_workorder" in intents:
        flags.update(
            need_workorder_decision=True,
            need_permission_check=True,
            need_risk_check=True,
            may_involve_write_action=True,
        )
    if "action_request" in intents:
        flags.update(
            need_sql=True,
            need_knowledge=True,
            need_analysis=True,
            need_resolution=True,
            may_involve_write_action=True,
        )
    if "permission_scope_query" in intents:
        flags.update(
            need_sql=False,
            need_knowledge=False,
            need_analysis=True,
            need_resolution=False,
            need_workorder_decision=False,
            need_report=False,
        )
    if len(intents) > 1:
        flags["safe_union_workflow"] = True


def _extract_objects(payload: dict[str, Any], text: str) -> WorkflowObjects:
    device_ids = _dedupe(
        [
            str(payload.get("equipment_hint") or "").strip(),
            *[match.group(1).strip() for match in _DEVICE_RE.finditer(text or "")],
        ]
    )
    alarm_codes = _dedupe(
        [
            str(payload.get("fault_code_hint") or "").strip().upper(),
            *[
                match.group(1).strip().upper()
                for match in _ALARM_CODE_RE.finditer(text or "")
                if not _looks_like_model_code(match.group(1))
            ],
        ]
    )
    metrics = [
        metric
        for metric, keywords in _METRIC_KEYWORDS.items()
        if any(keyword in text for keyword in keywords)
    ]
    topics: list[str] = []
    if payload.get("metric_hint"):
        topics.append(str(payload["metric_hint"]))
    if payload.get("analysis_goal"):
        topics.append(str(payload["analysis_goal"]))
    return WorkflowObjects(
        device_ids=device_ids,
        alarm_codes=alarm_codes,
        metrics=_dedupe(metrics),
        topics=_dedupe(topics)[:3],
    )


def _time_window(task_type: TaskType, payload: dict[str, Any], text: str) -> WorkflowTimeWindow:
    time_hint = str(payload.get("time_range_hint") or "").strip()
    if time_hint:
        return WorkflowTimeWindow(is_inferred=False, default_strategy=time_hint)
    if any(keyword in text for keyword in ("昨天", "上周", "过去", "最近", "历史")):
        strategy = "last_2h"
        if task_type == TaskType.HEALTH_ASSESSMENT:
            strategy = "last_7d"
        elif task_type == TaskType.ROOT_CAUSE_ANALYSIS:
            strategy = "event_window"
        return WorkflowTimeWindow(is_inferred=True, default_strategy=strategy)
    defaults = {
        TaskType.STATUS_QUERY: "current_status",
        TaskType.ALARM_TRIAGE: "current_status",
        TaskType.FAULT_DIAGNOSIS: "last_2h",
        TaskType.ROOT_CAUSE_ANALYSIS: "event_window_required",
        TaskType.HEALTH_ASSESSMENT: "last_7d",
        TaskType.KNOWLEDGE_QA: "static_reference",
        TaskType.REPORT_GENERATION: "existing_evidence_or_current",
        TaskType.ACTION_REQUEST: "current_status",
        TaskType.PERMISSION_SCOPE_QUERY: "none",
    }
    return WorkflowTimeWindow(is_inferred=True, default_strategy=defaults[task_type])


def _flags_for_task(
    task_type: TaskType,
    text: str,
    objects: WorkflowObjects,
    requested_output: str,
    report_from_previous_artifact: bool,
) -> dict[str, bool]:
    asks_resolution = _has_any(text, _RESOLUTION_KEYWORDS)
    asks_workorder = _has_any(text, _WORKORDER_KEYWORDS)
    asks_current = _has_any(text, _CURRENT_STATUS_KEYWORDS)
    flags = {
        "need_sql": False,
        "need_knowledge": False,
        "need_analysis": True,
        "need_resolution": asks_resolution,
        "need_workorder_decision": asks_workorder,
        "need_report": requested_output == "report",
        "may_involve_write_action": task_type == TaskType.ACTION_REQUEST,
    }
    if task_type == TaskType.STATUS_QUERY:
        flags.update(need_sql=True, need_knowledge=False, need_workorder_decision=asks_workorder)
    elif task_type == TaskType.ALARM_TRIAGE:
        flags.update(
            need_sql=asks_current or bool(objects.device_ids),
            need_knowledge=True,
            need_resolution=True,
            need_workorder_decision=True,
        )
    elif task_type in {TaskType.FAULT_DIAGNOSIS, TaskType.ROOT_CAUSE_ANALYSIS}:
        flags.update(need_sql=True, need_knowledge=True, need_resolution=True, need_workorder_decision=True)
    elif task_type == TaskType.HEALTH_ASSESSMENT:
        flags.update(need_sql=True, need_knowledge=asks_resolution, need_resolution=True, need_workorder_decision=True)
    elif task_type == TaskType.KNOWLEDGE_QA:
        flags.update(
            need_sql=bool(objects.device_ids) and asks_current,
            need_knowledge=True,
            need_resolution=asks_resolution,
            need_workorder_decision=False,
        )
    elif task_type == TaskType.REPORT_GENERATION:
        flags.update(
            need_sql=not report_from_previous_artifact
            and (
                bool(objects.device_ids)
                or _has_any(text, ("最新", "今天", "日报", "周报", "当前", "运行", "运行情况", "运行状态", "状态"))
            ),
            need_knowledge=not report_from_previous_artifact and _has_any(text, ("模板", "SOP", "sop", "RCA", "rca")),
            need_report=True,
            need_workorder_decision=False,
        )
    elif task_type == TaskType.ACTION_REQUEST:
        is_workorder_only = _has_any(text, _CREATE_WORKORDER_DRAFT_KEYWORDS + _DISPATCH_WORKORDER_KEYWORDS)
        flags.update(
            need_sql=not is_workorder_only,
            need_knowledge=not is_workorder_only,
            need_resolution=True,
            need_workorder_decision=asks_workorder,
        )
    elif task_type == TaskType.PERMISSION_SCOPE_QUERY:
        flags.update(need_sql=False, need_knowledge=False, need_analysis=True, need_workorder_decision=False, need_report=False)
    return flags


def _subgoals(
    task_type: TaskType,
    objects: WorkflowObjects,
    flags: dict[str, bool],
    missing_slots: list[str],
) -> list[WorkflowSubgoal]:
    builders = {
        TaskType.STATUS_QUERY: [
            ("check_current_status", True, []),
            ("summarize_recent_alarms", False, []),
            ("workorder_decision", False, ["current_abnormal_status"] if flags.get("need_workorder_decision") else []),
        ],
        TaskType.ALARM_TRIAGE: [
            ("explain_alarm_code", True, [] if objects.alarm_codes else ["alarm_code"]),
            ("check_current_alarm_status", True, [] if objects.device_ids else ["device_id"]),
            ("check_current_fault_status", True, [] if objects.device_ids else ["device_id"]),
            ("recommend_resolution_steps", True, []),
            ("workorder_decision", False, [] if objects.device_ids else ["device_id", "current_alarm_status"]),
        ],
        TaskType.FAULT_DIAGNOSIS: [
            ("collect_asset_context", True, [] if objects.device_ids else ["device_id_or_system"]),
            ("diagnose_fault", True, []),
            ("recommend_resolution_steps", True, []),
            ("workorder_decision", False, [] if objects.device_ids else ["device_id"]),
        ],
        TaskType.ROOT_CAUSE_ANALYSIS: [
            ("build_event_timeline", True, ["time_window"] if "time_window" in missing_slots else []),
            ("identify_direct_and_root_causes", True, []),
            ("recommend_prevention_actions", True, []),
            ("generate_rca_report", False, []),
        ],
        TaskType.HEALTH_ASSESSMENT: [
            ("assess_health_score", True, [] if objects.device_ids else ["device_id_or_group"]),
            ("detect_degradation_trend", True, []),
            ("recommend_preventive_actions", True, []),
            ("workorder_decision", False, [] if objects.device_ids else ["device_id_or_group"]),
        ],
        TaskType.KNOWLEDGE_QA: [
            ("retrieve_manual_or_sop", True, []),
            ("explain_applicability", True, []),
            ("recommend_resolution_steps", False, [] if flags.get("need_resolution") else ["resolution_not_requested"]),
        ],
        TaskType.REPORT_GENERATION: [
            ("load_or_initialize_evidence_bundle", True, []),
            ("organize_report_content", True, []),
            ("generate_report", True, []),
        ],
        TaskType.ACTION_REQUEST: [
            ("identify_action_type", True, []),
            ("permission_check", True, []),
            ("risk_check", True, []),
            ("action_decision", True, ["human_confirmation"]),
        ],
        TaskType.PERMISSION_SCOPE_QUERY: [
            ("summarize_permission_scope", True, []),
        ],
    }[task_type]
    return [
        WorkflowSubgoal(
            id=f"sg_{index:03d}",
            type=goal_type,
            required=required,
            status="blocked" if missing else "ready",
            missing_slots=missing,
        )
        for index, (goal_type, required, missing) in enumerate(builders, start=1)
    ]


def _missing_slots(
    task_type: TaskType,
    objects: WorkflowObjects,
    time_window: WorkflowTimeWindow,
    text: str,
) -> list[str]:
    missing: list[str] = []
    if task_type in {TaskType.STATUS_QUERY, TaskType.FAULT_DIAGNOSIS, TaskType.HEALTH_ASSESSMENT} and not (
        objects.device_ids or objects.system or objects.location
    ):
        missing.append("device_id_or_system")
    if task_type == TaskType.ALARM_TRIAGE and not objects.alarm_codes:
        missing.append("alarm_code")
    if task_type == TaskType.ROOT_CAUSE_ANALYSIS and time_window.default_strategy == "event_window_required":
        missing.append("time_window")
    if task_type == TaskType.ACTION_REQUEST and not _action_type(text):
        missing.append("action_type")
    return missing


def _requested_output(task_type: TaskType, text: str) -> str:
    if task_type == TaskType.REPORT_GENERATION or _has_any(text, _REPORT_KEYWORDS):
        return "report"
    if task_type == TaskType.ACTION_REQUEST:
        return "action_confirmation"
    if task_type == TaskType.PERMISSION_SCOPE_QUERY:
        return "permission_scope"
    return "answer"


def _risk_level(task_type: TaskType, text: str) -> str:
    if task_type != TaskType.ACTION_REQUEST:
        return "read_only"
    if _has_any(text, ("重启", "停机", "修改", "改成", "关闭告警", "屏蔽告警", "下发", "派发")):
        return "high_risk"
    return "requires_confirmation"


def _action_type(text: str) -> str | None:
    compact = text.replace(" ", "")
    mapping = [
        ("restart_device", ("重启",)),
        ("stop_device", ("停机", "关机")),
        ("update_config", ("修改", "改成", "阈值", "参数")),
        ("acknowledge_alarm", ("确认告警",)),
        ("close_alarm", ("关闭告警", "屏蔽告警")),
        ("create_workorder", ("创建工单", "生成工单")),
        ("dispatch_workorder", ("派发工单", "派单", "直接派发")),
    ]
    for action_type, keywords in mapping:
        if _has_any(compact, keywords):
            return action_type
    return "other_write_action" if _has_any(compact, _ACTION_KEYWORDS) else None


def _action_target(intent_stack: list[str]) -> str | None:
    if any(intent in intent_stack for intent in ("workorder_decision", "create_workorder_draft", "dispatch_workorder")):
        return "workorder"
    if "action_request" in intent_stack:
        return "device_or_configuration"
    return None


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword.lower() in text.lower() for keyword in keywords if keyword)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for item in values if (value := str(item or "").strip())))


def _looks_like_model_code(value: str) -> bool:
    return str(value or "").strip().upper() in {"G120", "S120", "V20", "G130", "G150"}
