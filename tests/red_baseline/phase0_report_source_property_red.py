"""Combinatorial report-source expressions for the Phase 0 red baseline."""

from __future__ import annotations

import json
from itertools import product

from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest
from fault_diagnosis.agent.engine import AgentEngineV2
from fault_diagnosis.domain.security.permissions import build_auth_context


THREAD_ID = "thread.phase0.report-property"
ANALYSIS_ID = "analysis:report-source"


def _expressions() -> list[str]:
    source_forms = (
        ("基于", "刚才的", "诊断结果"),
        ("根据", "上一轮的", "诊断结果"),
        ("使用", "前面的", "分析结果"),
        ("用", "这个", "诊断"),
    )
    report_actions = (("生成", "报告"), ("生成", "运行报告"), ("整理成", "报告"))
    return [f"{prep}{deictic}{source}{verb}{target}" for (prep, deictic, source), (verb, target) in product(source_forms, report_actions)]


def _context() -> dict:
    manifest = ArtifactManifest(
        artifact_id=ANALYSIS_ID,
        artifact_type="analysis_artifact",
        thread_id=THREAD_ID,
        status="completed",
        artifact_status="complete",
        persistence_status="committed",
        readback_verified=True,
        followupable=True,
        reportable=True,
        device_refs=["G120电机1"],
        owner_user_id="admin-phase0",
        owner_session_id="session-phase0",
        freshness="fresh",
        report_input_snapshot_schema_version="report_input_snapshot.v1",
        report_tabular_source_sql_artifact_id="sql:report-source",
        lineage=ArtifactLineage(
            lineage_status="complete",
            artifact_id=ANALYSIS_ID,
            artifact_type="analysis_artifact",
            subject_device_refs=["G120电机1"],
            source_artifact_ids=["sql:report-source"],
            created_from_goal_ids=["goal_previous_diagnosis"],
        ),
    )
    return {"artifact_manifests": [manifest.model_dump(mode="json")]}


def _snapshots():
    auth = build_auth_context(user_id="admin-phase0", role="admin", session_id="session-phase0")
    for message in _expressions():
        yield message, AgentEngineV2().build_plan_snapshot(
            raw_message=message,
            thread_id=THREAD_ID,
            auth_context=auth,
            conversation_context=_context(),
        )


def test_report_source_grammar_produces_only_report_action() -> None:
    violations = {
        message: snapshot.effective_request_frame.requested_goals
        for message, snapshot in _snapshots()
        if snapshot.effective_request_frame.requested_goals != ["generate_report"]
    }

    assert not violations, json.dumps(violations, ensure_ascii=False, indent=2)


def test_report_source_grammar_never_recomputes_sql_or_analysis() -> None:
    violations = {
        message: [node.node_type for node in snapshot.execution_plan.nodes]
        for message, snapshot in _snapshots()
        if [node.node_type for node in snapshot.execution_plan.nodes] != ["report"]
    }

    assert not violations, json.dumps(violations, ensure_ascii=False, indent=2)


def test_report_source_binding_belongs_only_to_report_goal() -> None:
    violations = {}
    for message, snapshot in _snapshots():
        bindings = snapshot.effective_request_frame.source_bindings
        report_goal_ids = {
            goal.goal_id
            for goal in snapshot.effective_request_frame.requested_goal_set.goals
            if goal.capability == "generate_report"
        }
        observed = [(item.goal_id, item.artifact_id, item.artifact_type) for item in bindings]
        if len(bindings) != 1 or bindings[0].goal_id not in report_goal_ids or bindings[0].artifact_id != ANALYSIS_ID:
            violations[message] = observed

    assert not violations, json.dumps(violations, ensure_ascii=False, indent=2)
