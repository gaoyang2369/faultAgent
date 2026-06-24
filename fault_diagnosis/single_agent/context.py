"""Conversation context resolution for the restricted single-agent runtime."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..diagnosis.artifact_store import list_thread_artifacts
from ..diagnosis.contracts import DiagnosisArtifactEnvelope


class DiagnosisCase(BaseModel):
    """Active diagnosis context recovered from a thread's recent artifacts."""

    case_id: str
    thread_id: str
    active_asset: str | None = None
    active_fault_codes: list[str] = Field(default_factory=list)
    active_time_window: dict[str, Any] = Field(default_factory=dict)
    last_evidence_bundle_id: str | None = None
    last_report_url: str | None = None
    unresolved_questions: list[str] = Field(default_factory=list)


class ConversationDiagnosisState(BaseModel):
    """Per-thread diagnosis memory used only for deterministic context filling."""

    thread_id: str
    active_case_id: str | None = None
    cases: list[DiagnosisCase] = Field(default_factory=list)

    @property
    def active_case(self) -> DiagnosisCase | None:
        if self.active_case_id:
            for case in self.cases:
                if case.case_id == self.active_case_id:
                    return case
        return self.cases[0] if self.cases else None


def load_conversation_diagnosis_state(thread_id: str, *, limit: int = 5) -> ConversationDiagnosisState:
    """Build thread context from the latest saved diagnosis artifacts."""

    cases: list[DiagnosisCase] = []
    try:
        artifacts = list_thread_artifacts(thread_id, limit=limit)
    except Exception:
        artifacts = []
    for envelope in artifacts:
        case = diagnosis_case_from_artifact(envelope)
        if case is not None:
            cases.append(case)
    return ConversationDiagnosisState(
        thread_id=thread_id,
        active_case_id=cases[0].case_id if cases else None,
        cases=cases,
    )


def diagnosis_case_from_artifact(envelope: DiagnosisArtifactEnvelope) -> DiagnosisCase | None:
    """Extract the most useful thread-local context from one artifact envelope."""

    payload = envelope.payload or {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    objects = decision.get("objects") if isinstance(decision.get("objects"), dict) else {}
    evidence_bundle = (
        payload.get("evidence_bundle") if isinstance(payload.get("evidence_bundle"), dict) else {}
    )
    report_artifact = (
        payload.get("report_artifact") if isinstance(payload.get("report_artifact"), dict) else {}
    )
    context_resolution = (
        decision.get("context_resolution")
        if isinstance(decision.get("context_resolution"), dict)
        else {}
    )

    active_asset = _first_non_empty(
        [
            context_resolution.get("active_asset"),
            _first_list_item(objects.get("device_ids")),
            request.get("equipment_hint"),
        ]
    )
    active_fault_codes = _dedupe(
        [
            *(_as_text_list(context_resolution.get("active_fault_codes"))),
            *(_as_text_list(objects.get("alarm_codes"))),
            request.get("fault_code_hint"),
        ]
    )
    active_time_window = {}
    if isinstance(decision.get("time_window"), dict):
        active_time_window.update(decision["time_window"])
    if request.get("time_range_hint") and "default_strategy" not in active_time_window:
        active_time_window["default_strategy"] = request.get("time_range_hint")

    last_evidence_bundle_id = _first_non_empty(
        [
            evidence_bundle.get("bundle_id"),
            context_resolution.get("last_evidence_bundle_id"),
        ]
    )
    last_report_url = _first_non_empty(
        [
            report_artifact.get("report_url"),
            report_artifact.get("report_filename"),
            envelope.report_filename,
            context_resolution.get("last_report_url"),
        ]
    )
    unresolved_questions = _dedupe(
        [
            *(_as_text_list(context_resolution.get("unresolved_questions"))),
            *(_as_text_list(decision.get("missing_slots"))),
        ]
    )
    case_id = _first_non_empty(
        [
            decision.get("active_case_id"),
            last_evidence_bundle_id,
            (payload.get("trace") or {}).get("trace_id") if isinstance(payload.get("trace"), dict) else None,
            envelope.created_at,
        ]
    )
    if not any([active_asset, active_fault_codes, last_evidence_bundle_id, last_report_url]):
        return None
    return DiagnosisCase(
        case_id=str(case_id),
        thread_id=envelope.thread_id,
        active_asset=active_asset,
        active_fault_codes=active_fault_codes,
        active_time_window=active_time_window,
        last_evidence_bundle_id=last_evidence_bundle_id,
        last_report_url=last_report_url,
        unresolved_questions=unresolved_questions,
    )


def apply_context_resolution(
    *,
    payload: dict[str, Any],
    message: str,
    state: ConversationDiagnosisState | None,
) -> dict[str, Any]:
    """Fill missing slots from the active case and describe what was resolved."""

    active_case = state.active_case if state is not None else None
    payload.setdefault("context_resolution", {})
    current_asset = str(payload.get("equipment_hint") or "").strip() or None
    current_fault_code = str(payload.get("fault_code_hint") or "").strip().upper() or None
    current_assets = _dedupe([current_asset])
    current_fault_codes = _dedupe([current_fault_code])
    referenced = _has_context_reference(message)

    used_active_asset = False
    used_active_fault_codes = False
    unresolved_questions: list[str] = []
    source = "current_message" if (current_asset or current_fault_code) else "none"
    state_assets = _dedupe([case.active_asset for case in (state.cases if state else []) if case.active_asset])
    state_fault_codes = _dedupe(
        [
            code
            for case in (state.cases if state else [])
            for code in case.active_fault_codes
        ]
    )

    if active_case is not None:
        can_reuse_asset = len(state_assets) <= 1 or not referenced
        can_reuse_fault_code = len(state_fault_codes) <= 1 or not referenced
        if (
            not current_asset
            and active_case.active_asset
            and can_reuse_asset
            and (_should_reuse_asset(message) or referenced)
        ):
            payload["equipment_hint"] = active_case.active_asset
            current_asset = active_case.active_asset
            current_assets = [active_case.active_asset]
            used_active_asset = True
            source = "conversation_state"
        if (
            not current_fault_code
            and active_case.active_fault_codes
            and can_reuse_fault_code
            and (_should_reuse_fault_code(message) or referenced)
        ):
            payload["fault_code_hint"] = active_case.active_fault_codes[0]
            current_fault_code = active_case.active_fault_codes[0]
            current_fault_codes = [active_case.active_fault_codes[0]]
            used_active_fault_codes = True
            source = "conversation_state"
        if not payload.get("time_range_hint") and active_case.active_time_window:
            default_strategy = active_case.active_time_window.get("default_strategy")
            if default_strategy:
                payload["time_range_hint"] = default_strategy

    candidate_assets = _dedupe(
        [
            *current_assets,
            *state_assets,
        ]
    )
    candidate_fault_codes = _dedupe(
        [
            *current_fault_codes,
            *state_fault_codes,
        ]
    )
    if referenced and not current_asset and len(candidate_assets) > 1:
        unresolved_questions.append("请确认“它/这个设备”指的是哪个设备。")
        source = "ambiguous"
    if referenced and not current_fault_code and len(candidate_fault_codes) > 1:
        unresolved_questions.append("请确认“这个故障/刚才那个”指的是哪个故障码。")
        source = "ambiguous"

    resolution = {
        "resolved": source not in {"none", "ambiguous"},
        "source": source,
        "references": _context_reference_labels(message),
        "used_active_asset": used_active_asset,
        "used_active_fault_codes": used_active_fault_codes,
        "active_asset": current_asset or (active_case.active_asset if active_case else None),
        "active_fault_codes": current_fault_codes or (active_case.active_fault_codes if active_case else []),
        "active_time_window": payload.get("time_range_hint")
        or (active_case.active_time_window if active_case else {}),
        "last_evidence_bundle_id": active_case.last_evidence_bundle_id if active_case else None,
        "last_report_url": active_case.last_report_url if active_case else None,
        "unresolved_questions": _dedupe([
            *unresolved_questions,
            *(active_case.unresolved_questions if active_case else []),
        ]),
        "candidates": {
            "assets": candidate_assets,
            "fault_codes": candidate_fault_codes,
        },
    }
    payload["context_resolution"] = resolution
    return resolution


def _has_context_reference(message: str) -> bool:
    return bool(_context_reference_labels(message))


def _context_reference_labels(message: str) -> list[str]:
    text = (message or "").replace(" ", "")
    labels: list[str] = []
    for keyword in ("它", "这个故障", "这个设备", "刚才那个", "刚才结果", "刚才", "上一轮", "上一次"):
        if keyword in text:
            labels.append(keyword)
    return _dedupe(labels)


def _should_reuse_asset(message: str) -> bool:
    text = (message or "").replace(" ", "")
    return bool(text) and any(
        keyword in text
        for keyword in ("严重", "状态", "现在", "当前", "看一下", "查一下", "故障", "报警", "告警", "报告")
    )


def _should_reuse_fault_code(message: str) -> bool:
    text = (message or "").replace(" ", "")
    return bool(text) and any(
        keyword in text
        for keyword in ("怎么处理", "如何处理", "解决", "处置", "严重", "影响", "是什么", "含义", "原因", "故障")
    )


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
