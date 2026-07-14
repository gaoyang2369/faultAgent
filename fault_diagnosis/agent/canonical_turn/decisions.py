"""Per-Goal authorization, source satisfaction and readiness decisions."""

from __future__ import annotations

import hashlib
from typing import Any

from fault_diagnosis.agent.contracts import ArtifactManifest
from fault_diagnosis.domain.canonical_turn import (
    CanonicalTurnRequest,
    GoalAuthorizationDecision,
    GoalReadinessDecision,
    GoalSourceResolution,
)
from fault_diagnosis.domain.security.assets import asset_is_in_scope, resolve_asset
from fault_diagnosis.domain.security.capability import authorize_capability_preflight
from fault_diagnosis.domain.security.contracts import AuthContext


_SOURCE_TYPES = {
    "check_runtime_status": {"sql_artifact"},
    "compare_runtime_status": {"comparison_artifact", "sql_artifact"},
    "explain_fault_code": {"knowledge_artifact"},
    "diagnose_fault": {"analysis_artifact", "sql_artifact"},
    "resolution_recommendation": {"analysis_artifact", "sql_artifact"},
    "generate_report": {"analysis_artifact"},
    "create_workorder_draft": {"report_artifact", "analysis_artifact"},
}


def decide_goal_authorization(
    request: CanonicalTurnRequest,
    auth: AuthContext,
) -> list[GoalAuthorizationDecision]:
    decisions: list[GoalAuthorizationDecision] = []
    for goal in request.goals:
        preflight = authorize_capability_preflight(auth, goal.capability)
        status = "authorized" if preflight.allowed and preflight.mode == "allow" else "denied"
        reason_code = preflight.denied_reason_code
        reason = preflight.reason
        devices = _slot_values(goal.resolved_slots.get("device"))
        if status == "authorized" and not auth.is_admin():
            denied_device = next(
                (device for device in devices if not asset_is_in_scope(device, auth.asset_scope)),
                "",
            )
            if denied_device:
                status = "denied"
                reason_code = "asset_out_of_scope"
                reason = f"Requested asset is outside current account scope: {denied_device}"
            elif devices and goal.capability in {
                "check_runtime_status",
                "compare_runtime_status",
                "diagnose_fault",
                "resolution_recommendation",
                "generate_report",
                "create_workorder_draft",
            }:
                required_tables = {
                    source.table
                    for device in devices
                    for source in ((resolve_asset(device).data_sources if resolve_asset(device) else []))
                }
                if required_tables and not required_tables.issubset(set(auth.table_scope)):
                    status = "denied"
                    reason_code = "table_out_of_scope"
                    reason = "Requested data table is outside current account scope."
        decisions.append(
            GoalAuthorizationDecision(
                goal_id=goal.goal_id,
                capability=goal.capability,
                status=status,
                reason_code=reason_code,
                reason=reason,
                audit=preflight.model_dump(mode="json"),
            )
        )
    return decisions


def resolve_goal_sources(
    request: CanonicalTurnRequest,
    conversation_context: dict[str, Any] | None,
) -> list[GoalSourceResolution]:
    manifests = _manifests(conversation_context)
    results: list[GoalSourceResolution] = []
    for goal in request.goals:
        if goal.capability in {"check_runtime_status", "compare_runtime_status"}:
            contextual = _resolve_explicit_candidate(goal, manifests, _SOURCE_TYPES[goal.capability])
            if contextual is not None:
                results.append(
                    GoalSourceResolution(
                        goal_id=goal.goal_id,
                        status="requires_execution",
                        artifact_id=contextual.artifact_id,
                        artifact_type=contextual.artifact_type,
                        source_freshness=str(contextual.freshness or "unknown"),
                        reason="historical source supplies context only; runtime status is refreshed",
                        candidate_artifact_ids=[contextual.artifact_id],
                        resolved_slots={
                            "device": contextual.device_refs[0] if len(contextual.device_refs) == 1 else list(contextual.device_refs),
                            "fault_code": contextual.fault_code_refs[0] if len(contextual.fault_code_refs) == 1 else list(contextual.fault_code_refs),
                        },
                    )
                )
            else:
                results.append(GoalSourceResolution(goal_id=goal.goal_id, status="requires_execution", reason="runtime status is always refreshed"))
            continue
        allowed = _SOURCE_TYPES.get(goal.capability, set())
        if not goal.source_requirements or not allowed:
            results.append(GoalSourceResolution(goal_id=goal.goal_id, status="requires_execution", reason="no explicit reusable source"))
            continue
        verified = [item for item in manifests if _verified(item)]
        compatible = [item for item in verified if item.artifact_type in allowed]
        explicit_ids = set(goal.source_requirements).intersection(item.artifact_id for item in verified)
        if explicit_ids:
            compatible = [item for item in compatible if item.artifact_id in explicit_ids]
        requested_type = _requested_source_type(goal.source_requirements)
        if requested_type:
            compatible = [item for item in compatible if item.artifact_type == requested_type]
        candidate_ids = [item.artifact_id for item in verified]
        if len(compatible) > 1:
            results.append(GoalSourceResolution(goal_id=goal.goal_id, status="ambiguous", reason="multiple compatible explicit source candidates", candidate_artifact_ids=candidate_ids))
            continue
        if not compatible:
            status = "incompatible" if verified else "unresolved"
            results.append(GoalSourceResolution(goal_id=goal.goal_id, status=status, reason="explicit source has no compatible artifact", candidate_artifact_ids=candidate_ids))
            continue
        selected = compatible[0]
        freshness = str(selected.freshness or "unknown")
        if freshness == "stale":
            results.append(GoalSourceResolution(goal_id=goal.goal_id, status="stale", artifact_id=selected.artifact_id, artifact_type=selected.artifact_type, source_freshness=freshness, reason="explicit source is stale", candidate_artifact_ids=candidate_ids))
            continue
        if goal.capability == "generate_report" and not selected.report_input_snapshot_schema_version:
            results.append(
                GoalSourceResolution(
                    goal_id=goal.goal_id,
                    status="blocked",
                    artifact_id=selected.artifact_id,
                    artifact_type=selected.artifact_type,
                    source_freshness=freshness,
                    reason="legacy Analysis Artifact has no immutable ReportInputSnapshot",
                    reason_code="legacy_analysis_missing_report_input_snapshot",
                    candidate_artifact_ids=candidate_ids,
                )
            )
            continue
        satisfied = (
            goal.capability in {"diagnose_fault", "resolution_recommendation"}
            and selected.artifact_type == "analysis_artifact"
        )
        reusable_id = None
        if goal.capability == "create_workorder_draft":
            reusable = [
                item
                for item in verified
                if item.artifact_type == "workorder_artifact"
                and selected.artifact_id in item.lineage.source_artifact_ids
            ]
            if len(reusable) == 1:
                reusable_id = reusable[0].artifact_id
        operation_key = hashlib.sha256(
            f"{request.thread_id}|{goal.capability}|{selected.artifact_id}".encode("utf-8")
        ).hexdigest()[:24]
        results.append(
            GoalSourceResolution(
                goal_id=goal.goal_id,
                status="satisfied_by_artifact" if satisfied else "source_for_execution",
                artifact_id=selected.artifact_id,
                artifact_type=selected.artifact_type,
                source_freshness=freshness,
                reason="explicit compatible source selected",
                candidate_artifact_ids=candidate_ids,
                resolved_slots={
                    "device": selected.device_refs[0] if len(selected.device_refs) == 1 else list(selected.device_refs),
                    "fault_code": selected.fault_code_refs[0] if len(selected.fault_code_refs) == 1 else list(selected.fault_code_refs),
                },
                reusable_result_artifact_id=reusable_id,
                idempotency_key=operation_key,
                tabular_source_artifact_id=(
                    selected.report_tabular_source_sql_artifact_id or None
                    if goal.capability == "generate_report"
                    else None
                ),
            )
        )
    return results


def decide_goal_readiness(
    request: CanonicalTurnRequest,
    authorization: list[GoalAuthorizationDecision],
    sources: list[GoalSourceResolution],
) -> list[GoalReadinessDecision]:
    auth_by_goal = {item.goal_id: item for item in authorization}
    source_by_goal = {item.goal_id: item for item in sources}
    results: list[GoalReadinessDecision] = []
    for goal in request.goals:
        auth = auth_by_goal[goal.goal_id]
        source = source_by_goal[goal.goal_id]
        if auth.status == "denied":
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="blocked_permission", blockers=[auth.reason_code or "permission_denied"]))
        elif goal.capability == "explain_fault_code" and len(_slot_values(source.resolved_slots.get("fault_code") or goal.resolved_slots.get("fault_code"))) != 1:
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="blocked_missing_slot", blockers=["exactly_one_fault_code"]))
        elif goal.capability == "create_workorder_draft" and len(_slot_values(source.resolved_slots.get("device") or goal.resolved_slots.get("device"))) != 1:
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="blocked_missing_slot", blockers=["exactly_one_device"]))
        elif [slot for slot in goal.missing_slots if slot not in source.resolved_slots]:
            unresolved = [slot for slot in goal.missing_slots if slot not in source.resolved_slots]
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="blocked_missing_slot", blockers=unresolved))
        elif source.status == "satisfied_by_artifact":
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="satisfied_by_artifact"))
        elif source.status in {"ambiguous", "unresolved", "incompatible", "stale", "blocked"}:
            results.append(
                GoalReadinessDecision(
                    goal_id=goal.goal_id,
                    status="blocked_source",
                    blockers=[source.reason_code or source.status],
                )
            )
        else:
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="ready"))
    return results


def _slot_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def _manifests(context: dict[str, Any] | None) -> list[ArtifactManifest]:
    values = (context or {}).get("artifact_manifests") or []
    result: list[ArtifactManifest] = []
    for value in values:
        try:
            result.append(ArtifactManifest.model_validate(value))
        except Exception:
            continue
    return result


def _verified(item: ArtifactManifest) -> bool:
    return (
        item.status == "completed"
        and item.artifact_status == "complete"
        and item.persistence_status == "committed"
        and item.readback_verified
        and item.lineage.lineage_status == "complete"
    )


def _requested_source_type(requirements: list[str]) -> str:
    text = " ".join(requirements)
    if "诊断" in text or "分析" in text:
        return "analysis_artifact"
    if "报告" in text:
        return "report_artifact"
    if "数据" in text:
        return "sql_artifact"
    return ""


def _resolve_explicit_candidate(goal, manifests: list[ArtifactManifest], allowed: set[str]) -> ArtifactManifest | None:
    if not goal.source_requirements:
        return None
    verified = [item for item in manifests if _verified(item) and item.artifact_type in allowed]
    explicit_ids = set(goal.source_requirements).intersection(item.artifact_id for item in verified)
    if explicit_ids:
        verified = [item for item in verified if item.artifact_id in explicit_ids]
    requested_type = _requested_source_type(goal.source_requirements)
    if requested_type:
        verified = [item for item in verified if item.artifact_type == requested_type]
    return verified[0] if len(verified) == 1 else None
