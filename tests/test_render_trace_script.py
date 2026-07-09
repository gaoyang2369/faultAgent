from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fault_diagnosis.platform.observability.trace_rendering import render_compact_summary


def test_render_trace_latest_summary_markdown_and_pretty_json(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "render_trace.py"
    env = {**os.environ, "PYTHONPATH": str(repo_root)}
    trace_id = "trace.render"
    record = {
        "metadata": {"trace_id": trace_id, "request_id": "request.render", "status": "completed"},
        "trace": {
            "schema_version": "agent_trace.v1",
            "trace_id": trace_id,
            "request_id": "request.render",
            "thread_id": "thread.render",
            "status": "completed",
            "duration_ms": 12.3,
            "metadata": {},
            "spans": [
                {
                    "name": "chat.request",
                    "kind": "request",
                    "attributes": {"message": {"chars": 15, "sha256_12": "abc123"}},
                },
                {
                    "name": "goal.build",
                    "kind": "planner",
                    "attributes": {"task_family": "knowledge_lookup"},
                },
                {
                    "name": "skill.route",
                    "kind": "planner",
                    "attributes": {"primary_skill": "fault_code_explain", "selected_skills": ["fault_code_explain"]},
                },
                {
                    "name": "context.resolve",
                    "kind": "planner",
                    "attributes": {"relation_to_previous": "new_case", "reuse_decision": "collect_new"},
                },
                {
                    "name": "node.rag",
                    "kind": "node",
                    "status": "completed",
                    "duration_ms": 7.0,
                    "attributes": {"node_id": "rag_1"},
                },
                {
                    "name": "tool.kb.search",
                    "kind": "tool",
                    "duration_ms": 1501.0,
                    "attributes": {
                        "match_type": "exact_match",
                        "retrieval_mode": "fault_code_exact_match",
                        "hit_count": 1,
                        "latency_ms": 1501.0,
                        "phase_latencies_ms": {"total": 1501.0},
                        "selected_sources": [{"file": "S120_故障手册.pdf", "page": "232"}],
                    },
                },
                {
                    "name": "evidence.ledger",
                    "kind": "evidence",
                    "attributes": {"evidence_count": 1, "claim_count": 1, "final_claim_ids": ["claim_1"], "warnings": []},
                },
                {
                    "name": "guardrail.check",
                    "kind": "guardrail",
                    "attributes": {"blocked": False, "missing_evidence": [], "stale_evidence": []},
                },
                {
                    "name": "answer.render",
                    "kind": "output",
                    "attributes": {
                        "answer_template": "fault_code_concise_v1",
                        "llm_used": False,
                        "citation_count": 1,
                        "answer_length": 210,
                    },
                },
            ],
            "events": [],
            "errors": [],
        },
        "runtime_events": [{"event_type": "node_status", "node_id": "rag_1"}],
        "legacy_runtime_events": [{"event_type": "node_status", "node_id": "rag_1"}],
    }
    trace_path = tmp_path / "agent-trace.jsonl"
    trace_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    out_dir = tmp_path / "traces"

    summary = subprocess.run(
        [sys.executable, str(script), str(trace_path), "--format", "summary"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert summary.strip() == render_compact_summary(record, trace_path=str(trace_path))
    assert "[AgentTrace] completed 12.3ms request_id=request.render trace_id=trace.render" in summary
    assert "User: chars=15 sha256_12=abc123" in summary
    assert "Route: knowledge_lookup / fault_code_explain" in summary
    assert "Context: new_case reuse=collect_new" in summary
    assert "Plan: rag_1 completed 7ms" in summary
    assert "Tool: kb.search exact_match mode=fault_code_exact_match hit=1 source=S120_故障手册.pdf#232 latency=1501ms phases=total:1501ms" in summary
    assert "Evidence: evidence=1 claim=1 final_claim=1 guardrail=pass" in summary
    assert "Output: fault_code_concise_v1 llm_used=false answer_len=210" in summary
    assert "Slowest: tool.kb.search 1501ms" in summary
    assert "Trace: " in summary
    assert "Warnings: fault_code_exact_match_slow" in summary

    markdown_path = subprocess.run(
        [sys.executable, str(script), str(trace_path), "--format", "markdown", "--out-dir", str(out_dir)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    pretty_path = subprocess.run(
        [sys.executable, str(script), str(trace_path), "--format", "pretty-json", "--out-dir", str(out_dir)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert markdown_path.endswith("trace_trace.render.md")
    assert pretty_path.endswith("trace_trace.render.pretty.json")
    assert (out_dir / "trace_trace.render.md").exists()
    assert (out_dir / "trace_trace.render.pretty.json").exists()

    latest_dir = tmp_path / "latest"
    latest_trace_path = tmp_path / "trash" / "run"
    latest_trace_path.mkdir(parents=True)
    (latest_trace_path / "agent-trace.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    latest = subprocess.run(
        [sys.executable, str(script), "--latest", "--format", "summary"],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
    ).stdout
    assert latest.startswith("[AgentTrace] completed")
    latest_markdown = subprocess.run(
        [sys.executable, str(script), "--latest", "--format", "markdown", "--out-dir", str(latest_dir)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
    ).stdout.strip()
    latest_pretty = subprocess.run(
        [sys.executable, str(script), "--latest", "--format", "pretty-json", "--out-dir", str(latest_dir)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
    ).stdout.strip()
    assert latest_markdown.endswith("trace_trace.render.md")
    assert latest_pretty.endswith("trace_trace.render.pretty.json")
