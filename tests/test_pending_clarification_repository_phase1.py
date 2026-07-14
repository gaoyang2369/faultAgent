from __future__ import annotations

import inspect
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from fault_diagnosis.domain.canonical_turn import CanonicalGoal
from fault_diagnosis.domain.canonical_turn import PendingSourceBinding
from fault_diagnosis.domain.canonical_turn.contracts import GoalProvenance
from fault_diagnosis.platform.persistence.repositories.pending_clarification_repository import (
    CompareAndSetConflict,
    MemoryPendingClarificationRepository,
    PendingClarificationRepository,
    SQLitePendingClarificationRepository,
    WaitingClarificationExists,
)


NOW = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)


def _goal(goal_id: str = "goal_original") -> CanonicalGoal:
    return CanonicalGoal(
        goal_id=goal_id,
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


def _create(repository, *, pending_id="pending-1", thread_id="thread-1", user_id="user-1", key="create-1", ttl=timedelta(minutes=30)):
    return repository.create_waiting(
        pending_id=pending_id,
        thread_id=thread_id,
        user_id=user_id,
        created_turn_id="turn-create",
        created_message_id="message-create",
        idempotency_key=key,
        original_goals=[_goal()],
        missing_slots=["device"],
        candidate_values={"device": ["G120电机1", "G120电机2"]},
        source_bindings=[
            PendingSourceBinding(
                source_kind="candidate_context",
                reference_text="G120 comparison context",
            )
        ],
        historical_authorization_audit={"role_at_creation": "engineer", "authorized_then": True},
        ttl=ttl,
        now=NOW,
    )


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    if request.param == "memory":
        return MemoryPendingClarificationRepository(clock=lambda: NOW)
    return SQLitePendingClarificationRepository(tmp_path / "pending.sqlite3", clock=lambda: NOW)


def test_memory_and_sqlite_repository_interfaces_are_equal() -> None:
    methods = (
        "create_waiting",
        "get_waiting",
        "compare_and_set_status",
        "get_by_idempotency_key",
        "mark_resumed",
        "mark_consumed",
        "mark_expired",
    )
    for method in methods:
        assert inspect.signature(getattr(MemoryPendingClarificationRepository, method)) == inspect.signature(
            getattr(SQLitePendingClarificationRepository, method)
        )
        assert hasattr(PendingClarificationRepository, method)


def test_waiting_scope_is_unique_and_sqlite_uses_partial_unique_index(repository) -> None:
    _create(repository)
    with pytest.raises(WaitingClarificationExists):
        _create(repository, pending_id="pending-2", key="create-2")

    if isinstance(repository, SQLitePendingClarificationRepository):
        with sqlite3.connect(repository.path) as connection:
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'uq_pending_clarification_waiting_scope'"
            ).fetchone()[0]
        assert "UNIQUE INDEX" in sql
        assert "WHERE status = 'waiting'" in sql


def test_create_resume_and_consume_are_idempotent_replays(repository) -> None:
    created = _create(repository)
    replayed_create = _create(repository)
    assert replayed_create.pending_id == created.pending_id
    assert replayed_create.version == 1
    assert replayed_create.source_bindings[0].audit_only is True
    assert replayed_create.historical_authorization_audit["authorized_then"] is True

    resumed = repository.mark_resumed(
        created.pending_id,
        thread_id="thread-1",
        user_id="user-1",
        expected_version=1,
        turn_id="turn-resume",
        message_id="message-resume",
        idempotency_key="resume-key",
        now=NOW + timedelta(minutes=1),
    )
    replayed_resume = repository.mark_resumed(
        created.pending_id,
        thread_id="thread-1",
        user_id="user-1",
        expected_version=1,
        turn_id="turn-resume",
        message_id="message-resume",
        idempotency_key="resume-key",
        now=NOW + timedelta(minutes=1),
    )
    assert resumed.status == replayed_resume.status == "resumed"
    assert resumed.version == replayed_resume.version == 2

    consumed = repository.mark_consumed(
        created.pending_id,
        thread_id="thread-1",
        user_id="user-1",
        expected_version=2,
        turn_id="turn-resume",
        message_id="message-resume",
        idempotency_key="consume-key",
        now=NOW + timedelta(minutes=1),
    )
    replayed_consumed = repository.mark_consumed(
        created.pending_id,
        thread_id="thread-1",
        user_id="user-1",
        expected_version=2,
        turn_id="turn-resume",
        message_id="message-resume",
        idempotency_key="consume-key",
        now=NOW + timedelta(minutes=1),
    )
    assert consumed.status == replayed_consumed.status == "consumed"
    assert consumed.version == replayed_consumed.version == 3
    assert consumed.resumed_turn_id == consumed.consumed_turn_id == "turn-resume"
    assert consumed.resumed_message_id == consumed.consumed_message_id == "message-resume"
    assert repository.get_by_idempotency_key("create-1").pending_id == created.pending_id
    assert repository.get_by_idempotency_key("resume-key").status == "consumed"


def test_scope_isolated_by_both_thread_and_user(repository) -> None:
    _create(repository)

    assert repository.get_waiting("thread-1", "user-1", now=NOW) is not None
    assert repository.get_waiting("thread-other", "user-1", now=NOW) is None
    assert repository.get_waiting("thread-1", "user-other", now=NOW) is None


def test_default_ttl_and_lazy_expiry(repository) -> None:
    pending = _create(repository)

    assert pending.expires_at - pending.created_at == timedelta(minutes=30)
    assert repository.get_waiting("thread-1", "user-1", now=NOW + timedelta(minutes=29)) is not None
    assert repository.get_waiting("thread-1", "user-1", now=NOW + timedelta(minutes=31)) is None
    assert repository.get_by_idempotency_key("create-1").status == "expired"


def test_sqlite_cas_allows_only_one_concurrent_transition(tmp_path) -> None:
    path = tmp_path / "cas.sqlite3"
    creator = SQLitePendingClarificationRepository(path, clock=lambda: NOW)
    _create(creator)

    def resume(index: int):
        repository = SQLitePendingClarificationRepository(path, clock=lambda: NOW)
        try:
            return repository.mark_resumed(
                "pending-1",
                thread_id="thread-1",
                user_id="user-1",
                expected_version=1,
                turn_id=f"turn-{index}",
                message_id=f"message-{index}",
                idempotency_key=f"resume-{index}",
                now=NOW + timedelta(minutes=1),
            ).status
        except CompareAndSetConflict:
            return "cas_conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resume, [1, 2]))

    assert sorted(results) == ["cas_conflict", "resumed"]


def test_stale_version_is_rejected_by_both_repositories(repository) -> None:
    pending = _create(repository)
    repository.mark_resumed(
        pending.pending_id,
        thread_id="thread-1",
        user_id="user-1",
        expected_version=1,
        turn_id="turn-r",
        message_id="message-r",
        idempotency_key="resume-r",
        now=NOW,
    )

    with pytest.raises(CompareAndSetConflict):
        repository.mark_consumed(
            pending.pending_id,
            thread_id="thread-1",
            user_id="user-1",
            expected_version=1,
            turn_id="turn-c",
            message_id="message-c",
            idempotency_key="consume-stale",
            now=NOW,
        )


@pytest.mark.parametrize(
    "thread_id,user_id",
    [("thread-other", "user-1"), ("thread-1", "user-other")],
)
def test_status_transition_rejects_cross_thread_or_user_scope(repository, thread_id: str, user_id: str) -> None:
    pending = _create(repository)

    with pytest.raises(CompareAndSetConflict):
        repository.mark_resumed(
            pending.pending_id,
            thread_id=thread_id,
            user_id=user_id,
            expected_version=1,
            turn_id="turn-cross-scope",
            message_id="message-cross-scope",
            idempotency_key=f"cross:{thread_id}:{user_id}",
            now=NOW,
        )

    assert repository.get_waiting("thread-1", "user-1", now=NOW).status == "waiting"


def test_sqlite_memory_database_keeps_schema_and_records_between_calls() -> None:
    repository = SQLitePendingClarificationRepository(":memory:", clock=lambda: NOW)

    pending = _create(repository)

    assert repository.get_waiting("thread-1", "user-1", now=NOW).pending_id == pending.pending_id


def test_sqlite_schema_remains_phase1_compatible(tmp_path) -> None:
    repository = SQLitePendingClarificationRepository(tmp_path / "schema.sqlite3", clock=lambda: NOW)
    _create(repository)

    with sqlite3.connect(repository.path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(pending_clarifications)")]
        operation_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(pending_clarification_operations)")
        ]
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pending_clarifications'"
        ).fetchone()[0]
        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'uq_pending_clarification_waiting_scope'"
        ).fetchone()[0]

    assert columns == [
        "pending_id",
        "thread_id",
        "user_id",
        "status",
        "version",
        "create_idempotency_key",
        "created_turn_id",
        "created_message_id",
        "resumed_turn_id",
        "resumed_message_id",
        "consumed_turn_id",
        "consumed_message_id",
        "created_at",
        "updated_at",
        "expires_at",
        "original_goals_json",
        "missing_slots_json",
        "candidate_values_json",
        "source_bindings_json",
        "historical_authorization_audit_json",
    ]
    assert operation_columns == [
        "operation_id",
        "pending_id",
        "operation_type",
        "idempotency_key",
        "turn_id",
        "message_id",
        "from_status",
        "to_status",
        "resulting_version",
        "created_at",
    ]
    assert "'waiting','resumed','consumed','expired','cancelled'" in table_sql
    assert "UNIQUE INDEX" in index_sql
    assert "WHERE status = 'waiting'" in index_sql
