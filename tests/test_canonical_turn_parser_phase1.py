from __future__ import annotations

from itertools import product

import pytest
from pydantic import ValidationError

from fault_diagnosis.agent.canonical_turn import CurrentUtteranceParser
from fault_diagnosis.agent.canonical_turn.entity_extractor import compile_rule
from fault_diagnosis.agent.canonical_turn.rule_catalog import CAPABILITY_LEXICON, RULE_CATALOG
from fault_diagnosis.domain.canonical_turn import (
    CAPABILITY_ALLOWLIST,
    CanonicalGoal,
    ClauseAction,
    CurrentUtteranceParse,
    EntitySpan,
)
from fault_diagnosis.domain.canonical_turn.contracts import GoalProvenance

def test_rule_catalog_has_unique_auditable_metadata_and_valid_priorities() -> None:
    required = {
        "rule_id",
        "category",
        "pattern",
        "semantic_value",
        "priority",
        "confidence",
        "allowed_clause_roles",
    }
    rule_ids = [rule["rule_id"] for rule in RULE_CATALOG]

    assert len(rule_ids) == len(set(rule_ids))
    assert all(required.issubset(rule) for rule in RULE_CATALOG)
    assert all(isinstance(rule["priority"], int) and 0 <= rule["priority"] <= 1000 for rule in RULE_CATALOG)
    assert all(0.0 <= rule["confidence"] <= 1.0 for rule in RULE_CATALOG)


def test_rule_catalog_capabilities_and_source_roles_obey_contract_boundaries() -> None:
    rule_ids = {rule["rule_id"] for rule in RULE_CATALOG}
    action_rules = [rule for rule in RULE_CATALOG if rule["category"] == "action_predicate"]
    source_rules = [rule for rule in RULE_CATALOG if rule["category"] == "source_marker"]

    assert {rule["semantic_value"] for rule in action_rules}.issubset(CAPABILITY_ALLOWLIST)
    assert set(CAPABILITY_LEXICON).issubset(CAPABILITY_ALLOWLIST)
    assert all(set(ids).issubset(rule_ids) for ids in CAPABILITY_LEXICON.values())
    assert all("action" not in rule["allowed_clause_roles"] for rule in source_rules)


def test_rule_catalog_forbids_empty_patterns_and_sentence_literals() -> None:
    semantic_rules = [
        rule
        for rule in RULE_CATALOG
        if rule["category"] not in {"clause_boundary", "model_validation"}
    ]

    assert all(str(rule["pattern"]).strip() for rule in RULE_CATALOG)
    assert all(not set("。！？").intersection(str(rule["pattern"])) for rule in semantic_rules)
    assert all(
        any(marker in str(rule["pattern"]) for marker in ("(", "[", "\\", ".", "?", "|"))
        for rule in semantic_rules
    )


@pytest.mark.parametrize("rule", RULE_CATALOG, ids=lambda rule: rule["rule_id"])
def test_every_catalog_rule_has_an_independently_matching_example(rule) -> None:
    pattern = compile_rule(rule)
    example = str(rule["example"])
    matched = pattern.fullmatch(example) if rule.get("match_mode") == "fullmatch" else pattern.search(example)

    assert matched is not None


def test_deterministic_entities_preserve_raw_spans_and_reference_types() -> None:
    text = "不是G120电机1，是G120电机2；基于刚才的诊断结果查看最近一小时的 A07089 和 analysis:case-7"
    parsed = CurrentUtteranceParser().parse(text)

    kinds = {entity.kind for entity in parsed.entities}
    assert {
        "fault_code",
        "device_reference",
        "time_window",
        "artifact_reference",
        "source_reference",
        "correction_reference",
        "deictic_reference",
    }.issubset(kinds)
    assert all(text[item.start : item.end] == item.text for item in parsed.entities)
    assert {item.value for item in parsed.entities if item.kind == "device_reference"} == {
        "G120电机1",
        "G120电机2",
    }


def test_current_parse_rejects_out_of_bounds_span_and_unknown_entity_reference() -> None:
    with pytest.raises(ValidationError, match="invalid entity span"):
        CurrentUtteranceParse(
            raw_text="A07089",
            entities=[
                EntitySpan(
                    entity_id="bad",
                    kind="fault_code",
                    value="A07089",
                    start=0,
                    end=5,
                    text="A07089",
                )
            ],
        )

    parsed = CurrentUtteranceParser().parse("解释 A07089")
    clause = parsed.clauses[0].model_copy(deep=True)
    clause.action = clause.action.model_copy(update={"entity_refs": ["missing"]})
    with pytest.raises(ValidationError, match="unknown entities"):
        CurrentUtteranceParse(raw_text=parsed.raw_text, entities=parsed.entities, clauses=[clause])


def test_capability_allowlist_applies_to_model_and_canonical_goal() -> None:
    with pytest.raises(ValidationError, match="not allowlisted"):
        ClauseAction(capability="execute_sql")

    with pytest.raises(ValidationError, match="not allowlisted"):
        CanonicalGoal(
            goal_id="g1",
            capability="invoke_tool",
            origin="explicit",
            user_requested=True,
            user_visible=True,
            clause_index=0,
            provenance=GoalProvenance(parser_source="deterministic"),
        )


@pytest.mark.parametrize(
    "message,origin",
    [
        ("A07089", "inferred"),
        ("查询 A07089", "inferred"),
        ("A07089 是什么意思？", "explicit"),
        ("详细解释一下 A07089", "explicit"),
    ],
)
def test_fault_code_shapes_produce_explanation_action_without_device(message: str, origin: str) -> None:
    parsed = CurrentUtteranceParser().parse(message)

    assert [clause.action.capability for clause in parsed.clauses if clause.action] == ["explain_fault_code"]
    assert [clause.action.inferred for clause in parsed.clauses if clause.action] == [origin == "inferred"]
    assert not [item for item in parsed.entities if item.kind == "device_reference"]


def test_device_only_message_does_not_default_to_status_action() -> None:
    parsed = CurrentUtteranceParser().parse("G120电机2")

    assert [clause.action for clause in parsed.clauses if clause.action] == []
    assert [item.value for item in parsed.entities if item.kind == "device_reference"] == ["G120电机2"]


@pytest.mark.parametrize(
    "message",
    [
        f"{prep}{deictic}{source}{verb}{target}"
        for (prep, deictic, source), (verb, target) in product(
            (
                ("基于", "刚才的", "诊断结果"),
                ("根据", "上一轮的", "诊断结果"),
                ("使用", "前面的", "分析结果"),
                ("用", "这个", "诊断结果"),
            ),
            (("生成", "报告"), ("生成", "运行报告"), ("整理成", "报告")),
        )
    ],
)
def test_source_action_grammar_never_promotes_source_to_diagnosis(message: str) -> None:
    parsed = CurrentUtteranceParser().parse(message)
    action_clauses = [clause for clause in parsed.clauses if clause.action]

    assert [clause.action.capability for clause in action_clauses] == ["generate_report"]
    assert action_clauses[0].source is not None


@pytest.mark.parametrize("message", ["刚才的诊断结果", "上一轮分析结果", "前面的诊断结果"])
def test_source_only_clause_never_becomes_action_goal(message: str) -> None:
    parsed = CurrentUtteranceParser().parse(message)

    assert all(clause.action is None for clause in parsed.clauses)
    assert any(clause.source is not None for clause in parsed.clauses)
