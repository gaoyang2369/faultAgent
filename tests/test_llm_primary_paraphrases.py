"""Primary semantic resolution is the authority for ordinary wording variants."""

from __future__ import annotations

import pytest

from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator, CurrentUtteranceParser
from fault_diagnosis.agent.semantics import SemanticResolutionService
from fault_diagnosis.agent.semantics.model_gateway import ModelGatewayResult
from fault_diagnosis.domain.canonical_turn import TurnCommand
from fault_diagnosis.domain.security.permissions import build_auth_context


class _Gateway:
    model_name = "primary-paraphrase-test"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def invoke_clause_model(self, _request, *, cancel_event=None):  # noqa: ANN001
        self.calls += 1
        return ModelGatewayResult(self.payload, 1.0, 1, 1, False)


def _command(message: str) -> TurnCommand:
    return TurnCommand(
        command="preview", thread_id="primary-paraphrase", user_id="semantic-admin",
        turn_id=message, message_id=message, idempotency_key=message, raw_message=message,
    )


async def _preview(message: str, payload: dict):
    parser = CurrentUtteranceParser()
    gateway = _Gateway(payload)
    result = await ConversationTurnCoordinator(
        parser=parser,
        semantic_service=SemanticResolutionService(
            parser=parser, gateway_factory=lambda _semaphore: gateway, mode="primary",
        ),
    ).preview_turn_async(_command(message), auth_context=build_auth_context(user_id="semantic-admin", role="admin"))
    return result, gateway


def _payload(message: str, capability: str, *, entity_indexes: list[int] | None = None, entities: list[dict] | None = None) -> dict:
    return {
        "schema_version": "semantic_turn_proposal.v1",
        "entities": entities or [],
        "clauses": [{
            "clause_index": 0, "text": message, "start": 0, "end": len(message),
            "capability": capability, "confidence": 0.93, "entity_indexes": entity_indexes or [],
        }],
        "ambiguities": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "capability", "entity_text", "entity_kind", "normalized"),
    [
        ("把 G120电机1 的近况调出来", "check_runtime_status", "G120电机1", "device_reference", "G120电机1"),
        ("我想看看 G120电机1 这会儿运转得怎样", "check_runtime_status", "G120电机1", "device_reference", "G120电机1"),
        ("替我找出 G120电机1 的异常根源", "diagnose_fault", "G120电机1", "device_reference", "G120电机1"),
        ("G120电机1 到底哪里不对劲，查清缘由", "diagnose_fault", "G120电机1", "device_reference", "G120电机1"),
        ("帮忙解读 A07089", "explain_fault_code", "A07089", "fault_code", "A07089"),
        ("A07089 代表哪类问题", "explain_fault_code", "A07089", "fault_code", "A07089"),
        ("针对 G120电机1 给个处置方案", "resolution_recommendation", "G120电机1", "device_reference", "G120电机1"),
        ("G120电机1 下一步该怎么应对", "resolution_recommendation", "G120电机1", "device_reference", "G120电机1"),
        ("为 G120电机1 汇总成一份运行材料", "generate_report", "G120电机1", "device_reference", "G120电机1"),
        ("把 G120电机1 的情况整理成一份可交付文档", "generate_report", "G120电机1", "device_reference", "G120电机1"),
        ("G120电机1 这事值得报修吗", "evaluate_workorder_need", "G120电机1", "device_reference", "G120电机1"),
        ("G120电机1 这种情况要不要让维修流程介入", "evaluate_workorder_need", "G120电机1", "device_reference", "G120电机1"),
        ("给 G120电机1 预填一张维修单", "create_workorder_draft", "G120电机1", "device_reference", "G120电机1"),
        ("先替 G120电机1 准备一张待确认的处理单", "create_workorder_draft", "G120电机1", "device_reference", "G120电机1"),
        ("把现有维修单派给现场班组", "dispatch_workorder", None, None, None),
        ("让待办单正式流转给值班人员", "dispatch_workorder", None, None, None),
    ],
)
async def test_primary_model_accepts_paraphrases_without_rule_keyword_coverage(
    message: str, capability: str, entity_text: str | None, entity_kind: str | None, normalized: str | None,
) -> None:
    entities = []
    indexes = []
    if entity_text and entity_kind:
        start = message.index(entity_text)
        entities = [{
            "kind": entity_kind, "text": entity_text, "start": start, "end": start + len(entity_text),
            "normalized_candidate": normalized, "confidence": 0.95,
        }]
        indexes = [0]
    result, gateway = await _preview(message, _payload(message, capability, entity_indexes=indexes, entities=entities))

    assert gateway.calls == 1
    assert [goal.capability for goal in result.request.goals] == [capability]
    assert result.request.current_parse.intent_resolution.mode == "llm_primary"


@pytest.mark.asyncio
async def test_primary_model_preserves_all_compound_goals_and_dependencies() -> None:
    message = "A07089 是什么意思？查询 G120电机1 最近一小时有没有相关异常，判断现在是否存在故障，并给出处理建议。"
    code_start = message.index("A07089")
    device_start = message.index("G120电机1")
    parts = [
        ("A07089 是什么意思", "explain_fault_code", [0], []),
        ("查询 G120电机1 最近一小时有没有相关异常", "check_runtime_status", [1, 2], []),
        ("判断现在是否存在故障", "diagnose_fault", [1], [0, 1]),
        ("并给出处理建议", "resolution_recommendation", [1], [2]),
    ]
    clauses = []
    for index, (text, capability, entity_indexes, depends_on) in enumerate(parts):
        start = message.index(text)
        clauses.append({
            "clause_index": index, "text": text, "start": start, "end": start + len(text),
            "capability": capability, "confidence": 0.95, "entity_indexes": entity_indexes,
            "sequence_index": index, "depends_on_clause_indexes": depends_on,
        })
    payload = {
        "schema_version": "semantic_turn_proposal.v1",
        "entities": [
            {"kind": "fault_code", "text": "A07089", "start": code_start, "end": code_start + 6,
             "normalized_candidate": "A07089", "confidence": 1.0},
            {"kind": "device_reference", "text": "G120电机1", "start": device_start, "end": device_start + len("G120电机1"),
             "normalized_candidate": "G120电机1", "confidence": 1.0},
            {"kind": "time_window", "text": "最近一小时", "start": message.index("最近一小时"), "end": message.index("最近一小时") + len("最近一小时"),
             "normalized_candidate": "最近一小时", "confidence": 1.0},
        ],
        "clauses": clauses, "ambiguities": [],
    }
    result, gateway = await _preview(message, payload)

    assert gateway.calls == 1
    goals = result.request.goals
    assert [goal.capability for goal in goals] == [
        "explain_fault_code", "check_runtime_status", "diagnose_fault", "resolution_recommendation",
    ]
    assert goals[2].dependencies == [goals[0].goal_id, goals[1].goal_id]
    assert goals[3].dependencies == [goals[2].goal_id]


@pytest.mark.asyncio
async def test_model_failure_uses_narrow_fallback_and_clarifies_unknown_business_wording() -> None:
    message = "把 G120电机1 的近况调出来"
    parser = CurrentUtteranceParser()
    service = SemanticResolutionService(parser=parser, gateway_factory=lambda _semaphore: _UnavailableGateway(), mode="primary")
    result = await ConversationTurnCoordinator(parser=parser, semantic_service=service).preview_turn_async(
        _command(message), auth_context=build_auth_context(user_id="semantic-admin", role="admin"),
    )

    assert result.request.goals == []
    semantic = next(event.detail["semantic"] for event in result.events if event.event_type == "intent_resolution")
    assert semantic["fallback"] is True
    assert semantic["status"] == "model_not_configured"


class _UnavailableGateway:
    model_name = "unavailable"

    async def invoke_clause_model(self, _request, *, cancel_event=None):  # noqa: ANN001
        raise RuntimeError("not_configured")
