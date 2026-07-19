from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from fault_diagnosis.platform import settings
from fault_diagnosis.platform.llm_runtime import (
    chat_openai_transport_kwargs,
    chat_template_extra_body,
    resolve_llm_api_key,
    resolve_llm_base_url,
    resolve_llm_model_name,
)
from fault_diagnosis.platform.model_catalog import resolve_answer_model_name


def build_chat_model(model_name: str | None = None) -> ChatOpenAI:
    base_url = resolve_llm_base_url()
    runtime_kwargs = chat_openai_transport_kwargs(base_url)
    extra_body = chat_template_extra_body(base_url)
    if extra_body is not None:
        runtime_kwargs["extra_body"] = extra_body
    return ChatOpenAI(
        model=resolve_llm_model_name(model_name),
        base_url=base_url or None,
        api_key=resolve_llm_api_key(),
        temperature=0.7,
        **runtime_kwargs,
    )


def build_summary_model(model_name: str | None = None) -> ChatOpenAI:
    base_url = resolve_llm_base_url()
    runtime_kwargs = chat_openai_transport_kwargs(base_url)
    extra_body = chat_template_extra_body(base_url)
    if extra_body is not None:
        runtime_kwargs["extra_body"] = extra_body
    return ChatOpenAI(
        model=resolve_llm_model_name(model_name),
        base_url=base_url or None,
        api_key=resolve_llm_api_key(),
        temperature=0.7,
        **runtime_kwargs,
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
    api_key = resolve_llm_api_key()
    base_url = resolve_llm_base_url()
    if not resolved_model or not api_key:
        raise RuntimeError("answer_model_not_configured")
    runtime_kwargs = chat_openai_transport_kwargs(base_url)
    extra_body = chat_template_extra_body(base_url)
    if extra_body is not None:
        runtime_kwargs["extra_body"] = extra_body
    return ChatOpenAI(
        model=resolved_model,
        base_url=base_url or None,
        api_key=api_key,
        temperature=settings.ANSWER_SYNTHESIS_TEMPERATURE,
        timeout=request_timeout_seconds or settings.ANSWER_MODEL_REQUEST_TIMEOUT_SECONDS,
        max_tokens=max_tokens or settings.ANSWER_MODEL_MAX_TOKENS,
        max_retries=0,
        model_kwargs={"response_format": {"type": "json_object"}} if json_mode else {},
        **runtime_kwargs,
    )
