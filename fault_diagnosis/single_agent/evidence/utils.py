"""Shared helpers for evidence construction."""

from __future__ import annotations

from typing import Any


def dedupe(items: list[str]) -> list[str]:
    """Return non-empty strings while preserving first-seen order."""

    return list(dict.fromkeys(str(item or "").strip() for item in items if str(item or "").strip()))


def first_non_empty(values: list[Any]) -> str | None:
    """Return the first non-empty, non-placeholder value."""

    for value in values:
        text = str(value or "").strip()
        if text and text != "-":
            return text
    return None
