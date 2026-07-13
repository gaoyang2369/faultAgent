"""Read-only observations used by the staged Agent Engine V2 cutover."""

from __future__ import annotations

import hashlib
from typing import Any


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
    selected_content: str,
) -> dict[str, Any]:
    return {
        "requested_goal_ids": [_value(goal, "goal_id") for goal in goals if _value(goal, "goal_id")],
        "deliverables": [
            {
                "goal_id": _value(item, "goal_id"),
                "deliverable_type": _value(item, "deliverable_type"),
                "status": _value(item, "status"),
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


def summarize_runtime_artifacts(artifacts: dict[str, Any], envelopes: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for artifact_id, envelope in (envelopes or {}).items():
        manifest = _mapping(_value(envelope, "manifest"))
        lineage = _mapping(_value(envelope, "lineage"))
        summaries.append(
            {
                "artifact_id": str(artifact_id),
                "artifact_type": _value(envelope, "artifact_type"),
                "devices": list(lineage.get("subject_device_refs") or manifest.get("device_refs") or []),
                "source_tables": list(lineage.get("source_tables") or []),
                "source_table_origins": [
                    {"value": table, "source_field": "payload.sql_artifact.source_table"}
                    for table in lineage.get("source_tables") or []
                ],
                "source_artifact_ids": list(lineage.get("source_artifact_ids") or []),
                "lineage_status": lineage.get("lineage_status"),
            }
        )
    if summaries:
        return summaries
    for key, value in artifacts.items():
        if not key.endswith("_artifact") or value is None:
            continue
        dumped = _mapping(value)
        summaries.append(
            {
                "artifact_id": dumped.get("artifact_id"),
                "artifact_type": key,
                "source_tables": [dumped["source_table"]] if dumped.get("source_table") else [],
                "source_table_origins": (
                    [{"value": dumped["source_table"], "source_field": f"RuntimeState.artifacts.{key}.source_table"}]
                    if dumped.get("source_table")
                    else []
                ),
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
