"""Tool-event helpers for evidence registration and SSE payload shaping."""

from __future__ import annotations

from typing import Any

from ..quality.evidence import (
    build_evidence_preview,
    consume_tool_evidence,
    register_artifact_tool_evidence,
    register_chart_tool_evidence,
    register_sql_tool_evidence,
)
from ..common.utils import safe_json_dumps, sanitize_for_json
from .workflow_runtime import upsert_stage_detail


def register_tool_runtime_evidence(
    tool_name: str,
    tool_input: Any,
    tool_output: Any,
) -> list[dict[str, Any]]:
    """Normalize per-tool evidence registration into one reusable path."""
    register_sql_tool_evidence(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
    )
    if tool_name == "fig_inter":
        register_chart_tool_evidence(
            tool_name=tool_name,
            tool_output=tool_output,
        )
    else:
        register_artifact_tool_evidence(
            tool_name=tool_name,
            tool_output=tool_output,
        )
    return consume_tool_evidence(tool_name)


def extract_action_guard(tool_evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first action guard found in tool evidence metadata."""
    for record in tool_evidence:
        metadata = record.get("metadata") or {}
        if isinstance(metadata, dict) and metadata.get("action_guard"):
            action_guard = metadata.get("action_guard")
            if isinstance(action_guard, dict):
                return action_guard
    return None


def touch_tool_stage_detail(
    workflow_stage_details: dict[str, dict[str, Any]],
    tool_stage: str,
    *,
    now_ms: float,
) -> dict[str, Any]:
    """Update workflow-stage aggregate metrics after one tool finishes."""
    stage_detail = upsert_stage_detail(
        workflow_stage_details,
        tool_stage,
        started_at_ms=now_ms,
    )
    stage_detail["status"] = "active"
    stage_detail["tool_count"] += 1
    stage_detail["duration_ms"] = round(
        now_ms - stage_detail["started_at_ms"],
        1,
    ) if stage_detail["started_at_ms"] is not None else None
    return stage_detail


def build_tool_end_payload(
    *,
    tool_name: str,
    tool_stage: str,
    current_workflow_stage: str | None,
    tool_output: Any,
    tool_run_id: str | None = None,
    trace_id: str | None = None,
    stage_duration_ms: float | None,
    tool_evidence: list[dict[str, Any]],
    max_serialized_chars: int = 6000,
    preview_chars: int = 4000,
) -> dict[str, Any]:
    """Build a safe tool-end payload with evidence preview and truncation fallback."""
    payload: dict[str, Any] = {
        "type": "tool_end",
        "tool": tool_name,
        "result": sanitize_for_json(tool_output),
        "stage": tool_stage,
        "current_stage": current_workflow_stage,
        "stage_duration_ms": stage_duration_ms,
        "evidence_ids": [],
    }
    if tool_run_id:
        payload["run_id"] = tool_run_id
    if trace_id:
        payload["trace_id"] = trace_id

    evidence_preview = build_evidence_preview(tool_evidence)
    if evidence_preview:
        payload["evidence"] = evidence_preview
        payload["evidence_count"] = len(tool_evidence)
        payload["evidence_ids"] = [
            item.get("evidence_id")
            for item in tool_evidence
            if isinstance(item, dict) and item.get("evidence_id")
        ]

    action_guard = extract_action_guard(tool_evidence)
    if action_guard:
        payload["action_guard"] = action_guard

    try:
        serialized = safe_json_dumps(payload)
    except Exception:
        serialized = None

    if serialized is not None and len(serialized) <= max_serialized_chars:
        return payload

    compact_payload: dict[str, Any] = {
        "type": "tool_end",
        "tool": tool_name,
        "stage": tool_stage,
        "current_stage": current_workflow_stage,
        "stage_duration_ms": stage_duration_ms,
        "result_preview": f"{str(tool_output)[:preview_chars]}...(truncated)",
        "truncated": True,
        "evidence_ids": [],
    }
    if tool_run_id:
        compact_payload["run_id"] = tool_run_id
    if trace_id:
        compact_payload["trace_id"] = trace_id
    if evidence_preview:
        compact_payload["evidence"] = evidence_preview
        compact_payload["evidence_count"] = len(tool_evidence)
        compact_payload["evidence_ids"] = [
            item.get("evidence_id")
            for item in tool_evidence
            if isinstance(item, dict) and item.get("evidence_id")
        ]
    if action_guard:
        compact_payload["action_guard"] = action_guard
    return compact_payload


def build_tool_start_payload(
    *,
    tool_name: str,
    tool_input: Any,
    tool_stage: str,
    current_workflow_stage: str | None,
    tool_run_id: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Build a standardized tool-start SSE payload."""
    payload: dict[str, Any] = {
        "type": "tool_start",
        "tool": tool_name,
        "input": sanitize_for_json(tool_input),
        "stage": tool_stage,
        "current_stage": current_workflow_stage,
    }
    if tool_run_id:
        payload["run_id"] = tool_run_id
    if trace_id:
        payload["trace_id"] = trace_id
    return payload
