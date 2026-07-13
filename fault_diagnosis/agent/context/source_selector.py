"""Goal-scoped selection of exact, verified historical artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from fault_diagnosis.agent.contracts import ArtifactManifest, EffectiveGoal, SourceBinding


_ALLOWED_TYPES: dict[str, tuple[str, ...]] = {
    "explain_fault_code": ("knowledge_artifact",),
    "check_runtime_status": ("sql_artifact",),
    "diagnose_fault": ("analysis_artifact", "sql_artifact"),
    "resolution_recommendation": ("analysis_artifact", "sql_artifact"),
    "generate_report": ("analysis_artifact", "comparison_artifact", "sql_artifact"),
    "create_workorder_draft": ("report_artifact", "analysis_artifact"),
    "confirm_workorder_draft": ("workorder_artifact",),
}


@dataclass(frozen=True)
class GoalSourceSelection:
    binding: SourceBinding | None
    manifest: ArtifactManifest | None
    status: str
    observation: dict[str, object] = field(default_factory=dict)


class GoalScopedSourceSelector:
    """Resolve one source per Goal without latest/focus or list-order guesses."""

    def select(
        self,
        *,
        goal: EffectiveGoal,
        manifests: list[ArtifactManifest],
        explicit_artifact_id: str | None,
        expected_devices: list[str],
        thread_id: str,
    ) -> GoalSourceSelection:
        allowed_types = _ALLOWED_TYPES.get(goal.capability, ())
        verified, rejected = _verified_candidates(manifests, expected_devices=expected_devices)
        by_id = {item.artifact_id: item for item in verified}
        explicit = by_id.get(str(explicit_artifact_id or ""))
        selected: ArtifactManifest | None = None
        reason = ""
        lineage_ancestor = ""
        status = "not_required"

        if not allowed_types or not _policy_allows_reuse(goal.source_policy):
            status = "collect_new"
            reason = "source_policy_requires_collection"
        elif explicit is not None and explicit.artifact_type in allowed_types:
            selected = explicit
            status = "selected"
            reason = "explicit_compatible_reference"
        elif explicit is not None:
            ancestors = _compatible_ancestors(explicit, by_id=by_id, allowed_types=set(allowed_types))
            if len(ancestors) == 1:
                selected = ancestors[0]
                status = "selected"
                reason = "unique_compatible_lineage_ancestor"
                lineage_ancestor = selected.artifact_id
            elif len(ancestors) > 1:
                status = "ambiguous"
                reason = "multiple_compatible_lineage_ancestors"
            else:
                status = "unresolved"
                reason = "explicit_reference_has_no_compatible_ancestor"
        else:
            compatible = [item for item in verified if item.artifact_type in allowed_types]
            if len(compatible) == 1:
                selected = compatible[0]
                status = "selected"
                reason = "unique_compatible_candidate"
            elif len(compatible) > 1:
                status = "ambiguous"
                reason = "multiple_compatible_candidates"
            else:
                status = "unresolved"
                reason = "no_compatible_candidate"

        binding = None
        reusable_id = None
        idempotency_key = None
        if selected is not None:
            if goal.capability == "create_workorder_draft" and len(expected_devices) == 1:
                reusable = _matching_workorders(
                    verified,
                    source_artifact_id=selected.artifact_id,
                    device=expected_devices[0],
                )
                if len(reusable) == 1:
                    reusable_id = reusable[0].artifact_id
                elif len(reusable) > 1:
                    selected = None
                    status = "ambiguous"
                    reason = "multiple_idempotent_workorder_results"
            if selected is not None:
                idempotency_key = _idempotency_key(
                    thread_id=thread_id or selected.thread_id,
                    capability=goal.capability,
                    devices=expected_devices,
                    source_artifact_id=selected.artifact_id,
                )
                binding = SourceBinding(
                    goal_id=goal.goal_id,
                    artifact_id=selected.artifact_id,
                    artifact_type=selected.artifact_type,
                    thread_id=selected.thread_id or thread_id,
                    lineage_status=selected.lineage.lineage_status,
                    persistence_status=selected.persistence_status,
                    readback_verified=selected.readback_verified,
                    source_policy=goal.source_policy,
                    selection_reason=reason,
                    reusable_result_artifact_id=reusable_id,
                    idempotency_key=idempotency_key,
                )

        selected_id = selected.artifact_id if selected is not None else ""
        observation = {
            "stage": "source.select.goal",
            "selector": "GoalScopedSourceSelector.select",
            "goal_id": goal.goal_id,
            "capability": goal.capability,
            "source_policy": goal.source_policy,
            "allowed_types": list(allowed_types),
            "explicit_artifact_id": str(explicit_artifact_id or ""),
            "candidates_before": [_observation(item) for item in manifests],
            "candidates_after": [_observation(item) for item in verified],
            "selected_artifact_id": selected_id,
            "selected_artifact_type": selected.artifact_type if selected is not None else "",
            "selection_status": status,
            "selection_reason": reason,
            "lineage_ancestor": lineage_ancestor,
            "reusable_result_artifact_id": reusable_id or "",
            "idempotency_key": idempotency_key or "",
            "rejected": rejected
            + [
                {"artifact_id": item.artifact_id, "reason": "type_not_allowed_for_goal"}
                for item in verified
                if item.artifact_id != selected_id and item.artifact_type not in allowed_types
            ],
        }
        return GoalSourceSelection(binding=binding, manifest=selected, status=status, observation=observation)


def allowed_source_types(capability: str) -> tuple[str, ...]:
    return _ALLOWED_TYPES.get(capability, ())


def _policy_allows_reuse(policy: str) -> bool:
    return policy not in {"collect_new", "refresh_runtime_data", "collect_fresh_runtime"}


def _verified_candidates(
    manifests: list[ArtifactManifest],
    *,
    expected_devices: list[str],
) -> tuple[list[ArtifactManifest], list[dict[str, str]]]:
    verified: list[ArtifactManifest] = []
    rejected: list[dict[str, str]] = []
    expected = set(expected_devices)
    for item in manifests:
        reason = ""
        if item.status != "completed" or item.artifact_status != "complete":
            reason = "artifact_not_complete"
        elif item.persistence_status != "committed":
            reason = "artifact_not_committed"
        elif not item.readback_verified:
            reason = "artifact_readback_unverified"
        elif item.lineage.lineage_status != "complete":
            reason = "artifact_lineage_incomplete"
        elif expected and item.device_refs and set(item.device_refs) != expected:
            reason = "device_scope_mismatch"
        if reason:
            rejected.append({"artifact_id": item.artifact_id, "reason": reason})
        else:
            verified.append(item)
    return verified, rejected


def _compatible_ancestors(
    manifest: ArtifactManifest,
    *,
    by_id: dict[str, ArtifactManifest],
    allowed_types: set[str],
) -> list[ArtifactManifest]:
    found: list[ArtifactManifest] = []
    visited: set[str] = set()
    pending = list(manifest.lineage.source_artifact_ids)
    while pending:
        artifact_id = pending.pop(0)
        if artifact_id in visited:
            continue
        visited.add(artifact_id)
        ancestor = by_id.get(artifact_id)
        if ancestor is None:
            continue
        if ancestor.artifact_type in allowed_types:
            found.append(ancestor)
        else:
            pending.extend(ancestor.lineage.source_artifact_ids)
    return found


def _matching_workorders(
    manifests: list[ArtifactManifest],
    *,
    source_artifact_id: str,
    device: str,
) -> list[ArtifactManifest]:
    return [
        item
        for item in manifests
        if item.artifact_type == "workorder_artifact"
        and item.device_refs == [device]
        and source_artifact_id in item.lineage.source_artifact_ids
    ]


def _idempotency_key(
    *,
    thread_id: str,
    capability: str,
    devices: list[str],
    source_artifact_id: str,
) -> str:
    raw = "|".join([thread_id, capability, ",".join(devices), source_artifact_id])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _observation(item: ArtifactManifest) -> dict[str, object]:
    return {
        "artifact_id": item.artifact_id,
        "artifact_type": item.artifact_type,
        "subject_devices": list(item.device_refs),
        "source_artifact_ids": list(item.lineage.source_artifact_ids),
        "lineage_status": item.lineage.lineage_status,
        "persistence_status": item.persistence_status,
        "readback_verified": item.readback_verified,
    }
