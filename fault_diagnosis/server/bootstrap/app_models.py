from __future__ import annotations

import os
from functools import lru_cache

from langchain_openai import ChatOpenAI

from fault_diagnosis.platform import settings
from fault_diagnosis.platform.model_catalog import resolve_answer_model_name
from fault_diagnosis.agent.canonical_turn.llm_structured_clause_model import LLMStructuredClauseModel


def build_chat_model(model_name: str | None = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name or os.getenv("MODEL_NAME"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7,
    )


def build_summary_model(model_name: str | None = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name or os.getenv("MODEL_NAME"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7,
    )


@lru_cache(maxsize=8)
def build_answer_model(
    model_name: str,
    json_mode: bool = True,
    request_timeout_seconds: float | None = None,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """Lazily build and cache the low-temperature final-answer model."""

    resolved_model = resolve_answer_model_name(model_name)
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not resolved_model or not api_key:
        raise RuntimeError("answer_model_not_configured")
    return ChatOpenAI(
        model=resolved_model,
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=api_key,
        temperature=settings.ANSWER_SYNTHESIS_TEMPERATURE,
        timeout=request_timeout_seconds or settings.ANSWER_MODEL_REQUEST_TIMEOUT_SECONDS,
        max_tokens=max_tokens or settings.ANSWER_MODEL_MAX_TOKENS,
        max_retries=0,
        model_kwargs={"response_format": {"type": "json_object"}} if json_mode else {},
    )


def build_intent_clause_model() -> tuple[LLMStructuredClauseModel, str, str]:
    """Build the independent intent model only after the shadow gate is enabled."""

    model_name = settings.INTENT_MODEL_NAME or (os.getenv("MODEL_NAME") or "").strip()
    api_key = settings.INTENT_MODEL_API_KEY or (os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = settings.INTENT_MODEL_BASE_URL or (os.getenv("OPENAI_BASE_URL") or "").strip()
    if not model_name or not api_key:
        raise RuntimeError("intent_model_not_configured")
    source = ";".join((
        f"model:{'intent' if settings.INTENT_MODEL_NAME else 'general_fallback'}",
        f"base_url:{'intent' if settings.INTENT_MODEL_BASE_URL else 'general_fallback'}",
        f"api_key:{'intent' if settings.INTENT_MODEL_API_KEY else 'general_fallback'}",
    ))
    client = ChatOpenAI(
        model=model_name,
        base_url=base_url or None,
        api_key=api_key,
        temperature=settings.INTENT_MODEL_TEMPERATURE,
        timeout=settings.INTENT_MODEL_TIMEOUT_SECONDS,
        max_tokens=settings.INTENT_MODEL_MAX_TOKENS,
        max_retries=0,
        model_kwargs={"response_format": {"type": "json_object"}},
    )
    return LLMStructuredClauseModel(client), model_name, source
