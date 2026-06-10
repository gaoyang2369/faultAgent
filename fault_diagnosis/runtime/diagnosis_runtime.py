"""Diagnosis finalization helpers for grounded outputs and evidence gates."""

from __future__ import annotations

from typing import Any

from ..quality.evidence import (
    apply_grounding_status_to_findings,
    build_grounded_final_content,
    build_quality_gate_notice,
    build_quality_gated_response,
    create_grounded_findings,
    list_evidence_records,
    list_normalized_evidence_records,
    summarize_evidence_quality,
)


def get_current_quality_summary() -> dict[str, Any]:
    return summarize_evidence_quality()


def build_diagnosis_runtime_payload(final_content: str) -> dict[str, Any]:
    raw_final_content = final_content
    findings, finding_links = create_grounded_findings(final_content)
    evidence_records = list_evidence_records()
    normalized_evidence_records = list_normalized_evidence_records()
    evidence_quality = summarize_evidence_quality(
        findings=findings,
        links=finding_links,
        records=evidence_records,
    )
    quality_gate_notice = build_quality_gate_notice(evidence_quality)
    findings = apply_grounding_status_to_findings(
        findings,
        finding_links,
        evidence_quality,
    )
    grounded_final_content = build_grounded_final_content(
        final_content,
        findings=findings,
        links=finding_links,
    )
    final_content, grounded_final_content = build_quality_gated_response(
        final_content,
        grounded_final_content,
        evidence_quality,
        findings=findings,
    )
    return {
        "raw_final_content": raw_final_content,
        "final_content": final_content,
        "grounded_final_content": grounded_final_content,
        "findings": findings,
        "finding_links": finding_links,
        "evidence_records": evidence_records,
        "normalized_evidence_records": normalized_evidence_records,
        "evidence_quality": evidence_quality,
        "quality_gate_notice": quality_gate_notice,
    }
