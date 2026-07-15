"""OpenAI 兼容模型的一次异步 JSON 调用边界。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from fault_diagnosis.agent.canonical_turn.llm_structured_clause_model import INTENT_CLAUSE_SYSTEM_PROMPT
from fault_diagnosis.agent.canonical_turn.model_clause_parser import ClauseModelRequest
from fault_diagnosis.platform import settings


class SemanticModelCancelled(Exception):
    """取消语义调用，不把它升级为控制平面失败。"""


@dataclass(frozen=True)
class ModelGatewayResult:
    payload: dict[str, Any]
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    concurrency_limited: bool


class AsyncModelGateway:
    """每次请求只发起一次 ``ainvoke``，并在此处统一超时与取消。"""

    def __init__(
        self,
        *,
        client: Any,
        model_name: str,
        timeout_seconds: float,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self._client = client
        self.model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._semaphore = semaphore

    async def invoke_clause_model(
        self,
        request: ClauseModelRequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> ModelGatewayResult:
        if cancel_event is not None and cancel_event.is_set():
            raise SemanticModelCancelled()
        model_input = _model_input(request)
        messages = [
            SystemMessage(content=INTENT_CLAUSE_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(model_input, ensure_ascii=False, separators=(",", ":"))),
        ]
        started = time.perf_counter()
        concurrency_limited = bool(self._semaphore and self._semaphore.locked())
        if self._semaphore is None:
            response = await self._await_response(messages, cancel_event)
        else:
            await self._acquire_semaphore(cancel_event)
            try:
                response = await self._await_response(messages, cancel_event)
            finally:
                self._semaphore.release()
        payload = _decode_payload(response)
        input_tokens, output_tokens = _usage_tokens(response)
        return ModelGatewayResult(
            payload=payload,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            concurrency_limited=concurrency_limited,
        )

    async def _await_response(self, messages, cancel_event: asyncio.Event | None):  # noqa: ANN001
        request_task = asyncio.create_task(self._client.ainvoke(messages))
        cancel_task = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
        tasks = {request_task} | ({cancel_task} if cancel_task is not None else set())
        try:
            done, _ = await asyncio.wait(tasks, timeout=self._timeout_seconds, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
                raise TimeoutError("semantic model request timed out")
            if cancel_task is not None and cancel_task in done:
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
                raise SemanticModelCancelled()
            return await request_task
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)

    async def _acquire_semaphore(self, cancel_event: asyncio.Event | None) -> None:
        """等待并发配额时也响应取消，避免排队请求占用 stream 生命周期。"""

        if cancel_event is None:
            await self._semaphore.acquire()
            return
        acquire_task = asyncio.create_task(self._semaphore.acquire())
        cancel_task = asyncio.create_task(cancel_event.wait())
        try:
            done, _ = await asyncio.wait({acquire_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
            if cancel_task in done:
                if acquire_task in done:
                    await acquire_task
                    self._semaphore.release()
                else:
                    acquire_task.cancel()
                    await asyncio.gather(acquire_task, return_exceptions=True)
                raise SemanticModelCancelled()
            await acquire_task
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)


def build_semantic_model_gateway(*, semaphore: asyncio.Semaphore | None = None) -> AsyncModelGateway:
    """按新配置构造唯一的生产语义网关。"""

    model_name = settings.INTENT_MODEL_NAME or (os.getenv("MODEL_NAME") or "").strip()
    api_key = settings.INTENT_MODEL_API_KEY or (os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = settings.INTENT_MODEL_BASE_URL or (os.getenv("OPENAI_BASE_URL") or "").strip()
    if not model_name or not api_key:
        raise RuntimeError("intent_model_not_configured")
    return AsyncModelGateway(
        client=ChatOpenAI(
            model=model_name,
            base_url=base_url or None,
            api_key=api_key,
            temperature=settings.INTENT_MODEL_TEMPERATURE,
            timeout=settings.INTENT_MODEL_TIMEOUT_SECONDS,
            max_tokens=settings.INTENT_MODEL_MAX_TOKENS,
            max_retries=0,
            model_kwargs={"response_format": {"type": "json_object"}},
        ),
        model_name=model_name,
        timeout_seconds=settings.INTENT_MODEL_TIMEOUT_SECONDS,
        semaphore=semaphore,
    )


def _model_input(request: ClauseModelRequest) -> dict[str, Any]:
    return {
        "schema_version": request.schema_version,
        "text": request.text,
        "deterministic_entities": list(request.deterministic_entities),
        "deterministic_parse": {
            "clauses": list(request.deterministic_clauses),
            "fallback_reasons": list(request.fallback_reasons),
        },
        "allowed_capabilities": list(request.allowed_capabilities),
        "allowed_source_kinds": list(request.allowed_source_kinds),
        "authorized_context_candidates": list(getattr(request, "context_candidates", ())),
        "pending_context": getattr(request, "pending_summary", None),
        "output_schema": request.response_schema,
        "clause_fields": [
            "clause_index", "text", "start", "end", "action", "source", "slot", "linker", "shadow_metadata",
        ],
        "shadow_metadata_fields": [
            "requested", "negated", "conditional", "sequence_index", "depends_on", "relation", "unsupported_by_deterministic_action",
        ],
        "semantic_turn_proposal_fields": {
            "schema_version": "semantic_turn_proposal.v1",
            "entities": ["kind", "text", "start", "end", "normalized_candidate", "confidence"],
            "clauses": [
                "clause_index", "text", "start", "end", "capability", "confidence", "entity_indexes",
                "requested", "negated", "conditional", "condition_type", "sequence_index",
                "depends_on_clause_indexes", "source_kind",
            ],
            "context": {
                "reference_target": ["none", "prior_diagnosis_result", "prior_runtime_result", "prior_report", "prior_comparison"],
                "temporal_relation": ["previous", "latest", "earliest", "ordinal"],
                "ordinal": "positive integer only when temporal_relation is ordinal",
                "include_asset_refs": "authorized asset display names only",
                "exclude_asset_refs": "authorized asset display names only",
                "requested_reuse": "boolean",
                "freshness_intent": ["current_required", "historical_ok", "unspecified"],
                "relation": ["none", "worse_device_from_previous_comparison", "comparison_member", "related_prior_result"],
                "confidence": "0..1",
            },
            "forbidden": ["artifact_id", "artifact_ref", "candidate_id", "lineage", "permission", "authorization", "tool", "node", "sql"],
        },
    }


def _decode_payload(response: Any) -> dict[str, Any]:
    content = getattr(response, "content", response)
    if not isinstance(content, str):
        raise ValueError("semantic model returned non-text content")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("semantic model returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("semantic model JSON root must be an object")
    return payload


def _usage_tokens(response: Any) -> tuple[int | None, int | None]:
    metadata = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}) or {}
    usage = metadata.get("token_usage", metadata) if isinstance(metadata, dict) else {}
    if not isinstance(usage, dict):
        return None, None
    return _int_or_none(usage.get("input_tokens", usage.get("prompt_tokens"))), _int_or_none(
        usage.get("output_tokens", usage.get("completion_tokens"))
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
