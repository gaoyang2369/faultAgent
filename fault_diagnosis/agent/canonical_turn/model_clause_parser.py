"""Shared hard validation for model-produced current-message clauses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from fault_diagnosis.agent.canonical_turn.entity_extractor import compile_rule, rules_for
from fault_diagnosis.domain.canonical_turn import (
    ALL_INTENT_CAPABILITIES,
    CANONICAL_CAPABILITIES,
    ClauseAction,
    ClauseSource,
    EntitySpan,
    StructuredClause,
)


@dataclass(frozen=True)
class ClauseModelRequest:
    text: str
    deterministic_entities: tuple[dict[str, Any], ...]
    deterministic_clauses: tuple[dict[str, Any], ...] = ()
    fallback_reasons: tuple[str, ...] = ()
    temperature: Literal[0] = 0
    response_schema: str = "model_clause_parse.v1"
    schema_version: str = "intent_shadow_request.v1"
    allowed_capabilities: tuple[str, ...] = tuple(sorted(ALL_INTENT_CAPABILITIES))
    allowed_source_kinds: tuple[str, ...] = ("artifact", "prior_result", "current_message")
    context_candidates: tuple[dict[str, Any], ...] = ()
    pending_summary: dict[str, Any] | None = None


class StructuredClauseModel(Protocol):
    def parse(self, request: ClauseModelRequest) -> dict[str, Any]:
        """Return a JSON-like payload conforming to ``model_clause_parse.v1``."""


class ShadowClauseMetadata(BaseModel):
    """Experimental semantics kept outside canonical domain contracts."""

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


class ModelClauseCandidate(BaseModel):
    """Validated model schema; never serialized as a canonical clause."""

    model_config = ConfigDict(extra="forbid")

    clause_index: int = Field(ge=0)
    text: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    action: _ModelAction | None = None
    source: _ModelSource | None = None
    slot: dict[str, list[str]] = Field(default_factory=dict)
    linker: str | None = None
    shadow_metadata: ShadowClauseMetadata = Field(default_factory=ShadowClauseMetadata)


class _ModelClauseParse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["model_clause_parse.v1"]
    clauses: list[ModelClauseCandidate]


@dataclass(frozen=True)
class ModelClauseShadowEnvelope:
    clauses: tuple[ModelClauseCandidate, ...]
    unsupported_model_capabilities: tuple[str, ...]


class ModelClauseParser:
    def __init__(self) -> None:
        self._forbidden_rules = rules_for("model_validation")

    def validate_for_shadow(
        self,
        text: str,
        entities: list[EntitySpan],
        payload: dict[str, Any],
        *,
        detect_action: Callable[[str, list[EntitySpan]], ClauseAction | None],
    ) -> ModelClauseShadowEnvelope:
        return self._validate_base(text, entities, payload, detect_action=detect_action)

    def validate_for_execution(
        self,
        text: str,
        entities: list[EntitySpan],
        payload: dict[str, Any],
        *,
        detect_action: Callable[[str, list[EntitySpan]], ClauseAction | None],
    ) -> list[StructuredClause]:
        envelope = self._validate_base(text, entities, payload, detect_action=detect_action)
        if envelope.unsupported_model_capabilities:
            raise ValueError("model action is not supported by deterministic action evidence")
        clauses: list[StructuredClause] = []
        for candidate in envelope.clauses:
            if candidate.action and candidate.action.capability not in CANONICAL_CAPABILITIES:
                raise ValueError("shadow-only capability cannot enter execution validation")
            clauses.append(StructuredClause(
                clause_index=candidate.clause_index,
                text=candidate.text,
                start=candidate.start,
                end=candidate.end,
                action=ClauseAction.model_validate(candidate.action.model_dump()) if candidate.action else None,
                source=ClauseSource.model_validate(candidate.source.model_dump()) if candidate.source else None,
                slot=candidate.slot,
                linker=candidate.linker,
                parser_source="model",
            ))
        return clauses

    def _validate_base(
        self,
        text: str,
        entities: list[EntitySpan],
        payload: dict[str, Any],
        *,
        detect_action: Callable[[str, list[EntitySpan]], ClauseAction | None],
    ) -> ModelClauseShadowEnvelope:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if any(compile_rule(rule).search(serialized) for rule in self._forbidden_rules):
            raise ValueError("model payload contains forbidden execution or authorization content")
        parsed = _ModelClauseParse.model_validate(payload)
        entity_ids = {entity.entity_id for entity in entities}
        candidates: list[ModelClauseCandidate] = []
        unsupported: list[str] = []
        for expected_index, candidate in enumerate(parsed.clauses):
            if candidate.clause_index != expected_index:
                raise ValueError("model clause indexes must be contiguous and ordered")
            if candidate.end > len(text) or text[candidate.start : candidate.end] != candidate.text:
                raise ValueError("model clause span is outside the current utterance")
            refs = set(candidate.action.entity_refs if candidate.action else [])
            refs.update(candidate.source.entity_refs if candidate.source else [])
            for slot_refs in candidate.slot.values():
                refs.update(slot_refs)
            if refs - entity_ids:
                raise ValueError("model clause references an entity not extracted deterministically")
            if any(dependency >= expected_index for dependency in candidate.shadow_metadata.depends_on):
                raise ValueError("model clause dependencies must reference earlier clauses")
            overlapping = [entity for entity in entities if entity.start < candidate.end and entity.end > candidate.start]
            deterministic_action = detect_action(candidate.text, overlapping)
            supported = not candidate.action or (
                deterministic_action is not None
                and deterministic_action.capability == candidate.action.capability
            )
            if candidate.action and candidate.action.capability not in ALL_INTENT_CAPABILITIES:
                raise ValueError(f"capability is not allowlisted: {candidate.action.capability}")
            if candidate.action and not supported:
                unsupported.append(candidate.action.capability)
                candidate = candidate.model_copy(update={
                    "shadow_metadata": candidate.shadow_metadata.model_copy(
                        update={"unsupported_by_deterministic_action": True}
                    )
                })
            candidates.append(candidate)
        return ModelClauseShadowEnvelope(
            clauses=tuple(candidates),
            unsupported_model_capabilities=tuple(dict.fromkeys(unsupported)),
        )
