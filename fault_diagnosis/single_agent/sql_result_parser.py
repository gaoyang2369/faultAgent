"""Helpers for parsing restricted SQL tool output."""

from __future__ import annotations

import ast
from typing import Any

from .sql_safety import REAL_DATA_FALLBACK_COLUMN_NAMES


def parse_sql_rows(raw_output: str) -> list[dict[str, Any]]:
    """Parse the tuple-list shape returned by the restricted SQL fallback query."""

    text = (raw_output or "").strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, (list, tuple)):
            continue
        row = {
            column: item[index] if index < len(item) else None
            for index, column in enumerate(REAL_DATA_FALLBACK_COLUMN_NAMES)
        }
        rows.append(row)
    return rows
