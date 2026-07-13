"""Canonical Agent Engine V2 artifact creation and typed access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar, cast
from uuid import uuid4

from pydantic import ValidationError

from fault_diagnosis.domain.artifacts import (
    AnalysisArtifactPayload,
    ArtifactEnvelope,
    ArtifactLineage,
    ArtifactManifest,
    ArtifactPayload,
    ComparisonArtifactPayload,
    KnowledgeArtifactPayload,
    ReportArtifactPayload,
    SqlArtifactPayload,
    WorkorderArtifactPayload,
)


_NODE_ARTIFACT_TYPES = {
    "sql": "sql_artifact",
    "rag": "knowledge_artifact",
    "analysis": "analysis_artifact",
    "comparison": "comparison_artifact",
    "report": "report_artifact",
    "workorder": "workorder_artifact",
}

_PAYLOAD = TypeVar("_PAYLOAD", bound=ArtifactPayload)


@dataclass(frozen=True)
class ArtifactPayloadError(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def require_payload(envelope: ArtifactEnvelope, expected: type[_PAYLOAD]) -> _PAYLOAD:
    """The only internal entrypoint allowed to expose an envelope payload."""

    payload = envelope.payload
    if not isinstance(payload, expected):
        raise ArtifactPayloadError(
            "artifact_payload_type_mismatch",
            f"Artifact {envelope.artifact_id} payload is {type(payload).__name__}, expected {expected.__name__}.",
        )
    try:
        expected.model_validate(payload.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise ArtifactPayloadError(
            "artifact_payload_invalid",
            f"Artifact {envelope.artifact_id} payload is incomplete or invalid.",
        ) from exc
    return cast(_PAYLOAD, payload)


def artifact_id_factory(artifact_type: str) -> str:
    prefix = str(artifact_type or "artifact").removesuffix("_artifact")
    return f"art_{prefix}_{uuid4().hex}"


def allocate_node_artifact_id(node: dict[str, Any]) -> str:
    """Single Node Artifact ID allocation entrypoint."""

    artifact_type = _NODE_ARTIFACT_TYPES.get(str(node.get("node_type") or ""))
    if not artifact_type:
        return ""
    existing = str(node.get("artifact_id") or "")
    if existing:
        return existing
    inputs = dict(node.get("inputs") or {})
    reused = str(inputs.get("reuse_existing_artifact_id") or inputs.get("artifact_id") or "")
    artifact_id = reused or artifact_id_factory(artifact_type)
    node["artifact_id"] = artifact_id
    inputs["artifact_id"] = artifact_id
    node["inputs"] = inputs
    return artifact_id


def build_node_artifact_envelope(
    *,
    node: dict[str, Any],
    state: Any,
    node_status: str,
    evidence_refs: list[str],
    payload: ArtifactPayload | None,
) -> ArtifactEnvelope | None:
    artifact_id = str(node.get("artifact_id") or "")
    node_type = str(node.get("node_type") or "")
    artifact_type = _NODE_ARTIFACT_TYPES.get(node_type)
    if not artifact_id or not artifact_type or payload is None:
        return None
    if payload.payload_type != artifact_type:
        raise ArtifactPayloadError(
            "artifact_payload_type_mismatch",
            f"Node {node_type} cannot emit {payload.payload_type}.",
        )

    source_ids = source_artifact_ids(node_type, node=node, state=state)
    devices = _devices(node, payload)
    fault_codes = _fault_codes(node, payload)
    source_tables = _source_tables(payload, source_ids=source_ids, state=state)
    lineage_status = (
        "complete"
        if node_status == "completed"
        and _lineage_is_complete(
            payload,
            devices=devices,
            source_ids=source_ids,
            evidence_refs=evidence_refs,
        )
        else "invalid"
    )
    lineage = ArtifactLineage(
        lineage_status=lineage_status,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        subject_device_refs=devices,
        fault_code_refs=fault_codes,
        source_artifact_ids=source_ids,
        source_evidence_bundle_ids=[],
        data_basis=_data_basis(payload),
        source_tables=source_tables,
        time_windows=_time_windows(payload),
        created_from_goal_ids=list(node.get("goal_ids") or []),
    )
    report = payload.report_artifact if isinstance(payload, ReportArtifactPayload) else None
    analysis = payload.structured_analysis.analysis_artifact if isinstance(payload, AnalysisArtifactPayload) else None
    assessment = payload.runtime_status_assessment if isinstance(payload, SqlArtifactPayload) else None
    knowledge = payload.knowledge_artifact if isinstance(payload, KnowledgeArtifactPayload) else None
    manifest = ArtifactManifest(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        thread_id=str(state.thread_id or ""),
        turn_id=str(state.request_id or ""),
        request_id=str(state.request_id or ""),
        trace_id=str(state.trace_id or ""),
        produced_by_skill=str(node.get("skill") or ""),
        produced_by_nodes=[str(node.get("node_id") or "")],
        status="completed" if lineage_status == "complete" else "failed",
        artifact_status="complete" if lineage_status == "complete" else "failed",
        followupable=lineage_status == "complete",
        reportable=lineage_status == "complete" and artifact_type in {
            "sql_artifact", "analysis_artifact", "comparison_artifact", "report_artifact"
        },
        actionable=lineage_status == "complete" and artifact_type in {
            "analysis_artifact", "report_artifact", "workorder_artifact"
        },
        device_refs=devices,
        fault_code_refs=fault_codes,
        source_table=(source_tables or [""])[0],
        time_window=(_time_windows(payload) or [{}])[0],
        data_basis=(_data_basis(payload) or [{}])[0],
        requested_window=_sql_window(payload, "requested_window"),
        resolved_window=_sql_window(payload, "resolved_window"),
        data_window=_sql_window(payload, "resolved_window"),
        latest_sample_time=_latest_sample_time(payload),
        sample_count=_sample_count(payload),
        runtime_status=assessment.runtime_status if assessment is not None else "unknown",
        freshness=_freshness(payload),
        currentness=_currentness(payload),
        key_findings=_key_findings(payload),
        supporting_evidence_ids=list(evidence_refs),
        evidence_refs=list(evidence_refs),
        diagnosis_summary=analysis.conclusion if analysis is not None else "",
        findings=_key_findings(payload),
        probable_causes=list(analysis.probable_causes) if analysis is not None else [],
        recommendations=list(analysis.recommendations) if analysis is not None else [],
        report_url=str(report.report_url or "") if report is not None else "",
        report_filename=str(report.report_filename or "") if report is not None else "",
        linked_analysis_artifact_id=_linked_source_id(state, source_ids, "analysis_artifact"),
        linked_sql_artifact_id=_linked_source_id(state, source_ids, "sql_artifact"),
        parsed_manual_fields=_knowledge_projection(knowledge),
        supported_followup_capabilities=_followups(artifact_type),
        available_followups=_followups(artifact_type),
        available_actions=["create_workorder_draft"] if artifact_type in {"analysis_artifact", "report_artifact"} else [],
        draft_only=artifact_type == "workorder_artifact",
        manual_confirmation_required=artifact_type == "workorder_artifact",
        dispatch_forbidden=artifact_type == "workorder_artifact",
        owner_user_id=str(getattr(state.auth_context, "user_id", "") or ""),
        lineage=lineage,
    )
    return ArtifactEnvelope(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        owner_user_id=manifest.owner_user_id,
        thread_id=str(state.thread_id or ""),
        request_id=str(state.request_id or ""),
        trace_id=str(state.trace_id or ""),
        turn_id=str(state.request_id or ""),
        produced_by_node=str(node.get("node_id") or ""),
        payload=payload,
        manifest=manifest,
        lineage=lineage,
        status="complete" if lineage_status == "complete" else "failed",
    )


def source_artifact_ids(node_type: str, *, node: dict[str, Any], state: Any) -> list[str]:
    refs: list[str] = []
    inputs = dict(node.get("inputs") or {})
    for key in ("target_artifact_id", "source_artifact_id"):
        value = str(inputs.get(key) or "")
        if value:
            refs.append(value)
    if not refs:
        for item in inputs.get("source_artifact_refs", []) or []:
            if isinstance(item, dict) and item.get("artifact_id"):
                refs.append(str(item["artifact_id"]))
    allowed = {
        "analysis": {"sql_artifact", "knowledge_artifact"},
        "comparison": {"sql_artifact"},
        "report": {"analysis_artifact", "comparison_artifact", "sql_artifact"},
        "workorder": {"analysis_artifact", "report_artifact", "sql_artifact"},
    }.get(node_type, set())
    ancestor_nodes = _ancestor_node_ids(state.plan, str(node.get("node_id") or ""))
    for result in state.node_results:
        if result.node_id not in ancestor_nodes or not result.artifact_id:
            continue
        envelope = state.artifact_registry.get(result.artifact_id)
        if envelope is not None and envelope.artifact_type in allowed and envelope.status == "complete":
            refs.append(envelope.artifact_id)
    return list(dict.fromkeys(item for item in refs if item))


def source_envelopes(node_type: str, *, node: dict[str, Any], state: Any) -> list[ArtifactEnvelope]:
    result: list[ArtifactEnvelope] = []
    pending = list(source_artifact_ids(node_type, node=node, state=state))
    seen: set[str] = set()
    while pending:
        artifact_id = pending.pop(0)
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        envelope = state.artifact_registry.get(artifact_id)
        if envelope is None:
            continue
        result.append(envelope)
        pending.extend(envelope.lineage.source_artifact_ids)
    return result


def hydrate_artifact_lineage(state: Any, artifact_id: str, _visiting: set[str] | None = None) -> bool:
    """Load one exact persisted artifact and its declared ancestors into the registry."""

    wanted = str(artifact_id or "")
    if not wanted:
        return False
    visiting = set(_visiting or set())
    if wanted in visiting:
        return False
    visiting.add(wanted)
    envelope = state.artifact_registry.get(wanted)
    if envelope is None:
        from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import get_artifact_by_id

        record = get_artifact_by_id(str(state.thread_id or ""), wanted)
        if record is None or record.artifact_envelope is None:
            return False
        envelope = record.artifact_envelope
    if envelope.status != "complete" or envelope.lineage.lineage_status != "complete":
        return False
    state.artifact_registry[envelope.artifact_id] = envelope
    for source_id in envelope.lineage.source_artifact_ids:
        if not hydrate_artifact_lineage(state, source_id, visiting):
            return False
    return True


def _ancestor_node_ids(plan: Any, node_id: str) -> set[str]:
    parents: dict[str, set[str]] = {}
    for edge in plan.edges:
        source = str(edge.get("from") or edge.get("source") or "")
        target = str(edge.get("to") or edge.get("target") or "")
        if source and target:
            parents.setdefault(target, set()).add(source)
    result: set[str] = set()
    pending = list(parents.get(node_id, set()))
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        pending.extend(parents.get(current, set()))
    return result


def _lineage_is_complete(
    payload: ArtifactPayload,
    *,
    devices: list[str],
    source_ids: list[str],
    evidence_refs: list[str],
) -> bool:
    if isinstance(payload, SqlArtifactPayload):
        return bool(devices and payload.sql_artifact.source_table and evidence_refs)
    if isinstance(payload, KnowledgeArtifactPayload):
        return bool(payload.knowledge_artifact.fault_code_entries and evidence_refs)
    if isinstance(payload, AnalysisArtifactPayload):
        return bool(devices and source_ids and evidence_refs)
    if isinstance(payload, ComparisonArtifactPayload):
        return bool(len(devices) >= 2 and len(source_ids) >= 2 and payload.comparison_artifact.comparison_dimensions)
    if isinstance(payload, ReportArtifactPayload):
        report = payload.report_artifact
        return bool(source_ids and report.success and (report.report_url or report.report_filename))
    if isinstance(payload, WorkorderArtifactPayload):
        return bool(len(devices) == 1 and source_ids and payload.workorder_draft)
    return False


def _devices(node: dict[str, Any], payload: ArtifactPayload) -> list[str]:
    values = [str(item) for item in (node.get("inputs") or {}).get("device_refs", []) if str(item)]
    if isinstance(payload, SqlArtifactPayload) and payload.runtime_status_assessment.device:
        values.append(payload.runtime_status_assessment.device)
    if isinstance(payload, ComparisonArtifactPayload):
        values.extend(payload.comparison_artifact.devices)
    return list(dict.fromkeys(item for item in values if item))


def _fault_codes(node: dict[str, Any], payload: ArtifactPayload) -> list[str]:
    values = [str(item) for item in (node.get("inputs") or {}).get("fault_code_refs", []) if str(item)]
    if isinstance(payload, KnowledgeArtifactPayload):
        values.extend(payload.knowledge_artifact.fault_codes)
    if isinstance(payload, WorkorderArtifactPayload) and payload.workorder_draft.fault_code:
        values.append(payload.workorder_draft.fault_code)
    return list(dict.fromkeys(item for item in values if item))


def _source_tables(payload: ArtifactPayload, *, source_ids: list[str], state: Any) -> list[str]:
    if isinstance(payload, SqlArtifactPayload):
        return [payload.sql_artifact.source_table] if payload.sql_artifact.source_table else []
    values: list[str] = []
    for artifact_id in source_ids:
        envelope = state.artifact_registry.get(artifact_id)
        if envelope is not None:
            values.extend(envelope.lineage.source_tables)
    return list(dict.fromkeys(item for item in values if item))


def _data_basis(payload: ArtifactPayload) -> list[dict[str, Any]]:
    if not isinstance(payload, SqlArtifactPayload):
        return []
    return [payload.runtime_status_assessment.data_basis.model_dump(mode="json", exclude_none=True)]


def _time_windows(payload: ArtifactPayload) -> list[dict[str, Any]]:
    if not isinstance(payload, SqlArtifactPayload) or payload.runtime_status_assessment.data_basis.resolved_window is None:
        return []
    return [payload.runtime_status_assessment.data_basis.resolved_window.model_dump(mode="json")]


def _sql_window(payload: ArtifactPayload, field: str) -> dict[str, Any]:
    if not isinstance(payload, SqlArtifactPayload):
        return {}
    value = getattr(payload.sql_artifact, field)
    return dict(value) if isinstance(value, dict) else {}


def _latest_sample_time(payload: ArtifactPayload) -> str:
    if not isinstance(payload, SqlArtifactPayload):
        return ""
    value = payload.runtime_status_assessment.data_basis.latest_sample_time
    return value.isoformat(sep=" ") if value is not None else payload.sql_artifact.latest_sample_time


def _sample_count(payload: ArtifactPayload) -> int:
    if isinstance(payload, SqlArtifactPayload):
        return payload.runtime_status_assessment.sample_count
    return 0


def _key_findings(payload: ArtifactPayload) -> list[str]:
    if isinstance(payload, SqlArtifactPayload):
        return list(payload.runtime_status_assessment.key_findings)
    if isinstance(payload, AnalysisArtifactPayload):
        return [item.summary for item in payload.structured_analysis.assessment.findings]
    return []


def _freshness(payload: ArtifactPayload) -> str:
    if isinstance(payload, SqlArtifactPayload):
        return payload.runtime_status_assessment.data_basis.freshness
    return "unknown"


def _currentness(payload: ArtifactPayload) -> str:
    if isinstance(payload, SqlArtifactPayload):
        return payload.runtime_status_assessment.data_basis.resolution_mode
    if isinstance(payload, AnalysisArtifactPayload):
        return payload.structured_analysis.assessment.currentness_level
    return ""


def _knowledge_projection(knowledge: Any) -> dict[str, Any]:
    if knowledge is None:
        return {}
    return {
        "fault_codes": list(knowledge.fault_codes),
        "entries": [item.model_dump(mode="json", exclude_none=True) for item in knowledge.fault_code_entries],
    }


def _linked_source_id(state: Any, source_ids: list[str], artifact_type: str) -> str:
    matches = [
        artifact_id
        for artifact_id in source_ids
        if (envelope := state.artifact_registry.get(artifact_id)) is not None
        and envelope.artifact_type == artifact_type
    ]
    return matches[0] if len(matches) == 1 else ""


def _followups(artifact_type: str) -> list[str]:
    return {
        "sql_artifact": ["check_runtime_status", "diagnose_fault", "generate_report"],
        "knowledge_artifact": ["explain_fault_code"],
        "analysis_artifact": ["generate_report", "create_workorder_draft"],
        "comparison_artifact": ["generate_report"],
        "report_artifact": ["create_workorder_draft"],
        "workorder_artifact": ["confirm_workorder_draft"],
    }.get(artifact_type, [])
