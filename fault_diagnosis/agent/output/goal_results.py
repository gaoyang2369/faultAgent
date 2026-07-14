"""Build canonical per-goal execution and evidence projections for Output."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import EvidenceBundle

from ..contracts import GoalExecutionResult


_CAPABILITY_ARTIFACT_KEYS = {
    "explain_fault_code": ("knowledge_artifact",),
    "check_runtime_status": ("runtime_status_assessment", "sql_artifact"),
    "compare_runtime_status": ("comparison_artifact",),
    "diagnose_fault": ("analysis_artifact",),
    "resolution_recommendation": ("analysis_artifact",),
    "generate_report": ("report_artifact",),
    "create_workorder_draft": ("workorder_draft", "workorder_suggestion"),
    "evaluate_workorder_need": ("workorder_suggestion",),
}


def is_user_requested_goal(goal: Any) -> bool:
    """Apply the canonical explicit/inferred visibility rule in one place."""

    origin = str(_value(goal, "origin") or "")
    return origin == "explicit" or (origin == "inferred" and _value(goal, "user_visible") is True)


def ordered_user_goals(goals: list[Any]) -> list[dict[str, Any]]:
    normalized = [_mapping(goal) for goal in goals]
    visible = [goal for goal in normalized if is_user_requested_goal(goal)]
    return sorted(
        visible,
        key=lambda goal: (
            int(goal.get("clause_index") or 0),
            normalized.index(goal),
            str(goal.get("goal_id") or ""),
        ),
    )


def dependency_closure(goal_id: str, goals: list[Any]) -> list[str]:
    by_id = {str(_value(goal, "goal_id") or ""): _mapping(goal) for goal in goals}
    result: list[str] = []
    pending = list(by_id.get(goal_id, {}).get("depends_on_goal_ids") or by_id.get(goal_id, {}).get("dependencies") or [])
    while pending:
        dependency_id = str(pending.pop(0) or "")
        if not dependency_id or dependency_id in result:
            continue
        result.append(dependency_id)
        dependency = by_id.get(dependency_id, {})
        pending.extend(dependency.get("depends_on_goal_ids") or dependency.get("dependencies") or [])
    return result


def build_goal_execution_results(
    *,
    goals: list[Any],
    node_results: list[Any],
    artifacts: dict[str, Any],
    evidence_bundle: EvidenceBundle | None,
    status: str,
    plan_nodes: list[Any] | None = None,
    goal_statuses: list[Any] | None = None,
) -> list[GoalExecutionResult]:
    status_by_goal = {str(_value(item, "goal_id") or ""): _mapping(item) for item in goal_statuses or []}
    evidence_by_goal, claims_by_goal = _evidence_refs_by_goal(evidence_bundle)
    artifact_by_goal = _artifact_ids_by_goal(artifacts)
    results: list[GoalExecutionResult] = []
    for raw_goal in goals:
        goal = _mapping(raw_goal)
        goal_id = str(goal.get("goal_id") or "")
        capability = str(goal.get("capability") or "")
        scoped_nodes = [item for item in node_results if goal_id in _goal_ids(item)]
        planned_nodes = [item for item in plan_nodes or [] if goal_id in _goal_ids(item)]
        executed_nodes = [item for item in scoped_nodes if str(_value(item, "status") or "") != "pending"]
        artifact_ids = list(artifact_by_goal.get(goal_id, []))
        artifact_ids.extend(
            str(_value(item, "artifact_id") or "")
            for item in scoped_nodes
            if str(_value(item, "artifact_id") or "")
        )
        if not artifact_ids:
            artifact_ids.extend(_direct_artifact_ids(capability, artifacts))
        if not artifact_ids and str(goal.get("source_artifact_id") or ""):
            artifact_ids.append(str(goal["source_artifact_id"]))
        terminal = status_by_goal.get(goal_id, {})
        terminal_status = _terminal_status(
            goal,
            scoped_nodes=scoped_nodes,
            artifacts=artifacts,
            terminal=terminal,
            invocation_status=status,
        )
        if terminal_status == "failed" and capability in {"diagnose_fault", "resolution_recommendation"} and claims_by_goal.get(goal_id):
            terminal_status = "completed"
        error = _goal_error(scoped_nodes, terminal)
        if not error.get("code") and str(goal.get("execution_error_code") or ""):
            error = {
                "code": str(goal.get("execution_error_code") or ""),
                "message": str(goal.get("execution_error_message") or ""),
            }
        blockers = list(goal.get("depends_on_goal_ids") or goal.get("dependencies") or []) if terminal_status == "blocked" else []
        results.append(
            GoalExecutionResult(
                goal_id=goal_id,
                capability=capability,
                user_requested=is_user_requested_goal(goal),
                status=terminal_status,
                planned_node_ids=_dedupe([str(_value(item, "node_id") or "") for item in planned_nodes or scoped_nodes]),
                executed_node_ids=_dedupe([str(_value(item, "node_id") or "") for item in executed_nodes]),
                artifact_ids=_dedupe(artifact_ids),
                evidence_ids=list(evidence_by_goal.get(goal_id, [])),
                claim_ids=list(claims_by_goal.get(goal_id, [])),
                satisfied_by_artifact=str(goal.get("readiness_status") or "") == "satisfied_by_artifact"
                or str(terminal.get("status") or "") == "satisfied",
                blocked_by_goal_ids=_dedupe([str(item) for item in blockers]),
                error_code=error.get("code") or terminal.get("error_code"),
                error_message=error.get("message") or terminal.get("error_message"),
            )
        )
    return results


def refs_for_goal_closure(
    goal_id: str,
    *,
    goals: list[Any],
    executions: list[GoalExecutionResult],
) -> tuple[list[str], list[str], list[str]]:
    allowed = {goal_id, *dependency_closure(goal_id, goals)}
    selected = [result for result in executions if result.goal_id in allowed]
    return (
        _dedupe([item for result in selected for item in result.artifact_ids]),
        _dedupe([item for result in selected for item in result.evidence_ids]),
        _dedupe([item for result in selected for item in result.claim_ids]),
    )


def source_metadata(goal_id: str, *, goals: list[Any], artifacts: dict[str, Any]) -> tuple[str | None, str | None]:
    allowed = {goal_id, *dependency_closure(goal_id, goals)}
    envelopes = artifacts.get("artifact_envelopes") if isinstance(artifacts.get("artifact_envelopes"), dict) else {}
    freshness: list[str] = []
    generated: list[str] = []
    for envelope in envelopes.values():
        lineage = _mapping(_value(envelope, "lineage"))
        if not allowed.intersection(str(item) for item in lineage.get("created_from_goal_ids") or []):
            continue
        manifest = _mapping(_value(envelope, "manifest"))
        value = str(manifest.get("freshness") or "").strip()
        if value:
            freshness.append(value)
        timestamp = str(manifest.get("created_at") or _value(envelope, "created_at") or "").strip()
        if timestamp:
            generated.append(timestamp)
    if not freshness:
        goal = next((_mapping(item) for item in goals if str(_value(item, "goal_id") or "") == goal_id), {})
        source_freshness = str(goal.get("source_freshness") or "").strip()
        if source_freshness and source_freshness != "unknown":
            freshness.append(source_freshness)
        for key in _CAPABILITY_ARTIFACT_KEYS.get(_capability_for_goal(goal_id, goals), ()):
            value = _mapping(artifacts.get(key))
            freshness_value = str(value.get("freshness") or "").strip()
            generated_value = str(value.get("generated_at") or value.get("created_at") or value.get("latest_sample_time") or "").strip()
            if freshness_value:
                freshness.append(freshness_value)
            if generated_value:
                generated.append(generated_value)
    return (freshness[0] if freshness else None, generated[0] if generated else None)


def _terminal_status(
    goal: dict[str, Any],
    *,
    scoped_nodes: list[Any],
    artifacts: dict[str, Any],
    terminal: dict[str, Any],
    invocation_status: str,
) -> str:
    if str(goal.get("authorization_status") or "") == "denied":
        return "denied"
    terminal_value = str(terminal.get("status") or "")
    if terminal_value in {"completed", "failed", "blocked", "denied"}:
        return terminal_value
    if terminal_value == "satisfied" or str(goal.get("readiness_status") or "") == "satisfied_by_artifact":
        return "completed"
    if str(goal.get("readiness_status") or "").startswith("blocked_"):
        return "blocked"
    statuses = {str(_value(item, "status") or "") for item in scoped_nodes}
    if "failed" in statuses:
        return "failed"
    if statuses == {"skipped"} and any(
        _mapping(_value(item, "error")).get("code") == "skipped_condition_not_met"
        for item in scoped_nodes
    ):
        return "skipped"
    if statuses.intersection({"blocked", "skipped", "cancelled"}):
        return "blocked"
    if statuses and statuses <= {"completed"}:
        if str(goal.get("capability") or "") == "evaluate_workorder_need" and any(
            _mapping(_value(item, "output")).get("need_assessment") for item in scoped_nodes
        ):
            return "completed"
        return "completed" if _artifact_available(str(goal.get("capability") or ""), artifacts) else "failed"
    if _artifact_available(str(goal.get("capability") or ""), artifacts):
        return "completed"
    if invocation_status in {"blocked", "cancelled"}:
        return "blocked"
    return "failed"


def _artifact_available(capability: str, artifacts: dict[str, Any]) -> bool:
    for key in _CAPABILITY_ARTIFACT_KEYS.get(capability, ()):
        value = artifacts.get(key)
        if value is None:
            continue
        mapped = _mapping(value)
        if mapped.get("success") is False:
            continue
        if key == "workorder_suggestion" and not mapped.get("need_workorder") and not mapped.get("lifecycle_status"):
            continue
        return True
    return False


def _artifact_ids_by_goal(artifacts: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    envelopes = artifacts.get("artifact_envelopes") if isinstance(artifacts.get("artifact_envelopes"), dict) else {}
    for artifact_id, envelope in envelopes.items():
        lineage = _mapping(_value(envelope, "lineage"))
        for goal_id in lineage.get("created_from_goal_ids") or []:
            result.setdefault(str(goal_id), []).append(str(artifact_id))
    return result


def _direct_artifact_ids(capability: str, artifacts: dict[str, Any]) -> list[str]:
    values: list[str] = []
    if capability == "check_runtime_status":
        values.extend(str(item) for item in artifacts.get("sql_artifact_ids") or [])
    for key in _CAPABILITY_ARTIFACT_KEYS.get(capability, ()):
        artifact = artifacts.get(key)
        artifact_id = str(_value(artifact, "artifact_id") or "")
        if artifact_id:
            values.append(artifact_id)
    return _dedupe(values)


def _evidence_refs_by_goal(bundle: EvidenceBundle | None) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    evidence: dict[str, list[str]] = {}
    claims: dict[str, list[str]] = {}
    if bundle is None:
        return evidence, claims
    for item in bundle.evidence_items:
        goal_ids = list(item.goal_ids or item.metadata.get("goal_ids") or [])
        if item.producer_goal_id:
            goal_ids.append(item.producer_goal_id)
        for goal_id in _dedupe([str(value) for value in goal_ids]):
            evidence.setdefault(goal_id, []).append(item.evidence_id)
    for claim in bundle.claims:
        goal_ids = list(claim.goal_ids)
        if claim.producer_goal_id:
            goal_ids.append(claim.producer_goal_id)
        for goal_id in _dedupe([str(value) for value in goal_ids]):
            claims.setdefault(goal_id, []).append(claim.claim_id)
    return evidence, claims


def _goal_error(scoped_nodes: list[Any], terminal: dict[str, Any]) -> dict[str, Any]:
    for item in scoped_nodes:
        error = _mapping(_value(item, "error"))
        if error:
            return error
    return {"code": terminal.get("error_code"), "message": terminal.get("error_message")}


def _goal_ids(value: Any) -> list[str]:
    values = _value(value, "goal_ids") or []
    if not values and _value(value, "goal_id"):
        values = [_value(value, "goal_id")]
    return [str(item) for item in values if str(item)]


def _capability_for_goal(goal_id: str, goals: list[Any]) -> str:
    return next((str(_value(goal, "capability") or "") for goal in goals if str(_value(goal, "goal_id") or "") == goal_id), "")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return {}


def _value(value: Any, field: str) -> Any:
    return value.get(field) if isinstance(value, dict) else getattr(value, field, None)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
