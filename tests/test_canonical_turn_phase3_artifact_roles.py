from __future__ import annotations

from copy import deepcopy

from fault_diagnosis.agent import AgentEngineV2, ArtifactRoleBinding, PlanValidator
from fault_diagnosis.domain.canonical_turn import (
    CanonicalTurnRequest,
    GoalAuthorizationDecision,
    GoalReadinessDecision,
    GoalSourceResolution,
)
from fault_diagnosis.domain.security.permissions import build_auth_context


def _admin():
    return build_auth_context(user_id="phase3", role="admin")


def _snapshot(message: str, *, context: dict | None = None):
    return AgentEngineV2().build_plan_snapshot(
        raw_message=message,
        thread_id="thread.phase3.roles",
        request_id=f"request:{message}",
        auth_context=_admin(),
        conversation_context=context,
    )


def _validate(snapshot, plan):
    return PlanValidator().validate(
        candidate_plan=plan,
        request=CanonicalTurnRequest.model_validate(snapshot.metadata["canonical_request"]),
        authorization=[GoalAuthorizationDecision.model_validate(item) for item in snapshot.metadata["goal_authorization"]],
        readiness=[GoalReadinessDecision.model_validate(item) for item in snapshot.metadata["goal_readiness"]],
        sources=[GoalSourceResolution.model_validate(item) for item in snapshot.metadata["goal_source_resolution"]],
    )


def _analysis_plan():
    snapshot = _snapshot("诊断 G120电机1 当前是否存在故障")
    plan = snapshot.execution_plan.model_copy(deep=True)
    analysis = next(node for node in plan.nodes if node.node_type == "analysis")
    return snapshot, plan, analysis


def test_analysis_role_cardinality_zero_one_two_sql_and_optional_knowledge() -> None:
    snapshot, plan, analysis = _analysis_plan()
    sql_binding = deepcopy(analysis.inputs["artifact_role_bindings"][0])
    assert _validate(snapshot, plan).status == "validated"

    analysis.inputs["artifact_role_bindings"] = []
    assert {item.code for item in _validate(snapshot, plan).issues} >= {"analysis_sql_source_cardinality"}

    analysis.inputs["artifact_role_bindings"] = [sql_binding, deepcopy(sql_binding)]
    assert {item.code for item in _validate(snapshot, plan).issues} >= {"analysis_sql_source_cardinality"}

    knowledge = ArtifactRoleBinding(
        goal_id=analysis.goal_id,
        node_id=analysis.node_id,
        role="knowledge_source",
        artifact_id="knowledge:phase3:1",
        artifact_type="knowledge_artifact",
        required=False,
    ).model_dump(mode="json")
    analysis.inputs["artifact_role_bindings"] = [sql_binding, knowledge]
    assert _validate(snapshot, plan).status == "validated"

    second_knowledge = deepcopy(knowledge)
    second_knowledge["artifact_id"] = "knowledge:phase3:2"
    analysis.inputs["artifact_role_bindings"].append(second_knowledge)
    assert {item.code for item in _validate(snapshot, plan).issues} >= {"analysis_knowledge_source_cardinality"}


def test_sql_has_zero_artifact_inputs_and_all_planned_bindings_trace_to_dag_outputs() -> None:
    snapshot, plan, _ = _analysis_plan()
    sql = next(node for node in plan.nodes if node.node_type == "sql")
    assert sql.inputs["artifact_role_bindings"] == []
    assert sql.planned_output_artifact_id
    for node in plan.nodes:
        for binding in node.inputs["artifact_role_bindings"]:
            if not binding.get("producer_node_id"):
                continue
            producer = next(item for item in plan.nodes if item.node_id == binding["producer_node_id"])
            assert binding["artifact_id"] == producer.planned_output_artifact_id
            assert any(edge.source == producer.node_id and edge.target == node.node_id for edge in plan.edges)


def test_comparison_members_require_two_devices_and_stable_order() -> None:
    snapshot = _snapshot("比较 G120电机1 和 J1号机 当前运行状态")
    plan = snapshot.execution_plan.model_copy(deep=True)
    comparison = next(node for node in plan.nodes if node.node_type == "comparison")
    members = deepcopy(comparison.inputs["artifact_role_bindings"])
    assert [item["member_order"] for item in members] == [0, 1]
    assert [item["device_ref"] for item in members] == ["G120电机1", "J1号机"]
    assert _validate(snapshot, plan).status == "validated"

    comparison.inputs["artifact_role_bindings"] = members[:1]
    assert {item.code for item in _validate(snapshot, plan).issues} >= {"comparison_member_cardinality"}
    comparison.inputs["artifact_role_bindings"] = deepcopy(members)
    comparison.inputs["artifact_role_bindings"][1]["device_ref"] = None
    assert {item.code for item in _validate(snapshot, plan).issues} >= {"comparison_member_device_mapping"}
    comparison.inputs["artifact_role_bindings"] = deepcopy(members)
    comparison.inputs["artifact_role_bindings"][1]["member_order"] = 0
    assert {item.code for item in _validate(snapshot, plan).issues} >= {"comparison_member_order"}


def _manifest(
    artifact_id: str,
    artifact_type: str,
    *,
    snapshot: bool = False,
    sources: list[str] | None = None,
    tabular_source: str = "",
) -> dict:
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "thread_id": "thread.phase3.roles",
        "status": "completed",
        "artifact_status": "complete",
        "persistence_status": "committed",
        "readback_verified": True,
        "device_refs": ["G120电机1"],
        "freshness": "recent",
        "report_input_snapshot_schema_version": "report_input_snapshot.v1" if snapshot else "",
        "report_tabular_source_sql_artifact_id": tabular_source,
        "lineage": {
            "lineage_status": "complete",
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "subject_device_refs": ["G120电机1"],
            "source_artifact_ids": list(sources or []),
        },
    }


def test_report_source_is_exact_snapshot_analysis_and_legacy_analysis_is_blocked() -> None:
    current = _snapshot(
        "根据诊断结果生成报告",
        context={"artifact_manifests": [_manifest("analysis:phase3", "analysis_artifact", snapshot=True, sources=["sql:phase3"])]},
    )
    report = next(node for node in current.execution_plan.nodes if node.node_type == "report")
    assert [item["role"] for item in report.inputs["artifact_role_bindings"]] == ["report_source"]
    assert report.inputs["artifact_role_bindings"][0]["artifact_id"] == "analysis:phase3"
    assert _validate(current, current.execution_plan).status == "validated"

    with_tabular = _snapshot(
        "根据诊断结果生成带表格的报告",
        context={
            "artifact_manifests": [
                _manifest(
                    "analysis:tabular",
                    "analysis_artifact",
                    snapshot=True,
                    sources=["sql:phase3"],
                    tabular_source="sql:phase3",
                )
            ]
        },
    )
    tabular_report = next(node for node in with_tabular.execution_plan.nodes if node.node_type == "report")
    assert [item["role"] for item in tabular_report.inputs["artifact_role_bindings"]] == ["report_source", "tabular_source"]
    assert tabular_report.inputs["artifact_role_bindings"][1]["artifact_id"] == "sql:phase3"

    legacy = _snapshot(
        "根据诊断结果生成报告",
        context={"artifact_manifests": [_manifest("analysis:legacy", "analysis_artifact", sources=["sql:legacy"])]},
    )
    assert legacy.metadata["goal_source_resolution"][0]["status"] == "blocked"
    assert legacy.metadata["goal_source_resolution"][0]["reason_code"] == "legacy_analysis_missing_report_input_snapshot"
    assert legacy.execution_plan.nodes == []


def test_report_and_workorder_cardinality_reject_ambiguity_without_lineage_promotion() -> None:
    report_snapshot = _snapshot(
        "根据诊断结果生成报告",
        context={"artifact_manifests": [_manifest("analysis:phase3", "analysis_artifact", snapshot=True)]},
    )
    report_plan = report_snapshot.execution_plan.model_copy(deep=True)
    report = next(node for node in report_plan.nodes if node.node_type == "report")
    report.inputs["artifact_role_bindings"].append(deepcopy(report.inputs["artifact_role_bindings"][0]))
    assert {item.code for item in _validate(report_snapshot, report_plan).issues} >= {"report_source_cardinality"}

    workorder_snapshot = _snapshot(
        "根据报告生成工单草稿",
        context={"artifact_manifests": [_manifest("report:phase3", "report_artifact", sources=["analysis:ancestor"])]},
    )
    workorder_plan = workorder_snapshot.execution_plan.model_copy(deep=True)
    workorder = next(node for node in workorder_plan.nodes if node.node_type == "workorder")
    bindings = workorder.inputs["artifact_role_bindings"]
    assert len(bindings) == 1
    assert bindings[0]["artifact_id"] == "report:phase3"
    assert all(item["artifact_id"] != "analysis:ancestor" for item in bindings)
    bindings.append(deepcopy(bindings[0]))
    assert {item.code for item in _validate(workorder_snapshot, workorder_plan).issues} >= {"workorder_source_cardinality"}

    analysis_workorder = _snapshot(
        "根据诊断结果生成工单草稿",
        context={"artifact_manifests": [_manifest("analysis:workorder", "analysis_artifact", snapshot=True)]},
    )
    analysis_binding = next(
        item
        for node in analysis_workorder.execution_plan.nodes
        if node.node_type == "workorder"
        for item in node.inputs["artifact_role_bindings"]
    )
    assert analysis_binding["artifact_id"] == "analysis:workorder"
    assert analysis_binding["artifact_type"] == "analysis_artifact"


def test_blocked_report_source_does_not_block_independent_fault_code_goal() -> None:
    snapshot = _snapshot(
        "解释 A07089，并根据诊断结果生成报告",
        context={"artifact_manifests": [_manifest("analysis:legacy", "analysis_artifact")]},
    )
    readiness = {
        goal["capability"]: item["status"]
        for goal, item in zip(
            snapshot.metadata["canonical_request"]["goals"],
            snapshot.metadata["goal_readiness"],
            strict=True,
        )
    }
    assert readiness == {"explain_fault_code": "ready", "generate_report": "blocked_source"}
    assert [node.node_type for node in snapshot.execution_plan.nodes] == ["rag"]
