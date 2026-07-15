"""Wave 2：LLM 提议必须经字段级 Canonical 仲裁。"""

from __future__ import annotations

import pytest

from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator, CurrentUtteranceParser
from fault_diagnosis.agent.semantics import SemanticResolutionService
from fault_diagnosis.agent.semantics.model_gateway import ModelGatewayResult
from fault_diagnosis.domain.canonical_turn import CAPABILITY_SPECS, TurnCommand
from fault_diagnosis.domain.security.permissions import build_auth_context


class _Gateway:
    model_name = "wave2-test-model"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def invoke_clause_model(self, _request, *, cancel_event=None):  # noqa: ANN001
        self.calls += 1
        return ModelGatewayResult(self.payload, 1.0, 3, 2, False)


def _command(message: str) -> TurnCommand:
    return TurnCommand(
        command="preview", thread_id="wave2-thread", user_id="wave2-user", turn_id="wave2-turn",
        message_id="wave2-message", idempotency_key="wave2-key", raw_message=message,
    )


def _proposal(message: str, *, capability: str, entities: list[dict] | None = None, **clause) -> dict:
    return {
        "schema_version": "semantic_turn_proposal.v1",
        "entities": entities or [],
        "clauses": [{
            "clause_index": 0, "text": message, "start": 0, "end": len(message),
            "capability": capability, "confidence": 0.91, **clause,
        }],
        "ambiguities": [],
    }


async def _resolve(message: str, payload: dict):
    parser = CurrentUtteranceParser(enable_fallback=False)
    gateway = _Gateway(payload)
    service = SemanticResolutionService(parser=parser, gateway_factory=lambda _semaphore: gateway, mode="primary")
    result = await ConversationTurnCoordinator(parser=parser, semantic_service=service).preview_turn_async(
        _command(message), auth_context=build_auth_context(user_id="wave2-user", role="admin")
    )
    return result, gateway


@pytest.mark.asyncio
async def test_model_can_correct_a_grounded_deterministic_capability() -> None:
    message = "查询 G120电机1 当前状态"
    result, gateway = await _resolve(message, _proposal(message, capability="diagnose_fault"))

    assert gateway.calls == 1
    assert [goal.capability for goal in result.request.goals] == ["diagnose_fault"]
    decision = next(item for item in _semantic(result) if item["field"] == "clauses[0].capability")
    assert decision == {
        "field": "clauses[0].capability", "decision": "ACCEPT", "value": "diagnose_fault",
        "reason_code": "model_corrected_deterministic_capability",
    }


@pytest.mark.asyncio
async def test_model_alias_is_accepted_only_after_asset_registry_resolution() -> None:
    message = "请诊断二号设备"
    start = message.index("二号设备")
    result, _ = await _resolve(message, _proposal(
        message, capability="diagnose_fault", entities=[{
            "kind": "device_reference", "text": "二号设备", "start": start, "end": start + len("二号设备"),
            "normalized_candidate": "G120电机2", "confidence": 0.94,
        }], entity_indexes=[0],
    ))

    assert result.request.goals[0].resolved_slots == {"device": "G120电机2"}
    assert any(item["reason_code"] == "verified_model_entity" for item in _semantic(result))


@pytest.mark.asyncio
async def test_unknown_model_asset_requires_clarification_and_never_binds() -> None:
    message = "诊断九号设备"
    start = message.index("九号设备")
    result, _ = await _resolve(message, _proposal(
        message, capability="diagnose_fault", entities=[{
            "kind": "device_reference", "text": "九号设备", "start": start, "end": start + len("九号设备"),
            "normalized_candidate": "G120电机9", "confidence": 0.94,
        }], entity_indexes=[0],
    ))

    assert result.request.goals[0].missing_slots == ["device"]
    assert result.pending_transition.action == "create_waiting"
    assert any(item["decision"] == "CLARIFY" and item["reason_code"] == "unknown_asset_alias" for item in _semantic(result))


@pytest.mark.asyncio
async def test_high_risk_capability_conflict_is_clarified_not_promoted() -> None:
    message = "查询 G120电机1 当前状态"
    result, _ = await _resolve(message, _proposal(message, capability="dispatch_workorder"))

    assert [goal.capability for goal in result.request.goals] == ["check_runtime_status"]
    assert any(item["decision"] == "CLARIFY" and item["reason_code"] == "high_risk_capability_conflict" for item in _semantic(result))


@pytest.mark.asyncio
async def test_model_cannot_reverse_a_grounded_negation() -> None:
    message = "不要生成报告"
    result, _ = await _resolve(message, _proposal(message, capability="generate_report", requested=True, negated=False))

    assert result.request.goals == []
    assert any(item["decision"] == "REJECT" and item["reason_code"] == "deterministic_negation_conflict" for item in _semantic(result))


def test_capability_specs_are_the_canonical_allowlist_and_slot_source() -> None:
    assert CAPABILITY_SPECS["diagnose_fault"].required_slots == ("device",)
    assert CAPABILITY_SPECS["explain_fault_code"].required_slots == ("fault_code",)
    assert CAPABILITY_SPECS["create_workorder_draft"].approval_required is True


@pytest.mark.asyncio
async def test_model_sequence_projects_to_canonical_goal_dependencies() -> None:
    message = "查询 G120电机1 当前状态，然后生成报告"
    second = message.index("然后生成报告")
    payload = {
        "schema_version": "semantic_turn_proposal.v1", "entities": [], "ambiguities": [],
        "clauses": [
            {"clause_index": 0, "text": message[:second - 1], "start": 0, "end": second - 1,
             "capability": "check_runtime_status", "confidence": 0.9, "sequence_index": 0},
            {"clause_index": 1, "text": message[second:], "start": second, "end": len(message),
             "capability": "generate_report", "confidence": 0.9, "sequence_index": 1},
        ],
    }
    result, _ = await _resolve(message, payload)

    assert [goal.capability for goal in result.request.goals] == ["check_runtime_status", "generate_report"]
    assert result.request.goals[1].dependencies == [result.request.goals[0].goal_id]
    assert any(item["reason_code"] == "sequence_implies_prior_clause_dependency" for item in _semantic(result))


def _semantic(result) -> list[dict]:  # noqa: ANN001
    return next(event.detail["semantic"]["field_decisions"] for event in result.events if event.event_type == "intent_resolution")
