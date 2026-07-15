from __future__ import annotations

import json

import pytest

from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator, CurrentUtteranceParser
from fault_diagnosis.agent.canonical_turn.intent_shadow_service import IntentShadowService
from fault_diagnosis.agent.canonical_turn.model_clause_parser import ModelClauseCandidate
from fault_diagnosis.agent.canonical_turn.semantic_fallback import IntentSemanticMerger
from fault_diagnosis.domain.canonical_turn import TurnCommand
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform import settings


class FakeModel:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.requests = []

    def parse(self, request):  # noqa: ANN001
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.payload


def payload(text: str, capability: str | None, *, source_kind=None, refs=None, metadata=None):  # noqa: ANN001
    clause_text = text.rstrip("。！？?!")
    return {
        "schema_version": "model_clause_parse.v1",
        "clauses": [{
            "clause_index": 0, "text": clause_text, "start": 0, "end": len(clause_text),
            "action": ({"capability": capability, "confidence": 0.37,
                        "entity_refs": refs or [], "inferred": False} if capability else None),
            "source": ({"source_kind": source_kind, "entity_refs": refs or [],
                        "relation": "input_to_action"} if source_kind else None),
            "slot": {}, "linker": None, "shadow_metadata": metadata or {},
        }],
    }


def parse_with(text: str, capability: str, **kwargs):  # noqa: ANN001
    model = FakeModel(payload(text, capability, **kwargs))
    return CurrentUtteranceParser(model=model, enable_fallback=True).parse(text), model


def command(text: str) -> TurnCommand:
    return TurnCommand(
        thread_id="thread-fallback", user_id="user-fallback", turn_id=f"turn:{text}",
        message_id=f"message:{text}", idempotency_key=f"key:{text}", raw_message=text,
    )


def candidate(text: str, capability: str, *, metadata=None) -> ModelClauseCandidate:  # noqa: ANN001
    return ModelClauseCandidate.model_validate(payload(text, capability, metadata=metadata)["clauses"][0])


def test_fallback_off_is_identical_and_does_not_call_model() -> None:
    text = "给我整理出来。"
    baseline = CurrentUtteranceParser().parse(text)
    model = FakeModel(payload(text, "generate_report"))
    disabled = CurrentUtteranceParser(model=model, enable_fallback=False).parse(text)
    assert disabled == baseline
    assert model.requests == []


@pytest.mark.parametrize("text", ["查询 J1 当前状态", "帮我写首诗。", "设备1和设备2，它有没有故障？", "它。"])
def test_sufficient_irrelevant_and_context_ambiguous_messages_do_not_call_model(text: str) -> None:
    model = FakeModel(payload(text, "diagnose_fault"))
    CurrentUtteranceParser(model=model, enable_fallback=True).parse(text)
    assert model.requests == []


@pytest.mark.parametrize(
    ("text", "capability", "reason"),
    [
        ("这个看着不太正常。", "diagnose_fault", "unresolved_action"),
        ("继续处理。", "generate_report", "prior_result_without_action"),
    ],
)
def test_unresolved_and_prior_result_fallback_call_once(text: str, capability: str, reason: str) -> None:
    parsed, model = parse_with(text, capability, source_kind="prior_result")
    assert len(model.requests) == 1
    assert reason in parsed.intent_resolution.fallback_reasons
    assert [item.action.capability for item in parsed.clauses if item.action] == [capability]


@pytest.mark.parametrize(
    ("error", "status"),
    [(TimeoutError("slow"), "model_timeout"), (ValueError("not-json"), "schema_invalid")],
)
def test_timeout_and_invalid_json_fall_back_without_losing_deterministic_parse(error, status) -> None:  # noqa: ANN001
    model = FakeModel(error=error)
    parsed = CurrentUtteranceParser(model=model, enable_fallback=True).parse("帮我处理一下")
    assert parsed.clauses == []
    assert parsed.intent_resolution.mode == "deterministic_after_llm_failure"
    assert parsed.intent_resolution.model_status == status
    assert len(model.requests) == 1


@pytest.mark.parametrize(
    "bad_payload",
    [
        payload("帮我处理一下", "execute_sql"),
        payload("G120电机1 帮我处理一下", "diagnose_fault", refs=["ent_missing"]),
    ],
)
def test_unknown_capability_and_created_entity_are_rejected(bad_payload) -> None:  # noqa: ANN001
    text = bad_payload["clauses"][0]["text"]
    parsed = CurrentUtteranceParser(model=FakeModel(bad_payload), enable_fallback=True).parse(text)
    assert parsed.intent_resolution.mode == "deterministic_after_llm_failure"
    assert parsed.clauses == CurrentUtteranceParser().parse(text).clauses


def test_low_risk_ambiguity_cannot_be_promoted_to_dispatch() -> None:
    text = "这情况是不是得安排人处理一下？"
    parsed, _ = parse_with(text, "dispatch_workorder")
    assert parsed.clauses == []
    assert parsed.intent_resolution.model_status == "validation_failed"


@pytest.mark.parametrize(
    ("text", "model_capability", "field"),
    [
        ("生成报告", "diagnose_fault", "capability"),
        ("不要生成报告", "generate_report", "requested"),
        ("正式派发工单", "create_workorder_draft", "capability"),
    ],
)
def test_merger_rejects_capability_override_negation_reversal_and_dispatch_downgrade(
    text: str, model_capability: str, field: str,
) -> None:
    deterministic = CurrentUtteranceParser().parse(text).clauses
    metadata = {"requested": True, "negated": False}
    merged, accepted, rejected = IntentSemanticMerger().merge(
        text, [], deterministic, [candidate(text, model_capability, metadata=metadata)]
    )
    assert field in rejected
    assert [(item.action.capability, item.modality.negated) for item in merged if item.action] == [
        (deterministic[0].action.capability, deterministic[0].modality.negated)
    ]
    assert "capability" not in accepted


def test_missing_capability_and_prior_source_are_accepted_but_entities_stay_deterministic() -> None:
    text = "给我整理出来。"
    parsed, model = parse_with(text, "generate_report", source_kind="prior_result")
    request = model.requests[0]
    assert request.fallback_reasons
    assert request.deterministic_clauses == ()
    assert request.allowed_source_kinds == ("prior_result", "current_message")
    clause = parsed.clauses[0]
    assert parsed.deterministic_confident is False and parsed.model_used is True
    assert clause.action.capability == "generate_report"
    assert clause.action.entity_refs == [] and clause.slot == {}
    assert clause.source.source_kind == "prior_result"
    assert {"capability", "source_kind"}.issubset(parsed.intent_resolution.accepted_model_fields)


def test_controlled_condition_is_accepted_only_when_grounded_by_existing_predicate() -> None:
    text = "如果异常的话就处理一下。"
    parsed, _ = parse_with(
        text, "resolution_recommendation", metadata={"conditional": True, "requested": True},
    )
    clause = parsed.clauses[0]
    assert clause.modality.conditional is True
    assert clause.modality.condition_type == "if_abnormal"
    assert "conditional" in parsed.intent_resolution.accepted_model_fields


def test_context_binder_consumes_only_final_controlled_clause_and_deterministic_entity() -> None:
    text = "G120电机1 最近运转还顺利吗？"
    parser = CurrentUtteranceParser(
        model=FakeModel(payload(text, "check_runtime_status")), enable_fallback=True,
    )
    result = ConversationTurnCoordinator(parser=parser).preview_turn(
        command(text), auth_context=build_auth_context(user_id="user-fallback", role="admin"),
    )
    assert result.request.goals[0].resolved_slots == {"device": "G120电机1"}
    assert result.bound_turn.goal_bindings[0].asset_refs == ["G120电机1"]
    assert result.request.goals[0].provenance.parser_source == "model"


def test_fallback_high_risk_action_still_uses_existing_authorization() -> None:
    text = "赶紧安排人过去修。"
    parser = CurrentUtteranceParser(model=FakeModel(payload(text, "dispatch_workorder")), enable_fallback=True)
    result = ConversationTurnCoordinator(parser=parser).preview_turn(
        command(text), auth_context=build_auth_context(user_id="user-fallback", role="guest"),
    )
    assert result.request.goals[0].capability == "dispatch_workorder"
    assert result.authorization[0].status == "denied"


def test_shadow_reuses_fallback_call_and_trace_contains_no_raw_model_material(monkeypatch) -> None:
    text = "给我整理出来。"
    model = FakeModel(payload(text, "generate_report", source_kind="prior_result"))
    monkeypatch.setattr(settings, "ENABLE_LLM_INTENT_FALLBACK", True)
    monkeypatch.setattr(settings, "ENABLE_LLM_INTENT_SHADOW", True)
    parser = CurrentUtteranceParser(model=model, enable_fallback=True)
    result = ConversationTurnCoordinator(parser=parser).preview_turn(command(text))
    summary = IntentShadowService(
        model_factory=lambda: pytest.fail("shadow must reuse the fallback result")
    ).evaluate_current_message(result.request.current_parse)
    assert len(model.requests) == 1
    assert summary.status == "completed"
    trace = next(item.detail for item in result.events if item.event_type == "intent_resolution")
    serialized = json.dumps(trace, ensure_ascii=False)
    assert not {"prompt", "raw_response", "reasoning", "api_key"}.intersection(trace)
    assert text not in serialized


def test_planner_and_runtime_contract_receive_no_model_payload() -> None:
    parsed, _ = parse_with("给我整理出来。", "generate_report", source_kind="prior_result")
    serialized = parsed.model_dump(mode="json")
    assert not {"model_payload", "raw_response", "prompt", "artifact_ref", "candidate_id"}.intersection(serialized)
