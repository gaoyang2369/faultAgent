"""Public LLM catalog and request-scoped model validation."""

from __future__ import annotations

import os
from typing import Any

from fault_diagnosis.platform.llm_runtime import resolve_llm_model_name


DEFAULT_MODEL_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "deepseek-ai/DeepSeek-V3.2",
        "label": "DeepSeek V3.2",
        "description": "综合诊断",
    },
    {
        "id": "Qwen/Qwen3.5-397B-A17B",
        "label": "Qwen3.5 397B",
        "description": "复杂推理",
    },
    {
        "id": "zai-org/GLM-5.2",
        "label": "GLM-5.2",
        "description": "工具调用",
    },
    {
        "id": "Pro/moonshotai/Kimi-K2.6",
        "label": "Kimi K2.6 Pro",
        "description": "长上下文",
    },
)


def _configured_model_ids() -> list[str]:
    raw_value = os.getenv("AVAILABLE_MODEL_NAMES", "")
    if not raw_value.strip():
        return [item["id"] for item in DEFAULT_MODEL_OPTIONS]
    return [value.strip() for value in raw_value.split(",") if value.strip()]


def get_model_catalog() -> dict[str, Any]:
    """Return a key-free model catalog safe to expose to the browser."""

    configured_ids = _configured_model_ids()
    configured_default = resolve_llm_model_name()
    if configured_default and configured_default not in configured_ids:
        configured_ids.insert(0, configured_default)

    metadata = {item["id"]: item for item in DEFAULT_MODEL_OPTIONS}
    models = []
    for model_id in configured_ids:
        item = metadata.get(model_id)
        models.append(
            item
            or {
                "id": model_id,
                "label": model_id.rsplit("/", 1)[-1],
                "description": "环境配置",
            }
        )

    default_model = configured_default if configured_default in configured_ids else (configured_ids[0] if configured_ids else "")
    return {"default_model": default_model, "models": models}


def resolve_model_name(requested_model: str | None) -> str:
    """Resolve a request model against the server-side allowlist."""

    catalog = get_model_catalog()
    allowed = {item["id"] for item in catalog["models"]}
    requested = (requested_model or "").strip()
    if not requested:
        return str(catalog["default_model"])
    if requested not in allowed:
        raise ValueError("不支持的模型，请刷新模型列表后重试")
    return requested


def get_answer_model_config() -> tuple[str, str]:
    """Resolve the fixed Answer model and record whether it was explicit."""

    explicit = os.getenv("ANSWER_MODEL_NAME", "").strip()
    selected = resolve_llm_model_name(explicit)
    if not selected:
        raise ValueError("answer_model_not_configured")
    return resolve_answer_model_name(selected), "answer_model" if explicit else "default_model"


def resolve_answer_model_name(requested_model: str) -> str:
    """Validate Answer models against a server-side catalog or explicit allowlist."""

    configured = {
        value.strip()
        for value in os.getenv("AVAILABLE_ANSWER_MODEL_NAMES", "").split(",")
        if value.strip()
    }
    allowed = configured or {item["id"] for item in get_model_catalog()["models"]}
    requested = str(requested_model or "").strip()
    if not requested or requested not in allowed:
        raise ValueError("answer_model_not_allowed")
    return requested
