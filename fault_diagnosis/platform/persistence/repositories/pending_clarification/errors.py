"""Pending clarification repository errors."""


class PendingClarificationConflict(RuntimeError):
    """Base error for a rejected persistence transition."""


class WaitingClarificationExists(PendingClarificationConflict):
    """A scope already has a waiting clarification."""


class CompareAndSetConflict(PendingClarificationConflict):
    """Scope, status or version changed before a requested transition."""


class IdempotencyConflict(PendingClarificationConflict):
    """An idempotency key was reused for a different operation."""
