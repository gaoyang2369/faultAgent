"""Request intent frame builder for Agent Engine V2."""

from __future__ import annotations

import re
from typing import Any

from ..contracts import IntentFrame


_FAULT_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]\d{3,5})(?![A-Z0-9])", re.IGNORECASE)
_DEVICE_RE = re.compile(r"([A-Z]{2,}(?:-\d{1,})+|J\d+|\d+号机|[A-Z]+\d+电机\d+)", re.IGNORECASE)
_RECENT_WINDOW_RE = re.compile(r"近\s*(\d+)\s*(分钟|小时|天|日)")
_MODEL_CODES = {"G120", "S120", "G130", "G150", "V20"}

_EXPLAIN_WORDS = ("是什么", "什么意思", "含义", "故障码", "告警码", "报警码", "异常码", "详细", "原文", "手册字段", "完整字段")
_STATUS_WORDS = ("现在", "当前", "最新", "还故障", "还在", "状态", "运行", "看一下")
_DIAGNOSIS_WORDS = (
    "诊断", "是否有故障", "是不是有故障", "有没有故障", "是否存在故障", "判断是否存在故障",
    "是否异常", "是否存在异常", "有没有异常", "判断异常", "判断它有没有异常",
)
_RECOMMENDATION_WORDS = ("处理建议", "处置建议", "解决建议", "维修建议", "给出建议", "怎么处理")
_COMPARE_WORDS = ("比较", "对比", "差异")
_HEALTH_WORDS = ("健康状况", "健康状态", "健康评估")
_ROOT_CAUSE_WORDS = ("根因", "为什么", "原因", "故障原因", "异常原因", "分析原因", "原因分析")
_REPORT_WORDS = ("报告", "导出", "生成报告", "出报告", "整理成报告", "形成报告")
_REPORT_CONTEXT_WORDS = ("刚才", "刚刚", "上一轮", "上一条", "上一次", "前面的结果", "诊断结果", "巡检结果")
_WORKORDER_DECISION_WORDS = ("要不要工单", "要不要生成工单", "是否需要工单", "是否生成工单", "是不是要生成工单")
_WORKORDER_DRAFT_WORDS = ("生成工单草稿", "创建工单草稿", "生成工单", "创建工单")
_DISPATCH_WORDS = ("派发工单", "下发工单", "执行工单", "直接派发", "确认派发", "派单")
_DEVICE_ACTION_WORDS = ("重启", "复位", "停机", "停止设备", "修改参数", "改参数", "关闭告警", "屏蔽告警")
_DEICTIC_WORDS = ("刚才", "刚刚", "上一轮", "上一条", "上一次", "它", "这个", "那")


class IntentFrameBuilder:
    """Build an IntentFrame with deterministic rules and optional model hints."""

    def build(
        self,
        raw_message: str,
        *,
        model_result: dict[str, Any] | None = None,
        model_error: BaseException | None = None,
    ) -> IntentFrame:
        normalized = (raw_message or "").strip()
        compact = normalized.replace(" ", "")
        device_refs = _extract_devices(normalized)
        fault_code_refs = _extract_fault_codes(normalized)
        sub_intents = _infer_sub_intents(compact, device_refs=device_refs, fault_code_refs=fault_code_refs)
        requested_outputs = _infer_requested_outputs(sub_intents)
        risk_hints = _infer_risk_hints(compact, sub_intents)
        time_window = _extract_time_window(compact)
        ambiguities = _infer_ambiguities(compact, device_refs=device_refs, sub_intents=sub_intents)
        intent_candidates = [{"intent": item, "source": "rule", "confidence": 0.7} for item in sub_intents]
        entities: dict[str, Any] = {
            "devices": list(device_refs),
            "fault_codes": list(fault_code_refs),
        }
        confidence = 0.7 if sub_intents else 0.3
        model_trace: dict[str, Any] = {
            "source": "rule_fallback",
            "fallback_used": True,
        }

        if model_error is not None:
            model_trace["fallback_reason"] = f"model_error:{type(model_error).__name__}"
            model_trace["model_error"] = str(model_error)
        elif model_result is not None:
            if isinstance(model_result, dict):
                model_trace["model_result_used"] = True
                _merge_model_result(
                    model_result,
                    sub_intents=sub_intents,
                    requested_outputs=requested_outputs,
                    risk_hints=risk_hints,
                    intent_candidates=intent_candidates,
                    entities=entities,
                )
                confidence = _model_confidence(model_result, default=confidence)
            else:
                model_trace["fallback_reason"] = "invalid_model_result"

        primary_intent = _primary_intent(sub_intents)
        if not primary_intent and intent_candidates:
            primary_intent = str(intent_candidates[0].get("intent") or "")

        return IntentFrame(
            raw_message=raw_message,
            normalized_message=normalized,
            language=_detect_language(normalized),
            intent_candidates=intent_candidates,
            primary_intent=primary_intent,
            sub_intents=sub_intents,
            entities=entities,
            device_refs=device_refs,
            fault_code_refs=fault_code_refs,
            time_window=time_window,
            requested_outputs=requested_outputs,
            risk_hints=risk_hints,
            ambiguities=ambiguities,
            confidence=confidence,
            model_trace=model_trace,
        )


def _extract_devices(text: str) -> list[str]:
    values: list[str] = []
    for match in _DEVICE_RE.finditer(text or ""):
        value = match.group(1)
        normalized = value.upper() if value.upper().startswith("J") else value
        values.append(normalized)
    return _dedupe(values)


def _extract_fault_codes(text: str) -> list[str]:
    return _dedupe(
        match.group(1).upper()
        for match in _FAULT_CODE_RE.finditer(text or "")
        if match.group(1).upper() not in _MODEL_CODES
    )


def _infer_sub_intents(compact: str, *, device_refs: list[str], fault_code_refs: list[str]) -> list[str]:
    intents: list[str] = []
    if fault_code_refs and _has_any(compact, _EXPLAIN_WORDS):
        intents.append("explain_fault_code")
    if _has_any(compact, _ROOT_CAUSE_WORDS):
        intents.append("root_cause_analysis")
    elif _has_any(compact, _HEALTH_WORDS):
        intents.append("health_assessment")
    elif _has_any(compact, _DIAGNOSIS_WORDS):
        intents.append("diagnose_fault")
    if _has_any(compact, _COMPARE_WORDS) and len(device_refs) >= 2:
        intents.append("compare_runtime_status")
    elif _has_any(compact, _STATUS_WORDS):
        has_diagnosis = bool(set(intents).intersection({"diagnose_fault", "health_assessment", "root_cause_analysis"}))
        explicitly_queries_status = any(word in compact for word in ("查询", "运行状态", "最近", "状态"))
        if not has_diagnosis or explicitly_queries_status:
            intents.append("check_current_status")
    if _has_any(compact, _RECOMMENDATION_WORDS):
        intents.append("resolution_recommendation")
    if _has_any(compact, _REPORT_WORDS):
        intents.append("generate_report")
    if _has_any(compact, _WORKORDER_DECISION_WORDS):
        intents.append("decide_workorder")
    if _has_any(compact, _DISPATCH_WORDS):
        intents.append("dispatch_workorder")
    elif (_has_any(compact, _WORKORDER_DRAFT_WORDS) or ("工单" in compact and _has_any(compact, ("创建", "生成")))) and "decide_workorder" not in intents:
        intents.append("create_workorder_draft")
    if _has_any(compact, _DEVICE_ACTION_WORDS):
        intents.append("device_action_request")
    if not intents and device_refs:
        intents.append("check_current_status")
    return _dedupe(intents)


def _infer_requested_outputs(sub_intents: list[str]) -> list[str]:
    outputs: list[str] = []
    if any(item in sub_intents for item in ("explain_fault_code", "check_current_status", "diagnose_fault", "health_assessment", "root_cause_analysis", "device_action_request")):
        outputs.append("answer")
    if "generate_report" in sub_intents:
        outputs.append("report")
    if "compare_runtime_status" in sub_intents:
        outputs.append("runtime_comparison")
    if "resolution_recommendation" in sub_intents:
        outputs.append("recommendations")
    if "decide_workorder" in sub_intents:
        outputs.append("workorder_decision")
    if "create_workorder_draft" in sub_intents:
        outputs.append("workorder_draft")
    if "dispatch_workorder" in sub_intents:
        outputs.append("dispatch_boundary")
    return _dedupe(outputs or ["answer"])


def _infer_risk_hints(compact: str, sub_intents: list[str]) -> list[str]:
    hints: list[str] = []
    if "dispatch_workorder" in sub_intents:
        hints.append("workorder_dispatch_requires_external_approval")
    if "device_action_request" in sub_intents:
        hints.append("device_action_forbidden_without_manual_approval")
    if _has_any(compact, ("停机", "复位", "重启", "修改参数", "关闭告警", "屏蔽告警")):
        hints.append("high_risk_action")
    return _dedupe(hints)


def _extract_time_window(compact: str) -> dict[str, Any]:
    if not compact:
        return {}
    if "当前" in compact or "现在" in compact or "latest" in compact.lower():
        return {"type": "relative", "value": "current", "is_inferred": False}
    if "最近" in compact:
        return {"type": "relative", "value": "recent", "is_inferred": False}
    if "昨天" in compact:
        return {"type": "relative", "value": "yesterday", "is_inferred": False}
    if "今天" in compact:
        return {"type": "relative", "value": "today", "is_inferred": False}
    if match := _RECENT_WINDOW_RE.search(compact):
        return {
            "type": "relative",
            "value": f"last_{match.group(1)}_{match.group(2)}",
            "amount": int(match.group(1)),
            "unit": match.group(2),
            "is_inferred": False,
        }
    return {}


def _infer_ambiguities(compact: str, *, device_refs: list[str], sub_intents: list[str]) -> list[str]:
    if device_refs:
        return []
    if set(sub_intents).intersection({"check_current_status", "diagnose_fault", "health_assessment", "root_cause_analysis", "device_action_request"}):
        return ["missing_device"]
    if any(word in compact for word in ("它", "这个", "那")) and not any(word in compact for word in _REPORT_CONTEXT_WORDS):
        return ["deictic_reference_without_context"]
    return []


def _merge_model_result(
    model_result: dict[str, Any],
    *,
    sub_intents: list[str],
    requested_outputs: list[str],
    risk_hints: list[str],
    intent_candidates: list[dict[str, Any]],
    entities: dict[str, Any],
) -> None:
    for item in _as_list(model_result.get("sub_intents")):
        if item not in sub_intents:
            sub_intents.append(item)
    for item in _as_list(model_result.get("requested_outputs")):
        if item not in requested_outputs:
            requested_outputs.append(item)
    for item in _as_list(model_result.get("risk_hints")):
        if item not in risk_hints:
            risk_hints.append(item)
    candidates = model_result.get("intent_candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict):
                merged = dict(candidate)
                merged.setdefault("source", "model")
                intent_candidates.append(merged)
            elif str(candidate or "").strip():
                intent_candidates.append({"intent": str(candidate).strip(), "source": "model"})
    model_entities = model_result.get("entities")
    if isinstance(model_entities, dict):
        entities.update(model_entities)


def _model_confidence(model_result: dict[str, Any], *, default: float) -> float:
    try:
        value = float(model_result.get("confidence"))
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, value))


def _primary_intent(sub_intents: list[str]) -> str:
    priority = [
        "dispatch_workorder",
        "device_action_request",
        "create_workorder_draft",
        "decide_workorder",
        "generate_report",
        "resolution_recommendation",
        "compare_runtime_status",
        "root_cause_analysis",
        "health_assessment",
        "diagnose_fault",
        "check_current_status",
        "explain_fault_code",
    ]
    for item in priority:
        if item in sub_intents:
            return item
    return sub_intents[0] if sub_intents else ""


def _detect_language(text: str) -> str:
    return "zh" if re.search(r"[\u4e00-\u9fff]", text or "") else "en" if text else "unknown"


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords if keyword)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if str(value or "").strip():
        return [str(value).strip()]
    return []


def _dedupe(values: Any) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))
