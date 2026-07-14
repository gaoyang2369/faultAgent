"""Optional structured-model boundary for current-utterance clauses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from fault_diagnosis.agent.canonical_turn.entity_extractor import compile_rule, rules_for
from fault_diagnosis.domain.canonical_turn import (
    ClauseAction,
    ClauseSource,
    EntitySpan,
    StructuredClause,
)


@dataclass(frozen=True)
class ClauseModelRequest:
    text: str
    deterministic_entities: tuple[dict[str, Any], ...]
    temperature: Literal[0] = 0
    response_schema: str = "model_clause_parse.v1"


class StructuredClauseModel(Protocol):
    def parse(self, request: ClauseModelRequest) -> dict[str, Any]:
        """Return a JSON-like payload conforming to ``model_clause_parse.v1``."""


class _ModelAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    confidence: float = Field(ge=0.0, le=1.0)
    entity_refs: list[str] = Field(default_factory=list)
    inferred: bool = False


class _ModelSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["artifact", "prior_result", "current_message"]
    entity_refs: list[str] = Field(default_factory=list)
    relation: str = "input_to_action"


class _ModelClause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_index: int = Field(ge=0)
    text: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    action: _ModelAction | None = None
    source: _ModelSource | None = None
    slot: dict[str, list[str]] = Field(default_factory=dict)
    linker: str | None = None


class _ModelClauseParse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["model_clause_parse.v1"]
    clauses: list[_ModelClause]


class ModelClauseParser:
    def __init__(self) -> None:
        self._forbidden_rules = rules_for("model_validation")

    def validate(
        self,
        text: str,
        entities: list[EntitySpan],
        payload: dict[str, Any],
        *,
        detect_action: Callable[[str, list[EntitySpan]], ClauseAction | None],
    ) -> list[StructuredClause]:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if any(compile_rule(rule).search(serialized) for rule in self._forbidden_rules):
            raise ValueError("model payload contains forbidden execution or authorization content")
        parsed = _ModelClauseParse.model_validate(payload)
        entity_ids = {entity.entity_id for entity in entities}
        clauses: list[StructuredClause] = []
        for expected_index, model_clause in enumerate(parsed.clauses):
            if model_clause.clause_index != expected_index:
                raise ValueError("model clause indexes must be contiguous and ordered")
            if model_clause.end > len(text) or text[model_clause.start : model_clause.end] != model_clause.text:
                raise ValueError("model clause span is outside the current utterance")
            refs = set(model_clause.action.entity_refs if model_clause.action else [])
            refs.update(model_clause.source.entity_refs if model_clause.source else [])
            for slot_refs in model_clause.slot.values():
                refs.update(slot_refs)
            if refs - entity_ids:
                raise ValueError("model clause references an entity not extracted deterministically")
            overlapping = [entity for entity in entities if entity.start < model_clause.end and entity.end > model_clause.start]
            deterministic_action = detect_action(model_clause.text, overlapping)
            if model_clause.action is not None:
                if deterministic_action is None or deterministic_action.capability != model_clause.action.capability:
                    raise ValueError("model action is not supported by deterministic action evidence")
            action = ClauseAction.model_validate(model_clause.action.model_dump()) if model_clause.action else None
            source = ClauseSource.model_validate(model_clause.source.model_dump()) if model_clause.source else None
            clauses.append(
                StructuredClause(
                    clause_index=model_clause.clause_index,
                    text=model_clause.text,
                    start=model_clause.start,
                    end=model_clause.end,
                    action=action,
                    source=source,
                    slot=model_clause.slot,
                    linker=model_clause.linker,
                    parser_source="model",
                )
            )
        return clauses
