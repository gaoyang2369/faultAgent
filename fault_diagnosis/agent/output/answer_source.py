"""Build compact AnswerFacts for presentation-only LLM synthesis."""

from __future__ import annotations

import re
from typing import Any, Iterable

from fault_diagnosis.domain.diagnosis.contracts import EvidenceBundle
from fault_diagnosis.agent.canonical_turn.entity_extractor import DeterministicEntityExtractor

from ..contracts import DeliverableResult
from .answer_contracts import AnswerFactResult, AnswerFacts


_SAFE_CONTENT_KEYS = {
    "explain_fault_code": {"fault_codes", "fault_code_entries"},
    "check_runtime_status": {"assessments", "legacy_sql"},
    "compare_runtime_status": {
        "devices", "assessments", "device_summaries", "comparison_dimensions",
        "similarities", "differences", "ranking", "conclusion",
    },
    "diagnose_fault": {
        "conclusion", "basis", "probable_causes", "verification_items", "recommendations",
        "risk_notice", "missing_information", "confidence_details", "confidence",
    },
    "resolution_recommendation": {"recommendations"},
    "generate_report": {"report_title", "report_url"},
    "create_workorder_draft": {
        "workorder_suggestion", "workorder_pending_action", "workorder_draft",
        "manual_confirmation_required", "draft_only", "dispatch_forbidden",
        "stale_evidence_disclosure_required", "evidence_freshness",
    },
    "evaluate_workorder_need": {"workorder_suggestion"},
}
_SAFE_FALLBACK_KEYS = {"message", "clarification_question"}
_SENSITIVE_KEY_PARTS = (
    "artifact_id", "bundle_id", "claim_id", "evidence_id", "node_id", "trace_id",
    "thread_id", "session_id", "request_id", "draft_id", "idempotency_key",
    "source_hash", "sql_used", "raw_output", "result_preview", "password",
    "credential", "api_key", "connection",
)
_DEVICE_KEYS = {"device", "devices", "device_ref", "device_refs", "equipment_object", "asset_id"}
_FAULT_CODE_KEYS = {"fault_code", "fault_codes", "event_code", "event_codes", "effective_codes", "code"}
_ACTION_KEYS = {
    "lifecycle_status", "status", "dispatch_policy", "dispatch_forbidden", "draft_only",
    "manual_confirmation_required", "need_workorder",
}
_LIMITATION_KEYS = {"limitations", "missing_information", "stale_warning", "risk_notice"}
_RECOMMENDATION_KEYS = {
    "recommendations", "processing_steps", "verification_items", "acceptance_criteria",
    "required_evidence", "next_action",
}
_FACT_LABELS = {
    "runtime_status": "运行状态",
    "data_state": "数据状态",
    "query_status": "查询状态",
    "event_code": "故障码",
    "event_codes": "故障码",
    "fault_code": "故障码",
    "fault_codes": "故障码",
    "meaning": "含义",
    "conclusion": "结论",
    "basis": "依据",
    "probable_causes": "可能原因",
    "key_findings": "关键发现",
    "status_reasons": "状态原因",
    "differences": "差异",
    "similarities": "共同点",
    "ranking": "排序",
    "diagnosis_conclusion": "诊断结论",
    "key_evidence": "关键证据",
    "reason": "原因",
    "message": "说明",
    "sample_count": "样本数量",
    "latest_sample_time": "最新样本时间",
    "priority_label": "优先级",
    "priority": "优先级",
    "risk_level": "风险等级",
    "workorder_type": "工单类型",
    "assignee_role": "建议处理角色",
    "recommended_assignee_role": "建议处理角色",
    "suggested_completion_window": "建议完成时限",
    "status": "状态",
    "lifecycle_status": "生命周期状态",
    "dispatch_policy": "派发策略",
    "report_title": "报告名称",
    "title": "标题",
}
_DISPLAY_VALUES = {
    "runtime_status": {
        "normal": "正常", "attention": "需关注", "warning": "告警",
        "abnormal": "异常", "critical": "严重异常", "unknown": "未知",
    },
    "status": {
        "completed": "已完成", "partial": "部分完成", "blocked": "已阻断",
        "failed": "失败", "denied": "无权限", "draft": "草稿", "pending": "待处理",
    },
    "lifecycle_status": {
        "recommended_draft": "建议生成草稿", "not_recommended": "暂不建议创建",
        "draft": "草稿", "pending": "待处理",
    },
    "dispatch_policy": {"forbidden": "禁止派发", "manual_only": "仅允许人工确认后派发"},
}
_SAFE_LEGACY_SQL_KEYS = {
    "summary", "data_state", "query_status", "data_basis", "requested_window",
    "resolved_window", "latest_sample_time", "sample_count", "runtime_status",
    "status_reasons", "key_findings",
}
_SAFE_WORKORDER_NESTED_KEYS = {
    "workorder_suggestion": {
        "lifecycle_status", "need_workorder", "reason", "workorder_type", "priority",
        "priority_label", "risk_level", "assignee_role", "suggested_completion_window",
        "diagnosis_conclusion", "key_evidence", "processing_steps", "acceptance_criteria",
        "equipment_object", "fault_code", "title", "status",
    },
    "workorder_pending_action": {"status", "required_role", "required_evidence", "message"},
    "workorder_draft": {
        "device", "fault_code", "priority", "workorder_type", "recommended_assignee_role",
        "acceptance_criteria", "stale_warning", "status", "dispatch_policy", "title",
    },
}


def build_answer_facts(
    *,
    user_message: str,
    deterministic_answer: str,
    deliverables: list[DeliverableResult],
    evidence_bundle: EvidenceBundle | None,
    runtime_metadata: dict[str, Any] | None = None,
    auth_safe_context: dict[str, Any] | None = None,
) -> AnswerFacts:
    """Project authorized results into short facts and C/E references."""

    del auth_safe_context, deterministic_answer
    runtime_metadata = dict(runtime_metadata or {})
    safe_deliverables = [_project_deliverable(item) for item in deliverables]
    claims, evidence = _project_evidence_bundle(deliverables, evidence_bundle)
    claim_ref_map = {f"C{index}": str(item["claim_id"]) for index, item in enumerate(claims, start=1)}
    evidence_ref_map = {f"E{index}": str(item["evidence_id"]) for index, item in enumerate(evidence, start=1)}
    claim_short_by_real = {real: short for short, real in claim_ref_map.items()}
    evidence_short_by_real = {real: short for short, real in evidence_ref_map.items()}
    claims_by_id = {str(item["claim_id"]): item for item in claims}
    evidence_by_id = {str(item["evidence_id"]): item for item in evidence}
    internal_ids = _internal_identifiers(deliverables, evidence_bundle)

    results: list[AnswerFactResult] = []
    for source, safe in zip(deliverables, safe_deliverables, strict=True):
        content = safe["structured_content"]
        selected_claims = [claims_by_id[value] for value in source.claim_ids if value in claims_by_id]
        supporting_real_ids = {
            value for claim in selected_claims for value in claim.get("supporting_evidence_ids", [])
        }
        selected_evidence = [
            evidence_by_id[value]
            for value in dict.fromkeys([*source.evidence_ids, *supporting_real_ids])
            if value in evidence_by_id
        ]
        data_basis = _project_data_basis([content])
        limitations = _collect_limitations([content], selected_claims, selected_evidence)
        if any(str(item.get("freshness", {}).get("quality") or "").lower() == "stale" for item in selected_evidence):
            limitations.append("存在滞后证据，不能代表当前实时状态。")
        subject = _first_non_empty([
            *_collect_values(content, _DEVICE_KEYS),
            *[str(item.get("asset_id") or "") for item in selected_claims],
            *[str(item.get("asset_id") or "") for item in selected_evidence],
        ])
        facts = _build_fact_lines(content, selected_claims, selected_evidence)
        actions = _dedupe(_collect_values(content, _RECOMMENDATION_KEYS))
        if "latest_available_fallback" in _resolution_modes(data_basis):
            actions.append("重新获取实时数据后确认当前状态。")
        urls = _dedupe(_collect_values(content, {"report_url"})) if source.status == "completed" else []
        results.append(AnswerFactResult(
            capability=source.capability,
            status=source.status,
            subject=_hide_internal_ids(subject, internal_ids),
            facts=_clean_lines(facts, internal_ids, limit=12),
            limitations=_clean_lines(_dedupe(limitations), internal_ids, limit=6),
            recommended_actions=_clean_lines(_dedupe(actions), internal_ids, limit=8),
            urls=urls,
            data_basis=data_basis,
            claim_refs=[claim_short_by_real[value] for value in source.claim_ids if value in claim_short_by_real],
            evidence_refs=[
                evidence_short_by_real[value]
                for value in dict.fromkeys([*source.evidence_ids, *supporting_real_ids])
                if value in evidence_short_by_real
            ],
            failure_reason=_hide_internal_ids(_safe_text(source.error_message or ""), internal_ids),
        ))

    structured_values = [item["structured_content"] for item in safe_deliverables]
    completed_values = [
        item["structured_content"]
        for source, item in zip(deliverables, safe_deliverables, strict=True)
        if source.status == "completed"
    ]
    allowed_devices = _dedupe([
        *[
            item.value
            for item in DeterministicEntityExtractor().extract(user_message)
            if item.kind == "device_reference"
        ],
        *_collect_goal_devices(runtime_metadata),
        *_collect_values(structured_values, _DEVICE_KEYS),
        *[str(item.get("asset_id") or "") for item in claims],
        *[str(item.get("asset_id") or "") for item in evidence],
    ])
    allowed_codes = [value.upper() for value in _dedupe(_collect_values(structured_values, _FAULT_CODE_KEYS))]
    action_states = _dedupe([
        *_collect_values(structured_values, _ACTION_KEYS),
        *[f"{item.capability}:{item.status}" for item in deliverables],
    ])
    support_refs = {
        claim_short_by_real[str(claim["claim_id"])]: [
            evidence_short_by_real[value]
            for value in claim.get("supporting_evidence_ids", [])
            if value in evidence_short_by_real
        ]
        for claim in claims
        if str(claim["claim_id"]) in claim_short_by_real
    }
    return AnswerFacts(
        user_question=_hide_internal_ids(_safe_text(str(user_message or "").strip()), internal_ids),
        overall_status=_overall_status(runtime_metadata, deliverables),
        results=results,
        allowed_urls=_dedupe(_collect_values(completed_values, {"report_url"})),
        allowed_device_refs=allowed_devices,
        allowed_fault_codes=allowed_codes,
        allowed_action_states=action_states,
        claim_ref_map=claim_ref_map,
        evidence_ref_map=evidence_ref_map,
        claim_support_refs=support_refs,
        internal_identifiers=sorted(internal_ids, key=len, reverse=True),
        limitations_required=any(item.limitations for item in results),
        data_basis_required=any("latest_available_fallback" in _resolution_modes(item.data_basis) for item in results),
    )


def build_answer_source_packet(**kwargs: Any) -> AnswerFacts:
    """Compatibility entry point; returns the compact AnswerFacts contract."""

    return build_answer_facts(**kwargs)


def _project_deliverable(item: DeliverableResult) -> dict[str, Any]:
    allowed_keys = _SAFE_CONTENT_KEYS.get(item.capability, _SAFE_FALLBACK_KEYS)
    content = {
        key: _safe_value(value, key=key)
        for key, value in item.structured_content.items()
        if key in allowed_keys
    }
    legacy_sql = content.get("legacy_sql")
    if isinstance(legacy_sql, dict):
        content["legacy_sql"] = {key: value for key, value in legacy_sql.items() if key in _SAFE_LEGACY_SQL_KEYS}
    for nested_key, nested_allowed in _SAFE_WORKORDER_NESTED_KEYS.items():
        nested = content.get(nested_key)
        if isinstance(nested, dict):
            content[nested_key] = {key: value for key, value in nested.items() if key in nested_allowed}
    return {"structured_content": content}


def _project_evidence_bundle(
    deliverables: list[DeliverableResult], bundle: EvidenceBundle | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if bundle is None:
        return [], []
    requested_claim_ids = {value for item in deliverables for value in item.claim_ids if value}
    if not deliverables:
        requested_claim_ids.update(value for value in bundle.final_claim_ids if value)
    selected_claims = [claim for claim in bundle.claims if claim.claim_id in requested_claim_ids]
    selected_evidence_ids = {value for item in deliverables for value in item.evidence_ids if value}
    selected_evidence_ids.update(
        evidence_id for claim in selected_claims for evidence_id in claim.supporting_evidence_ids if evidence_id
    )
    selected_evidence_ids_in_bundle = {
        item.evidence_id for item in bundle.evidence_items if item.evidence_id in selected_evidence_ids
    }
    evidence = [
        {
            "evidence_id": item.evidence_id,
            "source_type": item.source_type,
            "summary": _safe_text(str(item.summary or item.title or "").strip())[:600],
            "freshness": {
                "quality": item.quality.freshness,
                "timestamp": item.timestamp,
                "time_range": item.time_range or {},
            },
            "asset_id": item.asset_id or "",
            "is_untrusted_data": item.source_type in {"knowledge", "rag", "user", "uploaded_file", "artifact"},
        }
        for item in bundle.evidence_items
        if item.evidence_id in selected_evidence_ids
    ]
    claims = [
        {
            "claim_id": claim.claim_id,
            "claim_type": claim.claim_type,
            "statement": _safe_text(claim.statement)[:600],
            "supporting_evidence_ids": [
                value for value in claim.supporting_evidence_ids if value in selected_evidence_ids_in_bundle
            ],
            "missing_evidence": list(claim.missing_evidence),
            "asset_id": claim.asset_id or "",
            "status": claim.status,
        }
        for claim in selected_claims
    ]
    return claims, evidence


def _build_fact_lines(
    content: dict[str, Any], claims: list[dict[str, Any]], evidence: list[dict[str, Any]],
) -> list[str]:
    lines = [str(item.get("statement") or "") for item in claims]
    lines.extend(_named_fact_lines(content))
    # Trusted runtime evidence can add detail; untrusted text is excluded from the model-facing facts.
    lines.extend(
        str(item.get("summary") or "")
        for item in evidence
        if not item.get("is_untrusted_data")
    )
    return _dedupe(lines)


def _named_fact_lines(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _LIMITATION_KEYS or key in _RECOMMENDATION_KEYS or key in {"data_basis", "report_url"}:
                continue
            if key in _FACT_LABELS:
                label = _FACT_LABELS[key]
                result.extend(f"{label}：{_display_value(key, item)}" for item in _flatten_scalars(child))
                continue
            if key in {"dispatch_forbidden", "draft_only", "manual_confirmation_required", "need_workorder"}:
                if child is True:
                    result.append({
                        "dispatch_forbidden": "禁止自动派发工单",
                        "draft_only": "当前结果仅为工单草稿",
                        "manual_confirmation_required": "工单需要人工确认",
                        "need_workorder": "建议创建工单",
                    }[key])
                elif key == "need_workorder" and child is False:
                    result.append("当前不建议创建工单")
                continue
            result.extend(_named_fact_lines(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            result.extend(_named_fact_lines(child))
    return result


def _display_value(key: str, value: str) -> str:
    return _DISPLAY_VALUES.get(key, {}).get(str(value).strip().lower(), str(value))


def _project_data_basis(values: list[dict[str, Any]]) -> dict[str, Any]:
    bases = [value for value in _collect_dicts(values, "data_basis") if value]
    if not bases:
        return {}
    allowed = {"resolution_mode", "latest_sample_time", "sample_count", "fallback_used", "requested_window", "resolved_window"}
    projected = [
        {key: _safe_value(value, key=key) for key, value in base.items() if key in allowed}
        for base in bases
    ]
    primary = dict(projected[0])
    primary["resolution_modes"] = _dedupe(str(value.get("resolution_mode") or "") for value in projected)
    return {key: value for key, value in primary.items() if value not in (None, "", [], {})}


def _collect_limitations(
    values: list[dict[str, Any]], claims: list[dict[str, Any]], evidence: list[dict[str, Any]],
) -> list[str]:
    limitations = _collect_values(values, _LIMITATION_KEYS)
    limitations.extend(value for claim in claims for value in claim.get("missing_evidence", []))
    return _dedupe(limitations)


def _internal_identifiers(
    deliverables: list[DeliverableResult], evidence_bundle: EvidenceBundle | None,
) -> set[str]:
    values = {
        value
        for item in deliverables
        for value in [item.goal_id, *item.artifact_ids, *item.claim_ids, *item.evidence_ids]
        if value
    }
    if evidence_bundle is not None:
        values.update(value for value in [
            evidence_bundle.bundle_id, evidence_bundle.trace_id, *evidence_bundle.final_claim_ids,
            *[claim.claim_id for claim in evidence_bundle.claims],
            *[item.evidence_id for item in evidence_bundle.evidence_items],
        ] if value)
    return {str(value) for value in values if str(value)}


def _overall_status(metadata: dict[str, Any], deliverables: list[DeliverableResult]) -> str:
    value = str(metadata.get("overall_status") or metadata.get("status") or "")
    if value in {"completed", "partial", "blocked", "denied", "failed"}:
        return value
    statuses = {item.status for item in deliverables}
    if statuses and statuses <= {"completed", "skipped"}:
        return "completed"
    if "denied" in statuses and len(statuses) == 1:
        return "denied"
    if statuses & {"completed", "partial"}:
        return "partial"
    return "failed" if "failed" in statuses else "blocked"


def _collect_goal_devices(metadata: dict[str, Any]) -> list[str]:
    request = metadata.get("canonical_request")
    goals = request.get("goals", []) if isinstance(request, dict) else metadata.get("goals", [])
    values: list[str] = []
    for goal in goals if isinstance(goals, list) else []:
        if not isinstance(goal, dict):
            continue
        values.extend(_collect_values(goal, _DEVICE_KEYS))
        slots = goal.get("resolved_slots")
        if isinstance(slots, dict):
            values.extend(_values(slots.get("device")))
    return _dedupe(values)


def _values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)] if value else []


def _resolution_modes(data_basis: dict[str, Any]) -> set[str]:
    return {str(data_basis.get("resolution_mode") or ""), *map(str, data_basis.get("resolution_modes", []))} - {""}


def _safe_value(value: Any, *, key: str = "") -> Any:
    if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
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
        return value[:1200] if key == "report_url" else _safe_text(value)[:1200]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:600]


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


def _clean_lines(values: Iterable[Any], internal_ids: set[str], *, limit: int) -> list[str]:
    return [
        _hide_internal_ids(_safe_text(str(value).strip()), internal_ids)[:400]
        for value in list(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))[:limit]
    ]


def _hide_internal_ids(value: str, internal_ids: set[str]) -> str:
    result = value
    for internal_id in sorted(internal_ids, key=len, reverse=True):
        result = result.replace(internal_id, "[内部引用已隐藏]")
    return result


def _first_non_empty(values: Iterable[Any]) -> str:
    return next((str(value).strip() for value in values if str(value or "").strip()), "")


def _dedupe(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


def _safe_text(value: str) -> str:
    safe = re.sub(r"(?i)(?:mysql(?:\+\w+)?|postgres(?:ql)?|https?)://[^\s，。；]+", "[外部地址已隐藏]", value)
    safe = re.sub(r"(?i)(?:api[_ -]?key|password|passwd|credential)\s*[:=]\s*[^\s，。；]+", "[敏感值已隐藏]", safe)
    safe = re.sub(r"(?:/home/|[A-Za-z]:\\)[^\s，。；]+", "[内部路径已隐藏]", safe)
    return safe


__all__ = ["build_answer_facts", "build_answer_source_packet"]
