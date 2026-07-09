"""Agent Engine V2 output and compatibility projections."""

from .answer import build_output_frame
from .artifact_projection import project_artifact_envelope
from .artifact_manifest import build_artifact_manifests, latest_focus_from_manifests
from .report import build_reportable_payload
from .sse_projection import (
    project_complete,
    project_start,
    project_task_update,
    project_token,
    project_tool_end,
    project_tool_start,
)

__all__ = [
    "build_output_frame",
    "build_artifact_manifests",
    "build_reportable_payload",
    "latest_focus_from_manifests",
    "project_artifact_envelope",
    "project_complete",
    "project_start",
    "project_task_update",
    "project_token",
    "project_tool_end",
    "project_tool_start",
]
