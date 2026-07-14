"""Public pending clarification repository protocol."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from fault_diagnosis.domain.canonical_turn import (
    CanonicalGoal,
    PendingClarification,
    PendingSourceBinding,
)

from .transition_service import DEFAULT_PENDING_TTL


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
