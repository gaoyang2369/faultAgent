"""Claim construction from evidence IDs and analysis artifacts."""

from __future__ import annotations

from ...diagnosis.contracts import (
    AnalysisStepArtifact,
    Claim,
    ClaimConfidence,
    DiagnosisRequest,
    WorkOrderSuggestion,
)
from .utils import dedupe


def build_claims(
    *,
    request: DiagnosisRequest,
    analysis_artifact: AnalysisStepArtifact,
    workorder_suggestion: WorkOrderSuggestion,
    evidence_ids: list[str],
) -> list[Claim]:
    """Build traceable claims backed by evidence IDs."""

    if not evidence_ids:
        return []

    claims: list[Claim] = []
    sql_ids = [item for item in evidence_ids if item.startswith("ev_sql_")]
    kb_ids = [item for item in evidence_ids if item.startswith("ev_kb_") and item != "ev_kb_result_missing"]
    support_all = _prefer_supporting_ids(evidence_ids)
    missing_evidence = dedupe(analysis_artifact.missing_information)
    confidence = _confidence_from_analysis(analysis_artifact)

    if analysis_artifact.conclusion:
        claims.append(
            Claim(
                claim_id="claim_diagnosis_summary",
                claim_type="diagnosis_summary",
                asset_id=request.equipment_hint,
                statement=analysis_artifact.conclusion,
                confidence=confidence,
                supporting_evidence_ids=support_all,
                missing_evidence=missing_evidence,
                reasoning_summary="综合用户请求、SQL 运行数据、知识库片段和规则分析形成诊断摘要。",
                status="final",
                created_by="analysis_node",
            )
        )

    for index, cause in enumerate(dedupe(analysis_artifact.probable_causes)[:3], start=1):
        claims.append(
            Claim(
                claim_id=f"claim_root_cause_{index:03d}",
                claim_type="root_cause_candidate",
                asset_id=request.equipment_hint,
                statement=cause,
                confidence=confidence,
                supporting_evidence_ids=dedupe([*sql_ids, *kb_ids]) or support_all,
                missing_evidence=missing_evidence,
                reasoning_summary="该根因候选由运行数据特征和可用知识库提示共同支撑，仍需现场闭环验证。",
                status="candidate",
                created_by="analysis_node",
            )
        )

    if analysis_artifact.risk_notice:
        claims.append(
            Claim(
                claim_id="claim_risk_assessment",
                claim_type="risk_assessment",
                asset_id=request.equipment_hint,
                statement=analysis_artifact.risk_notice,
                confidence=ClaimConfidence(level="medium", score=0.68, reason="风险提示通常依赖数据异常和现场安全规程，需现场确认。"),
                supporting_evidence_ids=support_all,
                missing_evidence=missing_evidence,
                reasoning_summary="根据当前异常状态和处置闭环要求形成风险提示。",
                status="candidate",
                created_by="analysis_node",
            )
        )

    if analysis_artifact.recommendations:
        claims.append(
            Claim(
                claim_id="claim_recommendation",
                claim_type="recommendation",
                asset_id=request.equipment_hint,
                statement="；".join(dedupe(analysis_artifact.recommendations)[:3]),
                confidence=ClaimConfidence(level=analysis_artifact.confidence if analysis_artifact.confidence in {"high", "medium", "low"} else "medium", score=None, reason="建议基于诊断结论、关键依据和风险提示生成。"),
                supporting_evidence_ids=support_all,
                missing_evidence=missing_evidence,
                reasoning_summary="将诊断结论转化为现场可执行的下一步建议。",
                status="candidate",
                created_by="analysis_node",
            )
        )

    claims.append(
        Claim(
            claim_id="claim_workorder_decision",
            claim_type="workorder_decision",
            asset_id=request.equipment_hint or workorder_suggestion.equipment_object or None,
            statement=workorder_suggestion.reason or ("建议生成维修工单" if workorder_suggestion.need_workorder else "暂不建议自动生成维修工单"),
            confidence=ClaimConfidence(
                level="medium" if workorder_suggestion.need_workorder else "low",
                score=0.72 if workorder_suggestion.need_workorder else 0.45,
                reason="工单建议由规则阈值、异常持续性和分析结论共同生成。",
            ),
            supporting_evidence_ids=support_all,
            missing_evidence=missing_evidence,
            reasoning_summary=workorder_suggestion.diagnosis_conclusion or workorder_suggestion.reason,
            status="candidate",
            created_by="workorder_decision_node",
            decision="suggest_create" if workorder_suggestion.need_workorder else "skip_create",
            reason_codes=_workorder_reason_codes(workorder_suggestion),
        )
    )
    return claims


def _confidence_from_analysis(analysis_artifact: AnalysisStepArtifact) -> ClaimConfidence:
    level = analysis_artifact.confidence if analysis_artifact.confidence in {"high", "medium", "low"} else "medium"
    score = {"high": 0.85, "medium": 0.65, "low": 0.4}[level]
    reason = "；".join(analysis_artifact.confidence_details[:3]) if analysis_artifact.confidence_details else ""
    return ClaimConfidence(level=level, score=score, reason=reason)


def _prefer_supporting_ids(evidence_ids: list[str]) -> list[str]:
    preferred = [
        evidence_id
        for evidence_id in evidence_ids
        if not evidence_id.endswith("_missing") and not evidence_id.startswith("ev_user_")
    ]
    return preferred or evidence_ids[:]


def _workorder_reason_codes(workorder_suggestion: WorkOrderSuggestion) -> list[str]:
    codes: list[str] = []
    text = " ".join([workorder_suggestion.reason, *workorder_suggestion.key_evidence])
    if "速度偏差" in text:
        codes.append("speed_deviation_above_threshold")
    if "负载率" in text:
        codes.append("load_rate_attention")
    if "温度" in text:
        codes.append("temperature_attention")
    if workorder_suggestion.fault_code:
        codes.append("fault_or_alarm_code_present")
    if workorder_suggestion.need_workorder:
        codes.append("workorder_rule_triggered")
    return codes
