"""Summarize retired references and exact legacy execution-authority debt."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scripts.legacy_authority_scan import (
    ALLOWLIST_PATH,
    compare_with_allowlist,
    grouped_counts,
    load_allowlist,
    scan_authority_reads,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "trash" / "run"
JSON_OUTPUT = OUTPUT_DIR / "legacy_dependency_scan.json"
MD_OUTPUT = OUTPUT_DIR / "legacy_dependency_scan.md"

SCAN_DIRS = ("fault_diagnosis", "scripts", "tests", "docs", "trash")
SCAN_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".vue", ".ts", ".js"}
EXCLUDED_DIRS = {"__pycache__", ".git", ".pytest_cache", "node_modules", "dist", "build", ".vite", ".nuxt"}
EXCLUDED_PREFIXES = ("trash/run/",)
SELF_FILES = {"scripts/legacy_dependency_scan.py", "scripts/goal_native_cutover_check.py"}

PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bTaskType\b",
        r"\bprimary_task_type\b",
        r"\bcandidate_task_types\b",
        r"\bintent_stack\b",
        r"\bshadow_plan\b",
        r"\bplanning_diff\b",
        r"\bplanner_gate\b",
        r"\bPlannerGate\b",
        r"\bPlanningDiff\b",
        r"\blegacy_policy\b",
        r"\bparity\b",
        r"\bmigration_readiness\b",
        r"\bsafe_to_migrate\b",
        r"\bfallback_to_legacy\b",
    )
)

COMPAT_ALLOWED_PREFIXES = (
    "fault_diagnosis/server/devtools/dev_mode.py",
    "tests/",
    "docs/",
)
LEGACY_ARCHIVED_PREFIXES = ("trash/",)


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    snippet: str

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "line": self.line, "snippet": self.snippet}


def run_scan(root: Path = ROOT) -> dict[str, object]:
    hits = _collect_hits(_iter_files(root), root)
    internal_hits = [hit for hit in hits if _category(hit.path) == "internal_forbidden"]
    allowed_hits = [hit for hit in hits if _category(hit.path) == "compat_allowed"]
    archived_hits = [hit for hit in hits if _category(hit.path) == "legacy_archived"]
    observed_debt = scan_authority_reads(root)
    comparison = compare_with_allowlist(observed_debt, load_allowlist(root / ALLOWLIST_PATH.relative_to(ROOT)))
    payload: dict[str, object] = {
        "schema_version": "legacy_dependency_scan.v3",
        "root": str(root),
        "allowlist": str(root / ALLOWLIST_PATH.relative_to(ROOT)),
        "summary": {
            "internal_forbidden_hits": len(internal_hits),
            "compat_allowed_hits": len(allowed_hits),
            "legacy_archived_hits": len(archived_hits),
            "accepted_authority_debt_hits": len(comparison["accepted"]),
            "unexpected_authority_hits": len(comparison["unexpected"]),
            "stale_allowlist_entries": len(comparison["stale"]),
            "authority_debt_by_component": grouped_counts(comparison["accepted"]),
        },
        "internal_forbidden_hits": [hit.to_dict() for hit in internal_hits],
        "compat_allowed_hits": [hit.to_dict() for hit in allowed_hits],
        "legacy_archived_hits": [hit.to_dict() for hit in archived_hits],
        "accepted_authority_debt": comparison["accepted"],
        "unexpected_authority_hits": comparison["unexpected"],
        "stale_allowlist_entries": comparison["stale"],
    }
    return payload


def write_outputs(payload: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    MD_OUTPUT.write_text(_to_markdown(payload), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print the full JSON payload.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Require the observed authority debt to match the checked-in allowlist exactly.",
    )
    args = parser.parse_args(argv)
    payload = run_scan(ROOT)
    write_outputs(payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps({"json": str(JSON_OUTPUT), "markdown": str(MD_OUTPUT), "summary": payload["summary"]}, ensure_ascii=False, indent=2))
    summary = dict(payload["summary"])
    failed = any(
        int(summary[key]) > 0
        for key in ("internal_forbidden_hits", "unexpected_authority_hits", "stale_allowlist_entries")
    )
    return 1 if failed else 0


def _iter_files(root: Path) -> Iterable[Path]:
    for directory in SCAN_DIRS:
        base = root / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            rel = path.relative_to(root).as_posix()
            if rel.startswith(EXCLUDED_PREFIXES):
                continue
            if rel in SELF_FILES:
                continue
            if path.is_file() and path.suffix in SCAN_SUFFIXES:
                yield path


def _collect_hits(files: Iterable[Path], root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(root).as_posix()
        for index, line in enumerate(lines, start=1):
            if any(pattern.search(line) for pattern in PATTERNS):
                hits.append(Hit(path=rel, line=index, snippet=line.strip()[:240]))
    return hits


def _category(path: str) -> str:
    if path.startswith(LEGACY_ARCHIVED_PREFIXES):
        return "legacy_archived"
    if any(path.startswith(prefix) for prefix in COMPAT_ALLOWED_PREFIXES):
        return "compat_allowed"
    return "internal_forbidden"


def _to_markdown(payload: dict[str, object]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = ["# Legacy Dependency Scan", "", "## Summary", ""]
    for key in (
        "internal_forbidden_hits",
        "compat_allowed_hits",
        "legacy_archived_hits",
        "accepted_authority_debt_hits",
        "unexpected_authority_hits",
        "stale_allowlist_entries",
    ):
        lines.append(f"- `{key}`: `{summary.get(key, 0)}`")
    lines.append(f"- `authority_debt_by_component`: `{summary.get('authority_debt_by_component', {})}`")
    for key, title in (
        ("internal_forbidden_hits", "Internal Forbidden Hits"),
        ("compat_allowed_hits", "Compat Allowed Hits"),
        ("legacy_archived_hits", "Legacy Archived Hits"),
        ("accepted_authority_debt", "Accepted Legacy Authority Debt"),
        ("unexpected_authority_hits", "Unexpected Legacy Authority Reads"),
        ("stale_allowlist_entries", "Stale Allowlist Entries"),
    ):
        lines.extend(["", f"## {title}", ""])
        entries = payload.get(key) or []
        if not isinstance(entries, list) or not entries:
            lines.append("- None found.")
            continue
        for item in entries[:160]:
            if "snippet" in item:
                lines.append(f"- `{item['path']}:{item['line']}` {item['snippet']}")
            else:
                location = f"{item['path']}:{item.get('line', '?')}::{item['symbol']}"
                lines.append(
                    f"- `{item['component']}` `{location}` `{item['authority']}` "
                    f"purpose=`{item['purpose']}` usage=`{item['usage']}` expression=`{item['expression']}`"
                )
        if len(entries) > 120:
            lines.append(f"- ... truncated, total `{len(entries)}` hits.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
