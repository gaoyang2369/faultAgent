"""Diagnosis finalization helpers for the minimal runtime path."""

from __future__ import annotations

from typing import Any


def build_diagnosis_runtime_payload(final_content: str) -> dict[str, Any]:
    """Return the raw model answer without evidence gating or grounding."""

    raw_final_content = final_content
    return {
        "raw_final_content": raw_final_content,
        "final_content": final_content,
        "grounded_final_content": final_content,
    }
