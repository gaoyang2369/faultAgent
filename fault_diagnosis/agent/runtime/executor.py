"""Plan-only fake Workflow Runtime for Agent Engine V2."""

from __future__ import annotations

import time
from typing import Any, Protocol

from ..contracts import ExecutionPlan, NodeStatus
from fault_diagnosis.domain.security.contracts import AuthContext
from fault_diagnosis.domain.security.permissions import build_auth_context
from .graph import RuntimeGraph, RuntimeGraphError
from .state import (
    CancelToken,
    RuntimeResult,
    RuntimeState,
    build_complete_payload,
    node_result,
)
from ..output.answer import build_output_frame
from ..evidence import project_ledger_to_evidence_bundle
from ..artifacts import allocate_node_artifact_id, build_node_artifact_envelope
from ..observability.cutover_observation import summarize_runtime_artifacts


class NodeExecutionOutput:
    """Internal output returned by typed nodes before executor commits evidence."""

    def __init__(
        self,
        *,
        status: NodeStatus = "completed",
        output: dict[str, Any] | None = None,
        tool_call_refs: list[str] | None = None,
        proposed_evidence: list[dict[str, Any]] | None = None,
        proposed_claims: list[dict[str, Any]] | None = None,
        artifacts: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.output = output or {}
        self.tool_call_refs = list(tool_call_refs or [])
        self.proposed_evidence = list(proposed_evidence or [])
        self.proposed_claims = list(proposed_claims or [])
        self.artifacts = dict(artifacts or {})
        self.error = error


class TypedNode(Protocol):
    """Typed node protocol used by fake runtime and future real tools."""

    node_type: str

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        ...


class FakeTypedNode:
    """Default fake node that returns deterministic summaries."""

    def __init__(self, node_type: str, *, emits_evidence: bool = False) -> None:
        self.node_type = node_type
        self.emits_evidence = emits_evidence

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        node_id = str(node.get("node_id") or "")
        output = {
            "status": "fake_completed",
            "node_id": node_id,
            "node_type": self.node_type,
            "summary": f"Fake {self.node_type} node completed.",
        }
        evidence = []
        if self.emits_evidence:
            evidence = [
                {
                    "evidence_id": f"fake_ev_{node_id}",
                    "source_type": "fake_runtime",
                    "evidence_type": self.node_type,
                    "summary": output["summary"],
                    "authorized": True,
                }
            ]
        return NodeExecutionOutput(
            output=output,
            tool_call_refs=[f"fake_tool_call:{node_id}"],
            proposed_evidence=evidence,
        )


class WorkflowRuntimeExecutor:
    """Execute validated V2 plans with fake typed nodes."""

    def __init__(
        self,
        node_registry: dict[str, TypedNode] | None = None,
        *,
        real_tools: bool = False,
        tool_runtime: Any | None = None,
    ) -> None:
        if real_tools:
            from .nodes import build_real_node_registry
            from .tool_runtime import ToolRuntime

            registry = build_real_node_registry(tool_runtime=tool_runtime or ToolRuntime())
        else:
            registry: dict[str, TypedNode] = {
                "sql": FakeTypedNode("sql", emits_evidence=True),
                "rag": FakeTypedNode("rag", emits_evidence=True),
                "kg": FakeTypedNode("kg", emits_evidence=True),
                "analysis": FakeTypedNode("analysis", emits_evidence=False),
                "report": FakeTypedNode("report", emits_evidence=False),
                "workorder": FakeTypedNode("workorder", emits_evidence=False),
                "approval": FakeTypedNode("approval", emits_evidence=False),
                "clarification": FakeTypedNode("clarification", emits_evidence=False),
                "comparison": FakeTypedNode("comparison", emits_evidence=False),
            }
        registry.update(node_registry or {})
        self.node_registry = registry

    def execute(
        self,
        plan: ExecutionPlan,
        *,
        trace_id: str = "",
        thread_id: str = "",
        request_id: str = "",
        cancel_token: CancelToken | None = None,
        auth_context: AuthContext | None = None,
    ) -> RuntimeResult:
        state = RuntimeState(
            plan=plan.model_copy(deep=True),
            trace_id=trace_id,
            thread_id=thread_id,
            request_id=request_id,
            auth_context=auth_context or build_auth_context(role="guest"),
            cancel_token=cancel_token or CancelToken(),
        )
        state.initialize_ledger()
        state.add_trace(
            "runtime_gate",
            status="checking",
            metadata={"validated_plan_required": True, "plan_version": plan.plan_version, "plan_id": plan.plan_id},
        )
        if not _is_executable_validated_plan(plan):
            return self._finish_blocked(
                state,
                code="validated_plan_required",
                message="V2 runtime only executes validated plans.",
            )
        try:
            graph = RuntimeGraph(plan)
        except RuntimeGraphError as exc:
            return self._finish_blocked(state, code="invalid_graph", message=str(exc))

        node_status: dict[str, str] = {}
        for node in graph.ordered_nodes():
            allocate_node_artifact_id(node)
            node_id = str(node.get("node_id") or "")
            input_summary = _summarize_input(node)
            state.add_trace("node_status", node_id=node_id, node_type=_node_type(node), status="pending", input_summary=input_summary)

            if state.cancel_token.cancelled:
                self._cancel_node(state, node, input_summary=input_summary)
                node_status[node_id] = "cancelled"
                self._cancel_remaining(state, graph, after_node_id=node_id, node_status=node_status)
                return self._finish_cancelled(state)

            failed_deps = [
                dep
                for dep in graph.dependencies(node_id)
                if node_status.get(dep) != "completed" and not graph.dependency_is_optional(dep, node_id)
            ]
            if failed_deps:
                result = node_result(
                    node=node,
                    status="skipped",
                    input_summary=input_summary,
                    output={"skipped_reason": "dependency_not_completed", "dependencies": failed_deps},
                )
                state.append_node_result(result)
                node_status[node_id] = "skipped"
                state.add_trace(
                    "node_status",
                    node_id=node_id,
                    node_type=result.node_type,
                    status="skipped",
                    input_summary=input_summary,
                    output_summary=_summarize_output(result.output),
                    retry_count=result.retry_count,
                )
                continue

            typed_node = self.node_registry.get(_node_type(node))
            if typed_node is None:
                result = node_result(
                    node=node,
                    status="skipped",
                    input_summary=input_summary,
                    output={"skipped_reason": "fake_handler_not_configured"},
                )
                state.append_node_result(result)
                node_status[node_id] = "skipped"
                state.add_trace(
                    "node_status",
                    node_id=node_id,
                    node_type=result.node_type,
                    status="skipped",
                    input_summary=input_summary,
                    output_summary=_summarize_output(result.output),
                )
                continue

            result = self._run_node_with_retry(state, node, typed_node, input_summary=input_summary)
            state.append_node_result(result)
            node_status[node_id] = result.status
            if result.status == "cancelled":
                self._cancel_remaining(state, graph, after_node_id=node_id, node_status=node_status)
                return self._finish_cancelled(state)
            failure_policy = str(node.get("failure_policy") or "block_all")
            if result.status == "blocked":
                if failure_policy == "block_all":
                    return self._finish_blocked(
                        state,
                        code=(result.error or {}).get("code", "node_blocked"),
                        message=(result.error or {}).get("message", "Node blocked execution."),
                    )
                continue
            if result.status == "failed":
                if failure_policy == "block_all":
                    return self._finish_failed(state, error=result.error or {})
                continue

        return self._finish_completed(state)

    def _run_node_with_retry(
        self,
        state: RuntimeState,
        node: dict[str, Any],
        typed_node: TypedNode,
        *,
        input_summary: str,
    ):
        node_id = str(node.get("node_id") or "")
        retry_limit = _retry_limit(node)
        attempts = 0
        last_error: dict[str, Any] | None = None
        started = time.monotonic()
        input_artifact_summaries = summarize_runtime_artifacts(state.artifacts, state.artifact_envelopes)
        while attempts <= retry_limit:
            state.add_trace(
                "node_status",
                node_id=node_id,
                node_type=_node_type(node),
                status="running",
                input_summary=input_summary,
                retry_count=attempts,
            )
            try:
                output = typed_node.run(node=node, state=state)
                if state.cancel_token.cancelled:
                    duration_ms = round((time.monotonic() - started) * 1000, 1)
                    result = node_result(
                        node=node,
                        status="cancelled",
                        input_summary=input_summary,
                        output={"cancel_reason": state.cancel_token.reason},
                        retry_count=attempts,
                        duration_ms=duration_ms,
                    )
                    state.add_trace(
                        "node_status",
                        node_id=node_id,
                        node_type=result.node_type,
                        status="cancelled",
                        input_summary=input_summary,
                        output_summary=_summarize_output(result.output),
                        duration_ms=duration_ms,
                        retry_count=attempts,
                    )
                    return result

                if output.artifacts:
                    state.artifacts.update(output.artifacts)
                evidence_refs: list[str] = []
                if output.status == "completed":
                    evidence_refs = state.commit_evidence(node, output.proposed_evidence)
                    state.commit_claims(output.proposed_claims)
                envelope = build_node_artifact_envelope(
                    node=node,
                    state=state,
                    node_status=output.status,
                    evidence_refs=evidence_refs,
                )
                if envelope is not None:
                    state.artifact_envelopes[envelope.artifact_id] = envelope
                    state.artifacts.setdefault("artifact_envelopes", {})[envelope.artifact_id] = envelope
                    output.output["artifact_id"] = envelope.artifact_id
                duration_ms = round((time.monotonic() - started) * 1000, 1)
                result = node_result(
                    node=node,
                    status=output.status,
                    input_summary=input_summary,
                    output=output.output,
                    evidence_refs=evidence_refs,
                    tool_call_refs=output.tool_call_refs,
                    error=output.error,
                    retry_count=attempts,
                    duration_ms=duration_ms,
                    artifact_id=envelope.artifact_id if envelope is not None else "",
                )
                if output.status in {"failed", "blocked"} and result.error:
                    state.errors.append(result.error)
                if output.status == "blocked":
                    state.interrupts.extend(
                        result.output.get("interrupts", []) if isinstance(result.output, dict) else []
                    )
                state.add_trace(
                    "node_status",
                    node_id=node_id,
                    node_type=result.node_type,
                    status=result.status,
                    input_summary=input_summary,
                    output_summary=_summarize_output(result.output),
                    duration_ms=duration_ms,
                    retry_count=attempts,
                    error=result.error,
                    metadata={
                        "goal_ids": list(node.get("goal_ids") or []),
                        "query_spec_id": str(node.get("query_spec_id") or ""),
                        "target_scope_id": str(node.get("target_scope_id") or ""),
                        "failure_policy": str(node.get("failure_policy") or "block_all"),
                        "input_artifacts": input_artifact_summaries,
                        "output_artifacts": summarize_runtime_artifacts(state.artifacts, state.artifact_envelopes),
                    },
                )
                return result
            except Exception as exc:  # noqa: BLE001 - fake nodes intentionally simulate arbitrary failures.
                last_error = {"code": type(exc).__name__, "message": str(exc)}
                state.add_trace(
                    "node_retry",
                    node_id=node_id,
                    node_type=_node_type(node),
                    status="failed_attempt",
                    input_summary=input_summary,
                    retry_count=attempts,
                    error=last_error,
                )
                if attempts >= retry_limit:
                    break
                attempts += 1

        duration_ms = round((time.monotonic() - started) * 1000, 1)
        result = node_result(
            node=node,
            status="failed",
            input_summary=input_summary,
            output={},
            error=last_error or {"code": "node_failed", "message": "Node failed."},
            retry_count=attempts,
            duration_ms=duration_ms,
        )
        state.errors.append(result.error or {})
        state.add_trace(
            "node_status",
            node_id=node_id,
            node_type=result.node_type,
            status="failed",
            input_summary=input_summary,
            duration_ms=duration_ms,
            retry_count=attempts,
            error=result.error,
            metadata={
                "goal_ids": list(node.get("goal_ids") or []),
                "query_spec_id": str(node.get("query_spec_id") or ""),
                "target_scope_id": str(node.get("target_scope_id") or ""),
                "failure_policy": str(node.get("failure_policy") or "block_all"),
            },
        )
        return result

    def _cancel_node(self, state: RuntimeState, node: dict[str, Any], *, input_summary: str) -> None:
        result = node_result(
            node=node,
            status="cancelled",
            input_summary=input_summary,
            output={"cancel_reason": state.cancel_token.reason},
        )
        state.append_node_result(result)
        state.add_trace(
            "node_status",
            node_id=result.node_id,
            node_type=result.node_type,
            status="cancelled",
            input_summary=input_summary,
            output_summary=_summarize_output(result.output),
        )

    def _cancel_remaining(
        self,
        state: RuntimeState,
        graph: RuntimeGraph,
        *,
        after_node_id: str,
        node_status: dict[str, str],
    ) -> None:
        remaining = False
        for node in graph.ordered_nodes():
            node_id = str(node.get("node_id") or "")
            if node_id == after_node_id:
                remaining = True
                continue
            if not remaining or node_id in node_status:
                continue
            self._cancel_node(state, node, input_summary=_summarize_input(node))
            node_status[node_id] = "cancelled"

    def _finish_completed(self, state: RuntimeState) -> RuntimeResult:
        state.status = "completed"
        return _runtime_result(state, status="completed")

    def _finish_blocked(self, state: RuntimeState, *, code: str, message: str) -> RuntimeResult:
        state.status = "blocked"
        error = {"code": code, "message": message}
        state.errors.append(error)
        state.add_trace("runtime_status", status="blocked", error=error)
        return _runtime_result(state, status="blocked")

    def _finish_failed(self, state: RuntimeState, *, error: dict[str, Any]) -> RuntimeResult:
        state.status = "failed"
        state.add_trace("runtime_status", status="failed", error=error)
        return _runtime_result(state, status="failed")

    def _finish_cancelled(self, state: RuntimeState) -> RuntimeResult:
        state.status = "cancelled"
        state.finalize_ledger()
        state.add_trace("runtime_status", status="cancelled", metadata={"cancel_reason": state.cancel_token.reason})
        output_frame = _runtime_output_frame(state, status="cancelled", cancelled=True)
        state.deliverable_statuses = [
            {"goal_id": item.goal_id, "deliverable_type": item.deliverable_type, "status": item.status}
            for item in output_frame.composite_output.deliverables
        ]
        complete = build_complete_payload(
            state=state,
            status="cancelled",
            output_frame=output_frame,
            cancelled=True,
            cancel_reason=state.cancel_token.reason,
        )
        return RuntimeResult(
            status="cancelled",
            node_results=list(state.node_results),
            evidence_ledger=state.evidence_ledger,
            output_frame=output_frame,
            trace=state.trace_payload(),
            complete_payload=complete,
            cancel_payload=complete,
        )


def _runtime_result(state: RuntimeState, *, status: str) -> RuntimeResult:
    state.finalize_ledger()
    output_frame = _runtime_output_frame(state, status=status)
    state.trace_observations["output_observation"] = dict(
        output_frame.guardrail_result.get("output_observation") or {}
    )
    state.deliverable_statuses = [
        {"goal_id": item.goal_id, "deliverable_type": item.deliverable_type, "status": item.status}
        for item in output_frame.composite_output.deliverables
    ]
    effective_status = status
    if status == "completed" and not state.artifacts.get("clarification") and output_frame.composite_output.deliverables:
        if output_frame.composite_output.overall_status == "failed":
            effective_status = "failed"
        elif output_frame.composite_output.overall_status == "blocked":
            effective_status = "blocked"
    state.status = effective_status  # type: ignore[assignment]
    complete = build_complete_payload(
        state=state,
        status=effective_status,  # type: ignore[arg-type]
        output_frame=output_frame,
    )
    return RuntimeResult(
        status=effective_status,  # type: ignore[arg-type]
        node_results=list(state.node_results),
        evidence_ledger=state.evidence_ledger,
        output_frame=output_frame,
        trace=state.trace_payload(),
        complete_payload=complete,
        cancel_payload=None,
    )


def _runtime_output_frame(state: RuntimeState, *, status: str, cancelled: bool = False):
    bundle = project_ledger_to_evidence_bundle(
        state.evidence_ledger,
        trace_id=state.trace_id,
        task={"plan_id": state.plan.plan_id},
    )
    return build_output_frame(
        status=status,
        artifacts=state.artifacts,
        evidence_bundle=bundle,
        node_results=state.node_results,
        error=state.errors[-1] if state.errors else None,
        cancelled=cancelled,
        cancel_reason=state.cancel_token.reason if cancelled else None,
        output_contract=state.plan.output_contract,
        goals=state.plan.goals,
    )


def _is_executable_validated_plan(plan: ExecutionPlan) -> bool:
    plan_version = str(plan.plan_version or "")
    return bool(plan.plan_id and ".validated" in plan_version and ".blocked" not in plan_version)


def _retry_limit(node: dict[str, Any]) -> int:
    retry = node.get("retry")
    if not isinstance(retry, dict):
        return 0
    if "max_retries" in retry:
        return max(0, int(retry.get("max_retries") or 0))
    if "max_attempts" in retry:
        return max(0, int(retry.get("max_attempts") or 1) - 1)
    return 0


def _node_type(node: dict[str, Any]) -> str:
    return str(node.get("node_type") or node.get("type") or "").strip()


def _summarize_input(node: dict[str, Any]) -> str:
    summary = {
        "node_id": node.get("node_id"),
        "node_type": _node_type(node),
        "inputs": node.get("inputs", {}),
        "required_tools": node.get("required_tools", []),
    }
    return _truncate(str(summary))


def _summarize_output(output: Any) -> str:
    return _truncate(str(output))


def _truncate(value: str, limit: int = 500) -> str:
    return value if len(value) <= limit else f"{value[:limit]}..."
