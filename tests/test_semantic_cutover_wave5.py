"""Wave 5：旧语义权威删除后的单入口与 CapabilitySpec 合同。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fault_diagnosis.agent.canonical_turn import CurrentUtteranceParser
from fault_diagnosis.agent.semantics import SemanticResolutionService
from fault_diagnosis.agent.semantics.intent_interpreter import parse_semantic_turn_proposal
from fault_diagnosis.agent.semantics.model_gateway import ModelGatewayResult
from fault_diagnosis.agent.skills.registry import SkillRegistry
from fault_diagnosis.agent.skills.router import skill_for_capability
from fault_diagnosis.domain.canonical_turn import CAPABILITY_SPECS


class _Gateway:
    model_name = "wave5-shadow-model"

    def __init__(self) -> None:
        self.calls = 0

    async def invoke_clause_model(self, request, *, cancel_event=None):  # noqa: ANN001
        self.calls += 1
        return ModelGatewayResult(
            payload={
                "schema_version": "semantic_turn_proposal.v1",
                "entities": [],
                "ambiguities": [],
                "clauses": [{
                    "clause_index": 0, "text": request.text, "start": 0, "end": len(request.text),
                    "capability": "diagnose_fault", "confidence": 0.9,
                }],
            },
            latency_ms=1.0,
            input_tokens=1,
            output_tokens=1,
            concurrency_limited=False,
        )


@pytest.mark.asyncio
async def test_shadow_observes_the_single_semantic_call_without_replacing_canonical_parse() -> None:
    parser = CurrentUtteranceParser()
    gateway = _Gateway()
    service = SemanticResolutionService(parser=parser, gateway_factory=lambda _semaphore: gateway, mode="shadow")

    resolution = await service.resolve("查询 G120电机1 当前状态")

    assert gateway.calls == 1
    assert resolution.trace.mode == "shadow"
    assert resolution.trace.schema_name == "semantic_turn_proposal.v1"
    assert [item.action.capability for item in resolution.parsed.clauses if item.action] == ["check_runtime_status"]
    assert any(item.field == "clauses[0].capability" for item in resolution.field_decisions)


def test_capability_spec_is_the_skill_routing_and_skill_policy_authority() -> None:
    registry = SkillRegistry().discover()

    for capability, spec in CAPABILITY_SPECS.items():
        assert spec.skill in registry
        assert skill_for_capability(capability) == spec.skill
        assert set(spec.required_slots).issubset(registry[spec.skill].required_slots)
        assert set(spec.runtime_nodes).issubset(registry[spec.skill].allowed_nodes)


def test_legacy_sync_semantic_modules_are_absent_and_only_new_schema_is_accepted() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "fault_diagnosis/agent/understanding/intent_frame.py",
        "fault_diagnosis/agent/understanding/rewrite.py",
        "fault_diagnosis/agent/canonical_turn/semantic_fallback.py",
        "fault_diagnosis/agent/canonical_turn/intent_shadow_service.py",
        "fault_diagnosis/agent/canonical_turn/llm_structured_clause_model.py",
        "fault_diagnosis/agent/canonical_turn/model_clause_parser.py",
    ):
        assert not (root / relative).exists()
    with pytest.raises(ValueError, match="unsupported_semantic_schema"):
        parse_semantic_turn_proposal({"schema_version": "model_clause_parse.v1", "clauses": []})
