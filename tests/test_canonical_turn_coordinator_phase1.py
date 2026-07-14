from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator
from fault_diagnosis.domain.canonical_turn import CanonicalGoal, TurnCommand
from fault_diagnosis.domain.canonical_turn.contracts import GoalProvenance
from fault_diagnosis.platform.persistence.repositories.pending_clarification_repository import (
    MemoryPendingClarificationRepository,
)


NOW = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)


def _command(message: str, *, turn="turn-2", message_id="message-2", key="turn-key") -> TurnCommand:
    return TurnCommand(
        thread_id="thread-1",
        user_id="user-1",
        turn_id=turn,
        message_id=message_id,
        idempotency_key=key,
        raw_message=message,
    )


def _repository_with_pending() -> tuple[MemoryPendingClarificationRepository, CanonicalGoal]:
    repository = MemoryPendingClarificationRepository(clock=lambda: NOW)
    goal = CanonicalGoal(
        goal_id="goal_pending_diagnose",
        capability="diagnose_fault",
        origin="explicit",
        user_requested=True,
        user_visible=True,
        clause_index=0,
        required_slots=["device"],
        resolved_slots={},
        missing_slots=["device"],
        provenance=GoalProvenance(parser_source="deterministic", utterance_span=(0, 8)),
    )
    repository.create_waiting(
        pending_id="pending-diagnose-device",
        thread_id="thread-1",
        user_id="user-1",
        created_turn_id="turn-1",
        created_message_id="message-1",
        idempotency_key="create-pending",
        original_goals=[goal],
        missing_slots=["device"],
        candidate_values={"device": ["G120电机1", "G120电机2"]},
        historical_authorization_audit={"authorized_then": True, "role": "engineer"},
        now=NOW,
    )
    return repository, goal


def test_missing_slot_preview_proposes_independent_pending_record() -> None:
    coordinator = ConversationTurnCoordinator(clock=lambda: NOW)
    command = _command("它现在是否存在故障？", turn="turn-1", message_id="message-1", key="create-turn")
    command.candidate_values = {"device": ["G120电机1", "G120电机2"]}
    command.historical_authorization_audit = {"authorized_then": True}

    result = coordinator.preview(command)

    assert result.pending_transition.action == "create_waiting"
    pending = result.pending_transition.proposed_pending
    assert pending.missing_slots == ["device"]
    assert pending.original_goals[0].goal_id == result.request.goals[0].goal_id
    assert pending.historical_authorization_audit == {"authorized_then": True}
    assert result.request.authorization_required is True
    assert result.request.authorization_result is None
    assert result.execution_performed is False


def test_slot_only_reply_restores_goal_id_without_creating_goal_from_device() -> None:
    repository, original = _repository_with_pending()
    result = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW).preview(
        _command("是电机2。")
    )

    assert result.request.pending_binding.kind == "slot_only"
    assert [goal.goal_id for goal in result.request.goals] == [original.goal_id]
    assert [goal.capability for goal in result.request.goals] == ["diagnose_fault"]
    assert result.request.goals[0].resolved_slots == {"device": "G120电机2"}
    assert result.request.goals[0].missing_slots == []
    assert result.request.goals[0].provenance.parser_source == "pending"
    assert result.pending_transition.action == "resume_and_consume"


def test_mixed_reply_restores_pending_then_appends_explicit_goal_in_clause_order() -> None:
    repository, original = _repository_with_pending()
    result = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW).preview(
        _command("是电机2，顺便生成报告。")
    )

    assert result.request.pending_binding.kind == "mixed"
    assert [goal.capability for goal in result.request.goals] == ["diagnose_fault", "generate_report"]
    assert result.request.goals[0].goal_id == original.goal_id
    assert result.request.goals[1].origin == "explicit"
    assert result.request.goals[1].clause_index == 1
    assert result.request.pending_binding.appended_goal_ids == [result.request.goals[1].goal_id]
    assert result.pending_transition.action == "resume_and_consume"
    assert result.request.authorization_required is True
    assert result.request.authorization_result is None


def test_action_only_new_request_does_not_consume_unrelated_waiting_pending() -> None:
    repository, _ = _repository_with_pending()
    result = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW).preview(
        _command("生成运行报告。")
    )

    assert result.request.pending_binding.kind == "new_action"
    assert result.request.pending_binding.consumes_pending is False
    assert [goal.capability for goal in result.request.goals] == ["generate_report"]
    assert result.pending_transition.action == "none"
    assert repository.get_waiting("thread-1", "user-1", now=NOW) is not None


@pytest.mark.parametrize(
    "message,kind",
    [("谢谢", "unrelated"), ("是电机9。", "no_match")],
)
def test_unrelated_and_unmatched_slot_replies_remain_distinct(message: str, kind: str) -> None:
    repository, _ = _repository_with_pending()

    result = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW).preview(
        _command(message)
    )

    assert result.request.pending_binding.kind == kind
    assert result.request.pending_binding.consumes_pending is False
    assert result.pending_transition.action == "none"


@pytest.mark.parametrize(
    "thread_id,user_id",
    [("thread-other", "user-1"), ("thread-1", "user-other")],
)
def test_coordinator_cannot_resume_across_thread_or_user(thread_id: str, user_id: str) -> None:
    repository, _ = _repository_with_pending()
    command = _command("是电机2。")
    command.thread_id = thread_id
    command.user_id = user_id

    result = ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW).preview(command)

    assert result.request.goals == []
    assert result.request.pending_binding.kind == "no_match"
    assert result.pending_transition.action == "none"


def test_goal_ids_origins_and_clause_indexes_are_stable_on_idempotent_preview() -> None:
    coordinator = ConversationTurnCoordinator(clock=lambda: NOW)
    command = _command(
        "A07089 是什么意思？查询 G120电机1 最近一小时有没有异常，判断是否存在故障，并给出处理建议。",
        turn="turn-composite",
        message_id="message-composite",
        key="composite-key",
    )

    first = coordinator.preview(command)
    second = coordinator.preview(command)

    assert [goal.goal_id for goal in first.request.goals] == [goal.goal_id for goal in second.request.goals]
    assert [goal.capability for goal in first.request.goals] == [
        "explain_fault_code",
        "check_runtime_status",
        "diagnose_fault",
        "resolution_recommendation",
    ]
    assert [goal.origin for goal in first.request.goals] == ["explicit"] * 4
    assert [goal.clause_index for goal in first.request.goals] == [0, 1, 2, 3]


def test_dependency_goal_contract_is_not_user_requested_or_visible() -> None:
    goal = CanonicalGoal(
        goal_id="goal_dependency_status",
        capability="check_runtime_status",
        origin="dependency",
        user_requested=False,
        user_visible=False,
        clause_index=0,
        required_slots=["device"],
        resolved_slots={"device": "G120电机1"},
        missing_slots=[],
        provenance=GoalProvenance(parser_source="deterministic"),
    )
    assert goal.origin == "dependency"
    assert goal.user_requested is False
    assert goal.user_visible is False

    with pytest.raises(ValueError, match="dependency goals"):
        goal.model_copy(update={"user_visible": True}).model_validate(
            {**goal.model_dump(), "user_visible": True}
        )
