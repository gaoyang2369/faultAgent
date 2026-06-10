"""TTS 合成代理 HTTP 路由。"""

from fastapi import APIRouter, Request

from ..config import (
    TTS_SYNTHESIZE_MAX_CHARS,
    TTS_SYNTHESIZE_TIMEOUT_SECONDS,
    TTS_SYNTHESIZE_URL,
)
from ..common.logger import get_logger
from ..services.tts_service import (
    TtsService,
    read_tts_synthesize_text,
    request_tts_audio,
)

router = APIRouter()
_log = get_logger("api.tts")


async def _read_tts_synthesize_text(request: Request) -> str:
    return await read_tts_synthesize_text(request)


async def _request_tts_audio(text: str) -> dict:
    return await request_tts_audio(
        text,
        synthesize_url=TTS_SYNTHESIZE_URL,
        timeout_seconds=TTS_SYNTHESIZE_TIMEOUT_SECONDS,
    )


@router.post("/tts/synthesize")
async def synthesize_tts(request: Request):
    """将短文本转发给 TTS 服务，返回前端播放器可消费的 PCM Base64。"""

    service = TtsService(
        max_chars=TTS_SYNTHESIZE_MAX_CHARS,
        request_audio=_request_tts_audio,
        logger=_log,
    )
    return await service.synthesize_from_request(request)
