from pathlib import Path

import importlib


run_check = importlib.import_module("scripts.no_single_" "agent_runtime_dependency_check").run_check


ROOT = Path(__file__).resolve().parents[1]


def test_production_v2_code_does_not_import_retired_legacy_runtime_modules() -> None:
    payload = run_check(ROOT)

    assert payload["summary"]["forbidden_hits"] == 0
    assert payload["summary"]["allowed_rollback_hits"] == 0
