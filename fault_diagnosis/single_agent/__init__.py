"""Restricted single-agent runtime for the minimal diagnosis path."""

from typing import TYPE_CHECKING, Any

from .contracts import AgentTrace, SingleAgentDecision, SingleAgentLimits, TraceEvent

if TYPE_CHECKING:
    from .runner import RestrictedSingleAgentRunner


def __getattr__(name: str) -> Any:
    """Keep the public runner import lazy so security helpers can reuse SQL contracts."""

    if name == "RestrictedSingleAgentRunner":
        from .runner import RestrictedSingleAgentRunner

        return RestrictedSingleAgentRunner
    raise AttributeError(name)

__all__ = [
    "AgentTrace",
    "RestrictedSingleAgentRunner",
    "SingleAgentDecision",
    "SingleAgentLimits",
    "TraceEvent",
]
