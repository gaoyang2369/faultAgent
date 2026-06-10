"""Workflow thin adapters over existing tools and legacy diagnosis outputs."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from ..quality.governance import build_governance_snapshot
from .artifact_store import save_thread_artifact
from .contracts import EvidenceItem, WorkflowArtifactEnvelope, WorkflowType


def _stringify_tool_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


async def invoke_tool(tool: Any, payload: Any) -> Any:
    """Invoke a LangChain tool through a unified async interface."""

    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(payload)
    if hasattr(tool, "invoke"):
        return await asyncio.to_thread(tool.invoke, payload)
    return await asyncio.to_thread(tool, payload)


def get_current_time_text() -> str:
    """Get the current time text using the existing utility tool."""

    from ..tools.utility_tools import get_time

    return get_time.invoke({}) if hasattr(get_time, "invoke") else get_time()


def query_knowledge_text(query: str) -> str:
    """Query the knowledge-base tool and return plain text."""

    from ..tools.kb_tools import query_knowledge_base

    result = query_knowledge_base.invoke({"query": query}) if hasattr(query_knowledge_base, "invoke") else query_knowledge_base(query)
    return _stringify_tool_output(result)


def build_sql_tools_map() -> dict[str, Any]:
    """Build a name -> SQL tool mapping."""

    from ..tools.sql_tools import get_sqltools

    tools_map: dict[str, Any] = {}
    for tool in get_sqltools():
        tool_name = getattr(tool, "name", "").strip()
        if tool_name:
            tools_map[tool_name] = tool
    return tools_map


def find_sql_tool(tools_map: dict[str, Any], tool_name: str, required: bool = True) -> Any:
    """Find a SQL tool by name."""

    tool = tools_map.get(tool_name)
    if tool is None and required:
        available = ", ".join(sorted(tools_map))
        raise RuntimeError(f"未找到 SQL 工具：{tool_name}，当前可用工具：{available}")
    return tool


def save_markdown_report_from_analysis(
    *,
    title: str,
    report_time: str,
    diagnosis_object: str,
    diagnosis_type: str,
    executive_summary: str,
    diagnosis_overview: str,
    diagnosis_details: str,
    fault_inference: str,
    repair_recommendations: str,
    preventive_maintenance: str,
    diagnosis_basis: str,
    report_filename: str,
    report_gate_summary: dict[str, Any] | None = None,
    findings_snapshot: list[dict[str, Any]] | None = None,
    finding_links_snapshot: list[dict[str, Any]] | None = None,
    evidence_records_snapshot: list[dict[str, Any]] | None = None,
) -> str:
    """Save a markdown report through the existing report tool."""

    from ..tools.report_tools import save_report

    result = save_report.invoke(
        {
            "title": title,
            "report_time": report_time,
            "diagnosis_object": diagnosis_object,
            "diagnosis_type": diagnosis_type,
            "executive_summary": executive_summary,
            "diagnosis_overview": diagnosis_overview,
            "diagnosis_details": diagnosis_details,
            "fault_inference": fault_inference,
            "repair_recommendations": repair_recommendations,
            "preventive_maintenance": preventive_maintenance,
            "diagnosis_basis": diagnosis_basis,
            "report_filename": report_filename,
            "report_gate_summary": report_gate_summary or {},
            "findings_snapshot": findings_snapshot or [],
            "finding_links_snapshot": finding_links_snapshot or [],
            "evidence_records_snapshot": evidence_records_snapshot or [],
        }
    )
    return _stringify_tool_output(result)


_FAULT_CODE_RE = re.compile(r"\b([A-Z]\d{4,})\b")
_DEVICE_RE = re.compile(r"\b([A-Z]{2,}(?:-\d{1,})+)\b")


def _guess_fault_code(message: str, findings: list[dict[str, Any]], evidences: list[dict[str, Any]]) -> str | None:
    sources = [message]
    sources.extend(item.get("text", "") for item in findings if isinstance(item, dict))
    sources.extend(item.get("title", "") for item in evidences if isinstance(item, dict))
    sources.extend(item.get("summary", "") for item in evidences if isinstance(item, dict))
    for source in sources:
        matched = _FAULT_CODE_RE.search(source or "")
        if matched:
            return matched.group(1)
    return None


def _guess_equipment_hint(message: str, evidences: list[dict[str, Any]]) -> str | None:
    sources = [message]
    sources.extend(item.get("summary", "") for item in evidences if isinstance(item, dict))
    for source in sources:
        matched = _DEVICE_RE.search(source or "")
        if matched:
            candidate = matched.group(1)
            if candidate.upper() not in {"SQL", "RAG"}:
                return candidate
    return None


def _collect_sql_artifact(evidences: list[dict[str, Any]]) -> dict[str, Any]:
    sql_records = [item for item in evidences if isinstance(item, dict) and item.get("type") == "sql"]
    if not sql_records:
        return {
            "summary": "未记录到 SQL 证据",
            "sql_used": [],
            "result_preview": "",
            "raw_output": "",
        }

    queries: list[str] = []
    previews: list[str] = []
    for item in sql_records:
        metadata = item.get("metadata") or {}
        query = _compact_text(metadata.get("query"))
        if query and query not in queries:
            queries.append(query)
        preview = _compact_text(item.get("summary"))
        if preview and preview not in previews:
            previews.append(preview)

    joined_preview = "\n".join(previews[:3])
    return {
        "summary": f"共记录 {len(sql_records)} 条 SQL 证据",
        "sql_used": queries,
        "result_preview": joined_preview,
        "raw_output": joined_preview,
    }


def _collect_knowledge_artifact(evidences: list[dict[str, Any]]) -> dict[str, Any]:
    knowledge_records = [item for item in evidences if isinstance(item, dict) and item.get("type") == "rag"]
    if not knowledge_records:
        return {
            "query": "",
            "snippets": [],
            "raw_output": "",
            "success": False,
        }

    snippets: list[str] = []
    query = ""
    for item in knowledge_records:
        metadata = item.get("metadata") or {}
        if not query:
            query = _compact_text(metadata.get("query"))
        summary = _compact_text(item.get("summary"))
        if summary and summary not in snippets:
            snippets.append(summary)

    return {
        "query": query,
        "snippets": snippets[:5],
        "raw_output": "\n".join(snippets[:3]),
        "success": True,
    }


def _collect_analysis_artifact(
    final_content: str,
    findings: list[dict[str, Any]],
    evidence_quality: dict[str, Any],
) -> dict[str, Any]:
    basis = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        text = _compact_text(finding.get("text"))
        if text and text not in basis:
            basis.append(text)

    confidence = "medium"
    if evidence_quality.get("gate") == "pass":
        confidence = "high"
    elif evidence_quality.get("gate") == "blocked":
        confidence = "low"

    return {
        "conclusion": _compact_text(final_content),
        "basis": basis[:5],
        "recommendations": [],
        "confidence": confidence,
        "risk_notice": _compact_text(evidence_quality.get("gate")),
    }


def _map_importance(record: dict[str, Any]) -> str:
    evidence_type = str(record.get("type") or "")
    if evidence_type in {"sql", "report", "action"}:
        return "high"
    if evidence_type == "rag":
        return "medium"
    return "low"


def _build_workflow_evidence_items(evidences: list[dict[str, Any]]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for record in evidences:
        if not isinstance(record, dict):
            continue
        items.append(
            EvidenceItem(
                source_type=str(record.get("type") or "generic"),
                title=_compact_text(record.get("title")) or "未命名证据",
                content=_compact_text(record.get("summary")) or _compact_text(record.get("source")),
                importance=_map_importance(record),
            )
        )
    return items


def save_legacy_diagnosis_artifact(
    *,
    thread_id: str,
    user_message: str,
    user_identity: str,
    final_content: str,
    findings: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]],
    evidence_quality: dict[str, Any],
    finding_links: list[dict[str, Any]] | None = None,
    workflow_stage_details: list[dict[str, Any]] | None = None,
    route_result: Any = None,
) -> WorkflowArtifactEnvelope:
    """Persist the legacy diagnosis output as a workflow artifact envelope."""

    fault_code_hint = _guess_fault_code(user_message, findings, evidence_records)
    equipment_hint = _guess_equipment_hint(user_message, evidence_records)
    sql_artifact = _collect_sql_artifact(evidence_records)
    knowledge_artifact = _collect_knowledge_artifact(evidence_records)
    analysis_artifact = _collect_analysis_artifact(final_content, findings, evidence_quality)
    if route_result is None:
        route_result = {
            "workflow_type": WorkflowType.FAULT_DIAGNOSIS.value,
            "confidence": "high",
            "reason": "legacy 主链路已实际执行故障诊断流程",
            "needs_sql": True,
            "needs_knowledge": True,
            "needs_report": True,
        }
    governance = build_governance_snapshot(
        route_result=route_result,
        evidence_quality=evidence_quality,
        findings=findings,
    )

    envelope = WorkflowArtifactEnvelope(
        workflow_type=WorkflowType.FAULT_DIAGNOSIS,
        thread_id=thread_id,
        created_at=datetime.now().isoformat(),
        request_summary=_compact_text(user_message),
        final_answer=_compact_text(final_content),
        payload={
            "request": {
                "user_message": _compact_text(user_message),
                "user_identity": _compact_text(user_identity),
                "equipment_hint": equipment_hint,
                "fault_code_hint": fault_code_hint,
                "analysis_goal": _compact_text(user_message),
                "needs_report": True,
                "report_format": "markdown",
            },
            "sql_artifact": sql_artifact,
            "knowledge_artifact": knowledge_artifact,
            "analysis_artifact": analysis_artifact,
            "governance": governance,
            "legacy_bridge": {
                "source": "legacy_streaming_complete",
                "workflow_stage_details": workflow_stage_details or [],
                "route_result": governance.get("route_result") or {},
                "evidence_quality": evidence_quality,
                "findings": findings,
                "finding_links": finding_links or [],
                "evidence_records": evidence_records,
            },
        },
        evidence=_build_workflow_evidence_items(evidence_records),
    )
    return save_thread_artifact(envelope)
