"""Build the immutable report input owned by an Analysis Artifact."""

from __future__ import annotations

from datetime import UTC, datetime

from fault_diagnosis.domain.artifacts import (
    ArtifactEnvelope,
    KnowledgeArtifactPayload,
    ReportInputSnapshot,
    SqlArtifactPayload,
)
from fault_diagnosis.domain.diagnosis.analysis.contracts import StructuredAnalysisArtifact


def build_report_input_snapshot(
    *,
    structured_analysis: StructuredAnalysisArtifact,
    sql_source: ArtifactEnvelope,
    knowledge_source: ArtifactEnvelope | None = None,
    generated_at: str | None = None,
) -> ReportInputSnapshot:
    """Project report data from the exact artifacts actually consumed by analysis."""

    if not isinstance(sql_source.payload, SqlArtifactPayload):
        raise TypeError("report snapshot requires an exact SQL Artifact source")
    if knowledge_source is not None and not isinstance(knowledge_source.payload, KnowledgeArtifactPayload):
        raise TypeError("report snapshot knowledge source must be a Knowledge Artifact")

    sql = sql_source.payload
    assessment = sql.runtime_status_assessment
    data_basis = assessment.data_basis
    structured_evidence_ids = {
        item.evidence_id for item in structured_analysis.evidence_items if item.evidence_id
    }
    declared_evidence_ids = list(
        dict.fromkeys(
            evidence_id
            for evidence_id in (
                *assessment.supporting_evidence_ids,
                *(
                    evidence_id
                    for finding in structured_analysis.assessment.findings
                    for evidence_id in finding.supporting_evidence_ids
                ),
            )
            if evidence_id and evidence_id in structured_evidence_ids
        )
    )
    findings = [item.model_dump(mode="json", exclude_none=True) for item in structured_analysis.assessment.findings]
    return ReportInputSnapshot(
        device_refs=[assessment.device] if assessment.device else list(sql_source.lineage.subject_device_refs),
        fault_codes=list(dict.fromkeys((*assessment.event_codes, *sql_source.lineage.fault_code_refs))),
        requested_window=(
            data_basis.requested_window.model_dump(mode="json")
            if data_basis.requested_window is not None
            else dict(sql.sql_artifact.requested_window or {}) or None
        ),
        resolved_window=(
            data_basis.resolved_window.model_dump(mode="json")
            if data_basis.resolved_window is not None
            else dict(sql.sql_artifact.resolved_window or {}) or None
        ),
        latest_sample_time=(
            data_basis.latest_sample_time.isoformat()
            if data_basis.latest_sample_time is not None
            else sql.sql_artifact.latest_sample_time or None
        ),
        sample_count=assessment.sample_count,
        freshness_status=data_basis.freshness,
        freshness_reason=data_basis.fallback_reason,
        runtime_summary={
            **assessment.model_dump(mode="json", exclude_none=True),
            "source_table": sql_source.manifest.source_table,
        },
        structured_findings=findings,
        diagnosis_summary=structured_analysis.analysis_artifact.conclusion,
        severity=_highest_severity(findings),
        recommendations=list(structured_analysis.analysis_artifact.recommendations),
        supporting_evidence_ids=declared_evidence_ids,
        source_sql_artifact_id=sql_source.artifact_id,
        source_knowledge_artifact_id=knowledge_source.artifact_id if knowledge_source is not None else None,
        tabular_source_sql_artifact_id=None,
        generated_at=generated_at or datetime.now(UTC).isoformat(),
    )


def _highest_severity(findings: list[dict]) -> str | None:
    rank = {"unknown": 0, "normal": 1, "notice": 2, "warning": 3, "high": 4, "critical": 5}
    values = [str(item.get("severity") or "unknown") for item in findings]
    return max(values, key=lambda value: rank.get(value, 0)) if values else None
