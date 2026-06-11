"""健康检查相关 HTTP 路由。"""

import time

from fastapi import APIRouter, Request

from ..services.health_service import build_dependencies_health
from ..common.logger import ensure_request_id, get_logger

router = APIRouter()
_log = get_logger("api.health")


def _health_issue_names(payload: dict) -> list[str]:
    checks = payload.get("checks", {}) if isinstance(payload, dict) else {}
    issue_statuses = {
        "warning",
        "error",
        "timeout",
        "missing_config",
        "not_initialized",
        "not_ready",
        "failed",
        "degraded",
        "not_configured",
    }
    return [name for name, check in checks.items() if isinstance(check, dict) and check.get("status") in issue_statuses]


@router.get("/health/dependencies")
async def health_dependencies(request: Request, deep: bool = True):
    """检查真实依赖状态，不触发模型推理。"""
    request_id = ensure_request_id()
    started_at = time.monotonic()
    _log.info(
        "收到依赖健康检查请求",
        request_id=request_id,
        path="/health/dependencies",
        deep=deep,
    )
    payload = await build_dependencies_health(request.app, deep=deep)
    _log.info(
        "依赖健康检查完成",
        request_id=request_id,
        path="/health/dependencies",
        deep=deep,
        status=payload.get("status"),
        issue_checks=_health_issue_names(payload),
        duration_ms=round((time.monotonic() - started_at) * 1000, 1),
    )
    return payload


@router.get("/health/ocr")
async def health_ocr():
    """返回 OCR provider 的轻量可用性探测结果，不执行模型加载。"""
    try:
        from ..integrations.medicine_ocr_runtime import get_medicine_ocr_status

        return get_medicine_ocr_status(load_tested=False)
    except Exception as exc:
        return {
            "status": "failed",
            "provider": "medicine_ocr",
            "detail": f"OCR 依赖加载失败：{str(exc)[:300]}",
        }


@router.get("/health/real")
async def health_real(request: Request, deep: bool = True):
    """真实环境健康检查别名，用于依赖恢复后的检查。"""
    request_id = ensure_request_id()
    started_at = time.monotonic()
    _log.info(
        "收到真实环境健康检查请求",
        request_id=request_id,
        path="/health/real",
        deep=deep,
    )
    payload = await build_dependencies_health(request.app, deep=deep)
    _log.info(
        "真实环境健康检查完成",
        request_id=request_id,
        path="/health/real",
        deep=deep,
        status=payload.get("status"),
        issue_checks=_health_issue_names(payload),
        duration_ms=round((time.monotonic() - started_at) * 1000, 1),
    )
    return payload
