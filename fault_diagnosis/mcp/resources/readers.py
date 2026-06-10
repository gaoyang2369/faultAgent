"""Resource readers for first-batch MCP outputs."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from ...paths import REPORTS_DIR
from ...workflows.artifact_store import get_thread_artifact
from ..errors import McpErrorCode, McpProtocolError
from .store import get_resource_content

_FILENAME_RE = re.compile(r"filename=([A-Za-z0-9._\-]+\.(?:md|html))", re.IGNORECASE)


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _parse_filename(payload: dict[str, Any]) -> str | None:
    explicit = _compact(payload.get("filename"))
    if explicit:
        return explicit
    uri = _compact(payload.get("uri"))
    matched = _FILENAME_RE.search(uri)
    return matched.group(1) if matched else None


def _load_thread_artifact(thread_id: str):
    envelope = get_thread_artifact(thread_id)
    if envelope is None:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"未找到 thread_id={thread_id} 对应的结构化产物",
            details={"thread_id": thread_id},
        )
    return envelope


def read_diagnosis_report_markdown(payload: dict[str, Any]) -> str:
    thread_id = _compact(payload.get("thread_id"))
    key = _compact(payload.get("key")) or thread_id
    cached = get_resource_content("diagnosis_report_markdown", key)
    if cached is not None:
        return cached if isinstance(cached, str) else json.dumps(cached, ensure_ascii=False, indent=2)

    filename = _parse_filename(payload)
    if not filename and thread_id:
        envelope = _load_thread_artifact(thread_id)
        filename = _compact(envelope.report_filename)
        if not filename:
            raise McpProtocolError(
                code=McpErrorCode.DATA_NOT_FOUND,
                message="当前线程还没有可读取的报告文件",
                details={"thread_id": thread_id},
            )
    if not filename:
        raise McpProtocolError(
            code=McpErrorCode.INVALID_ARGUMENT,
            message="读取报告资源时需要提供 thread_id、filename 或 uri",
            details={"resource_name": "diagnosis_report_markdown"},
        )

    path = os.path.join(REPORTS_DIR, filename)
    if not os.path.exists(path):
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"报告文件不存在：{filename}",
            details={"filename": filename},
        )
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def read_fault_knowledge_reference(payload: dict[str, Any]) -> dict[str, Any]:
    key = _compact(payload.get("key") or payload.get("trace_id") or payload.get("run_id") or payload.get("thread_id"))
    cached = get_resource_content("fault_knowledge_reference", key) if key else None
    if cached is not None:
        return cached

    thread_id = _compact(payload.get("thread_id"))
    if not thread_id:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message="未找到可读取的知识检索资源",
            details={"resource_name": "fault_knowledge_reference"},
        )
    envelope = _load_thread_artifact(thread_id)
    knowledge_artifact = (envelope.payload or {}).get("knowledge_artifact") or {}
    return {
        "thread_id": thread_id,
        "query": knowledge_artifact.get("query") or "",
        "snippets": list(knowledge_artifact.get("snippets") or []),
        "raw_output": knowledge_artifact.get("raw_output") or "",
        "success": bool(knowledge_artifact.get("success")),
    }


def read_diagnosis_evidence_summary(payload: dict[str, Any]) -> dict[str, Any]:
    key = _compact(payload.get("key") or payload.get("trace_id") or payload.get("run_id") or payload.get("thread_id"))
    cached = get_resource_content("diagnosis_evidence_summary", key) if key else None
    if cached is not None:
        return cached

    thread_id = _compact(payload.get("thread_id"))
    if not thread_id:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message="未找到可读取的证据摘要资源",
            details={"resource_name": "diagnosis_evidence_summary"},
        )
    envelope = _load_thread_artifact(thread_id)
    payload_data = envelope.payload or {}
    return {
        "thread_id": thread_id,
        "workflow_type": str(envelope.workflow_type),
        "request_summary": envelope.request_summary,
        "governance": payload_data.get("governance") or {},
        "report_gate_summary": payload_data.get("report_gate_summary") or {},
        "findings_snapshot": list(payload_data.get("findings_snapshot") or []),
        "finding_links_snapshot": list(payload_data.get("finding_links_snapshot") or []),
        "evidence_records_snapshot": list(payload_data.get("evidence_records_snapshot") or []),
        "evidence": [item.model_dump() for item in envelope.evidence],
    }
