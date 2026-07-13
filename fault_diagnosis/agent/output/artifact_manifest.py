"""Build stable artifact manifests from V2 runtime artifacts."""

from __future__ import annotations

import re
from typing import Any

from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    EvidenceBundle,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderDraftArtifact,
    WorkOrderSuggestion,
)
from fault_diagnosis.domain.diagnosis.runtime_status import RuntimeComparisonArtifact, RuntimeStatusAssessment


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
    goal_by_type = _goals_by_type(node_results or [])
    manifests: list[ArtifactManifest] = []

    sql_entries = _sql_entries(artifact_map)
    for runtime_artifact_id, sql, runtime_assessment in sql_entries:
        basis = runtime_assessment.data_basis if runtime_assessment is not None else None
        supported_followups = ["check_runtime_status"]
        if basis is not None and basis.usable_for_report:
            supported_followups.append("generate_report")
        manifests.append(
            ArtifactManifest(
                artifact_id=runtime_artifact_id or sql.artifact_id or _artifact_id("sql", trace_id, request_id, f"{sql.source_table}:{getattr(runtime_assessment, 'device', '')}"),
                artifact_type="sql_artifact",
                thread_id=thread_id,
                request_id=request_id,
                trace_id=trace_id,
                produced_by_skill="runtime_status",
                produced_by_nodes=node_by_type.get("sql", []),
                status="completed" if sql.success else "failed",
                followupable=bool(sql.success and runtime_assessment is not None),
                reportable=bool(sql.success and basis and basis.usable_for_report),
                device_refs=[runtime_assessment.device] if runtime_assessment is not None else [],
                source_table=sql.source_table,
                requested_window=dict(sql.requested_window),
                resolved_window=dict(sql.resolved_window),
                time_window=dict(sql.resolved_window),
                data_window=dict(sql.resolved_window),
                data_basis=dict(sql.data_basis),
                latest_sample_time=sql.latest_sample_time,
                sample_count=sql.sample_count,
                runtime_status=sql.runtime_status,
                freshness=str((sql.data_basis or {}).get("freshness") or "unknown"),
                currentness=str((sql.data_basis or {}).get("resolution_mode") or ""),
                status_level=sql.runtime_status,
                diagnosis_summary=(runtime_assessment.key_findings[0] if runtime_assessment and runtime_assessment.key_findings else sql.summary),
                findings=list(sql.key_findings),
                key_findings=list(sql.key_findings),
                supporting_evidence_ids=list(sql.supporting_evidence_ids),
                evidence_refs=list(sql.supporting_evidence_ids),
                evidence_bundle_id=bundle.bundle_id if bundle else "",
                available_followups=supported_followups,
                supported_followup_capabilities=supported_followups,
                authorization_scope_summary=dict(auth_summary or {}),
            )
        )

    knowledge = _model(artifact_map.get("knowledge_artifact"), KnowledgeStepArtifact)
    if knowledge is not None:
        entry = _best_entry(knowledge)
        fields = entry.model_dump(mode="json", exclude_none=True) if entry is not None else {}
        manifests.append(
            ArtifactManifest(
                artifact_id=knowledge.artifact_id or _artifact_id("knowledge", trace_id, request_id, ",".join(knowledge.fault_codes)),
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
    structured_info = _structured_info(
        structured,
        text_sources=_analysis_text_sources(analysis),
        evidence_bundle=bundle,
    )
    if analysis is not None:
        manifests.append(
            ArtifactManifest(
                artifact_id=analysis.artifact_id or _artifact_id("analysis", trace_id, request_id, analysis.conclusion),
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
                artifact_id=report.artifact_id or _artifact_id("report", trace_id, request_id, report.report_filename or "report"),
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
    comparison = _model(artifact_map.get("comparison_artifact"), RuntimeComparisonArtifact)
    if comparison is not None:
        manifests.append(
            ArtifactManifest(
                artifact_id=_artifact_id("comparison", trace_id, request_id, ":".join(comparison.devices)),
                artifact_type="comparison_artifact",
                thread_id=thread_id,
                request_id=request_id,
                trace_id=trace_id,
                produced_by_skill="runtime_status",
                produced_by_nodes=node_by_type.get("comparison", []),
                status="completed",
                followupable=True,
                reportable=True,
                device_refs=list(comparison.devices),
                diagnosis_summary=comparison.conclusion,
                findings=[item.conclusion for item in comparison.comparison_dimensions],
                authorization_scope_summary=dict(auth_summary or {}),
            )
        )

    _attach_lineage(manifests, bundle=bundle, auth_summary=auth_summary or {}, goal_by_type=goal_by_type)
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
        "supported_followup_capabilities": list(selected.supported_followup_capabilities),
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


def _goals_by_type(node_results: list[Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in node_results:
        dumped = _dump(item) or {}
        node_type = str(dumped.get("node_type") or "")
        if node_type:
            result.setdefault(node_type, []).extend(str(goal_id) for goal_id in dumped.get("goal_ids", []) if str(goal_id))
    return {key: _dedupe(value) for key, value in result.items()}


def _best_entry(artifact: KnowledgeStepArtifact) -> Any:
    exact = [item for item in artifact.fault_code_entries if item.match_type == "exact_match"]
    return (exact or list(artifact.fault_code_entries) or [None])[0]


def _structured_info(
    value: Any,
    *,
    text_sources: list[str] | None = None,
    evidence_bundle: EvidenceBundle | None = None,
) -> dict[str, Any]:
    dumped = _dump(value)
    flat = _flatten(dumped)
    text_sources = list(text_sources or [])
    if flat.get("diagnosis_summary"):
        text_sources.append(str(flat.get("diagnosis_summary") or ""))
    if flat.get("conclusion"):
        text_sources.append(str(flat.get("conclusion") or ""))
    evidence_codes = _codes_from_evidence(evidence_bundle)
    return {
        "device_refs": _dedupe([flat.get("asset"), flat.get("diagnosis_object"), flat.get("equipment_object")]),
        "fault_code_refs": _dedupe(
            [
                *_as_list(flat.get("fault_code")),
                *_as_list(flat.get("fault_codes")),
                *_as_list(flat.get("event_code")),
                *_as_list(flat.get("event_codes")),
                *_as_list(flat.get("alarm_code")),
                *_as_list(flat.get("alarm_codes")),
                *_extract_codes(str(flat.get("current_event") or "")),
                *_extract_codes(" ".join(text_sources)),
                *evidence_codes,
            ]
        ),
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
        "asset", "diagnosis_object", "equipment_object", "fault_code", "fault_codes", "event_code", "event_codes",
        "alarm_code", "alarm_codes", "current_event",
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


def _analysis_text_sources(analysis: AnalysisStepArtifact | None) -> list[str]:
    if analysis is None:
        return []
    values: list[str] = [
        analysis.conclusion,
        *(analysis.basis or []),
        *(analysis.probable_causes or []),
        *(analysis.recommendations or []),
        analysis.risk_notice or "",
    ]
    return [str(item) for item in values if str(item).strip()]


def _codes_from_evidence(bundle: EvidenceBundle | None) -> list[str]:
    if bundle is None:
        return []
    codes: list[str] = []
    for item in bundle.evidence_items:
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        content = item.content if isinstance(item.content, dict) else {}
        for key in ("fault_codes", "alarm_codes", "event_codes", "fault_code", "alarm_code", "event_code"):
            codes.extend(_as_list(metadata.get(key)))
            codes.extend(_as_list(content.get(key)))
        codes.extend(_extract_codes(str(item.summary or "")))
    return _dedupe(codes)


def _extract_codes(text: str) -> list[str]:
    return _dedupe(match.upper() for match in re.findall(r"\b[AF]\d{3,5}\b", text or "", flags=re.IGNORECASE))


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


def _sql_entries(artifact_map: dict[str, Any]) -> list[tuple[str, SqlStepArtifact, RuntimeStatusAssessment | None]]:
    raw_sql = artifact_map.get("sql_artifacts")
    assessments = artifact_map.get("runtime_status_assessments")
    result: list[tuple[str, SqlStepArtifact, RuntimeStatusAssessment | None]] = []
    if isinstance(raw_sql, dict):
        assessment_values = list(assessments.values()) if isinstance(assessments, dict) else []
        for index, (artifact_id, value) in enumerate(raw_sql.items()):
            sql = _model(value, SqlStepArtifact)
            if sql is None:
                continue
            assessment = _model(assessment_values[index], RuntimeStatusAssessment) if index < len(assessment_values) else None
            result.append((str(artifact_id), sql, assessment))
    if result:
        return result
    sql = _model(artifact_map.get("sql_artifact"), SqlStepArtifact)
    assessment = _model(artifact_map.get("runtime_status_assessment"), RuntimeStatusAssessment)
    return [("", sql, assessment)] if sql is not None else []


def _attach_lineage(
    manifests: list[ArtifactManifest],
    *,
    bundle: EvidenceBundle | None,
    auth_summary: dict[str, Any],
    goal_by_type: dict[str, list[str]],
) -> None:
    sql_ids = [item.artifact_id for item in manifests if item.artifact_type == "sql_artifact"]
    knowledge_ids = [item.artifact_id for item in manifests if item.artifact_type == "knowledge_artifact"]
    analysis_ids = [item.artifact_id for item in manifests if item.artifact_type in {"analysis_artifact", "structured_analysis_artifact"}]
    report_ids = [item.artifact_id for item in manifests if item.artifact_type == "report_artifact"]
    comparison_ids = [item.artifact_id for item in manifests if item.artifact_type == "comparison_artifact"]
    sql_devices = _dedupe(device for item in manifests if item.artifact_type == "sql_artifact" for device in item.device_refs)
    for item in manifests:
        if item.artifact_type in {"analysis_artifact", "structured_analysis_artifact", "report_artifact"} and not item.device_refs:
            item.device_refs = list(sql_devices)
        if item.artifact_type == "sql_artifact":
            sources: list[str] = []
        elif item.artifact_type == "knowledge_artifact":
            sources = []
        elif item.artifact_type in {"analysis_artifact", "structured_analysis_artifact"}:
            sources = [*sql_ids, *knowledge_ids]
        elif item.artifact_type == "comparison_artifact":
            sources = list(sql_ids)
        elif item.artifact_type == "report_artifact":
            sources = [*analysis_ids, *comparison_ids] or list(sql_ids)
        elif item.artifact_type == "workorder_artifact":
            sources = [*report_ids, *analysis_ids]
        else:
            sources = []
        complete = bool(item.artifact_id) and (
            item.artifact_type == "knowledge_artifact"
            or bool(item.device_refs)
        )
        if item.artifact_type == "workorder_artifact":
            complete = complete and len(item.device_refs) == 1 and bool(sources)
        if item.artifact_type in {"analysis_artifact", "structured_analysis_artifact", "comparison_artifact", "report_artifact"}:
            complete = complete and bool(sources)
        item.owner_user_id = str(auth_summary.get("user_id") or "")
        item.owner_session_id = str(auth_summary.get("session_id") or "")
        item.lineage = ArtifactLineage(
            lineage_status="complete" if complete else "invalid",
            artifact_id=item.artifact_id,
            artifact_type=item.artifact_type,
            subject_device_refs=list(item.device_refs),
            fault_code_refs=list(item.fault_code_refs),
            source_artifact_ids=_dedupe(sources),
            source_evidence_bundle_ids=[bundle.bundle_id] if bundle and bundle.bundle_id else [],
            data_basis=(
                [dict(item.data_basis)]
                if item.data_basis
                else [basis for source in manifests if source.artifact_id in sources for basis in source.lineage.data_basis]
            ),
            source_tables=(
                [item.source_table]
                if item.source_table
                else _dedupe(
                    table
                    for source in manifests
                    if source.artifact_id in sources
                    for table in ([source.source_table] if source.source_table else source.lineage.source_tables)
                )
            ),
            time_windows=(
                [dict(item.resolved_window or item.time_window)]
                if (item.resolved_window or item.time_window)
                else [window for source in manifests if source.artifact_id in sources for window in source.lineage.time_windows]
            ),
            created_from_goal_ids=list(goal_by_type.get(_node_type_for_artifact(item.artifact_type), [])),
        )


def _node_type_for_artifact(artifact_type: str) -> str:
    return {
        "sql_artifact": "sql",
        "knowledge_artifact": "rag",
        "analysis_artifact": "analysis",
        "structured_analysis_artifact": "analysis",
        "comparison_artifact": "comparison",
        "report_artifact": "report",
        "workorder_artifact": "workorder",
    }.get(artifact_type, "")


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
