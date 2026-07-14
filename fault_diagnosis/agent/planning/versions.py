"""Stable canonical plan-version policy and historical read compatibility."""

from __future__ import annotations

from dataclasses import dataclass


CANONICAL_PLAN_VERSION = "v2.canonical.validated"
HISTORICAL_CANONICAL_PLAN_VERSIONS = frozenset(
    {
        "v2.canonical-phase2.validated",
        "v2.canonical-phase3.validated",
        "v2.canonical-phase4.validated",
    }
)


@dataclass(frozen=True)
class PlanVersionResolution:
    actual_version: str
    normalized_version: str
    read_compatible: bool
    executable: bool
    compatibility_mode: str


def resolve_plan_version(version: str) -> PlanVersionResolution:
    """Classify without rewriting the source plan or persisted historical data."""

    actual = str(version or "").strip()
    if actual == CANONICAL_PLAN_VERSION:
        return PlanVersionResolution(actual, CANONICAL_PLAN_VERSION, True, True, "canonical")
    if actual in HISTORICAL_CANONICAL_PLAN_VERSIONS:
        return PlanVersionResolution(actual, CANONICAL_PLAN_VERSION, True, True, "historical_read")
    return PlanVersionResolution(actual, "", False, False, "unknown_read_only")
