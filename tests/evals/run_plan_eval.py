from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVAL_DIR = Path(__file__).resolve().parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from fault_diagnosis import config
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.api.chat import router as chat_router
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.diagnosis.artifact_store import clear_all_artifacts, save_thread_artifact
from fault_diagnosis.diagnosis.contracts import DiagnosisArtifactEnvelope
from fault_diagnosis.runtime.dev_mode import init_dev_state
from evaluators import evaluate_plan_case, summarize_results

CASE_FILE = ROOT / "tests" / "evals" / "agent_workflow_cases.yaml"
FIXTURE_DIR = ROOT / "tests" / "evals" / "fixtures"


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    return list(cases or [])


def build_test_client() -> TestClient:
    config.ENABLE_PLAN_ENDPOINT = True
    config.LOCAL_DEV_MODE = True
    config.DEV_AUTH_ENABLED = True
    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("agent-plan-eval-secret")
    init_dev_state(app)
    app.include_router(auth_router)
    app.include_router(chat_router)
    return TestClient(app)


def load_json_fixture(kind: str, name: str | None) -> Any:
    if not name:
        return None
    path = FIXTURE_DIR / kind / name
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def install_artifact_fixture(thread_id: str, fixture_name: str | None) -> None:
    payload = load_json_fixture("artifacts", fixture_name)
    if not payload:
        return
    items = payload if isinstance(payload, list) else [payload]
    for item in items:
        item = dict(item)
        item["thread_id"] = thread_id
        save_thread_artifact(DiagnosisArtifactEnvelope.model_validate(item))


def login_identity(client: TestClient, fixture_name: str | None, role: str | None) -> None:
    fixture = load_json_fixture("auth", fixture_name) or {}
    payload = {
        "role": fixture.get("role") or role or "guest",
        "user_id": fixture.get("user_id"),
        "asset_scope": fixture.get("asset_scope"),
        "allowed_tables": fixture.get("allowed_tables"),
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    response = client.post("/auth/dev-login", json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"dev login failed for {fixture_name or role}: {response.text}")


def last_turn(case: dict[str, Any]) -> str:
    turns = case.get("turns") or []
    if not turns:
        return ""
    last = turns[-1]
    if isinstance(last, dict):
        return str(last.get("message") or last.get("content") or "")
    return str(last)


def run_local_case(client: TestClient, case: dict[str, Any]) -> dict[str, Any]:
    clear_all_artifacts()
    thread_id = f"eval-plan-{case['id']}"
    install_artifact_fixture(thread_id, case.get("artifact_fixture"))
    login_identity(client, case.get("identity_fixture"), case.get("role"))
    response = client.get(
        "/chat/plan",
        params={
            "message": last_turn(case),
            "thread_id": thread_id,
            "user_identity": "管理员",
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"{case['id']} plan request failed: {response.status_code} {response.text}")
    return response.json()


def run_remote_case(base_url: str, case: dict[str, Any]) -> dict[str, Any]:
    response = httpx.get(
        f"{base_url.rstrip('/')}/chat/plan",
        params={"message": last_turn(case), "thread_id": f"eval-plan-{case['id']}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(CASE_FILE))
    parser.add_argument("--base-url", default=os.getenv("AGENT_EVAL_BASE_URL", ""))
    parser.add_argument("--tier", choices=["smoke", "core", "extended"], default="")
    args = parser.parse_args()

    cases = [
        case
        for case in load_cases(Path(args.cases))
        if "plan" in (case.get("eval_modes") or [])
        and (not args.tier or case.get("tier") == args.tier)
    ]
    client = None if args.base_url else build_test_client()
    results = []
    for case in cases:
        try:
            snapshot = run_remote_case(args.base_url, case) if args.base_url else run_local_case(client, case)  # type: ignore[arg-type]
            result = evaluate_plan_case(case, snapshot)
        except Exception as exc:  # noqa: BLE001
            result = evaluate_plan_case(case, {})
            result.passed = False
            result.failures.append(str(exc))
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.case_id} {case.get('name', '')}")
        for failure in result.failures:
            print(f"  - {failure}")

    summary = summarize_results(
        results,
        [
            "intent_accuracy",
            "context_binding_accuracy",
            "workflow_policy_accuracy",
            "tool_selection_precision",
            "evidence_gap_accuracy",
        ],
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
