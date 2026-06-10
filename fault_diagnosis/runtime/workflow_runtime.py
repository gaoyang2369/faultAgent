"""Workflow-stage helpers used by the agent runtime."""

from __future__ import annotations

from typing import Any

TOOL_STAGE_MAP = {
    "get_time": "collect",
    "write_todos": "collect",
    "sql_db_query": "collect",
    "sql_db_schema": "collect",
    "sql_inter": "collect",
    "extract_data": "collect",
    "query_knowledge_base": "retrieve",
    "search_tool": "retrieve",
    "fault_explanation_tool": "analyze",
    "fig_inter": "analyze",
    "save_report": "report",
    "save_html_report": "report",
    "create_work_order": "report",
}

WORKFLOW_STAGE_ORDER = ("collect", "retrieve", "analyze", "report")


def resolve_tool_stage(tool_name: str) -> str:
    return TOOL_STAGE_MAP.get((tool_name or "").strip(), "analyze")


def append_workflow_stage(workflow_stages: list[str], stage: str) -> None:
    if stage and stage not in workflow_stages:
        workflow_stages.append(stage)


def upsert_stage_detail(
    workflow_stage_details: dict[str, dict[str, Any]],
    stage: str,
    *,
    started_at_ms: float | None = None,
) -> dict[str, Any]:
    detail = workflow_stage_details.get(stage)
    if detail is None:
        detail = {
            "stage": stage,
            "status": "pending",
            "started_at_ms": started_at_ms,
            "ended_at_ms": None,
            "duration_ms": None,
            "tool_count": 0,
        }
        workflow_stage_details[stage] = detail
    elif started_at_ms is not None and detail.get("started_at_ms") is None:
        detail["started_at_ms"] = started_at_ms
    return detail


def activate_stage(
    workflow_stages_seen: list[str],
    workflow_stage_details: dict[str, dict[str, Any]],
    stage: str,
    now_ms: float,
) -> None:
    append_workflow_stage(workflow_stages_seen, stage)
    detail = upsert_stage_detail(
        workflow_stage_details,
        stage,
        started_at_ms=now_ms,
    )
    detail["status"] = "active"
    detail["ended_at_ms"] = None
    detail["duration_ms"] = round(now_ms - detail["started_at_ms"], 1) if detail["started_at_ms"] is not None else None


def complete_stage(
    workflow_stage_details: dict[str, dict[str, Any]],
    stage: str,
    now_ms: float,
) -> None:
    detail = workflow_stage_details.get(stage)
    if detail is None:
        detail = upsert_stage_detail(
            workflow_stage_details,
            stage,
            started_at_ms=now_ms,
        )
    detail["status"] = "completed"
    detail["ended_at_ms"] = now_ms
    detail["duration_ms"] = round(now_ms - detail["started_at_ms"], 1) if detail["started_at_ms"] is not None else 0.0


def build_workflow_stage_details(
    workflow_stages_seen: list[str],
    workflow_stage_details: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for stage in WORKFLOW_STAGE_ORDER:
        if stage in workflow_stages_seen and stage in workflow_stage_details:
            ordered.append(dict(workflow_stage_details[stage]))
    for stage in workflow_stages_seen:
        if stage not in WORKFLOW_STAGE_ORDER and stage in workflow_stage_details:
            ordered.append(dict(workflow_stage_details[stage]))
    return ordered
