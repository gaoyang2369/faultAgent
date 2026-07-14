from __future__ import annotations

import os
from functools import lru_cache

from langchain_openai import ChatOpenAI

from fault_diagnosis.platform import settings


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
def build_answer_model(model_name: str | None = None) -> ChatOpenAI:
    """Lazily build and cache the low-temperature final-answer model."""

    resolved_model = (model_name or os.getenv("MODEL_NAME") or "").strip()
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not resolved_model or not api_key:
        raise RuntimeError("answer_model_not_configured")
    return ChatOpenAI(
        model=resolved_model,
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=api_key,
        temperature=settings.ANSWER_SYNTHESIS_TEMPERATURE,
        timeout=settings.ANSWER_SYNTHESIS_TIMEOUT_SECONDS,
        max_tokens=settings.ANSWER_SYNTHESIS_MAX_OUTPUT_TOKENS,
        max_retries=0,
        model_kwargs={"response_format": {"type": "json_object"}},
    )
