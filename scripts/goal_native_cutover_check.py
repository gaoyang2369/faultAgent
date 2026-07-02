"""Fail when retired legacy planning contracts leak into production internals."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("fault_diagnosis", "scripts")
SCAN_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".vue", ".ts", ".js"}
EXCLUDED_DIRS = {"__pycache__", ".git", ".pytest_cache", "node_modules", "dist", "build", ".vite", ".nuxt"}
ALLOW_PREFIXES = (
    "fault_diagnosis/single_agent/compat/legacy_intent.py",
    "fault_diagnosis/single_agent/output/",
    "fault_diagnosis/single_agent/artifacts.py",
    "fault_diagnosis/runtime/dev_mode.py",
    "tests/",
    "docs/",
    "trash/",
)
ALLOW_FILES = {"scripts/goal_native_cutover_check.py", "scripts/legacy_dependency_scan.py"}

FORBIDDEN_PATTERNS = tuple(
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


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    snippet: str

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "line": self.line, "snippet": self.snippet}


def run_check(root: Path = ROOT) -> dict[str, object]:
    hits = _collect_hits(_iter_files(root), root)
    forbidden = [hit for hit in hits if not _allowed(hit.path)]
    allowed = [hit for hit in hits if _allowed(hit.path)]
    return {
        "schema_version": "goal_native_cutover_check.v1",
        "summary": {"internal_forbidden_hits": len(forbidden), "compat_allowed_hits": len(allowed)},
        "internal_forbidden_hits": [hit.to_dict() for hit in forbidden],
        "compat_allowed_hits": [hit.to_dict() for hit in allowed],
    }


def main() -> int:
    payload = run_check(ROOT)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if payload["summary"]["internal_forbidden_hits"] else 0


def _iter_files(root: Path) -> Iterable[Path]:
    for directory in SCAN_DIRS:
        base = root / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if any(part in EXCLUDED_DIRS for part in path.parts):
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
            if any(pattern.search(line) for pattern in FORBIDDEN_PATTERNS):
                hits.append(Hit(path=rel, line=index, snippet=line.strip()[:240]))
    return hits


def _allowed(path: str) -> bool:
    return path in ALLOW_FILES or path.startswith(ALLOW_PREFIXES)


if __name__ == "__main__":
    sys.exit(main())
