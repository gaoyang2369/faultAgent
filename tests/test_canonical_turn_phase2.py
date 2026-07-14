from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator
from fault_diagnosis.agent.canonical_turn.compatibility import project_legacy_frames_from_canonical
from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest
from fault_diagnosis.agent.planning import PlanCompiler, PlanValidator
from fault_diagnosis.agent.skills import SkillRouter
from fault_diagnosis.domain.canonical_turn import CanonicalGoal, TurnCommand, TurnEvent
from fault_diagnosis.domain.canonical_turn.contracts import GoalProvenance
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.persistence.repositories.pending_clarification_repository import (
    MemoryPendingClarificationRepository,
)
from fault_diagnosis.platform.persistence.repositories.conversation_store import MemoryConversationRepository
from fault_diagnosis.server.use_cases.conversation_persistence import parse_sse_payloads
from fault_diagnosis.server.use_cases.turn_execution import ProductionTurnCoordinator


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)


def _command(
    message: str,
    *,
    channel: str = "text",
    turn_id: str = "turn-1",
    message_id: str = "message-1",
    key: str = "key-1",
) -> TurnCommand:
    return TurnCommand(
        command="execute",
        thread_id="thread-phase2",
        user_id="user-phase2",
        turn_id=turn_id,
        message_id=message_id,
        idempotency_key=key,
        raw_message=message,
        channel=channel,
    )


def _admin():
    return build_auth_context(user_id="user-phase2", role="admin")


def _guest():
    return build_auth_context(user_id="user-phase2", role="guest")


def _analysis_context() -> dict:
    manifest = ArtifactManifest(
        artifact_id="analysis:phase2",
        artifact_type="analysis_artifact",
        thread_id="thread-phase2",
        status="completed",
        artifact_status="complete",
        persistence_status="committed",
        readback_verified=True,
        device_refs=["G120电机1"],
        freshness="fresh",
        report_input_snapshot_schema_version="report_input_snapshot.v1",
        lineage=ArtifactLineage(
            lineage_status="complete",
            artifact_id="analysis:phase2",
            artifact_type="analysis_artifact",
            subject_device_refs=["G120电机1"],
        ),
    )
    return {"artifact_manifests": [manifest.model_dump(mode="json")]}


def _compile(result):
    route = SkillRouter().route(
        request=result.request,
        authorization=result.authorization,
        readiness=result.readiness,
        sources=result.source_resolutions,
    )
    plan = PlanCompiler().compile(
        request=result.request,
        authorization=result.authorization,
        readiness=result.readiness,
        sources=result.source_resolutions,
    )
    validation = PlanValidator().validate(
        candidate_plan=plan,
        request=result.request,
        authorization=result.authorization,
        readiness=result.readiness,
        sources=result.source_resolutions,
    )
    return route, plan, validation


def _repository_with_pending():
    repository = MemoryPendingClarificationRepository(clock=lambda: NOW)
    goal = CanonicalGoal(
        goal_id="goal-pending-diagnosis",
        capability="diagnose_fault",
        origin="explicit",
        user_requested=True,
        user_visible=True,
        clause_index=0,
        required_slots=["device"],
        resolved_slots={},
        missing_slots=["device"],
        provenance=GoalProvenance(parser_source="deterministic", utterance_span=(0, 4)),
    )
    repository.create_waiting(
        pending_id="pending-phase2",
        thread_id="thread-phase2",
        user_id="user-phase2",
        created_turn_id="turn-0",
        created_message_id="message-0",
        idempotency_key="pending-create",
        original_goals=[goal],
        missing_slots=["device"],
        candidate_values={"device": ["G120电机1", "G120电机2"]},
        historical_authorization_audit={"role": "engineer"},
        now=NOW,
    )
    return repository


def test_all_production_channels_construct_equivalent_canonical_goals() -> None:
    coordinator = ConversationTurnCoordinator(clock=lambda: NOW)
    message = "A07089 是什么？查询 G120电机1 当前状态"
    observed = []
    for channel in ("text", "voice", "collect", "text_edit"):
        result = coordinator.preview_turn(
            _command(message, channel=channel, turn_id=f"turn-{channel}", message_id=f"message-{channel}", key=f"key-{channel}"),
            auth_context=_admin(),
        )
        observed.append([(goal.capability, goal.origin, goal.clause_index) for goal in result.request.goals])
    assert observed == [observed[0]] * len(observed)
    assert [item[0] for item in observed[0]] == ["explain_fault_code", "check_runtime_status"]


def test_preview_turn_has_zero_pending_repository_writes() -> None:
    repository = _repository_with_pending()
    before = repository.get_waiting("thread-phase2", "user-phase2", now=NOW)
    result = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW).preview_turn(
        _command("是电机2"),
        auth_context=_admin(),
    )
    after = repository.get_waiting("thread-phase2", "user-phase2", now=NOW)
    assert result.execution_performed is False
    assert result.pending_transition.action == "resume_and_consume"
    assert before == after
    assert after is not None and after.status == "waiting" and after.version == 1


def test_preview_does_not_lazily_expire_and_write_an_expired_pending() -> None:
    repository = _repository_with_pending()
    later = NOW + timedelta(hours=1)
    coordinator = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: later)
    result = coordinator.preview_turn(_command("谢谢"), auth_context=_admin())
    stored = repository.get_by_idempotency_key("pending-create")
    assert result.request.pending_binding.kind == "no_match"
    assert stored is not None and stored.status == "waiting" and stored.version == 1


def test_preview_and_execute_build_the_same_canonical_request() -> None:
    coordinator = ConversationTurnCoordinator(clock=lambda: NOW)
    command = _command("查询 G120电机1 当前运行状态")
    preview = coordinator.preview_turn(command, auth_context=_admin())

    async def executor(result):
        return {
            "events": [TurnEvent(event_type="runtime_complete", sequence=len(result.events), detail={"status": "completed"})],
            "execution_performed": True,
            "terminal_status": "completed",
        }

    executed = asyncio.run(coordinator.execute_turn(command, auth_context=_admin(), executor=executor))
    assert executed.request == preview.request
    assert executed.terminal_status == "completed"


def test_idempotent_replay_and_concurrent_replay_execute_once() -> None:
    coordinator = ConversationTurnCoordinator(clock=lambda: NOW)
    command = _command("查询 G120电机1 当前运行状态")
    calls = 0

    async def executor(_):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"terminal_status": "completed", "execution_performed": True}

    async def run_twice():
        return await asyncio.gather(
            coordinator.execute_turn(command, auth_context=_admin(), executor=executor),
            coordinator.execute_turn(command, auth_context=_admin(), executor=executor),
        )

    first, second = asyncio.run(run_twice())
    assert calls == 1
    assert first == second
    other_scope = command.model_copy(update={"thread_id": "thread-phase2-other"}, deep=True)
    asyncio.run(coordinator.execute_turn(other_scope, auth_context=_admin(), executor=executor))
    assert calls == 2


def test_pending_slot_only_and_mixed_bindings_preserve_goal_id_and_current_auth() -> None:
    for message, expected in (("是电机2", ["diagnose_fault"]), ("是电机2，顺便生成报告", ["diagnose_fault", "generate_report"])):
        repository = _repository_with_pending()
        coordinator = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW)
        preview = coordinator.preview_turn(_command(message), auth_context=_guest())
        assert [goal.capability for goal in preview.request.goals] == expected
        assert preview.request.goals[0].goal_id == "goal-pending-diagnosis"
        assert preview.authorization[0].status == "denied"
        assert preview.authorization[0].audit["denied_reason_code"] == "diagnosis_permission_denied"
        assert preview.request.pending_binding.kind == ("mixed" if len(expected) == 2 else "slot_only")


def test_new_action_does_not_consume_unrelated_pending() -> None:
    repository = _repository_with_pending()
    result = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW).preview_turn(
        _command("生成运行报告"),
        auth_context=_admin(),
    )
    assert result.request.pending_binding.kind == "new_action"
    assert result.request.pending_binding.consumes_pending is False
    assert repository.get_waiting("thread-phase2", "user-phase2", now=NOW).status == "waiting"


def test_per_goal_authorization_and_missing_slot_do_not_block_ready_goal() -> None:
    guest = ConversationTurnCoordinator(clock=lambda: NOW).preview_turn(
        _command("A07089 是什么？并生成报告"),
        auth_context=_guest(),
    )
    decisions = {item.capability: item.status for item in guest.authorization}
    assert decisions == {"explain_fault_code": "authorized", "generate_report": "denied"}
    _, guest_plan, _ = _compile(guest)
    assert [node.node_type for node in guest_plan.nodes] == ["rag"]

    partial = ConversationTurnCoordinator(clock=lambda: NOW).preview_turn(
        _command("解释 A07089，并诊断是否存在故障"),
        auth_context=_admin(),
    )
    readiness = {goal.capability: decision.status for goal, decision in zip(partial.request.goals, partial.readiness, strict=True)}
    assert readiness == {"explain_fault_code": "ready", "diagnose_fault": "blocked_missing_slot"}
    _, partial_plan, _ = _compile(partial)
    assert [node.node_type for node in partial_plan.nodes] == ["rag"]


def test_source_selection_distinguishes_satisfaction_from_execution_input() -> None:
    coordinator = ConversationTurnCoordinator(clock=lambda: NOW)
    report = coordinator.preview_turn(
        _command("根据诊断结果生成报告"),
        auth_context=_admin(),
        conversation_context=_analysis_context(),
    )
    assert report.source_resolutions[0].status == "source_for_execution"
    _, report_plan, _ = _compile(report)
    assert [node.node_type for node in report_plan.nodes] == ["report"]

    diagnosis = coordinator.preview_turn(
        _command("根据诊断结果诊断一下", turn_id="turn-2", message_id="message-2", key="key-2"),
        auth_context=_admin(),
        conversation_context=_analysis_context(),
    )
    assert diagnosis.source_resolutions[0].status == "satisfied_by_artifact"
    assert diagnosis.readiness[0].status == "satisfied_by_artifact"
    _, diagnosis_plan, _ = _compile(diagnosis)
    assert diagnosis_plan.nodes == []


def test_clause_order_projection_and_validator_preserve_canonical_goals() -> None:
    result = ConversationTurnCoordinator(clock=lambda: NOW).preview_turn(
        _command("A07089 是什么？查询 G120电机1 当前状态，判断是否存在故障，并给出处理建议"),
        auth_context=_admin(),
    )
    before = [goal.model_dump(mode="json") for goal in result.request.goals]
    projection = project_legacy_frames_from_canonical(
        result.request,
        result.authorization,
        result.readiness,
        result.source_resolutions,
    )
    route, plan, validation = _compile(result)
    assert [goal.capability for goal in result.request.goals] == [
        "explain_fault_code",
        "check_runtime_status",
        "diagnose_fault",
        "resolution_recommendation",
    ]
    assert projection.primary_execution_capability == "composite"
    assert projection.compatibility_only is True
    assert before == [goal.model_dump(mode="json") for goal in result.request.goals]
    assert [goal.goal_id for goal in plan.goals] == [goal.goal_id for goal in result.request.goals]
    assert validation.status == "validated"
    assert route.primary_skill


def test_pending_cas_consumes_only_after_terminal_result() -> None:
    repository = _repository_with_pending()
    coordinator = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW)
    observed_status = ""

    async def executor(_):
        nonlocal observed_status
        observed_status = repository.get_by_idempotency_key("key-1:resume").status
        return {"terminal_status": "completed", "execution_performed": True}

    result = asyncio.run(coordinator.execute_turn(_command("是电机2"), auth_context=_admin(), executor=executor))
    consumed = repository.get_by_idempotency_key("key-1:consume")
    assert observed_status == "resumed"
    assert result.terminal_status == "completed"
    assert consumed is not None and consumed.status == "consumed" and consumed.version == 3


def test_executor_exception_is_terminally_cached_and_does_not_resume_or_execute_twice() -> None:
    repository = _repository_with_pending()
    coordinator = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW)
    calls = 0

    async def failing(_):
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(coordinator.execute_turn(_command("是电机2"), auth_context=_admin(), executor=failing))
    replay = asyncio.run(coordinator.execute_turn(_command("是电机2"), auth_context=_admin(), executor=failing))
    assert calls == 1
    assert replay.terminal_status == "failed"
    assert repository.get_by_idempotency_key("key-1:resume").status == "consumed"
    assert repository.get_by_idempotency_key("key-1:consume").status == "consumed"


def test_edit_stream_and_all_chat_entrypoints_reference_one_production_coordinator() -> None:
    chat = (ROOT / "fault_diagnosis/server/use_cases/chat_service.py").read_text(encoding="utf-8")
    streaming = (ROOT / "fault_diagnosis/server/agent_gateway/streaming.py").read_text(encoding="utf-8")
    assert chat.count(".stream_turn(") >= 3
    assert "get_production_turn_coordinator" in chat
    assert "AgentEngineV2(" not in chat
    assert "ConversationTurnCoordinator(" not in chat
    assert "AgentEngineV2(" not in streaming
    assert "ConversationTurnCoordinator(" not in streaming


def test_production_coordinator_replays_durable_terminal_turn_without_executor() -> None:
    repository = MemoryConversationRepository()
    repository.ensure_thread(
        thread_id="thread-phase2",
        session_id="session-phase2",
        owner_user_id="user-phase2",
    )
    repository.append_message(
        thread_id="thread-phase2",
        role="assistant",
        content_text="cached answer",
        content_json={"report_url": None},
        status="completed",
        request_id="old-request",
        metadata={"idempotency_key": "durable-key"},
    )

    async def unused_stream(*args, **kwargs):
        raise AssertionError("durable replay must not invoke runtime")
        yield ""  # pragma: no cover

    app = SimpleNamespace(state=SimpleNamespace(conversation_repository=repository))
    logger = SimpleNamespace(warning=lambda *args, **kwargs: None)
    coordinator = ProductionTurnCoordinator(app=app, stream_events=unused_stream, logger=logger)
    context = SimpleNamespace(
        thread_id="thread-phase2",
        request_id="new-request",
        metadata={"idempotency_key": "durable-key"},
    )
    chunks = coordinator._load_durable_replay(context)
    payload = parse_sse_payloads(chunks[0])[0]
    assert payload["type"] == "chat_complete"
    assert payload["final_content"] == "cached answer"
    assert payload["replayed"] is True

    repository.append_message(
        thread_id="thread-phase2",
        role="user",
        content_text="interrupted request",
        content_json={},
        status="accepted",
        request_id="interrupted-request",
        metadata={"idempotency_key": "interrupted-key"},
    )
    interrupted_context = SimpleNamespace(
        thread_id="thread-phase2",
        request_id="retry-request",
        metadata={"idempotency_key": "interrupted-key"},
    )
    interrupted = parse_sse_payloads(coordinator._load_durable_replay(interrupted_context)[0])[0]
    assert interrupted["status"] == "failed"
    assert interrupted["recovery_required"] is True
