#!/usr/bin/env python3
"""Read-only decision-entry audit for the Agent Engine V2 staged cutover."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "fault_diagnosis"

RG_CHECKS = {
    "semantic_intent": r"semantic_intent|task_family|requested_action",
    "artifact_payload": r"\.payload\b|payload\.get\(|artifacts\[|artifacts\.get\(",
    "final_content": r"final_content|final_answer|composite_output.*content|content_text",
    "artifact_representation": r"ArtifactEnvelope|RuntimeState|artifact_envelopes|artifacts_by_id",
    "source_selection": r"target_artifact_id|source_artifact_refs|selected_artifact|_select_target",
    "legacy_boundary": r"legacy|compat|serializer|sse_projection|diagnosis_payload",
}

BOUNDARY_PARTS = {
    "server/agent_gateway",
    "server/http",
    "server/use_cases/conversation_persistence.py",
    "agent/output/sse_projection.py",
    "agent/output/diagnosis_payload.py",
    "platform/observability",
}

DECISION_CALL_MARKERS = {
    "authorize",
    "compile",
    "validate",
    "route",
    "select",
    "resolve",
    "build_goal",
    "build_target",
    "prepare",
    "execute",
    "render",
    "guardrail",
}

FINAL_ORCHESTRATORS = {
    "build_output_frame",
    "project_complete",
    "project_token",
    "_append_assistant_message_from_complete",
    "stream_events_with_persistence",
    "adapt_sse_payload",
    "adapt_sse_chunk",
}

FORMAT_HELPER_PREFIXES = ("_render_", "_status_", "_deliverable_body", "_line", "_numbered")


@dataclass(frozen=True)
class Evidence:
    file: str
    line: int
    symbol: str
    category: str
    function: str
    snippet: str


class SourceAudit(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.relative = path.relative_to(ROOT).as_posix()
        self.source = source
        self.lines = source.splitlines()
        self.parents: list[ast.AST] = []
        self.functions: list[str] = []
        self.classes: list[str] = []
        self.semantic: list[Evidence] = []
        self.payload: list[Evidence] = []
        self.final_content: list[Evidence] = []
        self.representations: list[Evidence] = []

    def visit(self, node: ast.AST) -> Any:
        self.parents.append(node)
        try:
            return super().visit(node)
        finally:
            self.parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.functions.append(node.name)
        try:
            if node.name in FINAL_ORCHESTRATORS and _function_mentions_final_content(node):
                self.final_content.append(self._evidence(node, node.name, "content orchestration entrypoint"))
            self.generic_visit(node)
        finally:
            self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self.classes.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self.classes.pop()

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id in {"semantic_intent", "task_family", "requested_action"}:
            self.semantic.append(self._evidence(node, node.id, self._category(node)))
        if node.id in {"artifacts", "artifact_envelopes"} and isinstance(node.ctx, ast.Load):
            self.payload.append(self._evidence(node, "RuntimeState alias", self._category(node)))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        if node.attr in {"semantic_intent", "task_family", "requested_action"}:
            self.semantic.append(self._evidence(node, node.attr, self._category(node)))
        if node.attr == "payload" and isinstance(node.ctx, ast.Load):
            self.payload.append(self._evidence(node, ".payload", self._category(node)))
        if (
            node.attr == "get"
            and isinstance(node.value, ast.Name)
            and node.value.id == "payload"
            and self._artifact_payload_scope()
        ):
            self.payload.append(self._evidence(node, "payload.get", self._category(node)))
        if node.attr in {"final_content", "final_answer", "content_text"}:
            self.final_content.append(self._evidence(node, node.attr, self._final_category(node)))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        key = _literal_string(node.slice)
        if key in {"semantic_intent", "task_family", "requested_action"}:
            self.semantic.append(self._evidence(node, key, self._category(node)))
        if key in {"final_content", "final_answer", "content", "content_text"}:
            self.final_content.append(self._evidence(node, key, self._final_category(node)))
        if key in {"payload", "artifacts", "artifact_envelopes", "artifacts_by_id"} and isinstance(node.ctx, ast.Load):
            self.payload.append(self._evidence(node, key, self._category(node)))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        target = node.target.id if isinstance(node.target, ast.Name) else ""
        class_name = self.classes[-1] if self.classes else ""
        if target in {"semantic_intent", "task_family", "requested_action"}:
            self.semantic.append(self._evidence(node, target, "schema/definition"))
        if class_name == "RuntimeState" and target in {"artifacts", "artifact_envelopes"}:
            self.representations.append(self._evidence(node, f"RuntimeState.{target}", "schema/definition"))
        if class_name.endswith("ArtifactEnvelope") and target == "payload":
            self.representations.append(self._evidence(node, f"{class_name}.payload", "schema/definition"))
        self.generic_visit(node)

    def _category(self, node: ast.AST) -> str:
        if _is_write(node):
            return "write"
        if self._is_boundary():
            return "trace/api/legacy compatibility read"
        if self._is_decision_context(node):
            return "business decision"
        return "ordinary read"

    def _final_category(self, node: ast.AST) -> str:
        function = self.functions[-1] if self.functions else "<module>"
        if function.startswith(FORMAT_HELPER_PREFIXES):
            return "internal formatting helper"
        if function in FINAL_ORCHESTRATORS:
            return "content orchestration entrypoint"
        if self._is_boundary():
            return "trace/api/legacy compatibility read"
        return "ordinary read"

    def _is_boundary(self) -> bool:
        path = self.relative.removeprefix("fault_diagnosis/")
        return any(path == part or path.startswith(f"{part}/") for part in BOUNDARY_PARTS)

    def _artifact_payload_scope(self) -> bool:
        path = self.relative.removeprefix("fault_diagnosis/")
        return path.startswith(
            (
                "agent/",
                "domain/artifacts/",
                "domain/context/",
                "domain/diagnosis/",
                "platform/persistence/diagnosis_artifacts/",
                "server/agent_gateway/",
            )
        )

    def _is_decision_context(self, node: ast.AST) -> bool:
        for parent in reversed(self.parents[:-1]):
            if isinstance(parent, (ast.If, ast.IfExp, ast.Match, ast.While, ast.comprehension)):
                return True
            if isinstance(parent, ast.Call):
                name = _call_name(parent.func).casefold()
                if any(marker in name for marker in DECISION_CALL_MARKERS):
                    return True
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                break
        return False

    def _evidence(self, node: ast.AST, symbol: str, category: str) -> Evidence:
        line = int(getattr(node, "lineno", 0) or 0)
        snippet = self.lines[line - 1].strip() if 0 < line <= len(self.lines) else ""
        return Evidence(
            file=self.relative,
            line=line,
            symbol=symbol,
            category=category,
            function=self.functions[-1] if self.functions else "<module>",
            snippet=snippet[:240],
        )


def audit() -> dict[str, Any]:
    audits: list[SourceAudit] = []
    parse_errors: list[dict[str, str]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            parse_errors.append({"file": path.relative_to(ROOT).as_posix(), "error": str(exc)})
            continue
        visitor = SourceAudit(path, source)
        visitor.visit(tree)
        audits.append(visitor)

    semantic = _dedupe(item for audit_item in audits for item in audit_item.semantic)
    payload = _dedupe(item for audit_item in audits for item in audit_item.payload)
    final_content = _dedupe(item for audit_item in audits for item in audit_item.final_content)
    representations = _dedupe(item for audit_item in audits for item in audit_item.representations)
    internal_payload = [item for item in payload if item.category != "trace/api/legacy compatibility read"]
    boundary_payload = [item for item in payload if item.category == "trace/api/legacy compatibility read"]
    alias_reads = [item for item in payload if item.symbol == "RuntimeState alias"]
    semantic_decisions = [item for item in semantic if item.category == "business decision"]
    content_entries = [item for item in final_content if item.category == "content orchestration entrypoint"]
    boundary_content = [item for item in final_content if item.category == "trace/api/legacy compatibility read"]

    rows = [
        _row("semantic intent decision entries", semantic_decisions),
        _row("artifact internal representations", representations),
        _row("internal payload read entries", internal_payload),
        _row("RuntimeState alias read entries", alias_reads),
        _row("final content orchestration entries", content_entries),
        _row("boundary/legacy compatibility reads", _dedupe([*boundary_payload, *boundary_content])),
    ]
    return {
        "root": str(ROOT),
        "scope": "fault_diagnosis/**/*.py",
        "counting_rule": "unique (file, line, symbol); internal formatting helpers excluded from final orchestration count",
        "rg_checks": _run_rg_checks(),
        "summary_rows": rows,
        "classifications": {
            "semantic_intent": _group_categories(semantic),
            "artifact_payload": _group_categories(payload),
            "final_content": _group_categories(final_content),
        },
        "parse_errors": parse_errors,
    }


def _run_rg_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, pattern in RG_CHECKS.items():
        process = subprocess.run(
            ["rg", "-n", "--glob", "*.py", pattern, str(SOURCE_ROOT)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        matches = []
        for line in process.stdout.splitlines():
            match = re.match(r"(.+?):(\d+):(.*)", line)
            if not match:
                continue
            path = Path(match.group(1))
            matches.append(
                {
                    "file": path.relative_to(ROOT).as_posix() if path.is_absolute() else path.as_posix(),
                    "line": int(match.group(2)),
                    "snippet": match.group(3).strip()[:240],
                }
            )
        checks.append({"name": name, "pattern": pattern, "exit_code": process.returncode, "match_count": len(matches), "matches": matches})
    return checks


def _row(name: str, entries: Iterable[Evidence]) -> dict[str, Any]:
    evidence = _dedupe(entries)
    return {"metric": name, "count": len(evidence), "evidence": [asdict(item) for item in evidence]}


def _group_categories(entries: Iterable[Evidence]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in _dedupe(entries):
        grouped.setdefault(item.category, []).append(asdict(item))
    return grouped


def _dedupe(entries: Iterable[Evidence]) -> list[Evidence]:
    by_key = {(item.file, item.line, item.symbol): item for item in entries if item.line > 0}
    return sorted(by_key.values(), key=lambda item: (item.file, item.line, item.symbol))


def _literal_string(node: ast.AST) -> str:
    return str(node.value) if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_call_name(node.value)}.{node.attr}".strip(".")
    return ""


def _is_write(node: ast.AST) -> bool:
    context = getattr(node, "ctx", None)
    return isinstance(context, (ast.Store, ast.Del))


def _function_mentions_final_content(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    wanted = {"final_content", "final_answer", "content", "content_text", "composite_output"}
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and item.id in wanted:
            return True
        if isinstance(item, ast.Attribute) and item.attr in wanted:
            return True
        if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value in wanted:
            return True
        if isinstance(item, ast.keyword) and item.arg in wanted:
            return True
    return False


def render_text(result: dict[str, Any]) -> str:
    lines = [
        "Agent Engine V2 cutover read-only audit",
        f"root: {result['root']}",
        f"scope: {result['scope']}",
        f"counting: {result['counting_rule']}",
        "",
        "Six-line summary",
        "metric | count",
        "--- | ---:",
    ]
    for row in result["summary_rows"]:
        lines.append(f"{row['metric']} | {row['count']}")
    lines.extend(["", "Evidence by summary row"])
    for row in result["summary_rows"]:
        lines.extend(["", f"[{row['metric']}] count={row['count']}"])
        for item in row["evidence"]:
            lines.append(
                f"- {item['file']}:{item['line']} | {item['category']} | {item['function']} | {item['symbol']} | {item['snippet']}"
            )
    lines.extend(["", "RG checks"])
    for check in result["rg_checks"]:
        lines.append(f"- {check['name']}: matches={check['match_count']} exit={check['exit_code']} pattern={check['pattern']}")
    if result["parse_errors"]:
        lines.extend(["", "Parse errors", json.dumps(result["parse_errors"], ensure_ascii=False, indent=2)])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print the complete machine-readable audit as JSON.")
    parser.add_argument("--json-output", type=Path, help="Optionally archive the JSON result (use /tmp for Phase 0).")
    args = parser.parse_args()
    result = audit()
    if args.json_output:
        args.json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render_text(result))
    return 1 if result["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
