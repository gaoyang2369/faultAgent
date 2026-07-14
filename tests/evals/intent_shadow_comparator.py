"""Evaluation-only clause comparison helpers for the Phase 2A Gold suite."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from fault_diagnosis.agent.canonical_turn.model_clause_parser import (
    ModelClauseShadowEnvelope,
    ShadowClauseMetadata,
)
from fault_diagnosis.domain.canonical_turn import StructuredClause


@dataclass(frozen=True)
class EvaluationDifference:
    dimension: str
    clause_index: int | None = None
    deterministic_value: Any = None
    model_value: Any = None


@dataclass(frozen=True)
class IntentShadowEvaluationResult:
    differences: tuple[EvaluationDifference, ...]
    exact_match: bool
    capability_match: bool
    clause_boundary_match: bool
    entity_reference_match: bool
    source_relation_match: bool
    negation_relation_match: bool
    condition_relation_match: bool
    sequence_relation_match: bool
    dependency_relation_match: bool
    unsupported_model_capabilities: tuple[str, ...]


_NEGATION = re.compile(r"(?:不要|不需要|先别|无需|不是要|别再|不必|禁止)")
_CONDITION = re.compile(r"(?:如果|若|有.+?的话|确认.+?后|确实.+?再|之后再)")
_SEQUENCE = re.compile(r"(?:先|然后|再|最后|接着)")


def infer_shadow_metadata(
    clauses: list[StructuredClause] | tuple[StructuredClause, ...],
) -> tuple[ShadowClauseMetadata, ...]:
    has_sequence = any(_SEQUENCE.search(clause.text) for clause in clauses)
    result = []
    for index, clause in enumerate(clauses):
        negated = bool(_NEGATION.search(clause.text))
        conditional = bool(_CONDITION.search(clause.text))
        result.append(ShadowClauseMetadata(
            requested=clause.action is not None and not negated,
            negated=negated,
            conditional=conditional,
            sequence_index=index if has_sequence else None,
            depends_on=[index - 1] if index > 0 and (conditional or _SEQUENCE.search(clause.text)) else [],
        ))
    return tuple(result)


class IntentShadowComparator:
    def compare(
        self,
        deterministic: list[StructuredClause],
        model: ModelClauseShadowEnvelope,
        **_: Any,
    ) -> IntentShadowEvaluationResult:
        det_meta = infer_shadow_metadata(deterministic)
        differences: list[EvaluationDifference] = []

        def record(dimension: str, index: int | None, left: Any, right: Any) -> None:
            if left != right:
                differences.append(EvaluationDifference(dimension, index, left, right))

        record("clause_count", None, len(deterministic), len(model.clauses))
        for index, (left, right) in enumerate(zip(deterministic, model.clauses)):
            record("clause_boundary", index, (left.text, left.start, left.end), (right.text, right.start, right.end))
            record("capability", index, _capability(left), _capability(right))
            record("entity_reference", index, _entity_refs(left), _entity_refs(right))
            record("source_relation", index, _source_kind(left), _source_kind(right))
            record(
                "negation", index,
                (det_meta[index].negated, det_meta[index].requested),
                (right.shadow_metadata.negated, right.shadow_metadata.requested),
            )
            record("condition", index, det_meta[index].conditional, right.shadow_metadata.conditional)
            record("sequence", index, det_meta[index].sequence_index, right.shadow_metadata.sequence_index)
            record("dependency", index, det_meta[index].depends_on, right.shadow_metadata.depends_on)
        for capability in model.unsupported_model_capabilities:
            differences.append(EvaluationDifference("unsupported_output", model_value=capability))
        dimensions = {item.dimension for item in differences}
        def matched(dimension: str) -> bool:
            return dimension not in dimensions and "clause_count" not in dimensions
        return IntentShadowEvaluationResult(
            differences=tuple(differences),
            exact_match=not dimensions,
            capability_match=matched("capability"),
            clause_boundary_match=matched("clause_boundary"),
            entity_reference_match=matched("entity_reference"),
            source_relation_match=matched("source_relation"),
            negation_relation_match=matched("negation"),
            condition_relation_match=matched("condition"),
            sequence_relation_match=matched("sequence"),
            dependency_relation_match=matched("dependency"),
            unsupported_model_capabilities=model.unsupported_model_capabilities,
        )


def _capability(clause: Any) -> str | None:
    return clause.action.capability if clause.action else None


def _entity_refs(clause: Any) -> tuple[str, ...]:
    refs = set(clause.action.entity_refs if clause.action else [])
    refs.update(clause.source.entity_refs if clause.source else [])
    for values in clause.slot.values():
        refs.update(values)
    return tuple(sorted(refs))


def _source_kind(clause: Any) -> str:
    return clause.source.source_kind if clause.source else "current_message"
