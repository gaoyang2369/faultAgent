"""Compatibility facade for the Agent Engine V2 output pipeline."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import (
    EvidenceBundle,
    SqlStepArtifact,
    WorkOrderDraftArtifact,
    WorkOrderSuggestion,
)
from fault_diagnosis.domain.diagnosis.runtime_status import RuntimeStatusAssessment

from ..contracts import CompositeOutputFrame, NodeResult, OutputFrame, PlanGoal
from ..observability.cutover_observation import build_output_observation
from .deliverables import DeliverableAssembler, build_workorder_payload, composite_status
from .presenter import CompositePresenter
from .artifact_view import project_runtime_artifact_view
from fault_diagnosis.domain.artifacts import ArtifactEnvelope


def build_output_frame(
    *,
    status: str = "completed",
    artifacts: dict[str, Any] | None = None,
    artifact_registry: dict[str, ArtifactEnvelope] | None = None,
    evidence_bundle: EvidenceBundle | dict[str, Any] | None = None,
    node_results: list[NodeResult] | list[dict[str, Any]] | None = None,
    error: dict[str, Any] | None = None,
    cancelled: bool = False,
    cancel_reason: str | None = None,
    output_contract: dict[str, Any] | None = None,
    goals: list[PlanGoal] | list[dict[str, Any]] | None = None,
    plan_nodes: list[Any] | None = None,
    goal_statuses: list[Any] | None = None,
) -> OutputFrame:
    """Build the public frame through assembler then the single presenter."""

    goal_items = list(goals or [])
    node_result_items = list(node_results or [])
    artifact_map = (
        project_runtime_artifact_view(artifact_registry, node_result_items)
        if artifact_registry is not None
        else dict(artifacts or {})
    )
    bundle = _model(evidence_bundle, EvidenceBundle)
    sql_artifact = _model(artifact_map.get("sql_artifact"), SqlStepArtifact)
    runtime_assessment = _model(artifact_map.get("runtime_status_assessment"), RuntimeStatusAssessment)
    workorder_suggestion = _model(artifact_map.get("workorder_suggestion"), WorkOrderSuggestion)
    workorder_draft = _model(artifact_map.get("workorder_draft"), WorkOrderDraftArtifact)
    workorder_payload = build_workorder_payload(
        suggestion=workorder_suggestion,
        pending_action=_as_dict(artifact_map.get("workorder_pending_action")),
        draft=workorder_draft,
        evidence_bundle=bundle,
        node_results=node_result_items,
    )
    contract_validation = _runtime_status_contract_validation(
        runtime_assessment,
        bundle,
        output_contract=output_contract,
    )
    deliverables, goal_execution_results = DeliverableAssembler().assemble(
        goals=goal_items,
        artifacts=artifact_map,
        node_results=node_result_items,
        workorder_payload=workorder_payload,
        status=status,
        evidence_bundle=bundle,
        plan_nodes=plan_nodes,
        goal_statuses=goal_statuses,
    )
    contract_validation = _deliverable_contract_validation(
        deliverables,
        bundle,
        runtime_validation=contract_validation,
    )
    if contract_validation.get("contract_satisfied") is False:
        incomplete = set(contract_validation.get("incomplete_capabilities") or [])
        deliverables = [
            item.model_copy(update={"status": "partial"}, deep=True)
            if item.capability in incomplete and item.status == "completed"
            else item
            for item in deliverables
        ]
        goal_execution_results = [
            item.model_copy(update={"status": "incomplete"}, deep=True)
            if item.capability in incomplete and item.status == "completed"
            else item
            for item in goal_execution_results
        ]
    presented = CompositePresenter().present(
        deliverables=deliverables,
        status=status,
        evidence_bundle=bundle,
        error=error,
        cancelled=cancelled,
        cancel_reason=cancel_reason,
        contract_validation=contract_validation,
        degraded_notice=_degraded_notice(sql_artifact),
    )
    composite = CompositeOutputFrame(
        deliverables=deliverables,
        overall_status=composite_status(deliverables, status),
        content=presented.content,
        answer_variant=presented.answer_variant,
        legacy_answer_variant=presented.answer_variant,
    )
    diagnosis_payload = {
        "sql_artifact": _dump(artifact_map.get("sql_artifact")),
        "knowledge_artifact": _dump(artifact_map.get("knowledge_artifact")),
        "analysis_artifact": _dump(artifact_map.get("analysis_artifact")),
        "report_artifact": _dump(artifact_map.get("report_artifact")),
        "workorder_suggestion": _dump(workorder_suggestion),
        "workorder_pending_action": _as_dict(artifact_map.get("workorder_pending_action")),
        "workorder_draft": _dump(workorder_draft),
        "evidence_bundle": _dump(bundle),
        "node_results": [_dump(item) for item in node_result_items],
    }
    guardrail = {
        "status": status,
        "cancelled": bool(cancelled),
        "missing_evidence": _missing_evidence(bundle, artifact_map.get("analysis_artifact")),
        "final_claim_ids": list(bundle.final_claim_ids if bundle else []),
        "final_claims_without_evidence": _final_claims_without_evidence(bundle),
        "stale_evidence": _stale_evidence(bundle),
        **contract_validation,
    }
    if workorder_payload:
        guardrail.update(
            {
                "target_evidence_bundle_id": workorder_payload.get("target_evidence_bundle_id"),
                "source_artifact_refs": workorder_payload.get("source_artifact_refs", []),
                "supporting_evidence_refs": workorder_payload.get("supporting_evidence_refs", []),
                "stale_evidence_disclosure_required": workorder_payload.get("stale_evidence_disclosure_required", False),
                "evidence_freshness": workorder_payload.get("evidence_freshness", "unknown"),
                "generated_from_previous_artifact": workorder_payload.get("generated_from_previous_artifact", False),
            }
        )
    if error:
        guardrail["error"] = dict(error)
    if cancel_reason:
        guardrail["cancel_reason"] = cancel_reason
    guardrail["output_observation"] = build_output_observation(
        goals=goal_items,
        deliverables=deliverables,
        goal_execution_results=goal_execution_results,
        answer_variant=presented.answer_variant,
        selected_content=presented.content,
    )
    return OutputFrame(
        answer_variant=presented.answer_variant,
        final_answer=presented.content,
        status_brief=presented.status_brief,
        diagnosis_report_payload=diagnosis_payload,
        workorder_draft_payload=workorder_payload,
        artifact_payload={key: _dump(value) for key, value in artifact_map.items()},
        guardrail_result=guardrail,
        runtime_status_assessment=_dump(runtime_assessment) or {},
        contract_validation=contract_validation,
        composite_output=composite,
        goal_execution_results=goal_execution_results,
    )


def _runtime_status_contract_validation(
    assessment: RuntimeStatusAssessment | None,
    bundle: EvidenceBundle | None,
    *,
    output_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    default_fields = [
        "runtime_status",
        "status_reasons",
        "data_basis",
        "data_window",
        "latest_sample_time",
        "freshness_disclosure",
        "supporting_evidence_ids",
    ]
    contract = dict(output_contract or {})
    required_fields = list(contract.get("required_fields") or default_fields)
    missing_fields: list[str] = []
    if assessment is None:
        missing_fields = list(required_fields)
    else:
        if not assessment.runtime_status:
            missing_fields.append("runtime_status")
        if not assessment.status_reasons:
            missing_fields.append("status_reasons")
        if not assessment.supporting_evidence_ids:
            missing_fields.append("supporting_evidence_ids")
        if assessment.data_basis.resolution_mode != "no_data" and assessment.data_basis.resolved_window is None:
            missing_fields.append("data_window")
        if assessment.data_basis.resolution_mode != "no_data" and assessment.data_basis.latest_sample_time is None:
            missing_fields.append("latest_sample_time")
        if assessment.data_basis.resolution_mode == "latest_available_fallback" and not assessment.limitations:
            missing_fields.append("freshness_disclosure")
    claim_types = sorted({claim.claim_type for claim in (bundle.claims if bundle else [])})
    required_claim_types = list(contract.get("required_claim_types") or (["runtime_status_assessment"] if assessment else []))
    missing_claim_types = [value for value in required_claim_types if value not in claim_types]
    evidence_ids = {item.evidence_id for item in (bundle.evidence_items if bundle else []) if item.evidence_id}
    claim_refs_valid = all(
        ref in evidence_ids
        for claim in (bundle.claims if bundle else [])
        if claim.claim_type == "runtime_status_assessment"
        for ref in claim.supporting_evidence_ids
    )
    ledger_passed = bool((bundle.quality_checks if bundle else {}).get("passed", True))
    forbidden = {
        "root_cause",
        "root_cause_conclusion",
        "diagnosis_summary",
        "fault_attribution",
        *[str(item) for item in contract.get("forbidden_claims", [])],
    }
    forbidden_present = sorted(forbidden.intersection(claim_types))
    satisfied = bool(
        assessment
        and not missing_fields
        and not missing_claim_types
        and claim_refs_valid
        and ledger_passed
        and not forbidden_present
    )
    return {
        "required_fields": required_fields,
        "missing_fields": missing_fields,
        "required_claim_types": required_claim_types,
        "present_claim_types": claim_types,
        "missing_claim_types": missing_claim_types,
        "ledger_passed": ledger_passed,
        "forbidden_claim_types_present": forbidden_present,
        "contract_satisfied": satisfied,
    }


def _deliverable_contract_validation(
    deliverables,
    bundle: EvidenceBundle | None,
    *,
    runtime_validation: dict[str, Any],
) -> dict[str, Any]:  # noqa: ANN001
    capabilities = {item.capability for item in deliverables}
    covered = capabilities.intersection({"check_runtime_status", "explain_fault_code", "generate_report"})
    if len(covered) > 1:
        children = [
            _deliverable_contract_validation(
                [item for item in deliverables if item.capability == capability],
                bundle,
                runtime_validation=runtime_validation,
            )
            for capability in sorted(covered)
        ]
        return {
            "required_fields": [
                f"{capability}.{field}"
                for capability, child in zip(sorted(covered), children, strict=True)
                for field in child.get("required_fields", [])
            ],
            "missing_fields": [
                f"{capability}.{field}"
                for capability, child in zip(sorted(covered), children, strict=True)
                for field in child.get("missing_fields", [])
            ],
            "required_claim_types": list(dict.fromkeys(
                value for child in children for value in child.get("required_claim_types", [])
            )),
            "present_claim_types": list(dict.fromkeys(
                value for child in children for value in child.get("present_claim_types", [])
            )),
            "missing_claim_types": list(dict.fromkeys(
                value for child in children for value in child.get("missing_claim_types", [])
            )),
            "ledger_passed": all(child.get("ledger_passed", True) for child in children),
            "forbidden_claim_types_present": list(dict.fromkeys(
                value for child in children for value in child.get("forbidden_claim_types_present", [])
            )),
            "contract_satisfied": all(child.get("contract_satisfied") is True for child in children),
            "covered_capabilities": sorted(covered),
            "incomplete_capabilities": list(dict.fromkeys(
                value for child in children for value in child.get("incomplete_capabilities", [])
            )),
        }
    if "check_runtime_status" in capabilities:
        satisfied = runtime_validation.get("contract_satisfied") is True
        return {
            **runtime_validation,
            "covered_capabilities": ["check_runtime_status"],
            "incomplete_capabilities": [] if satisfied else ["check_runtime_status"],
        }
    if capabilities == {"explain_fault_code"}:
        entries = deliverables[0].structured_content.get("fault_code_entries") or []
        entry = entries[0] if entries and isinstance(entries[0], dict) else {}
        checks = {
            "fault_code": bool(entry.get("code")),
            "meaning": bool(entry.get("meaning") or entry.get("title")),
            "cause_or_trigger_condition": bool(entry.get("cause")),
            "recommended_action": bool(entry.get("remedy")),
            "source_file": bool(entry.get("source_file")),
            "source_page": bool(entry.get("page")),
        }
        claim_types = sorted({claim.claim_type for claim in (bundle.claims if bundle else [])})
        missing = [field for field, present in checks.items() if not present]
        return {
            "required_fields": list(checks),
            "missing_fields": missing,
            "required_claim_types": ["fault_code_explanation"],
            "present_claim_types": claim_types,
            "missing_claim_types": [] if "fault_code_explanation" in claim_types else ["fault_code_explanation"],
            "ledger_passed": bool((bundle.quality_checks if bundle else {}).get("passed", True)),
            "forbidden_claim_types_present": [],
            "contract_satisfied": not missing and "fault_code_explanation" in claim_types,
            "covered_capabilities": ["explain_fault_code"],
            "incomplete_capabilities": (
                [] if not missing and "fault_code_explanation" in claim_types
                else ["explain_fault_code"]
            ),
        }
    if capabilities == {"generate_report"}:
        item = deliverables[0]
        payload = item.structured_content
        checks = {
            "report_generated": item.status == "completed",
            "analysis_source": bool(item.dependency_goal_ids or item.artifact_ids),
            "report_summary": bool(payload.get("report_title") or payload.get("save_result")),
            "report_access": bool(payload.get("report_url") or payload.get("report_filename") or item.artifact_ids),
        }
        missing = [field for field, present in checks.items() if not present]
        return {
            "required_fields": list(checks),
            "missing_fields": missing,
            "required_claim_types": [],
            "present_claim_types": [],
            "missing_claim_types": [],
            "ledger_passed": True,
            "forbidden_claim_types_present": [],
            "contract_satisfied": not missing,
            "covered_capabilities": ["generate_report"],
            "incomplete_capabilities": [] if not missing else ["generate_report"],
        }
    return runtime_validation


def _missing_evidence(bundle: EvidenceBundle | None, analysis: Any) -> list[str]:
    values: list[Any] = []
    if bundle:
        for claim in bundle.claims:
            values.extend(claim.missing_evidence)
        if isinstance(bundle.quality_checks.get("missing_evidence"), list):
            values.extend(bundle.quality_checks["missing_evidence"])
    dumped = _as_dict(analysis)
    values.extend(dumped.get("missing_information") or [])
    return _dedupe(values)


def _final_claims_without_evidence(bundle: EvidenceBundle | None) -> list[str]:
    if not bundle:
        return []
    final_ids = set(bundle.final_claim_ids)
    return [
        claim.claim_id
        for claim in bundle.claims
        if (claim.claim_id in final_ids or claim.status == "final") and not claim.supporting_evidence_ids
    ]


def _stale_evidence(bundle: EvidenceBundle | None) -> list[dict[str, Any]]:
    if not bundle:
        return []
    stale_ids = {str(item) for item in bundle.quality_checks.get("stale_evidence_ids", []) or []}
    result = []
    for item in bundle.evidence_items:
        freshness = str(getattr(item.quality, "freshness", "") or item.metadata.get("freshness", "")).lower()
        if item.evidence_id in stale_ids or freshness == "stale" or item.evidence_type == "stale_evidence_disclosure":
            result.append({"evidence_id": item.evidence_id, "summary": item.summary, "evidence_type": item.evidence_type})
    return result


def _degraded_notice(sql_artifact: SqlStepArtifact | None) -> str:
    summary = str(getattr(sql_artifact, "summary", "") or "")
    if "游客不能生成正式报告" in summary or "当前身份不能生成正式报告" in summary:
        return "当前身份不能生成正式报告，已降级为运行状态摘要。"
    return ""


def _model(value: Any, model_type: Any) -> Any:
    if isinstance(value, model_type):
        return value
    if isinstance(value, dict):
        return model_type.model_validate(value)
    return None


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return value


def _as_dict(value: Any) -> dict[str, Any]:
    dumped = _dump(value)
    return dict(dumped) if isinstance(dumped, dict) else {}


def _dedupe(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))
