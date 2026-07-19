"""Deterministic Phase 4 output regressions shared by pytest and the eval runner."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fault_diagnosis.agent import AgentEngineV2, PlanGoal
from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator
from fault_diagnosis.agent.output.answer import build_output_frame
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    EvidenceBundle,
    EvidenceItem,
    FaultCodeEntry,
    KnowledgeStepArtifact,
    ReportStepArtifact,
)
from fault_diagnosis.domain.diagnosis.runtime_status import DataBasis, RuntimeStatusAssessment
from fault_diagnosis.domain.canonical_turn import CanonicalGoal, TurnCommand
from fault_diagnosis.domain.canonical_turn.contracts import GoalProvenance
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.persistence.repositories.pending_clarification_repository import (
    MemoryPendingClarificationRepository,
)


def goal(goal_id: str, capability: str, index: int, **updates: Any) -> PlanGoal:
    kind = {
        "explain_fault_code": "fault_code_explanation",
        "check_runtime_status": "runtime_status",
        "compare_runtime_status": "runtime_comparison",
        "diagnose_fault": "diagnosis",
        "resolution_recommendation": "recommendations",
        "generate_report": "report",
        "create_workorder_draft": "workorder_draft",
    }[capability]
    values = {
        "goal_id": goal_id,
        "capability": capability,
        "clause_index": index,
        "requested_deliverables": [kind],
        "origin": "explicit",
        "user_requested": True,
        "user_visible": True,
    }
    values.update(updates)
    return PlanGoal(**values)


def artifacts() -> dict[str, Any]:
    return {
        "knowledge_artifact": KnowledgeStepArtifact(
            success=True,
            query="A07089",
            fault_codes=["A07089"],
            fault_code_entries=[FaultCodeEntry(code="A07089", meaning="速度偏差")],
        ),
        "runtime_status_assessment": RuntimeStatusAssessment(
            device="G120电机1",
            query_status="success",
            runtime_status="attention",
            data_basis=DataBasis(
                resolution_mode="latest_available_fallback",
                latest_sample_time=datetime.now() - timedelta(hours=1),
                usable_for_status=True,
                usable_for_diagnosis=True,
                usable_for_report=True,
            ),
            sample_count=12,
        ),
        "analysis_artifact": AnalysisStepArtifact(
            success=True,
            conclusion="存在速度偏差迹象",
            recommendations=["检查编码器"],
        ),
    }


def run_regressions() -> dict[str, Any]:
    results: dict[str, Any] = {}

    report = build_output_frame(
        goals=[goal("g_report", "generate_report", 0)],
        artifacts={
            "report_artifact": {
                **ReportStepArtifact(success=True, report_filename="phase4.html").model_dump(mode="json"),
                "freshness": "recent",
                "generated_at": "2026-07-14T10:00:00+08:00",
            }
        },
    )
    report_item = report.composite_output.deliverables[0]
    results["case_a"] = {
        "variant": report.answer_variant,
        "goal_ids": [item.goal_id for item in report.composite_output.deliverables],
        "freshness": report_item.source_freshness,
        "generated_at": report_item.source_generated_at,
    }

    four_goals = [
        goal("g_explain", "explain_fault_code", 0),
        goal("g_status", "check_runtime_status", 1),
        goal("g_diagnose", "diagnose_fault", 2),
        goal("g_recommend", "resolution_recommendation", 3),
    ]
    composite = build_output_frame(goals=four_goals, artifacts=artifacts())
    results["case_b"] = {
        "variant": composite.answer_variant,
        "goal_ids": [item.goal_id for item in composite.composite_output.deliverables],
        "capabilities": composite.guardrail_result["output_observation"]["executed_capabilities"],
    }

    resumed = _resume_pending("我说的是 G120电机2")
    mixed = _resume_pending("是 G120电机2，顺便生成报告")
    results["case_c"] = {
        "resumed_goal_ids": [item.goal_id for item in resumed.request.goals],
        "resumed_capabilities": [item.capability for item in resumed.request.goals if item.user_requested],
        "mixed_capabilities": [item.capability for item in mixed.request.goals if item.user_requested],
    }

    shorthand: dict[str, str] = {}
    auth = build_auth_context(role="admin")
    for message in ("A07089 是什么意思", "查询 A07089", "A07089", "详细解释一下 A07089"):
        snapshot = AgentEngineV2().build_plan_snapshot(raw_message=message, auth_context=auth)
        frame = build_output_frame(goals=snapshot.execution_plan.goals, artifacts=artifacts())
        shorthand[message] = frame.answer_variant
    results["case_d"] = shorthand

    dependency_goals = [
        goal("g_user", "diagnose_fault", 0, depends_on_goal_ids=["g_dependency"]),
        goal(
            "g_dependency",
            "check_runtime_status",
            0,
            origin="dependency",
            user_requested=False,
            user_visible=False,
        ),
    ]
    dependency = build_output_frame(goals=dependency_goals, artifacts=artifacts())
    results["dependency_hidden"] = {
        "deliverable_goal_ids": [item.goal_id for item in dependency.composite_output.deliverables],
        "execution_goal_ids": [item.goal_id for item in dependency.goal_execution_results],
    }

    satisfied_goal = goal(
        "g_satisfied",
        "check_runtime_status",
        0,
        readiness_status="satisfied_by_artifact",
        source_resolution_status="satisfied_by_artifact",
        source_artifact_id="sql:historical",
        source_artifact_type="sql_artifact",
        source_freshness="recent",
    )
    satisfied = build_output_frame(goals=[satisfied_goal], artifacts=artifacts())
    satisfied_observation = satisfied.guardrail_result["output_observation"]
    results["satisfied_by_artifact"] = {
        "status": satisfied.composite_output.deliverables[0].status,
        "artifact_ids": satisfied.composite_output.deliverables[0].artifact_ids,
        "executed_goal_ids": satisfied_observation["executed_goal_ids"],
        "completed_goal_ids": satisfied_observation["completed_goal_ids"],
    }

    partial_artifacts = artifacts()
    partial_artifacts["knowledge_artifact"] = KnowledgeStepArtifact(
        success=False,
        query="A07089",
        fault_codes=["A07089"],
        error="知识库不可用",
        error_code="kb_timeout",
    )
    partial = build_output_frame(goals=four_goals, artifacts=partial_artifacts)
    results["partial_failure"] = {
        item.goal_id: item.status for item in partial.composite_output.deliverables
    }

    sql_failure_goals = [
        goal("g_explain", "explain_fault_code", 0),
        goal("g_status", "check_runtime_status", 1),
        goal("g_diagnose", "diagnose_fault", 2, depends_on_goal_ids=["g_status"]),
        goal("g_recommend", "resolution_recommendation", 3, depends_on_goal_ids=["g_diagnose"]),
    ]
    sql_failure = build_output_frame(
        goals=sql_failure_goals,
        artifacts={"knowledge_artifact": artifacts()["knowledge_artifact"]},
    )
    results["sql_failure"] = {
        item.goal_id: item.status for item in sql_failure.composite_output.deliverables
    }

    denied_goals = [
        goal("g_explain", "explain_fault_code", 0),
        goal(
            "g_report",
            "generate_report",
            1,
            authorization_status="denied",
            drop_reason="capability_permission_denied",
        ),
    ]
    denied = build_output_frame(goals=denied_goals, artifacts=artifacts())
    results["permission"] = {
        "variant": denied.answer_variant,
        "statuses": {item.goal_id: item.status for item in denied.composite_output.deliverables},
    }

    missing_report = build_output_frame(
        goals=[
            goal("g_explain", "explain_fault_code", 0),
            goal(
                "g_report",
                "generate_report",
                1,
                readiness_status="blocked_source",
                source_resolution_status="unresolved",
            ),
        ],
        artifacts={"knowledge_artifact": artifacts()["knowledge_artifact"]},
    )
    results["missing_report_source"] = {
        "statuses": {item.goal_id: item.status for item in missing_report.composite_output.deliverables},
        "executed_goal_ids": missing_report.guardrail_result["output_observation"]["executed_goal_ids"],
    }

    denied_workorder = build_output_frame(
        goals=[
            goal("g_explain", "explain_fault_code", 0),
            goal(
                "g_workorder",
                "create_workorder_draft",
                1,
                authorization_status="denied",
                drop_reason="capability_permission_denied",
            ),
        ],
        artifacts={"knowledge_artifact": artifacts()["knowledge_artifact"]},
    )
    results["workorder_denied"] = {
        item.goal_id: item.status for item in denied_workorder.composite_output.deliverables
    }

    all_blocked = build_output_frame(
        goals=[
            goal("g_report", "generate_report", 0, readiness_status="blocked_source"),
            goal(
                "g_workorder",
                "create_workorder_draft",
                1,
                authorization_status="denied",
                drop_reason="capability_permission_denied",
            ),
        ],
    )
    results["all_blocked"] = {
        "variant": all_blocked.answer_variant,
        "statuses": {item.goal_id: item.status for item in all_blocked.composite_output.deliverables},
    }

    bundle = EvidenceBundle(
        bundle_id="bundle.phase4",
        trace_id="trace.phase4",
        evidence_items=[
            EvidenceItem(evidence_id="ev_explain", summary="手册证据", goal_ids=["g_explain"]),
            EvidenceItem(evidence_id="ev_status", summary="SQL 证据", goal_ids=["g_status"]),
            EvidenceItem(evidence_id="ev_unbound", summary="未绑定证据"),
        ],
    )
    isolated = build_output_frame(goals=four_goals[:2], artifacts=artifacts(), evidence_bundle=bundle)
    results["evidence_isolation"] = {
        item.goal_id: item.evidence_ids for item in isolated.composite_output.deliverables
    }
    return results


def assert_regressions(results: dict[str, Any]) -> None:
    assert results["case_a"] == {
        "variant": "report_answer",
        "goal_ids": ["g_report"],
        "freshness": "recent",
        "generated_at": "2026-07-14T10:00:00+08:00",
    }
    assert results["case_b"] == {
        "variant": "composite_answer",
        "goal_ids": ["g_explain", "g_status", "g_diagnose", "g_recommend"],
        "capabilities": [
            "diagnose_fault",
            "resolution_recommendation",
        ],
    }
    assert results["case_c"] == {
        "resumed_goal_ids": ["goal_pending_diagnose"],
        "resumed_capabilities": ["diagnose_fault"],
        "mixed_capabilities": ["diagnose_fault", "generate_report"],
    }
    assert set(results["case_d"].values()) == {"fault_code_answer"}
    assert results["dependency_hidden"] == {
        "deliverable_goal_ids": ["g_user"],
        "execution_goal_ids": ["g_user", "g_dependency"],
    }
    assert results["satisfied_by_artifact"] == {
        "status": "partial",
        "artifact_ids": ["sql:historical"],
        "executed_goal_ids": [],
        "completed_goal_ids": [],
    }
    assert results["partial_failure"] == {
        "g_explain": "failed",
        "g_status": "partial",
        "g_diagnose": "completed",
        "g_recommend": "completed",
    }
    assert results["sql_failure"] == {
        "g_explain": "partial",
        "g_status": "failed",
        "g_diagnose": "blocked",
        "g_recommend": "blocked",
    }
    assert results["permission"] == {
        "variant": "composite_answer",
        "statuses": {"g_explain": "partial", "g_report": "denied"},
    }
    assert results["missing_report_source"] == {
        "statuses": {"g_explain": "partial", "g_report": "blocked"},
        "executed_goal_ids": [],
    }
    assert results["workorder_denied"] == {"g_explain": "completed", "g_workorder": "denied"}
    assert results["all_blocked"] == {
        "variant": "composite_answer",
        "statuses": {"g_report": "blocked", "g_workorder": "denied"},
    }
    assert results["evidence_isolation"] == {
        "g_explain": ["ev_explain"],
        "g_status": ["ev_status"],
    }


def _resume_pending(message: str):
    repository = MemoryPendingClarificationRepository()
    pending_goal = CanonicalGoal(
        goal_id="goal_pending_diagnose",
        capability="diagnose_fault",
        origin="explicit",
        user_requested=True,
        user_visible=True,
        clause_index=0,
        required_slots=["device"],
        resolved_slots={},
        missing_slots=["device"],
        provenance=GoalProvenance(parser_source="deterministic", utterance_span=(0, 8)),
    )
    repository.create_waiting(
        pending_id="pending-diagnose-device",
        thread_id="thread.phase4.pending",
        user_id="admin-phase4",
        created_turn_id="turn-original",
        created_message_id="message-original",
        idempotency_key="pending-create",
        original_goals=[pending_goal],
        missing_slots=["device"],
        candidate_values={"device": ["G120电机1", "G120电机2"]},
    )
    coordinator = ConversationTurnCoordinator(pending_repository=repository)
    return coordinator.preview_turn(
        TurnCommand(
            command="preview",
            thread_id="thread.phase4.pending",
            user_id="admin-phase4",
            turn_id=f"turn-{message}",
            message_id=f"message-{message}",
            idempotency_key=f"key-{message}",
            raw_message=message,
        ),
        auth_context=build_auth_context(user_id="admin-phase4", role="admin"),
    )
