"""Shared report-source and readiness resolution for plan and stream paths."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ...diagnosis.artifact_store import list_thread_artifacts
from ...diagnosis.contracts import DiagnosisArtifactEnvelope, DiagnosisArtifactType, SqlStepArtifact
from ...security.assets import asset_is_in_scope
from ...security.contracts import AuthContext

ReportSourceMode = Literal["reuse_artifact", "refresh_sql", "blocked_missing_context", "ambiguous"]

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


class ReportSourceDecision(BaseModel):
    """Decision contract shared by planning and streaming report execution."""

    requested: bool = False
    mode: ReportSourceMode | str = ""
    referenced_artifact_id: str | None = None
    referenced_artifact_type: str | None = None
    inherited_slots: dict[str, Any] = Field(default_factory=dict)
    readiness: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    candidate_artifact_ids: list[str] = Field(default_factory=list)

    @property
    def ready(self) -> bool:
        return bool(self.readiness.get("passed"))


def resolve_report_source(
    *,
    thread_id: str,
    message: str,
    auth_context: AuthContext,
    current_payload: dict[str, Any],
    resolved_context: Any,
    artifact_limit: int = 10,
) -> ReportSourceDecision:
    """Resolve the report source once so plan and stream use identical rules."""

    context = _context_dict(resolved_context)
    requested = _is_report_request(message, current_payload, context)
    if not requested:
        return ReportSourceDecision(requested=False)

    if str(context.get("relation_to_previous") or "") == "ambiguous":
        return ReportSourceDecision(
            requested=True,
            mode="ambiguous",
            readiness=_readiness(False, blockers=["ambiguous_report_source"]),
            blockers=["ambiguous_report_source"],
        )

    wants_fresh = _has_any(message, _FRESH_WORDS)
    inherited_slots = _inherited_slots(current_payload, context)
    requested_device = str(
        current_payload.get("equipment_hint")
        or inherited_slots.get("device")
        or context.get("active_asset")
        or ""
    ).strip()
    artifacts = list_thread_artifacts(thread_id, limit=artifact_limit)
    valid_candidates: list[tuple[DiagnosisArtifactEnvelope, dict[str, Any]]] = []
    rejected: list[str] = []
    for envelope in artifacts:
        result = _candidate_reportability(
            envelope,
            auth_context=auth_context,
            requested_device=requested_device,
            wants_fresh=wants_fresh,
        )
        if result["valid"]:
            valid_candidates.append((envelope, result))
        elif result.get("reason"):
            rejected.append(str(result["reason"]))

    if valid_candidates:
        envelope, result = valid_candidates[0]
        artifact_id = _artifact_id(envelope)
        inherited = {
            **inherited_slots,
            **_artifact_inherited_slots(envelope),
            "referenced_artifact_type": str(envelope.workflow_type),
        }
        return ReportSourceDecision(
            requested=True,
            mode="reuse_artifact",
            referenced_artifact_id=artifact_id,
            referenced_artifact_type=str(envelope.workflow_type),
            inherited_slots={key: value for key, value in inherited.items() if value not in (None, "", [], {})},
            readiness=_readiness(
                True,
                source="referenced_artifact",
                checks=result.get("checks", {}),
            ),
            blockers=[],
            candidate_artifact_ids=[_artifact_id(item[0]) for item in valid_candidates],
        )

    if requested_device:
        return ReportSourceDecision(
            requested=True,
            mode="refresh_sql",
            inherited_slots={key: value for key, value in inherited_slots.items() if value not in (None, "", [], {})},
            readiness=_readiness(False, blockers=["fresh_sql_required"]),
            blockers=["fresh_sql_required", *list(dict.fromkeys(rejected))[:3]],
        )

    return ReportSourceDecision(
        requested=True,
        mode="blocked_missing_context",
        inherited_slots={},
        readiness=_readiness(False, blockers=["missing_device_or_reportable_artifact"]),
        blockers=["missing_device_or_reportable_artifact", *list(dict.fromkeys(rejected))[:3]],
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
        "referenced_artifact_type": decision.referenced_artifact_type,
    }
    if hasattr(resolved_context, "report_source_mode"):
        resolved_context.report_source_mode = decision.mode
        resolved_context.report_readiness = decision.readiness
        resolved_context.report_blockers = list(decision.blockers)
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


def find_referenced_artifact(thread_id: str, artifact_id: str | None, *, limit: int = 20) -> DiagnosisArtifactEnvelope | None:
    """Find a referenced artifact by any stable id used in context projection."""

    target = str(artifact_id or "").strip()
    if not target:
        return None
    for envelope in list_thread_artifacts(thread_id, limit=limit):
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
    if requested_device and artifact_device and requested_device != artifact_device:
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
    return {
        "device": _artifact_device(payload),
        "asset_id": payload.get("asset_id") or request.get("equipment_hint") or _artifact_device(payload),
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


def _artifact_stale(payload: dict[str, Any]) -> bool:
    text = " ".join(str(item or "") for item in [payload.get("freshness"), payload.get("freshness_label"), payload])
    lowered = text.lower()
    return any(marker in text or marker in lowered for marker in ("已滞后", "滞后", "stale", "非实时", "不代表实时"))


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
