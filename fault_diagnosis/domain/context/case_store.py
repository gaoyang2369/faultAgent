"""Artifact-backed case projection for context resolution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope
from .contracts import (
    CASE_STATE_SNAPSHOT_VERSION,
    CaseState,
    ConversationDiagnosisState,
    PendingAction,
)

_STALE_MARKERS = ("已滞后", "滞后", "stale", "STALE", "非实时", "不代表实时")


ArtifactLister = Callable[[str, int], list[DiagnosisArtifactEnvelope]]
_DEFAULT_ARTIFACT_LISTER: ArtifactLister | None = None


class ArtifactBackedCaseStore:
    """Project thread-local CaseState objects from diagnosis artifacts."""

    def __init__(self, *, limit: int = 5, artifact_lister: ArtifactLister | None = None):
        self.limit = max(1, limit)
        self.artifact_lister = artifact_lister or _DEFAULT_ARTIFACT_LISTER or _empty_artifact_lister

    def load(self, thread_id: str) -> ConversationDiagnosisState:
        cases: list[CaseState] = []
        try:
            artifacts = self.artifact_lister(thread_id, self.limit)
        except Exception:
            artifacts = []
        for envelope in artifacts:
            case = case_state_from_artifact(envelope)
            if case is not None:
                cases.append(case)
        return ConversationDiagnosisState(
            thread_id=thread_id,
            active_case_id=cases[0].case_id if cases else None,
            cases=cases,
        )


def _empty_artifact_lister(thread_id: str, limit: int) -> list[DiagnosisArtifactEnvelope]:  # noqa: ARG001
    return []


def set_default_artifact_lister(artifact_lister: ArtifactLister | None) -> None:
    global _DEFAULT_ARTIFACT_LISTER
    _DEFAULT_ARTIFACT_LISTER = artifact_lister


def case_state_from_artifact(envelope: DiagnosisArtifactEnvelope) -> CaseState | None:
    """Project decision state only from committed, exact-read manifests."""

    payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    return _case_from_manifest_payload(envelope, payload)


def build_case_state_snapshot(
    envelope: DiagnosisArtifactEnvelope,
) -> dict[str, Any]:
    """Build an optional cache payload from the saved artifact envelope."""

    payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    case = _case_from_manifest_payload(envelope, payload)
    if case is None:
        return {"schema_version": CASE_STATE_SNAPSHOT_VERSION}
    payload = case.model_dump(exclude_none=True)
    payload["schema_version"] = CASE_STATE_SNAPSHOT_VERSION
    return payload


def _case_from_snapshot(envelope: DiagnosisArtifactEnvelope) -> CaseState | None:
    payload = envelope.payload or {}
    raw = payload.get("case_state_snapshot")
    if not isinstance(raw, dict):
        return None
    if raw.get("schema_version") != CASE_STATE_SNAPSHOT_VERSION:
        return None
    try:
        data = dict(raw)
        data.pop("schema_version", None)
        case = CaseState.model_validate(data)
    except Exception:
        return None
    if case.thread_id != envelope.thread_id:
        return None
    return case


def _snapshot_rejection_reason(envelope: DiagnosisArtifactEnvelope) -> str:
    payload = envelope.payload or {}
    raw = payload.get("case_state_snapshot")
    if raw is None:
        return "case_state_snapshot 缺失，已从 artifact payload 回退投影。"
    if not isinstance(raw, dict):
        return "case_state_snapshot 不是对象，已从 artifact payload 回退投影。"
    if raw.get("schema_version") != CASE_STATE_SNAPSHOT_VERSION:
        return "case_state_snapshot schema_version 缺失或不匹配，已从 artifact payload 回退投影。"
    try:
        data = dict(raw)
        data.pop("schema_version", None)
        case = CaseState.model_validate(data)
    except Exception:
        return "case_state_snapshot 字段解析失败，已从 artifact payload 回退投影。"
    if case.thread_id != envelope.thread_id:
        return "case_state_snapshot thread_id 不匹配，已从 artifact payload 回退投影。"
    return ""


def _legacy_case_from_payload(envelope: DiagnosisArtifactEnvelope) -> CaseState | None:
    """Legacy serializer only; it is never consulted for planning decisions."""
    payload = envelope.payload or {}
    manifest_case = _case_from_manifest_payload(envelope, payload)
    if manifest_case is not None:
        return manifest_case
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    objects = decision.get("objects") if isinstance(decision.get("objects"), dict) else {}
    context_resolution = (
        decision.get("context_resolution")
        if isinstance(decision.get("context_resolution"), dict)
        else {}
    )
    evidence_bundle = (
        payload.get("evidence_bundle")
        if isinstance(payload.get("evidence_bundle"), dict)
        else {}
    )
    report_artifact = (
        payload.get("report_artifact")
        if isinstance(payload.get("report_artifact"), dict)
        else {}
    )
    workorder = (
        payload.get("workorder_decision")
        if isinstance(payload.get("workorder_decision"), dict)
        else {}
    )
    report_context = _flatten_context(payload)
    active_asset = _first_non_empty(
        [
            context_resolution.get("active_asset"),
            payload.get("asset_id"),
            payload.get("device"),
            payload.get("device_name"),
            _first_list_item(objects.get("device_ids")),
            request.get("equipment_hint"),
            report_context.get("asset"),
            report_context.get("diagnosis_object"),
        ]
    )
    active_fault_codes = _dedupe(
        [
            *(_as_text_list(context_resolution.get("active_fault_codes"))),
            *(_as_text_list(payload.get("fault_codes"))),
            *(_as_text_list(objects.get("alarm_codes"))),
            request.get("fault_code_hint"),
            report_context.get("event_code"),
            report_context.get("fault_code"),
        ]
    )
    active_time_window: dict[str, Any] = {}
    if isinstance(decision.get("time_window"), dict):
        active_time_window.update(decision["time_window"])
    if isinstance(payload.get("data_window"), dict):
        active_time_window.update(payload["data_window"])
    if request.get("time_range_hint") and "default_strategy" not in active_time_window:
        active_time_window["default_strategy"] = request.get("time_range_hint")

    latest_evidence_bundle_id = _first_non_empty(
        [
            payload.get("evidence_bundle_id"),
            evidence_bundle.get("bundle_id"),
            context_resolution.get("last_evidence_bundle_id"),
        ]
    )
    sql_artifact = payload.get("sql_artifact") if isinstance(payload.get("sql_artifact"), dict) else {}
    analysis_artifact = payload.get("analysis_artifact") if isinstance(payload.get("analysis_artifact"), dict) else {}
    reportable = payload.get("reportable") is True and _has_reportable_material(payload)
    latest_report_id = _first_non_empty(
        [
            report_artifact.get("report_url"),
            report_artifact.get("report_filename"),
            envelope.report_filename,
            context_resolution.get("last_report_url"),
        ]
    )
    latest_artifact_id = _first_non_empty(
        [
            latest_evidence_bundle_id,
            decision.get("referenced_artifact_id"),
            (payload.get("trace") or {}).get("trace_id") if isinstance(payload.get("trace"), dict) else None,
            envelope.created_at,
        ]
    )
    case_id = _first_non_empty(
        [
            decision.get("active_case_id"),
            latest_evidence_bundle_id,
            latest_artifact_id,
            envelope.created_at,
        ]
    )
    if not any([active_asset, active_fault_codes, latest_evidence_bundle_id, latest_report_id]):
        return None

    current_event = _first_non_empty(
        [
            report_context.get("current_event"),
            report_context.get("event_code"),
            report_context.get("fault_code"),
            _first_list_item(active_fault_codes),
        ]
    )
    evidence_summary = _dedupe(
        [
            *_as_text_list(report_context.get("evidence_summary")),
            *_as_text_list(report_context.get("findings")),
            *_as_text_list(report_context.get("key_evidence")),
            report_context.get("one_sentence_conclusion"),
            report_context.get("conclusion"),
        ]
    )[:8]
    diagnosis_summary = _first_non_empty(
        [
            report_context.get("diagnosis_summary"),
            report_context.get("initial_assessment"),
            report_context.get("one_sentence_conclusion"),
            report_context.get("conclusion"),
            envelope.request_summary,
        ]
    )
    freshness_text = " ".join(
        str(item or "")
        for item in [
            report_context.get("freshness_label"),
            report_context.get("data_freshness_label"),
            report_context.get("currentness"),
            report_context.get("data_currentness_label"),
            envelope.final_answer,
            payload,
        ]
    )
    evidence_freshness = "stale" if _contains_stale_marker(freshness_text) else "unknown"
    pending_actions = _pending_actions_from_payload(
        workorder=workorder,
        latest_artifact_id=latest_artifact_id,
        evidence_freshness=evidence_freshness,
    )
    return CaseState(
        case_id=str(case_id),
        thread_id=envelope.thread_id,
        active_asset=active_asset,
        active_fault_codes=active_fault_codes,
        active_time_window=active_time_window,
        latest_artifact_id=latest_artifact_id,
        latest_artifact_type=str(payload.get("workflow_type") or envelope.workflow_type or ""),
        latest_report_id=latest_report_id,
        latest_analysis_artifact_id=_first_non_empty([payload.get("analysis_artifact_id"), analysis_artifact.get("artifact_id"), latest_artifact_id]),
        latest_sql_artifact_id=_first_non_empty([payload.get("sql_artifact_id"), sql_artifact.get("artifact_id"), latest_artifact_id]),
        latest_evidence_bundle_id=latest_evidence_bundle_id,
        last_report_url=latest_report_id,
        status_level=_first_non_empty([report_context.get("status_level"), report_context.get("asset_risk_label")]),
        severity=_first_non_empty([report_context.get("severity"), report_context.get("severity_label")]),
        priority=_first_non_empty([report_context.get("priority"), report_context.get("action_priority")]),
        freshness_label=_first_non_empty(
            [
                report_context.get("freshness_label"),
                report_context.get("data_freshness_label"),
                report_context.get("currentness_label"),
            ]
        ),
        currentness=_first_non_empty(
            [
                report_context.get("currentness"),
                report_context.get("data_currentness_level"),
                report_context.get("data_currentness_label"),
            ]
        ),
        latest_sample_time=_first_non_empty(
            [
                payload.get("latest_sample_time"),
                report_context.get("latest_sample_time"),
                report_context.get("last_sample_time"),
                report_context.get("sample_time"),
            ]
        ),
        sample_count=_as_int(payload.get("row_count") or report_context.get("sample_count")),
        current_event=current_event,
        key_phenomenon=_first_non_empty(
            [report_context.get("key_phenomenon"), report_context.get("top_finding"), report_context.get("abnormal_summary")]
        ),
        diagnosis_summary=diagnosis_summary,
        initial_assessment=diagnosis_summary,
        next_action=_first_non_empty(
            [report_context.get("next_action"), report_context.get("action_priority_label"), report_context.get("recommended_action")]
        ),
        evidence_summary=evidence_summary,
        pending_actions=pending_actions,
        available_followups=_available_followups(active_asset, active_fault_codes, latest_report_id),
        available_actions=[],
        unresolved_questions=_dedupe(
            [
                *(_as_text_list(context_resolution.get("unresolved_questions"))),
                *(_as_text_list(decision.get("missing_slots"))),
            ]
        ),
        evidence_freshness=evidence_freshness,
        reportable=reportable,
        report_blockers=_as_text_list(payload.get("report_blockers")),
        source_table=_first_non_empty([payload.get("source_table"), sql_artifact.get("source_table")]),
        sql_artifact_id=_first_non_empty([payload.get("sql_artifact_id"), sql_artifact.get("artifact_id"), latest_artifact_id]),
        analysis_artifact_id=_first_non_empty([payload.get("analysis_artifact_id"), analysis_artifact.get("artifact_id"), latest_artifact_id]),
        evidence_bundle_id=_first_non_empty([payload.get("evidence_bundle_id"), latest_evidence_bundle_id]),
        data_window=(
            payload.get("data_window")
            if isinstance(payload.get("data_window"), dict)
            else active_time_window
        ),
    )


def _case_from_manifest_payload(envelope: DiagnosisArtifactEnvelope, payload: dict[str, Any]) -> CaseState | None:
    manifests = payload.get("artifact_manifests")
    if not isinstance(manifests, list):
        return None
    typed = [
        item for item in manifests
        if isinstance(item, dict)
        and item.get("status", "completed") == "completed"
        and item.get("artifact_status") == "complete"
        and item.get("persistence_status") == "committed"
        and item.get("readback_verified") is True
        and (item.get("lineage") or {}).get("lineage_status") == "complete"
    ]
    if not typed:
        return None
    focus = _select_focus_manifest(typed)
    if focus is None:
        return None
    active_asset = _first_list_item(focus.get("device_refs"))
    active_fault_codes = _as_text_list(focus.get("fault_code_refs"))
    latest_evidence_bundle_id = _first_non_empty(
        [
            focus.get("evidence_bundle_id"),
            focus.get("linked_evidence_bundle_id"),
            (payload.get("evidence_bundle") or {}).get("bundle_id") if isinstance(payload.get("evidence_bundle"), dict) else None,
        ]
    )
    latest_report_id = _first_non_empty([focus.get("report_url"), focus.get("report_filename"), envelope.report_filename])
    latest_artifact_id = _first_non_empty([focus.get("artifact_id"), latest_evidence_bundle_id, envelope.created_at])
    if not any([active_asset, active_fault_codes, latest_artifact_id, latest_report_id]):
        return None
    pending_actions = _pending_actions_from_payload(
        workorder=payload.get("workorder_decision") if isinstance(payload.get("workorder_decision"), dict) else {},
        latest_artifact_id=latest_artifact_id,
        evidence_freshness=str(focus.get("freshness") or "unknown"),
    )
    available_actions = _as_text_list(focus.get("available_actions"))
    if any(action in available_actions for action in ("decide_workorder", "create_workorder_draft")) and not pending_actions:
        pending_actions = [
            PendingAction(
                action_type="workorder_draft",
                status="pending",
                artifact_id=latest_artifact_id,
                reason=str(focus.get("diagnosis_summary") or ""),
                source_diagnosis_artifact_id=latest_artifact_id,
                source_report_artifact_id=latest_report_id,
                required_role="engineer",
                stale_refresh_required=False,
            )
        ]
    return CaseState(
        case_id=str(latest_artifact_id),
        thread_id=envelope.thread_id,
        active_asset=active_asset,
        active_fault_codes=active_fault_codes,
        active_time_window=focus.get("time_window") if isinstance(focus.get("time_window"), dict) else {},
        latest_artifact_id=latest_artifact_id,
        latest_artifact_type=str(focus.get("artifact_type") or ""),
        latest_report_id=latest_report_id,
        latest_analysis_artifact_id=_latest_manifest_id(typed, {"analysis_artifact"}),
        latest_sql_artifact_id=_latest_manifest_id(typed, {"sql_artifact"}),
        latest_evidence_bundle_id=latest_evidence_bundle_id,
        last_report_url=latest_report_id,
        status_level=_first_non_empty([focus.get("status_level"), focus.get("risk_level")]),
        severity=_first_non_empty([focus.get("severity"), focus.get("risk_level")]),
        freshness_label=str(focus.get("freshness") or ""),
        currentness=str(focus.get("currentness") or ""),
        latest_sample_time=str(focus.get("latest_sample_time") or ""),
        diagnosis_summary=str(focus.get("diagnosis_summary") or envelope.request_summary or ""),
        initial_assessment=str(focus.get("diagnosis_summary") or envelope.request_summary or ""),
        next_action=_first_list_item(focus.get("recommendations")),
        evidence_summary=_as_text_list(focus.get("findings"))[:8],
        pending_actions=pending_actions,
        available_followups=_as_text_list(focus.get("available_followups")),
        available_actions=available_actions,
        evidence_freshness=str(focus.get("freshness") or "unknown"),
        reportable=bool(focus.get("reportable")),
        source_table=str(focus.get("source_table") or ""),
        sql_artifact_id=_latest_manifest_id(typed, {"sql_artifact"}),
        analysis_artifact_id=_latest_manifest_id(typed, {"analysis_artifact"}),
        evidence_bundle_id=latest_evidence_bundle_id,
        data_window=focus.get("data_window") if isinstance(focus.get("data_window"), dict) else {},
        artifact_manifests=[dict(item) for item in typed],
    )


def _select_focus_manifest(manifests: list[dict[str, Any]]) -> dict[str, Any] | None:
    priority = {
        "workorder_artifact": 0,
        "report_artifact": 1,
        "analysis_artifact": 2,
        "knowledge_artifact": 3,
        "sql_artifact": 4,
    }
    usable = [
        item
        for item in manifests
        if item.get("followupable") or item.get("actionable") or item.get("reportable")
    ]
    if not usable:
        return manifests[0] if manifests else None
    return sorted(usable, key=lambda item: priority.get(str(item.get("artifact_type") or ""), 99))[0]


def _latest_manifest_id(manifests: list[dict[str, Any]], artifact_types: set[str]) -> str | None:
    for item in reversed(manifests):
        if str(item.get("artifact_type") or "") in artifact_types and str(item.get("artifact_id") or "").strip():
            return str(item.get("artifact_id"))
    return None


def _merge_snapshot_with_fallback(snapshot: CaseState, fallback: CaseState) -> CaseState:
    data = fallback.model_dump()
    snapshot_data = snapshot.model_dump()
    for key, value in snapshot_data.items():
        if value not in (None, "", [], {}):
            data[key] = value
    return CaseState.model_validate(data)


def _pending_actions_from_payload(
    *,
    workorder: dict[str, Any],
    latest_artifact_id: str | None,
    evidence_freshness: str,
) -> list[PendingAction]:
    if not workorder:
        return []
    pending = workorder.get("pending_action")
    if isinstance(pending, dict):
        try:
            return [PendingAction.model_validate(pending)]
        except Exception:
            return []
    lifecycle = str(workorder.get("lifecycle_status") or "").strip()
    if lifecycle != "recommended_draft":
        return []
    return [
        PendingAction(
            action_type="workorder_draft",
            status="pending",
            artifact_id=latest_artifact_id,
            reason=str(workorder.get("reason") or ""),
            required_evidence=[],
            source_diagnosis_artifact_id=workorder.get("source_diagnosis_artifact_id") or latest_artifact_id,
            recommendation_artifact_id=latest_artifact_id,
            source_report_artifact_id=workorder.get("source_report_artifact_id"),
            required_role="engineer",
            stale_refresh_required=False,
        )
    ]


def _contains_stale_marker(value: Any) -> bool:
    text = str(value or "")
    lowered = text.lower()
    return any(marker in text or marker.lower() in lowered for marker in _STALE_MARKERS)


def _has_reportable_material(payload: dict[str, Any]) -> bool:
    if payload.get("reportable_payload") not in (None, "", [], {}):
        return True
    if payload.get("operation_report_payload") not in (None, "", [], {}):
        return True
    if payload.get("chart_payload") not in (None, "", [], {}):
        return True
    rows = payload.get("normalized_rows")
    if isinstance(rows, list) and rows:
        return True
    materials = payload.get("report_materials") if isinstance(payload.get("report_materials"), dict) else {}
    rows = materials.get("normalized_rows")
    if isinstance(rows, list) and rows:
        return True
    evidence_bundle = payload.get("evidence_bundle") if isinstance(payload.get("evidence_bundle"), dict) else {}
    evidence_items = evidence_bundle.get("evidence_items")
    claims = evidence_bundle.get("claims")
    return bool((isinstance(evidence_items, list) and evidence_items) or (isinstance(claims, list) and claims))


def _flatten_context(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    interesting = {
        "asset",
        "diagnosis_object",
        "status_level",
        "current_event",
        "key_phenomenon",
        "priority",
        "action_priority",
        "latest_sample_time",
        "last_sample_time",
        "sample_time",
        "sample_count",
        "freshness_label",
        "data_freshness_label",
        "currentness",
        "data_currentness_level",
        "data_currentness_label",
        "currentness_label",
        "next_action",
        "action_priority_label",
        "recommended_action",
        "severity",
        "severity_label",
        "asset_risk_label",
        "one_sentence_conclusion",
        "conclusion",
        "diagnosis_summary",
        "initial_assessment",
        "evidence_summary",
        "findings",
        "key_evidence",
        "event_code",
        "fault_code",
        "abnormal_summary",
        "top_finding",
    }

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in interesting and key not in result and child not in (None, "", [], {}):
                    result[key] = child
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return result


def _available_followups(
    active_asset: str | None,
    active_fault_codes: list[str],
    last_report_url: str | None,
) -> list[str]:
    followups = ["explain_current_frame"]
    if active_asset:
        followups.append("refresh_current_status")
    if active_fault_codes or active_asset:
        followups.append("workorder_decision")
    if last_report_url:
        followups.append("render_previous_result")
    return followups


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _first_list_item(value: Any) -> str | None:
    items = _as_text_list(value)
    return items[0] if items else None


def _first_non_empty(values: list[Any]) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _dedupe(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))
