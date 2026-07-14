"""Deterministic-first orchestration for the isolated canonical-turn preview."""

from __future__ import annotations

from pydantic import ValidationError

from fault_diagnosis.agent.canonical_turn.clause_parser import DeterministicClauseParser
from fault_diagnosis.agent.canonical_turn.entity_extractor import DeterministicEntityExtractor
from fault_diagnosis.agent.canonical_turn.model_clause_parser import (
    ClauseModelRequest,
    ModelClauseParser,
    StructuredClauseModel,
)
from fault_diagnosis.domain.canonical_turn import CurrentUtteranceParse


class CurrentUtteranceParser:
    """Parse one utterance without consulting conversation or production frames."""

    def __init__(self, *, model: StructuredClauseModel | None = None) -> None:
        self._model = model
        self._entity_extractor = DeterministicEntityExtractor()
        self._clause_parser = DeterministicClauseParser()
        self._model_clause_parser = ModelClauseParser()

    def parse(self, text: str) -> CurrentUtteranceParse:
        raw_text = str(text or "")
        entities = self._entity_extractor.extract(raw_text)
        clauses = self._clause_parser.parse(raw_text, entities)
        confident = any(clause.action for clause in clauses) or bool(entities)
        model_used = False
        model_rejection_reason: str | None = None

        if self._model is not None:
            try:
                payload = self._model.parse(
                    ClauseModelRequest(
                        text=raw_text,
                        deterministic_entities=tuple(entity.model_dump(mode="json") for entity in entities),
                    )
                )
                clauses = self._model_clause_parser.validate(
                    raw_text,
                    entities,
                    payload,
                    detect_action=self._clause_parser.detect_action,
                )
                model_used = True
                confident = any(clause.action for clause in clauses) or bool(entities)
            except (ValidationError, ValueError, TypeError, RuntimeError) as exc:
                model_rejection_reason = f"{type(exc).__name__}: {exc}"

        clarification_needs: list[str] = []
        if not confident:
            clarification_needs.append("action_or_reference_not_determined")
        if self._model is not None and model_rejection_reason and not any(clause.action for clause in clauses):
            clarification_needs.append("structured_model_failed_without_high_confidence_action")

        return CurrentUtteranceParse(
            raw_text=raw_text,
            entities=entities,
            clauses=clauses,
            clarification_needs=list(dict.fromkeys(clarification_needs)),
            deterministic_confident=confident,
            model_used=model_used,
            model_rejection_reason=model_rejection_reason,
        )


__all__ = ["ClauseModelRequest", "CurrentUtteranceParser", "StructuredClauseModel"]
