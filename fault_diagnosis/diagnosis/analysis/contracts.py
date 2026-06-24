"""Contracts for deterministic diagnosis analysis."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..contracts import AnalysisStepArtifact, Claim, EvidenceItem

FeatureStatus = Literal["normal", "warning", "critical", "unknown"]
FindingSeverity = Literal["normal", "notice", "warning", "high", "critical", "unknown"]
CurrentnessLevel = Literal["realtime", "recent", "stale", "missing", "unknown"]


class RuntimeMetricFeature(BaseModel):
    """One deterministic runtime feature computed from tool results."""

    feature_id: str
    metric_key: str
    name: str
    value: float | str | None = None
    unit: str = ""
    latest_value: float | str | None = None
    window_max: float | None = None
    warning_threshold: float | None = None
    critical_threshold: float | None = None
    status: FeatureStatus = "unknown"
    summary: str = ""
    evidence_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuleFinding(BaseModel):
    """A rule-level finding with explicit evidence references."""

    finding_id: str
    rule_id: str
    title: str
    severity: FindingSeverity
    summary: str
    supporting_feature_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommendation: str | None = None


class DiagnosticAssessment(BaseModel):
    """Deterministic assessment produced by a domain analysis interface."""

    success: bool
    analyzer_id: str = "dcma_runtime"
    asset: str = "DCMA 系统"
    source_table: str = "real_data_01"
    sample_count: int = 0
    latest_sample_time: str | None = None
    oldest_sample_time: str | None = None
    currentness_level: CurrentnessLevel = "unknown"
    currentness_warning: str | None = None
    event_codes: list[str] = Field(default_factory=list)
    features: list[RuntimeMetricFeature] = Field(default_factory=list)
    findings: list[RuleFinding] = Field(default_factory=list)
    conclusion: str = ""
    probable_causes: list[str] = Field(default_factory=list)
    verification_items: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    risk_notice: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    confidence_details: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredAnalysisArtifact(BaseModel):
    """Analysis result plus compatibility artifacts for the existing runtime."""

    assessment: DiagnosticAssessment
    analysis_artifact: AnalysisStepArtifact
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
