from __future__ import annotations

import pytest

from fault_diagnosis.agent import AgentEngineV2, ExecutionPlan, WorkflowRuntimeExecutor
from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator, CurrentUtteranceParser
from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest
from fault_diagnosis.agent.planning import CANONICAL_PLAN_VERSION
from fault_diagnosis.agent.runtime import NodeExecutionOutput
from fault_diagnosis.agent.runtime.nodes.workorder import WorkorderNode
from fault_diagnosis.domain.canonical_turn import TurnCommand
from fault_diagnosis.domain.security.permissions import build_auth_context


def _command(text: str) -> TurnCommand:
    return TurnCommand(
        command="preview",
        thread_id="thread.phase2r",
        user_id="admin.phase2r",
        turn_id=f"turn:{text}",
        message_id=f"message:{text}",
        idempotency_key=f"key:{text}",
        raw_message=text,
    )


@pytest.mark.parametrize(
    ("text", "capability"),
    [
        ("要不要生成工单？", "evaluate_workorder_need"),
        ("是否建议报修？", "evaluate_workorder_need"),
        ("看起来有问题，需要生成工单吗？", "evaluate_workorder_need"),
        ("生成一份工单草稿。", "create_workorder_draft"),
        ("正式派发工单。", "dispatch_workorder"),
        ("直接安排工程师处理。", "dispatch_workorder"),
    ],
)
def test_workorder_capabilities_are_formally_distinct(text: str, capability: str) -> None:
    parsed = CurrentUtteranceParser().parse(text)
    assert [item.action.capability for item in parsed.clauses if item.action] == [capability]


@pytest.mark.parametrize(
    "text",
    [
        "不要生成工单，只判断是否有必要。",
        "先别生成报告，只告诉我是否异常。",
        "不是要派单，我只是问问。",
    ],
)
def test_negated_actions_remain_auditable_but_never_become_goals(text: str) -> None:
    result = ConversationTurnCoordinator().preview_turn(
        _command(text), auth_context=build_auth_context(role="admin")
    )
    negated = [item for item in result.request.current_parse.clauses if item.modality.negated]
    assert negated
    assert all(item.modality.requested is False for item in negated)
    assert not {item.action.capability for item in negated if item.action}.intersection(
        goal.capability for goal in result.request.goals
    )


def test_dispatch_is_denied_without_draft_or_dispatch_runtime_node() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="正式派发工单。", auth_context=build_auth_context(role="admin")
    )
    assert snapshot.metadata["canonical_request"]["goals"][0]["capability"] == "dispatch_workorder"
    assert snapshot.metadata["goal_authorization"][0]["reason_code"] == "unsupported_high_risk_action"
    assert snapshot.execution_plan.nodes == []


def test_sequence_and_parallel_relations_project_to_goal_dependencies() -> None:
    ordered = ConversationTurnCoordinator().preview_turn(
        _command("先查 J1 状态，再诊断，最后生成报告。"),
        auth_context=build_auth_context(role="admin"),
    ).request.goals
    assert [item.capability for item in ordered] == ["check_runtime_status", "diagnose_fault", "generate_report"]
    assert ordered[1].dependencies == [ordered[0].goal_id]
    assert ordered[2].dependencies == [ordered[1].goal_id]

    parallel = ConversationTurnCoordinator().preview_turn(
        _command("查询 G120电机1 和 G120电机2 的状态。"),
        auth_context=build_auth_context(role="admin"),
    ).request.goals
    assert len(parallel) == 1
    assert parallel[0].dependencies == []


def test_condition_enters_goal_and_compiled_node_gate() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="先查 J1 状态，如果异常，再生成报告。",
        auth_context=build_auth_context(role="admin"),
    )
    report = next(
        item for item in snapshot.metadata["canonical_request"]["goals"]
        if item["capability"] == "generate_report"
    )
    assert report["execution_condition"]["predicate"] == "diagnosis_is_abnormal"
    report_node = next(item for item in snapshot.execution_plan.nodes if item.node_type == "report")
    assert report_node.condition["predicate"] == "diagnosis_is_abnormal"


def test_evaluate_workorder_reuses_read_only_workorder_node_without_approval() -> None:
    manifest = ArtifactManifest(
        artifact_id="report-phase2r",
        artifact_type="report_artifact",
        thread_id="thread.phase2r",
        status="completed",
        artifact_status="complete",
        persistence_status="committed",
        readback_verified=True,
        device_refs=["J1号机"],
        lineage=ArtifactLineage(
            lineage_status="complete",
            artifact_id="report-phase2r",
            artifact_type="report_artifact",
            subject_device_refs=["J1号机"],
        ),
    )
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="根据刚才报告判断 J1 是否需要工单。",
        thread_id="thread.phase2r",
        auth_context=build_auth_context(role="admin"),
        conversation_context={"artifact_manifests": [manifest.model_dump(mode="json")]},
    )
    assert [item.node_type for item in snapshot.execution_plan.nodes] == ["workorder"]
    assert snapshot.execution_plan.nodes[0].inputs["create_draft"] is False
    assert snapshot.execution_plan.approval_requirements == []


def test_evaluate_workorder_without_evidence_returns_insufficient_evidence() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="判断 J1 是否需要工单。", auth_context=build_auth_context(role="admin")
    )
    result = WorkflowRuntimeExecutor(node_registry={"workorder": WorkorderNode()}).execute(
        snapshot.execution_plan
    )
    assessment = result.node_results[0].output["need_assessment"]
    assert assessment["recommendation"] == "insufficient_evidence"
    assert "draft" not in result.node_results[0].output


class _StructuredSource:
    node_type = "source"

    def __init__(self, output: dict) -> None:
        self.output = output

    def run(self, **_) -> NodeExecutionOutput:  # noqa: ANN003
        return NodeExecutionOutput(output=self.output)


@pytest.mark.parametrize(
    ("source_output", "expected_status", "expected_code"),
    [
        ({"runtime_status": "abnormal"}, "completed", None),
        ({"runtime_status": "normal"}, "skipped", "skipped_condition_not_met"),
        ({"note": "no structured assessment"}, "blocked", "blocked_condition_unresolved"),
    ],
)
def test_condition_gate_true_false_unknown(source_output, expected_status: str, expected_code: str | None) -> None:  # noqa: ANN001
    plan = ExecutionPlan(
        plan_id="plan.phase2r.condition",
        plan_version=CANONICAL_PLAN_VERSION,
        nodes=[
            {"node_id": "source_1", "node_type": "source", "goal_ids": ["goal_source"]},
            {
                "node_id": "target_1",
                "node_type": "target",
                "goal_ids": ["goal_target"],
                "condition": {
                    "predicate": "diagnosis_is_abnormal",
                    "source_goal_id": "goal_source",
                    "on_false": "skip",
                    "on_unknown": "block",
                },
            },
        ],
        edges=[{"from": "source_1", "to": "target_1"}],
    )
    runtime = WorkflowRuntimeExecutor(node_registry={
        "source": _StructuredSource(source_output),
        "target": _StructuredSource({"success": True}),
    })
    result = runtime.execute(plan)
    target = result.node_results[1]
    assert target.status == expected_status
    assert (target.error or {}).get("code") == expected_code


def test_prior_result_is_reference_only_and_never_an_artifact_id() -> None:
    parsed = CurrentUtteranceParser().parse("基于刚才结果生成报告。")
    assert parsed.clauses[0].source.source_kind == "prior_result"
    assert parsed.clauses[0].source.entity_refs
    assert all(not value.startswith("artifact:") for value in parsed.clauses[0].source.entity_refs)
