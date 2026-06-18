"""Knowledge-base evidence construction for the restricted single-agent runtime."""

from __future__ import annotations

import re
from typing import Any

from ...diagnosis.contracts import DiagnosisRequest, EvidenceItem, EvidenceQuality, KnowledgeStepArtifact
from ...diagnosis.steps.knowledge_lookup import extract_fault_codes_from_text

_KNOWLEDGE_SOURCE_RE = re.compile(r"^(来源|来源文件|file_id|source_type|extract_backend|来源页码|检索方式)[：:]\s*(.*)$")


def build_knowledge_evidence_items(
    knowledge_artifact: KnowledgeStepArtifact,
    *,
    request: DiagnosisRequest | None,
) -> list[EvidenceItem]:
    """Build evidence items from knowledge-base snippets."""

    raw_output = (knowledge_artifact.raw_output or "").strip()
    if not knowledge_artifact.success or not raw_output:
        summary = knowledge_artifact.error or "本次请求未获得可用知识库证据。"
        return [
            EvidenceItem(
                evidence_id="ev_kb_result_missing",
                evidence_type="tool_error",
                source_type="knowledge_base",
                source_name="knowledge_base",
                asset_id=request.equipment_hint if request else None,
                content={"query": knowledge_artifact.query, "error": knowledge_artifact.error, "raw_output": raw_output},
                summary=summary,
                quality=EvidenceQuality(reliability="medium", freshness="unknown", relevance="medium", completeness="missing"),
                metadata={"query": knowledge_artifact.query},
                title="知识库检索结果",
                importance="low",
            )
        ]

    items: list[EvidenceItem] = []
    blocks = [block.strip() for block in raw_output.split("\n\n") if block.strip()][:3]
    for index, block in enumerate(blocks, start=1):
        metadata = _knowledge_metadata(block)
        codes = extract_fault_codes_from_text(block)
        evidence_type = "fault_code_reference" if codes else "manual_reference"
        item_id = f"ev_kb_{index:03d}"
        summary = _knowledge_summary(block, codes)
        items.append(
            EvidenceItem(
                evidence_id=item_id,
                evidence_type=evidence_type,
                source_type="knowledge_base",
                source_name=str(metadata.get("来源文件") or metadata.get("来源") or "knowledge_base"),
                asset_id=request.equipment_hint if request else None,
                content={"query": knowledge_artifact.query, "codes": codes, "snippet": block[:1200]},
                summary=summary,
                quality=EvidenceQuality(reliability="high", freshness="unknown", relevance="high", completeness="partial"),
                metadata={"query": knowledge_artifact.query, **metadata},
                title="知识库手册片段",
                importance="high" if codes else "medium",
            )
        )
    return items


def _knowledge_metadata(block: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line in block.splitlines():
        matched = _KNOWLEDGE_SOURCE_RE.match(line.strip())
        if matched:
            metadata[matched.group(1)] = matched.group(2).strip()
    return metadata


def _knowledge_summary(block: str, codes: list[str]) -> str:
    lines = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or _KNOWLEDGE_SOURCE_RE.match(line):
            continue
        lines.append(line)
        if len("；".join(lines)) > 220:
            break
    prefix = f"{', '.join(codes)}：" if codes else ""
    body = "；".join(lines) or block.strip()
    return f"{prefix}{body[:260].strip()}"
