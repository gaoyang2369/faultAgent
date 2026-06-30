"""Artifact-backed case projection for context resolution."""

from __future__ import annotations

from typing import Any

from ..diagnosis.artifact_store import list_thread_artifacts
from ..diagnosis.contracts import DiagnosisArtifactEnvelope
from .contracts import (
    CASE_STATE_SNAPSHOT_VERSION,
    CaseState,
    ConversationDiagnosisState,
    PendingAction,
)

_STALE_MARKERS = ("已滞后", "滞后", "stale", "STALE", "非实时", "不代表实时")


class ArtifactBackedCaseStore:
    """Project thread-local CaseState objects from diagnosis artifacts."""

    def __init__(self, *, limit: int = 5):
        self.limit = max(1, limit)

    def load(self, thread_id: str) -> ConversationDiagnosisState:
        cases: list[CaseState] = []
        try:
            artifacts = list_thread_artifacts(thread_id, limit=self.limit)
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


def case_state_from_artifact(envelope: DiagnosisArtifactEnvelope) -> CaseState | None:
    """Build CaseState from snapshot cache, falling back to raw artifact payload."""

    snapshot_warning = _snapshot_rejection_reason(envelope)
    snapshot_case = _case_from_snapshot(envelope)
    fallback_case = _case_from_payload(envelope)
    if snapshot_case is None:
        if fallback_case is not None and snapshot_warning:
            fallback_case.projection_warnings.append(snapshot_warning)
        return fallback_case
    if fallback_case is None:
        return snapshot_case
    return _merge_snapshot_with_fallback(snapshot_case, fallback_case)


def build_case_state_snapshot(
    envelope: DiagnosisArtifactEnvelope,
) -> dict[str, Any]:
    """Build an optional cache payload from the saved artifact envelope."""

    case = _case_from_payload(envelope)
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


def _case_from_payload(envelope: DiagnosisArtifactEnvelope) -> CaseState | None:
    payload = envelope.payload or {}
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
            _first_list_item(objects.get("device_ids")),
            request.get("equipment_hint"),
            report_context.get("asset"),
            report_context.get("diagnosis_object"),
        ]
    )
    active_fault_codes = _dedupe(
        [
            *(_as_text_list(context_resolution.get("active_fault_codes"))),
            *(_as_text_list(objects.get("alarm_codes"))),
            request.get("fault_code_hint"),
            report_context.get("event_code"),
            report_context.get("fault_code"),
        ]
    )
    active_time_window: dict[str, Any] = {}
    if isinstance(decision.get("time_window"), dict):
        active_time_window.update(decision["time_window"])
    if request.get("time_range_hint") and "default_strategy" not in active_time_window:
        active_time_window["default_strategy"] = request.get("time_range_hint")

    latest_evidence_bundle_id = _first_non_empty(
        [
            evidence_bundle.get("bundle_id"),
            context_resolution.get("last_evidence_bundle_id"),
        ]
    )
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
        latest_report_id=latest_report_id,
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
            [report_context.get("latest_sample_time"), report_context.get("last_sample_time"), report_context.get("sample_time")]
        ),
        sample_count=_as_int(report_context.get("sample_count")),
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
        unresolved_questions=_dedupe(
            [
                *(_as_text_list(context_resolution.get("unresolved_questions"))),
                *(_as_text_list(decision.get("missing_slots"))),
            ]
        ),
        evidence_freshness=evidence_freshness,
    )


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
    status = str(workorder.get("status") or "").strip()
    if not (workorder.get("need_workorder") or status):
        return []
    required = ["latest_realtime_status"] if evidence_freshness == "stale" else []
    return [
        PendingAction(
            action_type="workorder_decision",
            status=status or "pending",
            artifact_id=latest_artifact_id,
            reason=str(workorder.get("reason") or ""),
            required_evidence=required,
        )
    ]


def _contains_stale_marker(value: Any) -> bool:
    text = str(value or "")
    lowered = text.lower()
    return any(marker in text or marker.lower() in lowered for marker in _STALE_MARKERS)


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
