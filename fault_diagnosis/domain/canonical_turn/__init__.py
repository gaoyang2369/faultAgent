"""Phase 1 canonical-turn contracts.

This package is intentionally not imported by the production agent or
transport entrypoints.  It is consumed only by the isolated preview
coordinator and its tests until the later cutover phase.
"""

from .contracts import (
    CAPABILITY_ALLOWLIST,
    CanonicalGoal,
    CanonicalTurnRequest,
    ClauseAction,
    ClauseSource,
    CurrentUtteranceParse,
    EntitySpan,
    PendingBinding,
    PendingClarification,
    PendingSourceBinding,
    PendingTransitionProposal,
    StructuredClause,
    TurnCommand,
    TurnEvent,
    TurnResult,
)

__all__ = [
    "CAPABILITY_ALLOWLIST",
    "CanonicalGoal",
    "CanonicalTurnRequest",
    "ClauseAction",
    "ClauseSource",
    "CurrentUtteranceParse",
    "EntitySpan",
    "PendingBinding",
    "PendingClarification",
    "PendingSourceBinding",
    "PendingTransitionProposal",
    "StructuredClause",
    "TurnCommand",
    "TurnEvent",
    "TurnResult",
]
