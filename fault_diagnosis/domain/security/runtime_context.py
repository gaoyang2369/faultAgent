"""Request-local auth propagation for synchronous LangChain tool internals."""

from __future__ import annotations

from contextvars import ContextVar, Token

from .contracts import AuthContext

_AUTH_CONTEXT: ContextVar[AuthContext | None] = ContextVar("fault_diagnosis_auth_context", default=None)


def get_current_auth_context() -> AuthContext | None:
    return _AUTH_CONTEXT.get()


def set_current_auth_context(auth: AuthContext) -> Token:
    return _AUTH_CONTEXT.set(auth)


def reset_current_auth_context(token: Token) -> None:
    _AUTH_CONTEXT.reset(token)
