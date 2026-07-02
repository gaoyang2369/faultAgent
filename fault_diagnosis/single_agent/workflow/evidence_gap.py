"""Artifact-driven follow-up planning for restricted single-agent workflows."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .axes import requests_action_or_workorder
from ..context import ConversationDiagnosisState, DiagnosisCase
from .contracts import TaskRoute


class EvidenceGapPlan(BaseModel):
    """Evidence reuse/refresh plan for the current route."""

    plan_mode: str = "normal"
    evidence_mode: str = "collect_new"
    relation_to_previous: str = "new_task"
    referenced_case_id: str | None = None
    referenced_artifact_id: str | None = None
    required_evidence: list[str] = Field(default_factory=list)
    satisfied_evidence: list[str] = Field(default_factory=list)
    missing_or_stale_evidence: list[str] = Field(default_factory=list)
    should_refresh_runtime_data: bool = False
    reason: str = ""


_CONTEXT_WORDS = (
    "结果",
    "从结果来看",
    "刚才报告",
    "报告里",
    "上面",
    "该故障",
    "该设备",
    "刚才",
    "上一轮",
    "上一次",
    "继续",
    "那",
    "所以",
)
_WORKORDER_DECISION_WORDS = (
    "要不要生成工单",
    "是否生成工单",
    "是不是要生成工单",
    "要不要工单",
    "是否需要工单",
    "应不应该派单",
    "要不要派单",
    "是否派人处理",
    "要不要派人",
)
_CURRENT_REFRESH_WORDS = (
    "现在还在报警吗",
    "当前还异常吗",
    "最新状态",
    "现在是否还故障",
    "现在还故障",
    "当前是否还故障",
    "现在还异常",
    "当前还报警",
)


def analyze_evidence_gap(
    route: TaskRoute,
    state: ConversationDiagnosisState | None,
) -> EvidenceGapPlan:
    """Return an artifact reuse/refresh plan for follow-up questions."""

    text = (route.user_goal or "").replace(" ", "")
    active_case = state.active_case if state is not None else None
    has_reference = _has_any(text, _CONTEXT_WORDS) or bool(route.context_resolution.get("references"))
    asks_workorder = _has_any(text, _WORKORDER_DECISION_WORDS) or requests_action_or_workorder(route)
    asks_refresh = _has_any(text, _CURRENT_REFRESH_WORDS)
    if active_case is None:
        if asks_workorder:
            return EvidenceGapPlan(
                plan_mode="new_diagnosis_then_workorder",
                evidence_mode="collect_new",
                relation_to_previous="new_task",
                required_evidence=_workorder_required_evidence(),
                missing_or_stale_evidence=["previous_diagnosis_or_report"],
                reason="没有可复用的上一轮诊断或报告 artifact。",
            )
        return EvidenceGapPlan()

    referenced_artifact_id = active_case.last_evidence_bundle_id or active_case.case_id
    has_artifact = bool(active_case.last_evidence_bundle_id or active_case.last_report_url)
    if asks_refresh and asks_workorder:
        return EvidenceGapPlan(
            plan_mode="status_refresh_then_workorder",
            evidence_mode="reuse_and_refresh_status",
            relation_to_previous="refresh_current_status",
            referenced_case_id=active_case.case_id,
            referenced_artifact_id=referenced_artifact_id,
            required_evidence=[*_workorder_required_evidence(), "latest_realtime_status"],
            satisfied_evidence=_satisfied_workorder_evidence(active_case),
            missing_or_stale_evidence=_missing_workorder_evidence(active_case, include_latest=True),
            should_refresh_runtime_data=True,
            reason="用户同时要求刷新当前状态并判断是否需要工单。",
        )
    if asks_refresh:
        return EvidenceGapPlan(
            plan_mode="refresh_current_status",
            evidence_mode="reuse_and_refresh_status",
            relation_to_previous="refresh_current_status",
            referenced_case_id=active_case.case_id,
            referenced_artifact_id=referenced_artifact_id,
            required_evidence=["latest_realtime_status"],
            satisfied_evidence=_satisfied_workorder_evidence(active_case),
            missing_or_stale_evidence=["latest_realtime_status"],
            should_refresh_runtime_data=True,
            reason="用户明确要求查看当前/最新状态。",
        )
    if asks_workorder and has_artifact:
        return EvidenceGapPlan(
            plan_mode="workorder_decision_from_artifact",
            evidence_mode="reuse_previous_artifact",
            relation_to_previous="actionize_previous_result",
            referenced_case_id=active_case.case_id,
            referenced_artifact_id=referenced_artifact_id,
            required_evidence=_workorder_required_evidence(),
            satisfied_evidence=_satisfied_workorder_evidence(active_case),
            missing_or_stale_evidence=_missing_workorder_evidence(active_case),
            should_refresh_runtime_data=False,
            reason="基于上一轮诊断/报告 artifact 判断是否建议生成待确认工单草稿。",
        )
    if has_reference and has_artifact:
        return EvidenceGapPlan(
            plan_mode="explain_from_artifact",
            evidence_mode="reuse_previous_artifact",
            relation_to_previous="continue_current_frame",
            referenced_case_id=active_case.case_id,
            referenced_artifact_id=referenced_artifact_id,
            required_evidence=["diagnosis_summary", "key_evidence"],
            satisfied_evidence=_satisfied_workorder_evidence(active_case),
            reason="用户指向上一轮结果，优先复用当前 case。",
        )
    return EvidenceGapPlan()


def _workorder_required_evidence() -> list[str]:
    return [
        "diagnosis_summary",
        "severity_or_status_level",
        "key_evidence",
        "freshness",
        "recommended_action_policy",
    ]


def _satisfied_workorder_evidence(case: DiagnosisCase) -> list[str]:
    satisfied: list[str] = []
    if case.initial_assessment or case.evidence_summary:
        satisfied.append("diagnosis_summary")
    if case.severity or case.status_level or case.priority:
        satisfied.append("severity_or_status_level")
    if case.evidence_summary or case.current_event or case.key_phenomenon:
        satisfied.append("key_evidence")
    if case.freshness_label or case.currentness or case.latest_sample_time:
        satisfied.append("freshness")
    if case.next_action or "workorder_decision" in case.available_followups:
        satisfied.append("recommended_action_policy")
    return list(dict.fromkeys(satisfied))


def _missing_workorder_evidence(case: DiagnosisCase, *, include_latest: bool = False) -> list[str]:
    missing = [item for item in _workorder_required_evidence() if item not in _satisfied_workorder_evidence(case)]
    if include_latest or _is_stale(case):
        missing.append("latest_realtime_status")
    return list(dict.fromkeys(missing))


def _is_stale(case: DiagnosisCase) -> bool:
    text = " ".join(
        str(item or "")
        for item in [case.freshness_label, case.currentness]
    )
    return any(keyword in text for keyword in ("已滞后", "滞后", "stale", "非实时", "不代表实时"))


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords if keyword)
