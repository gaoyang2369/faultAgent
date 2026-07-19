"""Wave 1：统一异步语义入口的合同测试。"""

from __future__ import annotations

import asyncio
import json

import pytest

from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator, CurrentUtteranceParser
from fault_diagnosis.agent.semantics import AsyncModelGateway, SemanticResolutionService
from fault_diagnosis.agent.semantics.model_gateway import ModelGatewayResult, SemanticModelCancelled
from fault_diagnosis.agent.semantics.model_request import SemanticContextPacket
from fault_diagnosis.domain.canonical_turn import TurnCommand
from fault_diagnosis.domain.security.permissions import build_auth_context


def _command(message: str = "查询 G120电机1 当前状态") -> TurnCommand:
    return TurnCommand(
        command="preview",
        thread_id="semantic-wave1-thread",
        user_id="semantic-wave1-user",
        turn_id="semantic-wave1-turn",
        message_id="semantic-wave1-message",
        idempotency_key="semantic-wave1-key",
        raw_message=message,
    )


def _payload(text: str) -> dict:
    return {
        "schema_version": "semantic_turn_proposal.v1",
        "entities": [],
        "ambiguities": [],
        "clauses": [{
            "clause_index": 0,
            "text": text,
            "start": 0,
            "end": len(text),
            "capability": "check_runtime_status", "confidence": 0.9,
        }],
    }


class _Gateway:
    model_name = "wave1-test-model"

    def __init__(self, *, payload: dict | None = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls = 0

    async def invoke_clause_model(self, request, *, cancel_event=None):  # noqa: ANN001
        self.calls += 1
        if self.error:
            raise self.error
        return ModelGatewayResult(
            payload=self.payload or _payload(request.text),
            latency_ms=12.5,
            input_tokens=11,
            output_tokens=7,
            concurrency_limited=False,
        )


@pytest.mark.asyncio
async def test_async_preview_attempts_one_model_call_and_keeps_canonical_deterministic() -> None:
    gateway = _Gateway()
    parser = CurrentUtteranceParser()
    service = SemanticResolutionService(
        parser=parser,
        gateway_factory=lambda _semaphore: gateway,
        mode="primary",
    )
    result = await ConversationTurnCoordinator(parser=parser, semantic_service=service).preview_turn_async(
        _command(), auth_context=build_auth_context(user_id="semantic-wave1-user", role="admin")
    )

    assert gateway.calls == 1
    assert [goal.capability for goal in result.request.goals] == ["check_runtime_status"]
    semantic = next(event.detail["semantic"] for event in result.events if event.event_type == "intent_resolution")
    assert semantic | {"field_decisions": []} == {
        "attempted": True,
        "mode": "primary",
        "schema": "semantic_turn_proposal.v1",
        "status": "completed",
        "accepted": ["clauses[0].capability"],
        "rejected": [],
        "clarify": [],
        "fallback": False,
        "fallback_reason": "",
        "latency_ms": 12.5,
        "input_tokens": 11,
        "output_tokens": 7,
        "model_name": "wave1-test-model",
        "concurrency_limited": False,
        "deterministic_capabilities": ["check_runtime_status"],
        "proposal_capabilities": ["check_runtime_status"],
        "field_decisions": [],
    }
    assert semantic["field_decisions"][0]["decision"] == "ACCEPT"


@pytest.mark.asyncio
async def test_model_timeout_falls_back_only_to_deterministic_control_plane() -> None:
    gateway = _Gateway(error=TimeoutError("slow"))
    parser = CurrentUtteranceParser()
    service = SemanticResolutionService(parser=parser, gateway_factory=lambda _semaphore: gateway, mode="primary")
    coordinator = ConversationTurnCoordinator(parser=parser, semantic_service=service)

    result = await coordinator.execute_turn(
        _command(),
        auth_context=build_auth_context(user_id="semantic-wave1-user", role="admin"),
        executor=lambda preview: {"execution_performed": False, "terminal_status": "completed"},
    )

    assert gateway.calls == 1
    assert result.terminal_status == "completed"
    semantic = next(event.detail["semantic"] for event in result.events if event.event_type == "intent_resolution")
    assert semantic["status"] == "model_timeout"
    assert semantic["fallback"] is True
    assert [goal.capability for goal in result.request.goals] == ["check_runtime_status"]


class _SlowClient:
    async def ainvoke(self, _messages):  # noqa: ANN001
        await asyncio.sleep(10)
        return type("Response", (), {"content": json.dumps({})})()


def _gateway_request():
    return SemanticContextPacket(current_message="查询 J1", deterministic_parse={"entities": [], "clauses": []})


@pytest.mark.asyncio
async def test_gateway_cancellation_interrupts_the_pending_async_request() -> None:
    cancel_event = asyncio.Event()
    gateway = AsyncModelGateway(client=_SlowClient(), model_name="cancel-test", timeout_seconds=5)
    task = asyncio.create_task(gateway.invoke_clause_model(_gateway_request(), cancel_event=cancel_event))
    await asyncio.sleep(0)
    cancel_event.set()
    with pytest.raises(SemanticModelCancelled):
        await task


@pytest.mark.asyncio
async def test_gateway_cancellation_also_interrupts_concurrency_queue() -> None:
    cancel_event = asyncio.Event()
    gateway = AsyncModelGateway(
        client=_SlowClient(), model_name="queue-cancel-test", timeout_seconds=5, semaphore=asyncio.Semaphore(0)
    )
    task = asyncio.create_task(gateway.invoke_clause_model(_gateway_request(), cancel_event=cancel_event))
    await asyncio.sleep(0)
    cancel_event.set()
    with pytest.raises(SemanticModelCancelled):
        await task
