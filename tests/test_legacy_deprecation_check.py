from __future__ import annotations

from pathlib import Path

from scripts.legacy_dependency_scan import run_scan
from scripts.legacy_deprecation_check import MD_OUTPUT, JSON_OUTPUT, run_check, write_outputs


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_deprecation_check_writes_reports_for_current_repo() -> None:
    payload = run_check(ROOT)
    json_output, md_output = write_outputs(payload)

    assert json_output == JSON_OUTPUT
    assert md_output == MD_OUTPUT
    assert JSON_OUTPUT.exists()
    assert MD_OUTPUT.exists()
    assert "Legacy Deprecation Check" in MD_OUTPUT.read_text(encoding="utf-8")
    assert payload["summary"]["disallowed_dependency_hits"] == 0


def test_legacy_deprecation_check_reports_disallowed_internal_dependency(tmp_path: Path) -> None:
    internal = tmp_path / "fault_diagnosis" / "new_internal"
    internal.mkdir(parents=True)
    (internal / "bad_dependency.py").write_text(
        "def f(decision):\n    return decision.intent_stack, decision.primary_task_type\n",
        encoding="utf-8",
    )
    allowed = tmp_path / "fault_diagnosis" / "single_agent" / "workflow"
    allowed.mkdir(parents=True)
    (allowed / "policies.py").write_text("from x import TaskType\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    (docs / "note.md").write_text("TaskType and intent_stack are documented here.\n", encoding="utf-8")

    payload = run_check(tmp_path)

    disallowed = payload["disallowed"]
    assert payload["summary"]["disallowed_dependency_hits"] == 2
    assert {item["field"] for item in disallowed} == {"intent_stack", "primary_task_type"}
    assert all(item["path"] == "fault_diagnosis/new_internal/bad_dependency.py" for item in disallowed)
    assert payload["summary"]["doc_hits"] == 2


def test_legacy_deprecation_check_can_write_to_custom_output_dir(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_compat.py").write_text("assert decision.intent_stack\n", encoding="utf-8")
    output_dir = tmp_path / "out"

    payload = run_check(root)
    json_output, md_output = write_outputs(payload, output_dir)

    assert json_output.exists()
    assert md_output.exists()
    assert json_output.parent == output_dir
    assert md_output.parent == output_dir
    assert payload["summary"]["disallowed_dependency_hits"] == 0


def test_phase5_4_internal_legacy_dependency_scan_metrics_drop_from_phase5_3_baseline() -> None:
    payload = run_scan(ROOT)
    summary = payload["summary"]

    assert summary["task_type_read_files"] <= 20
    assert summary["task_type_write_files"] <= 20
    assert summary["intent_stack_read_files"] <= 10
    assert summary["intent_stack_write_files"] <= 10
    assert summary["policy_dependency_files"] <= 3
