"""Build the minimal authorization-safe packet used for answer synthesis."""

from __future__ import annotations

import re
from typing import Any, Iterable

from fault_diagnosis.domain.diagnosis.contracts import EvidenceBundle

from ..contracts import DeliverableResult
from .answer_contracts import AnswerSourcePacket


_SAFE_CONTENT_KEYS = {
    "explain_fault_code": {"fault_codes", "fault_code_entries"},
    "check_runtime_status": {"assessments", "legacy_sql"},
    "compare_runtime_status": {
        "devices",
        "assessments",
        "device_summaries",
        "comparison_dimensions",
        "similarities",
        "differences",
        "ranking",
        "conclusion",
    },
    "diagnose_fault": {
        "conclusion",
        "basis",
        "probable_causes",
        "verification_items",
        "recommendations",
        "risk_notice",
        "missing_information",
        "confidence_details",
        "confidence",
    },
    "resolution_recommendation": {"recommendations"},
    "generate_report": {"report_title", "report_url"},
    "create_workorder_draft": {
        "workorder_suggestion",
        "workorder_pending_action",
        "workorder_draft",
        "manual_confirmation_required",
        "draft_only",
        "dispatch_forbidden",
        "stale_evidence_disclosure_required",
        "evidence_freshness",
    },
    "evaluate_workorder_need": {
        "workorder_suggestion",
    },
}
_SAFE_FALLBACK_KEYS = {"message", "clarification_question"}
_SENSITIVE_KEY_PARTS = (
    "artifact_id",
    "bundle_id",
    "claim_id",
    "evidence_id",
    "node_id",
    "trace_id",
    "thread_id",
    "session_id",
    "request_id",
    "draft_id",
    "idempotency_key",
    "source_hash",
    "sql_used",
    "raw_output",
    "result_preview",
    "password",
    "credential",
    "api_key",
    "connection",
)
_DEVICE_KEYS = {"device", "devices", "device_ref", "device_refs", "equipment_object", "asset_id"}
_FAULT_CODE_KEYS = {"fault_code", "fault_codes", "event_code", "event_codes", "effective_codes", "code"}
_ACTION_KEYS = {
    "lifecycle_status",
    "status",
    "dispatch_policy",
    "dispatch_forbidden",
    "draft_only",
    "manual_confirmation_required",
}
_SAFE_LEGACY_SQL_KEYS = {
    "summary",
    "data_state",
    "query_status",
    "data_basis",
    "requested_window",
    "resolved_window",
    "latest_sample_time",
    "sample_count",
    "runtime_status",
    "status_reasons",
    "key_findings",
}
_SAFE_WORKORDER_NESTED_KEYS = {
    "workorder_suggestion": {
        "lifecycle_status", "need_workorder", "reason", "workorder_type", "priority", "priority_label",
        "risk_level", "assignee_role", "suggested_completion_window", "diagnosis_conclusion", "key_evidence",
        "processing_steps", "acceptance_criteria", "equipment_object", "fault_code", "title", "status",
    },
    "workorder_pending_action": {"status", "required_role", "required_evidence", "message"},
    "workorder_draft": {
        "device", "fault_code", "priority", "workorder_type", "recommended_assignee_role", "acceptance_criteria",
        "stale_warning", "status", "dispatch_policy", "title",
    },
}


def build_answer_source_packet(
    *,
    user_message: str,
    deterministic_answer: str,
    deliverables: list[DeliverableResult],
    evidence_bundle: EvidenceBundle | None,
    runtime_metadata: dict[str, Any] | None = None,
    auth_safe_context: dict[str, Any] | None = None,
) -> AnswerSourcePacket:
    """Project completed runtime facts without exposing raw artifacts or policy internals."""

    del auth_safe_context  # Deliberately excluded: the answer layer does not reinterpret authorization.
    runtime_metadata = dict(runtime_metadata or {})
    safe_deliverables = [_project_deliverable(item) for item in deliverables]
    claims, evidence = _project_evidence_bundle(deliverables, evidence_bundle)
    structured_values = [item["structured_content"] for item in safe_deliverables]
    completed_values = [
        item["structured_content"]
        for source, item in zip(deliverables, safe_deliverables, strict=True)
        if source.status == "completed"
    ]
    data_basis = _project_data_basis(structured_values)
    limitations = _collect_limitations(structured_values, claims, evidence)
    allowed_urls = _dedupe(_collect_values(completed_values, {"report_url"}))
    allowed_devices = _dedupe(
        [
            *_collect_values(structured_values, _DEVICE_KEYS),
            *[str(item.get("asset_id") or "") for item in claims],
            *[str(item.get("asset_id") or "") for item in evidence],
        ]
    )
    allowed_fault_codes = [value.upper() for value in _dedupe(_collect_values(structured_values, _FAULT_CODE_KEYS))]
    action_states = _dedupe(
        [
            *_collect_values(structured_values, _ACTION_KEYS),
            *[f"{item.capability}:{item.status}" for item in deliverables],
        ]
    )
    return AnswerSourcePacket(
        user_request=_safe_text(str(user_message or "").strip()),
        overall_status=runtime_metadata.get("overall_status") or runtime_metadata.get("status"),  # type: ignore[arg-type]
        goals=[
            {"goal_id": item.goal_id, "capability": item.capability, "status": item.status}
            for item in deliverables
        ],
        deliverables=safe_deliverables,
        claims=claims,
        evidence=evidence,
        data_basis=data_basis,
        limitations=limitations,
        allowed_urls=allowed_urls,
        allowed_device_refs=allowed_devices,
        allowed_fault_codes=allowed_fault_codes,
        allowed_action_states=action_states,
        deterministic_fallback=_sanitize_deterministic_fallback(
            deterministic_answer,
            deliverables=deliverables,
            evidence_bundle=evidence_bundle,
        ),
    )


def _project_deliverable(item: DeliverableResult) -> dict[str, Any]:
    allowed_keys = _SAFE_CONTENT_KEYS.get(item.capability, _SAFE_FALLBACK_KEYS)
    content = {
        key: _safe_value(value, key=key)
        for key, value in item.structured_content.items()
        if key in allowed_keys
    }
    legacy_sql = content.get("legacy_sql")
    if isinstance(legacy_sql, dict):
        content["legacy_sql"] = {
            key: value for key, value in legacy_sql.items() if key in _SAFE_LEGACY_SQL_KEYS
        }
    for nested_key, nested_allowed in _SAFE_WORKORDER_NESTED_KEYS.items():
        nested = content.get(nested_key)
        if isinstance(nested, dict):
            content[nested_key] = {key: value for key, value in nested.items() if key in nested_allowed}
    return {
        "goal_id": item.goal_id,
        "capability": item.capability,
        "status": item.status,
        "title": item.title,
        "structured_content": content,
        "error_code": item.error_code or "",
        "error_message": _safe_text(item.error_message or ""),
    }


def _project_evidence_bundle(
    deliverables: list[DeliverableResult],
    bundle: EvidenceBundle | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if bundle is None:
        return [], []
    requested_claim_ids = {value for item in deliverables for value in item.claim_ids if value}
    if not deliverables:
        requested_claim_ids.update(value for value in bundle.final_claim_ids if value)
    selected_claims = [claim for claim in bundle.claims if claim.claim_id in requested_claim_ids]
    selected_evidence_ids = {value for item in deliverables for value in item.evidence_ids if value}
    selected_evidence_ids.update(
        evidence_id
        for claim in selected_claims
        for evidence_id in claim.supporting_evidence_ids
        if evidence_id
    )
    projected_evidence: list[dict[str, Any]] = []
    selected_evidence_ids_in_bundle: set[str] = set()
    for item in bundle.evidence_items:
        evidence_id = item.evidence_id
        if evidence_id not in selected_evidence_ids:
            continue
        selected_evidence_ids_in_bundle.add(evidence_id)
        projected_evidence.append(
            {
                "evidence_id": evidence_id,
                "source_type": item.source_type,
                "summary": _safe_text(str(item.summary or item.title or "").strip())[:1600],
                "freshness": {
                    "quality": item.quality.freshness,
                    "timestamp": item.timestamp,
                    "time_range": item.time_range or {},
                },
                "asset_id": item.asset_id or "",
                "is_untrusted_data": item.source_type in {"knowledge", "rag", "user", "uploaded_file", "artifact"},
            }
        )
    projected_claims = [
        {
            "claim_id": claim.claim_id,
            "claim_type": claim.claim_type,
            "statement": _safe_text(claim.statement),
            "supporting_evidence_ids": [
                value for value in claim.supporting_evidence_ids if value in selected_evidence_ids_in_bundle
            ],
            "missing_evidence": list(claim.missing_evidence),
            "asset_id": claim.asset_id or "",
            "status": claim.status,
        }
        for claim in selected_claims
    ]
    return projected_claims, projected_evidence


def _project_data_basis(values: list[dict[str, Any]]) -> dict[str, Any]:
    bases = [value for value in _collect_dicts(values, "data_basis") if value]
    if not bases:
        return {}
    safe_bases = [_safe_value(value, key="data_basis") for value in bases]
    primary = dict(safe_bases[0]) if isinstance(safe_bases[0], dict) else {}
    primary["resolution_modes"] = _dedupe(
        [str(value.get("resolution_mode") or "") for value in safe_bases if isinstance(value, dict)]
    )
    if len(safe_bases) > 1:
        primary["sources"] = safe_bases
    return primary


def _collect_limitations(
    values: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> list[str]:
    limitations = _collect_values(values, {"limitations", "missing_information", "stale_warning"})
    limitations.extend(value for claim in claims for value in claim.get("missing_evidence", []))
    for item in evidence:
        if str(item.get("freshness", {}).get("quality") or "").lower() == "stale":
            limitations.append(str(item.get("summary") or "存在滞后证据，不能代表当前实时状态。"))
    return _dedupe(limitations)


def _sanitize_deterministic_fallback(
    answer: str,
    *,
    deliverables: list[DeliverableResult],
    evidence_bundle: EvidenceBundle | None,
) -> str:
    internal_ids = {
        value
        for item in deliverables
        for value in [*item.artifact_ids, *item.claim_ids, *item.evidence_ids]
        if value
    }
    if evidence_bundle is not None:
        internal_ids.update(
            value
            for value in [
                evidence_bundle.bundle_id,
                evidence_bundle.trace_id,
                *evidence_bundle.final_claim_ids,
                *[claim.claim_id for claim in evidence_bundle.claims],
                *[item.evidence_id for item in evidence_bundle.evidence_items],
            ]
            if value
        )
    safe = str(answer or "")
    for value in sorted(internal_ids, key=len, reverse=True):
        safe = safe.replace(value, "[内部引用已隐藏]")
    return _safe_text(safe)


def _safe_value(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return None
    if isinstance(value, dict):
        return {
            str(child_key): safe
            for child_key, child_value in value.items()
            if (safe := _safe_value(child_value, key=str(child_key))) is not None
        }
    if isinstance(value, (list, tuple)):
        return [safe for item in value if (safe := _safe_value(item, key=key)) is not None]
    if isinstance(value, str):
        if key == "report_url":
            return value[:2400]
        return _safe_text(value)[:2400]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1200]


def _collect_values(values: Any, keys: set[str]) -> list[str]:
    collected: list[str] = []
    if isinstance(values, dict):
        for key, value in values.items():
            if str(key) in keys:
                collected.extend(_flatten_scalars(value))
            collected.extend(_collect_values(value, keys))
    elif isinstance(values, (list, tuple)):
        for value in values:
            collected.extend(_collect_values(value, keys))
    return collected


def _collect_dicts(values: Any, key_name: str) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    if isinstance(values, dict):
        for key, value in values.items():
            if key == key_name and isinstance(value, dict):
                collected.append(value)
            collected.extend(_collect_dicts(value, key_name))
    elif isinstance(values, (list, tuple)):
        for value in values:
            collected.extend(_collect_dicts(value, key_name))
    return collected


def _flatten_scalars(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [item for child in value.values() for item in _flatten_scalars(child)]
    if isinstance(value, (list, tuple, set)):
        return [item for child in value for item in _flatten_scalars(child)]
    if isinstance(value, bool):
        return [str(value).lower()]
    text = str(value or "").strip()
    return [text] if text else []


def _dedupe(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


def _safe_text(value: str) -> str:
    safe = re.sub(r"(?i)(?:mysql(?:\+\w+)?|postgres(?:ql)?|https?)://[^\s，。；]+", "[外部地址已隐藏]", value)
    safe = re.sub(r"(?i)(?:api[_ -]?key|password|passwd|credential)\s*[:=]\s*[^\s，。；]+", "[敏感值已隐藏]", safe)
    safe = re.sub(r"(?:/home/|[A-Za-z]:\\)[^\s，。；]+", "[内部路径已隐藏]", safe)
    return safe
