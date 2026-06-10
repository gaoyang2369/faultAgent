"""Reusable safe-action policy helpers for high-risk tool execution."""

from __future__ import annotations

from typing import Any

from ..runtime.session_store import get_namespace

_TOOL_ARTIFACTS_KEY = "__tool_artifacts__"


def ensure_tool_artifact_bucket(tool_name: str) -> list[dict[str, Any]]:
    namespace = get_namespace()
    artifacts = namespace.get(_TOOL_ARTIFACTS_KEY)
    if not isinstance(artifacts, dict):
        artifacts = {}
        namespace[_TOOL_ARTIFACTS_KEY] = artifacts
    bucket = artifacts.get(tool_name)
    if not isinstance(bucket, list):
        bucket = []
        artifacts[tool_name] = bucket
    return bucket


def store_tool_artifact_metadata(tool_name: str, metadata: dict[str, Any]) -> None:
    ensure_tool_artifact_bucket(tool_name).append(metadata)


def build_safe_action_guard(
    *,
    tool_name: str,
    target_name: str,
    extension: str,
    gate: str,
    risk_level: str,
    release_ready: bool,
    review_reasons: list[str] | None = None,
    allow_draft_on_fail: bool = True,
    blocked_suffix: str = "blocked",
    draft_suffix: str = "pending-review",
) -> dict[str, Any]:
    """Build a normalized guard result for a high-risk tool action."""
    clean_target = (target_name or "artifact").strip() or "artifact"
    clean_extension = (extension or "").lstrip(".")
    normalized_gate = str(gate or "pass")
    normalized_risk = str(risk_level or "high")
    reasons = list(review_reasons or [])

    published_name = f"{clean_target}.{clean_extension}" if clean_extension else clean_target

    if release_ready and normalized_gate == "pass":
        return {
            "tool_name": tool_name,
            "action": "publish",
            "status": "published",
            "publication_status": "published",
            "release_ready": True,
            "gate": normalized_gate,
            "risk_level": normalized_risk,
            "target_filename": published_name,
            "final_filename": published_name,
            "status_text": "当前证据充分，允许直接输出正式结果。",
            "review_reasons": reasons,
        }

    if allow_draft_on_fail:
        downgraded_name = (
            f"{clean_target}-{draft_suffix}.{clean_extension}"
            if clean_extension
            else f"{clean_target}-{draft_suffix}"
        )
        return {
            "tool_name": tool_name,
            "action": "draft",
            "status": "draft",
            "publication_status": "draft",
            "release_ready": False,
            "gate": normalized_gate,
            "risk_level": normalized_risk,
            "target_filename": published_name,
            "final_filename": downgraded_name,
            "status_text": "当前证据还不足以直接正式输出，已自动降级为草稿。",
            "review_reasons": reasons,
        }

    blocked_name = (
        f"{clean_target}-{blocked_suffix}.{clean_extension}"
        if clean_extension
        else f"{clean_target}-{blocked_suffix}"
    )
    return {
        "tool_name": tool_name,
        "action": "block",
        "status": "blocked",
        "publication_status": "blocked",
        "release_ready": False,
        "gate": normalized_gate,
        "risk_level": normalized_risk,
        "target_filename": published_name,
        "final_filename": blocked_name,
        "status_text": "当前证据不足，已阻止这一步高风险输出。",
        "review_reasons": reasons,
    }
