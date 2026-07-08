"""管理员知识文件管理 HTTP 路由。"""

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from fault_diagnosis.server.use_cases.admin_knowledge_file_service import (
    AdminKnowledgeFileService,
    ingest_admin_knowledge_file_record,
    process_admin_knowledge_file_record,
)
from ._shared import json_response_with_scope, require_admin_identity

router = APIRouter()


def _knowledge_file_service() -> AdminKnowledgeFileService:
    return AdminKnowledgeFileService()


@router.get("/admin/knowledge-files")
async def get_admin_knowledge_file_records(request: Request):
    """获取管理员上传知识文件历史。"""
    require_admin_identity(request)
    return json_response_with_scope(request, _knowledge_file_service().list_records())


@router.post("/admin/knowledge-files")
async def upload_admin_knowledge_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """上传并登记管理员知识文件。"""
    require_admin_identity(request)

    try:
        content = await file.read()
        result = _knowledge_file_service().save_upload(
            filename=file.filename,
            content_type=file.content_type,
            content=content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()

    if result.process_record_id:
        background_tasks.add_task(process_admin_knowledge_file_record, result.process_record_id)

    return json_response_with_scope(
        request,
        result.payload,
        status_code=result.status_code,
        background=background_tasks if result.process_record_id else None,
    )


@router.get("/admin/knowledge-files/{record_id}")
async def get_admin_knowledge_file_record_detail(request: Request, record_id: str):
    """获取单条管理员知识文件记录详情与处理状态。"""
    require_admin_identity(request)
    record = _knowledge_file_service().get_record_detail(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="未找到指定知识文件记录。")
    return json_response_with_scope(request, record)


@router.get("/admin/knowledge-files/{record_id}/file", name="get_admin_knowledge_file")
async def get_admin_knowledge_file(request: Request, record_id: str):
    """读取管理员上传的原文件。"""
    session_manager, session_id, legacy_bindings, _ = require_admin_identity(request)
    file_state = _knowledge_file_service().get_file_state(record_id)
    if not file_state:
        raise HTTPException(status_code=404, detail="未找到指定知识文件记录。")

    file_path, record = file_state
    response = FileResponse(
        file_path,
        media_type=record.get("file_type") or record.get("mime_type") or "application/octet-stream",
        filename=record["file_name"],
        content_disposition_type="inline",
    )
    session_manager.attach_scope_cookies(response, session_id, legacy_bindings)
    return response


@router.post("/admin/knowledge-files/{record_id}/ingest")
async def ingest_admin_knowledge_file(request: Request, background_tasks: BackgroundTasks, record_id: str):
    """将管理员上传文件显式归档到上传知识库底座。"""
    require_admin_identity(request)
    result = _knowledge_file_service().prepare_ingest(record_id)
    if not result:
        raise HTTPException(status_code=404, detail="未找到指定知识文件记录。")

    if result.ingest_record_id:
        background_tasks.add_task(ingest_admin_knowledge_file_record, result.ingest_record_id)

    return json_response_with_scope(
        request,
        result.payload,
        status_code=result.status_code,
        background=background_tasks if result.ingest_record_id else None,
    )


@router.patch("/admin/knowledge-files/{record_id}/correction")
async def save_admin_knowledge_file_correction(request: Request, record_id: str):
    """保存管理员对识别结果的人工校对，并标记为待重新归档。"""
    require_admin_identity(request)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON。") from exc

    corrected_text = str(payload.get("corrected_text") or payload.get("correction_text") or "").strip()
    if not corrected_text:
        raise HTTPException(status_code=400, detail="校正内容不能为空。")

    try:
        response_payload = _knowledge_file_service().save_correction(record_id, corrected_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not response_payload:
        raise HTTPException(status_code=404, detail="未找到指定知识文件记录。")

    return json_response_with_scope(request, response_payload)


@router.delete("/admin/knowledge-files/{record_id}")
async def delete_admin_knowledge_file(request: Request, record_id: str):
    """删除管理员上传记录及其文件。"""
    require_admin_identity(request)
    deleted = _knowledge_file_service().delete_record(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到可删除的知识文件记录。")
    return json_response_with_scope(
        request,
        {
            "deleted": True,
            "record_id": record_id,
        },
    )

