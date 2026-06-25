from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import quantiles
from typing import Any


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)


def nested_get(payload: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def expect_equal(
    failures: list[str],
    payload: dict[str, Any],
    path: str,
    expected: Any,
) -> None:
    if expected is None:
        return
    actual = nested_get(payload, path)
    if actual != expected:
        failures.append(f"{path}: expected {expected!r}, got {actual!r}")


def expect_contains_all(
    failures: list[str],
    payload: dict[str, Any],
    path: str,
    expected: list[Any] | None,
) -> None:
    if not expected:
        return
    actual = nested_get(payload, path, [])
    if not isinstance(actual, list):
        failures.append(f"{path}: expected list containing {expected!r}, got {actual!r}")
        return
    missing = [item for item in expected if item not in actual]
    if missing:
        failures.append(f"{path}: missing {missing!r}, got {actual!r}")


def evaluate_plan_case(case: dict[str, Any], snapshot: dict[str, Any]) -> EvalResult:
    failures: list[str] = []
    expected = case.get("expected") or {}
    intent = expected.get("intent") or {}
    context = expected.get("context") or {}
    workflow = expected.get("workflow") or {}
    tools = expected.get("tools") or {}

    expect_equal(failures, snapshot, "intent_axes.domain_task", intent.get("domain_task"))
    expect_equal(failures, snapshot, "intent_axes.continuation_type", intent.get("continuation_type"))
    expect_contains_all(failures, snapshot, "intent_axes.intent_stack", intent.get("intent_stack_contains"))
    expect_contains_all(failures, snapshot, "intent_axes.object_binding.device_ids", intent.get("device_ids"))
    expect_contains_all(failures, snapshot, "intent_axes.object_binding.alarm_codes", intent.get("alarm_codes"))

    expect_equal(failures, snapshot, "resolved_context.source", context.get("source"))
    expect_equal(failures, snapshot, "resolved_context.used_active_asset", context.get("used_active_asset"))
    expect_equal(failures, snapshot, "resolved_context.used_active_fault_codes", context.get("used_active_fault_codes"))

    expect_equal(failures, snapshot, "workflow_policy.policy_id", workflow.get("policy_id"))
    expect_equal(failures, snapshot, "plan_mode", workflow.get("plan_mode"))
    expect_equal(failures, snapshot, "context_relation", workflow.get("context_relation"))
    for node in workflow.get("enabled_nodes", []) or []:
        if not nested_get(snapshot, f"enabled_nodes.{node}", False):
            failures.append(f"enabled_nodes.{node}: expected enabled")
    for node in workflow.get("skipped_nodes", []) or []:
        if node not in (snapshot.get("skipped_nodes") or {}):
            failures.append(f"skipped_nodes.{node}: expected skipped")
    expect_contains_all(failures, snapshot, "planned_tools", tools.get("planned"))
    expect_contains_all(failures, snapshot, "forbidden_tools", tools.get("forbidden"))
    expect_contains_all(failures, snapshot, "missing_slots", workflow.get("missing_slots"))
    expect_contains_all(
        failures,
        snapshot,
        "evidence_gaps.missing_or_stale_evidence",
        workflow.get("evidence_gaps"),
    )

    return EvalResult(
        case_id=str(case.get("id") or ""),
        passed=not failures,
        failures=failures,
        metrics={
            "intent_accuracy": 0.0 if any(item.startswith("intent_axes.") for item in failures) else 1.0,
            "context_binding_accuracy": 0.0 if any(item.startswith("resolved_context.") for item in failures) else 1.0,
            "workflow_policy_accuracy": 0.0 if any("workflow_policy" in item or "enabled_nodes" in item for item in failures) else 1.0,
            "tool_selection_precision": 0.0 if any("planned_tools" in item or "forbidden_tools" in item for item in failures) else 1.0,
            "evidence_gap_accuracy": 0.0 if any("evidence_gaps" in item for item in failures) else 1.0,
        },
    )


def evaluate_trace_case(case: dict[str, Any], events: list[dict[str, Any]], complete: dict[str, Any]) -> EvalResult:
    failures: list[str] = []
    expected = case.get("expected") or {}
    tools = expected.get("tools") or {}
    actual_tools = [event.get("tool") for event in events if event.get("type") == "tool_start"]
    planned = tools.get("planned") or []
    forbidden = tools.get("forbidden") or []
    missing_tools = [tool for tool in planned if tool not in actual_tools]
    forbidden_seen = [tool for tool in forbidden if tool in actual_tools]
    if missing_tools:
        failures.append(f"tools: missing calls {missing_tools!r}, got {actual_tools!r}")
    if forbidden_seen:
        failures.append(f"tools: forbidden calls seen {forbidden_seen!r}")

    artifact_failures = validate_trace_artifacts(complete)
    invariant_failures = validate_invariants(case, complete, actual_tools)
    failures.extend(artifact_failures)
    failures.extend(invariant_failures)

    latency_ms = _trace_latency_ms(complete)
    return EvalResult(
        case_id=str(case.get("id") or ""),
        passed=not failures,
        failures=failures,
        metrics={
            "trajectory_pass_rate": 0.0 if missing_tools or forbidden_seen else 1.0,
            "answer_contract_pass_rate": 0.0 if artifact_failures else 1.0,
            "contradiction_rate": 1.0 if invariant_failures else 0.0,
            "unnecessary_tool_call_rate": _unnecessary_tool_call_rate(actual_tools, planned),
            "artifact_reuse_rate": 1.0 if nested_get(complete, "decision.evidence_mode") == "reuse_previous_artifact" else 0.0,
            "latency_ms": latency_ms or 0.0,
        },
    )


def validate_trace_artifacts(complete: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    has_runtime_artifacts = any(
        key in complete
        for key in (
            "sql_artifact",
            "knowledge_artifact",
            "analysis_artifact",
            "workorder_decision",
            "evidence_bundle",
            "trace",
        )
    )
    if complete.get("runtime") != "restricted_single_agent" and not has_runtime_artifacts:
        return failures
    decision = complete.get("decision") or {}
    enabled = decision.get("enabled_nodes") or {}
    if enabled.get("sql") and not complete.get("sql_artifact"):
        failures.append("sql_artifact: missing")
    if enabled.get("knowledge") and not complete.get("knowledge_artifact"):
        failures.append("knowledge_artifact: missing")
    if enabled.get("workorder_decision") and not complete.get("workorder_decision"):
        failures.append("workorder_decision: missing")
    if "output_guardrail" in complete and not isinstance(complete.get("output_guardrail"), dict):
        failures.append("output_guardrail: invalid")
    return failures


def validate_invariants(case: dict[str, Any], complete: dict[str, Any], actual_tools: list[str]) -> list[str]:
    failures: list[str] = []
    answer = str(complete.get("final_content") or "")
    sql = complete.get("sql_artifact") or {}
    workorder = complete.get("workorder_decision") or {}
    decision = complete.get("decision") or {}

    if int(sql.get("row_count") or 0) > 0 and _claims_sql_no_data(str(workorder.get("reason") or "")):
        failures.append("invariant: sql row_count > 0 contradicts workorder no-data reason")

    asks_current = any(keyword in "".join(case.get("turns") or []) for keyword in ("当前", "现在", "还在", "最新状态"))
    if asks_current and "当前状态" in answer:
        freshness = _sql_freshness(complete)
        if freshness not in {"current", "recent"} and not _discloses_uncertainty(answer):
            failures.append("invariant: current-status answer lacks current/recent SQL evidence or disclosure")

    stale = _has_stale_evidence(complete)
    action_or_current = any(keyword in answer for keyword in ("派单", "工单", "当前状态", "现在"))
    if stale and action_or_current and not _discloses_stale(answer):
        failures.append("invariant: stale evidence not disclosed")

    if decision.get("primary_task_type") == "action_request" and re.search(r"已(?:重启|停机|关闭告警|屏蔽告警|修改|改成|执行)", answer):
        failures.append("invariant: dangerous action completion claim")

    auth = complete.get("authorization") or {}
    if auth.get("mode") in {"degrade", "deny"} and (
        complete.get("report_url") or re.search(r"已(?:创建|生成|派发|下发)(?:维修)?工单", answer)
    ):
        failures.append("invariant: guest/restricted identity accessed report or workorder")

    if decision.get("plan_mode") == "workorder_decision_from_artifact":
        forbidden_reuse_tools = {"sql_db_query", "sql_db_query_checker", "query_knowledge_base"}
        if forbidden_reuse_tools.intersection(actual_tools):
            failures.append("invariant: artifact-sufficient follow-up called SQL/KB")
    return failures


def summarize_results(results: list[EvalResult], metric_names: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
    }
    for metric in metric_names:
        values = [result.metrics.get(metric) for result in results if metric in result.metrics]
        values = [float(value) for value in values if value is not None]
        if values:
            summary[metric] = round(sum(values) / len(values), 4)
    latencies = [result.metrics.get("latency_ms") for result in results if result.metrics.get("latency_ms")]
    if latencies:
        summary["p95_latency_by_intent"] = {"all": round(_p95([float(item) for item in latencies]), 2)}
    return summary


def _claims_sql_no_data(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return any(item in compact for item in ("SQL未返回数据", "SQL未返回可解析运行数据", "SQL没有返回数据", "SQL查询无数据"))


def _sql_freshness(complete: dict[str, Any]) -> str:
    for item in nested_get(complete, "evidence_bundle.evidence_items", []) or []:
        if item.get("source_type") == "sql":
            quality = item.get("quality") or {}
            if quality.get("freshness"):
                return str(quality["freshness"])
    return "unknown"


def _has_stale_evidence(complete: dict[str, Any]) -> bool:
    text = str(complete)
    return any(keyword in text for keyword in ("stale", "已滞后", "不代表实时状态", "非实时"))


def _discloses_uncertainty(text: str) -> bool:
    return any(keyword in text for keyword in ("不能确认", "无法确认", "暂不能", "证据不足", "不代表当前"))


def _discloses_stale(text: str) -> bool:
    return any(keyword in text for keyword in ("滞后", "采样窗口", "非实时", "不代表实时", "不代表当前"))


def _unnecessary_tool_call_rate(actual_tools: list[str], expected_tools: list[str]) -> float:
    if not actual_tools:
        return 0.0
    unnecessary = [tool for tool in actual_tools if tool not in expected_tools]
    return len(unnecessary) / len(actual_tools)


def _trace_latency_ms(complete: dict[str, Any]) -> float | None:
    trace = complete.get("trace") or {}
    durations = [
        event.get("duration_ms")
        for event in trace.get("events", [])
        if isinstance(event, dict) and event.get("duration_ms") is not None
    ]
    if durations:
        return float(sum(float(item) for item in durations))
    return None


def _p95(values: list[float]) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return quantiles(values, n=20)[-1]
