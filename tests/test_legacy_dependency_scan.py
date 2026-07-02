from pathlib import Path

from scripts.legacy_dependency_scan import JSON_OUTPUT, MD_OUTPUT, run_scan, write_outputs


def test_legacy_dependency_scan_reports_new_summary_buckets() -> None:
    payload = run_scan(Path(__file__).resolve().parents[1])
    summary = payload["summary"]

    assert set(summary) == {"internal_forbidden_hits", "compat_allowed_hits", "legacy_archived_hits"}
    assert summary["internal_forbidden_hits"] == 0
    assert summary["compat_allowed_hits"] >= 1


def test_legacy_dependency_scan_writes_json_and_markdown() -> None:
    payload = run_scan(Path(__file__).resolve().parents[1])
    write_outputs(payload)

    assert JSON_OUTPUT.exists()
    assert MD_OUTPUT.exists()
    assert "Legacy Dependency Scan" in MD_OUTPUT.read_text(encoding="utf-8")
