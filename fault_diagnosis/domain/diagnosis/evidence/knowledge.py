"""Knowledge-base evidence construction for diagnosis runtimes."""

from __future__ import annotations

import re
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import DiagnosisRequest, EvidenceItem, EvidenceQuality, KnowledgeStepArtifact
from fault_diagnosis.domain.diagnosis.steps.knowledge_lookup import extract_fault_codes_from_text

_KNOWLEDGE_SOURCE_RE = re.compile(r"^(来源|来源文件|file_id|source_type|extract_backend|来源页码|检索方式)[：:]\s*(.*)$")


def build_knowledge_evidence_items(
    knowledge_artifact: KnowledgeStepArtifact,
    *,
    request: DiagnosisRequest | None,
) -> list[EvidenceItem]:
    """Build evidence items from knowledge-base snippets."""

    raw_output = (knowledge_artifact.raw_output or "").strip()
    if not knowledge_artifact.success or not raw_output:
        return []

    items: list[EvidenceItem] = []
    if knowledge_artifact.fault_code_entries:
        for index, entry in enumerate(knowledge_artifact.fault_code_entries[:3], start=1):
            item_id = f"ev_kb_{index:03d}"
            summary = f"{entry.code}：{entry.meaning or entry.title or '手册未明确给出'}"
            items.append(
                EvidenceItem(
                    evidence_id=item_id,
                    evidence_type="fault_code_reference",
                    source_type="knowledge_base",
                    source_name=entry.source_file or "knowledge_base",
                    asset_id=request.equipment_hint if request else None,
                    content={"query": knowledge_artifact.query, "fault_code_entry": entry.model_dump(mode="json")},
                    summary=summary,
                    quality=EvidenceQuality(reliability="high", freshness="unknown", relevance="high", completeness="partial"),
                    metadata={
                        "query": knowledge_artifact.query,
                        "source_file": entry.source_file,
                        "page": entry.page,
                        "match_type": entry.match_type,
                    },
                    title="故障码手册条目",
                    importance="high",
                )
            )
        return items

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
