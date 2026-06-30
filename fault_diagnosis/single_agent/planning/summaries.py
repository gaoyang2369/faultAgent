"""Compact summaries for shadow planner output."""

from __future__ import annotations

from typing import Any

from .contracts import PlanningDecision


def summarize_shadow_plan(value: Any) -> dict[str, Any]:
    """Return compact shadow-plan metadata for plan, complete, trace, and eval."""

    data = value.model_dump(exclude_none=True) if isinstance(value, PlanningDecision) else dict(value or {}) if isinstance(value, dict) else {}
    if not data:
        return {}
    nodes = list(data.get("nodes") or [])
    enabled = [str(item.get("node")) for item in nodes if isinstance(item, dict) and item.get("desired_state") == "enabled" and item.get("node")]
    blocked = [str(item.get("node")) for item in nodes if isinstance(item, dict) and item.get("desired_state") == "blocked" and item.get("node")]
    evidence_plan = data.get("evidence_plan") if isinstance(data.get("evidence_plan"), dict) else {}
    tool_plan = data.get("tool_plan") if isinstance(data.get("tool_plan"), dict) else {}
    output_plan = data.get("output_plan") if isinstance(data.get("output_plan"), dict) else {}
    warnings = list(data.get("planner_warnings") or [])
    return {
        "planner_mode": data.get("planner_mode", "shadow"),
        "enabled_node_names": enabled,
        "blocked_node_names": blocked,
        "refresh_required": bool(evidence_plan.get("refresh_required", False)),
        "candidate_tools": list(tool_plan.get("candidate_tools") or []),
        "authorized_runtime_tools": list(tool_plan.get("authorized_runtime_tools") or []),
        "expected_output": output_plan.get("expected_output", "answer"),
        "planner_warnings_count": len(warnings),
        "planner_warnings": warnings[:5],
        "planner_summary": data.get("planner_summary", ""),
    }
