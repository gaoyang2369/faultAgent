"""Minimal, non-authoritative intent Shadow service for /chat/plan."""

from __future__ import annotations

import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fault_diagnosis.agent.canonical_turn.clause_parser import DeterministicClauseParser
from fault_diagnosis.agent.canonical_turn.llm_structured_clause_model import build_intent_clause_model
from fault_diagnosis.agent.canonical_turn.model_clause_parser import ClauseModelRequest, ModelClauseParser
from fault_diagnosis.domain.canonical_turn import CurrentUtteranceParse
from fault_diagnosis.platform import settings


class IntentShadowRuntimeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "completed", "model_not_configured", "model_timeout", "model_error",
        "schema_invalid", "validation_failed",
    ]
    model_name: str = ""
    duration_ms: float = 0
    schema_valid: bool = False
    validation_passed: bool = False
    deterministic_capabilities: list[str] = Field(default_factory=list)
    model_capabilities: list[str] = Field(default_factory=list)
    capability_match: bool = False
    difference_dimensions: list[str] = Field(default_factory=list)


class IntentShadowService:
    def __init__(
        self,
        *,
        model_factory: Callable[[], tuple[Any, str]] | None = None,
    ) -> None:
        self._model_factory = model_factory
        self._validator = ModelClauseParser()
        self._deterministic_parser = DeterministicClauseParser()

    def evaluate_current_message(
        self,
        deterministic_parse: CurrentUtteranceParse,
    ) -> IntentShadowRuntimeSummary | None:
        if not settings.ENABLE_LLM_INTENT_SHADOW:
            return None
        started = time.perf_counter()
        deterministic_capabilities = [
            clause.action.capability
            for clause in deterministic_parse.clauses
            if clause.action
        ]
        try:
            model, model_name = (self._model_factory or build_intent_clause_model)()
        except Exception as exc:
            return self._failure(
                "model_not_configured" if "not_configured" in str(exc) else "model_error",
                started,
                deterministic_capabilities,
            )
        try:
            payload = model.parse(ClauseModelRequest(
                text=deterministic_parse.raw_text,
                deterministic_entities=tuple(
                    item.model_dump(mode="json") for item in deterministic_parse.entities
                ),
            ))
        except TimeoutError:
            return self._failure("model_timeout", started, deterministic_capabilities, model_name)
        except (ValueError, TypeError):
            return self._failure("schema_invalid", started, deterministic_capabilities, model_name)
        except Exception:
            return self._failure("model_error", started, deterministic_capabilities, model_name)
        try:
            envelope = self._validator.validate_for_shadow(
                deterministic_parse.raw_text,
                deterministic_parse.entities,
                payload,
                detect_action=self._deterministic_parser.detect_action,
            )
        except ValidationError:
            return self._failure("schema_invalid", started, deterministic_capabilities, model_name)
        except Exception:
            return self._failure(
                "validation_failed", started, deterministic_capabilities, model_name,
                schema_valid=True,
            )
        model_capabilities = [
            clause.action.capability for clause in envelope.clauses if clause.action
        ]
        dimensions: list[str] = []
        if len(deterministic_parse.clauses) != len(envelope.clauses):
            dimensions.append("clause_count")
        if deterministic_capabilities != model_capabilities:
            dimensions.append("capability")
        if envelope.unsupported_model_capabilities:
            dimensions.append("unsupported_output")
        return IntentShadowRuntimeSummary(
            status="completed",
            model_name=model_name,
            duration_ms=(time.perf_counter() - started) * 1000,
            schema_valid=True,
            validation_passed=True,
            deterministic_capabilities=deterministic_capabilities,
            model_capabilities=model_capabilities,
            capability_match=deterministic_capabilities == model_capabilities,
            difference_dimensions=dimensions,
        )

    @staticmethod
    def _failure(
        status: str,
        started: float,
        deterministic_capabilities: list[str],
        model_name: str = "",
        *,
        schema_valid: bool = False,
    ) -> IntentShadowRuntimeSummary:
        return IntentShadowRuntimeSummary(
            status=status,
            model_name=model_name,
            duration_ms=(time.perf_counter() - started) * 1000,
            schema_valid=schema_valid,
            deterministic_capabilities=deterministic_capabilities,
            difference_dimensions=[status],
        )
