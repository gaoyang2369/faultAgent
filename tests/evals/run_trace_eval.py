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
from fault_diagnosis.agent_runtime.sse_adapter import encode_sse_event
from fault_diagnosis.api.auth import router as auth_router
from fault_diagnosis.api.chat import router as chat_router
from fault_diagnosis.auth.session_scope import SessionScopeManager
from fault_diagnosis.diagnosis.artifact_store import clear_all_artifacts, save_thread_artifact
from fault_diagnosis.diagnosis.contracts import DiagnosisArtifactEnvelope
from fault_diagnosis.single_agent import runner as runner_module
from evaluators import (
    evaluate_plan_stream_consistency,
    evaluate_trace_case,
    hard_gate_failures,
    summarize_results,
)

CASE_FILE = ROOT / "tests" / "evals" / "agent_workflow_cases.yaml"
FIXTURE_DIR = ROOT / "tests" / "evals" / "fixtures"
RESULTS_DIR = ROOT / "tests" / "evals" / "results"
TRACE_SUMMARY_FILE = RESULTS_DIR / "trace_eval_summary.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    return list(cases or [])


def build_test_client() -> TestClient:
    config.LOCAL_DEV_MODE = False
    config.ENABLE_PLAN_ENDPOINT = True
    config.DEV_AUTH_ENABLED = True
    _install_mock_runtime_hooks()
    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("agent-trace-eval-secret")
    app.state.dev_mode = False
    app.state.chat_model = None
    app.include_router(auth_router)
    app.include_router(chat_router)
    return TestClient(app)


def _install_mock_runtime_hooks() -> None:
    if getattr(runner_module.RestrictedSingleAgentRunner, "_eval_mock_runtime_installed", False):
        return

    async def fake_invoke_json_model(self, prompt: str) -> dict[str, Any]:  # noqa: ARG001
        return {
            "conclusion": "基于 mock trace fixture 形成的结构化结论。",
            "basis": ["mock SQL/KB evidence"],
            "probable_causes": ["待现场确认"],
            "verification_items": ["核对当前状态"],
            "recommendations": ["按规程复核后再执行"],
            "missing_information": [],
            "confidence": "medium",
        }

    async def fake_invoke_restricted_tool(self, *, tool_name: str, tool: Any, tool_input: Any, stage: str):  # noqa: ANN001, ARG001
        run_id, started_at, start_payload = self._start_tool_call(
            tool_name=tool_name,
            tool_input=tool_input,
            stage=stage,
        )
        yield encode_sse_event("tool_start", start_payload, trace_id=self.trace_id)
        result = _mock_tool_result(tool_name, tool_input)
        self._last_step_result = result
        end_payload = self._finish_tool_call(
            tool_name=tool_name,
            run_id=run_id,
            started_at=started_at,
            stage=stage,
            output=result,
        )
        yield encode_sse_event("tool_end", end_payload, trace_id=self.trace_id)

    runner_module.RestrictedSingleAgentRunner._invoke_json_model = fake_invoke_json_model
    runner_module.RestrictedSingleAgentRunner._invoke_restricted_tool = fake_invoke_restricted_tool
    runner_module.RestrictedSingleAgentRunner._eval_mock_runtime_installed = True


def _mock_tool_result(tool_name: str, tool_input: Any) -> Any:
    if tool_name == "sql_db_query_checker":
        return (tool_input or {}).get("query", "")
    if tool_name == "sql_db_query":
        query = str(tool_input.get("query") if isinstance(tool_input, dict) else "")
        device_name = "G120电机2" if "real_data_02" in query or "G120电机2" in query else "G120电机1"
        fault_code = "" if device_name == "G120电机2" else "A07089"
        return [
            (
                1001,
                "2026-06-25 13:58:00",
                device_name,
                device_name,
                "2026-06-25",
                "13:58:00",
                "warning" if fault_code else "running",
                fault_code,
                "0",
                0,
                0,
                620,
                1000,
                720,
                12.3,
                0,
                0,
                28,
                56,
                42,
                31.5,
                0,
                0,
                3600,
                45,
                78.47,
                72.1,
                4,
                55,
                0,
                "2026-06-25 13:58:00",
            )
        ]
    if tool_name == "query_knowledge_base":
        return "故障码：A07089\n原因：速度偏差或负载异常。\n处理：检查负载、速度闭环和参数配置。"
    if tool_name == "save_report":
        return "报告已保存至：/reports/mock_eval_report.html"
    return {"mocked": True}


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


def parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        data_lines = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def run_local_case(client: TestClient, case: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    clear_all_artifacts()
    thread_id = f"eval-trace-{case['id']}"
    stream_id = f"stream-eval-{case['id']}"
    install_artifact_fixture(thread_id, case.get("artifact_fixture"))
    login_identity(client, case.get("identity_fixture"), case.get("role"))
    plan_response = client.get(
        "/chat/plan",
        params={
            "message": last_turn(case),
            "thread_id": thread_id,
            "user_identity": "管理员",
        },
    )
    if plan_response.status_code != 200:
        raise RuntimeError(f"{case['id']} plan request failed: {plan_response.status_code} {plan_response.text}")
    response = client.get(
        "/chat/stream",
        params={
            "message": last_turn(case),
            "thread_id": thread_id,
            "stream_id": stream_id,
            "user_identity": "管理员",
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"{case['id']} trace request failed: {response.status_code} {response.text}")
    events = parse_sse(response.text)
    complete = next((event for event in events if event.get("type") == "chat_complete"), {})
    return plan_response.json(), events, complete


def run_remote_case(base_url: str, case: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    plan_response = httpx.get(
        f"{base_url.rstrip('/')}/chat/plan",
        params={
            "message": last_turn(case),
            "thread_id": f"eval-trace-{case['id']}",
        },
        timeout=30,
    )
    plan_response.raise_for_status()
    response = httpx.get(
        f"{base_url.rstrip('/')}/chat/stream",
        params={
            "message": last_turn(case),
            "thread_id": f"eval-trace-{case['id']}",
            "stream_id": f"stream-eval-{case['id']}",
        },
        timeout=120,
    )
    response.raise_for_status()
    events = parse_sse(response.text)
    complete = next((event for event in events if event.get("type") == "chat_complete"), {})
    return plan_response.json(), events, complete


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(CASE_FILE))
    parser.add_argument("--base-url", default=os.getenv("AGENT_EVAL_BASE_URL", ""))
    parser.add_argument("--subset", choices=["smoke", "core", "extended"], default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--with-llm-judge", action="store_true")
    args = parser.parse_args()
    if args.with_llm_judge:
        print("LLM judge hook is reserved for release evals; deterministic evaluators still run.")

    allowed_tiers = {"smoke", "core", "extended"} if args.all else {args.subset or "core"}
    cases = [
        case
        for case in load_cases(Path(args.cases))
        if case.get("tier") in allowed_tiers
        and ({"trace_mock", "trace_real"} & set(case.get("eval_modes") or []))
    ]
    client = None if args.base_url else build_test_client()
    results = []
    consistency_results = []
    for case in cases:
        try:
            plan, events, complete = run_remote_case(args.base_url, case) if args.base_url else run_local_case(client, case)  # type: ignore[arg-type]
            result = evaluate_trace_case(case, events, complete)
            consistency_result = evaluate_plan_stream_consistency(case, plan, events, complete)
        except Exception as exc:  # noqa: BLE001
            result = evaluate_trace_case(case, [], {})
            result.passed = False
            result.failures.append(str(exc))
            consistency_result = evaluate_plan_stream_consistency(case, {}, [], {})
            consistency_result.passed = False
            consistency_result.failures.append(str(exc))
        results.append(result)
        consistency_results.append(consistency_result)
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.case_id} {case.get('name', '')}")
        for failure in result.failures:
            print(f"  - {failure}")
        consistency_status = "PASS" if consistency_result.passed else "FAIL"
        print(f"{consistency_status} {consistency_result.case_id} plan-stream consistency")
        for failure in consistency_result.failures:
            print(f"  - {failure}")

    trace_summary = summarize_results(
        results,
        [
            "unnecessary_tool_call_rate",
            "artifact_reuse_rate",
            "trajectory_pass_rate",
            "answer_contract_pass_rate",
            "contradiction_rate",
            "dangerous_action_forbidden_phrase_count",
        ],
    )
    consistency_summary = summarize_results(
        consistency_results,
        ["plan_vs_stream_mismatch_count", "contradiction_rate", "dangerous_action_forbidden_phrase_count"],
    )
    trace_summary.setdefault("p95_latency_by_intent", {})
    consistency_summary.setdefault("p95_latency_by_intent", {})
    summary = {
        **trace_summary,
        "plan_stream_consistency": consistency_summary,
        "plan_vs_stream_mismatch_count": consistency_summary.get("plan_vs_stream_mismatch_count", 0.0),
    }
    gate_failures = [
        *hard_gate_failures(trace_summary, mode="trace"),
        *hard_gate_failures(consistency_summary, mode="consistency"),
    ]
    if gate_failures:
        summary["hard_gate_failures"] = gate_failures
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] and not consistency_summary["failed"] and not gate_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
