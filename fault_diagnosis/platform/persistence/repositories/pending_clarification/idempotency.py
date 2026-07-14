"""Storage-neutral idempotency references."""

from dataclasses import dataclass


@dataclass(frozen=True)
class IdempotencyReplay:
    pending_id: str
    operation: str


def lazy_expiry_idempotency_key(pending_id: str, version: int) -> str:
    return f"lazy-expire:{pending_id}:{version}"
