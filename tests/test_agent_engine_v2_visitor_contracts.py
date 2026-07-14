from __future__ import annotations

from datetime import datetime
import pytest
from pydantic import ValidationError

from fault_diagnosis.agent import AgentEngineV2, WorkflowRuntimeExecutor
from fault_diagnosis.agent.contracts import OutputFrame, PlanGoal
from fault_diagnosis.agent.runtime.plan_preparer import prepare_v2_execution_validation
from fault_diagnosis.agent.output.answer import build_output_frame
from fault_diagnosis.domain.diagnosis.contracts import EvidenceBundle, EvidenceItem, EvidenceQuality
from fault_diagnosis.domain.diagnosis.runtime_status import (
    DataResolutionPolicy,
    RuntimeStatusAssessment,
    TimeWindow,
    build_runtime_status_assessment,
)
from fault_diagnosis.domain.security.permissions import build_auth_context


def _guest():
    return build_auth_context(role="guest")


def _window(start: str, end: str) -> TimeWindow:
    return TimeWindow(start=datetime.fromisoformat(start), end=datetime.fromisoformat(end))


def test_v01_fault_code_and_detail_followup_remain_allowed() -> None:
    first = AgentEngineV2().build_plan_snapshot(raw_message="A07089是什么意思？", auth_context=_guest())
    assert first.status == "validated"
    assert first.effective_request_frame.effective_semantic_intent == "explain_fault_code"
    assert first.skill_route.primary_skill == "fault_code_explain"


@pytest.mark.parametrize(
    ("message", "capability"),
    [
        ("诊断一下设备是否有故障", "diagnose_fault"),
        ("判断它有没有异常", "diagnose_fault"),
        ("当前健康状况如何", "check_runtime_status"),
        ("为什么速度异常", "diagnose_fault"),
        ("分析故障原因", "diagnose_fault"),
        ("看一下当前运行状态", "check_runtime_status"),
    ],
)
def test_original_capability_taxonomy_is_not_rewritten(message: str, capability: str) -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(raw_message=message, auth_context=build_auth_context(role="admin"))
    assert snapshot.effective_request_frame.original_semantic_intent == capability
    assert snapshot.effective_request_frame.effective_semantic_intent == capability


def test_data_resolution_policy_supports_all_three_modes() -> None:
    requested = _window("2026-07-12T08:00:00", "2026-07-12T09:00:00")
    latest = datetime.fromisoformat("2026-06-10T12:12:59")

    realtime = DataResolutionPolicy(strategy="realtime_then_latest", data_environment="simulation")
    realtime_basis = realtime.resolve(requested_window=requested, realtime_row_count=3)
    assert realtime_basis.resolution_mode == "realtime_window"
    assert realtime_basis.fallback_used is False

    fallback_basis = realtime.resolve(
        requested_window=requested,
        realtime_row_count=0,
        latest_sample_time=latest,
        fallback_window=_window("2026-06-10T11:12:59", "2026-06-10T12:12:59"),
        fallback_row_count=50,
    )
    assert fallback_basis.resolution_mode == "latest_available_fallback"
    assert fallback_basis.freshness == "historical_latest"
    assert fallback_basis.usable_for_diagnosis is True

    no_data = DataResolutionPolicy(strategy="realtime_only", data_environment="simulation").resolve(
        requested_window=requested,
        realtime_row_count=0,
    )
    assert no_data.resolution_mode == "no_data"
    assert no_data.usable_for_status is False


def test_v02_status_assessment_does_not_treat_event_code_as_automatic_abnormal() -> None:
    basis = DataResolutionPolicy(strategy="realtime_then_latest", data_environment="simulation").resolve(
        requested_window=_window("2026-07-12T08:00:00", "2026-07-12T09:00:00"),
        realtime_row_count=1,
    )
    evidence = [
        EvidenceItem(
            evidence_id="ev_window",
            evidence_type="device_status",
            source_type="sql",
            source_name="real_data_01",
            asset_id="G120电机1",
            content={"sample_count": 1, "latest_status": "运行"},
            summary="查询到一条运行记录。",
            quality=EvidenceQuality(reliability="high", freshness="current", relevance="high", completeness="complete"),
        ),
        EvidenceItem(
            evidence_id="ev_event",
            evidence_type="alarm_event",
            source_type="sql",
            source_name="real_data_01",
            asset_id="G120电机1",
            content={"effective_codes": ["A07089"], "active_fault_count": 0, "abnormal_count": 0},
            summary="样本中出现事件码 A07089。",
            quality=EvidenceQuality(reliability="high", freshness="current", relevance="high", completeness="complete"),
        ),
    ]
    assessment = build_runtime_status_assessment(
        device="G120电机1",
        query_status="success",
        data_basis=basis,
        evidence_items=evidence,
    )
    assert assessment.runtime_status == "attention"
    assert "event_codes_present" in assessment.status_reasons
    assert assessment.supporting_evidence_ids == ["ev_window", "ev_event"]


@pytest.mark.parametrize(
    ("extra", "expected", "reason"),
    [
        ([('alarm_event', {"effective_codes": ["F01002"], "active_fault_count": 1})], "abnormal", "active_fault_present"),
        (
            [
                ('metric_snapshot', {"metric": "load", "value": 90, "status": "abnormal"}),
                ('timeseries_feature', {"metric": "speed", "value": 35, "status": "abnormal"}),
            ],
            "abnormal",
            "multiple_strong_abnormal_signals",
        ),
        ([('metric_snapshot', {"metric": "load", "value": 80, "status": "abnormal"})], "attention", "attention_metric_threshold_exceeded"),
        ([('metric_snapshot', {"metric": "load", "value": 50, "status": "normal"})], "normal", "evaluated_signals_within_normal_range"),
    ],
)
def test_runtime_status_reason_matrix(extra, expected: str, reason: str) -> None:
    basis = DataResolutionPolicy(strategy="realtime_then_latest", data_environment="simulation").resolve(
        requested_window=_window("2026-07-12T08:00:00", "2026-07-12T09:00:00"),
        realtime_row_count=1,
    )
    items = [
        EvidenceItem(
            evidence_id="ev_window",
            evidence_type="device_status",
            source_type="sql",
            source_name="real_data_01",
            content={"sample_count": 1, "latest_status": "运行"},
            summary="运行样本",
        )
    ]
    items.extend(
        EvidenceItem(
            evidence_id=f"ev_{index}",
            evidence_type=evidence_type,
            source_type="sql",
            source_name="real_data_01",
            content=content,
            summary=f"finding {index}",
        )
        for index, (evidence_type, content) in enumerate(extra, start=1)
    )
    assessment = build_runtime_status_assessment(
        device="G120电机1",
        query_status="success",
        data_basis=basis,
        evidence_items=items,
    )
    assert assessment.runtime_status == expected
    assert reason in assessment.status_reasons


def test_v02_status_output_requires_typed_assessment_and_claim() -> None:
    basis = DataResolutionPolicy(strategy="realtime_then_latest", data_environment="simulation").resolve(
        requested_window=_window("2026-07-12T08:00:00", "2026-07-12T09:00:00"),
        realtime_row_count=1,
    )
    assessment = RuntimeStatusAssessment(
        device="G120电机1",
        query_status="success",
        runtime_status="attention",
        status_reasons=["event_codes_present"],
        data_basis=basis,
        sample_count=1,
        event_codes=["A07089"],
        key_findings=["检测到事件码 A07089。"],
        supporting_evidence_ids=["ev_window"],
    )
    bundle = EvidenceBundle(
        bundle_id="bundle.status",
        trace_id="trace.status",
        evidence_items=[
            EvidenceItem(
                evidence_id="ev_window",
                evidence_type="device_status",
                source_type="sql",
                source_name="real_data_01",
                summary="状态证据",
                goal_ids=["goal_status"],
            )
        ],
        claims=[
            {
                "claim_id": "claim_status",
                "claim_type": "runtime_status_assessment",
                "asset_id": "G120电机1",
                "statement": "设备运行状态需关注。",
                "supporting_evidence_ids": ["ev_window"],
                "status": "final",
                "goal_ids": ["goal_status"],
            }
        ],
        final_claim_ids=["claim_status"],
    )
    frame = build_output_frame(
        artifacts={"runtime_status_assessment": assessment},
        evidence_bundle=bundle,
        goals=[PlanGoal(goal_id="goal_status", capability="check_runtime_status", requested_deliverables=["runtime_status"])],
    )
    assert frame.answer_variant == "runtime_status_answer"
    assert "数据状态：ok" not in frame.final_answer
    assert "A07089" in frame.final_answer
    assert frame.guardrail_result["contract_satisfied"] is True

    with pytest.raises(ValidationError):
        OutputFrame(answer_variant="status_brief_v2", final_answer="伪完整状态回答")


def test_v04_guest_diagnosis_and_workorder_are_blocked_before_clarification() -> None:
    diagnosis = AgentEngineV2().build_plan_snapshot(
        raw_message="诊断一下 G120电机1 当前是不是有故障",
        auth_context=_guest(),
    )
    assert diagnosis.status == "blocked"
    assert diagnosis.effective_request_frame.original_semantic_intent == "diagnose_fault"
    assert diagnosis.effective_request_frame.effective_semantic_intent == "diagnose_fault"
    assert diagnosis.output_frame.answer_variant == "permission_denied"
    assert diagnosis.output_frame.guardrail_result["authorization"]["denied_reason_code"] == "diagnosis_permission_denied"
    assert diagnosis.execution_plan.nodes == []

    workorder = AgentEngineV2().build_plan_snapshot(
        raw_message="那就给它创建一个维修工单",
        auth_context=_guest(),
    )
    assert workorder.status == "blocked"
    assert workorder.effective_request_frame.original_semantic_intent == "create_workorder_draft"
    assert workorder.skill_route.primary_skill != "clarification"
    assert workorder.execution_plan.nodes == []


def test_v05_out_of_scope_device_still_blocks_sql() -> None:
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="查询G120电机2最近的运行状态",
        auth_context=_guest(),
    )
    assert snapshot.status == "blocked"
    assert snapshot.output_frame.guardrail_result["authorization"]["denied_reason_code"] == "asset_out_of_scope"
    assert snapshot.effective_request_frame.executed_semantic_intent == ""


def test_authorized_missing_device_is_goal_scoped_and_does_not_create_node() -> None:
    auth = build_auth_context(
        role="engineer",
        asset_scope=["G120电机1"],
        table_scope=["real_data_01"],
    )
    snapshot = AgentEngineV2().build_plan_snapshot(raw_message="查看当前运行状态", auth_context=auth)
    goal = snapshot.metadata["canonical_request"]["goals"][0]
    assert snapshot.skill_route.primary_skill == ""
    assert snapshot.execution_plan.nodes == []
    assert snapshot.metadata["goal_readiness"] == [
        {
            "schema_version": "goal_readiness_decision.v1",
            "goal_id": goal["goal_id"],
            "status": "blocked_missing_slot",
            "blockers": ["device"],
        }
    ]


class _ResolutionSqlRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke_sql_tool(self, tool_name: str, query: str):
        assert tool_name == "sql_db_query"
        self.calls.append(query)
        if "MAX(create_time)" in query:
            return "[('2026-06-10 12:12:59',)]"
        if "NOW() - INTERVAL 1 HOUR" in query:
            return "[]"
        row = (
            1, "2026-06-10 12:12:59", "G120电机1", "INV-G120-1", "2026-06-10", "12:12:59",
            "运行", "", "A07089", "0", "0", 560.0, 1000.0, 950.0, 12.0, 10.0, 8.0,
            32.0, 56.0, 39.0, 11.0, 1.0, 1.0, 100.0, 38.0, 70.0, 70.0, 50.0, 12.0,
            2.0, "2026-06-10 12:12:59",
        )
        return repr([row])


def test_v02_runtime_assessment_and_v03_guest_report_denial_are_independent(monkeypatch) -> None:
    monkeypatch.setattr("fault_diagnosis.config.DATA_RESOLUTION_STRATEGY", "realtime_then_latest")
    monkeypatch.setattr("fault_diagnosis.config.DATA_ENVIRONMENT", "simulation")
    auth = _guest()
    for message in ["查询 G120电机1 最近的运行状态"]:
        snapshot = AgentEngineV2().build_plan_snapshot(raw_message=message, auth_context=auth)
        validation = prepare_v2_execution_validation(snapshot=snapshot, thread_id="thread.visitor", auth_context=auth)
        runtime = _ResolutionSqlRuntime()
        result = WorkflowRuntimeExecutor(real_tools=True, tool_runtime=runtime).execute(
            validation.validated_plan,
            trace_id="trace.visitor",
            thread_id="thread.visitor",
            auth_context=auth,
        )
        assert result.status == "completed"
        assessment = result.output_frame.runtime_status_assessment
        assert assessment["data_basis"]["resolution_mode"] == "latest_available_fallback"
        assert assessment["runtime_status"] == "attention"
        assert assessment["status_reasons"] == ["event_codes_present"]
        assert "2026-06-10 12:12:59" in result.output_frame.final_answer
        assert "不代表真实当前时刻状态" in result.output_frame.final_answer
        assert "数据状态：ok" not in result.output_frame.final_answer
        assert result.output_frame.guardrail_result["contract_satisfied"] is True
        assert {claim["claim_type"] for claim in result.evidence_ledger.claims} == {"runtime_status_assessment"}
        assert not any(node.node_type == "report" for node in validation.validated_plan.nodes)
        manifests = result.complete_payload["artifact"]["payload"]["artifact_manifests"]
        sql_manifest = next(item for item in manifests if item["artifact_type"] == "sql_artifact")
        assert sql_manifest["device_refs"] == ["G120电机1"]
        assert sql_manifest["data_basis"]["resolution_mode"] == "latest_available_fallback"
        assert sql_manifest["latest_sample_time"] == "2026-06-10 12:12:59"
        assert sql_manifest["sample_count"] == 1
        assert sql_manifest["runtime_status"] == "attention"
        assert sql_manifest["supporting_evidence_ids"]
        assert "check_runtime_status" in sql_manifest["supported_followup_capabilities"]

    denied_report = AgentEngineV2().build_plan_snapshot(
        raw_message="给我生成 G120电机1 最近一小时的运行报告",
        auth_context=auth,
    )
    assert denied_report.metadata["canonical_request"]["goals"][0]["capability"] == "generate_report"
    assert denied_report.metadata["goal_authorization"][0]["status"] == "denied"
    assert denied_report.execution_plan.nodes == []
