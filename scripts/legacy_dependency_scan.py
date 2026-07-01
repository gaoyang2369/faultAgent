"""Scan TaskType and intent_stack dependencies before legacy-field deletion."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "trash" / "run"
JSON_OUTPUT = OUTPUT_DIR / "legacy_dependency_scan.json"
MD_OUTPUT = OUTPUT_DIR / "legacy_dependency_scan.md"

SCAN_DIRS = ("fault_diagnosis", "tests", "scripts", "agent_fronted")
SCAN_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".vue", ".ts", ".js"}
EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
    ".vite",
    ".nuxt",
    "coverage",
}
EXCLUDED_FILES = {
    "scripts/legacy_dependency_scan.py",
    "scripts/legacy_deprecation_check.py",
}

TASKTYPE_PATTERNS = (
    re.compile(r"\bTaskType\b"),
    re.compile(r"\bprimary_task_type\b"),
    re.compile(r"\bcandidate_task_types\b"),
)
INTENT_STACK_PATTERNS = (re.compile(r"\bintent_stack\b"),)
WRITE_PATTERNS = (
    re.compile(r"\b{field}\s*="),
    re.compile(r"\"{field}\"\s*:"),
    re.compile(r"'{field}'\s*:"),
    re.compile(r"\.{field}\s*="),
)


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    snippet: str

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "line": self.line, "snippet": self.snippet}


def run_scan(root: Path = ROOT) -> dict[str, object]:
    files = list(_iter_files(root))
    task_hits = _collect_hits(files, TASKTYPE_PATTERNS)
    intent_hits = _collect_hits(files, INTENT_STACK_PATTERNS)
    task_writes = _filter_writes(task_hits, fields=("TaskType", "primary_task_type", "candidate_task_types"))
    intent_writes = _filter_writes(intent_hits, fields=("intent_stack",))
    task_reads = _subtract_hits(task_hits, task_writes)
    intent_reads = _subtract_hits(intent_hits, intent_writes)

    payload: dict[str, object] = {
        "schema_version": "legacy_dependency_scan.v1",
        "root": str(root),
        "summary": {
            "task_type_read_files": len(_paths(task_reads)),
            "task_type_write_files": len(_paths(task_writes)),
            "intent_stack_read_files": len(_paths(intent_reads)),
            "intent_stack_write_files": len(_paths(intent_writes)),
            "test_or_eval_dependency_files": len(_paths(_category_hits([*task_hits, *intent_hits], "test_or_eval"))),
            "frontend_dependency_files": len(_paths(_category_hits([*task_hits, *intent_hits], "frontend"))),
            "artifact_schema_dependency_files": len(_paths(_category_hits([*task_hits, *intent_hits], "artifact_schema"))),
            "policy_dependency_files": len(_paths(_category_hits([*task_hits, *intent_hits], "policy_logic"))),
        },
        "task_type": {
            "readers": [hit.to_dict() for hit in task_reads],
            "writers": [hit.to_dict() for hit in task_writes],
        },
        "intent_stack": {
            "readers": [hit.to_dict() for hit in intent_reads],
            "writers": [hit.to_dict() for hit in intent_writes],
        },
        "categories": {
            "test_or_eval": [hit.to_dict() for hit in _category_hits([*task_hits, *intent_hits], "test_or_eval")],
            "frontend": [hit.to_dict() for hit in _category_hits([*task_hits, *intent_hits], "frontend")],
            "artifact_schema": [hit.to_dict() for hit in _category_hits([*task_hits, *intent_hits], "artifact_schema")],
            "policy_logic": [hit.to_dict() for hit in _category_hits([*task_hits, *intent_hits], "policy_logic")],
        },
        "readiness": {
            "can_delete_task_type_now": False,
            "can_delete_intent_stack_now": False,
            "reason": "TaskType/primary_task_type and intent_stack are still consumed by workflow policy, planner diff, tests/evals, SSE payloads, and artifact-compatible schemas.",
        },
    }
    return payload


def write_outputs(payload: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    MD_OUTPUT.write_text(_to_markdown(payload), encoding="utf-8")


def main() -> None:
    payload = run_scan(ROOT)
    write_outputs(payload)
    print(json.dumps({"json": str(JSON_OUTPUT), "markdown": str(MD_OUTPUT), "summary": payload["summary"]}, ensure_ascii=False, indent=2))


def _iter_files(root: Path) -> Iterable[Path]:
    for directory in SCAN_DIRS:
        base = root / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            rel = path.relative_to(root).as_posix()
            if rel in EXCLUDED_FILES:
                continue
            if path.is_file() and path.suffix in SCAN_SUFFIXES:
                yield path


def _collect_hits(files: Iterable[Path], patterns: tuple[re.Pattern[str], ...]) -> list[Hit]:
    hits: list[Hit] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for index, line in enumerate(lines, start=1):
            if any(pattern.search(line) for pattern in patterns):
                hits.append(Hit(path=rel, line=index, snippet=line.strip()[:240]))
    return hits


def _filter_writes(hits: list[Hit], *, fields: tuple[str, ...]) -> list[Hit]:
    result: list[Hit] = []
    for hit in hits:
        for field in fields:
            if any(re.compile(pattern.pattern.format(field=re.escape(field))).search(hit.snippet) for pattern in WRITE_PATTERNS):
                result.append(hit)
                break
    return _unique_hits(result)


def _subtract_hits(all_hits: list[Hit], write_hits: list[Hit]) -> list[Hit]:
    write_keys = {(hit.path, hit.line, hit.snippet) for hit in write_hits}
    return [hit for hit in _unique_hits(all_hits) if (hit.path, hit.line, hit.snippet) not in write_keys]


def _category_hits(hits: list[Hit], category: str) -> list[Hit]:
    if category == "test_or_eval":
        return [hit for hit in _unique_hits(hits) if hit.path.startswith("tests/") or "eval" in hit.path]
    if category == "frontend":
        return [hit for hit in _unique_hits(hits) if hit.path.startswith("agent_fronted/")]
    if category == "artifact_schema":
        return [
            hit
            for hit in _unique_hits(hits)
            if "artifact" in hit.path or "contracts.py" in hit.path or "output/payloads.py" in hit.path
        ]
    if category == "policy_logic":
        return [
            hit
            for hit in _unique_hits(hits)
            if any(part in hit.path for part in ("workflow/policies.py", "workflow/evidence_gap.py", "stages.py"))
        ]
    return []


def _paths(hits: list[Hit]) -> set[str]:
    return {hit.path for hit in hits}


def _unique_hits(hits: list[Hit]) -> list[Hit]:
    seen: set[tuple[str, int, str]] = set()
    result: list[Hit] = []
    for hit in hits:
        key = (hit.path, hit.line, hit.snippet)
        if key not in seen:
            seen.add(key)
            result.append(hit)
    return result


def _to_markdown(payload: dict[str, object]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Legacy TaskType / intent_stack Dependency Scan",
        "",
        "## Summary",
        "",
    ]
    for key, value in dict(summary).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Readiness",
            "",
            "- `can_delete_task_type_now`: `false`",
            "- `can_delete_intent_stack_now`: `false`",
            "- Reason: TaskType/primary_task_type and intent_stack still participate in workflow policy, planner diff, tests/evals, SSE payloads, and artifact-compatible schemas.",
            "",
        ]
    )
    for title, key in (
        ("TaskType Readers", ("task_type", "readers")),
        ("TaskType Writers", ("task_type", "writers")),
        ("intent_stack Readers", ("intent_stack", "readers")),
        ("intent_stack Writers", ("intent_stack", "writers")),
        ("Policy Logic Dependencies", ("categories", "policy_logic")),
        ("Test/Eval Dependencies", ("categories", "test_or_eval")),
        ("Frontend Dependencies", ("categories", "frontend")),
        ("Artifact Schema Dependencies", ("categories", "artifact_schema")),
    ):
        lines.append(f"## {title}")
        lines.append("")
        entries = _nested(payload, key)
        if not entries:
            lines.append("- None found.")
        else:
            for item in entries[:80]:
                lines.append(f"- `{item['path']}:{item['line']}` {item['snippet']}")
            if len(entries) > 80:
                lines.append(f"- ... truncated, total `{len(entries)}` hits.")
        lines.append("")
    return "\n".join(lines)


def _nested(payload: dict[str, object], keys: tuple[str, str]) -> list[dict[str, object]]:
    first = payload.get(keys[0], {})
    if not isinstance(first, dict):
        return []
    value = first.get(keys[1], [])
    return value if isinstance(value, list) else []


if __name__ == "__main__":
    main()
