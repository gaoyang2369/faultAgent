from __future__ import annotations

from fastapi import APIRouter

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
