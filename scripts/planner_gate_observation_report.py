from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVAL_DIR = ROOT / "tests" / "evals"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from fault_diagnosis import config
from run_plan_eval import CASE_FILE, build_test_client, load_cases, run_local_case

REPORT_DIR = ROOT / "trash" / "run"
JSON_REPORT = REPORT_DIR / "planner_gate_observation_report.json"
MD_REPORT = REPORT_DIR / "planner_gate_observation_report.md"


MODES = [
    {"name": "disabled", "enabled": False, "dry_run": True},
    {"name": "dry_run", "enabled": True, "dry_run": True},
    {"name": "active", "enabled": True, "dry_run": False},
]


def configure_gate(*, enabled: bool, dry_run: bool) -> None:
    config.ENABLE_PLANNER_GATED_EXECUTION = enabled
    config.PLANNER_GATED_DRY_RUN = dry_run
    config.PLANNER_GATED_TASK_FAMILIES = ["knowledge_lookup", "runtime_status", "reporting"]
    config.PLANNER_GATED_REQUIRE_DIFF_STATUS = ["aligned", "acceptable_diff"]
    config.PLANNER_GATED_MAX_DIFF_SEVERITY = "warning"


def plan_cases() -> list[dict[str, Any]]:
    return [case for case in load_cases(CASE_FILE) if "plan" in (case.get("eval_modes") or [])]


def collect_observations() -> list[dict[str, Any]]:
    client = build_test_client()
    cases = plan_cases()
    observations: list[dict[str, Any]] = []
    with client:
        for mode in MODES:
            configure_gate(enabled=mode["enabled"], dry_run=mode["dry_run"])
            for case in cases:
                snapshot = run_local_case(client, case)
                gate = snapshot.get("planner_gate") or {}
                diff = snapshot.get("planning_diff") or {}
                observations.append(
                    {
                        "mode": mode["name"],
                        "case_id": str(case.get("id") or ""),
                        "task_family": str(snapshot.get("task_family") or ""),
                        "selected_execution_source": str(gate.get("selected_execution_source") or "legacy_policy"),
                        "eligible": bool(gate.get("eligible", False)),
                        "fallback_to_legacy": bool(gate.get("fallback_to_legacy", True)),
                        "blockers": list(gate.get("blockers") or []),
                        "diff_status": str(diff.get("overall_status") or ""),
                        "diff_severity": str(diff.get("severity") or ""),
                        "critical_count": int(
                            (diff.get("counters") or {}).get("critical_count") or diff.get("critical_count") or 0
                        ),
                        "legacy_enabled_nodes": sorted((snapshot.get("enabled_nodes") or {}).keys()),
                        "legacy_runtime_tools": sorted(snapshot.get("planned_tools") or []),
                        "gate_enabled_nodes": sorted(gate.get("final_enabled_nodes") or []),
                        "gate_runtime_tools": sorted(gate.get("final_runtime_tools") or []),
                    }
                )
    configure_gate(enabled=False, dry_run=True)
    return observations


def summarize(observations: list[dict[str, Any]]) -> dict[str, Any]:
    disabled_baseline = {
        item["case_id"]: {
            "enabled_nodes": item["legacy_enabled_nodes"],
            "runtime_tools": item["legacy_runtime_tools"],
        }
        for item in observations
        if item["mode"] == "disabled"
    }
    blockers = Counter(blocker for item in observations for blocker in item["blockers"])
    task_families = Counter(item["task_family"] for item in observations)
    diff_status = Counter(item["diff_status"] for item in observations)
    mode_counts = Counter(item["mode"] for item in observations)
    source_counts = Counter(item["selected_execution_source"] for item in observations)
    enabled_changed = [
        item
        for item in observations
        if item["selected_execution_source"] == "planner_gated"
        and item["gate_enabled_nodes"] != disabled_baseline.get(item["case_id"], {}).get("enabled_nodes", [])
    ]
    tools_changed = [
        item
        for item in observations
        if item["selected_execution_source"] == "planner_gated"
        and item["gate_runtime_tools"] != disabled_baseline.get(item["case_id"], {}).get("runtime_tools", [])
    ]
    tools_expanded = [
        item
        for item in observations
        if item["selected_execution_source"] == "planner_gated"
        and not set(item["gate_runtime_tools"]).issubset(
            set(disabled_baseline.get(item["case_id"], {}).get("runtime_tools", []))
        )
    ]
    unauthorized_reference = [
        item
        for item in observations
        if "unauthorized_or_missing_auth_context" in item["blockers"]
    ]
    critical = [item for item in observations if item["critical_count"] > 0 or item["diff_severity"] == "critical"]
    return {
        "total_cases": len(observations),
        "disabled_count": mode_counts.get("disabled", 0),
        "dry_run_eligible_count": sum(1 for item in observations if item["mode"] == "dry_run" and item["eligible"]),
        "active_eligible_count": sum(1 for item in observations if item["mode"] == "active" and item["eligible"]),
        "selected_legacy_count": source_counts.get("legacy_policy", 0),
        "selected_planner_gated_count": source_counts.get("planner_gated", 0),
        "fallback_count": sum(1 for item in observations if item["fallback_to_legacy"]),
        "fallback_reasons_top": blockers.most_common(10),
        "task_family_distribution": dict(sorted(task_families.items())),
        "diff_status_distribution": dict(sorted(diff_status.items())),
        "blockers_distribution": dict(sorted(blockers.items())),
        "runtime_tools_changed_count": len(tools_changed),
        "enabled_nodes_changed_count": len(enabled_changed),
        "runtime_tools_expanded_count": len(tools_expanded),
        "runtime_tools_changed_cases": _compact_cases(tools_changed, disabled_baseline),
        "enabled_nodes_changed_cases": _compact_cases(enabled_changed, disabled_baseline),
        "runtime_tools_expanded_cases": _compact_cases(tools_expanded, disabled_baseline),
        "unauthorized_reference_count": len(unauthorized_reference),
        "critical_diff_count": len(critical),
    }


def _compact_cases(items: list[dict[str, Any]], baseline: dict[str, dict[str, list[str]]]) -> list[dict[str, Any]]:
    return [
        {
            "mode": item["mode"],
            "case_id": item["case_id"],
            "task_family": item["task_family"],
            "baseline_enabled_nodes": baseline.get(item["case_id"], {}).get("enabled_nodes", []),
            "gate_enabled_nodes": item["gate_enabled_nodes"],
            "baseline_runtime_tools": baseline.get(item["case_id"], {}).get("runtime_tools", []),
            "gate_runtime_tools": item["gate_runtime_tools"],
        }
        for item in items[:20]
    ]


def write_reports(summary: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Planner Gate Observation Report",
        "",
        f"- total_cases: {summary['total_cases']}",
        f"- disabled_count: {summary['disabled_count']}",
        f"- dry_run_eligible_count: {summary['dry_run_eligible_count']}",
        f"- active_eligible_count: {summary['active_eligible_count']}",
        f"- selected_legacy_count: {summary['selected_legacy_count']}",
        f"- selected_planner_gated_count: {summary['selected_planner_gated_count']}",
        f"- fallback_count: {summary['fallback_count']}",
        f"- runtime_tools_changed_count: {summary['runtime_tools_changed_count']}",
        f"- enabled_nodes_changed_count: {summary['enabled_nodes_changed_count']}",
        f"- runtime_tools_expanded_count: {summary['runtime_tools_expanded_count']}",
        f"- unauthorized_reference_count: {summary['unauthorized_reference_count']}",
        f"- critical_diff_count: {summary['critical_diff_count']}",
        "",
        "## Fallback Reasons Top",
        "",
        *[f"- {name}: {count}" for name, count in summary["fallback_reasons_top"]],
        "",
        "## Task Family Distribution",
        "",
        *[f"- {name}: {count}" for name, count in summary["task_family_distribution"].items()],
        "",
        "## Diff Status Distribution",
        "",
        *[f"- {name}: {count}" for name, count in summary["diff_status_distribution"].items()],
    ]
    MD_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    observations = collect_observations()
    summary = summarize(observations)
    write_reports(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
