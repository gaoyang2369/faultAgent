from __future__ import annotations

from fastapi import APIRouter

from fault_diagnosis.platform.model_catalog import get_model_catalog

router = APIRouter()


@router.get("/")
async def root() -> dict[str, object]:
    return {
        "message": "LangChain 1.0 Streaming Agent API is running!",
        "streaming_endpoint": "/chat/stream",
        "features": [
            "SSE streaming output",
            "工具调用实时反馈",
            "支持中文响应",
            "Persistent chat history",
        ],
    }


@router.get("/api/models")
async def list_models() -> dict[str, object]:
    """Return selectable model identifiers without exposing provider credentials."""

    return get_model_catalog()
