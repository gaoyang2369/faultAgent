"""Shared OpenAI-compatible LLM endpoint resolution and transport options."""

from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from fault_diagnosis.platform.paths import PROJECT_ENV_FILE


load_dotenv(dotenv_path=PROJECT_ENV_FILE, override=False)


def resolve_llm_model_name(explicit: str | None = None) -> str:
    """Prefer the local-vLLM aliases while retaining OPENAI_* compatibility."""

    return (
        str(explicit or "").strip()
        or os.getenv("LLM_MODEL", "").strip()
        or os.getenv("MODEL_NAME", "").strip()
    )


def resolve_llm_base_url(explicit: str | None = None) -> str:
    return (
        str(explicit or "").strip()
        or os.getenv("LLM_BASE_URL", "").strip()
        or os.getenv("OPENAI_BASE_URL", "").strip()
    )


def resolve_llm_api_key(explicit: str | None = None) -> str:
    return (
        str(explicit or "").strip()
        or os.getenv("LLM_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )


def chat_openai_transport_kwargs(base_url: str) -> dict[str, Any]:
    """Bypass shell proxies for private/local model endpoints only."""

    configured = os.getenv("LLM_BYPASS_PROXY")
    bypass = _env_bool(configured) if configured is not None else _is_private_endpoint(base_url)
    if not bypass:
        return {}
    return {
        "http_client": httpx.Client(trust_env=False),
        "http_async_client": httpx.AsyncClient(trust_env=False),
    }


def chat_template_extra_body(base_url: str) -> dict[str, Any] | None:
    """Disable Qwen/vLLM thinking for bounded JSON and answer-formatting calls."""

    configured = os.getenv("LLM_ENABLE_THINKING")
    is_local_vllm = bool(os.getenv("LLM_BASE_URL", "").strip()) and _same_endpoint(
        base_url, os.getenv("LLM_BASE_URL", "")
    )
    if configured is None and not is_local_vllm:
        return None
    enable_thinking = _env_bool(configured) if configured is not None else False
    return {"chat_template_kwargs": {"enable_thinking": enable_thinking}}


def _same_endpoint(left: str, right: str) -> bool:
    return left.rstrip("/") == right.strip().rstrip("/")


def _is_private_endpoint(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").strip().lower()
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def _env_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "chat_openai_transport_kwargs",
    "chat_template_extra_body",
    "resolve_llm_api_key",
    "resolve_llm_base_url",
    "resolve_llm_model_name",
]
