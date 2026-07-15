from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fault_diagnosis.agent.output import GroundedAnswerResult
from fault_diagnosis.platform import settings
from fault_diagnosis.platform.observability import tracing
from fault_diagnosis.platform.persistence.diagnosis_artifacts.backends.file import FileArtifactStoreBackend
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import (
    configure_artifact_store_backend,
    list_thread_artifacts,
    reset_artifact_store_backend,
)
from fault_diagnosis.platform.persistence.repositories.conversation_store import SQLiteConversationRepository
from fault_diagnosis.platform.persistence.repositories.history_index import MemoryHistoryIndexRepository
from fault_diagnosis.server.agent_gateway import streaming
from fault_diagnosis.server.auth.session_scope import SessionScopeManager
from fault_diagnosis.server.http.routers.auth import router as auth_router
from fault_diagnosis.server.http.routers.chat import router as chat_router


CASE_FILE = Path(__file__).with_name("agent_v2_e2e_cases.yaml")
RESULT_FILE = Path(__file__).with_name("results") / "agent_v2_e2e_eval_summary.json"
TRACE_STAGE_ALIASES = {
    "canonical_parse": {"request.understand", "canonical.request"},
    "context_binding": {"context.resolve"},
    "authorization": {"goal.authorization", "capability.preflight"},
    "source_resolution": {"goal.source_resolution"},
    "readiness": {"goal.readiness"},
    "plan": {"goal.plan", "plan.compile", "plan.validate"},
    "answer_synthesis": {"answer_synthesis"},
}


class EvalToolRuntime:
    def __init__(self, *, data_profile: str = "realtime", failure: str = "") -> None:
        self.data_profile = data_profile
        self.failure = failure
        self.sql_calls: list[str] = []
        self.kb_calls: list[str] = []
        self.report_calls: list[dict[str, Any]] = []
        self._profile_query_count = 0

    def invoke_sql_tool(self, tool_name: str, payload: Any) -> Any:
        if tool_name == "sql_db_query_checker":
            return payload
        sql = str(payload)
        self.sql_calls.append(sql)
        self._profile_query_count += 1
        if self.failure == "sql_timeout":
            raise TimeoutError("fake sql timeout")
        if self.data_profile == "empty":
            return []
        if self.data_profile == "fallback":
            phase = self._profile_query_count % 3
            if phase == 1:
                return []
            if phase == 2:
                return [("2026-07-14 08:00:00",)]
        device = "G120电机2" if "real_data_02" in sql else "G120电机1"
        normal = self.data_profile == "normal"
        return [_runtime_row(device, normal=normal)]

    def query_knowledge_base(self, query: str) -> str:
        self.kb_calls.append(query)
        if self.failure == "rag_timeout":
            raise TimeoutError("fake rag timeout")
        return (
            "A07089 速度偏差超限\n原因：负载突变或速度反馈异常。\n"
            "处理：检查负载、编码器和速度环参数。\n来源文件：G120故障手册.pdf\n"
            "来源页码：42\n检索方式：故障码精确索引"
        )

    def save_report(self, **kwargs: Any) -> str:
        self.report_calls.append(dict(kwargs))
        if self.failure == "report_failure":
            raise RuntimeError("fake report generation failure")
        return "报告已保存至：/reports/agent-v2-e2e.html"


class SaveFailureStore(FileArtifactStoreBackend):
    def save_artifact(self, envelope):  # noqa: ANN001, ANN201
        raise OSError("fake artifact persistence failure")


class ReadbackFailureStore(FileArtifactStoreBackend):
    def get_artifact(self, thread_id: str, artifact_id: str):  # noqa: ANN201, ARG002
        return None


class WorkorderSaveFailureStore(FileArtifactStoreBackend):
    def save_artifact(self, envelope):  # noqa: ANN001, ANN201
        if envelope.artifact_type == "workorder_artifact":
            raise OSError("fake workorder draft persistence failure")
        return super().save_artifact(envelope)


class FakeAnswerSynthesizer:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    async def synthesize(self, **kwargs: Any) -> GroundedAnswerResult:
        self.calls += 1
        deterministic = str(kwargs.get("deterministic_answer") or "")
        if self.mode == "timeout":
            return GroundedAnswerResult(
                status="model_error",
                synthesis_status="model_timeout",
                final_answer_source="deterministic_fallback",
                fallback_used=True,
                answer=deterministic,
                enabled=True,
                attempted=True,
                fallback_reason="model_timeout",
            )
        return GroundedAnswerResult(
            status="generated",
            synthesis_status="generated",
            final_answer_source="grounded_model",
            fallback_used=False,
            answer=f"模型表达：{deterministic}",
            enabled=True,
            attempted=True,
        )


def _runtime_row(device: str, *, normal: bool) -> tuple[Any, ...]:
    values: list[Any] = [None] * 32
    values[0] = 1
    values[1] = "2026-07-15 08:30:00"
    values[2] = device
    values[3] = "INV-01" if device == "G120电机1" else "INV-02"
    values[4] = "2026-07-15"
    values[5] = "08:30:00"
    values[6] = "正常" if normal else "异常"
    values[7] = "" if normal else "A07089"
    values[11] = 500 if normal else 620
    values[12] = 1500
    values[13] = 1490 if normal else 1320
    values[18] = 50 if normal else 72
    values[19] = 52 if normal else 78
    values[25] = 60 if normal else 88
    values[26] = 58 if normal else 84
    values[31] = "2026-07-15 08:30:00"
    return tuple(values)


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(payload.get("cases") or [])


def parse_sse(response) -> list[dict[str, Any]]:  # noqa: ANN001
    events: list[dict[str, Any]] = []
    for block in response.text.split("\n\n"):
        data = [line.removeprefix("data:").strip() for line in block.splitlines() if line.startswith("data:")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


def login(client: TestClient, case: dict[str, Any], *, role: str | None = None) -> None:
    current_role = role or str(case.get("role") or "guest")
    payload: dict[str, Any] = {"role": current_role, "user_id": f"eval_{current_role}_{case['case_id']}"}
    if current_role == "engineer":
        payload["asset_scope"] = list(case.get("assigned_assets") or ["g120_motor_1"])
        payload["allowed_tables"] = list(case.get("allowed_tables") or ["real_data_01"])
    response = client.post("/auth/dev-login", json=payload)
    if response.status_code != 200:
        raise AssertionError(f"dev login failed: {response.status_code} {response.text}")


def build_client(case: dict[str, Any], root: Path) -> tuple[TestClient, EvalToolRuntime, FakeAnswerSynthesizer | None]:
    backend_kind = str(case.get("artifact_backend") or "")
    backend_class = {
        "save_failure": SaveFailureStore,
        "readback_failure": ReadbackFailureStore,
        "workorder_failure": WorkorderSaveFailureStore,
    }.get(backend_kind, FileArtifactStoreBackend)
    configure_artifact_store_backend(backend_class(root_dir=root / "artifacts"))

    flags = dict(case.get("flags") or {})
    settings.ENABLE_PLAN_ENDPOINT = True
    settings.LOCAL_DEV_MODE = False
    settings.DEV_AUTH_ENABLED = True
    settings.ENABLE_GROUNDED_ANSWER_SYNTHESIS = bool(flags.get("grounded_answer", False))
    settings.GROUNDED_ANSWER_ROLLOUT_PERCENT = 100 if flags.get("grounded_answer") else 0
    settings.LLM_SEMANTIC_MODE = "off"
    settings.ENABLE_LLM_CONTEXT_SEMANTICS = False
    settings.AGENT_TRACE_BACKEND = "none"
    settings.AGENT_TRACE_LOCAL_LOG = False
    settings.AGENT_TRACE_CONSOLE = False
    tracing.AGENT_TRACE_BACKEND = "none"
    tracing.AGENT_TRACE_LOCAL_LOG = False
    tracing.AGENT_TRACE_CONSOLE = False

    app = FastAPI()
    app.state.dev_mode = False
    app.state.session_scope_manager = SessionScopeManager(f"agent-v2-e2e-{case['case_id']}")
    app.state.conversation_repository = SQLiteConversationRepository(root / "conversation.sqlite3")
    app.state.history_index_repository = MemoryHistoryIndexRepository()
    tools = EvalToolRuntime(data_profile=str(case.get("data_profile") or "realtime"), failure=str(case.get("failure") or ""))
    app.state.agent_engine_v2_tool_runtime = tools

    answer_model = FakeAnswerSynthesizer(str(case.get("answer_model"))) if case.get("answer_model") else None
    if answer_model is not None:
        app.state.grounded_answer_synthesizer = answer_model
    app.include_router(auth_router)
    app.include_router(chat_router)
    return TestClient(app), tools, answer_model


def execute_stream(client: TestClient, message: str, thread_id: str | None) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    params = {"message": message}
    if thread_id:
        params["thread_id"] = thread_id
    response = client.get("/chat/stream", params=params)
    if response.status_code != 200:
        raise AssertionError(f"stream HTTP {response.status_code}: {response.text}")
    events = parse_sse(response)
    complete = next((item for item in events if item.get("type") == "chat_complete"), None)
    if complete is None:
        raise AssertionError(f"SSE has no terminal complete event: {[item.get('type') for item in events]}")
    return str(complete.get("thread_id") or thread_id or ""), events, complete


def run_case(case: dict[str, Any], root: Path) -> dict[str, Any]:
    client, tools, answer_model = build_client(case, root)
    thread_id: str | None = None
    turn_results: list[dict[str, Any]] = []
    original_export = streaming.export_trace_snapshot
    if case.get("failure") == "trace_export_failure":
        def fail_export(*args: Any, **kwargs: Any) -> None:  # noqa: ARG001
            raise RuntimeError("fake trace exporter failure")
        streaming.export_trace_snapshot = fail_export
    try:
        login(client, case)
        for pre_turn in case.get("pre_turns") or []:
            login(client, case, role=str(pre_turn.get("role") or case.get("role") or "guest"))
            thread_id, _, _ = execute_stream(client, str(pre_turn["user"]), thread_id)
        login(client, case)
        for index, turn in enumerate(case.get("turns") or []):
            if turn.get("data_profile"):
                tools.data_profile = str(turn["data_profile"])
                tools._profile_query_count = 0
            if "failure" in turn:
                tools.failure = str(turn.get("failure") or "")
            before_artifacts = len(list_thread_artifacts(thread_id, limit=100)) if thread_id else 0
            before_sql = len(tools.sql_calls)
            requested_thread = thread_id or f"e2e-{case['case_id']}"
            repository = client.app.state.conversation_repository
            before_messages = len(repository.list_messages(thread_id=requested_thread))
            plan_response = client.get("/chat/plan", params={"message": str(turn["user"]), "thread_id": requested_thread})
            if plan_response.status_code != 200:
                raise AssertionError(f"plan HTTP {plan_response.status_code}: {plan_response.text}")
            plan = plan_response.json()
            thread_id = str(plan["thread_id"])
            after_plan_artifacts = len(list_thread_artifacts(thread_id, limit=100))
            _expect(after_plan_artifacts == before_artifacts, "plan endpoint wrote an artifact")
            _expect(len(repository.list_messages(thread_id=thread_id)) == before_messages, "plan endpoint changed conversation or CaseState inputs")
            _expect(len(tools.sql_calls) == before_sql, "plan endpoint invoked SQL")
            before_answer = answer_model.calls if answer_model else 0
            before_stream_sql = len(tools.sql_calls)
            thread_id, events, complete = execute_stream(client, str(turn["user"]), thread_id)
            answer_calls = (answer_model.calls if answer_model else 0) - before_answer
            after_artifacts = len(list_thread_artifacts(thread_id, limit=100))
            assertions = evaluate_turn(
                case=case,
                turn=turn,
                plan=plan,
                events=events,
                complete=complete,
                artifact_delta=after_artifacts - before_artifacts,
                sql_delta=len(tools.sql_calls) - before_stream_sql,
                answer_calls=answer_calls,
            )
            turn_results.append({
                "turn": index + 1,
                "user": turn["user"],
                "status": complete.get("status"),
                "capabilities": [item.get("capability") for item in plan.get("goals", [])],
                "runtime_nodes": [item.get("node_type") for item in complete.get("node_results", [])],
                "artifact_delta": after_artifacts - before_artifacts,
                "assertions": assertions,
            })
    finally:
        streaming.export_trace_snapshot = original_export
        client.close()
        reset_artifact_store_backend()
    return {"case_id": case["case_id"], "category": case.get("category"), "passed": True, "turns": turn_results}


def evaluate_turn(
    *,
    case: dict[str, Any],
    turn: dict[str, Any],
    plan: dict[str, Any],
    events: list[dict[str, Any]],
    complete: dict[str, Any],
    artifact_delta: int,
    sql_delta: int,
    answer_calls: int,
) -> int:
    expect = dict(turn.get("expect") or {})
    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        _expect(condition, message)

    goals = list(plan.get("goals") or [])
    capabilities = [str(item.get("capability") or "") for item in goals]
    plan_nodes = [str(item.get("node_type") or "") for item in (plan.get("execution_plan") or {}).get("nodes", [])]
    node_results = list(complete.get("node_results") or [])
    runtime_nodes = [str(item.get("node_type") or "") for item in node_results]
    check(plan.get("canonical_request", {}).get("goals") == goals, "plan canonical goals diverged from top-level goals")
    if plan.get("status") == "blocked":
        check(not runtime_nodes, f"blocked plan executed runtime nodes: {runtime_nodes}")
    else:
        check(plan_nodes == runtime_nodes, f"plan/runtime node mismatch: {plan_nodes} != {runtime_nodes}")
    check(str(complete.get("status")) == str((complete.get("workflow_result") or {}).get("status") or complete.get("status")), "runtime/complete status mismatch")

    if "capabilities" in expect:
        check(capabilities == list(expect["capabilities"]), f"capabilities {capabilities} != {expect['capabilities']}")
    if "status" in expect:
        check(complete.get("status") == expect["status"], f"status {complete.get('status')} != {expect['status']}")
    if "runtime_nodes" in expect:
        check(runtime_nodes == list(expect["runtime_nodes"]), f"runtime nodes {runtime_nodes} != {expect['runtime_nodes']}")
    if expect.get("forbidden_nodes"):
        check(not set(expect["forbidden_nodes"]) & set(runtime_nodes), "forbidden runtime node present")
    if expect.get("forbidden_completed_nodes"):
        completed = {str(item.get("node_type")) for item in node_results if item.get("status") == "completed"}
        check(not completed & set(expect["forbidden_completed_nodes"]), f"forbidden completed nodes: {completed}")
    if "artifact_delta" in expect:
        check(artifact_delta == int(expect["artifact_delta"]), f"artifact delta {artifact_delta} != {expect['artifact_delta']}")
    if expect.get("no_sql"):
        check(sql_delta == 0, f"unexpected SQL calls: {sql_delta}")
    if expect.get("sql_required"):
        check(sql_delta > 0, "expected a SQL call")
    if "answer_model_calls" in expect:
        check(answer_calls == int(expect["answer_model_calls"]), f"answer model calls {answer_calls}")
    if expect.get("answer_not_contains"):
        answer = str(complete.get("final_content") or complete.get("content") or "")
        check(not any(value in answer for value in expect["answer_not_contains"]), "internal error text leaked to answer")
    if expect.get("answer_contains"):
        answer = str(complete.get("final_content") or complete.get("content") or "")
        check(all(value in answer for value in expect["answer_contains"]), f"answer missing degradation detail: {answer}")

    bindings = list(((plan.get("turn_result") or {}).get("bound_turn") or {}).get("goal_bindings") or [])
    binding_devices = list(dict.fromkeys(device for item in bindings for device in item.get("asset_refs", []) or []))
    binding_codes = list(dict.fromkeys(code for item in bindings for code in item.get("fault_codes", []) or []))
    if expect.get("binding_status"):
        check(any(item.get("binding_status") == expect["binding_status"] for item in bindings), "binding status mismatch")
    if expect.get("inherited_devices"):
        check(binding_devices == list(expect["inherited_devices"]), f"bound devices {binding_devices}")
    if expect.get("inherited_fault_codes"):
        check(binding_codes == list(expect["inherited_fault_codes"]), f"bound fault codes {binding_codes}")
    if expect.get("forbidden_devices"):
        check(not set(expect["forbidden_devices"]) & set(binding_devices), "old device survived explicit override")
    if expect.get("time_window_contains"):
        windows = [str(item.get("time_window") or "") for item in bindings]
        check(any(expect["time_window_contains"] in value for value in windows), f"time override missing: {windows}")
    if expect.get("unauthorized_inheritance") == 0:
        check(not any(item.get("source_artifact_refs") for item in bindings), "unauthorized artifact inherited")

    if expect.get("resolution_mode"):
        modes = _values_for_key(complete, "resolution_mode")
        check(expect["resolution_mode"] in modes, f"resolution mode {expect['resolution_mode']} not in {modes}")
    if expect.get("limitations_required"):
        limitations = [value for value in _values_for_key(complete, "limitations") if value]
        check(bool(limitations), "fallback limitations missing")
    if expect.get("condition_node"):
        condition = expect["condition_node"]
        result = next((
            item for item in node_results
            if item.get("node_type") == condition["node_type"] and item.get("status") == condition["status"]
        ), None)
        check(result is not None and result.get("status") == condition["status"], f"condition status mismatch: {result}")
        if condition.get("code"):
            check((result.get("error") or {}).get("code") == condition["code"], f"condition code mismatch: {result}")
    if expect.get("dispatch_runtime_nodes") == 0:
        check(not any(item.get("node_type") == "dispatch" for item in node_results), "dispatch runtime node present")
    if expect.get("answer_synthesis_status"):
        synthesis = complete.get("answer_synthesis") or {}
        check(synthesis.get("synthesis_status") == expect["answer_synthesis_status"], f"answer synthesis mismatch: {synthesis}")

    produced = [item for item in complete.get("produced_artifacts", []) if isinstance(item, dict)]
    if expect.get("require_lineage"):
        lineage_targets = [item for item in produced if item.get("artifact_type") in {"analysis_artifact", "comparison_artifact", "report_artifact", "workorder_artifact"}]
        check(bool(lineage_targets), "expected lineage-bearing artifact")
        check(all((item.get("manifest") or {}).get("lineage", {}).get("source_artifact_ids") for item in lineage_targets), "orphan artifact lineage")

    evidence = ((complete.get("evidence_bundle") or {}).get("evidence_items") or [])
    check(not any(str(item.get("source_type") or "").lower() in {"assistant", "chat", "message"} for item in evidence), "assistant text used as evidence")
    check(not any(item.get("status") == "completed" for item in node_results if item.get("status") in {"blocked", "skipped"}), "blocked/skipped node marked completed")
    _check_source_authority(plan, check)
    _check_trace(complete, runtime_nodes, check)
    return checks


def _check_source_authority(plan: dict[str, Any], check) -> None:  # noqa: ANN001
    bindings = list(((plan.get("turn_result") or {}).get("bound_turn") or {}).get("goal_bindings") or [])
    bound_refs = {str(ref) for item in bindings for ref in item.get("source_artifact_refs", []) or []}
    resolutions = list(plan.get("goal_source_resolution") or [])
    selected = {str(item.get("artifact_id")) for item in resolutions if item.get("artifact_id")}
    check(selected.issubset(bound_refs), f"SourceResolver selected refs outside Binder: {selected - bound_refs}")


def _check_trace(complete: dict[str, Any], runtime_nodes: list[str], check) -> None:  # noqa: ANN001
    trace = dict(complete.get("canonical_trace") or {})
    names = {str(item.get("name") or "") for item in trace.get("spans", [])}
    for stage, aliases in TRACE_STAGE_ALIASES.items():
        check(bool(names & aliases), f"trace missing {stage}")
    for node in runtime_nodes:
        check(f"node.{node}" in names, f"trace missing runtime node {node}")
    check(trace.get("status") == complete.get("status"), "trace terminal status mismatch")
    forbidden = {"api_key", "sql_password", "password", "prompt", "reasoning", "raw_response", "database_row"}
    check(not (forbidden & _all_keys(trace)), f"trace contains sensitive keys: {forbidden & _all_keys(trace)}")


def _values_for_key(value: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            if item_key == key:
                found.append(item_value)
            found.extend(_values_for_key(item_value, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_values_for_key(item, key))
    return found


def _all_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            result.add(str(key).lower())
            result.update(_all_keys(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_all_keys(item))
    return result


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(CASE_FILE))
    parser.add_argument("--category", default="")
    args = parser.parse_args()
    cases = load_cases(Path(args.cases))
    if args.category:
        cases = [case for case in cases if case.get("category") == args.category]
    original_settings = {
        name: getattr(settings, name)
        for name in (
            "ENABLE_PLAN_ENDPOINT", "LOCAL_DEV_MODE", "DEV_AUTH_ENABLED",
            "ENABLE_GROUNDED_ANSWER_SYNTHESIS", "GROUNDED_ANSWER_ROLLOUT_PERCENT",
            "LLM_SEMANTIC_MODE", "ENABLE_LLM_CONTEXT_SEMANTICS",
            "AGENT_TRACE_BACKEND", "AGENT_TRACE_LOCAL_LOG", "AGENT_TRACE_CONSOLE",
        )
    }
    original_trace_settings = {
        name: getattr(tracing, name)
        for name in ("AGENT_TRACE_BACKEND", "AGENT_TRACE_LOCAL_LOG", "AGENT_TRACE_CONSOLE")
    }
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agent-v2-e2e-") as temp:
        base = Path(temp)
        for case in cases:
            try:
                result = run_case(case, base / str(case["case_id"]))
                results.append(result)
                print(f"PASS {case['case_id']}")
            except Exception as exc:  # noqa: BLE001
                results.append({"case_id": case["case_id"], "category": case.get("category"), "passed": False, "error": str(exc)})
                print(f"FAIL {case['case_id']}: {exc}")
            finally:
                for name, value in original_settings.items():
                    setattr(settings, name, value)
                for name, value in original_trace_settings.items():
                    setattr(tracing, name, value)
                reset_artifact_store_backend()
    category_counts = Counter(str(item.get("category") or "unknown") for item in results)
    category_passed = Counter(str(item.get("category") or "unknown") for item in results if item.get("passed"))
    passed = sum(1 for item in results if item.get("passed"))
    summary = {
        "schema_version": "agent_v2_e2e_eval_result.v1",
        "dataset": str(Path(args.cases).resolve()),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "categories": {
            key: {"total": count, "passed": category_passed[key]}
            for key, count in sorted(category_counts.items())
        },
        "safety": {
            "unauthorized_inheritance": 0 if passed == len(results) else "see_failures",
            "unauthorized_sql_execution": 0 if passed == len(results) else "see_failures",
            "assistant_text_used_as_evidence": 0 if passed == len(results) else "see_failures",
            "failed_or_denied_artifact_reused": 0 if passed == len(results) else "see_failures",
            "ambiguous_device_auto_selection": 0 if passed == len(results) else "see_failures",
            "source_resolver_override": 0 if passed == len(results) else "see_failures",
            "orphan_report_or_workorder_goal": 0 if passed == len(results) else "see_failures",
            "condition_false_downstream_execution": 0 if passed == len(results) else "see_failures",
            "condition_unknown_downstream_execution": 0 if passed == len(results) else "see_failures",
            "dispatch_runtime_node_count": 0 if passed == len(results) else "see_failures",
            "llm_override_deterministic_entity_count": 0 if passed == len(results) else "see_failures",
            "llm_override_deterministic_high_risk_action_count": 0 if passed == len(results) else "see_failures",
            "runtime_complete_status_mismatch": 0 if passed == len(results) else "see_failures",
            "plan_runtime_node_mismatch": 0 if passed == len(results) else "see_failures",
            "artifact_case_state_invalid_update": 0 if passed == len(results) else "see_failures",
            "trace_missing_terminal_state": 0 if passed == len(results) else "see_failures",
            "feature_flag_double_model_invocation": 0 if passed == len(results) else "see_failures",
        },
        "results": results,
    }
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("total", "passed", "failed", "pass_rate", "categories", "safety")}, ensure_ascii=False, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
