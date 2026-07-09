"""Build stable artifact manifests from V2 runtime artifacts."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.agent.contracts import ArtifactManifest
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    EvidenceBundle,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderDraftArtifact,
    WorkOrderSuggestion,
)


def build_artifact_manifests(
    *,
    thread_id: str,
    artifacts: dict[str, Any] | None = None,
    evidence_bundle: EvidenceBundle | dict[str, Any] | None = None,
    node_results: list[Any] | None = None,
    trace: dict[str, Any] | None = None,
    request_id: str = "",
    auth_summary: dict[str, Any] | None = None,
) -> list[ArtifactManifest]:
    artifact_map = dict(artifacts or {})
    bundle = _bundle(evidence_bundle)
    trace = trace if isinstance(trace, dict) else {}
    trace_id = str(trace.get("trace_id") or (bundle.trace_id if bundle else "") or "")
    node_by_type = _nodes_by_type(node_results or [])
    manifests: list[ArtifactManifest] = []

    sql = _model(artifact_map.get("sql_artifact"), SqlStepArtifact)
    if sql is not None:
        manifests.append(
            ArtifactManifest(
                artifact_id=_artifact_id("sql", trace_id, request_id, sql.source_table),
                artifact_type="sql_artifact",
                thread_id=thread_id,
                request_id=request_id,
                trace_id=trace_id,
                produced_by_skill="runtime_status",
                produced_by_nodes=node_by_type.get("sql", []),
                status="completed" if sql.success else "failed",
                followupable=bool(sql.success),
                reportable=bool(sql.success),
                source_table=sql.source_table,
                freshness=_freshness_from_text(sql.data_state),
                currentness=sql.data_state,
                diagnosis_summary=sql.summary,
                findings=_nonempty([sql.summary, sql.result_preview]),
                evidence_refs=_evidence_refs(bundle, evidence_types={"device_status", "sql_result"}),
                evidence_bundle_id=bundle.bundle_id if bundle else "",
                authorization_scope_summary=dict(auth_summary or {}),
            )
        )

    knowledge = _model(artifact_map.get("knowledge_artifact"), KnowledgeStepArtifact)
    if knowledge is not None:
        entry = _best_entry(knowledge)
        fields = entry.model_dump(mode="json", exclude_none=True) if entry is not None else {}
        manifests.append(
            ArtifactManifest(
                artifact_id=_artifact_id("knowledge", trace_id, request_id, ",".join(knowledge.fault_codes)),
                artifact_type="knowledge_artifact",
                thread_id=thread_id,
                request_id=request_id,
                trace_id=trace_id,
                produced_by_skill="fault_code_explain",
                produced_by_nodes=node_by_type.get("rag", []),
                status="completed" if knowledge.success else "failed",
                followupable=bool(knowledge.success and knowledge.fault_codes),
                fault_code_refs=list(knowledge.fault_codes),
                diagnosis_summary=_first([getattr(entry, "meaning", ""), getattr(entry, "title", ""), knowledge.query]),
                findings=_nonempty([getattr(entry, "meaning", ""), getattr(entry, "cause", ""), getattr(entry, "remedy", "")]),
                recommendations=_nonempty([getattr(entry, "remedy", "")]),
                evidence_refs=_evidence_refs(bundle, evidence_types={"fault_code_reference", "manual_reference"}),
                evidence_bundle_id=bundle.bundle_id if bundle else "",
                source_file=str(getattr(entry, "source_file", "") or ""),
                source_page=str(getattr(entry, "page", "") or ""),
                parsed_manual_fields=fields,
                available_followups=["expand_previous_answer", "show_manual_fields"],
                authorization_scope_summary=dict(auth_summary or {}),
            )
        )

    analysis = _model(artifact_map.get("analysis_artifact"), AnalysisStepArtifact)
    structured = artifact_map.get("structured_analysis_artifact")
    structured_info = _structured_info(structured)
    if analysis is not None:
        manifests.append(
            ArtifactManifest(
                artifact_id=_artifact_id("analysis", trace_id, request_id, analysis.conclusion),
                artifact_type="analysis_artifact",
                thread_id=thread_id,
                request_id=request_id,
                trace_id=trace_id,
                produced_by_skill="root_cause",
                produced_by_nodes=node_by_type.get("analysis", []),
                status="completed" if analysis.success else "failed",
                followupable=bool(analysis.success),
                reportable=bool(analysis.success),
                actionable=bool(analysis.success),
                device_refs=structured_info["device_refs"],
                fault_code_refs=structured_info["fault_code_refs"],
                latest_sample_time=structured_info["latest_sample_time"],
                freshness=structured_info["freshness"],
                severity=structured_info["severity"],
                risk_level=structured_info["risk_level"],
                status_level=structured_info["status_level"],
                diagnosis_summary=analysis.conclusion,
                findings=list(analysis.basis),
                probable_causes=list(analysis.probable_causes),
                recommendations=list(analysis.recommendations),
                evidence_refs=_evidence_refs(bundle),
                evidence_bundle_id=bundle.bundle_id if bundle else "",
                available_followups=["expand_previous_answer", "generate_report_from_previous"],
                available_actions=["decide_workorder", "create_workorder_draft"],
                authorization_scope_summary=dict(auth_summary or {}),
            )
        )
    if structured is not None:
        manifests.append(
            ArtifactManifest(
                artifact_id=_artifact_id("structured_analysis", trace_id, request_id, structured_info["diagnosis_summary"]),
                artifact_type="structured_analysis_artifact",
                thread_id=thread_id,
                request_id=request_id,
                trace_id=trace_id,
                produced_by_skill="root_cause",
                produced_by_nodes=node_by_type.get("analysis", []),
                status="completed",
                followupable=True,
                reportable=True,
                actionable=True,
                device_refs=structured_info["device_refs"],
                fault_code_refs=structured_info["fault_code_refs"],
                latest_sample_time=structured_info["latest_sample_time"],
                freshness=structured_info["freshness"],
                severity=structured_info["severity"],
                risk_level=structured_info["risk_level"],
                status_level=structured_info["status_level"],
                diagnosis_summary=structured_info["diagnosis_summary"],
                findings=structured_info["findings"],
                probable_causes=structured_info["probable_causes"],
                recommendations=structured_info["recommendations"],
                evidence_refs=_evidence_refs(bundle),
                evidence_bundle_id=bundle.bundle_id if bundle else "",
                available_followups=["expand_previous_answer", "generate_report_from_previous"],
                available_actions=["decide_workorder", "create_workorder_draft"],
                authorization_scope_summary=dict(auth_summary or {}),
            )
        )

    report = _model(artifact_map.get("report_artifact"), ReportStepArtifact)
    if report is not None:
        manifests.append(
            ArtifactManifest(
                artifact_id=report.report_url or report.report_filename or _artifact_id("report", trace_id, request_id, ""),
                artifact_type="report_artifact",
                thread_id=thread_id,
                request_id=request_id,
                trace_id=trace_id,
                produced_by_skill="report_generation",
                produced_by_nodes=node_by_type.get("report", []),
                status="completed" if report.success else "failed",
                followupable=bool(report.success),
                reportable=bool(report.success),
                actionable=bool(report.success),
                device_refs=structured_info["device_refs"],
                fault_code_refs=structured_info["fault_code_refs"],
                latest_sample_time=structured_info["latest_sample_time"],
                freshness=structured_info["freshness"],
                severity=structured_info["severity"],
                risk_level=structured_info["risk_level"],
                status_level=structured_info["status_level"],
                diagnosis_summary=structured_info["diagnosis_summary"],
                findings=structured_info["findings"],
                recommendations=structured_info["recommendations"],
                evidence_refs=_evidence_refs(bundle),
                evidence_bundle_id=bundle.bundle_id if bundle else "",
                report_url=report.report_url or "",
                report_filename=report.report_filename or "",
                linked_analysis_artifact_id=_latest_id(manifests, "analysis_artifact"),
                linked_sql_artifact_id=_latest_id(manifests, "sql_artifact"),
                linked_evidence_bundle_id=bundle.bundle_id if bundle else "",
                available_followups=["expand_previous_answer", "generate_report_from_previous"],
                available_actions=["decide_workorder", "create_workorder_draft"],
                authorization_scope_summary=dict(auth_summary or {}),
            )
        )

    suggestion = _model(artifact_map.get("workorder_suggestion"), WorkOrderSuggestion)
    draft = _model(artifact_map.get("workorder_draft"), WorkOrderDraftArtifact)
    if suggestion is not None or draft is not None:
        manifests.append(
            ArtifactManifest(
                artifact_id=(draft.draft_id if draft else "") or _artifact_id("workorder", trace_id, request_id, ""),
                artifact_type="workorder_artifact",
                thread_id=thread_id,
                request_id=request_id,
                trace_id=trace_id,
                produced_by_skill="workorder_decision",
                produced_by_nodes=node_by_type.get("workorder", []),
                status="completed",
                followupable=True,
                actionable=False,
                device_refs=_nonempty([getattr(suggestion, "equipment_object", ""), getattr(draft, "device", "")]),
                fault_code_refs=_nonempty([getattr(suggestion, "fault_code", ""), getattr(draft, "fault_code", "")]),
                severity=str(getattr(suggestion, "risk_level", "") or ""),
                risk_level=str(getattr(suggestion, "risk_level", "") or ""),
                diagnosis_summary=str(getattr(suggestion, "diagnosis_conclusion", "") or getattr(suggestion, "reason", "") or ""),
                findings=list(getattr(suggestion, "key_evidence", []) or []),
                recommendations=list(getattr(suggestion, "processing_steps", []) or []),
                evidence_refs=_evidence_refs(bundle),
                evidence_bundle_id=bundle.bundle_id if bundle else "",
                available_followups=["expand_previous_answer"],
                available_actions=[],
                authorization_scope_summary=dict(auth_summary or {}),
                draft_only=True,
                manual_confirmation_required=True,
                dispatch_forbidden=True,
            )
        )
    return manifests


def latest_focus_from_manifests(manifests: list[ArtifactManifest]) -> dict[str, Any]:
    selected = select_focus_manifest(manifests)
    if selected is None:
        return {}
    return {
        "artifact_id": selected.artifact_id,
        "artifact_type": selected.artifact_type,
        "device_refs": list(selected.device_refs),
        "fault_code_refs": list(selected.fault_code_refs),
        "evidence_bundle_id": selected.evidence_bundle_id or selected.linked_evidence_bundle_id,
        "report_id": selected.report_url or selected.report_filename,
        "freshness": selected.freshness,
        "severity": selected.severity,
        "diagnosis_summary": selected.diagnosis_summary,
        "available_followups": list(selected.available_followups),
        "available_actions": list(selected.available_actions),
    }


def select_focus_manifest(manifests: list[ArtifactManifest]) -> ArtifactManifest | None:
    priority = {
        "workorder_artifact": 0,
        "report_artifact": 1,
        "structured_analysis_artifact": 2,
        "analysis_artifact": 3,
        "knowledge_artifact": 4,
        "sql_artifact": 5,
    }
    usable = [item for item in manifests if item.status == "completed" and (item.followupable or item.actionable or item.reportable)]
    return sorted(usable, key=lambda item: priority.get(item.artifact_type, 99))[0] if usable else None


def _bundle(value: EvidenceBundle | dict[str, Any] | None) -> EvidenceBundle | None:
    if isinstance(value, EvidenceBundle):
        return value
    if isinstance(value, dict) and value:
        try:
            return EvidenceBundle.model_validate(value)
        except Exception:
            return None
    return None


def _model(value: Any, model_type: Any) -> Any:
    if isinstance(value, model_type):
        return value
    if isinstance(value, dict) and value:
        try:
            return model_type.model_validate(value)
        except Exception:
            return None
    return None


def _nodes_by_type(node_results: list[Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in node_results:
        node_type = str(getattr(item, "node_type", "") or (item.get("node_type") if isinstance(item, dict) else "") or "")
        node_id = str(getattr(item, "node_id", "") or (item.get("node_id") if isinstance(item, dict) else "") or "")
        if node_type and node_id:
            result.setdefault(node_type, []).append(node_id)
    return result


def _best_entry(artifact: KnowledgeStepArtifact) -> Any:
    exact = [item for item in artifact.fault_code_entries if item.match_type == "exact_match"]
    return (exact or list(artifact.fault_code_entries) or [None])[0]


def _structured_info(value: Any) -> dict[str, Any]:
    dumped = _dump(value)
    flat = _flatten(dumped)
    return {
        "device_refs": _dedupe([flat.get("asset"), flat.get("diagnosis_object"), flat.get("equipment_object")]),
        "fault_code_refs": _dedupe([flat.get("fault_code"), flat.get("event_code"), flat.get("current_event")]),
        "latest_sample_time": _first([flat.get("latest_sample_time"), flat.get("last_sample_time"), flat.get("sample_time")]),
        "freshness": _freshness_from_text(_first([flat.get("freshness"), flat.get("freshness_label"), flat.get("data_freshness_label"), flat.get("currentness")])),
        "severity": _first([flat.get("severity"), flat.get("severity_label"), flat.get("asset_risk_label")]),
        "risk_level": _first([flat.get("risk_level"), flat.get("asset_risk_label"), flat.get("status_level")]),
        "status_level": _first([flat.get("status_level"), flat.get("data_currentness_label")]),
        "diagnosis_summary": _first([flat.get("diagnosis_summary"), flat.get("one_sentence_conclusion"), flat.get("conclusion"), flat.get("initial_assessment")]),
        "findings": _as_list(flat.get("findings") or flat.get("evidence_summary") or flat.get("key_evidence")),
        "probable_causes": _as_list(flat.get("probable_causes")),
        "recommendations": _as_list(flat.get("recommendations") or flat.get("next_action") or flat.get("recommended_action")),
    }


def _flatten(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    wanted = {
        "asset", "diagnosis_object", "equipment_object", "fault_code", "event_code", "current_event",
        "latest_sample_time", "last_sample_time", "sample_time", "freshness", "freshness_label",
        "data_freshness_label", "currentness", "severity", "severity_label", "asset_risk_label",
        "risk_level", "status_level", "data_currentness_label", "diagnosis_summary",
        "one_sentence_conclusion", "conclusion", "initial_assessment", "findings", "evidence_summary",
        "key_evidence", "probable_causes", "recommendations", "next_action", "recommended_action",
    }

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in wanted and key not in result and child not in (None, "", [], {}):
                    result[key] = child
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return result


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return value


def _evidence_refs(bundle: EvidenceBundle | None, evidence_types: set[str] | None = None) -> list[str]:
    if bundle is None:
        return []
    refs = []
    for item in bundle.evidence_items:
        if evidence_types and item.evidence_type not in evidence_types:
            continue
        if item.evidence_id:
            refs.append(item.evidence_id)
    return _dedupe(refs)


def _latest_id(manifests: list[ArtifactManifest], artifact_type: str) -> str:
    for item in reversed(manifests):
        if item.artifact_type == artifact_type:
            return item.artifact_id
    return ""


def _artifact_id(prefix: str, trace_id: str, request_id: str, seed: str) -> str:
    suffix = str(seed or trace_id or request_id or "artifact").strip().replace(" ", "_")[:48]
    return f"{prefix}:{trace_id or request_id or 'local'}:{suffix or 'artifact'}"


def _freshness_from_text(value: Any) -> str:
    text = str(value or "").lower()
    if any(marker in text for marker in ("stale", "滞后", "非实时", "不代表实时")):
        return "stale"
    if any(marker in text for marker in ("current", "当前", "ok", "fresh")):
        return "current"
    if "recent" in text or "最近" in text:
        return "recent"
    return "unknown"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _dedupe(value)
    if str(value or "").strip():
        return [str(value).strip()]
    return []


def _first(values: list[Any]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _nonempty(values: list[Any]) -> list[str]:
    return _dedupe(values)


def _dedupe(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))
