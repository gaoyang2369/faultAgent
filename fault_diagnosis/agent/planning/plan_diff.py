"""Compare legacy plan snapshots with V2 execution plans."""

from __future__ import annotations

from typing import Any

from ..contracts import ExecutionPlan
from .policy_bridge import PlanPolicyBridge


def diff_plans(legacy_plan: Any, v2_plan: ExecutionPlan | dict[str, Any]) -> dict[str, Any]:
    """Return a compact, JSON-safe comparison of legacy and V2 plan surfaces."""

    bridge = PlanPolicyBridge()
    legacy = _legacy_summary(legacy_plan, bridge)
    v2 = _v2_summary(_coerce_v2(v2_plan), bridge)
    return {
        "legacy": legacy,
        "v2": v2,
        "added": {
            key: sorted(set(v2[key]) - set(legacy[key]))
            for key in ("nodes", "tools", "required_evidence", "approval")
        },
        "removed": {
            key: sorted(set(legacy[key]) - set(v2[key]))
            for key in ("nodes", "tools", "required_evidence", "approval")
        },
        "changed": {
            "risk": legacy["risk"] != v2["risk"],
            "expected_outputs": sorted(set(legacy["expected_outputs"]) ^ set(v2["expected_outputs"])),
        },
    }


def _coerce_v2(plan: ExecutionPlan | dict[str, Any]) -> ExecutionPlan:
    return plan if isinstance(plan, ExecutionPlan) else ExecutionPlan.model_validate(plan)


def _legacy_summary(plan: Any, bridge: PlanPolicyBridge) -> dict[str, Any]:
    data = _dump(plan)
    enabled_nodes = data.get("enabled_nodes") or {}
    evidence_gaps = data.get("evidence_gaps") or {}
    manual = data.get("manual_confirmation") or {}
    tools = data.get("runtime_tools") or data.get("planned_tools") or []
    return {
        "nodes": sorted(key for key, value in dict(enabled_nodes).items() if value),
        "tools": sorted(bridge.legacy_to_v2_tools([str(tool) for tool in tools])),
        "required_evidence": sorted(
            str(item)
            for item in (
                evidence_gaps.get("required_evidence")
                or data.get("required_evidence")
                or []
            )
        ),
        "risk": str((data.get("workflow_route") or {}).get("risk_level") or data.get("risk_level") or ""),
        "approval": ["manual_confirmation"] if manual.get("required") else [],
        "expected_outputs": [str(data.get("requested_output"))] if data.get("requested_output") else [],
    }


def _v2_summary(plan: ExecutionPlan, bridge: PlanPolicyBridge) -> dict[str, Any]:
    del bridge
    return {
        "nodes": sorted(str(node.get("node_type") or "") for node in plan.nodes if node.get("node_type")),
        "tools": sorted(plan.allowed_tools),
        "required_evidence": sorted(plan.required_evidence),
        "risk": plan.risk_level,
        "approval": sorted(str(item.get("type") or "") for item in plan.approval_requirements if item.get("type")),
        "expected_outputs": sorted(plan.expected_outputs),
    }


def _dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return dict(value) if hasattr(value, "__iter__") else {}
