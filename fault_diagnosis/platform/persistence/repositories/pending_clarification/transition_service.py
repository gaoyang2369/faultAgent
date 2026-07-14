"""Pure status, scope, CAS and replay rules shared by all backends."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fault_diagnosis.domain.canonical_turn import (
    CanonicalGoal,
    PendingClarification,
    PendingSourceBinding,
)

from .errors import CompareAndSetConflict, IdempotencyConflict
from .idempotency import IdempotencyReplay


DEFAULT_PENDING_TTL = timedelta(minutes=30)
_ALLOWED_TRANSITIONS = {
    "waiting": {"resumed", "expired", "cancelled"},
    "resumed": {"consumed", "expired", "cancelled"},
    "consumed": set(),
    "expired": set(),
    "cancelled": set(),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def build_waiting_clarification(
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
    created_at = aware(now)
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


def validate_scope(pending: PendingClarification, *, thread_id: str, user_id: str) -> None:
    if pending.thread_id != thread_id or pending.user_id != user_id:
        raise CompareAndSetConflict(pending.pending_id)


def validate_status_transition(expected_status: str, new_status: str) -> None:
    if new_status not in _ALLOWED_TRANSITIONS.get(expected_status, set()):
        raise ValueError(f"invalid pending clarification transition: {expected_status} -> {new_status}")


def validate_expected_state(
    pending: PendingClarification | None,
    *,
    pending_id: str,
    thread_id: str,
    user_id: str,
    expected_status: str,
    expected_version: int,
) -> PendingClarification:
    if pending is None:
        raise CompareAndSetConflict(pending_id)
    validate_scope(pending, thread_id=thread_id, user_id=user_id)
    if pending.status != expected_status or pending.version != expected_version:
        raise CompareAndSetConflict(pending_id)
    return pending


def validate_idempotency_replay(
    replay: IdempotencyReplay,
    pending: PendingClarification,
    *,
    pending_id: str,
    operation: str,
    thread_id: str,
    user_id: str,
) -> None:
    if replay.pending_id != pending_id or replay.operation != operation:
        raise IdempotencyConflict(pending.idempotency_key)
    try:
        validate_scope(pending, thread_id=thread_id, user_id=user_id)
    except CompareAndSetConflict as exc:
        raise IdempotencyConflict(pending.idempotency_key) from exc


def build_transition_result(
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
        "updated_at": aware(now),
    }
    if new_status == "resumed":
        updates.update(resumed_turn_id=turn_id, resumed_message_id=message_id)
    elif new_status == "consumed":
        updates.update(consumed_turn_id=turn_id, consumed_message_id=message_id)
    return pending.model_copy(update=updates, deep=True)


class PendingTransitionCommands:
    """Shared public transition commands; backends provide atomic CAS."""

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
