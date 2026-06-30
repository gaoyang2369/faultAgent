"""Compact summaries for Phase 4.2 planning diffs."""

from __future__ import annotations

from typing import Any

from .diff_contracts import PlanningDiff

_MIGRATION_REASON = "shadow evaluation only; no execution migration in Phase 4.2"


def summarize_planning_diff(value: Any) -> dict[str, Any]:
    """Return compact planning-diff metadata for plan, complete, trace, and eval."""

    data = value.model_dump(exclude_none=True) if isinstance(value, PlanningDiff) else dict(value or {}) if isinstance(value, dict) else {}
    if not data:
        return {}
    counters = dict(data.get("counters") or {})
    diff_types = _diff_types(data)
    migration = dict(data.get("migration_readiness") or {})
    migration.setdefault("read_only_candidate", False)
    migration["safe_to_migrate"] = False
    migration.setdefault("reason", _MIGRATION_REASON)
    return {
        "overall_status": data.get("overall_status", "needs_review"),
        "severity": data.get("severity", "warning"),
        "node_diff_count": int(counters.get("node_diff_count", 0) or 0),
        "tool_diff_count": int(counters.get("tool_diff_count", 0) or 0),
        "evidence_diff_count": int(counters.get("evidence_diff_count", 0) or 0),
        "output_diff_count": int(counters.get("output_diff_count", 0) or 0),
        "safety_diff_count": int(counters.get("safety_diff_count", 0) or 0),
        "critical_count": int(counters.get("critical_count", 0) or 0),
        "warning_count": int(counters.get("warning_count", 0) or 0),
        "summary": str(data.get("summary") or ""),
        "counters": counters,
        "diff_types": diff_types,
        "migration_readiness": migration,
    }


def _diff_types(data: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("node_diffs", "tool_diffs", "evidence_diffs", "output_diffs", "safety_diffs"):
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            diff_type = str(item.get("diff_type") or "")
            severity = str(item.get("severity") or "none")
            if diff_type and (diff_type != "exact_match" or severity != "none"):
                values.append(diff_type)
    return sorted(set(values))
