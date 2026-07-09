from __future__ import annotations

from pathlib import Path
from typing import Any

from fault_diagnosis.platform.observability import trace_exporters
from fault_diagnosis.platform.observability.trace_exporters import write_console_trace_envelope, write_trace_side_artifacts
from fault_diagnosis.platform.observability.trace_schema import TraceEnvelope, TraceEvent, TraceSpan


class CapturingLog:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def info(self, message: str, **kwargs: Any) -> None:
        self.infos.append((message, kwargs))

    def warning(self, message: str, **kwargs: Any) -> None:
        self.warnings.append((message, kwargs))


def test_console_summary_only_does_not_emit_span_info(monkeypatch) -> None:
    log = CapturingLog()
    monkeypatch.setattr(trace_exporters, "_log", log)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE", True)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE_SUMMARY", True)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE_SPANS", False)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE_VERBOSE", False)

    write_console_trace_envelope(_envelope(), trace_path="trash/run/agent-trace.jsonl")

    assert len(log.infos) == 1
    message, kwargs = log.infos[0]
    assert kwargs == {}
    assert "[AgentTrace] completed 7104ms request_id=request.console trace_id=trace.console" in message
    assert "Plan: rag_1 completed 7014ms; kg_1 skipped:not_configured" in message
    assert "Evidence: evidence=1 claim=1 final_claim=1 guardrail=pass" in message
    assert "Slowest: tool.kb.search 7013ms" in message
    assert "Warnings: fault_code_exact_match_slow" in message
    assert "event_count=0" not in message
    assert "thread.console.full" not in message
    assert "stream.console" not in message


def test_console_spans_true_restores_span_info(monkeypatch) -> None:
    log = CapturingLog()
    monkeypatch.setattr(trace_exporters, "_log", log)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE", True)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE_SUMMARY", True)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE_SPANS", True)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE_VERBOSE", False)

    write_console_trace_envelope(_envelope())

    messages = [item[0] for item in log.infos]
    assert messages[0].startswith("[AgentTrace]")
    assert "goal.build" in messages
    assert "tool.kb.search" in messages


def test_console_verbose_allows_full_attributes(monkeypatch) -> None:
    log = CapturingLog()
    monkeypatch.setattr(trace_exporters, "_log", log)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE", True)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE_SUMMARY", False)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE_SPANS", True)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_CONSOLE_VERBOSE", True)

    write_console_trace_envelope(_envelope())

    span_logs = [kwargs for _message, kwargs in log.infos if kwargs.get("stage") == "goal.build"]
    assert span_logs
    assert "'task_family': 'knowledge_lookup'" in span_logs[0]["summary"]
    assert span_logs[0]["thread_id"] == "thread.console.full"


def test_trace_side_artifacts_generate_pretty_json_and_markdown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_PRETTY_JSON", True)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_MARKDOWN", True)
    monkeypatch.setattr(trace_exporters, "AGENT_TRACE_LOCAL_LOG_PATH", str(tmp_path / "agent-trace.jsonl"))

    paths = write_trace_side_artifacts(_envelope(), trace_path=str(tmp_path / "agent-trace.jsonl"))

    assert Path(paths["pretty_json_path"]).name == "trace_trace.console.pretty.json"
    assert Path(paths["markdown_path"]).name == "trace_trace.console.md"
    assert Path(paths["pretty_json_path"]).exists()
    assert Path(paths["markdown_path"]).exists()


def _envelope() -> TraceEnvelope:
    return TraceEnvelope(
        trace_id="trace.console",
        request_id="request.console",
        thread_id="thread.console.full",
        stream_id="stream.console",
        status="completed",
        duration_ms=7104.4,
        spans=[
            TraceSpan(
                trace_id="trace.console",
                span_id="span.chat.request",
                name="chat.request",
                kind="request",
                status="completed",
                duration_ms=7104.4,
                attributes={"message": {"preview": "A07089 是什么意思"}},
            ),
            TraceSpan(
                trace_id="trace.console",
                span_id="span.plan.goal",
                name="goal.build",
                kind="planner",
                attributes={"task_family": "knowledge_lookup", "primary_goal": "explain_fault_code"},
            ),
            TraceSpan(
                trace_id="trace.console",
                span_id="span.plan.skill",
                name="skill.route",
                kind="planner",
                attributes={"primary_skill": "fault_code_explain", "selected_skills": ["fault_code_explain"]},
            ),
            TraceSpan(
                trace_id="trace.console",
                span_id="span.plan.context",
                name="context.resolve",
                kind="planner",
                attributes={"relation_to_previous": "new_case", "reuse_decision": "collect_new"},
            ),
            TraceSpan(
                trace_id="trace.console",
                span_id="span.node.rag_1",
                name="node.rag",
                kind="node",
                status="completed",
                duration_ms=7013.6,
                attributes={"node_id": "rag_1", "node_type": "rag"},
                events=[TraceEvent(trace_id="trace.console", name="node.completed", timestamp="2026-07-09T07:23:17+00:00")],
            ),
            TraceSpan(
                trace_id="trace.console",
                span_id="span.node.rag_1.tool.kb.search",
                parent_span_id="span.node.rag_1",
                name="tool.kb.search",
                kind="tool",
                status="completed",
                duration_ms=7013.0,
                attributes={
                    "match_type": "exact_match",
                    "retrieval_mode": "fault_code_exact_match",
                    "hit_count": 1,
                    "latency_ms": 7013.0,
                    "phase_latencies_ms": {"total": 7013.0},
                    "selected_sources": [{"file": "S120_故障手册.pdf", "page": "232"}],
                },
            ),
            TraceSpan(
                trace_id="trace.console",
                span_id="span.node.kg_1",
                name="node.kg",
                kind="node",
                status="skipped",
                duration_ms=0.0,
                attributes={"node_id": "kg_1", "node_type": "kg", "reason": "not_configured"},
            ),
            TraceSpan(
                trace_id="trace.console",
                span_id="span.evidence.ledger",
                name="evidence.ledger",
                kind="evidence",
                attributes={"evidence_count": 1, "claim_count": 1, "final_claim_ids": ["claim_1"], "warnings": []},
            ),
            TraceSpan(
                trace_id="trace.console",
                span_id="span.answer.render",
                name="answer.render",
                kind="output",
                attributes={"answer_template": "fault_code_concise_v1", "llm_used": False, "answer_length": 210},
            ),
            TraceSpan(
                trace_id="trace.console",
                span_id="span.guardrail.check",
                name="guardrail.check",
                kind="guardrail",
                attributes={"blocked": False, "missing_evidence": [], "stale_evidence": []},
            ),
        ],
    )
