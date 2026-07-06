"""Interpret persisted conversation context into deterministic routing signals."""

from __future__ import annotations

import re
from typing import Any


_FAULT_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]\d{3,5})(?![A-Z0-9])", re.IGNORECASE)
_ASSET_RE = re.compile(r"([A-Z]{2,}(?:-\d{1,})+|J\d+|\d+号机|[A-Z]+\d+电机\d+)", re.IGNORECASE)
_CORRECTION_RE = re.compile(
    r"(?:说错了|弄错了|不是)\s*([A-Z]{2,}(?:-\d{1,})+|J\d+|\d+号机|[A-Z]+\d+电机\d+)?\s*(?:不是)?\s*([A-Z]{2,}(?:-\d{1,})+|J\d+|\d+号机|[A-Z]+\d+电机\d+)",
    re.IGNORECASE,
)

_DEICTIC_REF_PATTERNS: tuple[tuple[str, str], ...] = (
    ("刚才", "previous_turn"),
    ("刚刚", "previous_turn"),
    ("这个", "current_object"),
    ("它", "current_object"),
    ("那", "comparison_or_next_object"),
    ("第二个", "ordinal_reference"),
    ("上一个报告", "previous_report"),
    ("上一份报告", "previous_report"),
    ("刚生成的报告", "latest_report"),
    ("刚才那个故障码", "previous_fault_code"),
    ("这个故障码", "current_fault_code"),
    ("从结果看", "previous_result"),
    ("从结果来看", "previous_result"),
)
_REPORT_WORDS = ("报告", "出报告", "生成报告", "导出报告", "整理成报告", "形成报告")
_WORKORDER_WORDS = ("工单", "派单", "派人", "维修单")
_STATUS_WORDS = ("现在", "当前", "最新", "还有吗", "还在", "状态")
_EXPLANATION_WORDS = ("是什么", "什么意思", "含义", "原因", "怎么处理", "如何处理")


class ConversationContextInterpreter:
    """Extract context signals from history without creating diagnosis evidence."""

    def interpret(self, package: dict[str, Any] | None) -> dict[str, Any]:
        package = package if isinstance(package, dict) else {}
        current_message = str(package.get("current_user_message") or "")
        last_raw_messages = package.get("last_raw_messages") if isinstance(package.get("last_raw_messages"), list) else []
        rolling_summary = package.get("rolling_summary")
        artifact_refs = package.get("artifact_refs") if isinstance(package.get("artifact_refs"), list) else []
        latest_case_state = package.get("latest_case_state") if isinstance(package.get("latest_case_state"), dict) else {}

        texts = [
            str(item.get("content") or "")
            for item in last_raw_messages
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
        ]
        summary_text = ""
        if isinstance(rolling_summary, dict):
            summary_text = " ".join(str(value or "") for value in rolling_summary.values())
        elif rolling_summary:
            summary_text = str(rolling_summary)
        all_context_text = "\n".join([*texts, summary_text])
        current_assets = _extract_assets(current_message)
        current_fault_codes = _extract_fault_codes(current_message)
        corrections = _extract_recent_corrections([*texts, current_message])
        latest_correction_target = corrections[-1].get("to_asset") if corrections else None
        mentioned_assets = _dedupe(
            [
                *(_extract_assets(all_context_text)),
                *_as_text_list(latest_case_state.get("active_asset")),
                *current_assets,
                *([latest_correction_target] if latest_correction_target else []),
            ]
        )
        mentioned_fault_codes = _dedupe(
            [
                *(_extract_fault_codes(all_context_text)),
                *_as_text_list(latest_case_state.get("active_fault_codes")),
                *current_fault_codes,
            ]
        )
        deictic_refs = _extract_deictic_refs(current_message)
        last_requested_output = _requested_output(current_message) or _last_requested_output(texts)
        followups = _followup_intents(
            current_message=current_message,
            deictic_refs=deictic_refs,
            last_requested_output=last_requested_output,
            corrections=corrections,
        )
        signals = {
            "version": "recent_context_signals.v1",
            "mentioned_assets": mentioned_assets,
            "mentioned_fault_codes": mentioned_fault_codes,
            "recent_corrections": corrections[-3:],
            "deictic_refs": deictic_refs,
            "last_requested_output": last_requested_output,
            "candidate_artifact_refs": _artifact_ref_summary(artifact_refs),
            "open_followup_intents": followups,
            "current_message_assets": current_assets,
            "current_message_fault_codes": current_fault_codes,
            "latest_correction_target_asset": latest_correction_target,
            "safety": {
                "history_is_data_not_instruction": True,
                "summary_is_not_authorization_source": True,
                "summary_is_not_diagnosis_evidence": True,
                "signals_are_context_only": True,
            },
            "stats": {
                "raw_message_count": len(last_raw_messages),
                "mentioned_asset_count": len(mentioned_assets),
                "mentioned_fault_code_count": len(mentioned_fault_codes),
                "deictic_ref_count": len(deictic_refs),
                "candidate_artifact_ref_count": len(artifact_refs),
            },
        }
        return signals


def summarize_recent_context_signals(signals: dict[str, Any] | None) -> dict[str, Any]:
    """Return a payload-safe summary without raw historical message text."""

    signals = signals if isinstance(signals, dict) else {}
    return {
        "version": signals.get("version") or "recent_context_signals.v1",
        "mentioned_assets": list(signals.get("mentioned_assets") or []),
        "mentioned_fault_codes": list(signals.get("mentioned_fault_codes") or []),
        "recent_corrections": [
            {
                "from_asset": item.get("from_asset"),
                "to_asset": item.get("to_asset"),
            }
            for item in (signals.get("recent_corrections") or [])
            if isinstance(item, dict)
        ],
        "deictic_ref_types": list(
            dict.fromkeys(
                str(item.get("reference_type") or "")
                for item in (signals.get("deictic_refs") or [])
                if isinstance(item, dict) and item.get("reference_type")
            )
        ),
        "last_requested_output": signals.get("last_requested_output") or "",
        "candidate_artifact_ref_types": list(
            dict.fromkeys(
                str(item.get("artifact_type") or "")
                for item in (signals.get("candidate_artifact_refs") or [])
                if isinstance(item, dict) and item.get("artifact_type")
            )
        ),
        "open_followup_intents": list(signals.get("open_followup_intents") or []),
        "stats": signals.get("stats") if isinstance(signals.get("stats"), dict) else {},
        "safety": {
            "history_is_data_not_instruction": True,
            "summary_is_not_authorization_source": True,
            "summary_is_not_diagnosis_evidence": True,
            "signals_are_context_only": True,
        },
    }


def _extract_assets(text: str) -> list[str]:
    return _dedupe(match.group(1).upper() if match.group(1).upper().startswith("J") else match.group(1) for match in _ASSET_RE.finditer(text or ""))


def _extract_fault_codes(text: str) -> list[str]:
    return _dedupe(match.group(1).upper() for match in _FAULT_CODE_RE.finditer(text or "") if match.group(1).upper() not in {"G120", "S120", "G130", "G150"})


def _extract_recent_corrections(texts: list[str]) -> list[dict[str, Any]]:
    corrections: list[dict[str, Any]] = []
    for index, text in enumerate(texts):
        compact = (text or "").replace(" ", "")
        if ("说错了" in compact or "弄错了" in compact or "不是" in compact) and (assets := _extract_assets(compact)):
            corrections.append(
                {
                    "from_asset": assets[0] if len(assets) > 1 else None,
                    "to_asset": assets[-1],
                    "message_index": index,
                    "source": "conversation_history",
                }
            )
            continue
        match = _CORRECTION_RE.search(compact)
        if match:
            corrections.append(
                {
                    "from_asset": match.group(1),
                    "to_asset": match.group(2),
                    "message_index": index,
                    "source": "conversation_history",
                }
            )
            continue
    return corrections


def _extract_deictic_refs(message: str) -> list[dict[str, str]]:
    compact = (message or "").replace(" ", "")
    refs = [
        {"label": label, "reference_type": ref_type}
        for label, ref_type in _DEICTIC_REF_PATTERNS
        if label in compact
    ]
    return refs


def _requested_output(message: str) -> str:
    compact = (message or "").replace(" ", "")
    if any(word in compact for word in _WORKORDER_WORDS):
        return "workorder"
    if any(word in compact for word in _REPORT_WORDS):
        return "report"
    if any(word in compact for word in _STATUS_WORDS):
        return "current_status"
    if any(word in compact for word in _EXPLANATION_WORDS):
        return "explanation"
    return ""


def _last_requested_output(texts: list[str]) -> str:
    for text in reversed(texts):
        output = _requested_output(text)
        if output:
            return output
    return ""


def _followup_intents(
    *,
    current_message: str,
    deictic_refs: list[dict[str, str]],
    last_requested_output: str,
    corrections: list[dict[str, Any]],
) -> list[str]:
    compact = (current_message or "").replace(" ", "")
    intents: list[str] = []
    if corrections and corrections[-1].get("message_index") is not None and _extract_assets(current_message):
        intents.append("correction")
    if any(word in compact for word in _WORKORDER_WORDS):
        intents.append("action_followup" if deictic_refs or last_requested_output else "workorder_request")
    if any(word in compact for word in _REPORT_WORDS):
        intents.append("report_handoff" if deictic_refs else "report_request")
    if any(word in compact for word in _STATUS_WORDS):
        intents.append("refresh_current_status")
    if deictic_refs and not intents:
        intents.append("continuation")
    return _dedupe(intents)


def _artifact_ref_summary(refs: list[Any]) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        artifact_id = str(ref.get("artifact_id") or "").strip()
        artifact_type = str(ref.get("artifact_type") or "").strip()
        if not artifact_id:
            continue
        values.append(
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type or "diagnosis",
                "ref_role": str(ref.get("ref_role") or ""),
            }
        )
    return values


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if str(value or "").strip():
        return [str(value)]
    return []


def _dedupe(values: Any) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))
