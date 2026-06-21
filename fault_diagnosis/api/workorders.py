"""维修工单 HTTP 路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..common.logger import ensure_request_id, get_logger
from ..common.utils import summarize_identifier_for_log
from ..services.workorder_service import (
    CreateWorkOrderPayload,
    UpdateWorkOrderPayload,
    WorkOrderService,
)
from ._shared import json_response_with_scope, resolve_request_auth_context

router = APIRouter()
_log = get_logger("api.workorders")


def _workorder_service() -> WorkOrderService:
    return WorkOrderService()


def _summarize(value: str | None, *, keep: int = 10) -> str:
    return summarize_identifier_for_log(value, keep=keep)


@router.post("/api/workorders")
async def create_work_order(request: Request, payload: CreateWorkOrderPayload):
    _, session_id, _, auth_context = resolve_request_auth_context(request)
    request_id = ensure_request_id()
    _log.info(
        "收到维修工单创建请求",
        path="/api/workorders",
        session_id=_summarize(session_id, keep=8),
        thread_id=_summarize(payload.thread_id),
        trace_id=_summarize(payload.trace_id),
        title=payload.title[:80],
        priority=payload.priority,
    )
    try:
        response_payload = _workorder_service().create_work_order(payload, auth_context=auth_context)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _log.exception(
            "维修工单创建失败",
            path="/api/workorders",
            session_id=_summarize(session_id, keep=8),
            trace_id=_summarize(payload.trace_id),
            error_id=request_id,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"work order create failed: {request_id}") from exc
    return json_response_with_scope(request, response_payload)


@router.get("/api/workorders")
async def list_work_orders(
    request: Request,
    thread_id: str | None = None,
    trace_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
):
    _, session_id, _, auth_context = resolve_request_auth_context(request)
    _log.info(
        "收到维修工单列表请求",
        path="/api/workorders",
        session_id=_summarize(session_id, keep=8),
        thread_id=_summarize(thread_id),
        trace_id=_summarize(trace_id),
        status=status,
        limit=limit,
    )
    try:
        payload = _workorder_service().list_work_orders(
            thread_id=thread_id,
            trace_id=trace_id,
            status=status,
            limit=limit,
            auth_context=auth_context,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return json_response_with_scope(request, payload)


@router.get("/api/workorders/{work_order_id}")
async def get_work_order(request: Request, work_order_id: str):
    _, session_id, _, auth_context = resolve_request_auth_context(request)
    _log.info(
        "收到维修工单详情请求",
        path="/api/workorders/:work_order_id",
        session_id=_summarize(session_id, keep=8),
        work_order_id=work_order_id,
    )
    try:
        record = _workorder_service().get_work_order(work_order_id, auth_context=auth_context)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="work order not found")
    return json_response_with_scope(request, {"ok": True, "work_order": record})


@router.post("/api/workorders/update")
async def update_work_order(request: Request, payload: UpdateWorkOrderPayload):
    _, session_id, _, auth_context = resolve_request_auth_context(request)
    _log.info(
        "收到维修工单更新请求",
        path="/api/workorders/update",
        session_id=_summarize(session_id, keep=8),
        work_order_id=payload.work_order_id,
    )
    try:
        record = _workorder_service().update_work_order(payload, auth_context=auth_context)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="work order not found")
    return json_response_with_scope(request, record)
