"""管理员 PDF 管理 HTTP 路由。"""

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..services.admin_pdf_service import (
    AdminPdfService,
    ingest_admin_pdf_record,
    process_admin_pdf_record,
)
from ._shared import json_response_with_scope, require_admin_identity

router = APIRouter()


def _admin_pdf_service() -> AdminPdfService:
    return AdminPdfService()


@router.get("/admin/pdfs")
async def get_admin_pdf_records(request: Request):
    """获取管理员 PDF 上传历史。"""
    require_admin_identity(request)
    return json_response_with_scope(request, _admin_pdf_service().list_records())


@router.post("/admin/pdfs")
async def upload_admin_pdf(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """上传并登记管理员 PDF。"""
    require_admin_identity(request)

    try:
        content = await file.read()
        result = _admin_pdf_service().save_upload(
            filename=file.filename,
            content_type=file.content_type,
            content=content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()

    if result.process_record_id:
        background_tasks.add_task(process_admin_pdf_record, result.process_record_id)

    return json_response_with_scope(
        request,
        result.payload,
        status_code=result.status_code,
        background=background_tasks if result.process_record_id else None,
    )


@router.get("/admin/pdfs/{record_id}")
async def get_admin_pdf_record_detail(request: Request, record_id: str):
    """获取单条管理员 PDF 记录详情与处理状态。"""
    require_admin_identity(request)
    record = _admin_pdf_service().get_record_detail(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="未找到指定 PDF 记录。")
    return json_response_with_scope(request, record)


@router.get("/admin/pdfs/{record_id}/file", name="get_admin_pdf_file")
async def get_admin_pdf_file(request: Request, record_id: str):
    """读取管理员上传的 PDF 原文件。"""
    session_manager, session_id, legacy_bindings, _ = require_admin_identity(request)
    file_state = _admin_pdf_service().get_file_state(record_id)
    if not file_state:
        raise HTTPException(status_code=404, detail="未找到指定 PDF 记录。")

    file_path, record = file_state
    response = FileResponse(
        file_path,
        media_type="application/pdf",
        filename=record["file_name"],
        content_disposition_type="inline",
    )
    session_manager.attach_scope_cookies(response, session_id, legacy_bindings)
    return response


@router.post("/admin/pdfs/{record_id}/ingest")
async def ingest_admin_pdf(request: Request, background_tasks: BackgroundTasks, record_id: str):
    """将管理员上传 PDF 显式归档到上传知识库底座。"""
    require_admin_identity(request)
    result = _admin_pdf_service().prepare_ingest(record_id)
    if not result:
        raise HTTPException(status_code=404, detail="未找到指定 PDF 记录。")

    if result.ingest_record_id:
        background_tasks.add_task(ingest_admin_pdf_record, result.ingest_record_id)

    return json_response_with_scope(
        request,
        result.payload,
        status_code=result.status_code,
        background=background_tasks if result.ingest_record_id else None,
    )


@router.patch("/admin/pdfs/{record_id}/correction")
async def save_admin_pdf_correction(request: Request, record_id: str):
    """保存管理员对 PDF 识别结果的人工校正，并标记为待重新归档。"""
    require_admin_identity(request)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON。") from exc

    corrected_text = str(payload.get("corrected_text") or payload.get("correction_text") or "").strip()
    if not corrected_text:
        raise HTTPException(status_code=400, detail="校正内容不能为空。")

    try:
        response_payload = _admin_pdf_service().save_correction(record_id, corrected_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not response_payload:
        raise HTTPException(status_code=404, detail="未找到指定 PDF 记录。")

    return json_response_with_scope(request, response_payload)


@router.delete("/admin/pdfs/{record_id}")
async def delete_admin_pdf(request: Request, record_id: str):
    """删除管理员上传记录及其文件。"""
    require_admin_identity(request)
    deleted = _admin_pdf_service().delete_record(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到可删除的 PDF 记录。")
    return json_response_with_scope(
        request,
        {
            "deleted": True,
            "record_id": record_id,
        },
    )
