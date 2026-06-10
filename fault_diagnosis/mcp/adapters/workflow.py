"""Helpers that map workflow artifacts into MCP schema items."""

from __future__ import annotations

import re
from typing import Any

from ..schemas import (
    McpArtifactItem,
    McpEvidenceItem,
    McpFindingItem,
    McpGovernanceInfo,
    McpResourceReference,
    McpTimelineEntry,
)

_REPORT_URL_RE = re.compile(r"(/reports/([A-Za-z0-9._\-]+\.(?:md|html)))", re.IGNORECASE)


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_report_filename(filename: str | None, default_extension: str = ".md") -> str | None:
    value = _compact(filename)
    if not value:
        return None
    value = value.split("/")[-1].split("\\")[-1]
    if "." not in value:
        value = f"{value}{default_extension}"
    return value


def extract_report_filename(save_result: str | None, fallback: str | None = None) -> str | None:
    matched = _REPORT_URL_RE.search(save_result or "")
    if matched:
        return matched.group(2)
    return _normalize_report_filename(fallback)


def build_timeline_entries(steps: list[Any] | None) -> list[McpTimelineEntry]:
    items: list[McpTimelineEntry] = []
    for step in steps or []:
        if hasattr(step, "model_dump"):
            payload = step.model_dump()
        elif isinstance(step, dict):
            payload = step
        else:
            continue
        timestamp = _compact(payload.get("finished_at") or payload.get("started_at"))
        stage = _compact(payload.get("step_name")) or "step"
        status = _compact(payload.get("status")) or "unknown"
        summary = _compact(payload.get("summary")) or stage
        message = f"{summary}（{status}）"
        items.append(McpTimelineEntry(timestamp=timestamp or "unknown", stage=stage, message=message))
    return items


def build_diagnosis_findings(
    findings_snapshot: list[dict[str, Any]] | None,
    *,
    fallback_text: str = "",
    fallback_confidence: str = "unknown",
    severity: str = "medium",
) -> list[McpFindingItem]:
    findings: list[McpFindingItem] = []
    for index, item in enumerate(findings_snapshot or [], start=1):
        if not isinstance(item, dict):
            continue
        text = _compact(item.get("text"))
        if not text:
            continue
        title = _compact(item.get("title")) or text[:32]
        findings.append(
            McpFindingItem(
                finding_id=_compact(item.get("finding_id")) or f"finding_{index}",
                title=title,
                summary=text,
                severity=_compact(item.get("severity")) or severity,
                confidence=_compact(item.get("confidence")) or fallback_confidence,
            )
        )

    if findings:
        return findings

    if _compact(fallback_text):
        return [
            McpFindingItem(
                finding_id="finding_1",
                title=_compact(fallback_text)[:32],
                summary=_compact(fallback_text),
                severity=severity,
                confidence=fallback_confidence,
            )
        ]
    return []


def build_evidence_items(
    envelope_evidence: list[Any] | None,
    evidence_records_snapshot: list[dict[str, Any]] | None = None,
) -> list[McpEvidenceItem]:
    items: list[McpEvidenceItem] = []
    seen_ids: set[str] = set()

    for index, record in enumerate(evidence_records_snapshot or [], start=1):
        if not isinstance(record, dict):
            continue
        evidence_id = _compact(record.get("evidence_id")) or f"ev_snapshot_{index}"
        if evidence_id in seen_ids:
            continue
        seen_ids.add(evidence_id)
        items.append(
            McpEvidenceItem(
                evidence_id=evidence_id,
                source_type=_compact(record.get("type")) or "generic",
                title=_compact(record.get("title")) or "证据",
                summary=_compact(record.get("summary")) or _compact(record.get("source")),
                source_uri=_compact(record.get("raw_ref")) or None,
            )
        )

    for index, item in enumerate(envelope_evidence or [], start=1):
        payload = item.model_dump() if hasattr(item, "model_dump") else item if isinstance(item, dict) else None
        if payload is None:
            continue
        evidence_id = f"ev_envelope_{index}"
        if evidence_id in seen_ids:
            continue
        seen_ids.add(evidence_id)
        items.append(
            McpEvidenceItem(
                evidence_id=evidence_id,
                source_type=_compact(payload.get("source_type")) or "generic",
                title=_compact(payload.get("title")) or "证据",
                summary=_compact(payload.get("content")),
                source_uri=None,
            )
        )
    return items


def build_artifact_items(
    *,
    thread_id: str,
    report_filename: str | None = None,
    workflow_type: str | None = None,
) -> list[McpArtifactItem]:
    items = [
        McpArtifactItem(
            artifact_id=f"artifact_thread_{thread_id}",
            artifact_type="workflow_artifact",
            name="当前线程结构化产物",
            uri=f"artifact://thread/{thread_id}/latest",
            summary=_compact(workflow_type) or "workflow_artifact",
        )
    ]
    if report_filename:
        items.append(
            McpArtifactItem(
                artifact_id=f"artifact_report_{report_filename}",
                artifact_type="report",
                name=report_filename,
                uri=f"reports://thread/{thread_id}/markdown?filename={report_filename}",
                summary="诊断报告文件",
            )
        )
    return items


def build_resource_references(
    *,
    thread_id: str,
    report_filename: str | None = None,
    include_knowledge: bool = False,
    include_evidence_summary: bool = True,
    report_media_type: str = "text/markdown",
    key: str | None = None,
) -> list[McpResourceReference]:
    resource_key = _compact(key) or thread_id
    items: list[McpResourceReference] = []
    if report_filename:
        items.append(
            McpResourceReference(
                uri=f"reports://thread/{thread_id}/markdown?filename={report_filename}",
                name="diagnosis_report_markdown",
                media_type=report_media_type,
                description="诊断报告内容",
            )
        )
    if include_knowledge:
        items.append(
            McpResourceReference(
                uri=f"knowledge://thread/{resource_key}/latest",
                name="fault_knowledge_reference",
                media_type="application/json",
                description="知识检索命中明细",
            )
        )
    if include_evidence_summary:
        items.append(
            McpResourceReference(
                uri=f"evidence://thread/{resource_key}/latest",
                name="diagnosis_evidence_summary",
                media_type="application/json",
                description="证据与门禁摘要",
            )
        )
    return items


def build_governance_info(
    governance_payload: dict[str, Any] | None,
    *,
    emitted_events: list[str] | None = None,
    status: str = "success",
    extra_metadata: dict[str, Any] | None = None,
) -> McpGovernanceInfo:
    payload = dict(governance_payload or {})
    metadata = dict(payload)
    if extra_metadata:
        metadata.update(extra_metadata)
    return McpGovernanceInfo(
        status=status,
        emitted_events=list(emitted_events or []),
        metadata=metadata,
    )
