"""Helpers for parsing restricted SQL tool output."""

from __future__ import annotations

import ast
import re
from typing import Any

from .sql_safety import REAL_DATA_FALLBACK_COLUMN_NAMES


_DATETIME_RE = re.compile(
    r"(?:datetime\.)?datetime\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*,"
    r"\s*(\d{1,2})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})(?:\s*,\s*\d+)?\s*\)"
)
_DATE_RE = re.compile(r"(?:datetime\.)?date\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)")
_TIME_RE = re.compile(
    r"(?:datetime\.)?time\(\s*(\d{1,2})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})(?:\s*,\s*\d+)?\s*\)"
)
_DECIMAL_RE = re.compile(r"Decimal\(\s*(['\"])(.*?)\1\s*\)")


def parse_sql_rows(raw_output: Any) -> list[dict[str, Any]]:
    """Parse the tuple-list shape returned by the restricted SQL fallback query."""

    parsed = raw_output if isinstance(raw_output, list) else _literal_eval_sql_output(raw_output)
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


def _literal_eval_sql_output(raw_output: Any) -> Any:
    text = _normalize_python_value_literals(str(raw_output or "").strip())
    if not text:
        return []
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return []


def _normalize_python_value_literals(text: str) -> str:
    """Convert common DB driver repr values into literal_eval-safe strings."""

    text = _DATETIME_RE.sub(
        lambda match: (
            f"'{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d} "
            f"{int(match.group(4)):02d}:{int(match.group(5)):02d}:{int(match.group(6)):02d}'"
        ),
        text,
    )
    text = _DATE_RE.sub(
        lambda match: (
            f"'{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}'"
        ),
        text,
    )
    text = _TIME_RE.sub(
        lambda match: (
            f"'{int(match.group(1)):02d}:{int(match.group(2)):02d}:{int(match.group(3)):02d}'"
        ),
        text,
    )
    return _DECIMAL_RE.sub(lambda match: repr(match.group(2)), text)
