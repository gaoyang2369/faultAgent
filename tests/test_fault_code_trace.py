from __future__ import annotations

from fault_diagnosis.agent import AgentEngineV2, WorkflowRuntimeExecutor
from fault_diagnosis.agent.runtime.plan_preparer import prepare_v2_execution_plan
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.observability import TraceRecorder


class FaultCodeToolRuntime:
    def query_knowledge_base(self, query: str) -> str:  # noqa: ARG002
        return (
            "故障码：A07089\n"
            "来源：基础PDF知识库\n"
            "来源文件：S120_故障手册.pdf\n"
            "检索方式：故障码精确匹配\n"
            "来源页码：232\n"
            "文档片段：A07089 单位转换： 转换单位后不能激活功能块\n"
            "信息类别： 参数设置 / 配置 / 调试过程出错 (18)\n"
            "原因： 尝试激活功能块。转换单位后不允许此操作。\n"
            "处理： 将单位恢复到出厂设置。\n"
            "参见： p0100, p0349, p0505"
        )

    def invoke_sql_tool(self, tool_name: str, payload):  # noqa: ANN001, ARG002
        return []

    def save_report(self, **kwargs):  # noqa: ANN003
        return ""


def test_fault_code_query_trace_contains_retrieval_parse_answer_and_guardrail() -> None:
    auth = build_auth_context(role="guest")
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="A07089 是什么意思",
        thread_id="thread.fault_code",
        request_id="request.fault_code",
        auth_context=auth,
    )
    plan = prepare_v2_execution_plan(snapshot=snapshot, thread_id="thread.fault_code", auth_context=auth)
    result = WorkflowRuntimeExecutor(real_tools=True, tool_runtime=FaultCodeToolRuntime()).execute(
        plan,
        trace_id="trace.fault_code",
        thread_id="thread.fault_code",
        request_id="request.fault_code",
        auth_context=auth,
    )
    recorder = TraceRecorder(trace_id="trace.fault_code", request_id="request.fault_code", thread_id="thread.fault_code")
    recorder.add_plan_snapshot(snapshot)
    recorder.add_runtime_result(plan=plan, result=result)
    envelope = recorder.finish(status=result.status)
    by_name = {span.name: span for span in envelope.spans}

    assert by_name["goal.build"].attributes["task_family"] == "knowledge_lookup"
    assert any(goal["goal_type"] == "explain_fault_code" for goal in by_name["goal.build"].attributes["goals"])
    assert by_name["tool.kb.search"].attributes["match_type"] == "exact_match"
    assert by_name["tool.kb.search"].duration_ms > 0
    assert by_name["tool.kb.search"].attributes["latency_ms"] > 0
    assert by_name["tool.kb.search"].attributes["selected_sources"][0]["file"] == "S120_故障手册.pdf"
    assert by_name["tool.kb.search"].attributes["selected_sources"][0]["page"] == "232"
    assert by_name["fault_code.entry_parse"].attributes["code"] == "A07089"
    assert by_name["fault_code.entry_parse"].attributes["has_cause"] is True
    assert by_name["fault_code.entry_parse"].attributes["has_remedy"] is True
    assert by_name["answer.render"].attributes["answer_template"] == "fault_code_concise_v1"
    assert by_name["answer.render"].attributes["citation_count"] >= 1
    assert by_name["guardrail.check"].attributes["blocked"] is False
    assert by_name["evidence.ledger"].attributes["evidence_count"] >= 1
    assert by_name["evidence.ledger"].attributes["claim_count"] >= 1
    assert by_name["evidence.ledger"].attributes["final_claim_ids"]

    claim = result.evidence_ledger.claims[0]
    evidence_ids = {item["evidence_id"] for item in result.evidence_ledger.evidence_items}
    assert claim["claim_type"] == "fault_code_explanation"
    assert claim["supporting_evidence_ids"]
    assert set(claim["supporting_evidence_ids"]).issubset(evidence_ids)

    rag_span = by_name["node.rag"]
    running = next(event for event in rag_span.events if event.name == "node.running")
    completed = next(event for event in rag_span.events if event.name == "node.completed")
    assert rag_span.start_time == running.timestamp
    assert rag_span.end_time == completed.timestamp
    assert rag_span.duration_ms > 0
