"""公共证据复核 step。"""

from __future__ import annotations

from typing import Any, Callable

from ..contracts import EvidenceReviewArtifact, WorkflowArtifactEnvelope

_SOURCE_TYPE_TO_EVIDENCE_KIND = {
    "sql": "sql",
    "knowledge_base": "rag",
    "analysis": "generic",
    "report": "report",
}
_SOURCE_TYPE_TO_STAGE = {
    "sql": "collect",
    "knowledge_base": "retrieve",
    "analysis": "analyze",
    "report": "report",
}


def hydrate_evidence_registry_from_artifact(
    envelope: WorkflowArtifactEnvelope,
    *,
    list_evidence_records: Callable[[], list[dict[str, Any]]],
    register_evidence: Callable[..., Any],
) -> int:
    """当当前请求上下文里没有证据注册表时，用上游 artifact 中的 evidence 做保守回填。"""

    hydrated_count = 0
    if list_evidence_records():
        return hydrated_count

    for item in envelope.evidence:
        content = str(item.content or "").strip()
        if not content:
            continue
        source_type = str(item.source_type or "generic").strip()
        register_evidence(
            evidence_type=_SOURCE_TYPE_TO_EVIDENCE_KIND.get(source_type, "generic"),
            source=f"artifact:{source_type}",
            title=str(item.title or source_type or "artifact evidence").strip(),
            summary=content,
            stage=_SOURCE_TYPE_TO_STAGE.get(source_type, "review"),
            metadata={
                "from_artifact_envelope": True,
                "importance": item.importance,
                "source_type": source_type,
            },
        )
        hydrated_count += 1
    return hydrated_count


def collect_unsupported_finding_texts(
    findings: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> list[str]:
    """提取未被强证据支撑的结论文本，便于直接对用户展示。"""

    link_index = {
        str(link.get("finding_id")): link
        for link in links
        if isinstance(link, dict) and link.get("finding_id")
    }
    unsupported: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding_id = str(finding.get("finding_id") or "")
        link = link_index.get(finding_id, {})
        evidence_ids = link.get("evidence_ids") or []
        match_score = int(link.get("match_score") or 0)
        if not evidence_ids or match_score <= 0:
            text = str(finding.get("text") or "").strip()
            if text and text not in unsupported:
                unsupported.append(text)
    return unsupported


def build_evidence_review_artifact(
    envelope: WorkflowArtifactEnvelope,
    *,
    create_grounded_findings: Callable[[str], tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    list_evidence_records: Callable[[], list[dict[str, Any]]],
    list_findings: Callable[[], list[dict[str, Any]]],
    list_finding_links: Callable[[], list[dict[str, Any]]],
    register_evidence: Callable[..., Any],
    summarize_evidence_coverage: Callable[..., dict[str, Any]],
    summarize_evidence_quality: Callable[..., dict[str, Any]],
) -> EvidenceReviewArtifact:
    """基于上游结果和当前证据上下文生成复核产物。"""

    hydrated_count = hydrate_evidence_registry_from_artifact(
        envelope,
        list_evidence_records=list_evidence_records,
        register_evidence=register_evidence,
    )

    findings = list_findings()
    links = list_finding_links()
    if not findings:
        findings, links = create_grounded_findings(envelope.final_answer or "")

    records = list_evidence_records()
    coverage_summary = summarize_evidence_coverage(
        findings=findings,
        links=links,
        records=records,
    )
    quality_summary = summarize_evidence_quality(
        findings=findings,
        links=links,
        records=records,
    )
    unsupported_findings = collect_unsupported_finding_texts(findings, links)
    review_reasons = [str(item).strip() for item in quality_summary.get("review_reasons", []) if str(item).strip()]
    summary_parts = [
        f"复核目标={envelope.workflow_type}",
        f"证据={quality_summary.get('total_evidences', 0)}",
        f"结论={quality_summary.get('total_findings', 0)}",
        f"覆盖评分={coverage_summary.get('score', 0)}({coverage_summary.get('grade', 'D')})",
        f"门禁={quality_summary.get('gate', 'unknown')}",
    ]
    if hydrated_count > 0:
        summary_parts.append(f"已从上游 artifact 回填 {hydrated_count} 条证据")
    if review_reasons:
        summary_parts.append("原因：" + "；".join(review_reasons[:3]))

    return EvidenceReviewArtifact(
        success=True,
        review_target_workflow=str(envelope.workflow_type),
        total_findings=int(quality_summary.get("total_findings") or 0),
        total_evidences=int(quality_summary.get("total_evidences") or 0),
        coverage_score=float(coverage_summary.get("score")) if coverage_summary.get("score") is not None else None,
        quality_gate_status=str(quality_summary.get("gate") or "unknown"),
        unsupported_findings=unsupported_findings,
        missing_evidence_ids=[str(item) for item in quality_summary.get("missing_evidence_ids", []) if str(item)],
        recommended_action=str(quality_summary.get("recommended_action") or "").strip(),
        review_summary="；".join(summary_parts),
        error=None,
    )
