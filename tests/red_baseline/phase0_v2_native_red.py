"""Phase 0 V2-native red baseline.

This file intentionally does not match ``pytest.ini::python_files``.  Run it
explicitly.  Every failing assertion exercises an existing V2 object; no Phase
1 contract is imported or mocked.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest, PlanGoal
from fault_diagnosis.agent.engine import AgentEngineV2
from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator
from fault_diagnosis.domain.canonical_turn import CanonicalGoal, TurnCommand
from fault_diagnosis.domain.canonical_turn.contracts import GoalProvenance
from fault_diagnosis.agent.output.answer import build_output_frame
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    FaultCodeEntry,
    KnowledgeStepArtifact,
)
from fault_diagnosis.domain.diagnosis.runtime_status import DataBasis, RuntimeStatusAssessment
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.persistence.repositories.pending_clarification_repository import MemoryPendingClarificationRepository


THREAD_ID = "thread.phase0.red"
ANALYSIS_ID = "analysis:previous:g120-1"
COMPOSITE_MESSAGE = (
    "A07089 是什么意思？查询 G120电机1 最近一小时有没有相关异常，"
    "判断现在是否存在故障，并给出处理建议。"
)


def _admin():
    return build_auth_context(user_id="admin-phase0", role="admin", session_id="session-phase0")


def _manifest(
    artifact_id: str,
    artifact_type: str,
    devices: list[str],
    *,
    sources: list[str] | None = None,
) -> dict:
    return ArtifactManifest(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        thread_id=THREAD_ID,
        status="completed",
        artifact_status="complete",
        persistence_status="committed",
        readback_verified=True,
        followupable=True,
        reportable=True,
        device_refs=devices,
        owner_user_id="admin-phase0",
        owner_session_id="session-phase0",
        source_table="real_data_01",
        evidence_refs=[f"evidence:{artifact_id}"],
        freshness="fresh",
        report_input_snapshot_schema_version=(
            "report_input_snapshot.v1" if artifact_type == "analysis_artifact" else ""
        ),
        report_tabular_source_sql_artifact_id=(
            (sources or [""])[0] if artifact_type == "analysis_artifact" else ""
        ),
        lineage=ArtifactLineage(
            lineage_status="complete",
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            subject_device_refs=devices,
            source_artifact_ids=list(sources or []),
            source_tables=["real_data_01"],
            time_windows=[{"start": "2026-07-14T09:00:00", "end": "2026-07-14T10:00:00"}],
            created_from_goal_ids=["goal_previous"],
        ),
    ).model_dump(mode="json")


def _analysis_context() -> dict:
    return {
        "artifact_manifests": [
            _manifest(
                ANALYSIS_ID,
                "analysis_artifact",
                ["G120电机1"],
                sources=["sql:previous:g120-1"],
            )
        ]
    }


def _comparison_context(*, pending: dict | None = None) -> dict:
    context = {
        "artifact_manifests": [
            _manifest(
                "comparison:previous",
                "comparison_artifact",
                ["G120电机1", "G120电机2"],
                sources=["sql:previous:1", "sql:previous:2"],
            ),
            _manifest("sql:previous:1", "sql_artifact", ["G120电机1"]),
            _manifest("sql:previous:2", "sql_artifact", ["G120电机2"]),
        ]
    }
    if pending is not None:
        context["pending_clarification"] = pending
    return context


def _pending_diagnosis() -> dict:
    return {
        "pending_request_id": "pending-diagnose-device",
        "thread_id": THREAD_ID,
        "auth_user_id": "admin-phase0",
        "status": "waiting",
        "version": 1,
        "unresolved_slot": "device",
        "candidate_values": ["G120电机1", "G120电机2"],
        "original_goals": [
            {
                "goal_id": "goal_pending_diagnose",
                "capability": "diagnose_fault",
                "origin": "explicit",
                "required_slots": ["device"],
                "missing_slots": ["device"],
            }
        ],
    }


def _pending_coordinator() -> ConversationTurnCoordinator:
    repository = MemoryPendingClarificationRepository()
    goal = CanonicalGoal(
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
        thread_id=THREAD_ID,
        user_id="admin-phase0",
        created_turn_id="turn-original",
        created_message_id="message-original",
        idempotency_key="pending-create",
        original_goals=[goal],
        missing_slots=["device"],
        candidate_values={"device": ["G120电机1", "G120电机2"]},
    )
    return ConversationTurnCoordinator(pending_repository=repository)


def _pending_command(message: str) -> TurnCommand:
    return TurnCommand(
        command="preview",
        thread_id=THREAD_ID,
        user_id="admin-phase0",
        turn_id=f"turn-{message}",
        message_id=f"message-{message}",
        idempotency_key=f"key-{message}",
        raw_message=message,
    )


def _composite_goals() -> list[PlanGoal]:
    return [
        PlanGoal(goal_id="g_explain", capability="explain_fault_code", requested_deliverables=["fault_code_explanation"]),
        PlanGoal(goal_id="g_status", capability="check_runtime_status", requested_deliverables=["runtime_status"]),
        PlanGoal(goal_id="g_diagnose", capability="diagnose_fault", requested_deliverables=["diagnosis"]),
        PlanGoal(goal_id="g_recommend", capability="resolution_recommendation", requested_deliverables=["recommendations"]),
    ]


def _composite_artifacts() -> dict:
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


def test_case_a_source_phrase_is_not_a_new_diagnosis_goal() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="基于刚才的诊断结果生成运行报告。",
        thread_id=THREAD_ID,
        auth_context=_admin(),
        conversation_context=_analysis_context(),
    )

    assert snapshot.effective_request_frame.requested_goals == ["generate_report"]


def test_case_a_reuses_exact_analysis_without_sql_or_analysis_nodes() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="基于刚才的诊断结果生成运行报告。",
        thread_id=THREAD_ID,
        auth_context=_admin(),
        conversation_context=_analysis_context(),
    )

    assert [node.node_type for node in snapshot.execution_plan.nodes] == ["report"]
    report = snapshot.execution_plan.nodes[0]
    binding = next(item for item in report.inputs["artifact_role_bindings"] if item["role"] == "report_source")
    assert binding["artifact_id"] == ANALYSIS_ID


def test_case_a_report_input_discloses_inherited_source_freshness() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="基于刚才的诊断结果生成运行报告。",
        thread_id=THREAD_ID,
        auth_context=_admin(),
        conversation_context=_analysis_context(),
    )
    report = next(node for node in snapshot.execution_plan.nodes if node.node_type == "report")

    assert report.inputs.get("source_freshness") == "fresh"


def test_case_b_preserves_user_clause_goal_order() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(raw_message=COMPOSITE_MESSAGE, auth_context=_admin())

    assert snapshot.effective_request_frame.requested_goals == [
        "explain_fault_code",
        "check_runtime_status",
        "diagnose_fault",
        "resolution_recommendation",
    ]


def test_case_b_multi_goal_compatibility_projection_is_composite() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(raw_message=COMPOSITE_MESSAGE, auth_context=_admin())

    assert snapshot.execution_plan.execution_capability == "composite"
    assert snapshot.metadata.get("compatibility_only") is True


def test_case_b_output_uses_composite_variant_and_execution_observation() -> None:
    frame = build_output_frame(goals=_composite_goals(), artifacts=_composite_artifacts())
    observation = frame.guardrail_result["output_observation"]

    assert frame.answer_variant == "composite_answer"
    assert observation["executed_goal_ids"] == [goal.goal_id for goal in _composite_goals()]
    assert observation["executed_capabilities"] == [goal.capability for goal in _composite_goals()]


def test_case_b_one_failed_goal_does_not_delete_independent_deliverables() -> None:
    frame = build_output_frame(
        goals=_composite_goals(),
        artifacts={"knowledge_artifact": _composite_artifacts()["knowledge_artifact"]},
    )

    assert [item.goal_id for item in frame.composite_output.deliverables] == [
        "g_explain",
        "g_status",
        "g_diagnose",
        "g_recommend",
    ]
    assert frame.composite_output.deliverables[0].status == "completed"


def test_case_c_ambiguous_pronoun_creates_goal_scoped_pending() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="它现在有没有故障？",
        thread_id=THREAD_ID,
        auth_context=_admin(),
        conversation_context=_comparison_context(),
    )

    assert snapshot.effective_request_frame.needs_clarification is True
    assert snapshot.effective_request_frame.ambiguity["unresolved_slot"] == "device"
    assert snapshot.effective_request_frame.ambiguity["original_goals"][0]["capability"] == "diagnose_fault"
    assert snapshot.effective_request_frame.ambiguity["candidate_values"] == ["G120电机1", "G120电机2"]


def test_case_c_slot_only_reply_restores_original_diagnosis_goal() -> None:
    result = _pending_coordinator().preview_turn(_pending_command("是电机2。"), auth_context=_admin())

    assert [goal.capability for goal in result.request.goals] == ["diagnose_fault"]
    assert result.request.goals[0].resolved_slots == {"device": "G120电机2"}
    assert result.request.goals[0].goal_id == "goal_pending_diagnose"
    assert result.pending_transition.action == "resume_and_consume"


def test_case_c_mixed_reply_restores_pending_and_adds_explicit_report_goal() -> None:
    result = _pending_coordinator().preview_turn(_pending_command("是电机2，顺便生成报告。"), auth_context=_admin())

    assert [goal.capability for goal in result.request.goals] == ["diagnose_fault", "generate_report"]
    assert result.request.goals[0].resolved_slots == {"device": "G120电机2"}
    assert result.request.pending_binding.kind == "mixed"


@pytest.mark.parametrize(
    "message",
    ["A07089 是什么意思？", "查询 A07089", "A07089", "详细解释一下 A07089"],
)
def test_case_d_fault_code_explanation_never_requires_device(message: str) -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(raw_message=message, auth_context=_admin())

    assert snapshot.effective_request_frame.requested_goals == ["explain_fault_code"]
    goal = snapshot.effective_request_frame.requested_goal_set.goals[0]
    assert goal.required_slots == ["fault_code"]
    assert snapshot.effective_request_frame.needs_clarification is False


def test_case_d_missing_device_goal_does_not_block_explicit_explanation() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="解释 A07089，并诊断是否存在故障。",
        auth_context=_admin(),
    )

    assert "explain_fault_code" in snapshot.effective_request_frame.requested_goals
    assert any(node.node_type == "rag" for node in snapshot.execution_plan.nodes)
    diagnosis_goal = next(
        goal for goal in snapshot.metadata["canonical_request"]["goals"] if goal["capability"] == "diagnose_fault"
    )
    assert any(item.get("goal_id") == diagnosis_goal["goal_id"] for item in snapshot.effective_request_frame.clarification_reasons)
