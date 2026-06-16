"""线程级诊断产物到前端结构化契约的消费侧适配器。"""

from __future__ import annotations

from typing import Any

from ..diagnosis.contracts import DiagnosisArtifactEnvelope


def _dump_model(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _compact_text(value: Any, *, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _diagnosis_key(envelope: DiagnosisArtifactEnvelope) -> str:
    artifact_type = envelope.workflow_type
    return getattr(artifact_type, "value", str(artifact_type))


def _confidence_score(value: Any) -> float | None:
    if isinstance(value, dict):
        explicit_score = value.get("score")
        if isinstance(explicit_score, (int, float)):
            return round(max(0.0, min(float(explicit_score), 1.0)), 2)
        return _confidence_score(value.get("level"))
    if isinstance(value, (int, float)):
        return round(max(0.0, min(float(value), 1.0)), 2)
    normalized = str(value or "").strip().lower()
    if normalized == "high":
        return 0.85
    if normalized == "medium":
        return 0.65
    if normalized == "low":
        return 0.45
    return None


def _confidence_label(value: Any) -> str:
    if isinstance(value, dict):
        return _confidence_label(value.get("level"))
    if isinstance(value, (int, float)):
        score = float(value)
        if score >= 0.75:
            return "high"
        if score >= 0.55:
            return "medium"
        return "low"
    normalized = str(value or "").strip().lower()
    if normalized in {"high", "medium", "low"}:
        return normalized
    return "medium"


def _severity_from_payload(payload: dict[str, Any]) -> str:
    analysis = _dump_model(payload.get("analysis_artifact"))
    inspection = _dump_model(payload.get("inspection_artifact"))
    risk = str(analysis.get("risk_notice") or inspection.get("risk_level") or "").lower()
    conclusion = str(analysis.get("conclusion") or inspection.get("summary") or "").lower()
    text = f"{risk} {conclusion}"
    if any(keyword in text for keyword in ("critical", "严重", "停机", "高风险")):
        return "critical"
    if any(keyword in text for keyword in ("high", "异常", "报警", "过载", "故障")):
        return "high"
    if any(keyword in text for keyword in ("medium", "warning", "波动", "偏高", "超限")):
        return "medium"
    return "low"


def _build_standard_evidence(envelope: DiagnosisArtifactEnvelope) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, item in enumerate(envelope.evidence or [], start=1):
        data = _dump_model(item)
        source_type = str(data.get("source_type") or "generic").strip() or "generic"
        evidence_id = str(data.get("evidence_id") or f"evidence_{source_type}_{index:03d}")
        evidence_type = str(data.get("evidence_type") or source_type)
        summary = data.get("summary") or data.get("content") or data.get("title")
        items.append(
            {
                "id": evidence_id,
                "evidence_id": evidence_id,
                "type": evidence_type,
                "source": source_type,
                "title": data.get("title") or data.get("summary") or "证据项",
                "summary": _compact_text(summary, limit=220),
                "metadata": {
                    "importance": data.get("importance") or "medium",
                    "artifact_workflow_type": _diagnosis_key(envelope),
                    "thread_id": envelope.thread_id,
                    "source_name": data.get("source_name"),
                    "quality": data.get("quality") or {},
                    **(data.get("metadata") or {}),
                },
                "artifact_id": None,
                "status": "available" if summary else "unavailable",
            }
        )
    return items


def _evidence_bundle_payload(envelope: DiagnosisArtifactEnvelope) -> dict[str, Any]:
    payload = envelope.payload or {}
    bundle = payload.get("evidence_bundle")
    return bundle if isinstance(bundle, dict) else {}


def _build_artifacts(envelope: DiagnosisArtifactEnvelope) -> list[dict[str, Any]]:
    payload = envelope.payload or {}
    report_artifact = _dump_model(payload.get("report_artifact"))
    artifacts: list[dict[str, Any]] = []
    if envelope.report_filename or report_artifact:
        artifact_id = "artifact_report_001"
        artifacts.append(
            {
                "id": artifact_id,
                "type": "report",
                "title": "故障诊断报告" if _diagnosis_key(envelope) == "fault_diagnosis" else "诊断报告",
                "summary": _compact_text(report_artifact.get("save_result") or envelope.final_answer),
                "path": envelope.report_filename,
                "content": None,
                "metadata": report_artifact,
                "created_at": envelope.created_at,
            }
        )
    return artifacts


def _build_findings(envelope: DiagnosisArtifactEnvelope, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = envelope.payload or {}
    bundle = _evidence_bundle_payload(envelope)
    claims = bundle.get("claims") if isinstance(bundle.get("claims"), list) else []
    if claims:
        evidence_by_id = {item["id"]: item for item in evidence}
        final_claim_ids = bundle.get("final_claim_ids") if isinstance(bundle.get("final_claim_ids"), list) else []
        selected_claims = [
            claim
            for claim in claims
            if not final_claim_ids or claim.get("claim_id") in final_claim_ids
        ][:5]
        findings: list[dict[str, Any]] = []
        for index, claim in enumerate(selected_claims, start=1):
            claim_id = str(claim.get("claim_id") or f"claim_{index:03d}")
            supporting_ids = [
                str(evidence_id)
                for evidence_id in claim.get("supporting_evidence_ids") or []
                if str(evidence_id) in evidence_by_id
            ]
            confidence = claim.get("confidence") or {}
            findings.append(
                {
                    "id": claim_id,
                    "finding_id": claim_id,
                    "title": _compact_text(claim.get("statement") or claim_id, limit=80),
                    "description": _compact_text(claim.get("reasoning_summary") or claim.get("statement"), limit=260),
                    "severity": _severity_from_claim(claim, payload),
                    "confidence": _confidence_label(confidence),
                    "confidence_score": _confidence_score(confidence),
                    "evidence_ids": supporting_ids,
                    "text": _compact_text(claim.get("statement"), limit=260),
                    "metadata": {
                        "workflow_id": _diagnosis_key(envelope),
                        "source": claim.get("created_by") or "evidence_bundle",
                        "claim_type": claim.get("claim_type"),
                        "missing_evidence": claim.get("missing_evidence") or [],
                        "decision": claim.get("decision"),
                    },
                }
            )
        if findings:
            return findings

    analysis = _dump_model(payload.get("analysis_artifact"))
    inspection = _dump_model(payload.get("inspection_artifact"))
    source = analysis or inspection
    title = source.get("conclusion") or source.get("summary") or envelope.final_answer
    if not title:
        return []
    evidence_ids = [item["id"] for item in evidence if item.get("status") == "available"]
    confidence_label = _confidence_label(source.get("confidence"))
    confidence_score = _confidence_score(source.get("confidence"))
    return [
        {
            "id": "finding_001",
            "finding_id": "finding_001",
            "title": _compact_text(title, limit=80),
            "description": _compact_text(source.get("conclusion") or source.get("summary") or envelope.final_answer, limit=260),
            "severity": _severity_from_payload(payload),
            "confidence": confidence_label,
            "confidence_score": confidence_score,
            "evidence_ids": evidence_ids,
            "text": _compact_text(source.get("conclusion") or source.get("summary") or envelope.final_answer, limit=260),
            "metadata": {
                "workflow_id": _diagnosis_key(envelope),
                "source": "analysis_artifact" if analysis else "inspection_artifact",
            },
        }
    ]


def _severity_from_claim(claim: dict[str, Any], payload: dict[str, Any]) -> str:
    claim_type = str(claim.get("claim_type") or "")
    statement = str(claim.get("statement") or "")
    if claim_type == "risk_assessment" or any(keyword in statement for keyword in ("高风险", "停机", "故障")):
        return "high"
    if claim_type == "workorder_decision" and claim.get("decision") == "suggest_create":
        return "high"
    if claim_type in {"root_cause_candidate", "diagnosis_summary"}:
        return _severity_from_payload(payload)
    return "medium"


def _build_finding_links(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for item in findings:
        links.append(
            {
                "finding_id": item.get("id") or item.get("finding_id"),
                "evidence_ids": item.get("evidence_ids") or [],
                "match_score": max(len(item.get("evidence_ids") or []), 1),
                "matched_keywords": [item.get("title")] if item.get("title") else [],
            }
        )
    return links


def _build_timeline(envelope: DiagnosisArtifactEnvelope, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = envelope.payload or {}
    events: list[dict[str, Any]] = []
    evidence_ids = [item["id"] for item in evidence]
    if payload.get("sql_artifact"):
        events.append(
            {
                "time": envelope.created_at,
                "event": "完成数据查询与运行数据整理",
                "type": "sql_result",
                "evidence_ids": evidence_ids[:1],
            }
        )
    if payload.get("knowledge_artifact"):
        events.append(
            {
                "time": envelope.created_at,
                "event": "完成知识库检索与辅助依据整理",
                "type": "knowledge_lookup",
                "evidence_ids": evidence_ids[1:2],
            }
        )
    if payload.get("analysis_artifact") or payload.get("inspection_artifact"):
        events.append(
            {
                "time": envelope.created_at,
                "event": "形成结构化诊断结论",
                "type": "finding",
                "evidence_ids": evidence_ids,
            }
        )
    if payload.get("report_artifact"):
        events.append(
            {
                "time": envelope.created_at,
                "event": "生成报告产物",
                "type": "report",
                "evidence_ids": evidence_ids,
            }
        )
    return events


def _build_governance(envelope: DiagnosisArtifactEnvelope) -> dict[str, Any]:
    payload = envelope.payload or {}
    analysis = _dump_model(payload.get("analysis_artifact"))
    inspection = _dump_model(payload.get("inspection_artifact"))
    recommendations = analysis.get("recommendations") or inspection.get("suggested_actions") or []
    risk_level = inspection.get("risk_level") or _severity_from_payload(payload)
    warnings: list[str] = []
    if analysis.get("risk_notice"):
        warnings.append(str(analysis["risk_notice"]))
    return {
        "risk_level": risk_level or "low",
        "suggested_actions": recommendations if isinstance(recommendations, list) else [str(recommendations)],
        "next_steps": [],
        "warnings": warnings,
    }


def build_diagnosis_contract_payload(envelope: DiagnosisArtifactEnvelope | None) -> dict[str, Any]:
    """把单 Agent 诊断产物转成前端共同消费的结构化字段。"""

    if envelope is None:
        return {}
    evidence = _build_standard_evidence(envelope)
    findings = _build_findings(envelope, evidence)
    finding_links = _build_finding_links(findings)
    timeline = _build_timeline(envelope, evidence)
    artifacts = _build_artifacts(envelope)
    governance = _build_governance(envelope)
    workflow_id = _diagnosis_key(envelope)
    scenario_result = {
        "scenario": workflow_id,
        "status": "completed",
        "summary": envelope.request_summary or _compact_text(envelope.final_answer),
        "payload": envelope.payload or {},
        "artifacts": artifacts,
        "metadata": {
            "thread_id": envelope.thread_id,
            "created_at": envelope.created_at,
        },
    }
    workflow_result = {
        "workflow_id": workflow_id,
        "scenario": workflow_id,
        "status": "completed",
        "summary": envelope.request_summary or _compact_text(envelope.final_answer),
        "findings": findings,
        "evidence": evidence,
        "timeline": timeline,
        "artifacts": artifacts,
        "governance": governance,
        "error": None,
        "metadata": {
            "thread_id": envelope.thread_id,
            "created_at": envelope.created_at,
            "report_filename": envelope.report_filename,
        },
    }
    return {
        "workflow_result": workflow_result,
        "workflow_envelope": workflow_result,
        "scenario_result": scenario_result,
        "artifacts": artifacts,
        "timeline": timeline,
        "governance": governance,
        "evidences": evidence,
        "normalized_evidences": evidence,
        "evidence_count": len(evidence),
        "findings": findings,
        "finding_links": finding_links,
    }
