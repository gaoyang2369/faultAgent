"""将已授权会话状态投影为不可逆的模型语义输入。"""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.canonical_turn import CAPABILITY_SPECS, ContextCandidate
from fault_diagnosis.domain.security.assets import resolve_asset


_MAX_RECENT_TURNS = 6
_MAX_LIMITATIONS_PER_TURN = 4
_SAFE_TIME_KEYS = ("start", "end", "from", "to", "default_strategy", "label")


def project_context_semantic_input(candidates: list[ContextCandidate]) -> tuple[dict[str, Any], ...]:
    """将 ACL 已授权候选压缩成供模型筛选的不可逆摘要。"""

    return tuple({
        "ordinal": index,
        "artifact_type": item.artifact_type,
        "asset_refs": list(item.asset_refs),
        "fault_codes": list(item.fault_codes),
        "has_time_window": bool(item.time_window),
        "produced_by_immediately_previous_turn": item.produced_by_immediately_previous_turn,
        "completed": item.completed,
        "reportable": item.reportable,
        "freshness_state": item.freshness_state,
        "source_kind": item.source_kind,
        "pending_action_type": item.pending_action_type,
    } for index, item in enumerate(candidates, start=1))


def project_active_case_semantic_input(
    package: dict[str, Any] | None,
    candidates: list[ContextCandidate],
) -> dict[str, Any]:
    """投影 active case；只保留候选集中可见的资产、故障码和状态枚举。"""

    context = package or {}
    case = context.get("latest_case_state") if isinstance(context.get("latest_case_state"), dict) else {}
    if not case:
        return {}
    visible_assets = {asset for item in candidates for asset in item.asset_refs}
    visible_codes = {code for item in candidates for code in item.fault_codes}
    asset = _canonical_asset(case.get("active_asset"))
    fault_codes = _safe_texts(case.get("active_fault_codes"), allowed=visible_codes)
    pending_actions = [
        {
            "action_type": str(item.get("action_type") or "")[:64],
            "status": str(item.get("status") or "pending")[:24],
            "stale_refresh_required": bool(item.get("stale_refresh_required")),
        }
        for item in case.get("pending_actions") or []
        if isinstance(item, dict) and str(item.get("action_type") or "").strip()
    ][:4]
    result = {
        "active_asset": asset if asset in visible_assets else None,
        "active_fault_codes": fault_codes,
        "latest_result_type": str(case.get("latest_artifact_type") or "")[:64] or None,
        "time_window": _safe_time_window(case.get("active_time_window") or case.get("data_window")),
        "freshness_state": str(
            case.get("evidence_freshness") or case.get("freshness_label") or "unknown"
        )[:32],
        "reportable": bool(case.get("reportable")),
        "pending_actions": pending_actions,
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def project_recent_turns_semantic_input(
    package: dict[str, Any] | None,
    candidates: list[ContextCandidate],
) -> tuple[dict[str, Any], ...]:
    """二次净化持久化的轮次摘要，绝不读取 ``last_raw_messages.content``。"""

    context = package or {}
    visible_assets = {asset for item in candidates for asset in item.asset_refs}
    visible_codes = {code for item in candidates for code in item.fault_codes}
    projected: list[dict[str, Any]] = []
    for raw in context.get("recent_turns") or []:
        if not isinstance(raw, dict):
            continue
        goal_summaries = []
        for goal in raw.get("user_goal_summaries") or []:
            if not isinstance(goal, dict):
                continue
            capability = str(goal.get("capability") or "")
            if capability not in CAPABILITY_SPECS:
                continue
            goal_summaries.append({
                "capability": capability,
                "devices": _safe_assets(goal.get("devices"), visible_assets),
            })
        deliverables = []
        for item in raw.get("deliverables") or []:
            if not isinstance(item, dict):
                continue
            capability = str(item.get("capability") or "")
            status = str(item.get("status") or "")
            if capability in CAPABILITY_SPECS and status in {
                "completed", "partial", "failed", "blocked", "denied", "skipped",
            }:
                deliverables.append({"capability": capability, "status": status})
        summary = {
            "turn_index": _positive_or_zero(raw.get("turn_index")),
            "user_goal_summaries": goal_summaries,
            "deliverables": deliverables,
            "fault_codes": _safe_texts(raw.get("fault_codes"), allowed=visible_codes),
            "devices": _safe_assets(raw.get("devices"), visible_assets),
            "limitations": [
                _normalize_limitation(item)
                for item in raw.get("limitations") or []
                if _normalize_limitation(item)
            ][:_MAX_LIMITATIONS_PER_TURN],
        }
        if summary["turn_index"] is not None and any(
            summary[key] for key in ("user_goal_summaries", "deliverables", "fault_codes", "devices", "limitations")
        ):
            projected.append(summary)
    return tuple(projected[-_MAX_RECENT_TURNS:])


def project_pending_semantic_summary(waiting: Any) -> dict[str, Any] | None:
    """仅投影待澄清的类型和缺槽，不携带对话或内部引用。"""

    if waiting is None:
        return None
    payload = waiting.model_dump(mode="json") if hasattr(waiting, "model_dump") else {}
    return {
        "kind": payload.get("kind") or "clarification",
        "missing_slots": list(payload.get("missing_slots") or []),
    }


def _safe_assets(values: Any, visible: set[str]) -> list[str]:
    result = []
    for value in values or []:
        asset = _canonical_asset(value)
        if asset and asset in visible:
            result.append(asset)
    return list(dict.fromkeys(result))


def _canonical_asset(value: Any) -> str:
    record = resolve_asset(str(value or ""))
    return record.display_name if record else ""


def _safe_texts(values: Any, *, allowed: set[str]) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return list(dict.fromkeys(
        str(value).strip() for value in values
        if str(value or "").strip() and str(value).strip() in allowed
    ))


def _safe_time_window(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: str(value[key])[:80]
        for key in _SAFE_TIME_KEYS
        if value.get(key) not in (None, "")
    }


def _positive_or_zero(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _normalize_limitation(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:160]


__all__ = [
    "project_active_case_semantic_input",
    "project_context_semantic_input",
    "project_pending_semantic_summary",
    "project_recent_turns_semantic_input",
]
