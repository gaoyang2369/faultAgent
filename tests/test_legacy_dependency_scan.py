from pathlib import Path

from scripts.legacy_dependency_scan import JSON_OUTPUT, MD_OUTPUT, run_scan, write_outputs


def test_legacy_dependency_scan_finds_policy_and_intent_stack_dependencies() -> None:
    payload = run_scan(Path(__file__).resolve().parents[1])

    policy_paths = {item["path"] for item in payload["categories"]["policy_logic"]}
    task_paths = {item["path"] for item in payload["task_type"]["readers"] + payload["task_type"]["writers"]}
    intent_paths = {item["path"] for item in payload["intent_stack"]["readers"] + payload["intent_stack"]["writers"]}

    assert "fault_diagnosis/single_agent/workflow/policies.py" in policy_paths
    assert "fault_diagnosis/single_agent/workflow/evidence_gap.py" not in policy_paths
    assert "fault_diagnosis/single_agent/stages.py" not in policy_paths
    assert "fault_diagnosis/single_agent/compat/legacy_intent.py" in intent_paths
    assert any(path.startswith("tests/") for path in task_paths)
    assert any(path.startswith("tests/") for path in intent_paths)
    assert payload["readiness"]["can_delete_task_type_now"] is False
    assert payload["readiness"]["can_delete_intent_stack_now"] is False


def test_legacy_dependency_scan_writes_json_and_markdown() -> None:
    payload = run_scan(Path(__file__).resolve().parents[1])
    write_outputs(payload)

    assert JSON_OUTPUT.exists()
    assert MD_OUTPUT.exists()
    assert "Legacy TaskType / intent_stack Dependency Scan" in MD_OUTPUT.read_text(encoding="utf-8")
