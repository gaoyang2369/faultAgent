from pathlib import Path

from scripts.goal_native_cutover_check import run_check
from scripts.legacy_authority_scan import AuthorityRead, compare_with_allowlist


def test_goal_native_cutover_check_ratchets_exact_phase0_authority_debt() -> None:
    payload = run_check(Path(__file__).resolve().parents[1])
    summary = payload["summary"]

    assert payload["schema_version"] == "goal_native_cutover_check.v2"
    assert summary["retired_internal_forbidden_hits"] == 0
    assert summary["accepted_authority_debt_hits"] == 0
    assert summary["unexpected_authority_hits"] == 0
    assert summary["stale_allowlist_entries"] == 0
    assert all(value == 0 for value in summary["authority_debt_by_component"].values())
    assert set(summary["authority_debt_by_component"]) == {
        "Router",
        "Compiler",
        "Validator",
        "Runtime",
        "Output",
        "Transport",
    }


def test_goal_native_cutover_debt_entries_name_file_symbol_and_read_purpose() -> None:
    payload = run_check(Path(__file__).resolve().parents[1])

    assert all(item["path"].startswith("fault_diagnosis/") for item in payload["accepted_authority_debt"])
    assert all(item["symbol"] and item["purpose"] and item["usage"] for item in payload["accepted_authority_debt"])


def test_authority_allowlist_comparison_detects_unexpected_and_stale_reads() -> None:
    observed = AuthorityRead(
        component="Router",
        path="fault_diagnosis/agent/skills/router.py",
        symbol="SkillRouter.route",
        authority="primary_skill",
        usage="control_flow",
        purpose="route_selection_or_skill_input",
        expression="route.primary_skill",
        line=42,
    )
    stale = {
        "component": "Compiler",
        "path": "fault_diagnosis/agent/planning/compiler.py",
        "symbol": "PlanCompiler.compile",
        "authority": "execution_capability",
        "usage": "assignment_source",
        "purpose": "goal_node_or_artifact_plan_construction",
        "expression": "plan.execution_capability",
    }

    result = compare_with_allowlist([observed], [stale])

    assert result["unexpected"] == [observed.to_dict()]
    assert result["stale"] == [stale]
    assert result["accepted"] == []
