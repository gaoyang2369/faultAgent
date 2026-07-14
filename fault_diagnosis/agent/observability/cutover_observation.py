"""Read-only observations used by the staged Agent Engine V2 cutover."""

from __future__ import annotations

import hashlib
from typing import Any

from fault_diagnosis.agent.output.legacy_projection import legacy_deliverable_type


def content_fingerprint(value: Any) -> dict[str, Any]:
    text = str(value or "")
    return {
        "length": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def build_output_observation(
    *,
    goals: list[Any],
    deliverables: list[Any],
    goal_execution_results: list[Any],
    answer_variant: str,
    selected_content: str,
) -> dict[str, Any]:
    requested = [_value(goal, "goal_id") for goal in goals if _value(goal, "goal_id")]
    user_requested = [
        _value(goal, "goal_id")
        for goal in goals
        if _value(goal, "goal_id") and _is_user_requested(goal)
    ]
    dependencies = [
        _value(goal, "goal_id")
        for goal in goals
        if _value(goal, "goal_id") and not _is_user_requested(goal)
    ]
    execution_by_goal = {_value(item, "goal_id"): item for item in goal_execution_results}
    executed = [
        goal_id
        for goal_id in requested
        if (
            _value(execution_by_goal.get(goal_id), "executed_node_ids")
            or (
                _value(execution_by_goal.get(goal_id), "status") == "completed"
                and not _value(execution_by_goal.get(goal_id), "satisfied_by_artifact")
            )
        )
    ]
    capability_by_goal = {_value(goal, "goal_id"): _value(goal, "capability") for goal in goals}
    deliverable_goal_ids = [_value(item, "goal_id") for item in deliverables if _value(item, "goal_id")]
    return {
        "requested_goal_ids": requested,
        "user_requested_goal_ids": user_requested,
        "dependency_goal_ids": dependencies,
        "authorized_goal_ids": [
            _value(goal, "goal_id")
            for goal in goals
            if _value(goal, "goal_id") and _value(goal, "authorization_status") != "denied"
        ],
        "ready_goal_ids": [
            _value(goal, "goal_id")
            for goal in goals
            if _value(goal, "goal_id") and (_value(goal, "readiness_status") or "ready") == "ready"
        ],
        "satisfied_by_artifact_goal_ids": [
            goal_id for goal_id in requested if _value(execution_by_goal.get(goal_id), "satisfied_by_artifact")
        ],
        "executed_goal_ids": executed,
        "completed_goal_ids": _goals_with_status(requested, execution_by_goal, "completed"),
        "failed_goal_ids": _goals_with_status(requested, execution_by_goal, "failed"),
        "blocked_goal_ids": _goals_with_status(requested, execution_by_goal, "blocked"),
        "denied_goal_ids": _goals_with_status(requested, execution_by_goal, "denied"),
        "executed_capabilities": [capability_by_goal.get(goal_id) for goal_id in executed if capability_by_goal.get(goal_id)],
        "goal_execution_results": [_mapping(item) for item in goal_execution_results],
        "deliverable_goal_ids": deliverable_goal_ids,
        "deliverable_status_by_goal": {
            _value(item, "goal_id"): _value(item, "status") for item in deliverables if _value(item, "goal_id")
        },
        "answer_variant": answer_variant,
        "primary_execution_capability": "composite" if len(user_requested) > 1 else (
            capability_by_goal.get(user_requested[0], "") if user_requested else ""
        ),
        "compatibility_only": True,
        "deliverables": [
            {
                "goal_id": _value(item, "goal_id"),
                "capability": _value(item, "capability"),
                "deliverable_type": legacy_deliverable_type(str(_value(item, "capability") or "")),
                "status": _value(item, "status"),
                "compatibility_only": True,
            }
            for item in deliverables
        ],
        "renderer_calls": ["DeliverableAssembler.assemble", "CompositePresenter.render"],
        "legacy_answer_template": "",
        "answer_template": "composite_presenter_v1",
        "selected_content": "composite",
        "content_fingerprints": {
            "composite": content_fingerprint(selected_content),
            "selected": content_fingerprint(selected_content),
        },
    }


def _is_user_requested(goal: Any) -> bool:
    origin = str(_value(goal, "origin") or "")
    return origin == "explicit" or (origin == "inferred" and _value(goal, "user_visible") is True)


def _goals_with_status(goal_ids: list[str], by_goal: dict[str, Any], status: str) -> list[str]:
    return [goal_id for goal_id in goal_ids if _value(by_goal.get(goal_id), "status") == status]


def summarize_runtime_artifacts(artifact_registry: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for artifact_id, envelope in artifact_registry.items():
        manifest = _mapping(_value(envelope, "manifest"))
        lineage = _mapping(_value(envelope, "lineage"))
        payload_type = _value(_value(envelope, "payload"), "payload_type")
        summaries.append(
            {
                "artifact_id": str(artifact_id),
                "artifact_type": _value(envelope, "artifact_type"),
                "payload_type": payload_type,
                "devices": list(lineage.get("subject_device_refs") or manifest.get("device_refs") or []),
                "source_tables": list(lineage.get("source_tables") or []),
                "source_table_origins": [
                    {
                        "value": table,
                        "source_field": (
                            "payload.sql_artifact.source_table"
                            if payload_type == "sql_artifact"
                            else "lineage.source_tables"
                        ),
                    }
                    for table in lineage.get("source_tables") or []
                ],
                "source_artifact_ids": list(lineage.get("source_artifact_ids") or []),
                "lineage_status": lineage.get("lineage_status"),
            }
        )
    return summaries


def _value(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return {}
