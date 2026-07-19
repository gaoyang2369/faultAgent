from __future__ import annotations

import json

import pytest

from fault_diagnosis.agent.canonical_turn import CurrentUtteranceParser
from fault_diagnosis.agent.semantics.context_interpreter import (
    project_active_case_semantic_input,
    project_recent_turns_semantic_input,
)
from fault_diagnosis.agent.semantics.model_gateway import ModelGatewayResult, _model_input
from fault_diagnosis.agent.semantics.service import SemanticResolutionService
from fault_diagnosis.domain.canonical_turn import ContextCandidate
from fault_diagnosis.domain.context.conversation_context import ConversationContextAssembler
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.persistence.repositories.conversation_store import MemoryConversationRepository
from fault_diagnosis.server.use_cases.conversation_persistence import _assistant_content_json


def _payload(text: str, *, context: dict | None = None) -> dict:
    return {
        "schema_version": "semantic_turn_proposal.v1",
        "entities": [],
        "ambiguities": [],
        "clauses": [{
            "clause_index": 0,
            "text": text,
            "start": 0,
            "end": len(text),
            "capability": "generate_report" if "报告" in text else "check_runtime_status",
            "confidence": 0.95,
            "source_kind": "prior_result" if "报告" in text else "current_message",
        }],
        "context": context,
    }


class _Gateway:
    model_name = "p1-model"

    def __init__(self, context: dict | None = None) -> None:
        self.context = context
        self.requests = []

    async def invoke_clause_model(self, request, *, cancel_event=None):  # noqa: ANN001
        self.requests.append(request)
        return ModelGatewayResult(_payload(request.current_message, context=self.context), 1.0, 10, 5, False)


def _candidate(*, previous: bool = True) -> ContextCandidate:
    return ContextCandidate(
        candidate_id="internal-secret-candidate",
        artifact_ref="internal-secret-artifact",
        artifact_type="analysis_artifact",
        asset_refs=["G120电机1"],
        fault_codes=["A07089"],
        produced_by_immediately_previous_turn=previous,
        completed=True,
        freshness_state="stale",
        source_kind="artifact_manifest",
    )


def _context() -> dict:
    return {
        "latest_case_state": {
            "case_id": "internal-secret-case",
            "latest_artifact_id": "internal-secret-artifact",
            "active_asset": "G120电机1",
            "active_fault_codes": ["A07089"],
            "latest_artifact_type": "analysis_artifact",
            "evidence_freshness": "stale",
            "reportable": True,
        },
        "recent_turns": [{
            "turn_index": 3,
            "user_goal_summaries": [{"capability": "diagnose_fault", "devices": ["G120电机1"]}],
            "deliverables": [{"capability": "diagnose_fault", "status": "completed"}],
            "fault_codes": ["A07089"],
            "devices": ["G120电机1"],
            "limitations": ["历史数据，非实时"],
            "artifact_id": "must-not-pass",
        }],
        "last_raw_messages": [{"role": "user", "content": "RAW-HISTORY-MUST-NOT-PASS"}],
    }


def test_safe_context_projection_excludes_raw_history_and_internal_ids() -> None:
    candidate = _candidate()
    active = project_active_case_semantic_input(_context(), [candidate])
    recent = project_recent_turns_semantic_input(_context(), [candidate])
    serialized = json.dumps({"active_case": active, "recent_turns": recent}, ensure_ascii=False)

    assert active["active_asset"] == "G120电机1"
    assert recent[0]["user_goal_summaries"][0]["capability"] == "diagnose_fault"
    for forbidden in ("RAW-HISTORY-MUST-NOT-PASS", "internal-secret", "must-not-pass", "artifact_id"):
        assert forbidden not in serialized


def test_grounded_deictic_entity_is_allowed_but_still_requires_current_text_rule() -> None:
    from fault_diagnosis.agent.semantics.intent_interpreter import parse_semantic_turn_proposal

    proposal = parse_semantic_turn_proposal({
        "schema_version": "semantic_turn_proposal.v1",
        "entities": [{
            "kind": "deictic_reference", "text": "刚才", "start": 2, "end": 4,
            "normalized_candidate": "latest", "confidence": 1.0,
        }],
        "clauses": [],
        "ambiguities": [],
    })
    assert proposal.entities[0].kind == "deictic_reference"


def test_completed_turn_persists_and_assembles_only_safe_semantic_summary() -> None:
    event = {
        "goal_set": {"goals": [{
            "goal_id": "secret-goal-id",
            "capability": "diagnose_fault",
            "device_refs": ["G120电机1"],
            "fault_code_refs": ["A07089"],
            "source_artifact_id": "secret-artifact-id",
        }]},
        "composite_output": {"deliverables": [{
            "goal_id": "secret-goal-id",
            "capability": "diagnose_fault",
            "status": "completed",
            "structured_content": {"limitations": ["历史数据，非实时"]},
        }]},
    }
    persisted = _assistant_content_json(event)
    repository = MemoryConversationRepository()
    repository.ensure_thread(thread_id="p1-thread", session_id="p1-session", owner_user_id="p1-user")
    user = repository.append_message(
        thread_id="p1-thread", role="user", content_text="RAW-USER-HISTORY", status="completed",
    )
    repository.append_message(
        thread_id="p1-thread", role="assistant", content_text="RAW-ASSISTANT-HISTORY", status="completed",
        parent_message_id=user["id"], turn_index=user["turn_index"], content_json=persisted,
    )

    package = ConversationContextAssembler(conversation_repository=repository).build(
        thread_id="p1-thread",
        current_user_message="基于刚才结果生成报告",
        auth_context=build_auth_context(user_id="p1-user", role="admin"),
    )
    serialized = json.dumps(package["recent_turns"], ensure_ascii=False)

    assert package["recent_turns"][0]["user_goal_summaries"] == [
        {"capability": "diagnose_fault", "devices": ["G120电机1"]}
    ]
    assert package["recent_turns"][0]["limitations"] == ["历史数据，非实时"]
    for forbidden in ("RAW-USER-HISTORY", "RAW-ASSISTANT-HISTORY", "secret-goal-id", "secret-artifact-id"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_intent_and_context_share_exactly_one_model_call() -> None:
    text = "基于刚才的诊断生成报告"
    context_proposal = {
        "reference_target": "prior_diagnosis_result",
        "temporal_relation": "previous",
        "include_asset_refs": ["G120电机1"],
        "requested_reuse": True,
        "freshness_intent": "historical_ok",
        "confidence": 0.95,
    }
    gateway = _Gateway(context_proposal)
    service = SemanticResolutionService(
        parser=CurrentUtteranceParser(),
        gateway_factory=lambda _semaphore: gateway,
        mode="primary",
        call_policy="always",
        context_semantics_enabled=True,
    )

    result = await service.resolve(
        text,
        context_candidates=[_candidate()],
        pending_summary={"kind": "clarification", "missing_slots": ["device"]},
        conversation_context=_context(),
    )

    assert len(gateway.requests) == 1
    packet = gateway.requests[0]
    assert packet.current_message == text
    assert packet.active_case["active_asset"] == "G120电机1"
    assert packet.recent_turns[0]["turn_index"] == 3
    assert packet.pending_clarification["missing_slots"] == ["device"]
    assert result.context_proposal is not None
    assert result.context_proposal.reference_target == "prior_diagnosis_result"
    model_input = json.dumps(_model_input(packet), ensure_ascii=False)
    assert "internal-secret" not in model_input
    assert "RAW-HISTORY-MUST-NOT-PASS" not in model_input


@pytest.mark.asyncio
async def test_context_semantics_switch_removes_context_from_packet_and_result() -> None:
    gateway = _Gateway({
        "reference_target": "prior_diagnosis_result",
        "requested_reuse": True,
        "freshness_intent": "historical_ok",
    })
    service = SemanticResolutionService(
        parser=CurrentUtteranceParser(),
        gateway_factory=lambda _semaphore: gateway,
        mode="primary",
        call_policy="always",
        context_semantics_enabled=False,
    )

    result = await service.resolve(
        "查询 G120电机1 当前状态",
        context_candidates=[_candidate()],
        pending_summary={"missing_slots": ["device"]},
        conversation_context=_context(),
    )

    packet = gateway.requests[0]
    assert packet.active_case == {}
    assert packet.recent_turns == ()
    assert packet.context_candidates == ()
    assert packet.pending_clarification is None
    assert result.context_proposal is None


@pytest.mark.asyncio
async def test_call_policies_always_auto_and_off() -> None:
    parser = CurrentUtteranceParser()
    simple = "查询 G120电机1 当前状态"

    always_gateway = _Gateway()
    await SemanticResolutionService(
        parser=parser, gateway_factory=lambda _semaphore: always_gateway,
        mode="primary", call_policy="always",
    ).resolve(simple)
    assert len(always_gateway.requests) == 1

    auto_gateway = _Gateway()
    auto = SemanticResolutionService(
        parser=parser, gateway_factory=lambda _semaphore: auto_gateway,
        mode="primary", call_policy="auto",
    )
    simple_result = await auto.resolve(simple)
    assert auto_gateway.requests == []
    assert simple_result.trace.attempted is False
    await auto.resolve("基于刚才的诊断生成报告", context_candidates=[_candidate()])
    assert len(auto_gateway.requests) == 1

    off_gateway = _Gateway()
    await SemanticResolutionService(
        parser=parser, gateway_factory=lambda _semaphore: off_gateway,
        mode="primary", call_policy="off",
    ).resolve("基于刚才的诊断生成报告")
    assert off_gateway.requests == []


@pytest.mark.asyncio
async def test_unavailable_historical_reference_is_rejected_before_binding() -> None:
    gateway = _Gateway({
        "reference_target": "prior_report",
        "temporal_relation": "previous",
        "requested_reuse": True,
        "freshness_intent": "historical_ok",
    })
    service = SemanticResolutionService(
        parser=CurrentUtteranceParser(), gateway_factory=lambda _semaphore: gateway,
        mode="primary", call_policy="always", context_semantics_enabled=True,
    )

    result = await service.resolve("基于刚才的诊断生成报告", context_candidates=[_candidate()])

    assert result.context_proposal is None
    assert result.context_clarification_reason == "context_reference_unavailable"
    assert "context.reference_target" in result.trace.clarify
