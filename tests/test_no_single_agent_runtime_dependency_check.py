from pathlib import Path

from scripts.no_single_agent_runtime_dependency_check import run_check


ROOT = Path(__file__).resolve().parents[1]


def test_production_v2_code_does_not_import_retired_single_agent_runtime_modules() -> None:
    payload = run_check(ROOT)

    assert payload["summary"]["forbidden_hits"] == 0
    assert payload["summary"]["allowed_rollback_hits"] >= 1
