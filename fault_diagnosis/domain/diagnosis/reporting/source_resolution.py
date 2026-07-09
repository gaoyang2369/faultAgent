"""Shared report-source and readiness resolution for plan and stream paths."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType, SqlStepArtifact
from fault_diagnosis.domain.security.assets import asset_is_in_scope
from fault_diagnosis.domain.security.contracts import AuthContext

ReportSourceMode = Literal["reuse_artifact", "refresh_sql", "blocked_missing_context", "ambiguous"]
ArtifactLister = Callable[[str, int], list[DiagnosisArtifactEnvelope]]

_REPORT_WORDS = ("报告", "出报告", "生成报告", "导出报告", "整理成报告", "形成报告", "导出一下")
_FRESH_WORDS = ("最新", "当前", "现在", "重新查", "重新查询", "刷新", "今天")
_SUPPORTED_ARTIFACT_TYPES = {
    DiagnosisArtifactType.FAULT_DIAGNOSIS.value,
    DiagnosisArtifactType.STATUS_QUERY.value,
    DiagnosisArtifactType.ALARM_TRIAGE.value,
    DiagnosisArtifactType.ROOT_CAUSE_ANALYSIS.value,
    DiagnosisArtifactType.HEALTH_ASSESSMENT.value,
    DiagnosisArtifactType.STATUS_INSPECTION.value,
    DiagnosisArtifactType.REPORT_GENERATION.value,
}


@dataclass(slots=True)
class ReportSourceDecision:
    """Decision contract shared by planning and streaming report execution."""

    requested: bool = False
    mode: ReportSourceMode | str = ""
    referenced_artifact_id: str | None = None
    inherited_slots: dict[str, Any] = field(default_factory=dict)
    readiness: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    candidate_summary: list[dict[str, Any]] = field(default_factory=list)
    selected_artifact_id: str | None = None
    selected_artifact_type: str | None = None


def resolve_report_source(
    *,
    thread_id: str,
    message: str,
    auth_context: AuthContext,
    current_payload: dict[str, Any],
    resolved_context: Any,
    conversation_context: dict[str, Any] | None = None,
    artifact_lister: ArtifactLister | None = None,
    artifact_limit: int = 10,
) -> ReportSourceDecision:
    """Resolve the report source once so plan and stream use identical rules."""

    context = _context_dict(resolved_context)
    requested = _is_report_request(message, current_payload, context)
    if not requested:
        return ReportSourceDecision(requested=False)

    wants_fresh = _has_any(message, _FRESH_WORDS)
    inherited_slots = _inherited_slots(current_payload, context)
    requested_device = str(
        current_payload.get("equipment_hint")
        or inherited_slots.get("device")
        or context.get("active_asset")
        or ""
    ).strip()
    previous_artifact_ids = _previous_turn_artifact_ids(conversation_context)
    artifacts = artifact_lister(thread_id, artifact_limit) if artifact_lister is not None else []
    scanned: list[tuple[DiagnosisArtifactEnvelope, dict[str, Any], dict[str, Any]]] = []
    rejected: list[str] = []
    for envelope in artifacts:
        result = _candidate_reportability(
            envelope,
            auth_context=auth_context,
            requested_device=requested_device,
            wants_fresh=wants_fresh,
        )
        result["_previous_turn_candidate"] = _matches_previous_turn(envelope, previous_artifact_ids)
        summary = _candidate_summary(
            envelope,
            result,
            created_turn="previous",
        )
        scanned.append((envelope, result, summary))
        if not result["valid"] and result.get("reason"):
            rejected.append(str(result["reason"]))
    root_candidates = _dedupe_root_candidates(scanned, requested_device=requested_device)
    valid_candidates = [
        (item["envelope"], item["result"])
        for item in root_candidates
        if item["result"].get("valid")
    ]
    candidate_summary = [item["summary"] for item in root_candidates]

    if valid_candidates:
        ranked_candidates = sorted(
            [
                (
                    _candidate_priority(envelope, result, requested_device=requested_device),
                    envelope,
                    result,
                )
                for envelope, result in valid_candidates
            ],
            key=lambda item: item[0],
            reverse=True,
        )
        top_priority = ranked_candidates[0][0]
        top_candidates = [
            item for item in ranked_candidates
            if item[0] == top_priority
        ]
        if len(top_candidates) > 1:
            return ReportSourceDecision(
                requested=True,
                mode="ambiguous",
                readiness=_readiness(False, blockers=["ambiguous_report_source"]),
                blockers=["ambiguous_report_source"],
                candidate_summary=[
                    _mark_selected_summary(item, selected=False, ambiguous=True)
                    for item in candidate_summary
                ],
            )
        _, envelope, result = ranked_candidates[0]
        artifact_id = _artifact_id(envelope)
        inherited = {
            **inherited_slots,
            **_artifact_inherited_slots(envelope),
        }
        return ReportSourceDecision(
            requested=True,
            mode="reuse_artifact",
            referenced_artifact_id=artifact_id,
            inherited_slots={key: value for key, value in inherited.items() if value not in (None, "", [], {})},
            readiness=_readiness(
                True,
                source="referenced_artifact",
                checks=result.get("checks", {}),
            ),
            blockers=[],
            candidate_summary=[
                _mark_selected_summary(item, selected=item.get("artifact_id") == artifact_id)
                for item in candidate_summary
            ],
            selected_artifact_id=artifact_id,
            selected_artifact_type=str(envelope.workflow_type),
        )

    if requested_device:
        return ReportSourceDecision(
            requested=True,
            mode="refresh_sql",
            inherited_slots={key: value for key, value in inherited_slots.items() if value not in (None, "", [], {})},
            readiness=_readiness(False, blockers=["fresh_sql_required"]),
            blockers=["fresh_sql_required", *list(dict.fromkeys(rejected))[:3]],
            candidate_summary=candidate_summary,
        )

    return ReportSourceDecision(
        requested=True,
        mode="blocked_missing_context",
        inherited_slots={},
        readiness=_readiness(False, blockers=["missing_device_or_reportable_artifact"]),
        blockers=["missing_device_or_reportable_artifact", *list(dict.fromkeys(rejected))[:3]],
        candidate_summary=candidate_summary,
    )


def apply_report_source_decision(
    *,
    resolved_context: Any,
    current_payload: dict[str, Any],
    decision: ReportSourceDecision,
) -> None:
    """Attach report-source fields to the resolved context and payload."""

    if not decision.requested:
        return
    current_payload["needs_report"] = True
    if decision.mode in {"blocked_missing_context", "ambiguous"}:
        current_payload["needs_sql"] = False
    elif decision.mode == "refresh_sql":
        current_payload["needs_sql"] = True

    data = {
        "report_source_mode": decision.mode,
        "report_readiness": decision.readiness,
        "report_blockers": list(decision.blockers),
        "referenced_artifact_id": decision.referenced_artifact_id,
        "selected_artifact_id": decision.selected_artifact_id,
        "selected_artifact_type": decision.selected_artifact_type,
        "report_candidate_artifact_count": len(decision.candidate_summary),
        "report_candidate_summary": decision.candidate_summary,
    }
    if hasattr(resolved_context, "report_source_mode"):
        resolved_context.report_source_mode = decision.mode
        resolved_context.report_readiness = decision.readiness
        resolved_context.report_blockers = list(decision.blockers)
        resolved_context.report_candidate_summary = list(decision.candidate_summary)
        resolved_context.report_candidate_artifact_count = len(decision.candidate_summary)
        resolved_context.selected_artifact_id = decision.selected_artifact_id
        resolved_context.selected_artifact_type = decision.selected_artifact_type
        if decision.mode in {"blocked_missing_context", "ambiguous"}:
            resolved_context.missing_context = list(
                dict.fromkeys([*getattr(resolved_context, "missing_context", []), *decision.blockers])
            )
        if decision.referenced_artifact_id:
            resolved_context.referenced_artifact_id = decision.referenced_artifact_id
        if decision.inherited_slots:
            merged = dict(getattr(resolved_context, "inherited_slots", {}) or {})
            merged.update(decision.inherited_slots)
            resolved_context.inherited_slots = merged
        if decision.requested and getattr(resolved_context, "relation_to_previous", "new_case") == "new_case":
            resolved_context.relation_to_previous = "report_handoff"
    context_resolution = current_payload.get("context_resolution")
    if isinstance(context_resolution, dict):
        context_resolution.update(data)
        if decision.requested:
            context_resolution["relation_to_previous"] = "report_handoff"
        if decision.mode in {"blocked_missing_context", "ambiguous"}:
            context_resolution["missing_context"] = list(
                dict.fromkeys([*list(context_resolution.get("missing_context") or []), *decision.blockers])
            )
        if decision.inherited_slots:
            merged = dict(context_resolution.get("inherited_slots") or {})
            merged.update(decision.inherited_slots)
            context_resolution["inherited_slots"] = merged


def find_referenced_artifact(
    thread_id: str,
    artifact_id: str | None,
    *,
    limit: int = 20,
    artifact_lister: ArtifactLister | None = None,
) -> DiagnosisArtifactEnvelope | None:
    """Find a referenced artifact by any stable id used in context projection."""

    target = str(artifact_id or "").strip()
    if not target:
        return None
    if artifact_lister is None:
        return None
    for envelope in artifact_lister(thread_id, limit):
        if target in _artifact_ids(envelope):
            return envelope
    return None


def has_reportable_material(payload: dict[str, Any]) -> bool:
    """Return whether payload contains enough material to reconstruct a report."""

    if _non_empty(payload.get("reportable_payload")):
        return True
    if _non_empty(payload.get("operation_report_payload")):
        return True
    if _non_empty(payload.get("chart_payload")):
        return True
    if _normalized_rows(payload):
        return True
    evidence_bundle = payload.get("evidence_bundle") if isinstance(payload.get("evidence_bundle"), dict) else {}
    if _evidence_bundle_mappable(evidence_bundle):
        return True
    return False


def build_report_readiness(
    *,
    decision: Any,
    sql_artifact: SqlStepArtifact | None = None,
    referenced_artifact: DiagnosisArtifactEnvelope | None = None,
) -> dict[str, Any]:
    """Evaluate the hard pre-report readiness gate."""

    mode = str(getattr(decision, "report_source_mode", "") or "")
    blockers = list(getattr(decision, "report_blockers", []) or [])
    if mode in {"blocked_missing_context", "ambiguous"}:
        return _readiness(False, blockers=blockers or [mode])
    if sql_artifact is not None and (sql_artifact.row_count or 0) > 0:
        return _readiness(True, source="current_sql", checks={"sql_row_count": sql_artifact.row_count})
    payload = referenced_artifact.payload if referenced_artifact is not None else {}
    if isinstance(payload, dict) and has_reportable_material(payload):
        checks = {
            "has_reportable_payload": _non_empty(payload.get("reportable_payload")),
            "has_operation_report_payload": _non_empty(payload.get("operation_report_payload")),
            "has_chart_payload": _non_empty(payload.get("chart_payload")),
            "has_normalized_rows": bool(_normalized_rows(payload)),
            "has_mappable_evidence_bundle": _evidence_bundle_mappable(
                payload.get("evidence_bundle") if isinstance(payload.get("evidence_bundle"), dict) else {}
            ),
        }
        return _readiness(True, source="referenced_artifact", checks=checks)
    return _readiness(False, blockers=list(dict.fromkeys([*blockers, "report_readiness_failed"])))


def _candidate_reportability(
    envelope: DiagnosisArtifactEnvelope,
    *,
    auth_context: AuthContext,
    requested_device: str,
    wants_fresh: bool,
) -> dict[str, Any]:
    payload = envelope.payload or {}
    artifact_type = str(envelope.workflow_type)
    task_family = str((payload.get("decision") or {}).get("task_family") or payload.get("task_family") or "")
    if artifact_type not in _SUPPORTED_ARTIFACT_TYPES and task_family not in {"runtime_status", "diagnosis", "reporting"}:
        return {"valid": False, "reason": "unsupported_artifact_type"}
    if payload.get("reportable") is not True:
        return {"valid": False, "reason": "artifact_not_marked_reportable"}
    if not has_reportable_material(payload):
        return {"valid": False, "reason": "artifact_missing_reportable_material"}
    artifact_device = _artifact_device(payload)
    if requested_device and artifact_device and not _device_matches(requested_device, payload):
        return {"valid": False, "reason": "artifact_device_mismatch"}
    if artifact_device and not _asset_allowed(artifact_device, auth_context):
        return {"valid": False, "reason": "artifact_asset_out_of_scope"}
    if artifact_device and not auth_context.table_scope:
        return {"valid": False, "reason": "missing_table_scope"}
    if wants_fresh and _artifact_stale(payload):
        return {"valid": False, "reason": "fresh_runtime_data_requested"}
    return {
        "valid": True,
        "checks": {
            "artifact_type": artifact_type,
            "task_family": task_family,
            "artifact_device": artifact_device,
            "stale": _artifact_stale(payload),
            "has_reportable_material": True,
        },
    }


def _candidate_priority(
    envelope: DiagnosisArtifactEnvelope,
    result: dict[str, Any],
    *,
    requested_device: str,
) -> tuple[int, int, int, int, str]:
    payload = envelope.payload or {}
    checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
    artifact_device = str(checks.get("artifact_device") or _artifact_device(payload) or "")
    return (
        1 if bool(result.get("_previous_turn_candidate")) else 0,
        1 if _artifact_completed(payload) else 0,
        _artifact_type_priority(envelope),
        1 if requested_device and (artifact_device == requested_device or _device_matches(requested_device, payload)) else 0,
        str(envelope.created_at or ""),
    )


def _dedupe_root_candidates(
    scanned: list[tuple[DiagnosisArtifactEnvelope, dict[str, Any], dict[str, Any]]],
    *,
    requested_device: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[DiagnosisArtifactEnvelope, dict[str, Any], dict[str, Any]]]] = {}
    for item in scanned:
        key = _root_candidate_key(item[0])
        groups.setdefault(key, []).append(item)

    deduped: list[dict[str, Any]] = []
    for items in groups.values():
        selected = sorted(
            items,
            key=lambda item: (
                1 if item[1].get("valid") else 0,
                _candidate_priority(item[0], item[1], requested_device=requested_device),
            ),
            reverse=True,
        )[0]
        envelope, result, summary = selected
        if len(items) > 1:
            merged_reasons = list(dict.fromkeys(str(item[2].get("reason") or "") for item in items if item[2].get("reason")))
            summary = {
                **summary,
                "merged_candidate_count": len(items),
                "merged_reasons": merged_reasons,
                "reason": "candidate_valid" if result.get("valid") else (merged_reasons[0] if merged_reasons else "candidate_rejected"),
            }
        deduped.append({"envelope": envelope, "result": result, "summary": summary})
    deduped.sort(
        key=lambda item: _candidate_priority(item["envelope"], item["result"], requested_device=requested_device),
        reverse=True,
    )
    return deduped


def _root_candidate_key(envelope: DiagnosisArtifactEnvelope) -> str:
    payload = envelope.payload or {}
    workorder = payload.get("workorder_decision") if isinstance(payload.get("workorder_decision"), dict) else {}
    evidence = payload.get("evidence_bundle") if isinstance(payload.get("evidence_bundle"), dict) else {}
    root = _first_non_empty(
        [
            payload.get("root_artifact_id"),
            payload.get("source_turn_id"),
            payload.get("source_diagnosis_artifact_id"),
            payload.get("source_artifact_id"),
            workorder.get("source_diagnosis_artifact_id"),
            evidence.get("artifacts", {}).get("source_diagnosis_artifact_id")
            if isinstance(evidence.get("artifacts"), dict)
            else None,
            envelope.created_at,
        ]
    )
    return f"{envelope.thread_id}:{root}"


def _matches_previous_turn(envelope: DiagnosisArtifactEnvelope, previous_ids: set[str]) -> bool:
    if not previous_ids:
        return False
    payload = envelope.payload or {}
    ids = set(_artifact_ids(envelope))
    ids.add(str(_root_candidate_key(envelope).split(":", 1)[-1]))
    for key in ("root_artifact_id", "source_turn_id", "source_diagnosis_artifact_id", "source_artifact_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            ids.add(value)
    return bool(ids.intersection(previous_ids))


def _artifact_type_priority(envelope: DiagnosisArtifactEnvelope) -> int:
    artifact_type = str(envelope.workflow_type)
    payload = envelope.payload or {}
    task_family = str((payload.get("decision") or {}).get("task_family") or payload.get("task_family") or "")
    if artifact_type == DiagnosisArtifactType.STATUS_QUERY.value or task_family == "runtime_status":
        return 4
    if artifact_type in {
        DiagnosisArtifactType.FAULT_DIAGNOSIS.value,
        DiagnosisArtifactType.ALARM_TRIAGE.value,
        DiagnosisArtifactType.ROOT_CAUSE_ANALYSIS.value,
        DiagnosisArtifactType.HEALTH_ASSESSMENT.value,
    } or task_family == "diagnosis":
        return 3
    if artifact_type == DiagnosisArtifactType.STATUS_INSPECTION.value:
        return 2
    if artifact_type == DiagnosisArtifactType.REPORT_GENERATION.value or task_family == "reporting":
        return 1
    return 0


def _candidate_summary(
    envelope: DiagnosisArtifactEnvelope,
    result: dict[str, Any],
    *,
    created_turn: str,
) -> dict[str, Any]:
    payload = envelope.payload or {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    objects = decision.get("objects") if isinstance(decision.get("objects"), dict) else {}
    fault_codes = payload.get("fault_codes") or request.get("fault_code_hint") or objects.get("alarm_codes") or []
    if fault_codes and not isinstance(fault_codes, list):
        fault_codes = [fault_codes]
    return {
        "artifact_id": _artifact_id(envelope),
        "artifact_type": str(envelope.workflow_type),
        "device": _artifact_device(payload),
        "fault_codes": [str(item) for item in (fault_codes or []) if str(item or "").strip()],
        "reportable": payload.get("reportable") is True,
        "created_turn": created_turn,
        "created_before_current_turn": created_turn == "previous",
        "created_at": envelope.created_at,
        "completed": _artifact_completed(payload),
        "immediately_previous_turn": bool(result.get("_previous_turn_candidate")),
        "reason": "candidate_valid" if result.get("valid") else str(result.get("reason") or "candidate_rejected"),
    }


def _mark_selected_summary(item: dict[str, Any], *, selected: bool, ambiguous: bool = False) -> dict[str, Any]:
    marked = dict(item)
    if selected:
        marked["reason"] = "selected"
    elif ambiguous and item.get("reason") == "candidate_valid":
        marked["reason"] = "ambiguous_same_priority"
    return marked


def _is_report_request(message: str, payload: dict[str, Any], context: dict[str, Any]) -> bool:
    compact = str(message or "").replace(" ", "")
    return bool(
        payload.get("needs_report")
        or context.get("relation_to_previous") == "report_handoff"
        or _has_any(compact, _REPORT_WORDS)
    )


def _inherited_slots(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    inherited = dict(context.get("inherited_slots") or {}) if isinstance(context.get("inherited_slots"), dict) else {}
    device = payload.get("equipment_hint") or context.get("active_asset") or inherited.get("device")
    if device:
        inherited["device"] = device
    fault_codes = payload.get("fault_code_hint") or context.get("active_fault_codes") or inherited.get("fault_codes")
    if fault_codes:
        inherited["fault_codes"] = fault_codes if isinstance(fault_codes, list) else [fault_codes]
    return inherited


def _artifact_inherited_slots(envelope: DiagnosisArtifactEnvelope) -> dict[str, Any]:
    payload = envelope.payload or {}
    sql = payload.get("sql_artifact") if isinstance(payload.get("sql_artifact"), dict) else {}
    analysis = payload.get("analysis_artifact") if isinstance(payload.get("analysis_artifact"), dict) else {}
    evidence = payload.get("evidence_bundle") if isinstance(payload.get("evidence_bundle"), dict) else {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    artifact_device = _artifact_device(payload)
    return {
        "device": artifact_device,
        "asset_id": payload.get("asset_id") or request.get("equipment_hint") or artifact_device,
        "source_table": payload.get("source_table") or sql.get("source_table"),
        "sql_artifact_id": payload.get("sql_artifact_id") or sql.get("artifact_id") or envelope.created_at,
        "analysis_artifact_id": payload.get("analysis_artifact_id") or analysis.get("artifact_id") or envelope.created_at,
        "evidence_bundle_id": payload.get("evidence_bundle_id") or evidence.get("bundle_id"),
        "fault_codes": payload.get("fault_codes") or request.get("fault_code_hint"),
        "data_window": payload.get("data_window") or payload.get("active_time_window") or {},
        "freshness": payload.get("freshness") or payload.get("freshness_label"),
    }


def _artifact_device(payload: dict[str, Any]) -> str:
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    objects = decision.get("objects") if isinstance(decision.get("objects"), dict) else {}
    context = decision.get("context_resolution") if isinstance(decision.get("context_resolution"), dict) else {}
    return str(
        payload.get("device")
        or payload.get("asset_id")
        or request.get("equipment_hint")
        or context.get("active_asset")
        or _first(objects.get("device_ids"))
        or ""
    ).strip()


def _device_aliases(payload: dict[str, Any]) -> set[str]:
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    objects = decision.get("objects") if isinstance(decision.get("objects"), dict) else {}
    aliases: list[Any] = [
        payload.get("device"),
        payload.get("asset_id"),
        payload.get("device_name"),
        payload.get("inverter_name"),
        request.get("equipment_hint"),
        _first(objects.get("device_ids")),
    ]
    for key in ("device_aliases", "asset_aliases"):
        value = payload.get(key)
        if isinstance(value, list):
            aliases.extend(value)
        elif value:
            aliases.append(value)
    return {str(item).strip() for item in aliases if str(item or "").strip()}


def _device_matches(requested_device: str, payload: dict[str, Any]) -> bool:
    requested = str(requested_device or "").strip()
    if not requested:
        return True
    return requested in _device_aliases(payload)


def _artifact_stale(payload: dict[str, Any]) -> bool:
    text = " ".join(str(item or "") for item in [payload.get("freshness"), payload.get("freshness_label"), payload])
    lowered = text.lower()
    return any(marker in text or marker in lowered for marker in ("已滞后", "滞后", "stale", "非实时", "不代表实时"))


def _artifact_completed(payload: dict[str, Any]) -> bool:
    trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
    status = str(trace.get("status") or payload.get("status") or "").strip().lower()
    return status in {"", "completed", "success", "succeeded", "finished"}


def _artifact_id(envelope: DiagnosisArtifactEnvelope) -> str:
    return _artifact_ids(envelope)[0]


def _artifact_ids(envelope: DiagnosisArtifactEnvelope) -> list[str]:
    payload = envelope.payload or {}
    evidence = payload.get("evidence_bundle") if isinstance(payload.get("evidence_bundle"), dict) else {}
    report = payload.get("report_artifact") if isinstance(payload.get("report_artifact"), dict) else {}
    return list(
        dict.fromkeys(
            str(item).strip()
            for item in [
                envelope.created_at,
                evidence.get("bundle_id"),
                payload.get("evidence_bundle_id"),
                payload.get("sql_artifact_id"),
                payload.get("analysis_artifact_id"),
                report.get("report_url"),
                report.get("report_filename"),
                envelope.report_filename,
            ]
            if str(item or "").strip()
        )
    )


def _previous_turn_artifact_ids(conversation_context: dict[str, Any] | None) -> set[str]:
    if not isinstance(conversation_context, dict):
        return set()
    previous = conversation_context.get("immediately_previous_assistant_turn")
    if not isinstance(previous, dict):
        previous = {}
    refs = previous.get("produced_artifacts") if isinstance(previous.get("produced_artifacts"), list) else []
    ids: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        for key in ("artifact_id", "source_artifact_id", "root_artifact_id", "source_diagnosis_artifact_id"):
            value = str(ref.get(key) or "").strip()
            if value:
                ids.add(value)
    if ids:
        return ids
    refs = conversation_context.get("artifact_refs") if isinstance(conversation_context.get("artifact_refs"), list) else []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if str(ref.get("ref_role") or "").strip() not in {"produced", "produced_by", "context_source"}:
            continue
        for key in ("artifact_id", "source_artifact_id"):
            value = str(ref.get(key) or "").strip()
            if value:
                ids.add(value)
    return ids


def _first_non_empty(values: list[Any]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _readiness(passed: bool, *, source: str = "", checks: dict[str, Any] | None = None, blockers: list[str] | None = None) -> dict[str, Any]:
    return {
        "passed": bool(passed),
        "source": source,
        "checks": checks or {},
        "blockers": list(dict.fromkeys(blockers or [])),
    }


def _normalized_rows(payload: dict[str, Any]) -> list[Any]:
    rows = payload.get("normalized_rows")
    if isinstance(rows, list) and rows:
        return rows
    materials = payload.get("report_materials") if isinstance(payload.get("report_materials"), dict) else {}
    rows = materials.get("normalized_rows")
    return rows if isinstance(rows, list) and rows else []


def _evidence_bundle_mappable(bundle: dict[str, Any]) -> bool:
    items = bundle.get("evidence_items") if isinstance(bundle, dict) else None
    claims = bundle.get("claims") if isinstance(bundle, dict) else None
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            evidence_type = str(item.get("evidence_type") or "")
            source_type = str(item.get("source_type") or "")
            quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
            completeness = str(quality.get("completeness") or "")
            content = item.get("content") if isinstance(item.get("content"), dict) else {}
            if evidence_type in {"device_status", "metric_snapshot", "alarm_event", "timeseries_feature"} and completeness != "missing":
                return True
            if source_type == "sql" and content.get("sample_count"):
                return True
    if isinstance(claims, list):
        return any(
            isinstance(item, dict)
            and item.get("supporting_evidence_ids")
            and not item.get("missing_evidence")
            for item in claims
        )
    return False


def _non_empty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _asset_allowed(asset: str, auth: AuthContext) -> bool:
    return auth.is_admin() or asset_is_in_scope(asset, auth.asset_scope)


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords if keyword)


def _first(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return value


def _context_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return {}
