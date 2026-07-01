"""Guard deprecated TaskType / intent_stack compatibility dependencies."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "trash" / "run"
JSON_OUTPUT = OUTPUT_DIR / "legacy_deprecation_check.json"
MD_OUTPUT = OUTPUT_DIR / "legacy_deprecation_check.md"

SCHEMA_VERSION = "legacy_deprecation_check.v1"
SCAN_DIRS = ("fault_diagnosis", "tests", "scripts", "agent_fronted", "docs")
SCAN_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".vue", ".ts", ".js", ".md", ".sh"}
DOC_SUFFIXES = {".md"}
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
LEGACY_PATTERNS = (
    re.compile(r"\bTaskType\b"),
    re.compile(r"\bprimary_task_type\b"),
    re.compile(r"\bcandidate_task_types\b"),
    re.compile(r"\bintent_stack\b"),
)

ALLOWED_EXACT_PATHS = {
    "scripts/auth_acceptance_test.sh",
    "scripts/diagnosis_dry_run_acceptance_test.py",
    "scripts/diagnosis_limited_active_acceptance_test.py",
    "scripts/high_risk_dry_run_acceptance_test.py",
    "scripts/legacy_dependency_scan.py",
    "scripts/legacy_deprecation_check.py",
    "scripts/planner_gate_acceptance_test.py",
    "scripts/stream_context_goal_acceptance_test.py",
}
ALLOWED_PREFIXES = (
    "agent_fronted/",
    "tests/",
    "fault_diagnosis/single_agent/compat/",
    "fault_diagnosis/single_agent/workflow/",
    "fault_diagnosis/single_agent/planning/",
    "fault_diagnosis/single_agent/output/",
    "fault_diagnosis/single_agent/evidence/",
)
ALLOWED_SINGLE_AGENT_FILES = {
    "fault_diagnosis/single_agent/artifacts.py",
    "fault_diagnosis/single_agent/contracts.py",
    "fault_diagnosis/single_agent/flow.py",
    "fault_diagnosis/single_agent/intent.py",
    "fault_diagnosis/single_agent/planner.py",
    "fault_diagnosis/single_agent/stages.py",
    "fault_diagnosis/security/policy_engine.py",
    "fault_diagnosis/runtime/dev_mode.py",
}


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    field: str
    snippet: str
    category: str
    allowed: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "field": self.field,
            "snippet": self.snippet,
            "category": self.category,
            "allowed": self.allowed,
            "reason": self.reason,
        }


def run_check(root: Path = ROOT) -> dict[str, object]:
    hits = _collect_hits(root, _iter_files(root))
    disallowed = [hit for hit in hits if not hit.allowed and hit.category != "docs"]
    allowed = [hit for hit in hits if hit.allowed and hit.category != "docs"]
    docs = [hit for hit in hits if hit.category == "docs"]
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "summary": {
            "total_hits": len(hits),
            "allowed_dependency_hits": len(allowed),
            "disallowed_dependency_hits": len(disallowed),
            "doc_hits": len(docs),
            "disallowed_files": len({hit.path for hit in disallowed}),
            "allowed_files": len({hit.path for hit in allowed}),
        },
        "allowed": [hit.to_dict() for hit in allowed],
        "disallowed": [hit.to_dict() for hit in disallowed],
        "docs": [hit.to_dict() for hit in docs],
        "policy": {
            "status": "deprecation_phase",
            "new_internal_dependency_allowed": False,
            "fail_on_disallowed": True,
        },
    }
    return payload


def write_outputs(payload: dict[str, object], output_dir: Path = OUTPUT_DIR) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_output = output_dir / JSON_OUTPUT.name
    md_output = output_dir / MD_OUTPUT.name
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_output.write_text(_to_markdown(payload), encoding="utf-8")
    return json_output, md_output


def main() -> int:
    payload = run_check(ROOT)
    json_output, md_output = write_outputs(payload, OUTPUT_DIR)
    summary = dict(payload.get("summary") or {})
    print(
        json.dumps(
            {"json": str(json_output), "markdown": str(md_output), "summary": summary},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if int(summary.get("disallowed_dependency_hits") or 0) else 0


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


def _collect_hits(root: Path, files: Iterable[Path]) -> list[Hit]:
    hits: list[Hit] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(root).as_posix()
        for line_no, line in enumerate(lines, start=1):
            fields = _matched_fields(line)
            if not fields:
                continue
            category, allowed, reason = _classify(path, rel)
            for field in fields:
                hits.append(
                    Hit(
                        path=rel,
                        line=line_no,
                        field=field,
                        snippet=line.strip()[:240],
                        category=category,
                        allowed=allowed,
                        reason=reason,
                    )
                )
    return hits


def _matched_fields(line: str) -> list[str]:
    fields: list[str] = []
    for pattern, field in zip(
        LEGACY_PATTERNS,
        ("TaskType", "primary_task_type", "candidate_task_types", "intent_stack"),
        strict=True,
    ):
        if pattern.search(line):
            fields.append(field)
    return fields


def _classify(path: Path, rel: str) -> tuple[str, bool, str]:
    if path.suffix in DOC_SUFFIXES:
        return "docs", True, "documentation-only reference"
    if rel in ALLOWED_EXACT_PATHS:
        return "allowed", True, "allowlisted compatibility or acceptance script"
    if rel in ALLOWED_SINGLE_AGENT_FILES:
        return "allowed", True, "allowlisted single-agent compatibility surface"
    for prefix in ALLOWED_PREFIXES:
        if rel.startswith(prefix):
            return "allowed", True, f"allowlisted compatibility prefix:{prefix.rstrip('/')}"
    return "disallowed", False, "new internal legacy dependency is not allowlisted"


def _to_markdown(payload: dict[str, object]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Legacy Deprecation Check",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Policy",
            "",
            "- Status: `deprecation_phase`",
            "- New internal dependency allowed: `false`",
            "- Allowlisted references are compatibility-only or existing legacy execution blockers.",
            "",
        ]
    )
    _append_hits(lines, "Disallowed Dependencies", list(payload.get("disallowed") or []))
    _append_hits(lines, "Allowed Dependencies", list(payload.get("allowed") or []), limit=120)
    _append_hits(lines, "Documentation References", list(payload.get("docs") or []), limit=80)
    return "\n".join(lines)


def _append_hits(lines: list[str], title: str, hits: list[object], *, limit: int = 80) -> None:
    lines.append(f"## {title}")
    lines.append("")
    if not hits:
        lines.append("- None found.")
        lines.append("")
        return
    for item in hits[:limit]:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- `{item.get('path')}:{item.get('line')}` "
            f"`{item.get('field')}` {item.get('snippet')} "
            f"({item.get('reason')})"
        )
    if len(hits) > limit:
        lines.append(f"- ... truncated, total `{len(hits)}` hits.")
    lines.append("")


if __name__ == "__main__":
    sys.exit(main())
