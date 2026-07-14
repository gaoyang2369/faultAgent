from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fault_diagnosis.agent import AgentEngineV2
from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator, CurrentUtteranceParser
from fault_diagnosis.agent.canonical_turn.context_binding import project_authorized_context_candidates
from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest
from fault_diagnosis.domain.canonical_turn import TurnCommand
from fault_diagnosis.domain.security.permissions import build_auth_context


def _manifest(
    artifact_id: str,
    artifact_type: str,
    devices: list[str],
    *,
    fault_codes: list[str] | None = None,
    status: str = "completed",
    freshness: str = "fresh",
    reportable: bool = False,
    owner: str = "",
    snapshot: bool = False,
) -> dict:
    complete = status == "completed"
    return ArtifactManifest(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        thread_id="thread.phase3a",
        status=status,
        artifact_status="complete" if complete else "failed",
        persistence_status="committed" if complete else "failed",
        readback_verified=complete,
        device_refs=devices,
        fault_code_refs=fault_codes or [],
        freshness=freshness,
        reportable=reportable,
        owner_user_id=owner,
        report_input_snapshot_schema_version="report_input_snapshot.v1" if snapshot else "",
        lineage=ArtifactLineage(
            lineage_status="complete" if complete else "invalid",
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            subject_device_refs=devices,
            fault_code_refs=fault_codes or [],
        ),
    ).model_dump(mode="json")


def _context(*manifests: dict, previous: list[str] | None = None, case: dict | None = None, messages=None, context_unavailable=False) -> dict:
    return {
        "artifact_manifests": list(manifests),
        "immediately_previous_assistant_turn": (
            {"message_id": "assistant.previous", "produced_artifacts": [{"artifact_id": item} for item in previous or []], "context_unavailable": context_unavailable}
            if previous is not None else {}
        ),
        "latest_case_state": case,
        "last_raw_messages": messages or [],
    }


def _preview(message: str, context: dict | None = None, *, role="admin", user_id="user.phase3a"):
    command = TurnCommand(
        command="preview",
        thread_id="thread.phase3a",
        user_id=user_id,
        turn_id=f"turn:{message}",
        message_id=f"message:{message}",
        idempotency_key=f"key:{message}",
        raw_message=message,
    )
    auth = build_auth_context(
        user_id=user_id,
        role=role,
        asset_scope=["G120电机1"] if role == "engineer" else None,
        table_scope=["real_data_01"] if role == "engineer" else None,
    )
    return ConversationTurnCoordinator().preview_turn(command, auth_context=auth, conversation_context=context)


def _binding(result, index=0):
    assert result.bound_turn is not None
    return result.bound_turn.goal_bindings[index]


def test_explicit_device_and_correction_override_context() -> None:
    old = _manifest("sql.old", "sql_artifact", ["G120电机1"])
    explicit = _binding(_preview("查询 G120电机2 当前状态", _context(old, previous=["sql.old"])))
    corrected = _binding(_preview("不是G120电机1，是G120电机2，查询当前状态", _context(old, previous=["sql.old"])))
    assert explicit.asset_refs == ["G120电机2"]
    assert corrected.asset_refs == ["G120电机2"]
    assert corrected.slots[0].provenance == "explicit_correction"


def test_single_focus_inherits_and_alias_is_normalized() -> None:
    sql = _manifest("sql.j1", "sql_artifact", ["J1号机"])
    result = _preview("分析一下是否存在异常", _context(sql, previous=["sql.j1"]))
    assert _binding(result).asset_refs == ["G120电机1"]
    assert result.source_resolutions[0].artifact_id == "sql.j1"


@pytest.mark.parametrize("message", ["它有没有故障？", "这个设备是否异常？"])
def test_singular_reference_after_comparison_requires_safe_clarification(message: str) -> None:
    comparison = _manifest("comparison.two", "comparison_artifact", ["G120电机1", "G120电机2"])
    binding = _binding(_preview(message, _context(comparison, previous=["comparison.two"])))
    assert binding.binding_status == "needs_clarification"
    assert binding.clarification.reason_code == "ambiguous_asset_reference"
    assert [item.asset_ref for item in binding.clarification.options] == ["G120电机1", "G120电机2"]


def test_plural_reference_binds_all_supported_assets() -> None:
    comparison = _manifest("comparison.two", "comparison_artifact", ["G120电机1", "G120电机2"])
    binding = _binding(_preview("它们分别有没有异常？", _context(comparison, previous=["comparison.two"])))
    assert binding.binding_status == "bound"
    assert binding.asset_refs == ["G120电机1", "G120电机2"]


def test_fault_code_detail_followup_inherits_structured_code() -> None:
    knowledge = _manifest("knowledge.a07089", "knowledge_artifact", [], fault_codes=["A07089"])
    result = _preview("详细点", _context(knowledge, previous=["knowledge.a07089"]))
    assert result.request.goals[0].capability == "explain_fault_code"
    assert _binding(result).fault_codes == ["A07089"]
    assert result.source_resolutions[0].artifact_id == "knowledge.a07089"


def test_time_correction_inherits_device_and_overrides_window() -> None:
    sql = _manifest("sql.window", "sql_artifact", ["G120电机1"])
    binding = _binding(_preview("改成最近两小时", _context(sql, previous=["sql.window"])))
    assert binding.asset_refs == ["G120电机1"]
    assert binding.time_window == {"raw": "最近两小时"}
    assert binding.slots[2].status == "explicit"


def test_immediately_previous_compatible_artifact_wins() -> None:
    old = _manifest("sql.old", "sql_artifact", ["G120电机1"])
    latest = _manifest("sql.previous", "sql_artifact", ["G120电机2"])
    result = _preview("继续分析是否异常", _context(old, latest, previous=["sql.previous"]))
    assert _binding(result).source_artifact_refs == ["sql.previous"]


def test_compatibility_matrix_prefers_runtime_input_for_diagnosis() -> None:
    analysis = _manifest("analysis.old", "analysis_artifact", ["G120电机1"], snapshot=True)
    sql = _manifest("sql.old", "sql_artifact", ["G120电机1"])
    assert _binding(_preview("分析是否异常", _context(analysis, sql))).source_artifact_refs == ["sql.old"]


@pytest.mark.parametrize(
    "manifest",
    [
        _manifest("knowledge.bad", "knowledge_artifact", [], fault_codes=["A07089"]),
        _manifest("analysis.failed", "analysis_artifact", ["G120电机1"], status="failed"),
    ],
)
def test_incompatible_or_failed_artifact_is_never_report_source(manifest: dict) -> None:
    result = _preview("生成运行报告", _context(manifest))
    assert _binding(result).source_artifact_refs == []
    assert result.source_resolutions[0].status == "unresolved"


def test_acl_filters_unauthorized_artifact_before_binding() -> None:
    denied = _manifest("report.admin", "report_artifact", ["G120电机2"], owner="admin.other")
    context = _context(denied, previous=["report.admin"])
    result = _preview("根据刚才报告创建工单草稿", context, role="engineer", user_id="engineer.one")
    assert _binding(result).source_artifact_refs == []
    assert _binding(result).clarification.question == "请先提供可用的诊断、分析或报告结果。"
    assert "G120电机2" not in _binding(result).clarification.question


def test_pending_action_binds_only_while_valid() -> None:
    report = _manifest("report.pending", "report_artifact", ["G120电机1"])
    action = {
        "action_type": "workorder_draft",
        "status": "pending",
        "source_report_artifact_id": "report.pending",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }
    context = _context(report, case={"case_id": "case.one", "active_asset": "G120电机1", "latest_artifact_id": "report.pending", "latest_artifact_type": "report_artifact", "pending_actions": [action]})
    candidates = project_authorized_context_candidates(context, build_auth_context(user_id="user.phase3a", role="admin"))
    assert any(item.source_kind == "pending_action" for item in candidates)
    action["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    expired = project_authorized_context_candidates(context, build_auth_context(user_id="user.phase3a", role="admin"))
    assert not any(item.source_kind == "pending_action" for item in expired)


def test_report_accepts_analysis_or_reportable_status_only() -> None:
    analysis = _manifest("analysis.report", "analysis_artifact", ["G120电机1"], snapshot=True)
    reportable = _manifest("sql.reportable", "sql_artifact", ["G120电机1"], reportable=True)
    not_reportable = _manifest("sql.no-report", "sql_artifact", ["G120电机1"])
    assert _binding(_preview("生成报告", _context(analysis))).source_artifact_refs == ["analysis.report"]
    assert _binding(_preview("导出报告", _context(reportable))).source_artifact_refs == ["sql.reportable"]
    assert _binding(_preview("导出报告", _context(not_reportable))).source_artifact_refs == []


@pytest.mark.parametrize("artifact_type", ["analysis_artifact", "report_artifact"])
def test_workorder_binds_only_diagnosis_lineage_sources(artifact_type: str) -> None:
    source = _manifest(f"{artifact_type}.workorder", artifact_type, ["G120电机1"], snapshot=artifact_type == "analysis_artifact")
    result = _preview("根据这个结果创建工单草稿", _context(source, previous=[f"{artifact_type}.workorder"]))
    assert _binding(result).source_artifact_refs == [f"{artifact_type}.workorder"]
    assert result.source_resolutions[0].status == "source_for_execution"


def test_runtime_status_refreshes_by_default() -> None:
    sql = _manifest("sql.previous", "sql_artifact", ["G120电机1"])
    result = _preview("查询 G120电机1 当前状态", _context(sql, previous=["sql.previous"]))
    assert _binding(result).source_artifact_refs == []
    assert result.source_resolutions[0].status == "requires_execution"


def test_no_requery_diagnosis_reuses_previous_status_without_sql_goal() -> None:
    sql = _manifest("sql.previous", "sql_artifact", ["G120电机1"])
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message="无需重新查询，基于刚才结果说明是否异常",
        thread_id="thread.phase3a",
        auth_context=build_auth_context(user_id="user.phase3a", role="admin"),
        conversation_context=_context(sql, previous=["sql.previous"]),
    )
    assert [node.node_type for node in snapshot.execution_plan.nodes] == ["analysis"]


def test_stale_source_requires_refresh() -> None:
    stale = _manifest("sql.stale", "sql_artifact", ["G120电机1"], freshness="stale")
    result = _preview("分析是否异常", _context(stale, previous=["sql.stale"]))
    assert _binding(result).binding_status == "refresh_required"
    assert result.source_resolutions[0].status == "stale"


def test_assistant_text_is_never_projected_as_evidence() -> None:
    context = _context(messages=[{"role": "assistant", "content": "设备故障，建议生成工单"}])
    result = _preview("根据刚才结果生成报告", context)
    assert _binding(result).source_artifact_refs == []
    assert result.source_resolutions[0].status == "unresolved"


def test_source_resolver_cannot_override_binder_selection() -> None:
    selected = _manifest("sql.selected", "sql_artifact", ["G120电机1"])
    other = _manifest("sql.other", "sql_artifact", ["G120电机2"])
    result = _preview("继续分析", _context(other, selected, previous=["sql.selected"]))
    assert _binding(result).source_artifact_refs == ["sql.selected"]
    assert result.source_resolutions[0].artifact_id == "sql.selected"


def test_explicit_missing_artifact_does_not_fall_back() -> None:
    other = _manifest("analysis.other", "analysis_artifact", ["G120电机1"], snapshot=True)
    result = _preview("基于 analysis:missing 生成报告", _context(other))
    assert _binding(result).source_artifact_refs == []
    assert _binding(result).slots[3].status == "unavailable"


def test_previous_denied_or_artifactless_turn_does_not_fall_back_to_older_source() -> None:
    older = _manifest("report.older", "report_artifact", ["G120电机1"])
    result = _preview("继续分析这个报告", _context(older, previous=[], context_unavailable=True))
    assert _binding(result).source_artifact_refs == []
    assert _binding(result).clarification.reason_code == "missing_source"


def test_parser_remains_history_free_and_legacy_frame_is_projection_only() -> None:
    first = CurrentUtteranceParser().parse("详细点")
    second = CurrentUtteranceParser().parse("详细点")
    assert first == second and first.model_used is False
    engine = (AgentEngineV2.__module__, AgentEngineV2.__doc__)
    assert engine[0] == "fault_diagnosis.agent.engine"
    result = _preview("查询 G120电机1 当前状态")
    assert result.bound_turn.canonical_turn is result.request
