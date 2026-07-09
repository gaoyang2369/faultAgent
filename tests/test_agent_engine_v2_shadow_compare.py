from __future__ import annotations

import json

from fault_diagnosis.agent import AgentEngineV2, ExecutionPlan
from fault_diagnosis.agent.observability import build_plan_compare, record_plan_compare
from fault_diagnosis.domain.security.permissions import build_auth_context


def test_plan_compare_covers_required_surfaces_and_writes_jsonl(tmp_path) -> None:
    legacy = {
        "task_family": "runtime_status",
        "enabled_nodes": {"sql": True},
        "runtime_tools": ["sql_db_query"],
        "evidence_gaps": {"required_evidence": ["latest_runtime_status"]},
        "requested_output": "answer",
    }
    v2 = AgentEngineV2().build_plan_snapshot(
        raw_message="J1 当前运行状态怎么样",
        auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        legacy_plan=legacy,
    )

    record = build_plan_compare(
        legacy_plan=legacy,
        v2_snapshot=v2,
        request_id="request.compare",
        trace_id="trace.compare",
        thread_id="thread.compare",
        effective_skill_mode="shadow",
    )
    record_plan_compare(record, path=str(tmp_path / "compare.jsonl"))

    assert record["schema_version"] == "agent_engine_v2_compare.v1"
    assert record["primary_skill"] == "runtime_status"
    assert set(record["legacy"]) >= {"intent", "skill", "nodes", "tools", "evidence_requirements", "risk", "approval"}
    assert set(record["v2"]) >= {"intent", "skill", "nodes", "tools", "evidence_requirements", "risk", "approval"}
    written = json.loads((tmp_path / "compare.jsonl").read_text(encoding="utf-8").strip())
    assert written["request_id"] == "request.compare"


def test_plan_compare_marks_dangerous_tool_difference_for_review() -> None:
    legacy = {
        "task_family": "diagnosis",
        "enabled_nodes": {"sql": True},
        "runtime_tools": ["device_control.write"],
    }
    candidate = ExecutionPlan(
        plan_id="plan.compare.danger",
        plan_version="v2.compare.validated",
        goals=[{"skill": "workorder_decision"}],
        nodes=[{"node_id": "workorder_1", "node_type": "workorder"}],
        allowed_tools=["workorder.create"],
        required_evidence=["diagnosis_summary"],
        risk_level="high",
        approval_requirements=[],
    )
    v2 = AgentEngineV2().build_plan_snapshot(
        raw_message="判断 J1 是否需要工单",
        auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
        llm_candidate_plan=candidate,
        legacy_plan=legacy,
    )

    record = build_plan_compare(
        legacy_plan=legacy,
        v2_snapshot=v2,
        effective_skill_mode="shadow",
    )

    assert record["severity"] == "critical"
    assert record["review_required"] is True
    assert any(item["surface"] == "tools" for item in record["diffs"])
