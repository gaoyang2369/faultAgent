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

from ..contracts import DeliverableResult, GoalExecutionResult, NodeResult, PlanGoal
from .goal_results import (
    build_goal_execution_results,
    dependency_closure,
    ordered_user_goals,
    refs_for_goal_closure,
    source_metadata,
)


_TITLE_BY_CAPABILITY = {
    "explain_fault_code": "故障码解释",
    "check_runtime_status": "运行状态",
    "compare_runtime_status": "运行比较",
    "diagnose_fault": "故障诊断",
    "resolution_recommendation": "处理建议",
    "generate_report": "运行报告",
    "create_workorder_draft": "工单草稿",
    "evaluate_workorder_need": "工单必要性判断",
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
        evidence_bundle: EvidenceBundle | None = None,
        plan_nodes: list[Any] | None = None,
        goal_statuses: list[Any] | None = None,
        goal_execution_results: list[GoalExecutionResult] | None = None,
    ) -> tuple[list[DeliverableResult], list[GoalExecutionResult]]:
        knowledge = _model(artifacts.get("knowledge_artifact"), KnowledgeStepArtifact)
        analysis = _model(artifacts.get("analysis_artifact"), AnalysisStepArtifact)
        report = _model(artifacts.get("report_artifact"), ReportStepArtifact)
        assessments_raw = artifacts.get("runtime_status_assessments")
        assessments = list(assessments_raw.values()) if isinstance(assessments_raw, dict) else []
        if not assessments and artifacts.get("runtime_status_assessment") is not None:
            assessments = [artifacts["runtime_status_assessment"]]
        comparison = _as_dict(artifacts.get("comparison_artifact"))
        sql = _as_dict(artifacts.get("sql_artifact"))
        sql_ids = [str(item) for item in artifacts.get("sql_artifact_ids", []) if str(item)]
        normalized_goals = [_strict_canonical_goal(_dump(goal) or {}) for goal in goals]
        executions = goal_execution_results or build_goal_execution_results(
            goals=normalized_goals,
            node_results=node_results,
            artifacts=artifacts,
            evidence_bundle=evidence_bundle,
            status=status,
            plan_nodes=plan_nodes,
            goal_statuses=goal_statuses,
        )
        execution_by_goal = {item.goal_id: item for item in executions}

        results: list[DeliverableResult] = []
        for goal in ordered_user_goals(normalized_goals):
            goal_id = str(goal.get("goal_id") or "")
            capability = str(goal.get("capability") or "")
            execution = execution_by_goal.get(goal_id)
            terminal_status = execution.status if execution is not None else "failed"
            dependency_ids = dependency_closure(goal_id, normalized_goals)
            dependency_failures = [
                item.goal_id
                for item in executions
                if item.goal_id in dependency_ids and item.status in {"failed", "blocked", "denied"}
            ]
            if dependency_failures and terminal_status not in {"completed", "denied"}:
                terminal_status = "blocked"
            payload, available, error_code, error_message = self._content_for_goal(
                goal=goal,
                capability=capability,
                assessments=assessments,
                comparison=comparison,
                sql=sql,
                knowledge=knowledge,
                analysis=analysis,
                report=report,
                workorder_payload=workorder_payload,
                has_bound_claims=bool(execution and execution.claim_ids),
            )
            if terminal_status == "completed" and not available:
                terminal_status = "failed"
            artifact_ids, evidence_ids, claim_ids = refs_for_goal_closure(
                goal_id,
                goals=normalized_goals,
                executions=executions,
            )
            freshness, generated_at = source_metadata(goal_id, goals=normalized_goals, artifacts=artifacts)
            denied = terminal_status == "denied"
            blocked = terminal_status == "blocked"
            results.append(
                DeliverableResult(
                    goal_id=goal_id,
                    capability=capability,
                    clause_index=int(goal.get("clause_index") or 0),
                    user_requested=True,
                    status=terminal_status,
                    title=_TITLE_BY_CAPABILITY.get(capability, "请求结果"),
                    summary=_summary(payload),
                    structured_content=payload,
                    artifact_ids=artifact_ids or (sql_ids if capability == "check_runtime_status" else []),
                    evidence_ids=evidence_ids,
                    claim_ids=claim_ids,
                    dependency_goal_ids=dependency_ids,
                    error_code=(
                        str(goal.get("drop_reason") or "capability_permission_denied")
                        if denied
                        else "dependency_goal_blocked"
                        if blocked and dependency_failures
                        else execution.error_code
                        if execution and execution.error_code
                        else error_code
                    ),
                    error_message=(
                        "当前身份无权执行该子目标。"
                        if denied
                        else f"依赖目标未完成：{'、'.join(dependency_failures)}。"
                        if blocked and dependency_failures
                        else execution.error_message
                        if execution and execution.error_message
                        else error_message
                    ),
                    blocking_goal_ids=dependency_failures or (execution.blocked_by_goal_ids if execution else []),
                    source_freshness=freshness,
                    source_generated_at=generated_at,
                )
            )
        return results, executions

    @staticmethod
    def _content_for_goal(
        *,
        goal: dict[str, Any],
        capability: str,
        assessments: list[Any],
        comparison: dict[str, Any],
        sql: dict[str, Any],
        knowledge: KnowledgeStepArtifact | None,
        analysis: AnalysisStepArtifact | None,
        report: ReportStepArtifact | None,
        workorder_payload: dict[str, Any],
        has_bound_claims: bool,
    ) -> tuple[dict[str, Any], bool, str | None, str | None]:
        if capability == "explain_fault_code":
            success = bool(knowledge and knowledge.success)
            payload = {
                "fault_codes": list(getattr(knowledge, "fault_codes", []) or []),
                "fault_code_entries": [
                    {
                        key: value
                        for key, value in (_dump(entry) or {}).items()
                        if key in {
                            "code", "title", "meaning", "cause", "remedy", "match_type",
                            "source_file", "page",
                        }
                    }
                    for entry in list(getattr(knowledge, "fault_code_entries", []) or [])
                ],
            }
            return (
                payload,
                success,
                None if success else str(getattr(knowledge, "error_code", "") or "knowledge_unavailable"),
                None if success else str(getattr(knowledge, "error", "") or "知识库未返回可靠释义。"),
            )
        if capability == "check_runtime_status":
            devices = {str(item) for item in goal.get("device_refs") or [] if str(item)}
            scoped = [item for item in assessments if not devices or str(_as_dict(item).get("device") or "") in devices]
            available = bool(scoped or sql)
            return {"assessments": [_dump(item) for item in scoped], "legacy_sql": sql}, available, None if available else "runtime_status_unavailable", None
        if capability == "compare_runtime_status":
            return comparison, bool(comparison), None if comparison else "comparison_unavailable", None
        if capability in {"diagnose_fault", "resolution_recommendation"}:
            success = bool((analysis and analysis.success) or (capability == "diagnose_fault" and has_bound_claims))
            payload = (
                _dump(analysis) or {}
                if capability == "diagnose_fault"
                else {"recommendations": list(getattr(analysis, "recommendations", []) or [])}
            )
            return payload, success, None if success else "analysis_unavailable", None
        if capability == "generate_report":
            success = bool(report and report.success)
            return _dump(report) or {}, success, None if success else "report_unavailable", None
        if capability == "create_workorder_draft":
            success = bool(
                workorder_payload.get("workorder_draft")
                or workorder_payload.get("draft")
                or workorder_payload.get("draft_id")
                or workorder_payload.get("workorder_suggestion")
            )
            return dict(workorder_payload), success, None if success else "workorder_draft_unavailable", None
        if capability == "evaluate_workorder_need":
            payload = _as_dict(workorder_payload.get("workorder_need_assessment"))
            return payload, bool(payload), None if payload else "workorder_evidence_required", None
        clarification = _as_dict(goal.get("clarification"))
        return clarification, False, "missing_required_slot", str(
            clarification.get("clarification_question") or "需要补充设备、故障码或时间窗口等关键信息后才能继续处理。"
        )


def build_workorder_payload(
    *,
    suggestion: WorkOrderSuggestion | None,
    pending_action: dict[str, Any],
    draft: WorkOrderDraftArtifact | None,
    evidence_bundle: EvidenceBundle | None,
    node_results: list[NodeResult] | list[dict[str, Any]],
) -> dict[str, Any]:
    node_output = _workorder_node_output(node_results)
    if not suggestion and not pending_action and not draft and not node_output:
        return {}
    suggestion_data = _as_dict(suggestion) or _as_dict(node_output.get("suggestion"))
    draft_data = _as_dict(draft)
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
    manual_confirmation_required = not _truthy(node_output.get("evaluation_only")) and bool(
        draft_data
        or pending_action
        or suggestion_data.get("lifecycle_status") == "recommended_draft"
        or suggestion_data.get("need_workorder") is True
    )
    return {
        "workorder_suggestion": suggestion_data,
        "workorder_need_assessment": _as_dict(node_output.get("need_assessment")),
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
    if statuses <= {"blocked", "denied", "skipped"}:
        return "blocked"
    return "failed"


def _summary(payload: dict[str, Any]) -> str | None:
    for key in ("summary", "conclusion", "message", "clarification_question"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return None


def _strict_canonical_goal(goal: dict[str, Any]) -> dict[str, Any]:
    """Reject incomplete pre-canonical fixtures instead of upgrading them at runtime."""

    missing = [field for field in ("goal_id", "capability") if not str(goal.get(field) or "").strip()]
    if missing:
        raise ValueError(f"canonical output goal missing required fields: {', '.join(missing)}")
    return goal


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
