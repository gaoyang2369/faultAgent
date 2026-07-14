from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ENTRY_PATHS = (
    "fault_diagnosis/app.py",
    "fault_diagnosis/server",
    "fault_diagnosis/agent/engine.py",
    "fault_diagnosis/agent/cutover.py",
    "fault_diagnosis/agent/skills/router.py",
    "fault_diagnosis/agent/planning/compiler.py",
    "fault_diagnosis/agent/planning/validator.py",
    "fault_diagnosis/agent/runtime",
    "fault_diagnosis/agent/output",
)


def _python_files(path: Path):
    if path.is_file():
        yield path
    elif path.is_dir():
        yield from path.rglob("*.py")


def test_phase1_canonical_turn_is_not_imported_by_any_production_entry_or_decision_path() -> None:
    violations = []
    for relative in PRODUCTION_ENTRY_PATHS:
        for path in _python_files(ROOT / relative):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                imported = ""
                if isinstance(node, ast.Import):
                    imported = " ".join(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported = node.module or ""
                if any(
                    token in imported
                    for token in (
                        "canonical_turn",
                        "ConversationTurnCoordinator",
                        "pending_clarification_repository",
                    )
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{imported}")

    assert violations == []


def test_phase1_coordinator_has_preview_only_and_no_execution_api() -> None:
    from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator

    public_methods = {name for name in dir(ConversationTurnCoordinator) if not name.startswith("_")}
    assert public_methods == {"preview"}
    assert not public_methods.intersection({"execute", "stream", "compile", "route", "validate", "run"})
