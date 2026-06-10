"""运行时错误分类与用户可读提示。"""

from __future__ import annotations

from typing import Any

MODEL_QUOTA_MESSAGE = "模型 API 账号余额不足或额度不可用，请充值或更换 OPENAI_API_KEY 后重启后端"
MODEL_AUTH_MESSAGE = "模型网关鉴权失败，请检查 OPENAI_API_KEY、OPENAI_BASE_URL 和 MODEL_NAME 配置"
MODEL_FORBIDDEN_MESSAGE = "模型网关拒绝访问，请检查 API key、模型权限、账号状态或余额"

_MODEL_QUOTA_HINTS = (
    "insufficient balance",
    "account balance is insufficient",
    "insufficient_quota",
    "quota exceeded",
    "billing",
    "balance is insufficient",
    "code': 30001",
    '"code": 30001',
    "余额不足",
    "额度不足",
    "欠费",
)


def classify_model_gateway_error(error: Any) -> tuple[str, str] | None:
    """识别模型网关的鉴权、余额和权限类错误，避免前端只看到泛化失败。"""

    error_text = str(error)
    lowered = error_text.lower()

    if any(hint in lowered for hint in _MODEL_QUOTA_HINTS) or any(
        hint in error_text for hint in ("余额不足", "额度不足", "欠费")
    ):
        return "model_quota", MODEL_QUOTA_MESSAGE

    if "401" in error_text or "invalid api key" in lowered or "authentication" in lowered:
        return "model_auth", MODEL_AUTH_MESSAGE

    if "403" in error_text:
        return "model_auth", MODEL_FORBIDDEN_MESSAGE

    return None


def model_error_code(category: str) -> str:
    """把内部错误分类映射为对外稳定错误码。"""

    if category == "model_quota":
        return "MODEL_QUOTA_EXHAUSTED"
    if category == "model_auth":
        return "MODEL_AUTH_FAILED"
    return "UPSTREAM_UNAVAILABLE"
