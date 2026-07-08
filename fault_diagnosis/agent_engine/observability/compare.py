"""Plan comparison records for Agent Engine V2 rollout."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from ...common.logger import get_logger
from ..contracts import ExecutionPlan, PlanSnapshotV2
from ..flags import compare_log_path
from ..planning.policy_bridge import PlanPolicyBridge

_log = get_logger("agent_engine.v2_compare")
_SEVERITY_RANK = {"none": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
_DANGEROUS_TOOLS = {"device_control.write", "config.write", "workorder.dispatch", "sql.write"}


def build_plan_compare(
    *,
    legacy_plan: Any,
    v2_snapshot: PlanSnapshotV2,
    request_id: str = "",
    trace_id: str = "",
    thread_id: str = "",
    effective_skill_mode: str = "legacy",
    fallback_reason: str = "",
) -> dict[str, Any]:
    legacy = _legacy_summary(legacy_plan)
    v2 = _v2_summary(v2_snapshot)
    diffs = _diffs(legacy, v2)
    severity = _overall_severity(diffs, v2)
    review_required = severity in {"critical"} or (
        severity == "error" and v2.get("risk") in {"high", "critical"}
    )
    primary_skill = str(v2.get("primary_skill") or "")
    return {
        "schema_version": "agent_engine_v2_compare.v1",
        "request_id": request_id,
        "trace_id": trace_id,
        "thread_id": thread_id,
        "primary_skill": primary_skill,
        "effective_skill_mode": effective_skill_mode,
        "review_required": review_required,
        "severity": severity,
        "summary": _summary_text(diffs, severity, primary_skill, fallback_reason),
        "fallback_reason": fallback_reason,
        "legacy": legacy,
        "v2": v2,
        "diffs": diffs,
        "created_at": datetime.now(UTC).isoformat(),
    }


def record_plan_compare(record: dict[str, Any], *, path: str | None = None) -> None:
    target = path or compare_log_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        _log.warning("V2 compare record write failed", error=str(exc), path=target)
    _log.info(
        "V2 compare recorded",
        request_id=record.get("request_id"),
        trace_id=record.get("trace_id"),
        thread_id=record.get("thread_id"),
        primary_skill=record.get("primary_skill"),
        effective_skill_mode=record.get("effective_skill_mode"),
        review_required=record.get("review_required"),
        severity=record.get("severity"),
        summary=record.get("summary"),
    )


def _legacy_summary(plan: Any) -> dict[str, Any]:
    data = _dump(plan)
    bridge = PlanPolicyBridge()
    route = data.get("workflow_route") if isinstance(data.get("workflow_route"), dict) else {}
    evidence = data.get("evidence_gaps") if isinstance(data.get("evidence_gaps"), dict) else {}
    manual = data.get("manual_confirmation") if isinstance(data.get("manual_confirmation"), dict) else {}
    enabled = data.get("enabled_nodes") if isinstance(data.get("enabled_nodes"), dict) else {}
    legacy_tools = [str(item) for item in data.get("runtime_tools") or data.get("planned_tools") or []]
    return {
        "intent": {
            "task_family": data.get("task_family") or route.get("task_family") or "",
            "requested_output": data.get("requested_output") or "",
        },
        "skill": _legacy_skill(data),
        "nodes": sorted({_normalize_legacy_node(str(key)) for key, value in enabled.items() if value}),
        "tools": sorted(bridge.legacy_to_v2_tools(legacy_tools)),
        "evidence_requirements": sorted(str(item) for item in evidence.get("required_evidence") or []),
        "risk": str(route.get("risk_level") or data.get("risk_level") or ""),
        "approval": ["manual_confirmation"] if manual.get("required") else [],
    }


def _v2_summary(snapshot: PlanSnapshotV2) -> dict[str, Any]:
    plan = snapshot.execution_plan if isinstance(snapshot.execution_plan, ExecutionPlan) else ExecutionPlan.model_validate(snapshot.execution_plan)
    return {
        "intent": {
            "primary": snapshot.intent_frame.primary_intent,
            "sub_intents": list(snapshot.intent_frame.sub_intents),
            "requested_outputs": list(snapshot.intent_frame.requested_outputs),
        },
        "skill": {
            "primary": snapshot.skill_route.primary_skill,
            "selected": list(snapshot.skill_route.selected_skills),
        },
        "primary_skill": snapshot.skill_route.primary_skill,
        "nodes": sorted(str(node.get("node_type") or "") for node in plan.nodes if node.get("node_type")),
        "tools": sorted(plan.allowed_tools),
        "evidence_requirements": sorted(plan.required_evidence),
        "risk": plan.risk_level,
        "approval": sorted(str(item.get("type") or "") for item in plan.approval_requirements if item.get("type")),
        "status": snapshot.status,
        "warnings": list(snapshot.warnings),
    }


def _diffs(legacy: dict[str, Any], v2: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("nodes", "tools", "evidence_requirements", "approval"):
        left = set(legacy.get(key) or [])
        right = set(v2.get(key) or [])
        if left == right:
            continue
        items.append(
            {
                "surface": key,
                "severity": _surface_severity(key, removed=sorted(left - right), added=sorted(right - left), v2=v2),
                "legacy": sorted(left),
                "v2": sorted(right),
                "added": sorted(right - left),
                "removed": sorted(left - right),
            }
        )
    if str(legacy.get("risk") or "") != str(v2.get("risk") or ""):
        items.append(
            {
                "surface": "risk",
                "severity": "error" if v2.get("risk") in {"high", "critical"} else "warning",
                "legacy": legacy.get("risk") or "",
                "v2": v2.get("risk") or "",
            }
        )
    legacy_skill = (legacy.get("skill") or {}).get("primary")
    v2_skill = (v2.get("skill") or {}).get("primary")
    if legacy_skill and v2_skill and legacy_skill != v2_skill:
        items.append({"surface": "skill", "severity": "warning", "legacy": legacy_skill, "v2": v2_skill})
    return items


def _surface_severity(key: str, *, removed: list[str], added: list[str], v2: dict[str, Any]) -> str:
    values = set(removed + added)
    if values.intersection(_DANGEROUS_TOOLS):
        return "critical"
    if key == "approval" and removed and v2.get("risk") in {"high", "critical"}:
        return "critical"
    if key == "approval" and (added or removed):
        return "error"
    return "warning"


def _overall_severity(diffs: list[dict[str, Any]], v2: dict[str, Any]) -> str:
    if not diffs:
        return "none"
    severity = max((str(item.get("severity") or "warning") for item in diffs), key=lambda item: _SEVERITY_RANK.get(item, 0))
    if v2.get("status") == "blocked" and severity == "none":
        return "warning"
    return severity


def _summary_text(diffs: list[dict[str, Any]], severity: str, primary_skill: str, fallback_reason: str) -> str:
    if not diffs:
        base = f"V2 compare aligned for {primary_skill or 'unknown'}."
    else:
        surfaces = ", ".join(str(item.get("surface")) for item in diffs[:5])
        base = f"V2 compare severity={severity}; changed surfaces: {surfaces}."
    if fallback_reason:
        base = f"{base} Fallback: {fallback_reason}."
    return base


def _legacy_skill(data: dict[str, Any]) -> dict[str, Any]:
    family = str(data.get("task_family") or "")
    policy = str(data.get("policy_id") or "")
    mapping = {
        "knowledge_lookup": "fault_code_explain",
        "runtime_status": "runtime_status",
        "reporting": "report_generation",
        "action_or_workorder": "workorder_decision",
    }
    if policy == "alarm_triage_v1":
        primary = "alarm_triage"
    elif policy == "root_cause_analysis_v1":
        primary = "root_cause"
    else:
        primary = mapping.get(family, "")
    return {"primary": primary}


def _normalize_legacy_node(node: str) -> str:
    return {
        "knowledge": "rag",
        "workorder_decision": "workorder",
        "resolution_recommendation": "analysis",
        "permission_check": "approval",
    }.get(node, node)


def _dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return dict(value)
    return {}
