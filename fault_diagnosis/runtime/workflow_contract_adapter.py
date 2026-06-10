"""Workflow V1 产物到第四阶段结构化契约的消费侧适配器。"""

from __future__ import annotations

from typing import Any

from ..workflows.contracts import WorkflowArtifactEnvelope


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


def _workflow_key(envelope: WorkflowArtifactEnvelope) -> str:
    workflow_type = envelope.workflow_type
    return getattr(workflow_type, "value", str(workflow_type))


def _confidence_score(value: Any) -> float | None:
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


def _build_standard_evidence(envelope: WorkflowArtifactEnvelope) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, item in enumerate(envelope.evidence or [], start=1):
        data = _dump_model(item)
        source_type = str(data.get("source_type") or "generic").strip() or "generic"
        evidence_id = f"evidence_{source_type}_{index:03d}"
        items.append(
            {
                "id": evidence_id,
                "evidence_id": evidence_id,
                "type": source_type,
                "source": source_type,
                "title": data.get("title") or "证据项",
                "summary": _compact_text(data.get("content") or data.get("title"), limit=220),
                "metadata": {
                    "importance": data.get("importance") or "medium",
                    "workflow_id": _workflow_key(envelope),
                    "thread_id": envelope.thread_id,
                },
                "artifact_id": None,
                "status": "available" if data.get("content") else "unavailable",
            }
        )
    return items


def _build_artifacts(envelope: WorkflowArtifactEnvelope) -> list[dict[str, Any]]:
    payload = envelope.payload or {}
    report_artifact = _dump_model(payload.get("report_artifact"))
    artifacts: list[dict[str, Any]] = []
    if envelope.report_filename or report_artifact:
        artifact_id = "artifact_report_001"
        artifacts.append(
            {
                "id": artifact_id,
                "type": "report",
                "title": "故障诊断报告" if _workflow_key(envelope) == "fault_diagnosis" else "Workflow 报告",
                "summary": _compact_text(report_artifact.get("save_result") or envelope.final_answer),
                "path": envelope.report_filename,
                "content": None,
                "metadata": report_artifact,
                "created_at": envelope.created_at,
            }
        )
    return artifacts


def _build_findings(envelope: WorkflowArtifactEnvelope, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = envelope.payload or {}
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
                "workflow_id": _workflow_key(envelope),
                "source": "analysis_artifact" if analysis else "inspection_artifact",
            },
        }
    ]


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


def _build_timeline(envelope: WorkflowArtifactEnvelope, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _build_governance(envelope: WorkflowArtifactEnvelope) -> dict[str, Any]:
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


def build_phase4_contract_payload(envelope: WorkflowArtifactEnvelope | None) -> dict[str, Any]:
    """把 workflow artifact 转成第四阶段前后端共同消费的标准字段。"""

    if envelope is None:
        return {}
    evidence = _build_standard_evidence(envelope)
    findings = _build_findings(envelope, evidence)
    finding_links = _build_finding_links(findings)
    timeline = _build_timeline(envelope, evidence)
    artifacts = _build_artifacts(envelope)
    governance = _build_governance(envelope)
    workflow_id = _workflow_key(envelope)
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
