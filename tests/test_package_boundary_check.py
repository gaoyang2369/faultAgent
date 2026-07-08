from pathlib import Path

from scripts.package_boundary_check import run_check


ROOT = Path(__file__).resolve().parents[1]


def test_package_layers_do_not_import_against_runtime_direction() -> None:
    payload = run_check(ROOT)

    assert payload["summary"]["forbidden_hits"] == 0
