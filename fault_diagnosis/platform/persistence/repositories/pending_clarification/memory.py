"""In-memory pending clarification backend."""

from __future__ import annotations

from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Callable

from fault_diagnosis.domain.canonical_turn import (
    CanonicalGoal,
    PendingClarification,
    PendingSourceBinding,
)

from .errors import IdempotencyConflict, WaitingClarificationExists
from .idempotency import IdempotencyReplay, lazy_expiry_idempotency_key
from .transition_service import (
    DEFAULT_PENDING_TTL,
    PendingTransitionCommands,
    aware,
    build_transition_result,
    build_waiting_clarification,
    utc_now,
    validate_expected_state,
    validate_idempotency_replay,
    validate_status_transition,
)


class MemoryPendingClarificationRepository(PendingTransitionCommands):
    """Thread-safe backend; domain transition rules live in the shared service."""

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._records: dict[str, PendingClarification] = {}
        self._idempotency: dict[str, IdempotencyReplay] = {}
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
                existing = self._records[replay.pending_id]
                try:
                    validate_idempotency_replay(
                        replay,
                        existing,
                        pending_id=pending_id,
                        operation="create",
                        thread_id=thread_id,
                        user_id=user_id,
                    )
                except IdempotencyConflict as exc:
                    raise IdempotencyConflict(idempotency_key) from exc
                return existing.model_copy(deep=True)
            if any(
                item.thread_id == thread_id and item.user_id == user_id and item.status == "waiting"
                for item in self._records.values()
            ):
                raise WaitingClarificationExists(f"{thread_id}:{user_id}")
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
            self._records[pending_id] = pending
            self._idempotency[idempotency_key] = IdempotencyReplay(pending_id, "create")
            return pending.model_copy(deep=True)

    def get_waiting(
        self,
        thread_id: str,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> PendingClarification | None:
        current = aware(now or self._clock())
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
                expired = build_transition_result(
                    pending,
                    new_status="expired",
                    turn_id="lazy-expiry",
                    message_id="lazy-expiry",
                    now=current,
                )
                self._records[pending.pending_id] = expired
                key = lazy_expiry_idempotency_key(pending.pending_id, pending.version)
                self._idempotency[key] = IdempotencyReplay(pending.pending_id, "expired")
                return None
            return pending.model_copy(deep=True)

    def peek_waiting(
        self,
        thread_id: str,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> PendingClarification | None:
        current = aware(now or self._clock())
        with self._lock:
            pending = next(
                (
                    item
                    for item in self._records.values()
                    if item.thread_id == thread_id and item.user_id == user_id and item.status == "waiting"
                ),
                None,
            )
            if pending is None or pending.expires_at <= current:
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
        validate_status_transition(expected_status, new_status)
        with self._lock:
            replay = self._idempotency.get(idempotency_key)
            if replay:
                existing = self._records[replay.pending_id]
                try:
                    validate_idempotency_replay(
                        replay,
                        existing,
                        pending_id=pending_id,
                        operation=new_status,
                        thread_id=thread_id,
                        user_id=user_id,
                    )
                except IdempotencyConflict as exc:
                    raise IdempotencyConflict(idempotency_key) from exc
                return existing.model_copy(deep=True)
            pending = validate_expected_state(
                self._records.get(pending_id),
                pending_id=pending_id,
                thread_id=thread_id,
                user_id=user_id,
                expected_status=expected_status,
                expected_version=expected_version,
            )
            updated = build_transition_result(
                pending,
                new_status=new_status,
                turn_id=turn_id,
                message_id=message_id,
                now=now or self._clock(),
            )
            self._records[pending_id] = updated
            self._idempotency[idempotency_key] = IdempotencyReplay(pending_id, new_status)
            return updated.model_copy(deep=True)

    def get_by_idempotency_key(self, idempotency_key: str) -> PendingClarification | None:
        with self._lock:
            replay = self._idempotency.get(idempotency_key)
            if replay is None:
                return None
            return self._records[replay.pending_id].model_copy(deep=True)
