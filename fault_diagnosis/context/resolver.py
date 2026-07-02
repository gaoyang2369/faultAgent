"""Deterministic context resolution for multi-turn diagnosis workflows."""

from __future__ import annotations

from typing import Any

from ..security.assets import asset_is_in_scope
from ..security.contracts import AuthContext
from .conversation_interpreter import summarize_recent_context_signals
from .contracts import CaseState, ConversationDiagnosisState, ResolvedContext

_CONTEXT_WORDS = (
    "它",
    "这个",
    "这个故障",
    "这个设备",
    "刚才",
    "刚才结果",
    "刚才报告",
    "上一轮",
    "上一次",
    "上面",
    "结果",
    "从结果来看",
    "报告里",
    "该故障",
    "该设备",
    "继续",
    "那",
    "所以",
    "是不是",
    "要不要",
)
_PERMISSION_SCOPE_WORDS = (
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
_REPORT_WORDS = ("报告", "出报告", "生成报告", "导出报告", "整理成报告", "形成报告")
_REPORT_CONTEXT_WORDS = ("刚才", "刚刚", "上一轮", "上一条", "上一次", "前面的结果", "诊断结果", "巡检结果", "结果")
_WORKORDER_WORDS = (
    "要不要生成工单",
    "是否生成工单",
    "是不是要生成工单",
    "要不要工单",
    "是否需要工单",
    "应不应该派单",
    "要不要派单",
    "是否派人处理",
    "要不要派人",
    "创建工单",
    "生成工单",
    "派人",
    "派单",
    "维修单",
    "工单",
)
_REFRESH_WORDS = (
    "现在还在报警吗",
    "当前还异常吗",
    "最新状态",
    "现在是否还故障",
    "现在还故障",
    "当前是否还故障",
    "现在还异常",
    "当前还报警",
)


class ContextResolver:
    """Resolve current-message slots against thread-local CaseState."""

    def resolve(
        self,
        *,
        thread_id: str,
        message: str,
        auth_context: AuthContext,
        current_payload: dict[str, Any],
        state: ConversationDiagnosisState | None,
        recent_context_signals: dict[str, Any] | None = None,
    ) -> ResolvedContext:
        active_case = state.active_case if state is not None else None
        signals = recent_context_signals if isinstance(recent_context_signals, dict) else {}
        signals_summary = summarize_recent_context_signals(signals)
        signal_refs = [
            str(item.get("label") or "")
            for item in (signals.get("deictic_refs") or [])
            if isinstance(item, dict)
        ]
        signal_intents = _as_list(signals.get("open_followup_intents"))
        references = _dedupe([*_context_reference_labels(message), *signal_refs])
        suppress_reuse = _has_any(message, _PERMISSION_SCOPE_WORDS)
        current_asset = str(current_payload.get("equipment_hint") or "").strip() or None
        current_fault_code = str(current_payload.get("fault_code_hint") or "").strip().upper() or None
        signal_current_assets = _as_list(signals.get("current_message_assets"))
        signal_current_fault_codes = _as_list(signals.get("current_message_fault_codes"))
        latest_correction_target = str(signals.get("latest_correction_target_asset") or "").strip() or None
        if not current_asset:
            current_asset = signal_current_assets[0] if signal_current_assets else latest_correction_target
            if current_asset:
                current_payload["equipment_hint"] = current_asset
        if not current_fault_code and signal_current_fault_codes:
            current_fault_code = signal_current_fault_codes[0].upper()
            current_payload["fault_code_hint"] = current_fault_code
        state_assets = _dedupe([case.active_asset for case in (state.cases if state else []) if case.active_asset])
        state_fault_codes = _dedupe(
            [code for case in (state.cases if state else []) for code in case.active_fault_codes]
        )
        signal_assets = _as_list(signals.get("mentioned_assets"))
        signal_fault_codes = _as_list(signals.get("mentioned_fault_codes"))
        current_assets = _dedupe([current_asset])
        current_fault_codes = _dedupe([current_fault_code])
        candidates = {
            "assets": _dedupe([*current_assets, *([] if suppress_reuse else state_assets), *signal_assets]),
            "fault_codes": _dedupe([*current_fault_codes, *([] if suppress_reuse else state_fault_codes), *signal_fault_codes]),
        }
        payload_time_window = (
            current_payload.get("time_range_hint")
            if isinstance(current_payload.get("time_range_hint"), dict)
            else {"default_strategy": current_payload.get("time_range_hint")}
            if current_payload.get("time_range_hint")
            else {}
        )
        resolved = ResolvedContext(
            relation_to_previous="new_case",
            references=references,
            candidates=candidates,
            source="current_message" if (current_asset or current_fault_code) else "none",
            resolved=bool(current_asset or current_fault_code),
            active_asset=current_asset,
            active_fault_codes=current_fault_codes,
            context_resolution_reason="当前消息提供了明确上下文。" if (current_asset or current_fault_code) else "未发现可继承上下文。",
            conversation_context_signals_summary=signals_summary,
        )

        if active_case is None or suppress_reuse:
            if suppress_reuse:
                resolved.context_resolution_reason = "权限或身份范围问题不复用上一轮诊断上下文。"
            current_payload["context_resolution"] = resolved.legacy_context_resolution()
            return resolved

        if current_asset and active_case.active_asset and current_asset != active_case.active_asset:
            resolved.relation_to_previous = "correction" if (references or latest_correction_target or "correction" in signal_intents) else "new_case"
            resolved.source = "conversation_context_signals" if latest_correction_target and not signal_current_assets else "current_message"
            resolved.resolved = True
            resolved.active_asset = current_asset
            resolved.active_fault_codes = current_fault_codes
            resolved.context_resolution_reason = "当前消息显式指定了新的设备，优先使用当前设备。"
            current_payload["context_resolution"] = resolved.legacy_context_resolution()
            return resolved

        if references and not current_asset and len(candidates["assets"]) > 1:
            resolved.relation_to_previous = "ambiguous"
            resolved.source = "ambiguous"
            resolved.resolved = False
            resolved.missing_context.append("请确认“它/这个设备”指的是哪个设备。")
            resolved.context_resolution_reason = "存在多个候选设备，无法安全继承上一轮设备。"
            current_payload["context_resolution"] = resolved.legacy_context_resolution()
            return resolved
        if references and not current_fault_code and len(candidates["fault_codes"]) > 1:
            resolved.relation_to_previous = "ambiguous"
            resolved.source = "ambiguous"
            resolved.resolved = False
            resolved.missing_context.append("请确认“这个故障/刚才那个”指的是哪个故障码。")
            resolved.context_resolution_reason = "存在多个候选故障码，无法安全继承上一轮故障。"
            current_payload["context_resolution"] = resolved.legacy_context_resolution()
            return resolved

        permission_errors = _context_permission_errors(active_case, auth_context)
        if permission_errors:
            resolved.candidates = {"assets": current_assets, "fault_codes": current_fault_codes}
            resolved.conversation_context_signals_summary = _redact_context_signal_details(signals_summary)
            resolved.missing_context.extend(permission_errors)
            resolved.context_resolution_reason = "上一轮上下文不在当前身份授权范围内，已禁止继承。"
            current_payload["context_resolution"] = resolved.legacy_context_resolution()
            return resolved

        relation = _relation_to_previous(message, references, active_case, signals)
        used_asset = False
        used_fault = False
        inherited_slots: dict[str, Any] = {}
        if not current_asset and active_case.active_asset and (_should_reuse_asset(message) or references):
            current_payload["equipment_hint"] = active_case.active_asset
            resolved.active_asset = active_case.active_asset
            inherited_slots["device"] = active_case.active_asset
            used_asset = True
        if not current_fault_code and active_case.active_fault_codes and (_should_reuse_fault_code(message) or references):
            current_payload["fault_code_hint"] = active_case.active_fault_codes[0]
            resolved.active_fault_codes = [active_case.active_fault_codes[0]]
            inherited_slots["fault_codes"] = [active_case.active_fault_codes[0]]
            used_fault = True
        if not current_payload.get("time_range_hint") and active_case.active_time_window:
            default_strategy = active_case.active_time_window.get("default_strategy")
            if default_strategy:
                current_payload["time_range_hint"] = default_strategy
                inherited_slots["time_window"] = active_case.active_time_window
        if active_case.latest_evidence_bundle_id:
            inherited_slots["evidence_bundle"] = active_case.latest_evidence_bundle_id
        if active_case.latest_report_id:
            inherited_slots["report"] = active_case.latest_report_id

        stale = active_case.evidence_freshness == "stale" or _has_stale_text(active_case)
        resolved = ResolvedContext(
            relation_to_previous=relation,
            active_case_id=active_case.case_id,
            referenced_artifact_id=active_case.latest_artifact_id,
            referenced_case_id=active_case.case_id,
            referenced_report_id=active_case.latest_report_id,
            inherited_slots=inherited_slots,
            pending_actions=list(active_case.pending_actions),
            stale_evidence=stale,
            missing_context=[],
            context_resolution_reason=_reason_for_relation(relation, stale),
            references=references,
            candidates=candidates,
            resolved=bool(inherited_slots or current_asset or current_fault_code or relation != "new_case"),
            source="conversation_state" if inherited_slots else "conversation_context_signals" if signals else "current_message",
            used_active_asset=used_asset,
            used_active_fault_codes=used_fault,
            active_asset=str(current_payload.get("equipment_hint") or "").strip() or active_case.active_asset,
            active_fault_codes=_dedupe([current_payload.get("fault_code_hint"), *active_case.active_fault_codes]),
            active_time_window=payload_time_window or active_case.active_time_window,
            last_evidence_bundle_id=active_case.latest_evidence_bundle_id,
            last_report_url=active_case.last_report_url,
            evidence_mode=_evidence_mode_for_relation(relation, stale),
            should_refresh_runtime_data=stale or relation == "refresh_current_status",
            conversation_context_signals_summary=signals_summary,
        )
        if active_case.projection_warnings:
            resolved.context_resolution_reason = " ".join(
                [resolved.context_resolution_reason, *active_case.projection_warnings]
            ).strip()
        current_payload["context_resolution"] = resolved.legacy_context_resolution()
        return resolved


def _context_permission_errors(case: CaseState, auth: AuthContext) -> list[str]:
    errors: list[str] = []
    if case.active_asset and not _asset_allowed(case.active_asset, auth):
        errors.append("上一轮设备不在当前身份授权范围，不能继承设备上下文。")
    if case.latest_report_id and not (
        auth.has_permission("data.report.read") or auth.has_permission("data.report.read_all")
    ):
        errors.append("当前身份无权继承上一轮报告。")
    if (case.active_asset or case.active_fault_codes) and not auth.table_scope:
        errors.append("当前身份未配置可查询数据表，不能继承运行数据上下文。")
    return errors


def _asset_allowed(asset: str, auth: AuthContext) -> bool:
    return auth.is_admin() or asset_is_in_scope(asset, auth.asset_scope)


def _relation_to_previous(
    message: str,
    references: list[str],
    case: CaseState,
    signals: dict[str, Any] | None = None,
) -> str:
    compact = (message or "").replace(" ", "")
    signals = signals if isinstance(signals, dict) else {}
    intents = set(_as_list(signals.get("open_followup_intents")))
    if "correction" in intents:
        return "correction"
    if "action_followup" in intents:
        return "action_followup"
    if "report_handoff" in intents:
        return "report_handoff"
    if "refresh_current_status" in intents:
        return "refresh_current_status"
    if "continuation" in intents:
        return "continuation"
    if _has_any(compact, _REPORT_WORDS) and (_has_any(compact, _REPORT_CONTEXT_WORDS) or references):
        return "report_handoff"
    if _has_any(compact, _REFRESH_WORDS):
        return "refresh_current_status"
    if _has_any(compact, _WORKORDER_WORDS):
        return "action_followup"
    if references or case.latest_artifact_id:
        return "continuation"
    return "new_case"


def _evidence_mode_for_relation(relation: str, stale: bool) -> str:
    if relation in {"report_handoff", "action_followup", "continuation"}:
        return "reuse_and_refresh_status" if stale else "reuse_previous_artifact"
    if relation == "refresh_current_status":
        return "reuse_and_refresh_status"
    return "collect_new"


def _reason_for_relation(relation: str, stale: bool) -> str:
    reason = {
        "report_handoff": "用户要求基于上一轮结果生成或导出报告。",
        "action_followup": "用户基于上一轮诊断结果询问工单或处置动作。",
        "refresh_current_status": "用户要求刷新当前/最新状态。",
        "continuation": "用户指向上一轮诊断上下文。",
        "ambiguous": "上下文引用存在歧义。",
        "new_case": "当前消息作为新的诊断上下文处理。",
        "correction": "当前消息显式修正上一轮对象。",
    }.get(relation, "已完成上下文解析。")
    if stale:
        reason = f"{reason} 上一轮证据已标记为非实时或滞后。"
    return reason


def _has_stale_text(case: CaseState) -> bool:
    text = " ".join(
        str(item or "")
        for item in [case.freshness_label, case.currentness, case.latest_sample_time, case.diagnosis_summary]
    )
    lowered = text.lower()
    return any(marker in text or marker.lower() in lowered for marker in ("已滞后", "滞后", "stale", "非实时", "不代表实时"))


def _context_reference_labels(message: str) -> list[str]:
    text = (message or "").replace(" ", "")
    return _dedupe([keyword for keyword in _CONTEXT_WORDS if keyword in text])


def _should_reuse_asset(message: str) -> bool:
    text = (message or "").replace(" ", "")
    return bool(text) and _has_any(
        text,
        ("严重", "状态", "现在", "当前", "看一下", "查一下", "故障", "报警", "告警", "报告", "工单", "派人"),
    )


def _should_reuse_fault_code(message: str) -> bool:
    text = (message or "").replace(" ", "")
    return bool(text) and _has_any(
        text,
        ("怎么处理", "如何处理", "解决", "处置", "严重", "影响", "是什么", "含义", "原因", "故障", "工单", "派人"),
    )


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords if keyword)


def _dedupe(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _dedupe(value)
    if str(value or "").strip():
        return [str(value).strip()]
    return []


def _redact_context_signal_details(summary: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(summary or {})
    redacted["mentioned_assets"] = []
    redacted["mentioned_fault_codes"] = []
    redacted["recent_corrections"] = []
    redacted["candidate_artifact_ref_types"] = []
    redacted["redacted_by_authorization"] = True
    stats = dict(redacted.get("stats") or {})
    stats["mentioned_asset_count"] = 0
    stats["mentioned_fault_code_count"] = 0
    stats["candidate_artifact_ref_count"] = 0
    redacted["stats"] = stats
    return redacted
