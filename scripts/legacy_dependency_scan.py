"""Summarize legacy compatibility references after the goal-native cutover."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


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
        r"safe-to-migrate",
        r"fallback-to-legacy",
    )
)

COMPAT_ALLOWED_PREFIXES = (
    "fault_diagnosis/single_agent/compat/legacy_intent.py",
    "fault_diagnosis/single_agent/output/",
    "fault_diagnosis/single_agent/artifacts.py",
    "fault_diagnosis/runtime/dev_mode.py",
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
    payload: dict[str, object] = {
        "schema_version": "legacy_dependency_scan.v2",
        "root": str(root),
        "summary": {
            "internal_forbidden_hits": len(internal_hits),
            "compat_allowed_hits": len(allowed_hits),
            "legacy_archived_hits": len(archived_hits),
        },
        "internal_forbidden_hits": [hit.to_dict() for hit in internal_hits],
        "compat_allowed_hits": [hit.to_dict() for hit in allowed_hits],
        "legacy_archived_hits": [hit.to_dict() for hit in archived_hits],
    }
    return payload


def write_outputs(payload: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    MD_OUTPUT.write_text(_to_markdown(payload), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print the full JSON payload.")
    args = parser.parse_args(argv)
    payload = run_scan(ROOT)
    write_outputs(payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps({"json": str(JSON_OUTPUT), "markdown": str(MD_OUTPUT), "summary": payload["summary"]}, ensure_ascii=False, indent=2))
    return 1 if int(payload["summary"]["internal_forbidden_hits"]) > 0 else 0


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
    for key in ("internal_forbidden_hits", "compat_allowed_hits", "legacy_archived_hits"):
        lines.append(f"- `{key}`: `{summary.get(key, 0)}`")
    for key, title in (
        ("internal_forbidden_hits", "Internal Forbidden Hits"),
        ("compat_allowed_hits", "Compat Allowed Hits"),
        ("legacy_archived_hits", "Legacy Archived Hits"),
    ):
        lines.extend(["", f"## {title}", ""])
        entries = payload.get(key) or []
        if not isinstance(entries, list) or not entries:
            lines.append("- None found.")
            continue
        for item in entries[:120]:
            lines.append(f"- `{item['path']}:{item['line']}` {item['snippet']}")
        if len(entries) > 120:
            lines.append(f"- ... truncated, total `{len(entries)}` hits.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
