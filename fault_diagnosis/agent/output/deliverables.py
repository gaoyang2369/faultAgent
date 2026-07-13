"""Map runtime goals and artifacts to public deliverable contracts."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    EvidenceBundle,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    WorkOrderDraftArtifact,
    WorkOrderSuggestion,
)

from ..contracts import DeliverableResult, NodeResult, PlanGoal


_DELIVERABLE_TYPES = {
    "fault_code_explanation",
    "runtime_status",
    "runtime_comparison",
    "diagnosis",
    "recommendations",
    "report",
    "workorder_draft",
    "clarification",
    "permission_denied",
}


class DeliverableAssembler:
    """Assemble exactly one deliverable for each requested goal."""

    def assemble(
        self,
        *,
        goals: list[PlanGoal] | list[dict[str, Any]],
        artifacts: dict[str, Any],
        node_results: list[NodeResult] | list[dict[str, Any]],
        workorder_payload: dict[str, Any],
        status: str,
        requested_variant: str | None = None,
    ) -> list[DeliverableResult]:
        knowledge = _model(artifacts.get("knowledge_artifact"), KnowledgeStepArtifact)
        analysis = _model(artifacts.get("analysis_artifact"), AnalysisStepArtifact)
        report = _model(artifacts.get("report_artifact"), ReportStepArtifact)
        assessments_raw = artifacts.get("runtime_status_assessments")
        assessments = list(assessments_raw.values()) if isinstance(assessments_raw, dict) else []
        if not assessments and artifacts.get("runtime_status_assessment") is not None:
            assessments = [artifacts["runtime_status_assessment"]]
        comparison = _as_dict(artifacts.get("comparison_artifact"))
        clarification = _as_dict(artifacts.get("clarification"))
        sql = _as_dict(artifacts.get("sql_artifact"))
        sql_ids = [str(item) for item in artifacts.get("sql_artifact_ids", []) if str(item)]
        node_by_goal = _node_results_by_goal(node_results)

        normalized_goals = [_dump(goal) or {} for goal in goals]
        has_deliverable_contract = any(
            any(item in _DELIVERABLE_TYPES for item in (goal.get("requested_deliverables") or goal.get("expected_outputs") or []))
            for goal in normalized_goals
        )
        if not has_deliverable_contract:
            normalized_goals = self._compatibility_goals(
                artifacts=artifacts,
                status=status,
                requested_variant=requested_variant,
            )

        results: list[DeliverableResult] = []
        for goal in normalized_goals:
            goal_id = str(goal.get("goal_id") or "compat_primary")
            if goal.get("authorization_status") == "denied":
                results.append(
                    DeliverableResult(
                        goal_id=goal_id,
                        deliverable_type="permission_denied",
                        status="blocked",
                        payload={"capability": goal.get("capability")},
                        error_code=str(goal.get("drop_reason") or "capability_permission_denied"),
                        error_message="当前身份无权执行该子目标。",
                    )
                )
                continue
            if clarification:
                results.append(
                    DeliverableResult(
                        goal_id=goal_id,
                        deliverable_type="clarification",
                        status="blocked",
                        payload=clarification,
                        error_code="missing_required_slot",
                        error_message=str(clarification.get("clarification_question") or "请确认缺失信息后继续。"),
                    )
                )
                continue

            kinds = list(goal.get("requested_deliverables") or goal.get("expected_outputs") or [])
            kind = next((item for item in kinds if item in _DELIVERABLE_TYPES), None)
            if kind is None:
                continue
            result = self._assemble_one(
                goal_id=goal_id,
                kind=kind,
                assessments=assessments,
                comparison=comparison,
                sql=sql,
                sql_ids=sql_ids,
                knowledge=knowledge,
                analysis=analysis,
                report=report,
                workorder_payload=workorder_payload,
                goal_node_results=node_by_goal.get(goal_id, []),
                compatibility_call=bool(goal.get("compatibility_call")),
                allow_empty_diagnosis=bool(goal.get("compatibility_call")) and requested_variant == "diagnosis_answer",
            )
            results.append(result)
        return results

    @staticmethod
    def _compatibility_goals(
        *,
        artifacts: dict[str, Any],
        status: str,
        requested_variant: str | None,
    ) -> list[dict[str, Any]]:
        """Represent legacy no-goal calls as one synthetic deliverable."""

        if status in {"failed", "blocked", "cancelled"}:
            if requested_variant == "permission_denied":
                return [{"goal_id": "compat_primary", "requested_deliverables": ["permission_denied"], "compatibility_call": True}]
            return []
        if requested_variant == "permission_denied":
            return [{"goal_id": "compat_primary", "requested_deliverables": ["permission_denied"], "compatibility_call": True}]
        if requested_variant == "clarification" and not artifacts:
            return [{"goal_id": "compat_primary", "requested_deliverables": ["clarification"], "compatibility_call": True}]
        if requested_variant == "diagnosis_answer" and not artifacts:
            return [{"goal_id": "compat_primary", "requested_deliverables": ["diagnosis"], "compatibility_call": True}]
        priorities = (
            ("workorder_draft", "workorder_draft"),
            ("workorder_suggestion", "workorder_draft"),
            ("report_artifact", "report"),
            ("analysis_artifact", "diagnosis"),
            ("knowledge_artifact", "fault_code_explanation"),
            ("comparison_artifact", "runtime_comparison"),
            ("runtime_status_assessment", "runtime_status"),
            ("runtime_status_assessments", "runtime_status"),
            ("sql_artifact", "runtime_status"),
            ("clarification", "clarification"),
        )
        for artifact_key, deliverable_type in priorities:
            if artifacts.get(artifact_key) is not None:
                return [{"goal_id": "compat_primary", "requested_deliverables": [deliverable_type], "compatibility_call": True}]
        return []

    @staticmethod
    def _assemble_one(
        *,
        goal_id: str,
        kind: str,
        assessments: list[Any],
        comparison: dict[str, Any],
        sql: dict[str, Any],
        sql_ids: list[str],
        knowledge: KnowledgeStepArtifact | None,
        analysis: AnalysisStepArtifact | None,
        report: ReportStepArtifact | None,
        workorder_payload: dict[str, Any],
        goal_node_results: list[dict[str, Any]],
        compatibility_call: bool,
        allow_empty_diagnosis: bool,
    ) -> DeliverableResult:
        failure_status = _goal_failure_status(goal_node_results)
        if kind == "fault_code_explanation":
            success = bool(knowledge and knowledge.success)
            payload = {
                "fault_codes": list(getattr(knowledge, "fault_codes", []) or []),
                "fault_code_entries": [
                    {
                        key: value
                        for key, value in (_dump(entry) or {}).items()
                        if key in {"code", "title", "meaning", "cause", "remedy", "match_type"}
                    }
                    for entry in list(getattr(knowledge, "fault_code_entries", []) or [])
                ],
            }
            return DeliverableResult(
                goal_id=goal_id,
                deliverable_type=kind,
                status="completed" if success else "failed",
                payload=payload,
                error_code=None if success else str(getattr(knowledge, "error_code", "") or "knowledge_unavailable"),
                error_message=None if success else str(getattr(knowledge, "error", "") or "知识库未返回可靠释义。"),
            )
        if kind == "runtime_status":
            available = bool(assessments or (compatibility_call and sql))
            return DeliverableResult(
                goal_id=goal_id,
                deliverable_type=kind,
                status="completed" if available else failure_status,
                payload={"assessments": [_dump(item) for item in assessments], "legacy_sql": sql},
                source_artifact_ids=sql_ids,
                error_code=None if available else "runtime_status_unavailable",
            )
        if kind == "runtime_comparison":
            return DeliverableResult(
                goal_id=goal_id,
                deliverable_type=kind,
                status="completed" if comparison else failure_status,
                payload=comparison,
                source_artifact_ids=list(comparison.get("source_artifact_ids") or sql_ids),
                error_code=None if comparison else "comparison_unavailable",
            )
        if kind in {"diagnosis", "recommendations"}:
            success = bool((analysis and analysis.success) or (kind == "diagnosis" and allow_empty_diagnosis))
            degraded = bool(knowledge is not None and not knowledge.success)
            payload = (
                _dump(analysis) or {}
                if kind == "diagnosis"
                else {"recommendations": list(getattr(analysis, "recommendations", []) or [])}
            )
            return DeliverableResult(
                goal_id=goal_id,
                deliverable_type=kind,
                status="partial" if success and degraded else "completed" if success else failure_status,
                payload=payload,
                source_artifact_ids=sql_ids,
                error_code="knowledge_evidence_unavailable" if success and degraded else None if success else "analysis_unavailable",
            )
        if kind == "report":
            success = bool(report and report.success)
            return DeliverableResult(
                goal_id=goal_id,
                deliverable_type=kind,
                status="completed" if success else failure_status,
                payload=_dump(report) or {},
                source_artifact_ids=sql_ids,
                error_code=None if success else "report_unavailable",
            )
        if kind == "workorder_draft":
            success = bool(
                workorder_payload.get("workorder_draft")
                or workorder_payload.get("draft")
                or workorder_payload.get("draft_id")
                or (compatibility_call and workorder_payload.get("workorder_suggestion"))
            )
            return DeliverableResult(
                goal_id=goal_id,
                deliverable_type=kind,
                status="completed" if success else failure_status,
                payload=dict(workorder_payload),
                source_artifact_ids=[
                    str(item.get("artifact_id"))
                    for item in workorder_payload.get("source_artifact_refs", [])
                    if isinstance(item, dict) and item.get("artifact_id")
                ],
                error_code=None if success else "workorder_draft_unavailable",
            )
        if kind == "permission_denied":
            return DeliverableResult(
                goal_id=goal_id,
                deliverable_type=kind,
                status="blocked",
                error_code="capability_permission_denied",
                error_message="当前身份无权执行该子目标。",
            )
        return DeliverableResult(
            goal_id=goal_id,
            deliverable_type="clarification",
            status="blocked",
            error_code="missing_required_slot",
            error_message="需要补充设备、故障码或时间窗口等关键信息后才能继续处理。",
        )


def build_workorder_payload(
    *,
    suggestion: WorkOrderSuggestion | None,
    pending_action: dict[str, Any],
    draft: WorkOrderDraftArtifact | None,
    evidence_bundle: EvidenceBundle | None,
    node_results: list[NodeResult] | list[dict[str, Any]],
) -> dict[str, Any]:
    if not suggestion and not pending_action and not draft:
        return {}
    suggestion_data = _as_dict(suggestion)
    draft_data = _as_dict(draft)
    node_output = _workorder_node_output(node_results)
    source_artifact_refs = _source_artifact_refs(suggestion_data, pending_action, draft_data, node_output)
    target_evidence_bundle_id = _first_text(
        node_output.get("target_evidence_bundle_id"),
        pending_action.get("source_diagnosis_artifact_id"),
        draft_data.get("source_diagnosis_artifact_id"),
        suggestion_data.get("source_diagnosis_artifact_id"),
        evidence_bundle.bundle_id if evidence_bundle else None,
    )
    stale_required = any(
        (
            _truthy(pending_action.get("stale_refresh_required")),
            _truthy(node_output.get("stale_evidence_disclosure_required")),
            _truthy(node_output.get("stale_refresh_required")),
            bool(draft_data.get("stale_warning")),
            bool(_stale_evidence(evidence_bundle)),
        )
    )
    supporting_evidence_refs = _dedupe(
        [
            *_as_text_list(node_output.get("supporting_evidence_refs")),
            *_as_text_list(pending_action.get("required_evidence")),
            *[item.evidence_id for item in (evidence_bundle.evidence_items if evidence_bundle else [])],
        ]
    )
    manual_confirmation_required = bool(
        draft_data
        or pending_action
        or suggestion_data.get("lifecycle_status") == "recommended_draft"
        or suggestion_data.get("need_workorder") is True
    )
    return {
        "workorder_suggestion": suggestion_data,
        "workorder_pending_action": pending_action,
        "workorder_draft": draft_data,
        "approval_requirements": _approval_requirements_from_node_results(node_results),
        "source_artifact_refs": source_artifact_refs,
        "target_evidence_bundle_id": target_evidence_bundle_id,
        "supporting_evidence_refs": supporting_evidence_refs,
        "evidence_freshness": _first_text(node_output.get("evidence_freshness"), "stale" if stale_required else "", "unknown"),
        "stale_evidence_disclosure_required": stale_required,
        "generated_from_previous_artifact": bool(source_artifact_refs or target_evidence_bundle_id),
        "manual_confirmation_required": manual_confirmation_required,
        "draft_only": manual_confirmation_required,
        "dispatch_forbidden": True,
    }


def composite_status(deliverables: list[DeliverableResult], fallback: str) -> str:
    if not deliverables:
        return fallback
    statuses = {item.status for item in deliverables}
    if statuses == {"completed"}:
        return "completed"
    if statuses.intersection({"completed", "partial"}):
        return "partial"
    if statuses == {"blocked"}:
        return "blocked"
    return "failed"


def _node_results_by_goal(node_results: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in node_results:
        dumped = _dump(item) or {}
        for goal_id in dumped.get("goal_ids", []) or []:
            grouped.setdefault(str(goal_id), []).append(dumped)
    return grouped


def _goal_failure_status(results: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in results}
    return "blocked" if statuses.intersection({"blocked", "skipped"}) else "failed"


def _approval_requirements_from_node_results(node_results: list[Any]) -> list[dict[str, Any]]:
    for item in node_results:
        output = (_as_dict(item).get("output") or {})
        if not isinstance(output, dict):
            continue
        raw = output.get("approval_requirements")
        if isinstance(raw, dict):
            return [dict(raw)]
        if isinstance(raw, list):
            return [dict(value) for value in raw if isinstance(value, dict)]
    return []


def _workorder_node_output(node_results: list[Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in node_results:
        result = _as_dict(item)
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        if result.get("node_type") == "workorder" or any(
            key in output
            for key in ("workorder_suggestion", "suggestion", "pending_action", "draft", "target_evidence_bundle_id", "source_artifact_refs")
        ):
            merged.update(output)
    return merged


def _source_artifact_refs(
    suggestion: dict[str, Any],
    pending_action: dict[str, Any],
    draft: dict[str, Any],
    node_output: dict[str, Any],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(artifact_id: Any, artifact_type: str) -> None:
        value = str(artifact_id or "").strip()
        key = (value, artifact_type)
        if value and key not in seen:
            seen.add(key)
            refs.append({"artifact_id": value, "artifact_type": artifact_type})

    for item in node_output.get("source_artifact_refs") or []:
        if isinstance(item, dict):
            add(item.get("artifact_id"), str(item.get("artifact_type") or "artifact"))
    for source, artifact_type in (
        (draft.get("source_report_artifact_id"), "report_artifact"),
        (suggestion.get("source_report_artifact_id"), "report_artifact"),
        (pending_action.get("source_report_artifact_id"), "report_artifact"),
        (draft.get("source_diagnosis_artifact_id"), "analysis_artifact"),
        (suggestion.get("source_diagnosis_artifact_id"), "analysis_artifact"),
        (pending_action.get("source_diagnosis_artifact_id"), "analysis_artifact"),
        (pending_action.get("recommendation_artifact_id"), "workorder_suggestion"),
        (draft.get("created_from_recommendation_artifact_id"), "workorder_suggestion"),
    ):
        add(source, artifact_type)
    return refs


def _stale_evidence(bundle: EvidenceBundle | None) -> list[Any]:
    if not bundle:
        return []
    stale_ids = {str(item) for item in bundle.quality_checks.get("stale_evidence_ids", []) or []}
    return [
        item
        for item in bundle.evidence_items
        if item.evidence_id in stale_ids
        or str(getattr(item.quality, "freshness", "") or item.metadata.get("freshness", "")).lower() == "stale"
        or item.evidence_type == "stale_evidence_disclosure"
    ]


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


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() != "none":
            return text
    return ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "y", "stale"}


def _dedupe(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))
