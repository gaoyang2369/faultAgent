"""TTS 合成应用服务。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi import HTTPException, Request

from ..config import TTS_SYNTHESIZE_TIMEOUT_SECONDS, TTS_SYNTHESIZE_URL
from ..common.logger import get_logger

TtsAudioRequester = Callable[[str], Awaitable[dict[str, Any]]]


async def read_tts_synthesize_text(request: Request) -> str:
    """从 JSON 或纯文本请求体中读取待合成文本。"""

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体必须是有效 JSON。") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="请求体必须包含 text 字段。")
        return str(payload.get("text") or "").strip()

    body = await request.body()
    return body.decode("utf-8", errors="ignore").strip()


async def request_tts_audio(
    text: str,
    *,
    synthesize_url: str = TTS_SYNTHESIZE_URL,
    timeout_seconds: float = TTS_SYNTHESIZE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """请求外部 TTS 服务并规整返回给前端的音频字段。"""

    if not synthesize_url:
        raise RuntimeError("未配置 TTS 合成服务地址")

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            synthesize_url,
            content=text.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        response.raise_for_status()

    try:
        payload = response.json()
    except ValueError:
        payload = {"audio": response.text.strip()}

    if not isinstance(payload, dict):
        raise RuntimeError("TTS 合成服务返回格式错误")

    nested_data = payload.get("data")
    audio = payload.get("audio")
    if not audio and isinstance(nested_data, dict):
        audio = nested_data.get("audio")
    if not isinstance(audio, str) or not audio.strip():
        raise RuntimeError("TTS 合成服务未返回音频")

    result: dict[str, Any] = {"audio": audio.strip()}
    sample_rate = payload.get("sample_rate")
    if sample_rate is None and isinstance(nested_data, dict):
        sample_rate = nested_data.get("sample_rate")
    if isinstance(sample_rate, int):
        result["sample_rate"] = sample_rate
    return result


class TtsService:
    """处理 TTS HTTP 用例的应用服务。"""

    def __init__(
        self,
        *,
        max_chars: int,
        request_audio: TtsAudioRequester = request_tts_audio,
        logger=None,
    ) -> None:
        self.max_chars = max_chars
        self.request_audio = request_audio
        self._log = logger or get_logger("services.tts")

    async def synthesize_from_request(self, request: Request) -> dict[str, Any]:
        """读取请求、校验文本并返回 TTS 音频。"""

        text = await read_tts_synthesize_text(request)
        if not text:
            raise HTTPException(status_code=400, detail="text 字段不能为空。")
        if len(text) > self.max_chars:
            raise HTTPException(
                status_code=400,
                detail=f"text 字段不能超过 {self.max_chars} 个字符。",
            )

        try:
            return await self.request_audio(text)
        except httpx.HTTPError as exc:
            self._log.warning(
                "TTS 合成服务请求失败",
                error=str(exc),
                text_len=len(text),
            )
            raise HTTPException(status_code=502, detail="TTS 合成服务暂时不可用。") from exc
        except RuntimeError as exc:
            self._log.warning(
                "TTS 合成服务返回异常",
                error=str(exc),
                text_len=len(text),
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
