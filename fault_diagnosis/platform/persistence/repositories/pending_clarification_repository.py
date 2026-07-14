"""Independent persistence for Canonical Turn pending clarifications.

This repository is deliberately separate from conversation history and the
work-order ``PendingAction`` contract.  No production transport constructs it
during Phase 1.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Protocol

from fault_diagnosis.domain.canonical_turn import (
    CanonicalGoal,
    PendingClarification,
    PendingSourceBinding,
)
from fault_diagnosis.platform.paths import RUN_STATE_DIR


DEFAULT_PENDING_TTL = timedelta(minutes=30)
_ALLOWED_TRANSITIONS = {
    "waiting": {"resumed", "expired", "cancelled"},
    "resumed": {"consumed", "expired", "cancelled"},
    "consumed": set(),
    "expired": set(),
    "cancelled": set(),
}


class PendingClarificationConflict(RuntimeError):
    """Base error for a rejected persistence transition."""


class WaitingClarificationExists(PendingClarificationConflict):
    """A scope already has a waiting clarification."""


class CompareAndSetConflict(PendingClarificationConflict):
    """Status or version changed before a requested transition."""


class IdempotencyConflict(PendingClarificationConflict):
    """An idempotency key was reused for a different operation."""


class PendingClarificationRepository(Protocol):
    def create_waiting(
        self,
        *,
        pending_id: str,
        thread_id: str,
        user_id: str,
        created_turn_id: str,
        created_message_id: str,
        idempotency_key: str,
        original_goals: list[CanonicalGoal],
        missing_slots: list[str],
        candidate_values: dict[str, list[str]] | None = None,
        source_bindings: list[PendingSourceBinding] | None = None,
        historical_authorization_audit: dict[str, Any] | None = None,
        ttl: timedelta = DEFAULT_PENDING_TTL,
        now: datetime | None = None,
    ) -> PendingClarification:
        """Create one waiting clarification or replay the same create."""

    def get_waiting(
        self,
        thread_id: str,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> PendingClarification | None:
        """Return the scope's waiting record, lazily expiring it when needed."""

    def compare_and_set_status(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_status: str,
        expected_version: int,
        new_status: str,
        turn_id: str,
        message_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        """Apply a status and version CAS, protected by operation idempotency."""

    def get_by_idempotency_key(self, idempotency_key: str) -> PendingClarification | None:
        """Resolve either a create or transition idempotency key."""

    def mark_resumed(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_version: int,
        turn_id: str,
        message_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        """CAS waiting to resumed."""

    def mark_consumed(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_version: int,
        turn_id: str,
        message_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        """CAS resumed to consumed."""

    def mark_expired(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_status: str,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        """CAS a waiting or resumed record to expired."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _new_pending(
    *,
    pending_id: str,
    thread_id: str,
    user_id: str,
    created_turn_id: str,
    created_message_id: str,
    idempotency_key: str,
    original_goals: list[CanonicalGoal],
    missing_slots: list[str],
    candidate_values: dict[str, list[str]] | None,
    source_bindings: list[PendingSourceBinding] | None,
    historical_authorization_audit: dict[str, Any] | None,
    ttl: timedelta,
    now: datetime,
) -> PendingClarification:
    created_at = _aware(now)
    return PendingClarification(
        pending_id=pending_id,
        thread_id=thread_id,
        user_id=user_id,
        status="waiting",
        version=1,
        idempotency_key=idempotency_key,
        created_turn_id=created_turn_id,
        created_message_id=created_message_id,
        created_at=created_at,
        updated_at=created_at,
        expires_at=created_at + ttl,
        original_goals=original_goals,
        missing_slots=list(dict.fromkeys(missing_slots)),
        candidate_values=candidate_values or {},
        source_bindings=source_bindings or [],
        historical_authorization_audit=historical_authorization_audit or {},
    )


def _validate_transition(expected_status: str, new_status: str) -> None:
    if new_status not in _ALLOWED_TRANSITIONS.get(expected_status, set()):
        raise ValueError(f"invalid pending clarification transition: {expected_status} -> {new_status}")


def _transition_copy(
    pending: PendingClarification,
    *,
    new_status: str,
    turn_id: str,
    message_id: str,
    now: datetime,
) -> PendingClarification:
    updates: dict[str, Any] = {
        "status": new_status,
        "version": pending.version + 1,
        "updated_at": _aware(now),
    }
    if new_status == "resumed":
        updates.update(resumed_turn_id=turn_id, resumed_message_id=message_id)
    elif new_status == "consumed":
        updates.update(consumed_turn_id=turn_id, consumed_message_id=message_id)
    return pending.model_copy(update=updates, deep=True)


class MemoryPendingClarificationRepository:
    """Thread-safe in-memory implementation with the same CAS contract."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._records: dict[str, PendingClarification] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._lock = RLock()

    def create_waiting(
        self,
        *,
        pending_id: str,
        thread_id: str,
        user_id: str,
        created_turn_id: str,
        created_message_id: str,
        idempotency_key: str,
        original_goals: list[CanonicalGoal],
        missing_slots: list[str],
        candidate_values: dict[str, list[str]] | None = None,
        source_bindings: list[PendingSourceBinding] | None = None,
        historical_authorization_audit: dict[str, Any] | None = None,
        ttl: timedelta = DEFAULT_PENDING_TTL,
        now: datetime | None = None,
    ) -> PendingClarification:
        with self._lock:
            replay = self._idempotency.get(idempotency_key)
            if replay:
                existing_id, operation = replay
                if existing_id != pending_id or operation != "create":
                    raise IdempotencyConflict(idempotency_key)
                existing = self._records[existing_id]
                if existing.thread_id != thread_id or existing.user_id != user_id:
                    raise IdempotencyConflict(idempotency_key)
                return existing.model_copy(deep=True)
            for existing in self._records.values():
                if existing.thread_id == thread_id and existing.user_id == user_id and existing.status == "waiting":
                    raise WaitingClarificationExists(f"{thread_id}:{user_id}")
            pending = _new_pending(
                pending_id=pending_id,
                thread_id=thread_id,
                user_id=user_id,
                created_turn_id=created_turn_id,
                created_message_id=created_message_id,
                idempotency_key=idempotency_key,
                original_goals=original_goals,
                missing_slots=missing_slots,
                candidate_values=candidate_values,
                source_bindings=source_bindings,
                historical_authorization_audit=historical_authorization_audit,
                ttl=ttl,
                now=now or self._clock(),
            )
            self._records[pending_id] = pending
            self._idempotency[idempotency_key] = (pending_id, "create")
            return pending.model_copy(deep=True)

    def get_waiting(
        self,
        thread_id: str,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> PendingClarification | None:
        current = _aware(now or self._clock())
        with self._lock:
            pending = next(
                (
                    item
                    for item in self._records.values()
                    if item.thread_id == thread_id and item.user_id == user_id and item.status == "waiting"
                ),
                None,
            )
            if pending is None:
                return None
            if pending.expires_at <= current:
                expired = _transition_copy(
                    pending,
                    new_status="expired",
                    turn_id="lazy-expiry",
                    message_id="lazy-expiry",
                    now=current,
                )
                self._records[pending.pending_id] = expired
                self._idempotency[f"lazy-expire:{pending.pending_id}:{pending.version}"] = (
                    pending.pending_id,
                    "expired",
                )
                return None
            return pending.model_copy(deep=True)

    def compare_and_set_status(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_status: str,
        expected_version: int,
        new_status: str,
        turn_id: str,
        message_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        _validate_transition(expected_status, new_status)
        with self._lock:
            replay = self._idempotency.get(idempotency_key)
            if replay:
                existing_id, operation = replay
                if existing_id != pending_id or operation != new_status:
                    raise IdempotencyConflict(idempotency_key)
                existing = self._records[existing_id]
                if existing.thread_id != thread_id or existing.user_id != user_id:
                    raise IdempotencyConflict(idempotency_key)
                return existing.model_copy(deep=True)
            pending = self._records.get(pending_id)
            if (
                pending is None
                or pending.thread_id != thread_id
                or pending.user_id != user_id
                or pending.status != expected_status
                or pending.version != expected_version
            ):
                raise CompareAndSetConflict(pending_id)
            updated = _transition_copy(
                pending,
                new_status=new_status,
                turn_id=turn_id,
                message_id=message_id,
                now=now or self._clock(),
            )
            self._records[pending_id] = updated
            self._idempotency[idempotency_key] = (pending_id, new_status)
            return updated.model_copy(deep=True)

    def get_by_idempotency_key(self, idempotency_key: str) -> PendingClarification | None:
        with self._lock:
            reference = self._idempotency.get(idempotency_key)
            if not reference:
                return None
            return self._records[reference[0]].model_copy(deep=True)

    def mark_resumed(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_version: int,
        turn_id: str,
        message_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        return self.compare_and_set_status(
            pending_id,
            thread_id=thread_id,
            user_id=user_id,
            expected_status="waiting",
            expected_version=expected_version,
            new_status="resumed",
            turn_id=turn_id,
            message_id=message_id,
            idempotency_key=idempotency_key,
            now=now,
        )

    def mark_consumed(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_version: int,
        turn_id: str,
        message_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        return self.compare_and_set_status(
            pending_id,
            thread_id=thread_id,
            user_id=user_id,
            expected_status="resumed",
            expected_version=expected_version,
            new_status="consumed",
            turn_id=turn_id,
            message_id=message_id,
            idempotency_key=idempotency_key,
            now=now,
        )

    def mark_expired(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_status: str,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        return self.compare_and_set_status(
            pending_id,
            thread_id=thread_id,
            user_id=user_id,
            expected_status=expected_status,
            expected_version=expected_version,
            new_status="expired",
            turn_id="expiry",
            message_id="expiry",
            idempotency_key=idempotency_key,
            now=now,
        )


class SQLitePendingClarificationRepository:
    """SQLite implementation using a partial unique index and versioned CAS."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        default_path = Path(RUN_STATE_DIR) / "pending_clarifications.sqlite3"
        self.path = Path(path or os.getenv("PENDING_CLARIFICATION_DB_PATH") or default_path)
        self._clock = clock
        self._lock = RLock()
        self._initialized = False
        self._memory_connection: sqlite3.Connection | None = None
        if str(self.path) == ":memory:":
            self._memory_connection = sqlite3.connect(
                ":memory:",
                timeout=10.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._memory_connection.row_factory = sqlite3.Row

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pending_clarifications (
                    pending_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('waiting','resumed','consumed','expired','cancelled')),
                    version INTEGER NOT NULL CHECK(version >= 1),
                    create_idempotency_key TEXT NOT NULL UNIQUE,
                    created_turn_id TEXT NOT NULL,
                    created_message_id TEXT NOT NULL,
                    resumed_turn_id TEXT,
                    resumed_message_id TEXT,
                    consumed_turn_id TEXT,
                    consumed_message_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    original_goals_json TEXT NOT NULL,
                    missing_slots_json TEXT NOT NULL,
                    candidate_values_json TEXT NOT NULL,
                    source_bindings_json TEXT NOT NULL,
                    historical_authorization_audit_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_clarification_waiting_scope
                    ON pending_clarifications(thread_id, user_id)
                    WHERE status = 'waiting';
                CREATE INDEX IF NOT EXISTS idx_pending_clarification_scope_status
                    ON pending_clarifications(thread_id, user_id, status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS pending_clarification_operations (
                    operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pending_id TEXT NOT NULL,
                    operation_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    turn_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    resulting_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(pending_id) REFERENCES pending_clarifications(pending_id)
                );
                CREATE INDEX IF NOT EXISTS idx_pending_operations_pending
                    ON pending_clarification_operations(pending_id, operation_id);
                """
            )
            self._initialized = True

    def create_waiting(
        self,
        *,
        pending_id: str,
        thread_id: str,
        user_id: str,
        created_turn_id: str,
        created_message_id: str,
        idempotency_key: str,
        original_goals: list[CanonicalGoal],
        missing_slots: list[str],
        candidate_values: dict[str, list[str]] | None = None,
        source_bindings: list[PendingSourceBinding] | None = None,
        historical_authorization_audit: dict[str, Any] | None = None,
        ttl: timedelta = DEFAULT_PENDING_TTL,
        now: datetime | None = None,
    ) -> PendingClarification:
        pending = _new_pending(
            pending_id=pending_id,
            thread_id=thread_id,
            user_id=user_id,
            created_turn_id=created_turn_id,
            created_message_id=created_message_id,
            idempotency_key=idempotency_key,
            original_goals=original_goals,
            missing_slots=missing_slots,
            candidate_values=candidate_values,
            source_bindings=source_bindings,
            historical_authorization_audit=historical_authorization_audit,
            ttl=ttl,
            now=now or self._clock(),
        )
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            replay = connection.execute(
                "SELECT * FROM pending_clarifications WHERE create_idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if replay is not None:
                connection.commit()
                if (
                    replay["pending_id"] != pending_id
                    or replay["thread_id"] != thread_id
                    or replay["user_id"] != user_id
                ):
                    raise IdempotencyConflict(idempotency_key)
                return self._row_to_pending(replay)
            try:
                connection.execute(
                    """
                    INSERT INTO pending_clarifications (
                        pending_id, thread_id, user_id, status, version, create_idempotency_key,
                        created_turn_id, created_message_id, created_at, updated_at, expires_at,
                        original_goals_json, missing_slots_json, candidate_values_json,
                        source_bindings_json, historical_authorization_audit_json
                    ) VALUES (?, ?, ?, 'waiting', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._insert_values(pending),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                if (
                    "pending_clarifications.thread_id, pending_clarifications.user_id" in str(exc)
                    or "uq_pending_clarification_waiting_scope" in str(exc)
                ):
                    raise WaitingClarificationExists(f"{thread_id}:{user_id}") from exc
                raise PendingClarificationConflict(str(exc)) from exc
            row = connection.execute(
                "SELECT * FROM pending_clarifications WHERE pending_id = ?",
                (pending_id,),
            ).fetchone()
            connection.commit()
            return self._row_to_pending(row)

    def get_waiting(
        self,
        thread_id: str,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> PendingClarification | None:
        current = _aware(now or self._clock())
        with self._connect() as connection:
            self._ensure_schema(connection)
            row = connection.execute(
                """
                SELECT * FROM pending_clarifications
                WHERE thread_id = ? AND user_id = ? AND status = 'waiting'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (thread_id, user_id),
            ).fetchone()
        if row is None:
            return None
        pending = self._row_to_pending(row)
        if pending.expires_at <= current:
            try:
                self.mark_expired(
                    pending.pending_id,
                    thread_id=pending.thread_id,
                    user_id=pending.user_id,
                    expected_status="waiting",
                    expected_version=pending.version,
                    idempotency_key=f"lazy-expire:{pending.pending_id}:{pending.version}",
                    now=current,
                )
            except CompareAndSetConflict:
                pass
            return None
        return pending

    def compare_and_set_status(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_status: str,
        expected_version: int,
        new_status: str,
        turn_id: str,
        message_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        _validate_transition(expected_status, new_status)
        current = _aware(now or self._clock())
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            replay = connection.execute(
                "SELECT pending_id, operation_type FROM pending_clarification_operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if replay is not None:
                if replay["pending_id"] != pending_id or replay["operation_type"] != new_status:
                    connection.rollback()
                    raise IdempotencyConflict(idempotency_key)
                row = connection.execute(
                    "SELECT * FROM pending_clarifications WHERE pending_id = ?",
                    (pending_id,),
                ).fetchone()
                if row["thread_id"] != thread_id or row["user_id"] != user_id:
                    connection.rollback()
                    raise IdempotencyConflict(idempotency_key)
                connection.commit()
                return self._row_to_pending(row)

            assignments = ["status = ?", "version = version + 1", "updated_at = ?"]
            values: list[Any] = [new_status, current.isoformat()]
            if new_status == "resumed":
                assignments.extend(["resumed_turn_id = ?", "resumed_message_id = ?"])
                values.extend([turn_id, message_id])
            elif new_status == "consumed":
                assignments.extend(["consumed_turn_id = ?", "consumed_message_id = ?"])
                values.extend([turn_id, message_id])
            values.extend([pending_id, thread_id, user_id, expected_status, expected_version])
            cursor = connection.execute(
                f"""
                UPDATE pending_clarifications SET {', '.join(assignments)}
                WHERE pending_id = ? AND thread_id = ? AND user_id = ? AND status = ? AND version = ?
                """,
                values,
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise CompareAndSetConflict(pending_id)
            resulting_version = expected_version + 1
            connection.execute(
                """
                INSERT INTO pending_clarification_operations (
                    pending_id, operation_type, idempotency_key, turn_id, message_id,
                    from_status, to_status, resulting_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pending_id,
                    new_status,
                    idempotency_key,
                    turn_id,
                    message_id,
                    expected_status,
                    new_status,
                    resulting_version,
                    current.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM pending_clarifications WHERE pending_id = ?",
                (pending_id,),
            ).fetchone()
            connection.commit()
            return self._row_to_pending(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> PendingClarification | None:
        with self._connect() as connection:
            self._ensure_schema(connection)
            row = connection.execute(
                "SELECT * FROM pending_clarifications WHERE create_idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                return self._row_to_pending(row)
            row = connection.execute(
                """
                SELECT pending.* FROM pending_clarifications AS pending
                JOIN pending_clarification_operations AS operation
                  ON operation.pending_id = pending.pending_id
                WHERE operation.idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            return self._row_to_pending(row) if row is not None else None

    def mark_resumed(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_version: int,
        turn_id: str,
        message_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        return self.compare_and_set_status(
            pending_id,
            thread_id=thread_id,
            user_id=user_id,
            expected_status="waiting",
            expected_version=expected_version,
            new_status="resumed",
            turn_id=turn_id,
            message_id=message_id,
            idempotency_key=idempotency_key,
            now=now,
        )

    def mark_consumed(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_version: int,
        turn_id: str,
        message_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        return self.compare_and_set_status(
            pending_id,
            thread_id=thread_id,
            user_id=user_id,
            expected_status="resumed",
            expected_version=expected_version,
            new_status="consumed",
            turn_id=turn_id,
            message_id=message_id,
            idempotency_key=idempotency_key,
            now=now,
        )

    def mark_expired(
        self,
        pending_id: str,
        *,
        thread_id: str,
        user_id: str,
        expected_status: str,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PendingClarification:
        return self.compare_and_set_status(
            pending_id,
            thread_id=thread_id,
            user_id=user_id,
            expected_status=expected_status,
            expected_version=expected_version,
            new_status="expired",
            turn_id="expiry",
            message_id="expiry",
            idempotency_key=idempotency_key,
            now=now,
        )

    @staticmethod
    def _insert_values(pending: PendingClarification) -> tuple[Any, ...]:
        return (
            pending.pending_id,
            pending.thread_id,
            pending.user_id,
            pending.idempotency_key,
            pending.created_turn_id,
            pending.created_message_id,
            pending.created_at.isoformat(),
            pending.updated_at.isoformat(),
            pending.expires_at.isoformat(),
            json.dumps([goal.model_dump(mode="json") for goal in pending.original_goals], ensure_ascii=False),
            json.dumps(pending.missing_slots, ensure_ascii=False),
            json.dumps(pending.candidate_values, ensure_ascii=False),
            json.dumps([item.model_dump(mode="json") for item in pending.source_bindings], ensure_ascii=False),
            json.dumps(pending.historical_authorization_audit, ensure_ascii=False),
        )

    @staticmethod
    def _row_to_pending(row: sqlite3.Row | None) -> PendingClarification:
        if row is None:
            raise PendingClarificationConflict("pending clarification record disappeared")
        return PendingClarification(
            pending_id=row["pending_id"],
            thread_id=row["thread_id"],
            user_id=row["user_id"],
            status=row["status"],
            version=row["version"],
            idempotency_key=row["create_idempotency_key"],
            created_turn_id=row["created_turn_id"],
            created_message_id=row["created_message_id"],
            resumed_turn_id=row["resumed_turn_id"],
            resumed_message_id=row["resumed_message_id"],
            consumed_turn_id=row["consumed_turn_id"],
            consumed_message_id=row["consumed_message_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            original_goals=json.loads(row["original_goals_json"]),
            missing_slots=json.loads(row["missing_slots_json"]),
            candidate_values=json.loads(row["candidate_values_json"]),
            source_bindings=json.loads(row["source_bindings_json"]),
            historical_authorization_audit=json.loads(row["historical_authorization_audit_json"]),
        )
