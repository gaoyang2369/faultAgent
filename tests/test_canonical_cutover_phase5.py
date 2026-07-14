from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from fault_diagnosis.agent import (
    AgentEngineV2,
    DeliverableResult,
    ExecutionPlan,
    LegacyDeliverableProjection,
    PlanGoal,
    WorkflowRuntimeExecutor,
)
from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator
from fault_diagnosis.agent.output import build_output_frame, project_legacy_deliverable, serialize_composite_output
from fault_diagnosis.agent.planning import (
    CANONICAL_PLAN_VERSION,
    HISTORICAL_CANONICAL_PLAN_VERSIONS,
    resolve_plan_version,
)
from fault_diagnosis.domain.canonical_turn import TurnCommand
from fault_diagnosis.domain.diagnosis.contracts import SqlStepArtifact
from fault_diagnosis.domain.security.permissions import build_auth_context


ROOT = Path(__file__).resolve().parents[1]


def _goal() -> PlanGoal:
    return PlanGoal(
        goal_id="goal_status",
        capability="check_runtime_status",
        requested_deliverables=["runtime_status"],
        origin="explicit",
        user_requested=True,
        user_visible=True,
    )


def test_cleanup_inventory_is_machine_readable_and_has_no_unconfirmed_items() -> None:
    inventory = json.loads((ROOT / "scripts" / "canonical_cleanup_inventory.json").read_text(encoding="utf-8"))

    assert inventory["schema_version"] == "canonical_cleanup_inventory.v1"
    assert inventory["unconfirmed_items"] == []
    assert inventory["items"]
    assert all(
        set(item) >= {
            "path",
            "symbol",
            "line",
            "category",
            "reason",
            "public_contract",
            "persisted_data_impact",
            "planned_action",
        }
        for item in inventory["items"]
    )


def test_requested_variant_is_not_an_output_parameter_and_canonical_variant_is_stable() -> None:
    assert "requested_variant" not in inspect.signature(build_output_frame).parameters
    kwargs = {
        "status": "completed",
        "artifacts": {"sql_artifact": SqlStepArtifact(success=True, summary="ok")},
        "goals": [_goal()],
    }
    before = build_output_frame(**kwargs)
    compatibility_input = {**kwargs, "requested_variant": "report_answer"}
    compatibility_input.pop("requested_variant")
    after = build_output_frame(**compatibility_input)

    assert after.answer_variant == before.answer_variant == "runtime_status_answer"
    assert after.final_answer == before.final_answer


def test_deliverable_legacy_aliases_exist_only_in_one_way_boundary_projection() -> None:
    item = DeliverableResult(
        goal_id="goal_status",
        capability="check_runtime_status",
        status="completed",
        structured_content={"assessments": []},
        artifact_ids=["sql:1"],
    )
    projection = project_legacy_deliverable(item)

    assert projection == LegacyDeliverableProjection(
        deliverable_type="runtime_status",
        payload={"assessments": []},
        source_artifact_ids=["sql:1"],
    )
    assert projection.compatibility_only is True
    assert {"deliverable_type", "payload", "source_artifact_ids"}.isdisjoint(DeliverableResult.model_fields)
    with pytest.raises(ValueError):
        DeliverableResult(
            goal_id="goal_status",
            capability="check_runtime_status",
            status="completed",
            payload={"legacy": True},
        )

    frame = build_output_frame(
        artifacts={"sql_artifact": SqlStepArtifact(success=True, summary="ok")},
        goals=[_goal()],
    )
    serialized = serialize_composite_output(frame.composite_output)
    public_item = serialized["deliverables"][0]
    assert public_item["structured_content"] == public_item["payload"]
    assert public_item["artifact_ids"] == public_item["source_artifact_ids"]
    assert public_item["compatibility_only"] is True


def test_precanonical_goal_fixture_fails_before_runtime_node_execution() -> None:
    with pytest.raises(ValueError, match="canonical output goal missing required fields"):
        build_output_frame(
            goals=[PlanGoal(goal="old fixture", requested_deliverables=["runtime_status"])],
            artifacts={"sql_artifact": SqlStepArtifact(success=True, summary="ok")},
        )

    result = WorkflowRuntimeExecutor().execute(
        ExecutionPlan(
            plan_id="plan.old.fixture",
            plan_version=CANONICAL_PLAN_VERSION,
            goals=[PlanGoal(goal="old fixture", requested_deliverables=["runtime_status"])],
            nodes=[{"node_id": "sql_1", "node_type": "sql"}],
        )
    )
    assert result.status == "blocked"
    assert result.node_results == []
    assert result.trace["errors"][0]["code"] == "invalid_canonical_goal"


def test_stable_plan_version_write_and_historical_read_compatibility() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="查询 G120电机1 最近运行状态",
        auth_context=build_auth_context(role="admin"),
    )
    assert snapshot.execution_plan.plan_version == CANONICAL_PLAN_VERSION

    for historical in HISTORICAL_CANONICAL_PLAN_VERSIONS:
        resolution = resolve_plan_version(historical)
        assert resolution.read_compatible is True
        assert resolution.executable is True
        assert resolution.normalized_version == CANONICAL_PLAN_VERSION
        assert resolution.compatibility_mode == "historical_read"
        result = WorkflowRuntimeExecutor().execute(
            ExecutionPlan(plan_id=f"history:{historical}", plan_version=historical)
        )
        assert result.status == "completed"
        assert result.trace["plan_version"] == historical
        assert result.trace["normalized_plan_version"] == CANONICAL_PLAN_VERSION
        assert result.trace["plan_version_mode"] == "historical_read"

    unknown = WorkflowRuntimeExecutor().execute(
        ExecutionPlan(plan_id="unknown", plan_version="v2.canonical-phase99.validated")
    )
    assert unknown.status == "blocked"
    assert unknown.node_results == []
    assert unknown.trace["plan_version_mode"] == "unknown_read_only"
    assert unknown.trace["normalized_plan_version"] == ""


def test_preview_and_execute_snapshot_sources_preserve_the_same_canonical_request() -> None:
    auth = build_auth_context(user_id="phase5", role="admin")
    command = TurnCommand(
        command="preview",
        thread_id="thread.phase5.preview",
        user_id="phase5",
        turn_id="turn.phase5.preview",
        message_id="message.phase5.preview",
        idempotency_key="phase5-preview",
        raw_message="诊断 G120电机1 A07089 并生成报告",
    )
    canonical = ConversationTurnCoordinator().preview_turn(command, auth_context=auth)
    preview = AgentEngineV2().build_plan_snapshot(
        raw_message=command.raw_message,
        thread_id=command.thread_id,
        request_id=command.turn_id,
        auth_context=auth,
        metadata={"source": "chat_plan"},
        canonical_result=canonical,
    )
    execute = AgentEngineV2().build_plan_snapshot(
        raw_message=command.raw_message,
        thread_id=command.thread_id,
        request_id=command.turn_id,
        auth_context=auth,
        metadata={"source": "canonical_execute"},
        canonical_result=canonical,
    )

    assert preview.metadata["canonical_request"] == execute.metadata["canonical_request"]
    assert preview.execution_plan.goals == execute.execution_plan.goals
    assert preview.execution_plan.nodes == execute.execution_plan.nodes
