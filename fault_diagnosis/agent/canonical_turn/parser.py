"""Deterministic-first orchestration for the isolated canonical-turn preview."""

from __future__ import annotations

from fault_diagnosis.agent.canonical_turn.clause_parser import DeterministicClauseParser
from fault_diagnosis.agent.canonical_turn.entity_extractor import DeterministicEntityExtractor
from fault_diagnosis.agent.canonical_turn.model_clause_parser import (
    ClauseModelRequest,
    StructuredClauseModel,
)
from fault_diagnosis.agent.canonical_turn.semantic_fallback import ControlledIntentFallback
from fault_diagnosis.domain.canonical_turn import CurrentUtteranceParse, IntentResolutionMetadata
from fault_diagnosis.platform import settings


class CurrentUtteranceParser:
    """Parse one utterance without consulting conversation or production frames."""

    def __init__(
        self, *, model: StructuredClauseModel | None = None, model_factory=None,
        enable_fallback: bool | None = None,
    ) -> None:
        self._fallback = ControlledIntentFallback(model=model, model_factory=model_factory)
        self._enable_fallback = settings.ENABLE_LLM_INTENT_FALLBACK if enable_fallback is None else enable_fallback
        self._entity_extractor = DeterministicEntityExtractor()
        self._clause_parser = DeterministicClauseParser()

    def parse(self, text: str) -> CurrentUtteranceParse:
        raw_text = str(text or "")
        entities = self._entity_extractor.extract(raw_text)
        clauses = self._clause_parser.parse(raw_text, entities)
        clauses, resolution = self._fallback.resolve(raw_text, entities, clauses) if self._enable_fallback else (
            clauses,
            IntentResolutionMetadata(
                deterministic_capabilities=[clause.action.capability for clause in clauses if clause.action]
            ),
        )
        confident = any(clause.action for clause in clauses) or bool(entities)
        deterministic_confident = bool(resolution.deterministic_capabilities) or bool(entities)
        model_used = resolution.mode == "llm_fallback"
        model_rejection_reason = (
            resolution.model_status if resolution.fallback_attempted and resolution.model_status != "completed" else None
        )

        clarification_needs: list[str] = []
        if not confident:
            clarification_needs.append("action_or_reference_not_determined")
        if resolution.fallback_attempted and model_rejection_reason and not any(clause.action for clause in clauses):
            clarification_needs.append("structured_model_failed_without_high_confidence_action")

        return CurrentUtteranceParse(
            raw_text=raw_text,
            entities=entities,
            clauses=clauses,
            clarification_needs=list(dict.fromkeys(clarification_needs)),
            deterministic_confident=deterministic_confident,
            model_used=model_used,
            model_rejection_reason=model_rejection_reason,
            intent_resolution=resolution,
        )


__all__ = ["ClauseModelRequest", "CurrentUtteranceParser", "StructuredClauseModel"]
