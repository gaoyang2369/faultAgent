"""AST inventory for legacy execution-authority reads during the V2 cutover.

The Phase 0 allowlist is deliberately exact: every accepted debt item names the
component, file, containing symbol, legacy authority, read purpose and source
expression.  Later phases must delete entries as the corresponding reads are
removed; adding a new read is always reported as unexpected debt.
"""

from __future__ import annotations

import ast
import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = ROOT / "scripts" / "legacy_authority_debt_allowlist.json"

COMPONENT_PATHS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Router", ("fault_diagnosis/agent/skills/router.py",)),
    ("Compiler", ("fault_diagnosis/agent/planning/compiler.py",)),
    ("Validator", ("fault_diagnosis/agent/planning/validator.py",)),
    ("Runtime", ("fault_diagnosis/agent/runtime/",)),
    ("Output", ("fault_diagnosis/agent/output/",)),
    (
        "Transport",
        (
            "fault_diagnosis/server/agent_gateway/",
            "fault_diagnosis/server/use_cases/chat_service.py",
            "fault_diagnosis/server/http/routers/chat.py",
        ),
    ),
)

LEGACY_TYPE_NAMES = {
    "EffectiveRequestFrame",
    "IntentFrame",
    "RewriteFrame",
}
LEGACY_FIELD_NAMES = {
    "effective_semantic_intent",
    "execution_capability",
    "needs_clarification",
    "primary_intent",
    "primary_skill",
    "requested_action",
    "semantic_intent",
    "sub_intents",
    "target_artifact_id",
    "target_artifact_type",
    "user_rewrite",
}
OUTPUT_AUTHORITY_NAMES = {"compatibility_call", "requested_variant"}
TRANSPORT_AUTHORITY_NAMES = {
    "AgentEngineV2",
    "prepare_turn",
    "stream_events_with_persistence",
}


@dataclass(frozen=True, order=True)
class AuthorityRead:
    component: str
    path: str
    symbol: str
    authority: str
    usage: str
    purpose: str
    expression: str
    line: int

    def identity(self) -> tuple[str, ...]:
        return (
            self.component,
            self.path,
            self.symbol,
            self.authority,
            self.usage,
            self.purpose,
            self.expression,
        )

    def to_dict(self, *, include_line: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "component": self.component,
            "path": self.path,
            "symbol": self.symbol,
            "authority": self.authority,
            "usage": self.usage,
            "purpose": self.purpose,
            "expression": self.expression,
        }
        if include_line:
            payload["line"] = self.line
        return payload


def scan_authority_reads(root: Path = ROOT) -> list[AuthorityRead]:
    reads: list[AuthorityRead] = []
    for component, path in _iter_component_files(root):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        visitor = _AuthorityVisitor(
            component=component,
            path=path.relative_to(root).as_posix(),
            source=source,
        )
        visitor.visit(tree)
        reads.extend(visitor.reads)
    unique = {item.identity(): item for item in reads}
    return sorted(unique.values())


def load_allowlist(path: Path = ALLOWLIST_PATH) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"legacy authority allowlist has no entries list: {path}")
    return [dict(item) for item in entries]


def compare_with_allowlist(
    observed: Iterable[AuthorityRead],
    expected: Iterable[dict[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    observed_by_id = {item.identity(): item for item in observed}
    expected_by_id = {_allowlist_identity(item): dict(item) for item in expected}
    unexpected = [observed_by_id[key].to_dict() for key in sorted(observed_by_id.keys() - expected_by_id.keys())]
    stale = [expected_by_id[key] for key in sorted(expected_by_id.keys() - observed_by_id.keys())]
    accepted = [observed_by_id[key].to_dict() for key in sorted(observed_by_id.keys() & expected_by_id.keys())]
    return {"unexpected": unexpected, "stale": stale, "accepted": accepted}


def allowlist_payload(reads: Iterable[AuthorityRead]) -> dict[str, Any]:
    entries = [item.to_dict(include_line=False) for item in reads]
    return {
        "schema_version": "legacy_authority_debt_allowlist.v1",
        "policy": "Exact Phase 0 debt only. Remove entries as reads disappear; never add transitional production reads.",
        "entries": entries,
    }


def grouped_counts(entries: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name, _ in COMPONENT_PATHS}
    for item in entries:
        component = str(item.get("component") or "")
        counts[component] = counts.get(component, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-allowlist", action="store_true")
    args = parser.parse_args()
    reads = scan_authority_reads(ROOT)
    payload = allowlist_payload(reads)
    if args.write_allowlist:
        ALLOWLIST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _allowlist_identity(item: dict[str, Any]) -> tuple[str, ...]:
    fields = ("component", "path", "symbol", "authority", "usage", "purpose", "expression")
    return tuple(str(item.get(field) or "") for field in fields)


def _iter_component_files(root: Path) -> Iterable[tuple[str, Path]]:
    seen: set[Path] = set()
    for component, prefixes in COMPONENT_PATHS:
        for prefix in prefixes:
            candidate = root / prefix
            paths = candidate.rglob("*.py") if candidate.is_dir() else (candidate,)
            for path in paths:
                if not path.is_file() or path in seen or "__pycache__" in path.parts:
                    continue
                seen.add(path)
                yield component, path


class _AuthorityVisitor(ast.NodeVisitor):
    def __init__(self, *, component: str, path: str, source: str) -> None:
        self.component = component
        self.path = path
        self.source = source
        self.reads: list[AuthorityRead] = []
        self.scopes: list[str] = []
        self.parents: list[ast.AST] = []

    def visit(self, node: ast.AST) -> Any:
        self.parents.append(node)
        try:
            return super().visit(node)
        finally:
            self.parents.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self.scopes.append(node.name)
        try:
            return self.generic_visit(node)
        finally:
            self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.scopes.append(node.name)
        try:
            return self.generic_visit(node)
        finally:
            self.scopes.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self.scopes.append(node.name)
        try:
            return self.generic_visit(node)
        finally:
            self.scopes.pop()

    def visit_Name(self, node: ast.Name) -> Any:
        if isinstance(node.ctx, ast.Load):
            if node.id in LEGACY_TYPE_NAMES:
                self._record(node, node.id)
            elif self.component == "Output" and node.id in OUTPUT_AUTHORITY_NAMES:
                self._record(node, node.id)
            elif self.component == "Transport" and node.id in TRANSPORT_AUTHORITY_NAMES:
                self._record(node, node.id)
        return self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        if isinstance(node.ctx, ast.Load):
            if node.attr in LEGACY_FIELD_NAMES:
                self._record(node, node.attr)
            elif self.component == "Transport" and node.attr in TRANSPORT_AUTHORITY_NAMES:
                self._record(node, node.attr)
        return self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
            key = _literal_text(node.args[0])
            if key in LEGACY_FIELD_NAMES or (self.component == "Output" and key in OUTPUT_AUTHORITY_NAMES):
                self._record(node, key)
        return self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        if isinstance(node.ctx, ast.Load):
            key = _literal_text(node.slice)
            if key in LEGACY_FIELD_NAMES or (self.component == "Output" and key in OUTPUT_AUTHORITY_NAMES):
                self._record(node, key)
        return self.generic_visit(node)

    def _record(self, node: ast.AST, authority: str) -> None:
        expression_node = self._expression_node(node)
        expression = ast.get_source_segment(self.source, expression_node) or ast.get_source_segment(self.source, node) or authority
        self.reads.append(
            AuthorityRead(
                component=self.component,
                path=self.path,
                symbol=".".join(self.scopes) if self.scopes else "<module>",
                authority=authority,
                usage=self._usage(node),
                purpose=_purpose(self.component, authority),
                expression=" ".join(expression.strip().split()),
                line=int(getattr(node, "lineno", 0) or 0),
            )
        )

    def _expression_node(self, node: ast.AST) -> ast.AST:
        parent = self._parent(node)
        if isinstance(parent, ast.Call) and node is parent.func:
            return parent
        if isinstance(parent, (ast.BoolOp, ast.Compare, ast.UnaryOp, ast.IfExp)):
            return parent
        return node

    def _usage(self, node: ast.AST) -> str:
        ancestors = list(reversed(self.parents[:-1]))
        if any(isinstance(item, ast.arg) for item in ancestors):
            return "type_annotation"
        if any(isinstance(item, (ast.If, ast.While, ast.IfExp, ast.BoolOp, ast.Compare, ast.Assert)) for item in ancestors[:3]):
            return "control_flow"
        parent = self._parent(node)
        if isinstance(parent, ast.Call) and node is parent.func:
            return "construction_or_method_call"
        if any(isinstance(item, (ast.Call, ast.keyword)) for item in ancestors[:2]):
            return "call_argument"
        if any(isinstance(item, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) for item in ancestors[:2]):
            return "assignment_source"
        if any(isinstance(item, ast.Return) for item in ancestors[:2]):
            return "return_value"
        if any(isinstance(item, (ast.Dict, ast.List, ast.Tuple, ast.Set)) for item in ancestors[:2]):
            return "projection"
        return "read"

    def _parent(self, node: ast.AST) -> ast.AST | None:
        if len(self.parents) < 2:
            return None
        for index in range(len(self.parents) - 2, -1, -1):
            candidate = self.parents[index]
            if any(child is node for child in ast.iter_child_nodes(candidate)):
                return candidate
        return self.parents[-2]


def _literal_text(node: ast.AST) -> str:
    return str(node.value) if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _purpose(component: str, authority: str) -> str:
    if component == "Router":
        return "route_selection_or_skill_input"
    if component == "Compiler":
        return "goal_node_or_artifact_plan_construction"
    if component == "Validator":
        return "authorization_validation_or_plan_rewrite"
    if component == "Runtime":
        return "runtime_input_or_artifact_binding"
    if component == "Output":
        return "deliverable_or_answer_variant_selection"
    if component == "Transport":
        if authority in {"prepare_turn", "stream_events_with_persistence"}:
            return "turn_transaction_owned_by_transport"
        return "engine_or_plan_orchestration_owned_by_transport"
    return "legacy_execution_authority_read"


if __name__ == "__main__":
    raise SystemExit(main())
