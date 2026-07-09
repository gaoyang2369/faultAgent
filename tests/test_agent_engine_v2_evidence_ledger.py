from __future__ import annotations

from fault_diagnosis.agent import ExecutionPlan, WorkflowRuntimeExecutor
from fault_diagnosis.agent.output.answer import build_output_frame
from fault_diagnosis.agent.evidence import (
    EvidenceLedgerWriter,
    build_v2_claim,
    create_ledger,
    map_manual_evidence,
    map_timeseries_evidence,
    project_ledger_to_evidence_bundle,
    validate_ledger,
)
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.domain.diagnosis.evidence.quality import validate_evidence_bundle


def test_ledger_commits_authorized_evidence_dedupes_and_generates_refs() -> None:
    ledger = create_ledger(trace_id="trace.evidence")
    writer = EvidenceLedgerWriter(ledger)
    evidence = map_manual_evidence(
        evidence_id="ev_manual_1",
        summary="用户确认现场看到 A07089。",
        content={"fault_code": "A07089"},
    )

    first = writer.commit_evidence(evidence, node={"node_id": "manual_1", "node_type": "manual"})
    second = writer.commit_evidence(evidence, node={"node_id": "manual_1", "node_type": "manual"})

    assert first.refs == ["ev_manual_1"]
    assert second.refs == ["ev_manual_1"]
    assert second.deduped == 1
    assert len(ledger.evidence_items) == 1
    assert ledger.evidence_items[0]["node_id"] == "manual_1"


def test_final_claim_without_evidence_is_not_promoted_to_final_claim_ids() -> None:
    ledger = create_ledger(trace_id="trace.claim")
    writer = EvidenceLedgerWriter(ledger)
    writer.commit_claims(
        [
            build_v2_claim(
                claim_id="claim_no_support",
                claim_type="diagnosis_summary",
                statement="缺少证据的最终判断",
                supporting_evidence_ids=[],
                status="final",
            )
        ]
    )
    validation = writer.finalize()

    assert "claim_no_support" not in ledger.final_claim_ids
    assert validation.checks["all_final_claims_have_evidence"] is False
    assert "final_claim_without_evidence" in validation.warnings


def test_dangling_evidence_refs_are_detected_and_not_promoted() -> None:
    ledger = create_ledger(trace_id="trace.dangling")
    writer = EvidenceLedgerWriter(ledger)
    writer.commit_claims(
        [
            build_v2_claim(
                claim_id="claim_dangling",
                claim_type="diagnosis_summary",
                statement="引用不存在证据",
                supporting_evidence_ids=["ev_missing"],
                status="final",
            )
        ]
    )
    validation = writer.finalize()

    assert ledger.final_claim_ids == []
    assert validation.checks["no_dangling_evidence_refs"] is False
    assert validation.checks["dangling_evidence_refs"] == ["ev_missing"]


def test_missing_evidence_is_disclosed_and_projected_to_legacy_bundle() -> None:
    ledger = create_ledger(trace_id="trace.missing")
    writer = EvidenceLedgerWriter(ledger)
    writer.commit_evidence(
        map_manual_evidence(evidence_id="ev_manual_1", summary="已有用户陈述。", content="用户陈述"),
        node={"node_id": "manual_1", "node_type": "manual"},
    )
    writer.commit_claims(
        [
            build_v2_claim(
                claim_id="claim_missing",
                claim_type="diagnosis_summary",
                statement="还需要现场参数记录。",
                supporting_evidence_ids=["ev_manual_1"],
                missing_evidence=["现场参数变更记录"],
                status="final",
            )
        ]
    )
    validation = writer.finalize()
    bundle = project_ledger_to_evidence_bundle(ledger, trace_id="trace.missing")

    assert validation.checks["missing_evidence"] == ["现场参数变更记录"]
    assert validation.checks["missing_evidence_disclosed"] is True
    assert any(item.evidence_type == "missing_evidence_disclosure" for item in bundle.evidence_items)
    assert validate_evidence_bundle(bundle)["no_dangling_evidence_refs"] is True


def test_stale_evidence_is_disclosed_or_refreshed() -> None:
    stale_ledger = create_ledger(trace_id="trace.stale")
    stale_writer = EvidenceLedgerWriter(stale_ledger)
    stale_writer.commit_evidence(
        map_timeseries_evidence(
            evidence_id="ev_stale_metric",
            summary="上一轮样本已滞后，不代表实时状态。",
            content={"currentness_level": "stale"},
            freshness="stale",
        )
    )
    stale_validation = stale_writer.finalize()

    refreshed_ledger = create_ledger(trace_id="trace.refreshed")
    refreshed_writer = EvidenceLedgerWriter(refreshed_ledger)
    refreshed_writer.commit_evidence(
        map_timeseries_evidence(
            evidence_id="ev_stale_metric",
            summary="上一轮样本已滞后，不代表实时状态。",
            content={"currentness_level": "stale"},
            freshness="stale",
        )
    )
    refreshed_writer.commit_evidence(
        map_timeseries_evidence(
            evidence_id="ev_current_metric",
            summary="已刷新当前运行状态。",
            content={"currentness_level": "realtime"},
            freshness="current",
        )
    )
    refreshed_validation = refreshed_writer.finalize()

    assert stale_validation.checks["stale_evidence_disclosed"] is True
    assert any(item["evidence_type"] == "stale_evidence_disclosure" for item in stale_ledger.evidence_items)
    assert refreshed_validation.checks["stale_evidence_refreshed"] is True
    assert not any(item["evidence_type"] == "stale_evidence_disclosure" for item in refreshed_ledger.evidence_items)


def test_unauthorized_evidence_is_filtered_from_ledger_and_projection() -> None:
    ledger = create_ledger(trace_id="trace.unauthorized")
    writer = EvidenceLedgerWriter(ledger)
    result = writer.commit_evidence(
        map_manual_evidence(
            evidence_id="ev_secret",
            summary="未授权证据",
            content="secret",
            authorized=False,
        )
    )
    writer.commit_claims(
        [
            build_v2_claim(
                claim_id="claim_secret",
                claim_type="diagnosis_summary",
                statement="不能使用未授权证据支撑。",
                supporting_evidence_ids=["ev_secret"],
                status="final",
            )
        ]
    )
    validation = writer.finalize()
    bundle = project_ledger_to_evidence_bundle(ledger, trace_id="trace.unauthorized")

    assert result.filtered_unauthorized == ["ev_secret"]
    assert ledger.evidence_items == []
    assert validation.checks["no_unauthorized_evidence_refs"] is False
    assert bundle.evidence_items == []
    assert bundle.claims == []


def test_runtime_uses_v2_ledger_writer_for_real_tool_node_evidence() -> None:
    class Runtime:
        def invoke_sql_tool(self, tool_name: str, payload: str):
            return [
                (
                    1,
                    "2026-07-08 10:00:00",
                    "G120电机1",
                    "INV-J1",
                    "2026-07-08",
                    "10:00:00",
                    "异常",
                    "A07089",
                    "",
                    "0",
                    "0",
                    560.0,
                    1000.0,
                    700.0,
                    12.0,
                    10.0,
                    8.0,
                    32.0,
                    72.0,
                    66.0,
                    11.0,
                    1.0,
                    1.0,
                    100.0,
                    68.0,
                    82.0,
                    81.0,
                    50.0,
                    12.0,
                    2.0,
                    "2026-07-08 10:00:00",
                )
            ]

    plan = ExecutionPlan(
        plan_id="plan.ledger.runtime",
        plan_version="v2.phase7.validated",
        nodes=[
            {
                "node_id": "sql_1",
                "node_type": "sql",
                "inputs": {"sql_query": "SELECT * FROM real_data_01", "device_refs": ["J1号机"]},
            }
        ],
        allowed_tools=["sql.read"],
        required_evidence=["latest_runtime_status"],
    )
    result = WorkflowRuntimeExecutor(real_tools=True, tool_runtime=Runtime()).execute(
        plan,
        trace_id="trace.runtime.ledger",
        auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
    )

    assert result.status == "completed"
    assert result.evidence_ledger.ledger_id == "ledger_trace_runtime_ledger"
    assert result.evidence_ledger.evidence_items
    assert result.evidence_ledger.evidence_items[0]["metadata"]["authorized"] is True
    assert result.evidence_ledger.quality_checks["evidence_count"] >= 1
    assert "evidence_quality" in result.trace


def test_validate_ledger_public_interface_reports_passed_state() -> None:
    ledger = create_ledger(trace_id="trace.public")
    writer = EvidenceLedgerWriter(ledger)
    writer.commit_evidence(map_manual_evidence(evidence_id="ev_1", summary="证据", content="证据"))
    writer.commit_claims(
        [
            build_v2_claim(
                claim_id="claim_1",
                claim_type="diagnosis_summary",
                statement="有证据支撑。",
                supporting_evidence_ids=["ev_1"],
                status="final",
            )
        ]
    )
    writer.finalize()
    validation = validate_ledger(ledger)

    assert validation.passed is True
    assert ledger.final_claim_ids == ["claim_1"]


def test_final_answer_degrades_unsupported_final_claim() -> None:
    ledger = create_ledger(trace_id="trace.output.gate")
    writer = EvidenceLedgerWriter(ledger)
    writer.commit_claims(
        [
            build_v2_claim(
                claim_id="claim_no_support",
                claim_type="diagnosis_summary",
                statement="缺少证据的诊断结论",
                supporting_evidence_ids=[],
                status="final",
            )
        ]
    )
    writer.finalize()
    bundle = project_ledger_to_evidence_bundle(ledger, trace_id="trace.output.gate")

    frame = build_output_frame(status="completed", evidence_bundle=bundle, requested_variant="diagnosis_answer")

    assert "诊断结论：" not in frame.final_answer
    assert "待确认/需补充" in frame.final_answer
    assert frame.guardrail_result["final_claims_without_evidence"] == ["claim_no_support"]


def test_stale_evidence_is_disclosed_in_final_answer_and_guardrail() -> None:
    ledger = create_ledger(trace_id="trace.output.stale")
    writer = EvidenceLedgerWriter(ledger)
    writer.commit_evidence(
        map_timeseries_evidence(
            evidence_id="ev_stale_metric",
            summary="上一轮样本已滞后，不代表实时状态。",
            content={"currentness_level": "stale"},
            freshness="stale",
        )
    )
    writer.commit_claims(
        [
            build_v2_claim(
                claim_id="claim_supported_stale",
                claim_type="diagnosis_summary",
                statement="J1 状态需要复核。",
                supporting_evidence_ids=["ev_stale_metric"],
                status="final",
            )
        ]
    )
    writer.finalize()
    bundle = project_ledger_to_evidence_bundle(ledger, trace_id="trace.output.stale")

    frame = build_output_frame(status="completed", evidence_bundle=bundle, requested_variant="diagnosis_answer")

    assert "时效性提示" in frame.final_answer
    assert frame.guardrail_result["stale_evidence"]
