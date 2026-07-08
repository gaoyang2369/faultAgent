"""Placeholder KG runtime node for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from ..executor import NodeExecutionOutput
from ..state import RuntimeState


class KgNode:
    node_type = "kg"

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:  # noqa: ARG002
        return NodeExecutionOutput(
            status="skipped",
            output={"success": False, "skipped_reason": "not_configured", "summary": "Knowledge graph is not configured."},
        )
