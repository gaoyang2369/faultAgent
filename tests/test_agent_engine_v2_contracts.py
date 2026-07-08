from __future__ import annotations

import json

from fault_diagnosis.agent_engine import (
    AgentEngineV2,
    CancelToken,
    ContextFrame,
    EvidenceLedger,
    ExecutionPlan,
    IntentFrame,
    NodeResult,
    OutputFrame,
    PlanSnapshotV2,
    RewriteFrame,
    RuntimeResult,
    RuntimeState,
    SkillRoute,
    WorkflowRuntimeExecutor,
)


def test_v2_contracts_are_json_serializable() -> None:
    contracts = [
        IntentFrame(raw_message="诊断 J1 A07089", normalized_message="诊断 J1 A07089"),
        RewriteFrame(),
        ContextFrame(),
        SkillRoute(),
        ExecutionPlan(),
        NodeResult(),
        EvidenceLedger(),
        OutputFrame(),
        RuntimeState(plan=ExecutionPlan(plan_id="plan.test", plan_version="v2.test.validated")),
        RuntimeResult(status="completed"),
        PlanSnapshotV2(),
    ]

    for contract in contracts:
        dumped = contract.model_dump(mode="json")
        encoded = contract.model_dump_json()

        assert isinstance(dumped, dict)
        assert json.loads(encoded)["schema_version"] == dumped["schema_version"]


def test_v2_contract_defaults_are_stable_and_isolated() -> None:
    first = PlanSnapshotV2()
    second = PlanSnapshotV2()

    first.intent_frame.device_refs.append("J1")
    first.execution_plan.nodes.append({"node_id": "sql_1"})
    first.evidence_ledger.quality_checks["all_claims_have_evidence"] = True

    assert first.status == "not_implemented"
    assert first.execution_plan.risk_level == "low"
    assert first.intent_frame.confidence == 0.0
    assert first.node_results == []
    assert second.intent_frame.device_refs == []
    assert second.execution_plan.nodes == []
    assert second.evidence_ledger.quality_checks == {}


def test_plan_snapshot_v2_schema_exposes_expected_top_level_fields() -> None:
    schema = PlanSnapshotV2.model_json_schema()
    properties = schema["properties"]

    for field_name in (
        "schema_version",
        "engine_version",
        "status",
        "intent_frame",
        "rewrite_frame",
        "context_frame",
        "skill_route",
        "execution_plan",
        "node_results",
        "evidence_ledger",
        "output_frame",
        "trace",
        "warnings",
        "metadata",
    ):
        assert field_name in properties


def test_agent_engine_v2_plan_only_returns_validated_phase4_snapshot() -> None:
    snapshot = AgentEngineV2().plan_only(
        raw_message="  诊断 J1 A07089  ",
        thread_id="thread.phase1",
        request_id="request.phase1",
        metadata={"source": "unit_test"},
    )

    assert snapshot.schema_version == "agent_engine_plan_snapshot.v2"
    assert snapshot.engine_version == "v2"
    assert snapshot.status in {"validated", "blocked"}
    assert snapshot.intent_frame.raw_message == "  诊断 J1 A07089  "
    assert snapshot.intent_frame.normalized_message == "诊断 J1 A07089"
    assert snapshot.execution_plan.plan_version.endswith(".validated")
    assert snapshot.execution_plan.nodes
    assert snapshot.execution_plan.allowed_tools
    assert snapshot.output_frame.guardrail_result["status"] in {"validated", "degraded", "blocked"}
    assert "candidate_plan" in snapshot.trace
    assert "validation" in snapshot.trace
    assert "plan_diff" in snapshot.trace
    assert snapshot.metadata["thread_id"] == "thread.phase1"
    assert snapshot.metadata["request_id"] == "request.phase1"
    assert snapshot.metadata["source"] == "unit_test"
    assert json.loads(snapshot.model_dump_json())["status"] == snapshot.status


def test_v2_runtime_public_interfaces_are_exported() -> None:
    token = CancelToken()
    token.cancel("test_stop")

    assert token.cancelled is True
    assert token.reason == "test_stop"
    assert WorkflowRuntimeExecutor
    assert RuntimeState(plan=ExecutionPlan(plan_id="plan.test", plan_version="v2.test.validated")).status == "running"
