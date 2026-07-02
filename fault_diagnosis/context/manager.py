"""Public context manager facade used by the single-agent runtime."""

from __future__ import annotations

from typing import Any

from ..security.contracts import AuthContext
from .case_store import ArtifactBackedCaseStore
from .conversation_interpreter import ConversationContextInterpreter
from .contracts import ConversationDiagnosisState, ResolvedContext
from .resolver import ContextResolver


class ContextManager:
    """Resolve thread-local context with artifact projection and auth checks."""

    def __init__(
        self,
        *,
        case_store: ArtifactBackedCaseStore | None = None,
        resolver: ContextResolver | None = None,
        interpreter: ConversationContextInterpreter | None = None,
    ) -> None:
        self.case_store = case_store or ArtifactBackedCaseStore()
        self.resolver = resolver or ContextResolver()
        self.interpreter = interpreter or ConversationContextInterpreter()

    def load_state(self, thread_id: str) -> ConversationDiagnosisState:
        return self.case_store.load(thread_id)

    def resolve(
        self,
        *,
        thread_id: str,
        message: str,
        auth_context: AuthContext,
        current_payload: dict[str, Any],
        state: ConversationDiagnosisState | None = None,
        conversation_context: dict[str, Any] | None = None,
        recent_context_signals: dict[str, Any] | None = None,
    ) -> ResolvedContext:
        conversation_state = state or self.load_state(thread_id)
        signals = recent_context_signals
        if signals is None and conversation_context is not None:
            signals = self.interpreter.interpret(conversation_context)
        return self.resolver.resolve(
            thread_id=thread_id,
            message=message,
            auth_context=auth_context,
            current_payload=current_payload,
            state=conversation_state,
            recent_context_signals=signals,
        )
