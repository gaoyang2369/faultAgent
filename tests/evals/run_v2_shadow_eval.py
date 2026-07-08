from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fault_diagnosis import config
from fault_diagnosis.agent_engine import AgentEngineV2
from fault_diagnosis.agent_engine.cutover import decide_v2_execution
from fault_diagnosis.agent_engine.flags import AgentEngineFlags
from fault_diagnosis.agent_engine.observability import build_plan_compare
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.diagnosis.artifact_store import clear_all_artifacts, save_thread_artifact
from fault_diagnosis.diagnosis.contracts import DiagnosisArtifactEnvelope
from fault_diagnosis.runtime.dev_mode import init_dev_state
from fault_diagnosis.security.permissions import build_auth_context
from fault_diagnosis.single_agent.planner import build_plan_snapshot

CASE_FILE = ROOT / "tests" / "evals" / "agent_workflow_cases.yaml"
FIXTURE_DIR = ROOT / "tests" / "evals" / "fixtures"
RESULTS_DIR = ROOT / "tests" / "evals" / "results"
SUMMARY_FILE = RESULTS_DIR / "v2_shadow_eval_summary.json"
MD_FILE = ROOT / "trash" / "run" / "agent-engine-v2-compare-summary.md"
PHASE9_CUTOVER_CANDIDATES = {"fault_code_explain", "runtime_status"}


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    return list(cases or [])


def build_test_client() -> TestClient:
    config.LOCAL_DEV_MODE = True
    config.DEV_AUTH_ENABLED = True
    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("agent-v2-shadow-eval-secret")
    init_dev_state(app)
    app.include_router(auth_router)
    return TestClient(app)


def load_json_fixture(kind: str, name: str | None) -> Any:
    if not name:
        return None
    with (FIXTURE_DIR / kind / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def install_artifact_fixture(thread_id: str, fixture_name: str | None) -> None:
    payload = load_json_fixture("artifacts", fixture_name)
    if not payload:
        return
    for item in payload if isinstance(payload, list) else [payload]:
        copied = dict(item)
        copied["thread_id"] = thread_id
        save_thread_artifact(DiagnosisArtifactEnvelope.model_validate(copied))


def auth_for_case(case: dict[str, Any]):
    fixture = load_json_fixture("auth", case.get("identity_fixture")) or {}
    return build_auth_context(
        role=fixture.get("role") or case.get("role") or "guest",
        user_id=fixture.get("user_id") or "eval_user",
        asset_scope=fixture.get("asset_scope") or case.get("asset_scope") or [],
        table_scope=fixture.get("allowed_tables") or [],
    )


def last_turn(case: dict[str, Any]) -> str:
    turns = case.get("turns") or []
    if not turns:
        return ""
    last = turns[-1]
    return str((last.get("message") if isinstance(last, dict) else last) or "")


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    clear_all_artifacts()
    thread_id = f"eval-v2-compare-{case['id']}"
    install_artifact_fixture(thread_id, case.get("artifact_fixture"))
    auth_context = auth_for_case(case)
    message = last_turn(case)
    legacy_plan = build_plan_snapshot(
        message=message,
        thread_id=thread_id,
        user_identity="管理员" if auth_context.role == "admin" else "游客",
        auth_context=auth_context,
    )
    v2_snapshot = AgentEngineV2().plan_only(
        raw_message=message,
        thread_id=thread_id,
        request_id=f"eval-{case['id']}",
        auth_context=auth_context,
        legacy_plan=legacy_plan,
    )
    flags = AgentEngineFlags(engine_mode="v2_shadow", skill_modes={})
    decision = decide_v2_execution(snapshot=v2_snapshot, thread_id=thread_id, flags=flags)
    return build_plan_compare(
        legacy_plan=legacy_plan,
        v2_snapshot=v2_snapshot,
        request_id=f"eval-{case['id']}",
        trace_id=f"trace-eval-{case['id']}",
        thread_id=thread_id,
        effective_skill_mode=decision.effective_mode,
        fallback_reason=decision.fallback_reason,
    )


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_skill: dict[str, dict[str, Any]] = {}
    for record in records:
        skill = str(record.get("primary_skill") or "unknown")
        bucket = by_skill.setdefault(
            skill,
            {
                "total": 0,
                "none": 0,
                "warning": 0,
                "error": 0,
                "critical": 0,
                "review_required": 0,
                "cutover_candidate": False,
            },
        )
        severity = str(record.get("severity") or "none")
        bucket["total"] += 1
        bucket[severity] = int(bucket.get(severity, 0)) + 1
        if record.get("review_required"):
            bucket["review_required"] += 1
    for skill, bucket in by_skill.items():
        bucket["cutover_candidate"] = (
            skill in PHASE9_CUTOVER_CANDIDATES
            and bucket["total"] > 0
            and bucket["error"] == 0
            and bucket["critical"] == 0
            and bucket["review_required"] == 0
        )
    return {
        "schema_version": "agent_engine_v2_compare_summary.v1",
        "total": len(records),
        "failed": sum(1 for item in records if item.get("severity") in {"error", "critical"}),
        "review_required": sum(1 for item in records if item.get("review_required")),
        "by_skill": by_skill,
        "records": records,
    }


def write_markdown(summary: dict[str, Any]) -> None:
    lines = ["# Agent Engine V2 Compare Summary", ""]
    lines.append(f"- total: `{summary.get('total', 0)}`")
    lines.append(f"- failed: `{summary.get('failed', 0)}`")
    lines.append(f"- review_required: `{summary.get('review_required', 0)}`")
    lines.extend(["", "## By Skill", ""])
    for skill, bucket in sorted((summary.get("by_skill") or {}).items()):
        lines.append(
            f"- `{skill}` total={bucket['total']} warning={bucket['warning']} "
            f"error={bucket['error']} critical={bucket['critical']} "
            f"review={bucket['review_required']} cutover_candidate={bucket['cutover_candidate']}"
        )
    MD_FILE.parent.mkdir(parents=True, exist_ok=True)
    MD_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(CASE_FILE))
    parser.add_argument("--tier", choices=["smoke", "core", "extended"], default="")
    args = parser.parse_args()
    build_test_client()
    cases = [
        case
        for case in load_cases(Path(args.cases))
        if "plan" in (case.get("eval_modes") or [])
        and (not args.tier or case.get("tier") == args.tier)
    ]
    records = []
    for case in cases:
        try:
            record = run_case(case)
        except Exception as exc:  # noqa: BLE001
            record = {
                "schema_version": "agent_engine_v2_compare.v1",
                "request_id": f"eval-{case.get('id')}",
                "primary_skill": "unknown",
                "severity": "critical",
                "review_required": True,
                "summary": str(exc),
                "diffs": [{"surface": "eval", "severity": "critical", "error": str(exc)}],
            }
        records.append(record)
        print(f"{record.get('severity', 'none').upper()} {case.get('id')} {record.get('summary', '')}")
    summary = summarize(records)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(summary)
    print(json.dumps({key: summary[key] for key in ("total", "failed", "review_required")}, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
