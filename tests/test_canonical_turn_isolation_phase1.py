from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ENTRY_PATHS = (
    "fault_diagnosis/server/use_cases/chat_service.py",
    "fault_diagnosis/server/agent_gateway/streaming.py",
)


def _python_files(path: Path):
    if path.is_file():
        yield path
    elif path.is_dir():
        yield from path.rglob("*.py")


def test_phase2_production_entries_do_not_construct_or_orchestrate_engine() -> None:
    violations = []
    for relative in PRODUCTION_ENTRY_PATHS:
        for path in _python_files(ROOT / relative):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    called = node.func
                    name = called.id if isinstance(called, ast.Name) else called.attr if isinstance(called, ast.Attribute) else ""
                    if name in {"AgentEngineV2", "ConversationTurnCoordinator"}:
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")

    assert violations == []
    chat = (ROOT / "fault_diagnosis/server/use_cases/chat_service.py").read_text(encoding="utf-8")
    assert "get_production_turn_coordinator" in chat
    assert ".preview_turn(context)" in chat
    assert ".stream_turn(" in chat


def test_phase2_coordinator_exposes_preview_and_execute_without_embedding_runtime() -> None:
    from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator

    public_methods = {name for name in dir(ConversationTurnCoordinator) if not name.startswith("_")}
    assert {"preview", "preview_turn", "execute_turn"}.issubset(public_methods)
    assert not public_methods.intersection({"compile", "route", "validate", "run"})
