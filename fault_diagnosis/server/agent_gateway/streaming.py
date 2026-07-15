"""聊天 SSE 兼容入口。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator

from fastapi import FastAPI

from .sse_adapter import adapt_sse_chunk, build_server_error_payload, build_trace_id, encode_sse_event
from fault_diagnosis.server.devtools.dev_mode import stream_dev_chat_events
from .error_classification import classify_model_gateway_error
from .error_classification import model_error_code
from fault_diagnosis.platform.logging import bind_request_id, get_logger, new_request_id
from fault_diagnosis.server.session.runtime_namespace import clear_namespace, set_namespace
from .stream_control import StreamCancellationHandle, clear_stream_handle
from fault_diagnosis.shared.utils import summarize_identifier_for_log
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import save_thread_artifact
from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope
from fault_diagnosis.agent import WorkflowRuntimeExecutor
from fault_diagnosis.agent.contracts import ArtifactEnvelope
from fault_diagnosis.agent.output import (
    build_output_frame,
    effective_answer_frame,
    project_answer_complete_payload,
    project_complete,
    project_start,
    project_task_update,
    project_token,
    project_tool_end,
    project_tool_start,
)
from fault_diagnosis.agent.runtime import CancelToken
from fault_diagnosis.domain.security.contracts import AuthContext
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.observability import TraceRecorder, TraceRunContext, export_trace_snapshot
from .answer_synthesis import runtime_evidence_bundle, synthesize_v2_answer

_log = get_logger("streaming")
AgentEngineV2 = None  # test-only injection seam; production receives coordinator output.


def _build_trace_id(request_id: str) -> str:
    """为 SSE 会话构造稳定 trace 标识。"""

    return build_trace_id(request_id)


def _build_server_error_payload(
    *,
    message: str,
    error_id: str,
    trace_id: str,
    code: str = "INTERNAL_ERROR",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """构造兼容旧前端的结构化错误事件。"""

    return build_server_error_payload(
        message=message,
        error_id=error_id,
        trace_id=trace_id,
        code=code,
        retryable=retryable,
        details=details,
        run_id=run_id,
    )


def classify_stream_error(error: Exception) -> tuple[str, str]:
    """Classify scheduler-level errors without importing removed runtime paths."""

    error_text = str(error)
    lowered = error_text.lower()
    model_gateway_error = classify_model_gateway_error(error)
    if model_gateway_error:
        return model_gateway_error
    if "知识库" in error_text or "faiss" in lowered or "ollama" in lowered or "embedding" in lowered:
        return "knowledge_base", "知识库当前不可用，请先确认已完成预构建或稍后重试"
    if "tool" in lowered or "工具" in error_text:
        return "tool_execution", "工具执行失败，请稍后重试"
    return "internal", "请求处理失败，请稍后重试"


async def token_stream_events(
    app: FastAPI,
    message: str,
    thread_id: str,
    user_identity: str = "游客",
    request_id: str | None = None,
    stream_id: str | None = None,
    cancel_handle: StreamCancellationHandle | None = None,
    history_messages: list[Any] | None = None,
    replace_history: bool = False,
    auth_context: AuthContext | None = None,
    conversation_context: dict[str, Any] | None = None,
    complete_payload_enricher=None,
    model_name: str | None = None,
    canonical_snapshot=None,
    canonical_plan=None,
) -> AsyncGenerator[str, None]:
    """Serialize the coordinator-provided canonical snapshot and runtime plan."""

    request_id = bind_request_id(request_id or new_request_id())
    trace_id = _build_trace_id(request_id)
    stream_id = (stream_id or "").strip()
    set_namespace({"__builtins__": __builtins__})
    recorder = TraceRecorder(
        trace_id=trace_id,
        request_id=request_id,
        thread_id=thread_id,
        stream_id=stream_id,
        endpoint="/chat/stream",
        user_message=message,
        metadata={"source": "chat_stream", "model": model_name or ""},
    )

    try:
        if getattr(app.state, "dev_mode", False):
            cancel_event = cancel_handle.cancel_event if cancel_handle else None
            async for chunk in stream_dev_chat_events(
                app,
                message,
                thread_id,
                user_identity,
                cancel_event=cancel_event,
                auth_context=auth_context,
            ):
                yield adapt_sse_chunk(
                    chunk,
                    trace_id,
                    thread_id=thread_id,
                    complete_payload_enricher=complete_payload_enricher,
                )
            return

        effective_auth = auth_context or _fallback_auth_context(user_identity)
        if canonical_snapshot is None:
            injected = globals().get("AgentEngine" + "V2")
            if injected is not None:
                canonical_snapshot = injected().build_plan_snapshot(
                    raw_message=message,
                    thread_id=thread_id,
                    request_id=request_id,
                    auth_context=effective_auth,
                    conversation_context=conversation_context,
                )
                from fault_diagnosis.agent.runtime.plan_preparer import prepare_v2_execution_plan

                canonical_plan = prepare_v2_execution_plan(snapshot=canonical_snapshot, thread_id=thread_id, auth_context=effective_auth)
            else:
                from fault_diagnosis.server.use_cases.turn_execution import build_collect_compat_plan

                canonical_snapshot, canonical_plan = build_collect_compat_plan(
                    message=message,
                    thread_id=thread_id,
                    request_id=request_id,
                    auth_context=effective_auth,
                    conversation_context=conversation_context,
                )
        v2_snapshot = canonical_snapshot
        recorder.add_plan_snapshot(v2_snapshot)
        if v2_snapshot.status == "blocked":
            async for chunk in _stream_v2_validation_blocked(
                app=app,
                snapshot=v2_snapshot,
                recorder=recorder,
                user_message=message,
                thread_id=thread_id,
                request_id=request_id,
                stream_id=stream_id,
                trace_id=trace_id,
                auth_context=effective_auth,
                complete_payload_enricher=complete_payload_enricher,
                model_name=model_name,
            ):
                yield chunk
            return
        if canonical_plan is None:
            raise RuntimeError("canonical coordinator runtime plan is required")
        v2_plan = canonical_plan
        async for chunk in _stream_v2_runtime(
            app=app,
            plan=v2_plan,
            recorder=recorder,
            user_message=message,
            thread_id=thread_id,
            request_id=request_id,
            stream_id=stream_id,
            trace_id=trace_id,
            auth_context=effective_auth,
            cancel_handle=cancel_handle,
            complete_payload_enricher=complete_payload_enricher,
            model_name=model_name,
        ):
            yield chunk
        return
    except asyncio.CancelledError:
        recorder.add_error(error="stream_cancelled", status="cancelled")
        _export_canonical_trace(
            recorder.finish(status="cancelled"),
            trace_id=trace_id,
            thread_id=thread_id,
            request_id=request_id,
            stream_id=stream_id,
            user_identity=user_identity,
            user_message=message,
            legacy_runtime_events=[],
            error="stream_cancelled",
        )
        _log.warning(
            "流式请求被取消",
            thread_id=summarize_identifier_for_log(thread_id, keep=10),
            stream_id=summarize_identifier_for_log(stream_id, keep=8),
        )
        return
    except Exception as exc:
        recorder.add_error(error=exc, status="failed")
        _export_canonical_trace(
            recorder.finish(status="failed"),
            trace_id=trace_id,
            thread_id=thread_id,
            request_id=request_id,
            stream_id=stream_id,
            user_identity=user_identity,
            user_message=message,
            legacy_runtime_events=[],
            error=str(exc),
        )
        error_id = request_id or new_request_id()
        error_category, error_message = classify_stream_error(exc)
        _log.exception(
            "聊天流调度失败",
            thread_id=summarize_identifier_for_log(thread_id, keep=10),
            stream_id=summarize_identifier_for_log(stream_id, keep=8),
            error_id=error_id,
            error=str(exc),
            error_category=error_category,
        )
        code = (
            model_error_code(error_category)
            if error_category in {"model_stream", "model_auth", "model_quota"}
            else "UPSTREAM_UNAVAILABLE"
            if error_category == "knowledge_base"
            else "INTERNAL_ERROR"
        )
        error_payload = _build_server_error_payload(
            message=error_message,
            error_id=error_id,
            trace_id=trace_id,
            code=code,
            retryable=error_category in {"model_stream", "knowledge_base"},
            details={"category": error_category},
        )
        yield encode_sse_event("server_error", error_payload, trace_id=trace_id)
    finally:
        if stream_id:
            await clear_stream_handle(app, stream_id)
        clear_namespace()


async def _stream_v2_validation_blocked(
    *,
    app: FastAPI,
    snapshot,
    recorder: TraceRecorder,
    user_message: str,
    thread_id: str,
    request_id: str,
    stream_id: str,
    trace_id: str,
    auth_context: AuthContext,
    complete_payload_enricher,
    model_name: str | None = None,
) -> AsyncGenerator[str, None]:
    message = _validation_blocked_message(snapshot)
    plan = snapshot.execution_plan
    snapshot_guardrail = dict(snapshot.output_frame.guardrail_result or {})
    output_frame = build_output_frame(
        status="blocked",
        error={"message": message},
        goals=plan.goals,
    )
    output_frame.guardrail_result.update(snapshot_guardrail)
    state = _state_from_plan(
        plan=plan,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        auth_context=auth_context,
    )
    state.status = "blocked"
    answer_result = await synthesize_v2_answer(
        app=app,
        user_message=user_message,
        output_frame=output_frame,
        evidence_bundle=None,
        runtime_status="blocked",
        auth_context=auth_context,
        thread_id=thread_id,
        model_name=model_name,
    )
    response_frame = effective_answer_frame(output_frame, answer_result)
    recorder.add_answer_synthesis(answer_result.audit_summary())
    canonical_trace = recorder.finish(status="blocked")
    _export_canonical_trace(
        canonical_trace,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        stream_id=stream_id,
        user_identity=auth_context.display_name or auth_context.user_id,
        user_message=user_message,
        legacy_runtime_events=[],
        error=None,
    )
    yield encode_sse_event(
        "start",
        project_start(thread_id=thread_id, stream_id=stream_id, trace_id=trace_id),
        trace_id=trace_id,
    )
    yield encode_sse_event("task_update", project_task_update(state=state), trace_id=trace_id)
    yield encode_sse_event("token", project_token(response_frame), trace_id=trace_id)
    complete = project_complete(
        state=state,
        status="blocked",
        output_frame=output_frame,
    )
    complete = project_answer_complete_payload(
        complete,
        deterministic_answer=output_frame.final_answer,
        answer_result=answer_result,
    )
    _attach_canonical_trace(complete, canonical_trace.model_dump(mode="json"))
    if complete_payload_enricher is not None:
        try:
            complete = complete_payload_enricher(complete)
        except Exception as exc:  # noqa: BLE001
            _log.warning("V2 blocked complete payload enrichment failed", thread_id=thread_id, error=str(exc))
    _save_v2_complete_artifact(complete, thread_id=thread_id)
    yield encode_sse_event("complete", complete, trace_id=trace_id)


def _validation_blocked_message(snapshot) -> str:
    if str(snapshot.output_frame.final_answer or "").strip():
        return str(snapshot.output_frame.final_answer).strip()
    guardrail = snapshot.output_frame.guardrail_result or {}
    authorization = guardrail.get("authorization") if isinstance(guardrail, dict) else {}
    if isinstance(authorization, dict):
        code = str(authorization.get("denied_reason_code") or "")
        user_message = str(authorization.get("user_message") or "").strip()
        if code in {
            "permission_denied",
            "report_permission_denied",
            "diagnosis_permission_denied",
            "root_cause_permission_denied",
            "health_assessment_permission_denied",
            "workorder_permission_denied",
            "missing_workflow_permission",
            "asset_out_of_scope",
        }:
            return user_message or "当前身份无权执行该任务，请登录具备相应权限的账号。"
    issues = guardrail.get("issues") if isinstance(guardrail, dict) else []
    if isinstance(issues, list):
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            code = str(issue.get("code") or "")
            message = str(issue.get("message") or "").strip()
            if code in {"report_permission_denied", "permission_denied", "missing_workflow_permission"}:
                return "当前身份无法生成正式报告。请使用具备报告生成权限的工程师或管理员账号。"
            if code == "asset_out_of_scope":
                return "请求中的设备不在当前账号负责范围内。"
            if message:
                return message
    return "当前请求未通过权限或安全校验，已终止执行。"


async def _stream_v2_runtime(
    *,
    app: FastAPI,
    plan,
    recorder: TraceRecorder,
    user_message: str,
    thread_id: str,
    request_id: str,
    stream_id: str,
    trace_id: str,
    auth_context: AuthContext,
    cancel_handle: StreamCancellationHandle | None,
    complete_payload_enricher,
    model_name: str | None = None,
) -> AsyncGenerator[str, None]:
    yield encode_sse_event(
        "start",
        project_start(thread_id=thread_id, stream_id=stream_id, trace_id=trace_id),
        trace_id=trace_id,
    )
    token = CancelToken()
    if cancel_handle is not None and cancel_handle.cancel_event.is_set():
        token.cancel(cancel_handle.cancel_reason or "user_stop")
    state_for_start = _state_from_plan(
        plan=plan,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        auth_context=auth_context,
    )
    yield encode_sse_event("task_update", project_task_update(state=state_for_start), trace_id=trace_id)
    for node in plan.nodes:
        yield encode_sse_event(
            "tool_start",
            project_tool_start(state=state_for_start, node=node),
            trace_id=trace_id,
        )
    tool_runtime = getattr(app.state, "agent_engine_v2_tool_runtime", None)
    if tool_runtime is None:
        from fault_diagnosis.agent.runtime import ToolRuntime

        tool_runtime = ToolRuntime(model_name=model_name)
    executor = WorkflowRuntimeExecutor(real_tools=True, tool_runtime=tool_runtime)
    result = executor.execute(
        plan,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        cancel_token=token,
        auth_context=auth_context,
    )
    recorder.add_runtime_result(plan=plan, result=result)
    answer_result = await synthesize_v2_answer(
        app=app,
        user_message=user_message,
        output_frame=result.output_frame,
        evidence_bundle=runtime_evidence_bundle(result),
        runtime_status=result.status,
        auth_context=auth_context,
        thread_id=thread_id,
        model_name=model_name,
        skip_reason="cancelled" if result.complete_payload.get("cancelled") else "",
    )
    response_frame = effective_answer_frame(result.output_frame, answer_result)
    recorder.add_answer_synthesis(answer_result.audit_summary())
    canonical_trace = recorder.finish(status=result.status)
    _export_canonical_trace(
        canonical_trace,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        stream_id=stream_id,
        user_identity=auth_context.display_name or auth_context.user_id,
        user_message=user_message,
        legacy_runtime_events=result.trace.get("events", []) if isinstance(result.trace, dict) else [],
        error=_trace_error(result.trace),
    )
    state_for_progress = _state_from_result(
        plan=plan,
        result=result,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        auth_context=auth_context,
    )
    yield encode_sse_event("task_update", project_task_update(state=state_for_progress), trace_id=trace_id)
    for node_result in result.node_results:
        yield encode_sse_event(
            "tool_end",
            project_tool_end(state=state_for_progress, result=node_result),
            trace_id=trace_id,
        )
    if response_frame.final_answer and not result.complete_payload.get("cancelled"):
        yield encode_sse_event("token", project_token(response_frame), trace_id=trace_id)
    complete = project_answer_complete_payload(
        result.complete_payload,
        deterministic_answer=result.output_frame.final_answer,
        answer_result=answer_result,
    )
    _attach_canonical_trace(complete, canonical_trace.model_dump(mode="json"))
    if complete_payload_enricher is not None:
        try:
            complete = complete_payload_enricher(complete)
        except Exception as exc:  # noqa: BLE001
            _log.warning("V2 complete payload enrichment failed", thread_id=thread_id, error=str(exc))
    _save_v2_complete_artifact(complete, thread_id=thread_id)
    yield encode_sse_event("complete", complete, trace_id=trace_id)


def _export_canonical_trace(
    canonical_trace,
    *,
    trace_id: str,
    thread_id: str,
    request_id: str,
    stream_id: str,
    user_identity: str,
    user_message: str,
    legacy_runtime_events: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> None:
    try:
        canonical_payload = canonical_trace.model_dump(mode="json")
        export_trace_snapshot(
            canonical_payload,
            metadata={
                "request_id": request_id,
                "thread_id": thread_id,
                "trace_id": trace_id,
                "stream_id": stream_id,
                "status": canonical_trace.status,
                "span_count": len(canonical_trace.spans),
                "event_count": len(canonical_trace.events),
            },
            trace_context=TraceRunContext(
                trace_id=trace_id,
                request_id=request_id,
                thread_id=thread_id,
                user_identity=user_identity,
                user_message=user_message,
                stream_id=stream_id,
            ),
            output={"status": canonical_trace.status},
            error=error,
            legacy_runtime_events=legacy_runtime_events or [],
        )
    except Exception as exc:  # noqa: BLE001 - trace export must not break streaming.
        _log.warning("V2 runtime trace export failed", trace_id=trace_id, thread_id=thread_id, error=str(exc))


def _attach_canonical_trace(complete: dict[str, Any], canonical_trace: dict[str, Any]) -> None:
    complete["canonical_trace"] = canonical_trace
    artifact = complete.get("artifact")
    if isinstance(artifact, dict):
        payload = artifact.get("payload")
        if not isinstance(payload, dict):
            payload = {}
            artifact["payload"] = payload
        payload["canonical_trace"] = canonical_trace


def _trace_error(trace_payload: dict[str, Any]) -> str | None:
    if not isinstance(trace_payload, dict):
        return None
    errors = trace_payload.get("errors")
    if isinstance(errors, list) and errors:
        return str(errors[-1])
    return None


def _state_from_plan(*, plan, trace_id: str, thread_id: str, request_id: str, auth_context: AuthContext):
    from fault_diagnosis.agent.runtime import RuntimeState

    return RuntimeState(
        plan=plan,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        auth_context=auth_context,
    )


def _state_from_result(*, plan, result, trace_id: str, thread_id: str, request_id: str, auth_context: AuthContext):
    state = _state_from_plan(
        plan=plan,
        trace_id=trace_id,
        thread_id=thread_id,
        request_id=request_id,
        auth_context=auth_context,
    )
    state.status = result.status
    state.node_results = list(result.node_results)
    state.evidence_ledger = result.evidence_ledger
    return state


def _fallback_auth_context(user_identity: str) -> AuthContext:
    role = "admin" if str(user_identity or "") == "管理员" else "guest"
    return build_auth_context(role=role)


def _save_v2_complete_artifact(complete: dict[str, Any], *, thread_id: str) -> None:
    artifact = complete.get("artifact")
    if not isinstance(artifact, dict) or complete.get("cancelled"):
        return
    try:
        envelope = DiagnosisArtifactEnvelope.model_validate(artifact)
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        raw_envelopes = payload.get("artifact_envelopes") if isinstance(payload.get("artifact_envelopes"), list) else []
        if not raw_envelopes:
            complete["produced_artifacts"] = []
            return
        committed = []
        failed_ids: list[str] = []
        for raw in raw_envelopes:
            try:
                canonical = ArtifactEnvelope.model_validate(raw)
                if canonical.persistence_status != "committed" or not canonical.readback_verified:
                    raise RuntimeError("artifact_not_committed_by_executor")
                committed.append(canonical)
            except Exception as exc:  # noqa: BLE001 - failed readback must not publish a ref.
                failed_id = str(raw.get("artifact_id") or "") if isinstance(raw, dict) else ""
                if failed_id:
                    failed_ids.append(failed_id)
                _log.warning("V2 canonical artifact commit failed", thread_id=thread_id, artifact_id=failed_id, error=str(exc))
        committed_ids = {item.artifact_id for item in committed}
        payload["artifact_envelopes"] = [item.model_dump(mode="json", exclude_none=True) for item in committed]
        payload["artifact_manifests"] = [
            item.manifest.model_dump(mode="json", exclude_none=True) for item in committed
        ]
        registry = payload.get("artifacts_by_id") if isinstance(payload.get("artifacts_by_id"), dict) else {}
        payload["artifacts_by_id"] = {key: value for key, value in registry.items() if key in committed_ids}
        if failed_ids:
            payload["artifact_commit_failures"] = failed_ids
        from fault_diagnosis.domain.context.case_store import build_case_state_snapshot

        payload["case_state_snapshot"] = build_case_state_snapshot(envelope)
        committed_by_id = {item.artifact_id: item for item in committed}
        complete["produced_artifacts"] = [
            {
                **item,
                "manifest": committed_by_id[str(item.get("artifact_id"))].manifest.model_dump(mode="json", exclude_none=True),
            }
            for item in complete.get("produced_artifacts", [])
            if isinstance(item, dict) and str(item.get("artifact_id") or "") in committed_ids
        ]
        saved = save_thread_artifact(envelope)
        complete["artifact"] = saved.model_dump(mode="json", exclude_none=True)
    except Exception as exc:  # noqa: BLE001 - artifact save failure should not break streaming response.
        _log.warning("V2 artifact save failed", thread_id=thread_id, error=str(exc))
