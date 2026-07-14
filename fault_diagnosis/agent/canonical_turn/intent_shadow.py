"""Pure contracts, semantic comparison, and orchestration for intent shadowing."""

from __future__ import annotations

import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fault_diagnosis.agent.canonical_turn.clause_parser import DeterministicClauseParser
from fault_diagnosis.agent.canonical_turn.model_clause_parser import (
    ClauseModelRequest,
    ModelClauseParser,
    ModelClauseShadowMetadata,
    ModelClauseValidation,
    StructuredClauseModel,
)
from fault_diagnosis.domain.canonical_turn import CurrentUtteranceParse, StructuredClause


class IntentShadowDifference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: Literal[
        "clause_count", "clause_boundary", "capability", "entity_reference",
        "source_relation", "negation", "condition", "sequence", "dependency",
        "unsupported_output",
    ]
    clause_index: int | None = None
    deterministic_value: Any = None
    model_value: Any = None
    summary: str = ""


class IntentShadowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["intent_shadow.v1"] = "intent_shadow.v1"
    status: Literal[
        "disabled", "completed", "model_not_configured", "model_timeout",
        "model_error", "schema_invalid", "validation_failed",
    ]
    deterministic_clause_count: int = 0
    model_clause_count: int = 0
    deterministic_capabilities: list[str] = Field(default_factory=list)
    model_capabilities: list[str] = Field(default_factory=list)
    exact_match: bool = False
    capability_match: bool = False
    clause_boundary_match: bool = False
    entity_reference_match: bool = False
    source_relation_match: bool = False
    negation_relation_match: bool = False
    dependency_relation_match: bool = False
    condition_relation_match: bool = False
    sequence_relation_match: bool = False
    differences: list[IntentShadowDifference] = Field(default_factory=list)
    unsupported_model_capabilities: list[str] = Field(default_factory=list)
    model_name: str = ""
    model_config_source: str = ""
    duration_ms: float = 0
    fallback_reason: str = ""
    schema_valid: bool = False
    validation_passed: bool = False

    def safe_plan_summary(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"fallback_reason"})


_NEGATION = re.compile(r"(?:不要|不需要|先别|无需|不是要|别再|不必|禁止)")
_CONDITION = re.compile(r"(?:如果|若|有.+?的话|确认.+?后|确实.+?再|之后再)")
_SEQUENCE = re.compile(r"(?:先|然后|再|最后|接着)")


def infer_shadow_metadata(clauses: list[StructuredClause] | tuple[StructuredClause, ...]) -> tuple[ModelClauseShadowMetadata, ...]:
    has_sequence = any(_SEQUENCE.search(clause.text) for clause in clauses)
    result: list[ModelClauseShadowMetadata] = []
    for index, clause in enumerate(clauses):
        negated = bool(_NEGATION.search(clause.text))
        conditional = bool(_CONDITION.search(clause.text))
        depends = [index - 1] if index > 0 and (conditional or _SEQUENCE.search(clause.text)) else []
        result.append(
            ModelClauseShadowMetadata(
                requested=clause.action is not None and not negated,
                negated=negated,
                conditional=conditional,
                sequence_index=index if has_sequence else None,
                depends_on=depends,
                relation="negated_action" if negated else "conditional_action" if conditional else "requested_action",
            )
        )
    return tuple(result)


class IntentShadowComparator:
    """Compare representations without deciding which parser is correct."""

    def compare(
        self,
        deterministic: list[StructuredClause],
        model: ModelClauseValidation,
        *,
        model_name: str,
        model_config_source: str,
        duration_ms: float,
    ) -> IntentShadowResult:
        model_clauses = list(model.clauses)
        det_meta = infer_shadow_metadata(deterministic)
        model_meta = model.metadata
        differences: list[IntentShadowDifference] = []
        det_caps = [item.action.capability for item in deterministic if item.action]
        model_caps = [item.action.capability for item in model_clauses if item.action]

        def record(dimension: str, index: int | None, left: Any, right: Any) -> None:
            if left != right:
                differences.append(IntentShadowDifference(
                    dimension=dimension, clause_index=index,
                    deterministic_value=left, model_value=right,
                    summary=f"{dimension} differs" + (f" at clause {index}" if index is not None else ""),
                ))

        record("clause_count", None, len(deterministic), len(model_clauses))
        for index in range(max(len(deterministic), len(model_clauses))):
            left = deterministic[index] if index < len(deterministic) else None
            right = model_clauses[index] if index < len(model_clauses) else None
            if left is None or right is None:
                continue
            record("clause_boundary", index, _boundary(left), _boundary(right))
            record("capability", index, _capability(left), _capability(right))
            record("entity_reference", index, _entity_refs(left), _entity_refs(right))
            record("source_relation", index, _source_kind(left), _source_kind(right))
            record(
                "negation", index,
                (det_meta[index].negated, det_meta[index].requested),
                (model_meta[index].negated, model_meta[index].requested),
            )
            record("condition", index, det_meta[index].conditional, model_meta[index].conditional)
            record("sequence", index, det_meta[index].sequence_index, model_meta[index].sequence_index)
            record("dependency", index, det_meta[index].depends_on, model_meta[index].depends_on)
        for capability in model.unsupported_model_capabilities:
            differences.append(IntentShadowDifference(
                dimension="unsupported_output", model_value=capability,
                summary="allowlisted model capability lacks deterministic action evidence",
            ))
        dimensions = {item.dimension for item in differences}
        return IntentShadowResult(
            status="completed",
            deterministic_clause_count=len(deterministic), model_clause_count=len(model_clauses),
            deterministic_capabilities=det_caps, model_capabilities=model_caps,
            exact_match=not dimensions,
            capability_match="capability" not in dimensions and "clause_count" not in dimensions,
            clause_boundary_match="clause_boundary" not in dimensions and "clause_count" not in dimensions,
            entity_reference_match="entity_reference" not in dimensions and "clause_count" not in dimensions,
            source_relation_match="source_relation" not in dimensions and "clause_count" not in dimensions,
            negation_relation_match="negation" not in dimensions and "clause_count" not in dimensions,
            condition_relation_match="condition" not in dimensions and "clause_count" not in dimensions,
            sequence_relation_match="sequence" not in dimensions and "clause_count" not in dimensions,
            dependency_relation_match="dependency" not in dimensions and "clause_count" not in dimensions,
            differences=differences, unsupported_model_capabilities=list(model.unsupported_model_capabilities),
            model_name=model_name, model_config_source=model_config_source, duration_ms=duration_ms,
            schema_valid=True, validation_passed=True,
        )


class IntentShadowRunner:
    def __init__(self, *, model: StructuredClauseModel, model_name: str, model_config_source: str) -> None:
        self._model = model
        self._model_name = model_name
        self._model_config_source = model_config_source
        self._validator = ModelClauseParser()
        self._deterministic_parser = DeterministicClauseParser()

    def run(self, current_parse: CurrentUtteranceParse) -> IntentShadowResult:
        started = time.perf_counter()
        try:
            payload = self._model.parse(ClauseModelRequest(
                text=current_parse.raw_text,
                deterministic_entities=tuple(item.model_dump(mode="json") for item in current_parse.entities),
            ))
        except TimeoutError as exc:
            return self._failure("model_timeout", current_parse, started, exc)
        except (ValueError, TypeError) as exc:
            return self._failure("schema_invalid", current_parse, started, exc)
        except Exception as exc:  # provider failures must not escape the shadow boundary
            return self._failure("model_error", current_parse, started, exc)
        try:
            validated = self._validator.validate_for_shadow(
                current_parse.raw_text, current_parse.entities, payload,
                detect_action=self._deterministic_parser.detect_action,
            )
        except ValidationError as exc:
            return self._failure("schema_invalid", current_parse, started, exc)
        except Exception as exc:
            return self._failure("validation_failed", current_parse, started, exc, schema_valid=True)
        return IntentShadowComparator().compare(
            current_parse.clauses, validated, model_name=self._model_name,
            model_config_source=self._model_config_source,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    def _failure(self, status: str, parsed: CurrentUtteranceParse, started: float, exc: Exception, *, schema_valid: bool = False) -> IntentShadowResult:
        return IntentShadowResult(
            status=status, deterministic_clause_count=len(parsed.clauses),
            deterministic_capabilities=[item.action.capability for item in parsed.clauses if item.action],
            model_name=self._model_name, model_config_source=self._model_config_source,
            duration_ms=(time.perf_counter() - started) * 1000,
            fallback_reason=f"{type(exc).__name__}: {exc}", schema_valid=schema_valid,
        )


def disabled_intent_shadow() -> IntentShadowResult:
    return IntentShadowResult(status="disabled")


def _boundary(clause: StructuredClause) -> tuple[str, int, int]:
    normalized = re.sub(r"^[\s，,。；;！？!?]+|[\s，,。；;！？!?]+$", "", clause.text)
    return normalized, clause.start, clause.end


def _capability(clause: StructuredClause) -> str | None:
    return clause.action.capability if clause.action else None


def _entity_refs(clause: StructuredClause) -> tuple[str, ...]:
    refs = set(clause.action.entity_refs if clause.action else [])
    refs.update(clause.source.entity_refs if clause.source else [])
    for values in clause.slot.values():
        refs.update(values)
    return tuple(sorted(refs))


def _source_kind(clause: StructuredClause) -> str:
    return clause.source.source_kind if clause.source else "current_message"
