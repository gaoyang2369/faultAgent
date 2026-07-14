"""Pending, source, origin and deliverable architecture red baseline."""

from __future__ import annotations

from fault_diagnosis.agent.context.source_selector import GoalScopedSourceSelector
from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest, EffectiveGoal
from fault_diagnosis.agent.engine import AgentEngineV2
from fault_diagnosis.agent.output.answer import build_output_frame
from fault_diagnosis.domain.diagnosis.contracts import AnalysisStepArtifact
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.persistence.repositories.conversation_store import (
    ConversationRepository,
    SQLiteConversationRepository,
)


def _manifest(artifact_id: str, artifact_type: str, *, sources: list[str] | None = None) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        thread_id="thread.phase0.invariants",
        status="completed",
        artifact_status="complete",
        persistence_status="committed",
        readback_verified=True,
        device_refs=["G120电机1"],
        lineage=ArtifactLineage(
            lineage_status="complete",
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            subject_device_refs=["G120电机1"],
            source_artifact_ids=list(sources or []),
        ),
    )


def test_pending_clarification_repository_has_waiting_cas_and_idempotency_boundary() -> None:
    required_methods = {
        "get_waiting_clarification",
        "create_waiting_clarification",
        "compare_and_set_clarification_status",
        "get_turn_by_idempotency_key",
    }

    assert required_methods.issubset(set(dir(ConversationRepository)))
    assert required_methods.issubset(set(dir(SQLiteConversationRepository)))


def test_source_selector_reports_satisfaction_not_merely_candidate_selection() -> None:
    analysis = _manifest("analysis:exact", "analysis_artifact", sources=["sql:exact"])
    selection = GoalScopedSourceSelector().select(
        goal=EffectiveGoal(
            goal_id="goal_report",
            capability="generate_report",
            source_policy="reuse_verified_artifact",
        ),
        manifests=[analysis],
        explicit_artifact_id=analysis.artifact_id,
        expected_devices=["G120电机1"],
        thread_id=analysis.thread_id,
    )

    assert selection.status == "satisfied_by_artifact"
    assert selection.binding is not None
    assert selection.binding.artifact_id == analysis.artifact_id


def test_source_selector_does_not_recursively_guess_a_compatible_ancestor() -> None:
    report = _manifest("report:old", "report_artifact", sources=["analysis:old"])
    analysis = _manifest("analysis:old", "analysis_artifact", sources=["sql:old"])
    selection = GoalScopedSourceSelector().select(
        goal=EffectiveGoal(
            goal_id="goal_report",
            capability="generate_report",
            source_policy="reuse_verified_artifact",
        ),
        manifests=[report, analysis],
        explicit_artifact_id=report.artifact_id,
        expected_devices=["G120电机1"],
        thread_id=report.thread_id,
    )

    assert selection.status == "blocked"
    assert selection.binding is None
    assert selection.observation["selection_reason"] == "explicit_source_type_incompatible"


def test_requested_goals_record_origin_and_clause_order() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message=(
            "A07089 是什么意思？查询 G120电机1 最近一小时有没有相关异常，"
            "判断现在是否存在故障，并给出处理建议。"
        ),
        auth_context=build_auth_context(user_id="admin-phase0", role="admin", session_id="session-phase0"),
    )
    goals = snapshot.effective_request_frame.requested_goal_set.goals

    assert [getattr(goal, "origin", None) for goal in goals] == ["explicit"] * 4
    assert [getattr(goal, "clause_index", None) for goal in goals] == [0, 1, 2, 3]


def test_dependency_goal_has_terminal_state_but_no_user_deliverable() -> None:
    goals = [
        {
            "goal_id": "goal_user_diagnosis",
            "capability": "diagnose_fault",
            "origin": "explicit",
            "user_requested": True,
            "requested_deliverables": ["diagnosis"],
        },
        {
            "goal_id": "goal_dependency_status",
            "capability": "check_runtime_status",
            "origin": "dependency",
            "user_requested": False,
            "requested_deliverables": ["runtime_status"],
        },
    ]
    frame = build_output_frame(
        goals=goals,
        artifacts={"analysis_artifact": AnalysisStepArtifact(success=True, conclusion="存在异常迹象")},
    )

    assert [item.goal_id for item in frame.composite_output.deliverables] == ["goal_user_diagnosis"]

