"""Map deterministic assessments into evidence and claim contracts."""

from __future__ import annotations

from ..contracts import Claim, ClaimConfidence, DiagnosisRequest, EvidenceItem, EvidenceQuality
from .contracts import DiagnosticAssessment


def map_assessment_to_evidence_items(
    assessment: DiagnosticAssessment,
    *,
    request: DiagnosisRequest | None = None,
) -> list[EvidenceItem]:
    """Convert deterministic features and findings into traceable evidence."""

    asset_id = request.equipment_hint if request and request.equipment_hint else assessment.asset
    freshness = _freshness_from_currentness(assessment.currentness_level)
    items = [
        EvidenceItem(
            evidence_id="ev_analysis_sample_currentness",
            evidence_type="analysis_currentness",
            source_type="rule_analysis",
            source_name=assessment.analyzer_id,
            asset_id=asset_id,
            timestamp=assessment.latest_sample_time,
            time_range=(
                {"start": assessment.oldest_sample_time, "end": assessment.latest_sample_time}
                if assessment.oldest_sample_time and assessment.latest_sample_time
                else None
            ),
            content={
                "sample_count": assessment.sample_count,
                "currentness_level": assessment.currentness_level,
                "currentness_warning": assessment.currentness_warning,
                "source_table": assessment.source_table,
            },
            summary=assessment.currentness_warning
            or f"确定性分析使用 {assessment.sample_count} 条运行样本。",
            quality=EvidenceQuality(
                reliability="high",
                freshness=freshness,
                relevance="high",
                completeness="complete" if assessment.sample_count else "missing",
            ),
            metadata={"analyzer_id": assessment.analyzer_id},
            title="分析样本时效性",
            importance="high" if assessment.currentness_warning else "medium",
        )
    ]

    for feature in assessment.features:
        evidence_id = feature.evidence_id or f"ev_analysis_{feature.feature_id}"
        items.append(
            EvidenceItem(
                evidence_id=evidence_id,
                evidence_type="analysis_metric_feature",
                source_type="rule_analysis",
                source_name=assessment.analyzer_id,
                asset_id=asset_id,
                timestamp=assessment.latest_sample_time,
                content=feature.model_dump(exclude_none=True),
                summary=feature.summary,
                quality=EvidenceQuality(
                    reliability="high",
                    freshness=freshness,
                    relevance="high",
                    completeness="complete" if feature.status != "unknown" else "partial",
                ),
                metadata={"metric_key": feature.metric_key, **feature.metadata},
                title=feature.name,
                importance="high" if feature.status in {"warning", "critical"} else "medium",
            )
        )

    for finding in assessment.findings:
        items.append(
            EvidenceItem(
                evidence_id=f"ev_analysis_{finding.finding_id}",
                evidence_type="analysis_rule_finding",
                source_type="rule_analysis",
                source_name=assessment.analyzer_id,
                asset_id=asset_id,
                timestamp=assessment.latest_sample_time,
                content=finding.model_dump(exclude_none=True),
                summary=finding.summary,
                quality=EvidenceQuality(
                    reliability="high",
                    freshness=freshness,
                    relevance="high",
                    completeness="complete" if finding.supporting_evidence_ids else "partial",
                ),
                metadata={"rule_id": finding.rule_id},
                title=finding.title,
                importance="high" if finding.severity in {"warning", "high", "critical"} else "medium",
            )
        )

    return _dedupe_items(items)


def map_assessment_to_claims(
    assessment: DiagnosticAssessment,
    *,
    request: DiagnosisRequest | None = None,
) -> list[Claim]:
    """Convert deterministic findings into claims with supporting evidence IDs."""

    if not assessment.success:
        return []
    asset_id = request.equipment_hint if request and request.equipment_hint else assessment.asset
    claims: list[Claim] = []
    base_support = _dedupe(
        [
            "ev_analysis_sample_currentness",
            *[feature.evidence_id or f"ev_analysis_{feature.feature_id}" for feature in assessment.features],
        ]
    )
    confidence = ClaimConfidence(
        level=assessment.confidence,
        score={"high": 0.86, "medium": 0.66, "low": 0.42}.get(assessment.confidence, 0.42),
        reason="；".join(assessment.confidence_details[:3]),
    )
    summary_support = _dedupe(
        [
            *base_support,
            *[
                evidence_id
                for finding in assessment.findings
                for evidence_id in finding.supporting_evidence_ids
            ],
        ]
    )
    claims.append(
        Claim(
            claim_id="claim_rule_diagnostic_assessment",
            claim_type="diagnosis_summary",
            asset_id=asset_id,
            statement=assessment.conclusion,
            confidence=confidence,
            supporting_evidence_ids=summary_support or base_support,
            missing_evidence=assessment.missing_evidence,
            reasoning_summary="由确定性 DCMA 运行诊断规则生成。",
            status="final",
            created_by=assessment.analyzer_id,
        )
    )
    for index, finding in enumerate(assessment.findings, start=1):
        support = _dedupe([*finding.supporting_evidence_ids, f"ev_analysis_{finding.finding_id}"])
        if not support:
            support = base_support
        claims.append(
            Claim(
                claim_id=f"claim_rule_finding_{index:03d}",
                claim_type="rule_finding",
                asset_id=asset_id,
                statement=finding.summary,
                confidence=confidence,
                supporting_evidence_ids=support,
                missing_evidence=_dedupe([*assessment.missing_evidence, *finding.missing_evidence]),
                reasoning_summary=f"规则 {finding.rule_id} 触发：{finding.title}",
                status="candidate",
                created_by=assessment.analyzer_id,
                reason_codes=[finding.rule_id],
            )
        )
    return claims


def _freshness_from_currentness(level: str) -> str:
    if level == "realtime":
        return "current"
    if level == "recent":
        return "recent"
    if level == "stale":
        return "stale"
    return "unknown"


def _dedupe(items: list[str]) -> list[str]:
    values: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _dedupe_items(items: list[EvidenceItem]) -> list[EvidenceItem]:
    values: list[EvidenceItem] = []
    seen: set[str] = set()
    for item in items:
        key = item.evidence_id or item.summary
        if key in seen:
            continue
        seen.add(key)
        values.append(item)
    return values
