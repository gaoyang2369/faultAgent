"""Rule-first task routing and subgoal decomposition."""

from __future__ import annotations

import re
from typing import Any

from .axes import goal_types
from .contracts import TaskRoute, WorkflowObjects, WorkflowSubgoal, WorkflowTimeWindow
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
    task_key, confidence = _classify_task(normalized, objects, effective_report_from_previous_artifact)
    requested_output = _requested_output(task_key, normalized)
    time_window = _time_window(task_key, payload, normalized)
    missing_slots = _missing_slots(task_key, objects, time_window, normalized)
    risk_level = _risk_level(task_key, normalized)
    legacy_candidates = _legacy_goal_hints(
        task_key,
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
            "task_type": task_key,
            "requested_output": requested_output,
            "objects": objects,
            "missing_slots": missing_slots,
            "risk_level": risk_level,
            "legacy_candidates": legacy_candidates,
        },
    )
    current_goal_types = goal_types(goal_set)
    flags = _flags_for_task(task_key, normalized, objects, requested_output, effective_report_from_previous_artifact)
    _apply_goal_flags(flags, current_goal_types, objects)
    if bool(resolved_context_payload.get("should_refresh_runtime_data")):
        flags["need_sql"] = True
    subgoals = _subgoals(current_goal_types, objects, flags, missing_slots)
    action_type = _action_type(normalized) if task_key == "action_request" else None
    action_target = _action_target_from_goals(current_goal_types, action_type)
    task_family = resolve_task_family(
        requested_output=requested_output,
        goals=list(goal_set.goals),
        resolved_context=resolved_context_payload,
        action_target=action_target,
        action_type=action_type,
    )

    return TaskRoute(
        task_family=task_family.task_family,
        task_family_reason=task_family.reason,
        task_family_source=task_family.source,
        task_family_warnings=task_family.warnings,
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
        action_target=action_target,
        route_confidence=confidence,
        user_goal=str(payload.get("analysis_goal") or normalized or task_key),
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


def _classify_task(
    text: str,
    objects: WorkflowObjects,
    report_from_previous_artifact: bool,
) -> tuple[str, float]:
    compact = text.replace(" ", "").lower()
    if report_from_previous_artifact:
        return "report_generation", 0.95
    if _has_any(compact, _PERMISSION_SCOPE_KEYWORDS):
        return "permission_scope_query", 0.9
    if _has_any(compact, _ACTION_KEYWORDS):
        return "action_request", 0.92
    if _has_any(compact, _REPORT_KEYWORDS):
        return "report_generation", 0.9
    if _has_any(compact, _RCA_KEYWORDS):
        return "root_cause_analysis", 0.9
    if _has_any(compact, _HEALTH_KEYWORDS):
        return "health_assessment", 0.86
    has_alarm = bool(objects.alarm_codes) or _has_any(compact, _ALARM_KEYWORDS)
    if _has_any(compact, _WORKORDER_DECISION_KEYWORDS):
        return "fault_diagnosis", 0.72
    if has_alarm and _has_any(compact, _TRIAGE_KEYWORDS):
        return "alarm_triage", 0.88
    if _has_any(compact, _SEVERITY_KEYWORDS):
        return "health_assessment", 0.74
    if _has_any(compact, _KNOWLEDGE_KEYWORDS) and not _has_any(compact, _CURRENT_STATUS_KEYWORDS):
        return "knowledge_qa", 0.84
    if has_alarm and not objects.device_ids:
        return "knowledge_qa", 0.8
    if _has_any(compact, _FAULT_DIAGNOSIS_KEYWORDS):
        return "fault_diagnosis", 0.82
    if _has_any(compact, _STATUS_KEYWORDS):
        return "status_query", 0.78
    return "status_query", 0.58


def _legacy_goal_hints(
    task_key: str,
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
    if task_key == "action_request":
        intents.append("action_request")
    if _has_any(compact, _DISPATCH_WORKORDER_KEYWORDS):
        intents.append("dispatch_workorder")
    elif _has_any(compact, _CREATE_WORKORDER_DRAFT_KEYWORDS):
        intents.append("create_workorder_draft")
    elif _has_any(compact, _WORKORDER_DECISION_KEYWORDS):
        intents.append("workorder_decision")
    if requested_output == "report" or report_from_previous_artifact:
        intents.append("report_generation")
    if task_key == "permission_scope_query":
        intents.append("permission_scope_query")
    if has_alarm and (_has_any(compact, _KNOWLEDGE_KEYWORDS + _ALARM_KEYWORDS) or objects.alarm_codes):
        intents.append("explain_alarm_code")
    if _has_any(compact, _CURRENT_STATUS_KEYWORDS) or task_key == "status_query":
        intents.append("check_current_status")
    if _has_any(compact, ("影响", "范围", "后果")):
        intents.append("fault_impact")
    if _has_any(compact, _SEVERITY_KEYWORDS):
        intents.append("severity_assessment")
    if _has_any(compact, _RESOLUTION_KEYWORDS):
        intents.append("resolution_recommendation")
    if (
        task_key in {"fault_diagnosis", "root_cause_analysis"}
        or _has_any(compact, _FAULT_DIAGNOSIS_KEYWORDS)
    ):
        intents.append("fault_diagnosis")
    if not intents:
        intents.append("check_current_status")
    return _dedupe(intents)

def _apply_goal_flags(flags: dict[str, bool], goals: list[str], objects: WorkflowObjects) -> None:
    goal_set = set(goals)
    if "explain_fault_code" in goal_set:
        flags["need_knowledge"] = True
    if goal_set.intersection({"check_runtime_status", "refresh_current_status"}) and objects.device_ids:
        flags["need_sql"] = True
    if "assess_severity" in goal_set:
        flags["need_analysis"] = True
        flags["need_knowledge"] = True
        if objects.device_ids:
            flags["need_sql"] = True
    if "recommend_resolution" in goal_set:
        flags["need_knowledge"] = True
        flags["need_analysis"] = True
        flags["need_resolution"] = True
    if "generate_report" in goal_set:
        flags["need_report"] = True
    if "decide_workorder" in goal_set:
        flags["need_workorder_decision"] = True
    if goal_set.intersection({"create_workorder_draft", "dispatch_workorder"}):
        flags.update(
            need_workorder_decision=True,
            need_permission_check=True,
            need_risk_check=True,
            may_involve_write_action=True,
        )
    if "answer_meta_question" in goal_set:
        flags.update(
            need_sql=False,
            need_knowledge=False,
            need_analysis=True,
            need_resolution=False,
            need_workorder_decision=False,
            need_report=False,
        )
    if len(goal_set) > 1:
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


def _time_window(task_key: str, payload: dict[str, Any], text: str) -> WorkflowTimeWindow:
    time_hint = str(payload.get("time_range_hint") or "").strip()
    if time_hint:
        return WorkflowTimeWindow(is_inferred=False, default_strategy=time_hint)
    if any(keyword in text for keyword in ("昨天", "上周", "过去", "最近", "历史")):
        strategy = "last_2h"
        if task_key == "health_assessment":
            strategy = "last_7d"
        elif task_key == "root_cause_analysis":
            strategy = "event_window"
        return WorkflowTimeWindow(is_inferred=True, default_strategy=strategy)
    defaults = {
        "status_query": "current_status",
        "alarm_triage": "current_status",
        "fault_diagnosis": "last_2h",
        "root_cause_analysis": "event_window_required",
        "health_assessment": "last_7d",
        "knowledge_qa": "static_reference",
        "report_generation": "existing_evidence_or_current",
        "action_request": "current_status",
        "permission_scope_query": "none",
    }
    return WorkflowTimeWindow(is_inferred=True, default_strategy=defaults.get(task_key, "current_status"))


def _flags_for_task(
    task_key: str,
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
        "may_involve_write_action": task_key == "action_request",
    }
    if task_key == "status_query":
        flags.update(need_sql=True, need_knowledge=False, need_workorder_decision=asks_workorder)
    elif task_key == "alarm_triage":
        flags.update(
            need_sql=asks_current or bool(objects.device_ids),
            need_knowledge=True,
            need_resolution=True,
            need_workorder_decision=True,
        )
    elif task_key in {"fault_diagnosis", "root_cause_analysis"}:
        flags.update(need_sql=True, need_knowledge=True, need_resolution=True, need_workorder_decision=True)
    elif task_key == "health_assessment":
        flags.update(need_sql=True, need_knowledge=asks_resolution, need_resolution=True, need_workorder_decision=True)
    elif task_key == "knowledge_qa":
        flags.update(
            need_sql=bool(objects.device_ids) and asks_current,
            need_knowledge=True,
            need_resolution=asks_resolution,
            need_workorder_decision=False,
        )
    elif task_key == "report_generation":
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
    elif task_key == "action_request":
        is_workorder_only = _has_any(text, _CREATE_WORKORDER_DRAFT_KEYWORDS + _DISPATCH_WORKORDER_KEYWORDS)
        flags.update(
            need_sql=not is_workorder_only,
            need_knowledge=not is_workorder_only,
            need_resolution=True,
            need_workorder_decision=asks_workorder,
        )
    elif task_key == "permission_scope_query":
        flags.update(need_sql=False, need_knowledge=False, need_analysis=True, need_workorder_decision=False, need_report=False)
    return flags


def _subgoals(
    goals: list[str],
    objects: WorkflowObjects,
    flags: dict[str, bool],
    missing_slots: list[str],
) -> list[WorkflowSubgoal]:
    goal_set = set(goals)
    builders: list[tuple[str, bool, list[str]]] = []
    if "answer_meta_question" in goal_set:
        builders.append(("summarize_permission_scope", True, []))
    if "generate_report" in goal_set:
        builders.extend([
            ("load_or_initialize_evidence_bundle", True, []),
            ("organize_report_content", True, []),
            ("generate_report", True, []),
        ])
    if goal_set.intersection({"check_runtime_status", "refresh_current_status"}):
        builders.append(("check_current_status", True, [] if objects.device_ids else ["device_id"]))
    if "explain_fault_code" in goal_set:
        builders.append(("explain_alarm_code", True, [] if objects.alarm_codes else ["alarm_code"]))
    if "diagnose_fault" in goal_set:
        builders.append(("diagnose_fault", True, [] if objects.device_ids else ["device_id_or_system"]))
    if "assess_severity" in goal_set:
        builders.append(("assess_severity", True, [] if "device_id_or_system" not in missing_slots else ["device_id_or_system"]))
    if "recommend_resolution" in goal_set:
        builders.append(("recommend_resolution_steps", True, []))
    if goal_set.intersection({"decide_workorder", "create_workorder_draft", "dispatch_workorder"}):
        builders.extend([
            ("permission_check", True, []),
            ("risk_check", True, []),
            ("workorder_decision", True, [] if objects.device_ids else ["device_id"]),
        ])
    if not builders:
        builders.append(("check_current_status", True, [] if objects.device_ids else ["device_id"]))
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
    task_key: str,
    objects: WorkflowObjects,
    time_window: WorkflowTimeWindow,
    text: str,
) -> list[str]:
    missing: list[str] = []
    if task_key in {"status_query", "fault_diagnosis", "health_assessment"} and not (
        objects.device_ids or objects.system or objects.location
    ):
        missing.append("device_id_or_system")
    if task_key == "alarm_triage" and not objects.alarm_codes:
        missing.append("alarm_code")
    if task_key == "root_cause_analysis" and time_window.default_strategy == "event_window_required":
        missing.append("time_window")
    if task_key == "action_request" and not _action_type(text):
        missing.append("action_type")
    return missing


def _requested_output(task_key: str, text: str) -> str:
    if task_key == "report_generation" or _has_any(text, _REPORT_KEYWORDS):
        return "report"
    if task_key == "action_request":
        return "action_confirmation"
    if task_key == "permission_scope_query":
        return "permission_scope"
    return "answer"


def _risk_level(task_key: str, text: str) -> str:
    if task_key != "action_request":
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


def _action_target_from_goals(goals: list[str], action_type: str | None) -> str | None:
    goal_set = set(goals)
    if goal_set.intersection({"decide_workorder", "create_workorder_draft", "dispatch_workorder"}):
        return "workorder"
    if action_type:
        return "device_or_configuration"
    return None


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword.lower() in text.lower() for keyword in keywords if keyword)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for item in values if (value := str(item or "").strip())))


def _looks_like_model_code(value: str) -> bool:
    return str(value or "").strip().upper() in {"G120", "S120", "V20", "G130", "G150"}
