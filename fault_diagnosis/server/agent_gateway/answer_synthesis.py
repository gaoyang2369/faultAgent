"""Shared server boundary for optional V2 grounded-answer synthesis."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from fastapi import FastAPI

from fault_diagnosis.agent.output import GroundedAnswerResult, GroundedAnswerSynthesizer
from fault_diagnosis.domain.diagnosis.contracts import EvidenceBundle
from fault_diagnosis.domain.security.contracts import AuthContext
from fault_diagnosis.platform import settings
from fault_diagnosis.platform.logging import get_logger
from fault_diagnosis.server.bootstrap.app_models import build_answer_model
from fault_diagnosis.platform.model_catalog import get_answer_model_config


_log = get_logger("answer_synthesis")


async def synthesize_v2_answer(
    *,
    app: FastAPI,
    user_message: str,
    output_frame: Any,
    evidence_bundle: EvidenceBundle | None,
    runtime_status: str,
    auth_context: AuthContext,
    thread_id: str = "",
    model_name: str | None = None,
    skip_reason: str = "",
) -> GroundedAnswerResult:
    """Apply the global feature gate and fail closed around injected adapters."""

    del model_name  # The chat model never selects the fixed Answer model.
    enabled = bool(settings.ENABLE_GROUNDED_ANSWER_SYNTHESIS)
    rollout_enabled = grounded_answer_rollout_enabled(
        thread_id=thread_id,
        enabled=enabled,
        percent=settings.GROUNDED_ANSWER_ROLLOUT_PERCENT,
    )
    if not rollout_enabled:
        return GroundedAnswerResult(
            status="disabled",
            synthesis_status="disabled",
            final_answer_source="deterministic_fallback",
            fallback_used=True,
            answer=output_frame.final_answer,
            enabled=enabled,
            attempted=False,
            fallback_reason="feature_disabled" if not enabled else "rollout_disabled",
        )
    if skip_reason:
        return GroundedAnswerResult(
            status="disabled",
            synthesis_status="disabled",
            final_answer_source="deterministic_fallback",
            fallback_used=True,
            answer=output_frame.final_answer,
            enabled=True,
            attempted=False,
            fallback_reason=skip_reason,
        )
    synthesizer = getattr(app.state, "grounded_answer_synthesizer", None)
    if synthesizer is None:
        try:
            answer_model_name, answer_model_source = get_answer_model_config()
            synthesizer = GroundedAnswerSynthesizer(
                model_factory=build_answer_model,
                model_name=answer_model_name,
                model_source=answer_model_source,
                enabled=True,
                timeout_seconds=settings.ANSWER_MODEL_REQUEST_TIMEOUT_SECONDS,
                max_input_chars=settings.ANSWER_SYNTHESIS_MAX_INPUT_CHARS,
                max_output_chars=settings.ANSWER_SYNTHESIS_MAX_OUTPUT_CHARS,
                include_fallback_in_packet=settings.ANSWER_MODEL_INCLUDE_DETERMINISTIC_FALLBACK,
            )
        except Exception:
            return GroundedAnswerResult(
                status="fallback",
                synthesis_status="model_not_configured",
                final_answer_source="deterministic_fallback",
                fallback_used=True,
                answer=output_frame.final_answer,
                enabled=True,
                attempted=False,
                fallback_reason="model_not_configured",
            )
    try:
        semaphore = getattr(app.state, "answer_synthesis_semaphore", None)
        if semaphore is None:
            semaphore = asyncio.Semaphore(settings.ANSWER_MODEL_CONCURRENCY)
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
            synthesis_status="model_error",
            final_answer_source="deterministic_fallback",
            fallback_used=True,
            answer=output_frame.final_answer,
            enabled=True,
            attempted=True,
            fallback_reason="synthesis_service_error",
        )


def grounded_answer_rollout_enabled(*, thread_id: str, enabled: bool, percent: int) -> bool:
    """Use stable thread hashing; no request-level randomness."""

    if not enabled or percent <= 0:
        return False
    if percent >= 100:
        return True
    stable_id = str(thread_id or "").strip()
    if not stable_id:
        return False
    bucket = int.from_bytes(hashlib.sha256(stable_id.encode("utf-8")).digest()[:8], "big") % 100
    return bucket < percent


def runtime_evidence_bundle(result: Any) -> EvidenceBundle | None:
    """Read the already-public bundle projection without touching raw runtime artifacts."""

    payload = result.complete_payload.get("evidence_bundle") if isinstance(result.complete_payload, dict) else None
    if isinstance(payload, dict):
        try:
            return EvidenceBundle.model_validate(payload)
        except Exception:  # noqa: BLE001 - invalid audit projection must not affect runtime status.
            return None
    return None
