"""SQLite pending clarification backend."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from fault_diagnosis.domain.canonical_turn import (
    CanonicalGoal,
    PendingClarification,
    PendingSourceBinding,
)
from fault_diagnosis.platform.paths import RUN_STATE_DIR

from .errors import (
    CompareAndSetConflict,
    IdempotencyConflict,
    PendingClarificationConflict,
    WaitingClarificationExists,
)
from .idempotency import IdempotencyReplay, lazy_expiry_idempotency_key
from .schema import SCHEMA_SQL
from .transition_service import (
    DEFAULT_PENDING_TTL,
    PendingTransitionCommands,
    aware,
    build_waiting_clarification,
    utc_now,
    validate_idempotency_replay,
    validate_status_transition,
)


class SQLitePendingClarificationRepository(PendingTransitionCommands):
    """SQLite backend using the Phase 1 schema and atomic versioned CAS."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
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
            connection.executescript(SCHEMA_SQL)
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
        pending = build_waiting_clarification(
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
            replay_row = connection.execute(
                "SELECT * FROM pending_clarifications WHERE create_idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if replay_row is not None:
                existing = self._row_to_pending(replay_row)
                try:
                    validate_idempotency_replay(
                        IdempotencyReplay(existing.pending_id, "create"),
                        existing,
                        pending_id=pending_id,
                        operation="create",
                        thread_id=thread_id,
                        user_id=user_id,
                    )
                except IdempotencyConflict as exc:
                    connection.rollback()
                    raise IdempotencyConflict(idempotency_key) from exc
                connection.commit()
                return existing
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
        current = aware(now or self._clock())
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
                    idempotency_key=lazy_expiry_idempotency_key(pending.pending_id, pending.version),
                    now=current,
                )
            except CompareAndSetConflict:
                pass
            return None
        return pending

    def peek_waiting(
        self,
        thread_id: str,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> PendingClarification | None:
        current = aware(now or self._clock())
        if not self._initialized and self._memory_connection is None and not self.path.exists():
            return None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM pending_clarifications
                    WHERE thread_id = ? AND user_id = ? AND status = 'waiting'
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (thread_id, user_id),
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        pending = self._row_to_pending(row)
        return None if pending.expires_at <= current else pending

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
        validate_status_transition(expected_status, new_status)
        current = aware(now or self._clock())
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            replay_row = connection.execute(
                "SELECT pending_id, operation_type FROM pending_clarification_operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if replay_row is not None:
                row = connection.execute(
                    "SELECT * FROM pending_clarifications WHERE pending_id = ?",
                    (pending_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise IdempotencyConflict(idempotency_key)
                existing = self._row_to_pending(row)
                try:
                    validate_idempotency_replay(
                        IdempotencyReplay(replay_row["pending_id"], replay_row["operation_type"]),
                        existing,
                        pending_id=pending_id,
                        operation=new_status,
                        thread_id=thread_id,
                        user_id=user_id,
                    )
                except IdempotencyConflict as exc:
                    connection.rollback()
                    raise IdempotencyConflict(idempotency_key) from exc
                connection.commit()
                return existing

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
                    expected_version + 1,
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
