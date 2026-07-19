from __future__ import annotations

import json

import pytest

from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator, CurrentUtteranceParser
from fault_diagnosis.agent.output import GroundedAnswerValidator, build_answer_source_packet
from fault_diagnosis.agent.contracts import DeliverableResult
from fault_diagnosis.agent.output.deliverables import composite_status
from fault_diagnosis.agent.output.presenter import CompositePresenter
from fault_diagnosis.agent.output.answer import _deliverable_contract_validation
from fault_diagnosis.agent.planning import PlanCompiler
from fault_diagnosis.agent.semantics import SemanticResolutionService
from fault_diagnosis.agent.semantics.model_gateway import ModelGatewayResult
from fault_diagnosis.domain.canonical_turn import TurnCommand
from fault_diagnosis.domain.security.permissions import build_auth_context


MESSAGE = "G120电机1现在有没有故障呀？可以生成一份运行报告"


def _command(message: str = MESSAGE) -> TurnCommand:
    return TurnCommand(
        command="preview",
        thread_id="production-multigoal-thread",
        user_id="production-multigoal-user",
        turn_id="production-multigoal-turn",
        message_id="production-multigoal-message",
        idempotency_key=message,
        raw_message=message,
    )


def _report_only_payload(message: str = MESSAGE) -> dict:
    start = message.index("可以") if "可以" in message else 0
    return {
        "schema_version": "semantic_turn_proposal.v1",
        "entities": [],
        "ambiguities": [],
        "clauses": [{
            "clause_index": 0,
            "text": message[start:],
            "start": start,
            "end": len(message),
            "capability": "generate_report",
            "confidence": 0.95,
        }],
    }


class _Gateway:
    model_name = "production-regression-model"

    def __init__(self, *, payload: object = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    async def invoke_clause_model(self, _request, *, cancel_event=None):  # noqa: ANN001
        if self.error is not None:
            raise self.error
        return ModelGatewayResult(self.payload, 1.0, 1, 1, False)


def test_rule_parser_recognizes_status_check_and_report_as_separate_capabilities() -> None:
    parsed = CurrentUtteranceParser().parse(MESSAGE)

    assert [clause.action.capability for clause in parsed.clauses if clause.action] == [
        "check_runtime_status",
        "generate_report",
    ]


@pytest.mark.asyncio
async def test_model_success_cannot_reduce_deterministic_capabilities() -> None:
    parser = CurrentUtteranceParser()
    service = SemanticResolutionService(
        parser=parser,
        gateway_factory=lambda _semaphore: _Gateway(payload=_report_only_payload()),
        mode="primary",
        call_policy="always",
    )
    result = await ConversationTurnCoordinator(parser=parser, semantic_service=service).preview_turn_async(
        _command(), auth_context=build_auth_context(role="admin")
    )

    assert [goal.capability for goal in result.request.goals] == [
        "check_runtime_status",
        "generate_report",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (None, TimeoutError("timeout")),
        (None, RuntimeError("provider failed")),
        ({}, None),
        ({"schema_version": "semantic_turn_proposal.v1"}, None),
        ({"schema_version": "semantic_turn_proposal.v1", "entities": [], "clauses": []}, None),
    ],
)
async def test_model_failures_keep_every_deterministic_capability(payload, error) -> None:  # noqa: ANN001
    parser = CurrentUtteranceParser()
    service = SemanticResolutionService(
        parser=parser,
        gateway_factory=lambda _semaphore: _Gateway(payload=payload, error=error),
        mode="primary",
        call_policy="always",
    )
    result = await ConversationTurnCoordinator(parser=parser, semantic_service=service).preview_turn_async(
        _command(), auth_context=build_auth_context(role="admin")
    )

    assert [goal.capability for goal in result.request.goals] == [
        "check_runtime_status",
        "generate_report",
    ]


def test_report_depends_on_same_turn_status_artifact_and_both_goals_are_ready() -> None:
    coordinator = ConversationTurnCoordinator()
    preview = coordinator.preview_turn(_command(), auth_context=build_auth_context(role="admin"))
    status_goal, report_goal = preview.request.goals

    assert status_goal.user_requested and report_goal.user_requested
    assert report_goal.dependencies == [status_goal.goal_id]
    assert [item.status for item in preview.readiness] == ["ready", "ready"]

    plan = PlanCompiler().compile(
        request=preview.request,
        authorization=preview.authorization,
        readiness=preview.readiness,
        sources=preview.source_resolutions,
    )
    assert [node.node_type for node in plan.nodes] == ["sql", "analysis", "report"]
    report_node = next(node for node in plan.nodes if node.node_type == "report")
    report_sources = report_node.inputs["artifact_role_bindings"]
    assert any(
        item["role"] == "report_source" and item["producer_goal_id"] == report_goal.goal_id
        for item in report_sources
    )


def test_standalone_report_with_explicit_device_gets_a_system_runtime_dependency() -> None:
    preview = ConversationTurnCoordinator().preview_turn(
        _command("为 G120电机1 生成一份当前运行报告"),
        auth_context=build_auth_context(role="admin"),
    )

    producer, report = preview.request.goals
    assert producer.capability == "check_runtime_status"
    assert producer.origin == "dependency"
    assert producer.user_requested is False
    assert report.capability == "generate_report"
    assert report.dependencies == [producer.goal_id]
    assert [item.status for item in preview.readiness] == ["ready", "ready"]


def test_explicit_device_is_allowed_in_blocked_or_partial_answer_but_unknown_device_is_not() -> None:
    packet = build_answer_source_packet(
        user_message="G120电机1现在有没有故障？请生成报告",
        deterministic_answer="报告来源暂不可用。",
        deliverables=[],
        evidence_bundle=None,
        runtime_metadata={"status": "blocked"},
    )
    validator = GroundedAnswerValidator()

    allowed = validator.validate(json.dumps({
        "schema_version": "grounded_answer.v1",
        "answer": "G120电机1的报告暂未生成。",
        "used_claim_ids": [],
        "used_evidence_ids": [],
        "limitations_disclosed": False,
        "data_basis_disclosed": False,
    }, ensure_ascii=False), source_packet=packet)
    unknown = validator.validate(json.dumps({
        "schema_version": "grounded_answer.v1",
        "answer": "G120电机2的报告暂未生成。",
        "used_claim_ids": [],
        "used_evidence_ids": [],
        "limitations_disclosed": False,
        "data_basis_disclosed": False,
    }, ensure_ascii=False), source_packet=packet)

    assert allowed.valid is True
    assert "unallowed_device_reference" in unknown.errors


def test_completed_status_is_preserved_when_report_fails() -> None:
    status = DeliverableResult(
        goal_id="status",
        capability="check_runtime_status",
        status="completed",
        title="运行状态",
        structured_content={"assessments": [{
            "device": "G120电机1",
            "runtime_status": "normal",
            "sample_count": 3,
            "key_findings": ["未发现活动故障码"],
            "data_basis": {
                "resolution_mode": "realtime_window",
                "resolved_window": {"start": "2026-07-19 15:00:00", "end": "2026-07-19 16:00:00"},
                "latest_sample_time": "2026-07-19 16:00:00",
            },
        }]},
    )
    report = DeliverableResult(
        goal_id="report",
        capability="generate_report",
        status="failed",
        title="运行报告",
        error_message="报告服务暂不可用。",
    )

    answer = CompositePresenter().present(
        deliverables=[status, report], status="failed"
    ).content

    assert composite_status([status, report], "failed") == "partial"
    assert "G120电机1" in answer
    assert "未发现活动故障码" in answer
    assert "报告服务暂不可用" in answer


def test_composite_coverage_marks_only_the_incomplete_deliverable() -> None:
    status = DeliverableResult(
        goal_id="status",
        capability="check_runtime_status",
        status="completed",
        structured_content={"assessments": [{"device": "G120电机1"}]},
    )
    report = DeliverableResult(
        goal_id="report",
        capability="generate_report",
        status="completed",
        structured_content={},
    )

    validation = _deliverable_contract_validation(
        [status, report],
        None,
        runtime_validation={
            "required_fields": ["runtime_status"],
            "missing_fields": [],
            "required_claim_types": ["runtime_status_assessment"],
            "present_claim_types": ["runtime_status_assessment"],
            "missing_claim_types": [],
            "ledger_passed": True,
            "forbidden_claim_types_present": [],
            "contract_satisfied": True,
        },
    )

    assert validation["contract_satisfied"] is False
    assert validation["incomplete_capabilities"] == ["generate_report"]
