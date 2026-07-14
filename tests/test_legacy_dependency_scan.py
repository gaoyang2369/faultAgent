from pathlib import Path

from scripts.legacy_dependency_scan import JSON_OUTPUT, MD_OUTPUT, run_scan, write_outputs
from scripts.legacy_authority_scan import COMPONENT_PATHS


def test_legacy_dependency_scan_reports_new_summary_buckets() -> None:
    payload = run_scan(Path(__file__).resolve().parents[1])
    summary = payload["summary"]

    assert set(summary) == {
        "internal_forbidden_hits",
        "compat_allowed_hits",
        "legacy_archived_hits",
        "accepted_authority_debt_hits",
        "unexpected_authority_hits",
        "stale_allowlist_entries",
        "authority_debt_by_component",
    }
    assert summary["internal_forbidden_hits"] == 0
    assert summary["compat_allowed_hits"] >= 0
    assert summary["accepted_authority_debt_hits"] > 0
    assert summary["unexpected_authority_hits"] == 0
    assert summary["stale_allowlist_entries"] == 0
    assert set(summary["authority_debt_by_component"]) == {name for name, _ in COMPONENT_PATHS}


def test_legacy_authority_debt_is_precise_and_grouped_by_component() -> None:
    payload = run_scan(Path(__file__).resolve().parents[1])
    entries = payload["accepted_authority_debt"]

    assert entries
    assert all(
        {
            "component",
            "path",
            "line",
            "symbol",
            "authority",
            "usage",
            "purpose",
            "expression",
        }.issubset(item)
        for item in entries
    )
    assert {item["component"] for item in entries} == {
        "Router",
        "Compiler",
        "Validator",
        "Runtime",
        "Output",
        "Transport",
    }


def test_legacy_dependency_scan_writes_json_and_markdown() -> None:
    payload = run_scan(Path(__file__).resolve().parents[1])
    write_outputs(payload)

    assert JSON_OUTPUT.exists()
    assert MD_OUTPUT.exists()
    assert "Legacy Dependency Scan" in MD_OUTPUT.read_text(encoding="utf-8")
    assert "Accepted Legacy Authority Debt" in MD_OUTPUT.read_text(encoding="utf-8")
