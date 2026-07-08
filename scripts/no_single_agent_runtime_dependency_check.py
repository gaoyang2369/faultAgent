"""Fail when code imports the deleted legacy agent package."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("fault_diagnosis", "tests", "scripts")
SCAN_SUFFIXES = {".py"}
EXCLUDED_DIRS = {"__pycache__", ".git", ".pytest_cache"}

_DELETED_PACKAGE = "single_" "agent"

FORBIDDEN_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rf"\bfault_diagnosis\.{_DELETED_PACKAGE}\b",
        rf"from\s+\.*{_DELETED_PACKAGE}\b",
        rf"import\s+\.*{_DELETED_PACKAGE}\b",
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
    return {
        "schema_version": "no_deleted_agent_runtime_dependency_check.v1",
        "summary": {
            "forbidden_hits": len(hits),
            "allowed_rollback_hits": 0,
        },
        "forbidden_hits": [hit.to_dict() for hit in hits],
        "allowed_rollback_hits": [],
    }


def main() -> int:
    payload = run_check(ROOT)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if payload["summary"]["forbidden_hits"] else 0


def _iter_files(root: Path) -> Iterable[Path]:
    for directory in SCAN_DIRS:
        base = root / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            rel = path.relative_to(root).as_posix()
            if path.is_file() and path.suffix in SCAN_SUFFIXES:
                yield path


def _collect_hits(files: Iterable[Path], root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines, start=1):
            if any(pattern.search(line) for pattern in FORBIDDEN_PATTERNS):
                hits.append(Hit(path=rel, line=index, snippet=line.strip()[:240]))
    return hits


if __name__ == "__main__":
    sys.exit(main())
