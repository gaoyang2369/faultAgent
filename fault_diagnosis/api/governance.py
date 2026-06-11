"""治理快照与治理台账 HTTP 路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..common.logger import ensure_request_id, get_logger
from ..common.paths import REPORTS_DIR
from ..services.governance_service import (
    GovernanceLedgerPayload,
    GovernanceLedgerUpdatePayload,
    GovernanceService,
    GovernanceSnapshotPayload,
    sanitize_governance_thread_hint,
)
from ..auth.session_scope import resolve_request_scope
from ..common.utils import summarize_identifier_for_log
from ._shared import json_response_with_scope

router = APIRouter()
_log = get_logger("api.governance")


def _governance_service() -> GovernanceService:
    return GovernanceService(reports_dir=REPORTS_DIR)


def _summarize_session_id(session_id: str | None) -> str:
    return summarize_identifier_for_log(session_id, keep=8)


def _summarize_thread_id(thread_id: str | None) -> str:
    return summarize_identifier_for_log(thread_id, keep=10)


def _sanitize_governance_thread_hint(thread_id: str | None) -> str:
    return sanitize_governance_thread_hint(thread_id)


@router.post("/api/governance/save")
async def save_governance_snapshot(request: Request, payload: GovernanceSnapshotPayload):
    _, session_id, _, _ = resolve_request_scope(request)
    request_id = ensure_request_id()

    _log.info(
        "收到治理快照保存请求",
        path="/api/governance/save",
        session_id=_summarize_session_id(session_id),
        thread_id=_summarize_thread_id(payload.thread_id),
        markdown_len=len(payload.markdown or ""),
        doc_template_len=len(payload.doc_template or ""),
        report_len=len(payload.report_markdown or ""),
        backlog_len=len(payload.backlog_markdown or ""),
        json_keys=sorted(payload.json_content.keys()) if isinstance(payload.json_content, dict) else [],
    )

    try:
        response_payload = _governance_service().save_snapshot(payload)
        _log.info(
            "治理快照保存完成",
            path="/api/governance/save",
            session_id=_summarize_session_id(session_id),
            thread_id=_summarize_thread_id(payload.thread_id),
            markdown_path=response_payload["markdown_path"],
            json_path=response_payload["json_path"],
            doc_template_path=response_payload["doc_template_path"],
            report_path=response_payload.get("report_path"),
            backlog_path=response_payload.get("backlog_path"),
        )
        return json_response_with_scope(request, response_payload)
    except Exception as error:
        _log.exception(
            "治理快照保存失败",
            path="/api/governance/save",
            session_id=_summarize_session_id(session_id),
            thread_id=_summarize_thread_id(payload.thread_id),
            error=str(error),
            error_id=request_id,
        )
        raise HTTPException(status_code=500, detail=f"governance snapshot save failed: {request_id}") from error


@router.get("/api/governance/list")
async def list_governance_snapshots(
    request: Request,
    thread_id: str | None = None,
    limit: int = 10,
):
    _, session_id, _, _ = resolve_request_scope(request)
    _log.info(
        "收到治理快照列表请求",
        path="/api/governance/list",
        session_id=_summarize_session_id(session_id),
        thread_id=_summarize_thread_id(thread_id),
        limit=limit,
    )
    return json_response_with_scope(
        request,
        _governance_service().list_snapshots(thread_id=thread_id, limit=limit),
    )


@router.post("/api/governance/ledger")
async def create_governance_ledger_record(request: Request, payload: GovernanceLedgerPayload):
    _, session_id, _, _ = resolve_request_scope(request)
    _log.info(
        "收到治理台账创建请求",
        path="/api/governance/ledger",
        session_id=_summarize_session_id(session_id),
        thread_id=_summarize_thread_id(payload.thread_id),
        risk_count=len(payload.risks),
        item_count=len(payload.items),
    )
    return json_response_with_scope(
        request,
        _governance_service().create_ledger_record(payload),
    )


@router.get("/api/governance/ledger")
async def list_governance_ledger_records(
    request: Request,
    thread_id: str | None = None,
    limit: int = 10,
    status: str | None = None,
    priority: str | None = None,
    owner: str | None = None,
    tag: str | None = None,
):
    _, session_id, _, _ = resolve_request_scope(request)
    _log.info(
        "收到治理台账列表请求",
        path="/api/governance/ledger",
        session_id=_summarize_session_id(session_id),
        thread_id=_summarize_thread_id(thread_id),
        limit=limit,
        status=status,
        priority=priority,
        owner=owner,
        tag=tag,
    )
    return json_response_with_scope(
        request,
        _governance_service().list_ledger_records(
            thread_id=thread_id,
            limit=limit,
            status=status,
            priority=priority,
            owner=owner,
            tag=tag,
        ),
    )


@router.post("/api/governance/ledger/update")
async def update_governance_ledger_record(request: Request, payload: GovernanceLedgerUpdatePayload):
    _, session_id, _, _ = resolve_request_scope(request)
    _log.info(
        "收到治理台账更新请求",
        path="/api/governance/ledger/update",
        session_id=_summarize_session_id(session_id),
        record_id=payload.record_id,
    )
    record = _governance_service().update_ledger_record(payload)
    if not record:
        raise HTTPException(status_code=404, detail="governance ledger record not found")
    return json_response_with_scope(request, record)
