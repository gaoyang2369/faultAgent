"""Compatibility facade for the new context package."""

from __future__ import annotations

from typing import Any

from ..context import (
    ArtifactBackedCaseStore,
    CaseState,
    ContextManager,
    ConversationDiagnosisState,
    ResolvedContext,
)
from ..security.contracts import AuthContext
from ..security.permissions import build_auth_context

DiagnosisCase = CaseState


def load_conversation_diagnosis_state(thread_id: str, *, limit: int = 5) -> ConversationDiagnosisState:
    """Build thread context from the latest saved diagnosis artifacts."""

    return ArtifactBackedCaseStore(limit=limit).load(thread_id)


def apply_context_resolution(
    *,
    payload: dict[str, Any],
    message: str,
    state: ConversationDiagnosisState | None,
    auth_context: AuthContext | None = None,
) -> dict[str, Any]:
    """Fill missing slots from the active case and return legacy resolution dict."""

    manager = ContextManager(case_store=ArtifactBackedCaseStore())
    resolved = manager.resolve(
        thread_id=state.thread_id if state is not None else "",
        message=message,
        auth_context=auth_context or build_auth_context(role="admin"),
        current_payload=payload,
        state=state,
    )
    return resolved.legacy_context_resolution()


__all__ = [
    "ArtifactBackedCaseStore",
    "CaseState",
    "ContextManager",
    "ConversationDiagnosisState",
    "DiagnosisCase",
    "ResolvedContext",
    "apply_context_resolution",
    "load_conversation_diagnosis_state",
]
