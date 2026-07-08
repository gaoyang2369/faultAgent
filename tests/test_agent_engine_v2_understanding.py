from __future__ import annotations

import json

from fault_diagnosis.agent import AgentEngineV2, ContextFrame, IntentFrameBuilder, RewriteFrameBuilder


def test_composite_alarm_status_workorder_intent_frame() -> None:
    frame = IntentFrameBuilder().build("A07089 是什么，现在 J1 还故障吗，要不要工单")

    assert frame.fault_code_refs == ["A07089"]
    assert frame.device_refs == ["J1"]
    assert {"explain_fault_code", "check_current_status", "decide_workorder"} <= set(frame.sub_intents)
    assert {"answer", "workorder_decision"} <= set(frame.requested_outputs)


def test_report_handoff_rewrite_keeps_previous_artifact_intent() -> None:
    intent = IntentFrameBuilder().build("基于刚才结果生成报告")
    rewrite = RewriteFrameBuilder().build(
        "基于刚才结果生成报告",
        intent_frame=intent,
        context_frame=ContextFrame(referenced_artifact_id="artifact.prev"),
    )

    assert "generate_report" in intent.sub_intents
    assert "artifact.prev" in rewrite.user_rewrite
    assert "刚才结果" in rewrite.rewrite_reason
    assert "artifact" in " ".join(rewrite.retrieval_queries)
    assert rewrite.staleness_note


def test_explicit_j2_switch_rewrites_current_status_without_ambiguity() -> None:
    intent = IntentFrameBuilder().build("那 J2 呢")
    rewrite = RewriteFrameBuilder().build("那 J2 呢", intent_frame=intent)

    assert intent.device_refs == ["J2"]
    assert "check_current_status" in intent.sub_intents
    assert "missing_device" not in intent.ambiguities
    assert "J2" in rewrite.user_rewrite
    assert "当前运行状态" in rewrite.user_rewrite
    assert "显式切换" in rewrite.rewrite_reason


def test_model_failure_uses_rule_fallback_and_records_trace() -> None:
    frame = IntentFrameBuilder().build(
        "诊断 J1 A07089",
        model_error=RuntimeError("model unavailable"),
    )

    assert frame.fault_code_refs == ["A07089"]
    assert frame.device_refs == ["J1"]
    assert frame.model_trace["fallback_used"] is True
    assert frame.model_trace["fallback_reason"] == "model_error:RuntimeError"


def test_plan_only_includes_understanding_frames_and_trace() -> None:
    snapshot = AgentEngineV2().plan_only(raw_message="A07089 是什么，现在 J1 还故障吗")

    assert snapshot.status == "validated"
    assert snapshot.intent_frame.fault_code_refs == ["A07089"]
    assert snapshot.intent_frame.device_refs == ["J1"]
    assert snapshot.rewrite_frame.user_rewrite
    assert snapshot.trace["request_understanding"]["raw_message"] == "A07089 是什么，现在 J1 还故障吗"
    assert snapshot.trace["request_understanding"]["user_rewrite"] == snapshot.rewrite_frame.user_rewrite
    assert snapshot.trace["request_understanding"]["rewrite_reason"] == snapshot.rewrite_frame.rewrite_reason
    assert snapshot.execution_plan.nodes
    assert snapshot.output_frame.guardrail_result["status"] == "validated"
    assert "candidate_plan" in snapshot.trace
    assert "validation" in snapshot.trace
    assert json.loads(snapshot.model_dump_json())["status"] == snapshot.status
