from __future__ import annotations

from types import SimpleNamespace

from fault_diagnosis.agent_runtime.sse_adapter import parse_sse_chunk
from fault_diagnosis.diagnosis.artifact_backends.memory import MemoryArtifactStoreBackend
from fault_diagnosis.diagnosis.artifact_store import configure_artifact_store_backend, save_thread_artifact
from fault_diagnosis.diagnosis.contracts import (
    AnalysisStepArtifact,
    DiagnosisArtifactEnvelope,
    DiagnosisArtifactType,
    EvidenceBundle,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from fault_diagnosis.observability.tracing import NoopTraceRun
from fault_diagnosis.single_agent.context import apply_context_resolution, load_conversation_diagnosis_state
from fault_diagnosis.single_agent.contracts import SingleAgentDecision
from fault_diagnosis.single_agent.evidence.quality import build_output_guardrail_result
from fault_diagnosis.single_agent.intent import decide_capabilities, fallback_understanding_payload
from fault_diagnosis.single_agent.runner import RestrictedSingleAgentRunner
from fault_diagnosis.single_agent.workorder_drafts import build_pending_workorder_draft_action
from fault_diagnosis.single_agent.workorder_suggestions import build_workorder_suggestion_from_artifact


class _NoopTraceExporter:
    def start_run(self, trace_context):
        return NoopTraceRun(trace_context=trace_context)


def _artifact(thread_id: str = "thread.followup") -> DiagnosisArtifactEnvelope:
    sql_artifact = SqlStepArtifact(
        success=True,
        summary="上一轮报告 SQL 摘要",
        result_preview="[{'device_name': 'G120电机1', 'fault_code': 'A07089'}]",
        raw_output="[{'device_name': 'G120电机1', 'fault_code': 'A07089', 'inverter_load_rate': 78.47}]",
    )
    knowledge_artifact = KnowledgeStepArtifact(
        success=True,
        query="A07089 处理",
        raw_output="A07089 单位转换激活异常，建议复核单位制与功能块配置。",
    )
    analysis_artifact = AnalysisStepArtifact(
        success=True,
        conclusion="A07089 持续出现，速度偏差 46.3%，负载率最高 78.47%。",
        basis=["上一轮报告显示告警 / 需确认"],
        recommendations=["刷新当前状态后确认是否派发"],
        confidence="medium",
    )
    report_artifact = ReportStepArtifact(
        success=True,
        report_filename="report.html",
        report_url="/reports/report.html",
        save_result="/reports/report.html",
    )
    evidence_bundle = EvidenceBundle(
        bundle_id="eb_followup",
        trace_id="trace.previous",
        task={"asset_id": "G120电机1"},
        artifacts={"report_url": "/reports/report.html"},
    )
    decision = SingleAgentDecision(
        primary_task_type="report_generation",
        objects={"device_ids": ["G120电机1"], "alarm_codes": ["A07089"]},
        context_resolution={"active_asset": "G120电机1", "active_fault_codes": ["A07089"]},
    )
    operation_report_payload = {
        "asset": "G120电机1",
        "status_level": "告警 / 需确认",
        "current_event": "A07089 持续出现",
        "key_phenomenon": "速度偏差 46.3%，最高负载率 78.47%",
        "action_priority": "P2",
        "latest_sample_time": "2026-01-14 18:27:24",
        "sample_count": 50,
        "data_freshness_label": "已滞后",
        "data_currentness_label": "STALE / 不代表实时状态",
        "next_action": "刷新当前状态后确认是否派发",
        "evidence_summary": ["A07089 持续出现", "速度偏差 46.3%", "负载率最高 78.47%"],
    }
    return DiagnosisArtifactEnvelope(
        workflow_type=DiagnosisArtifactType.REPORT_GENERATION,
        thread_id=thread_id,
        created_at="2026-06-24T10:00:00",
        request_summary="生成 J1 运行报告",
        final_answer="上一轮报告：A07089 持续出现。",
        report_filename="report.html",
        payload={
            "request": {
                "user_message": "生成 J1 运行报告",
                "user_identity": "管理员",
                "equipment_hint": "G120电机1",
                "fault_code_hint": "A07089",
                "analysis_goal": "运行报告",
            },
            "decision": decision.model_dump(),
            "sql_artifact": sql_artifact.model_dump(),
            "knowledge_artifact": knowledge_artifact.model_dump(),
            "analysis_artifact": analysis_artifact.model_dump(),
            "report_artifact": report_artifact.model_dump(),
            "evidence_bundle": evidence_bundle.model_dump(),
            "operation_report_payload": operation_report_payload,
        },
        evidence=[],
    )


def _recommendation_artifact(thread_id: str = "thread.followup") -> DiagnosisArtifactEnvelope:
    envelope = _artifact(thread_id)
    suggestion = build_workorder_suggestion_from_artifact(
        envelope=envelope,
        decision=SingleAgentDecision(objects={"device_ids": ["G120电机1"], "alarm_codes": ["A07089"]}),
        user_identity="管理员",
    )
    suggestion.source_diagnosis_artifact_id = "eb_followup"
    suggestion.source_report_artifact_id = "/reports/report.html"
    pending = build_pending_workorder_draft_action(
        thread_id=thread_id,
        suggestion=suggestion,
        source_diagnosis_artifact_id="eb_followup",
        source_report_artifact_id="/reports/report.html",
        recommendation_artifact_id="trace.recommend",
        stale_refresh_required=True,
    )
    suggestion.pending_action = pending.model_dump(exclude_none=True)
    envelope.created_at = "2026-06-24T10:05:00"
    envelope.payload["workorder_decision"] = suggestion.model_dump(exclude_none=True)
    return envelope


def _request(message: str, payload: dict) -> object:
    from fault_diagnosis.diagnosis.contracts import DiagnosisRequest

    return DiagnosisRequest(
        user_message=message,
        user_identity="管理员",
        equipment_hint=payload.get("equipment_hint"),
        metric_hint=payload.get("metric_hint"),
        fault_code_hint=payload.get("fault_code_hint"),
        time_range_hint=payload.get("time_range_hint"),
        needs_report=bool(payload.get("needs_report")),
        report_format="markdown",
        analysis_goal=str(payload.get("analysis_goal") or message),
    )


def _decision(message: str, thread_id: str = "thread.followup") -> SingleAgentDecision:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    save_thread_artifact(_artifact(thread_id))
    state = load_conversation_diagnosis_state(thread_id)
    payload = fallback_understanding_payload(message, "管理员")
    apply_context_resolution(payload=payload, message=message, state=state)
    return decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
        conversation_state=state,
    )


def test_context_binder_result_reference() -> None:
    decision = _decision("从结果来看貌似有故障呀？是不是要生成工单？")

    assert decision.context_resolution["resolved"] is True
    assert decision.resolved_context["relation_to_previous"] == "action_followup"
    assert decision.relation_to_previous == "actionize_previous_result"
    assert decision.referenced_artifact_id
    assert decision.resolved_context["referenced_artifact_id"]
    assert decision.context_resolution["active_asset"] == "G120电机1"
    assert "A07089" in decision.context_resolution["active_fault_codes"]


def test_router_workorder_followup() -> None:
    decision = _decision("从结果来看貌似有故障呀？是不是要生成工单？")

    assert any(goal["goal_type"] == "decide_workorder" for goal in decision.goals)
    assert decision.action_target == "workorder"
    assert decision.plan_mode == "workorder_decision_from_artifact"
    assert decision.evidence_mode == "reuse_previous_artifact"
    assert decision.needs_sql is False
    assert decision.needs_knowledge is False
    assert decision.needs_report is False


async def _collect_flow_events(monkeypatch) -> list[dict]:
    from fault_diagnosis.single_agent import runner as runner_module

    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    save_thread_artifact(_artifact())
    monkeypatch.setattr(runner_module, "get_trace_exporter", lambda: _NoopTraceExporter())
    app = SimpleNamespace(state=SimpleNamespace(chat_model=None))
    runner = RestrictedSingleAgentRunner(
        message="从结果来看貌似有故障呀？是不是要生成工单？",
        thread_id="thread.followup",
        user_identity="管理员",
        request_id="request.followup",
        stream_id="stream.followup",
        trace_id="trace.followup",
    )
    chunks = [chunk async for chunk in runner.stream_events(app)]
    return [payload for chunk in chunks if (payload := parse_sse_chunk(chunk))]


async def _collect_create_draft_events(monkeypatch, *, message: str = "那就生成吧", reset_store: bool = True) -> list[dict]:
    from fault_diagnosis.single_agent import runner as runner_module

    if reset_store:
        configure_artifact_store_backend(MemoryArtifactStoreBackend())
        save_thread_artifact(_recommendation_artifact())
    monkeypatch.setattr(runner_module, "get_trace_exporter", lambda: _NoopTraceExporter())
    app = SimpleNamespace(state=SimpleNamespace(chat_model=None))
    runner = RestrictedSingleAgentRunner(
        message=message,
        thread_id="thread.followup",
        user_identity="管理员",
        request_id="request.create",
        stream_id="stream.create",
        trace_id="trace.create",
    )
    chunks = [chunk async for chunk in runner.stream_events(app)]
    return [payload for chunk in chunks if (payload := parse_sse_chunk(chunk))]


def test_flow_workorder_from_artifact_no_sql(monkeypatch) -> None:
    import asyncio

    parsed = asyncio.run(_collect_flow_events(monkeypatch))
    events = [item[0] for item in parsed]
    payloads = [item[1] for item in parsed]
    tools = [payload.get("tool") for payload in payloads if payload.get("type") == "tool_start"]
    complete = next(payload for payload in payloads if payload.get("type") == "chat_complete")

    assert "sql_db_query_checker" not in tools
    assert "sql_db_query" not in tools
    assert "query_knowledge_base" not in tools
    assert complete["decision"]["plan_mode"] == "workorder_decision_from_artifact"
    assert complete["resolved_context"]["relation_to_previous"] == "action_followup"
    assert complete["workorder_decision"]["status"] == "待确认"
    assert complete["resolved_context"]["stale_evidence"] is True
    assert complete["workflow_route"]["should_refresh_runtime_data"] is True
    assert any(
        event.get("stage") == "workorder_decision"
        for event in complete["trace"]["events"]
    )
    assert "数据已滞后" in complete["final_content"] or "采样窗口" in complete["final_content"]
    assert "刷新当前状态" in complete["final_content"]
    assert "已创建" not in complete["final_content"]
    assert "已派发" not in complete["final_content"]
    assert "complete" in events


def test_create_workorder_draft_from_pending_action(monkeypatch) -> None:
    import asyncio

    parsed = asyncio.run(_collect_create_draft_events(monkeypatch))
    payloads = [item[1] for item in parsed]
    complete = next(payload for payload in payloads if payload.get("type") == "chat_complete")

    assert any(goal["goal_type"] == "create_workorder_draft" for goal in complete["decision"]["goal_set"]["goals"])
    assert complete["workorder_decision"]["lifecycle_status"] == "draft_created"
    assert complete["workorder_draft"]["draft_id"].startswith("WOD-")
    assert complete["workorder_draft"]["status"] == "pending_verification"
    assert "建议生成" not in complete["final_content"]
    assert any(
        item["artifact_type"] == "workorder_draft"
        and item["created_in_current_turn"] is True
        and item["display_policy"] == "show_card"
        for item in complete["produced_artifacts"]
    )


def test_repeated_create_workorder_draft_reuses_existing_id(monkeypatch) -> None:
    import asyncio

    first = asyncio.run(_collect_create_draft_events(monkeypatch, message="那就生成吧"))
    first_complete = next(payload for _event, payload in first if payload.get("type") == "chat_complete")
    first_id = first_complete["workorder_draft"]["draft_id"]
    second = asyncio.run(_collect_create_draft_events(monkeypatch, message="现在生成", reset_store=False))
    second_complete = next(payload for _event, payload in second if payload.get("type") == "chat_complete")

    assert second_complete["workorder_draft"]["draft_id"] == first_id


def test_workorder_from_stale_artifact() -> None:
    suggestion = build_workorder_suggestion_from_artifact(
        envelope=_artifact(),
        decision=SingleAgentDecision(objects={"device_ids": ["G120电机1"], "alarm_codes": ["A07089"]}),
        user_identity="管理员",
    )

    assert "滞后" in suggestion.reason
    assert "直接派发" not in suggestion.reason
    assert any("滞后" in item or "采样窗口" in item for item in suggestion.key_evidence)
    assert suggestion.workorder_type in {"参数/配置核查工单", "运行异常确认工单"}
    assert suggestion.lifecycle_status == "recommended_draft"


def test_guardrail_sql_workorder_contradiction() -> None:
    result = build_output_guardrail_result(
        "建议生成待确认工单草稿。",
        None,
        SingleAgentDecision(),
        sql_artifact=SqlStepArtifact(success=True, summary="ok", result_preview="[{'device_name': 'J1'}]"),
        knowledge_artifact=KnowledgeStepArtifact(success=False, query="", raw_output=""),
        analysis_artifact=AnalysisStepArtifact(success=True, conclusion="ok"),
        workorder_suggestion=WorkOrderSuggestion(
            need_workorder=False,
            reason="SQL 未返回可解析运行数据，暂不自动生成工单。",
        ),
    )

    assert "sql_rows_contradict_workorder_no_data_reason" in result["warnings"]


def test_guardrail_blocks_reset_completion_claim() -> None:
    result = build_output_guardrail_result(
        "已复位设备并完成处理。",
        None,
        SingleAgentDecision(
            task_family="action_or_workorder",
            action_target="workorder",
            goal_set={"goals": [{"goal_type": "decide_workorder"}]},
        ),
    )

    assert "unsafe_action_execution_claim" in result["warnings"]
    assert result["passed"] is False


def test_new_diagnosis_then_workorder_without_artifact() -> None:
    configure_artifact_store_backend(MemoryArtifactStoreBackend())
    message = "要不要生成工单？"
    payload = fallback_understanding_payload(message, "管理员")
    decision = decide_capabilities(
        payload=payload,
        request=_request(message, payload),
        message=message,
        report_from_previous_artifact=False,
        conversation_state=load_conversation_diagnosis_state("thread.empty"),
    )

    assert decision.plan_mode == "new_diagnosis_then_workorder"
    assert decision.evidence_mode == "collect_new"
    assert "previous_diagnosis_or_report" in decision.missing_or_stale_evidence
    assert not decision.referenced_artifact_id
