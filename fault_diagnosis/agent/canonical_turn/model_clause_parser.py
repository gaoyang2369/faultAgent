"""Structured-model contract and validation for current-message clauses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from fault_diagnosis.agent.canonical_turn.entity_extractor import compile_rule, rules_for
from fault_diagnosis.domain.canonical_turn import (
    CAPABILITY_ALLOWLIST,
    STRUCTURED_CLAUSE_CAPABILITY_ALLOWLIST,
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
    schema_version: str = "intent_shadow_request.v1"
    allowed_capabilities: tuple[str, ...] = tuple(sorted(STRUCTURED_CLAUSE_CAPABILITY_ALLOWLIST))
    allowed_source_kinds: tuple[str, ...] = ("artifact", "prior_result", "current_message")


class StructuredClauseModel(Protocol):
    def parse(self, request: ClauseModelRequest) -> dict[str, Any]:
        """Return a JSON-like payload conforming to ``model_clause_parse.v1``."""


class ModelClauseShadowMetadata(BaseModel):
    """Evaluation-only semantics; never copied into canonical production objects."""

    model_config = ConfigDict(extra="forbid")

    requested: bool = True
    negated: bool = False
    conditional: bool = False
    sequence_index: int | None = Field(default=None, ge=0)
    depends_on: list[int] = Field(default_factory=list)
    relation: str = "requested_action"
    unsupported_by_deterministic_action: bool = False


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
    shadow_metadata: ModelClauseShadowMetadata = Field(default_factory=ModelClauseShadowMetadata)


class _ModelClauseParse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["model_clause_parse.v1"]
    clauses: list[_ModelClause]


@dataclass(frozen=True)
class ModelClauseValidation:
    clauses: tuple[StructuredClause, ...]
    metadata: tuple[ModelClauseShadowMetadata, ...]
    unsupported_model_capabilities: tuple[str, ...]


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
        """Execution-compatible validation: deterministic action evidence is required."""

        return list(
            self._validate(text, entities, payload, detect_action=detect_action, mode="execution").clauses
        )

    def validate_for_shadow(
        self,
        text: str,
        entities: list[EntitySpan],
        payload: dict[str, Any],
        *,
        detect_action: Callable[[str, list[EntitySpan]], ClauseAction | None],
    ) -> ModelClauseValidation:
        """Hard-safe validation that retains novel allowlisted semantic candidates."""

        return self._validate(text, entities, payload, detect_action=detect_action, mode="shadow")

    def _validate(
        self,
        text: str,
        entities: list[EntitySpan],
        payload: dict[str, Any],
        *,
        detect_action: Callable[[str, list[EntitySpan]], ClauseAction | None],
        mode: Literal["execution", "shadow"],
    ) -> ModelClauseValidation:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if any(compile_rule(rule).search(serialized) for rule in self._forbidden_rules):
            raise ValueError("model payload contains forbidden execution or authorization content")
        parsed = _ModelClauseParse.model_validate(payload)
        entity_ids = {entity.entity_id for entity in entities}
        clauses: list[StructuredClause] = []
        metadata: list[ModelClauseShadowMetadata] = []
        unsupported: list[str] = []
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
            shadow_metadata = model_clause.shadow_metadata.model_copy(deep=True)
            if any(dependency >= expected_index for dependency in shadow_metadata.depends_on):
                raise ValueError("model clause dependencies must reference earlier clauses")
            if model_clause.action is not None:
                capability = model_clause.action.capability
                if capability not in STRUCTURED_CLAUSE_CAPABILITY_ALLOWLIST:
                    raise ValueError(f"capability is not allowlisted: {capability}")
                supported = deterministic_action is not None and deterministic_action.capability == capability
                if mode == "execution" and (not supported or capability not in CAPABILITY_ALLOWLIST):
                    raise ValueError("model action is not supported by deterministic action evidence")
                if not supported:
                    shadow_metadata.unsupported_by_deterministic_action = True
                    unsupported.append(capability)
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
            metadata.append(shadow_metadata)
        return ModelClauseValidation(
            clauses=tuple(clauses),
            metadata=tuple(metadata),
            unsupported_model_capabilities=tuple(dict.fromkeys(unsupported)),
        )
