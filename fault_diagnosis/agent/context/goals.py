"""Canonical goal, target-scope, query, and slot contracts for V2."""

from __future__ import annotations

import re
from typing import Any

from ..contracts import EffectiveGoal, EffectiveGoalSet, GoalQuerySpec, IntentFrame, TargetScope


_CAPABILITY = {
    "explain_fault_code": "explain_fault_code",
    "check_current_status": "check_runtime_status",
    "compare_runtime_status": "compare_runtime_status",
    "diagnose_fault": "diagnose_fault",
    "health_assessment": "diagnose_fault",
    "root_cause_analysis": "diagnose_fault",
    "resolution_recommendation": "resolution_recommendation",
    "generate_report": "generate_report",
    "decide_workorder": "create_workorder_draft",
    "create_workorder_draft": "create_workorder_draft",
    "dispatch_workorder": "create_workorder_draft",
}

_DELIVERABLE = {
    "explain_fault_code": "fault_code_explanation",
    "check_runtime_status": "runtime_status",
    "compare_runtime_status": "runtime_comparison",
    "diagnose_fault": "diagnosis",
    "resolution_recommendation": "recommendations",
    "generate_report": "report",
    "create_workorder_draft": "workorder_draft",
}

_REQUIRED = {
    "explain_fault_code": ["fault_code"],
    "check_runtime_status": ["device"],
    "compare_runtime_status": ["at_least_two_devices"],
    "diagnose_fault": ["device_or_runtime_artifact"],
    "resolution_recommendation": ["diagnosis_or_runtime_artifact"],
    "generate_report": ["reportable_source_or_upstream"],
    "create_workorder_draft": ["exactly_one_device", "complete_analysis_or_report_lineage"],
}

_OPTIONAL = {
    "explain_fault_code": ["device"],
    "check_runtime_status": ["time_window", "fault_code"],
    "diagnose_fault": ["fault_code", "knowledge_artifact"],
    "resolution_recommendation": ["knowledge_artifact"],
    "generate_report": ["device", "fault_code", "evidence_bundle"],
    "create_workorder_draft": ["fault_code"],
}

_EVIDENCE = {
    "explain_fault_code": ["manual_reference"],
    "check_runtime_status": ["sql_runtime_evidence"],
    "compare_runtime_status": ["per_device_runtime_assessment"],
    "diagnose_fault": ["sql_runtime_evidence"],
    "resolution_recommendation": ["diagnosis_or_runtime_evidence"],
    "generate_report": ["reportable_artifact"],
    "create_workorder_draft": ["complete_artifact_lineage"],
}

_PRIORITY = {
    "explain_fault_code": 10,
    "check_runtime_status": 20,
    "compare_runtime_status": 20,
    "diagnose_fault": 30,
    "resolution_recommendation": 40,
    "generate_report": 50,
    "create_workorder_draft": 60,
}


def build_target_scope(
    *,
    raw_message: str,
    current_devices: list[str],
    inherited_devices: list[str],
    source: str = "current_message",
) -> TargetScope:
    current = _dedupe(current_devices)
    inherited = _dedupe(inherited_devices)
    compact = (raw_message or "").replace(" ", "")
    if any(word in compact for word in ("比较", "对比")) and len(current) >= 2:
        return TargetScope(
            operation="compare",
            included_devices=current,
            resolved_devices=current,
            source="current_message",
        )
    replace = bool(current and any(word in compact for word in ("不看", "不是", "改查", "换成", "改成", "替换")))
    if replace:
        excluded = [device for device in _dedupe([*current, *inherited]) if _is_excluded(raw_message, device)]
        included = [device for device in current if device not in excluded]
        if not included and current:
            included = [current[-1]]
            excluded = _dedupe([*excluded, *current[:-1]])
        resolved = [device for device in included if device not in set(excluded)]
        return TargetScope(
            operation="replace",
            included_devices=included,
            excluded_devices=excluded,
            resolved_devices=resolved,
            explicit_switch=True,
            source="current_message",
        )
    resolved = current or inherited
    return TargetScope(
        operation="keep",
        included_devices=current,
        resolved_devices=resolved,
        source="current_message" if current else source if source in {"artifact", "case_state", "context_signal"} else "case_state",
    )


def canonicalize_goals(intent: IntentFrame, *, target_scope: TargetScope, source_policy: str) -> EffectiveGoalSet:
    ordered: list[str] = []
    for raw in intent.sub_intents or ([intent.primary_intent] if intent.primary_intent else []):
        capability = _CAPABILITY.get(raw)
        if capability and capability not in ordered:
            ordered.append(capability)
    if target_scope.operation == "compare":
        ordered = [item for item in ordered if item != "check_runtime_status"]
        if "compare_runtime_status" not in ordered:
            ordered.insert(0, "compare_runtime_status")
    goals: list[EffectiveGoal] = []
    for index, capability in enumerate(ordered, start=1):
        goal_id = f"goal_{index:02d}_{capability}"
        dependencies = _dependencies(capability, goals)
        goals.append(
            EffectiveGoal(
                goal_id=goal_id,
                capability=capability,
                target_scope_id=target_scope.scope_id,
                requested_deliverables=[_DELIVERABLE[capability]],
                required_slots=list(_REQUIRED.get(capability, [])),
                optional_slots=list(_OPTIONAL.get(capability, [])),
                evidence_requirements=list(_EVIDENCE.get(capability, [])),
                source_policy=source_policy or "collect_new",
                priority=_PRIORITY.get(capability, 100),
                confidence=intent.confidence,
                explicit=True,
                depends_on_goal_ids=dependencies,
            )
        )
    primary = min(goals, key=lambda item: item.priority).goal_id if goals else ""
    # Preserve the legacy primary intent preference without deleting lower-ranked goals.
    primary_capability = _CAPABILITY.get(intent.primary_intent)
    for goal in goals:
        if goal.capability == primary_capability:
            primary = goal.goal_id
            break
    return EffectiveGoalSet(goals=goals, primary_goal_id=primary)


def build_goal_query_specs(
    *,
    goals: EffectiveGoalSet,
    target_scope: TargetScope,
    fault_codes: list[str],
    time_window: dict[str, Any],
) -> list[GoalQuerySpec]:
    devices = list(target_scope.resolved_devices)
    codes = _dedupe(fault_codes)
    specs: list[GoalQuerySpec] = []
    for goal in goals.goals:
        capability = goal.capability
        sql_question = None
        rag_query = None
        analysis_request = None
        if capability in {"check_runtime_status", "compare_runtime_status", "diagnose_fault"}:
            window = str(time_window.get("value") or "最近一小时")
            sql_question = f"查询 {'、'.join(devices)} {window}的运行状态"
        if capability == "explain_fault_code" or (codes and capability in {"diagnose_fault", "resolution_recommendation"}):
            rag_query = f"{' '.join(codes)} 故障码含义 原因 触发条件 处理措施".strip()
        if capability in {"diagnose_fault", "resolution_recommendation"}:
            analysis_request = {
                "mode": "diagnosis" if capability == "diagnose_fault" else "recommendations",
                "devices": devices,
                "fault_codes": codes,
                "requires_sql_evidence": True,
                "knowledge_optional": True,
            }
        specs.append(
            GoalQuerySpec(
                query_spec_id=f"query_{goal.goal_id}",
                goal_id=goal.goal_id,
                capability=capability,
                sql_question=sql_question,
                rag_query=rag_query,
                analysis_request=analysis_request,
                target_devices=devices,
                fault_codes=codes,
            )
        )
    return specs


def missing_goal_slots(
    *,
    goals: EffectiveGoalSet,
    devices: list[str],
    fault_codes: list[str],
    target_artifact_type: str | None,
    target_lineage_status: str = "",
) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    capabilities = {goal.capability for goal in goals.goals}
    reportable = target_artifact_type in {"sql_artifact", "analysis_artifact", "structured_analysis_artifact", "report_artifact"}
    analysis_source = target_artifact_type in {"analysis_artifact", "structured_analysis_artifact", "report_artifact"}
    for goal in goals.goals:
        missing = ""
        if goal.capability == "explain_fault_code" and not fault_codes:
            missing = "fault_code"
        elif goal.capability in {"check_runtime_status", "diagnose_fault"} and not devices and not reportable:
            missing = "device"
        elif goal.capability == "compare_runtime_status" and len(devices) < 2:
            missing = "at_least_two_devices"
        elif goal.capability == "create_workorder_draft":
            if len(devices) != 1:
                missing = "exactly_one_device"
        if missing:
            reasons.append(
                {
                    "goal_id": goal.goal_id,
                    "missing_slot": missing,
                    "valid_slots": {"devices": list(devices), "fault_codes": list(fault_codes), "target_artifact_type": target_artifact_type},
                    "inheritance_failure": "no_compatible_complete_artifact_or_case_slot",
                }
            )
    return reasons


def _dependencies(capability: str, existing: list[EffectiveGoal]) -> list[str]:
    by_capability = {goal.capability: goal.goal_id for goal in existing}
    if capability == "diagnose_fault":
        return _present(by_capability, "check_runtime_status")
    if capability == "resolution_recommendation":
        return _present(by_capability, "diagnose_fault", "check_runtime_status")
    if capability == "generate_report":
        return _present(by_capability, "diagnose_fault", "compare_runtime_status", "check_runtime_status")
    if capability == "create_workorder_draft":
        return _present(by_capability, "generate_report", "diagnose_fault")
    return []


def _present(mapping: dict[str, str], *capabilities: str) -> list[str]:
    return [mapping[item] for item in capabilities if item in mapping]


def _is_excluded(message: str, device: str) -> bool:
    compact = (message or "").replace(" ", "")
    escaped = re.escape(device)
    return bool(re.search(rf"(?:不看|不是|排除|不要)(?:设备)?{escaped}", compact))


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))
