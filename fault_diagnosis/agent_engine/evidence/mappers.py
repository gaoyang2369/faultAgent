"""Evidence mappers for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from ...diagnosis.contracts import DiagnosisRequest, EvidenceItem, EvidenceQuality, KnowledgeStepArtifact, SqlStepArtifact
from ...diagnosis.evidence.knowledge import build_knowledge_evidence_items
from ...diagnosis.evidence.sql import build_sql_evidence_items


def map_sql_evidence(sql_artifact: SqlStepArtifact | dict[str, Any], *, request: DiagnosisRequest | None = None) -> list[dict[str, Any]]:
    artifact = sql_artifact if isinstance(sql_artifact, SqlStepArtifact) else SqlStepArtifact.model_validate(sql_artifact)
    return [_authorized(item) for item in build_sql_evidence_items(artifact, request=request)]


def map_rag_evidence(knowledge_artifact: KnowledgeStepArtifact | dict[str, Any], *, request: DiagnosisRequest | None = None) -> list[dict[str, Any]]:
    artifact = knowledge_artifact if isinstance(knowledge_artifact, KnowledgeStepArtifact) else KnowledgeStepArtifact.model_validate(knowledge_artifact)
    return [_authorized(item) for item in build_knowledge_evidence_items(artifact, request=request)]


def map_kg_not_configured_evidence(*, node_id: str = "kg") -> list[dict[str, Any]]:
    return [
        EvidenceItem(
            evidence_id=f"ev_{node_id}_not_configured",
            evidence_type="kg_not_configured",
            source_type="knowledge_graph",
            source_name="kg",
            summary="知识图谱未配置，本次未使用 KG 证据。",
            content={"status": "not_configured"},
            quality=EvidenceQuality(reliability="high", freshness="current", relevance="medium", completeness="missing"),
            metadata={"authorized": True, "disclosure": True},
        ).model_dump(mode="json", exclude_none=True)
    ]


def map_timeseries_evidence(
    *,
    evidence_id: str,
    summary: str,
    content: dict[str, Any],
    asset_id: str | None = None,
    freshness: str = "current",
) -> list[dict[str, Any]]:
    return [
        EvidenceItem(
            evidence_id=evidence_id,
            evidence_type="timeseries_feature",
            source_type="timeseries",
            source_name="runtime_metrics",
            asset_id=asset_id,
            summary=summary,
            content=content,
            quality=EvidenceQuality(reliability="high", freshness=freshness, relevance="high", completeness="complete"),
            metadata={"authorized": True},
        ).model_dump(mode="json", exclude_none=True)
    ]


def map_manual_evidence(
    *,
    evidence_id: str,
    summary: str,
    content: Any,
    evidence_type: str = "manual_note",
    authorized: bool = True,
) -> list[dict[str, Any]]:
    return [
        EvidenceItem(
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            source_type="manual",
            source_name="user_or_operator",
            summary=summary,
            content=content,
            quality=EvidenceQuality(reliability="medium", freshness="current", relevance="medium", completeness="partial"),
            metadata={"authorized": authorized},
        ).model_dump(mode="json", exclude_none=True)
    ]


def _authorized(item: EvidenceItem) -> dict[str, Any]:
    payload = item.model_dump(mode="json", exclude_none=True)
    metadata = dict(payload.get("metadata") or {})
    metadata.setdefault("authorized", True)
    payload["metadata"] = metadata
    return payload
