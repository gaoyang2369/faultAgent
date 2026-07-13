"""Canonical Agent Engine V2 artifact creation and validation."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .contracts import ArtifactEnvelope, ArtifactLineage, ArtifactManifest


_NODE_ARTIFACT_TYPES = {
    "sql": "sql_artifact",
    "rag": "knowledge_artifact",
    "analysis": "analysis_artifact",
    "comparison": "comparison_artifact",
    "report": "report_artifact",
    "workorder": "workorder_artifact",
}


def artifact_id_factory(artifact_type: str) -> str:
    """Allocate one opaque ID. This function is called only by the node entry."""

    prefix = str(artifact_type or "artifact").removesuffix("_artifact")
    return f"art_{prefix}_{uuid4().hex}"


def allocate_node_artifact_id(node: dict[str, Any]) -> str:
    """Single Node Artifact creation entry; downstream code only propagates its ID."""

    artifact_type = _NODE_ARTIFACT_TYPES.get(str(node.get("node_type") or ""))
    if not artifact_type:
        return ""
    existing = str(node.get("artifact_id") or "")
    if existing:
        return existing
    artifact_id = artifact_id_factory(artifact_type)
    node["artifact_id"] = artifact_id
    inputs = dict(node.get("inputs") or {})
    inputs["artifact_id"] = artifact_id
    node["inputs"] = inputs
    return artifact_id


def build_node_artifact_envelope(
    *,
    node: dict[str, Any],
    state: Any,
    node_status: str,
    evidence_refs: list[str],
) -> ArtifactEnvelope | None:
    artifact_id = str(node.get("artifact_id") or "")
    node_type = str(node.get("node_type") or "")
    artifact_type = _NODE_ARTIFACT_TYPES.get(node_type)
    if not artifact_id or not artifact_type:
        return None
    payload = _canonical_payload(node_type, state)
    if not payload:
        return None
    source_ids = _source_artifact_ids(node_type, node=node, state=state)
    devices = _devices(node, payload)
    fault_codes = _fault_codes(node, payload)
    lineage_status = "complete" if node_status == "completed" and _lineage_is_complete(
        node_type,
        payload=payload,
        devices=devices,
        source_ids=source_ids,
        evidence_refs=evidence_refs,
    ) else "invalid"
    lineage = ArtifactLineage(
        lineage_status=lineage_status,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        subject_device_refs=devices,
        fault_code_refs=fault_codes,
        source_artifact_ids=source_ids,
        source_evidence_bundle_ids=[],
        data_basis=_data_basis(payload),
        source_tables=_source_tables(payload),
        time_windows=_time_windows(payload),
        created_from_goal_ids=list(node.get("goal_ids") or []),
    )
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
        actionable=lineage_status == "complete" and artifact_type in {"analysis_artifact", "report_artifact", "workorder_artifact"},
        device_refs=devices,
        fault_code_refs=fault_codes,
        source_table=(_source_tables(payload) or [""])[0],
        time_window=(_time_windows(payload) or [{}])[0],
        data_basis=(_data_basis(payload) or [{}])[0],
        requested_window=_nested_dict(payload, "sql_artifact", "requested_window"),
        resolved_window=_nested_dict(payload, "sql_artifact", "resolved_window"),
        data_window=_nested_dict(payload, "sql_artifact", "resolved_window"),
        latest_sample_time=_latest_sample_time(payload),
        sample_count=_sample_count(payload),
        runtime_status=_runtime_status(payload),
        freshness=str(((_data_basis(payload) or [{}])[0]).get("freshness") or "unknown"),
        currentness=str(((_data_basis(payload) or [{}])[0]).get("resolution_mode") or ""),
        key_findings=_key_findings(payload),
        supporting_evidence_ids=list(evidence_refs),
        evidence_refs=list(evidence_refs),
        diagnosis_summary=_diagnosis_summary(payload),
        findings=_key_findings(payload),
        recommendations=_recommendations(payload),
        report_url=_nested_text(payload, "report_artifact", "report_url"),
        report_filename=_nested_text(payload, "report_artifact", "report_filename"),
        linked_analysis_artifact_id=_latest_source_id(state, "analysis_artifact"),
        linked_sql_artifact_id=_latest_source_id(state, "sql_artifact"),
        parsed_manual_fields=_fault_code_explanation(payload),
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


def _canonical_payload(node_type: str, state: Any) -> dict[str, Any]:
    artifacts = state.artifacts
    if node_type == "sql":
        return {
            "sql_artifact": _dump(artifacts.get("sql_artifact")),
            "runtime_status_assessment": _dump(artifacts.get("runtime_status_assessment")),
        }
    if node_type == "rag":
        knowledge = _dump(artifacts.get("knowledge_artifact"))
        return {
            "knowledge_artifact": knowledge,
            "fault_code_explanation": _structured_fault_code_explanation(knowledge),
        }
    if node_type == "analysis":
        return {
            "analysis_artifact": _dump(artifacts.get("analysis_artifact")),
            "structured_analysis": _dump(artifacts.get("structured_analysis")),
        }
    if node_type == "comparison":
        return {"comparison_artifact": _dump(artifacts.get("comparison_artifact"))}
    if node_type == "report":
        return {"report_artifact": _dump(artifacts.get("report_artifact"))}
    if node_type == "workorder":
        return {
            "workorder_draft": _dump(artifacts.get("workorder_draft")),
            "pending_action": _dump(artifacts.get("workorder_pending_action")),
        }
    return {}


def _structured_fault_code_explanation(knowledge: Any) -> dict[str, Any]:
    if not isinstance(knowledge, dict):
        return {}
    entries = knowledge.get("fault_code_entries") if isinstance(knowledge.get("fault_code_entries"), list) else []
    result = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        result.append({
            key: item.get(key)
            for key in ("code", "title", "meaning", "cause", "remedy", "references", "source_file", "page", "match_type")
            if item.get(key) not in (None, "", [])
        })
    return {"fault_codes": list(knowledge.get("fault_codes") or []), "entries": result}


def _source_artifact_ids(node_type: str, *, node: dict[str, Any], state: Any) -> list[str]:
    refs = []
    for item in (node.get("inputs") or {}).get("source_artifact_refs", []) or []:
        if isinstance(item, dict) and item.get("artifact_id"):
            refs.append(str(item["artifact_id"]))
    target = str((node.get("inputs") or {}).get("target_artifact_id") or "")
    if target:
        refs.append(target)
    prepared_source = str((node.get("inputs") or {}).get("source_artifact_id") or "")
    if prepared_source:
        refs.append(prepared_source)
    wanted = {
        "analysis": {"sql_artifact", "knowledge_artifact"},
        "comparison": {"sql_artifact"},
        "report": {"analysis_artifact", "comparison_artifact", "sql_artifact"},
        "workorder": {"analysis_artifact", "report_artifact"},
    }.get(node_type, set())
    for envelope in getattr(state, "artifact_envelopes", {}).values():
        if envelope.artifact_type in wanted and envelope.status == "complete":
            refs.append(envelope.artifact_id)
    return list(dict.fromkeys(item for item in refs if item))


def _lineage_is_complete(
    node_type: str,
    *,
    payload: dict[str, Any],
    devices: list[str],
    source_ids: list[str],
    evidence_refs: list[str],
) -> bool:
    if node_type == "sql":
        return bool(devices and _source_tables(payload) and evidence_refs)
    if node_type == "rag":
        return bool(_fault_code_explanation(payload).get("entries") and evidence_refs)
    if node_type == "analysis":
        return bool(devices and source_ids and evidence_refs and payload.get("structured_analysis"))
    if node_type == "comparison":
        comparison = payload.get("comparison_artifact") or {}
        return bool(len(devices) >= 2 and len(source_ids) >= 2 and comparison.get("comparison_dimensions"))
    if node_type == "report":
        report = payload.get("report_artifact") or {}
        return bool(source_ids and report.get("success") and (report.get("report_url") or report.get("report_filename")))
    if node_type == "workorder":
        return bool(len(devices) == 1 and source_ids and payload.get("workorder_draft"))
    return False


def _devices(node: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    values = [str(item) for item in (node.get("inputs") or {}).get("device_refs", []) if str(item)]
    assessment = payload.get("runtime_status_assessment") if isinstance(payload.get("runtime_status_assessment"), dict) else {}
    if assessment.get("device"):
        values.append(str(assessment["device"]))
    comparison = payload.get("comparison_artifact") if isinstance(payload.get("comparison_artifact"), dict) else {}
    values.extend(str(item) for item in comparison.get("devices", []) if str(item))
    return list(dict.fromkeys(values))


def _fault_codes(node: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    values = [str(item) for item in (node.get("inputs") or {}).get("fault_code_refs", []) if str(item)]
    values.extend(str(item) for item in _fault_code_explanation(payload).get("fault_codes", []) if str(item))
    return list(dict.fromkeys(values))


def _source_tables(payload: dict[str, Any]) -> list[str]:
    table = _nested_text(payload, "sql_artifact", "source_table")
    return [table] if table else []


def _data_basis(payload: dict[str, Any]) -> list[dict[str, Any]]:
    assessment = payload.get("runtime_status_assessment") if isinstance(payload.get("runtime_status_assessment"), dict) else {}
    basis = assessment.get("data_basis") if isinstance(assessment.get("data_basis"), dict) else {}
    if not basis:
        sql = payload.get("sql_artifact") if isinstance(payload.get("sql_artifact"), dict) else {}
        basis = sql.get("data_basis") if isinstance(sql.get("data_basis"), dict) else {}
    return [basis] if basis else []


def _time_windows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    basis = (_data_basis(payload) or [{}])[0]
    window = basis.get("resolved_window") if isinstance(basis.get("resolved_window"), dict) else {}
    return [window] if window else []


def _runtime_status(payload: dict[str, Any]) -> str:
    return _nested_text(payload, "runtime_status_assessment", "runtime_status") or "unknown"


def _latest_sample_time(payload: dict[str, Any]) -> str:
    basis = (_data_basis(payload) or [{}])[0]
    return str(_nested_text(payload, "sql_artifact", "latest_sample_time") or basis.get("latest_sample_time") or "")


def _sample_count(payload: dict[str, Any]) -> int:
    assessment = payload.get("runtime_status_assessment") if isinstance(payload.get("runtime_status_assessment"), dict) else {}
    sql = payload.get("sql_artifact") if isinstance(payload.get("sql_artifact"), dict) else {}
    try:
        return int(assessment.get("sample_count") or sql.get("sample_count") or 0)
    except (TypeError, ValueError):
        return 0


def _key_findings(payload: dict[str, Any]) -> list[str]:
    for key in ("runtime_status_assessment", "analysis_artifact"):
        item = payload.get(key) if isinstance(payload.get(key), dict) else {}
        values = item.get("key_findings") or item.get("findings") or []
        if values:
            return [str(value) for value in values if str(value)]
    return []


def _recommendations(payload: dict[str, Any]) -> list[str]:
    item = payload.get("analysis_artifact") if isinstance(payload.get("analysis_artifact"), dict) else {}
    return [str(value) for value in item.get("recommendations", []) if str(value)]


def _diagnosis_summary(payload: dict[str, Any]) -> str:
    item = payload.get("analysis_artifact") if isinstance(payload.get("analysis_artifact"), dict) else {}
    return str(item.get("conclusion") or "")


def _fault_code_explanation(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("fault_code_explanation")
    return dict(value) if isinstance(value, dict) else {}


def _latest_source_id(state: Any, artifact_type: str) -> str:
    values = [
        item.artifact_id
        for item in getattr(state, "artifact_envelopes", {}).values()
        if item.artifact_type == artifact_type and item.status == "complete"
    ]
    return values[-1] if values else ""


def _followups(artifact_type: str) -> list[str]:
    return {
        "sql_artifact": ["check_runtime_status", "diagnose_fault", "generate_report"],
        "knowledge_artifact": ["explain_fault_code"],
        "analysis_artifact": ["generate_report", "create_workorder_draft"],
        "comparison_artifact": ["generate_report"],
        "report_artifact": ["create_workorder_draft"],
        "workorder_artifact": ["confirm_workorder_draft"],
    }.get(artifact_type, [])


def _nested_text(payload: dict[str, Any], key: str, field: str) -> str:
    item = payload.get(key) if isinstance(payload.get(key), dict) else {}
    return str(item.get(field) or "")


def _nested_dict(payload: dict[str, Any], key: str, field: str) -> dict[str, Any]:
    item = payload.get(key) if isinstance(payload.get(key), dict) else {}
    value = item.get(field)
    return dict(value) if isinstance(value, dict) else {}


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dump(item) for item in value]
    return value
