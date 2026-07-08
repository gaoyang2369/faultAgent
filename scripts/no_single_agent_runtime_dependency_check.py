"""Fail when production V2 code imports retired single_agent runtime modules."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("fault_diagnosis",)
SCAN_SUFFIXES = {".py"}
EXCLUDED_DIRS = {"__pycache__", ".git", ".pytest_cache"}

ALLOW_FILES = {
    "fault_diagnosis/agent_runtime/streaming.py",
    "fault_diagnosis/services/chat_service.py",
}
ALLOW_TEXT = {
    "from ..single_agent import RestrictedSingleAgentRunner",
    "from ..single_agent.planner import build_plan_snapshot",
}

FORBIDDEN_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"single_agent\.(?:flow|stages|runner|planner|workflow|output)\b",
        r"from\s+\.*single_agent\s+import\s+RestrictedSingleAgentRunner\b",
        r"from\s+\.*single_agent\.(?:flow|stages|runner|planner|workflow|output)\b",
        r"from\s+fault_diagnosis\.single_agent\.(?:flow|stages|runner|planner|workflow|output)\b",
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
    forbidden = [hit for hit in hits if not _allowed(hit)]
    allowed = [hit for hit in hits if _allowed(hit)]
    return {
        "schema_version": "no_single_agent_runtime_dependency_check.v1",
        "summary": {
            "forbidden_hits": len(forbidden),
            "allowed_rollback_hits": len(allowed),
        },
        "forbidden_hits": [hit.to_dict() for hit in forbidden],
        "allowed_rollback_hits": [hit.to_dict() for hit in allowed],
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
            if rel.startswith("fault_diagnosis/single_agent/"):
                continue
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


def _allowed(hit: Hit) -> bool:
    return hit.path in ALLOW_FILES and hit.snippet in ALLOW_TEXT


if __name__ == "__main__":
    sys.exit(main())
