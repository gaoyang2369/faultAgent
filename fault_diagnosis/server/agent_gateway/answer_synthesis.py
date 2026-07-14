"""Shared server boundary for optional V2 grounded-answer synthesis."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI

from fault_diagnosis.agent.output import GroundedAnswerResult, GroundedAnswerSynthesizer
from fault_diagnosis.domain.diagnosis.contracts import EvidenceBundle
from fault_diagnosis.domain.security.contracts import AuthContext
from fault_diagnosis.platform import settings
from fault_diagnosis.platform.logging import get_logger
from fault_diagnosis.server.bootstrap.app_models import build_answer_model


_log = get_logger("answer_synthesis")


async def synthesize_v2_answer(
    *,
    app: FastAPI,
    user_message: str,
    output_frame: Any,
    evidence_bundle: EvidenceBundle | None,
    runtime_status: str,
    auth_context: AuthContext,
    model_name: str | None = None,
    skip_reason: str = "",
) -> GroundedAnswerResult:
    """Apply the global feature gate and fail closed around injected adapters."""

    enabled = bool(settings.ENABLE_GROUNDED_ANSWER_SYNTHESIS)
    if not enabled:
        return GroundedAnswerResult(
            status="disabled",
            answer=output_frame.final_answer,
            enabled=False,
            attempted=False,
            model_name=str(model_name or ""),
            fallback_reason="feature_disabled",
        )
    if skip_reason:
        return GroundedAnswerResult(
            status="fallback",
            answer=output_frame.final_answer,
            enabled=True,
            attempted=False,
            model_name=str(model_name or ""),
            fallback_reason=skip_reason,
        )
    synthesizer = getattr(app.state, "grounded_answer_synthesizer", None)
    if synthesizer is None:
        synthesizer = GroundedAnswerSynthesizer(
            model_factory=build_answer_model,
            model_name=model_name,
            enabled=True,
            timeout_seconds=settings.ANSWER_SYNTHESIS_TIMEOUT_SECONDS,
            max_input_chars=settings.ANSWER_SYNTHESIS_MAX_INPUT_CHARS,
            max_output_chars=settings.ANSWER_SYNTHESIS_MAX_OUTPUT_CHARS,
        )
    try:
        semaphore = getattr(app.state, "answer_synthesis_semaphore", None)
        if semaphore is None:
            semaphore = asyncio.Semaphore(settings.ANSWER_SYNTHESIS_MAX_CONCURRENCY)
            app.state.answer_synthesis_semaphore = semaphore
        async with semaphore:
            return await synthesizer.synthesize(
                user_message=user_message,
                deterministic_answer=output_frame.final_answer,
                deliverables=list(output_frame.composite_output.deliverables),
                evidence_bundle=evidence_bundle,
                runtime_metadata={
                    "status": runtime_status,
                    "overall_status": output_frame.composite_output.overall_status,
                },
                auth_safe_context={"role": auth_context.role},
            )
    except Exception as exc:  # noqa: BLE001 - injected adapters must also fail closed.
        _log.warning("Grounded answer synthesis failed closed", error=type(exc).__name__)
        return GroundedAnswerResult(
            status="model_error",
            answer=output_frame.final_answer,
            enabled=True,
            attempted=True,
            model_name=str(model_name or ""),
            fallback_reason="synthesis_service_error",
        )


def runtime_evidence_bundle(result: Any) -> EvidenceBundle | None:
    """Read the already-public bundle projection without touching raw runtime artifacts."""

    payload = result.complete_payload.get("evidence_bundle") if isinstance(result.complete_payload, dict) else None
    if isinstance(payload, dict):
        try:
            return EvidenceBundle.model_validate(payload)
        except Exception:  # noqa: BLE001 - invalid audit projection must not affect runtime status.
            return None
    return None
