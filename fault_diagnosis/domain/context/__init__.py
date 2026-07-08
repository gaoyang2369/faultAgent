"""Context resolution public API."""

from .case_store import ArtifactBackedCaseStore, build_case_state_snapshot, case_state_from_artifact
from .contracts import (
    CASE_STATE_SNAPSHOT_VERSION,
    CaseState,
    ContextReference,
    ConversationDiagnosisState,
    PendingAction,
    ResolvedContext,
    summarize_resolved_context,
)
from .manager import ContextManager
from .resolver import ContextResolver

__all__ = [
    "ArtifactBackedCaseStore",
    "CASE_STATE_SNAPSHOT_VERSION",
    "CaseState",
    "ContextManager",
    "ContextReference",
    "ContextResolver",
    "ConversationDiagnosisState",
    "PendingAction",
    "ResolvedContext",
    "build_case_state_snapshot",
    "case_state_from_artifact",
    "summarize_resolved_context",
]
