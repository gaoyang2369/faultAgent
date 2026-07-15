"""Per-Goal authorization, source satisfaction and readiness decisions."""

from __future__ import annotations

import hashlib
from typing import Any

from fault_diagnosis.domain.canonical_turn import (
    BoundCanonicalTurn,
    ContextCandidate,
    GoalAuthorizationDecision,
    GoalReadinessDecision,
    GoalSourceResolution,
)
from fault_diagnosis.domain.security.assets import asset_is_in_scope, resolve_asset
from fault_diagnosis.domain.security.capability import authorize_capability_preflight
from fault_diagnosis.domain.security.contracts import AuthContext


def decide_goal_authorization(
    bound_turn: BoundCanonicalTurn,
    auth: AuthContext,
) -> list[GoalAuthorizationDecision]:
    request = bound_turn.canonical_turn
    bindings = {item.goal_id: item for item in bound_turn.goal_bindings}
    decisions: list[GoalAuthorizationDecision] = []
    for goal in request.goals:
        preflight = authorize_capability_preflight(auth, goal.capability)
        status = "authorized" if preflight.allowed and preflight.mode == "allow" else "denied"
        reason_code = preflight.denied_reason_code
        reason = preflight.reason
        devices = bindings[goal.goal_id].asset_refs
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
                "evaluate_workorder_need",
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
    bound_turn: BoundCanonicalTurn,
    candidates: list[ContextCandidate],
) -> list[GoalSourceResolution]:
    request = bound_turn.canonical_turn
    bindings = {item.goal_id: item for item in bound_turn.goal_bindings}
    by_ref = {item.artifact_ref: item for item in candidates if item.artifact_ref}
    results: list[GoalSourceResolution] = []
    for goal in request.goals:
        binding = bindings[goal.goal_id]
        slots = _binding_slots(binding)
        selected = next((by_ref.get(ref) for ref in binding.source_artifact_refs if by_ref.get(ref)), None)
        if binding.binding_status == "needs_clarification":
            reason = binding.clarification.reason_code if binding.clarification else "context_ambiguous"
            status = "ambiguous" if reason.startswith("ambiguous_") else "unresolved"
            results.append(GoalSourceResolution(goal_id=goal.goal_id, status=status, reason_code=reason, reason="canonical context binding requires clarification", resolved_slots=slots))
            continue
        if binding.binding_status == "refresh_required":
            results.append(GoalSourceResolution(goal_id=goal.goal_id, status="stale", artifact_id=selected.artifact_ref if selected else None, artifact_type=selected.artifact_type if selected else None, source_freshness=selected.freshness_state if selected else "stale", reason_code="refresh_required", reason="bound source requires refresh", resolved_slots=slots))
            continue
        if goal.dependencies and goal.capability in {"generate_report", "evaluate_workorder_need", "create_workorder_draft"}:
            results.append(GoalSourceResolution(
                goal_id=goal.goal_id,
                status="requires_execution",
                artifact_id=selected.artifact_ref if selected else None,
                artifact_type=selected.artifact_type if selected else None,
                source_freshness=selected.freshness_state if selected else "unknown",
                reason="same-turn canonical dependency supplies the execution source",
                candidate_artifact_ids=[str(selected.artifact_ref)] if selected and selected.artifact_ref else [],
                resolved_slots=slots,
            ))
            continue
        if selected is None:
            reason = "no evidence candidate; return insufficient_evidence" if goal.capability == "evaluate_workorder_need" else "new execution required"
            results.append(GoalSourceResolution(goal_id=goal.goal_id, status="requires_execution", reason=reason, resolved_slots=slots))
            continue
        if goal.capability == "generate_report" and selected.artifact_type == "analysis_artifact" and not selected.report_input_snapshot_schema_version:
            results.append(
                GoalSourceResolution(
                    goal_id=goal.goal_id,
                    status="blocked",
                    artifact_id=selected.artifact_ref,
                    artifact_type=selected.artifact_type,
                    source_freshness=selected.freshness_state,
                    reason="legacy Analysis Artifact has no immutable ReportInputSnapshot",
                    reason_code="legacy_analysis_missing_report_input_snapshot",
                    candidate_artifact_ids=[str(selected.artifact_ref)],
                    resolved_slots=slots,
                )
            )
            continue
        satisfied = (
            goal.capability in {"diagnose_fault", "resolution_recommendation"}
            and selected.artifact_type == "analysis_artifact"
        ) or goal.capability in {"check_runtime_status", "compare_runtime_status"}
        reusable_id = None
        if goal.capability == "create_workorder_draft":
            reusable = {
                item.artifact_ref: item for item in candidates
                if item.completed and item.artifact_type == "workorder_artifact"
                and selected.artifact_ref in item.lineage_refs
                and item.artifact_ref
            }
            if len(reusable) == 1:
                reusable_id = next(iter(reusable))
        operation_key = hashlib.sha256(
            f"{request.thread_id}|{goal.capability}|{selected.artifact_ref}".encode("utf-8")
        ).hexdigest()[:24]
        results.append(
            GoalSourceResolution(
                goal_id=goal.goal_id,
                status="satisfied_by_artifact" if satisfied else "source_for_execution",
                artifact_id=selected.artifact_ref,
                artifact_type=selected.artifact_type,
                source_freshness=selected.freshness_state,
                reason="CanonicalContextBinder selected a compatible source",
                candidate_artifact_ids=[str(selected.artifact_ref)],
                resolved_slots=slots,
                reusable_result_artifact_id=reusable_id,
                idempotency_key=operation_key,
                tabular_source_artifact_id=selected.tabular_source_artifact_ref if goal.capability == "generate_report" else None,
            )
        )
    return results


def decide_goal_readiness(
    bound_turn: BoundCanonicalTurn,
    authorization: list[GoalAuthorizationDecision],
    sources: list[GoalSourceResolution],
) -> list[GoalReadinessDecision]:
    request = bound_turn.canonical_turn
    bindings = {item.goal_id: item for item in bound_turn.goal_bindings}
    auth_by_goal = {item.goal_id: item for item in authorization}
    source_by_goal = {item.goal_id: item for item in sources}
    results: list[GoalReadinessDecision] = []
    for goal in request.goals:
        auth = auth_by_goal[goal.goal_id]
        source = source_by_goal[goal.goal_id]
        binding = bindings[goal.goal_id]
        if auth.status == "denied":
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="blocked_permission", blockers=[auth.reason_code or "permission_denied"]))
        elif goal.capability == "explain_fault_code" and len(_slot_values(source.resolved_slots.get("fault_code") or goal.resolved_slots.get("fault_code"))) != 1:
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="blocked_missing_slot", blockers=["exactly_one_fault_code"]))
        elif goal.execution_condition and not goal.execution_condition.source_goal_id:
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="blocked_source", blockers=["condition_source_unresolved"]))
        elif goal.capability in {"evaluate_workorder_need", "create_workorder_draft"} and len(_slot_values(source.resolved_slots.get("device") or goal.resolved_slots.get("device"))) != 1:
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="blocked_missing_slot", blockers=["exactly_one_device"]))
        elif binding.binding_status in {"needs_clarification", "blocked"}:
            blockers = ["device" if item == "missing_asset" else item for item in binding.blockers]
            results.append(GoalReadinessDecision(goal_id=goal.goal_id, status="blocked_missing_slot", blockers=blockers))
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


def _binding_slots(binding) -> dict[str, Any]:  # noqa: ANN001
    result: dict[str, Any] = {}
    if binding.asset_refs:
        result["device"] = binding.asset_refs[0] if len(binding.asset_refs) == 1 else binding.asset_refs
    if binding.fault_codes:
        result["fault_code"] = binding.fault_codes[0] if len(binding.fault_codes) == 1 else binding.fault_codes
    if binding.time_window:
        result["time_window"] = binding.time_window
    return result
