"""Build V2 output frames from structured runtime payloads."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import AnalysisStepArtifact, EvidenceBundle, ReportStepArtifact, SqlStepArtifact
from ..contracts import NodeResult, OutputFrame


def build_output_frame(
    *,
    status: str = "completed",
    artifacts: dict[str, Any] | None = None,
    evidence_bundle: EvidenceBundle | dict[str, Any] | None = None,
    node_results: list[NodeResult] | list[dict[str, Any]] | None = None,
    requested_variant: str | None = None,
    error: dict[str, Any] | None = None,
    cancelled: bool = False,
    cancel_reason: str | None = None,
) -> OutputFrame:
    """Return the internal V2 output frame before frontend compatibility projection."""

    artifact_map = dict(artifacts or {})
    bundle = _model(evidence_bundle, EvidenceBundle)
    sql_artifact = _model(artifact_map.get("sql_artifact"), SqlStepArtifact)
    analysis_artifact = _model(artifact_map.get("analysis_artifact"), AnalysisStepArtifact)
    report_artifact = _model(artifact_map.get("report_artifact"), ReportStepArtifact)
    variant = requested_variant or _infer_variant(
        status=status,
        sql_artifact=sql_artifact,
        analysis_artifact=analysis_artifact,
        report_artifact=report_artifact,
        cancelled=cancelled,
    )
    final_answer = _render_answer(
        variant=variant,
        status=status,
        sql_artifact=sql_artifact,
        analysis_artifact=analysis_artifact,
        report_artifact=report_artifact,
        evidence_bundle=bundle,
        error=error,
        cancel_reason=cancel_reason,
    )
    status_brief = _status_brief(sql_artifact=sql_artifact, evidence_bundle=bundle, status=status)
    diagnosis_payload = {
        "sql_artifact": _dump(sql_artifact),
        "analysis_artifact": _dump(analysis_artifact),
        "report_artifact": _dump(report_artifact),
        "evidence_bundle": _dump(bundle),
        "node_results": [_dump(item) for item in node_results or []],
    }
    guardrail = {
        "status": status,
        "cancelled": bool(cancelled),
        "missing_evidence": _missing_evidence(bundle, analysis_artifact),
        "final_claim_ids": list(bundle.final_claim_ids if bundle else []),
        "final_claims_without_evidence": _final_claims_without_evidence(bundle),
        "stale_evidence": _stale_evidence(bundle),
    }
    if error:
        guardrail["error"] = dict(error)
    if cancel_reason:
        guardrail["cancel_reason"] = cancel_reason
    return OutputFrame(
        answer_variant=variant,
        final_answer=final_answer,
        status_brief=status_brief,
        diagnosis_report_payload=diagnosis_payload,
        artifact_payload={key: _dump(value) for key, value in artifact_map.items()},
        guardrail_result=guardrail,
    )


def _infer_variant(
    *,
    status: str,
    sql_artifact: SqlStepArtifact | None,
    analysis_artifact: AnalysisStepArtifact | None,
    report_artifact: ReportStepArtifact | None,
    cancelled: bool,
) -> str:
    if cancelled or status == "blocked":
        return "blocked"
    if status == "failed":
        return "error"
    if report_artifact and report_artifact.success:
        return "report_ready"
    if analysis_artifact and analysis_artifact.success:
        return "diagnosis_answer"
    if sql_artifact and sql_artifact.success:
        return "status_brief"
    return "clarification"


def _render_answer(
    *,
    variant: str,
    status: str,
    sql_artifact: SqlStepArtifact | None,
    analysis_artifact: AnalysisStepArtifact | None,
    report_artifact: ReportStepArtifact | None,
    evidence_bundle: EvidenceBundle | None,
    error: dict[str, Any] | None,
    cancel_reason: str | None,
) -> str:
    if variant == "blocked":
        if status == "cancelled":
            return "已停止本次处理，未继续执行后续步骤。"
        message = str((error or {}).get("message") or "").strip()
        return message or "当前请求被安全边界阻止，未执行受限动作。"
    if variant == "error":
        message = str((error or {}).get("message") or "").strip()
        return message or "V2 执行失败，未生成可靠诊断结果。"
    if variant == "report_ready" and report_artifact:
        link = report_artifact.report_url or report_artifact.report_filename or ""
        suffix = f" 报告地址：{link}" if link else ""
        return f"报告已生成。{suffix}".strip()
    if variant == "diagnosis_answer":
        claim_answer = _render_claim_answer(evidence_bundle)
        if claim_answer:
            stale_lines = _stale_disclosure_lines(evidence_bundle)
            return "\n".join([claim_answer, *stale_lines]).strip()
        if evidence_bundle and evidence_bundle.claims and not evidence_bundle.final_claim_ids:
            pending = _pending_claim_lines(evidence_bundle)
            stale_lines = _stale_disclosure_lines(evidence_bundle)
            return "\n".join([*pending, *stale_lines]).strip()
        if not analysis_artifact:
            return "待确认/需补充：当前诊断判断缺少可引用证据，不能作为最终结论。"
        parts = [_line("诊断结论", analysis_artifact.conclusion)]
        parts.extend(_numbered("依据", analysis_artifact.basis))
        parts.extend(_numbered("建议", analysis_artifact.recommendations))
        missing = _missing_evidence(evidence_bundle, analysis_artifact)
        if missing:
            parts.extend(_numbered("仍需补充", missing))
        if analysis_artifact.risk_notice:
            parts.append(_line("风险提示", analysis_artifact.risk_notice))
        stale_lines = _stale_disclosure_lines(evidence_bundle)
        return "\n".join([*(item for item in parts if item), *stale_lines]).strip()
    if variant == "status_brief":
        return _status_brief(sql_artifact=sql_artifact, evidence_bundle=evidence_bundle, status=status)
    if cancel_reason:
        return f"需要补充信息后才能继续处理。当前停止原因：{cancel_reason}"
    return "需要补充设备、故障码或时间窗口等关键信息后才能继续处理。"


def _status_brief(
    *,
    sql_artifact: SqlStepArtifact | None,
    evidence_bundle: EvidenceBundle | None,
    status: str,
) -> str:
    if sql_artifact:
        data_state = f" 数据状态：{sql_artifact.data_state}。" if sql_artifact.data_state else ""
        return f"{sql_artifact.summary}{data_state}".strip()
    summaries = [
        item.summary
        for item in (evidence_bundle.evidence_items if evidence_bundle else [])
        if getattr(item, "summary", "")
    ][:3]
    if summaries:
        return "；".join(summaries)
    return f"V2 runtime {status}."


def _missing_evidence(
    evidence_bundle: EvidenceBundle | None,
    analysis_artifact: AnalysisStepArtifact | None,
) -> list[str]:
    items: list[str] = []
    if evidence_bundle:
        for claim in evidence_bundle.claims:
            items.extend(claim.missing_evidence)
        quality_missing = evidence_bundle.quality_checks.get("missing_evidence")
        if isinstance(quality_missing, list):
            items.extend(str(item) for item in quality_missing)
    if analysis_artifact:
        items.extend(analysis_artifact.missing_information)
    return _dedupe(items)


def _render_claim_answer(evidence_bundle: EvidenceBundle | None) -> str:
    if not evidence_bundle or not evidence_bundle.final_claim_ids:
        return ""
    by_id = {claim.claim_id: claim for claim in evidence_bundle.claims}
    final_claims = [
        by_id[claim_id]
        for claim_id in evidence_bundle.final_claim_ids
        if claim_id in by_id and by_id[claim_id].supporting_evidence_ids
    ]
    if not final_claims:
        return ""
    evidence_by_id = {item.evidence_id: item for item in evidence_bundle.evidence_items}
    parts: list[str] = []
    parts.extend(_numbered("诊断结论", [claim.statement for claim in final_claims]))
    supporting = _dedupe(
        [
            evidence_by_id[evidence_id].summary
            for claim in final_claims
            for evidence_id in claim.supporting_evidence_ids
            if evidence_id in evidence_by_id and evidence_by_id[evidence_id].summary
        ]
    )
    parts.extend(_numbered("依据", supporting[:5]))
    missing = _dedupe([item for claim in final_claims for item in claim.missing_evidence])
    if missing:
        parts.extend(_numbered("仍需补充", missing))
    return "\n".join(parts)


def _pending_claim_lines(evidence_bundle: EvidenceBundle) -> list[str]:
    unsupported = [
        claim.statement
        for claim in evidence_bundle.claims
        if not claim.supporting_evidence_ids and claim.statement
    ]
    missing = _missing_evidence(evidence_bundle, None)
    values = unsupported or missing or ["当前诊断判断缺少可引用证据，不能作为最终结论。"]
    lines = _numbered("待确认/需补充", values)
    if missing and unsupported:
        lines.extend(_numbered("缺失证据", missing))
    return lines


def _final_claims_without_evidence(evidence_bundle: EvidenceBundle | None) -> list[str]:
    if not evidence_bundle:
        return []
    final = set(evidence_bundle.final_claim_ids)
    return [
        claim.claim_id
        for claim in evidence_bundle.claims
        if (claim.claim_id in final or claim.status == "final") and not claim.supporting_evidence_ids
    ]


def _stale_evidence(evidence_bundle: EvidenceBundle | None) -> list[dict[str, Any]]:
    if not evidence_bundle:
        return []
    stale_ids = set(str(item) for item in evidence_bundle.quality_checks.get("stale_evidence_ids", []) or [])
    result = []
    for item in evidence_bundle.evidence_items:
        freshness = str(getattr(item.quality, "freshness", "") or item.metadata.get("freshness", "")).lower()
        if item.evidence_id in stale_ids or freshness == "stale" or item.evidence_type == "stale_evidence_disclosure":
            result.append({"evidence_id": item.evidence_id, "summary": item.summary, "evidence_type": item.evidence_type})
    return result


def _stale_disclosure_lines(evidence_bundle: EvidenceBundle | None) -> list[str]:
    stale = _stale_evidence(evidence_bundle)
    if not stale:
        return []
    summaries = _dedupe([item.get("summary") for item in stale if item.get("summary")])
    return _numbered("时效性提示", summaries[:3] or ["存在滞后证据，不能代表当前实时状态。"])


def _numbered(title: str, values: list[Any]) -> list[str]:
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    if not cleaned:
        return []
    return [f"{title}：", *[f"{index}. {item}" for index, item in enumerate(cleaned, start=1)]]


def _line(title: str, value: Any) -> str:
    text = str(value or "").strip()
    return f"{title}：{text}" if text else ""


def _dedupe(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _model(value: Any, model_type: Any) -> Any:
    if isinstance(value, model_type):
        return value
    if isinstance(value, dict):
        return model_type.model_validate(value)
    return None


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return value
