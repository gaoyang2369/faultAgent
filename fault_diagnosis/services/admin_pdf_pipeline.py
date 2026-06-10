"""管理员上传 PDF 的 OCR / 结构化 / 知识库归档流水线。"""

from __future__ import annotations

import os

from ..common.logger import get_logger
from ..integrations.medicine_ocr_runtime import extract_pdf_content
from ..repositories.admin_pdf_repository import FileAdminPdfRepository, get_admin_pdf_repository
from ..knowledge.uploaded_pdf_kb import rebuild_uploaded_pdf_knowledge_base, reset_uploaded_pdf_knowledge_base

_log = get_logger("admin_pdf_processing")


def _repository() -> FileAdminPdfRepository:
    return get_admin_pdf_repository()


def _mark_kb_ingested_records(
    record_ids: list[str],
    kb_mode: str,
    *,
    repository: FileAdminPdfRepository | None = None,
) -> None:
    repository = repository or _repository()
    for target_id in record_ids:
        target_record = repository.get_record(target_id) or {}
        correction_fields = {}
        if target_record.get("correction_file"):
            correction_fields["correction_ingested_at"] = target_record.get("corrected_at")
        repository.update_record_fields(
            target_id,
            kb_ingest_status="succeeded",
            kb_error=None,
            kb_document_id=target_id,
            kb_index_mode=kb_mode,
            status_label="校正内容已归档，Agent 可查询" if correction_fields else "已归档知识库，Agent 可查询",
            **correction_fields,
        )


def _has_kb_source_file(record: dict | None, *, repository: FileAdminPdfRepository | None = None) -> bool:
    if not record:
        return False
    repository = repository or _repository()
    correction_file = str(record.get("correction_file", "")).strip()
    if correction_file:
        correction_path = repository.artifact_path("corrections", correction_file)
        if os.path.exists(correction_path):
            return True
    kb_source_file = str(record.get("kb_source_file", "")).strip()
    if not kb_source_file:
        return False
    kb_source_path = repository.artifact_path("kb_docs", kb_source_file)
    return os.path.exists(kb_source_path)


def _has_pending_correction(record: dict | None) -> bool:
    if not record or not record.get("correction_file"):
        return False
    return int(record.get("corrected_at") or 0) > int(record.get("correction_ingested_at") or 0)


def process_admin_pdf_record(record_id: str) -> None:
    repository = _repository()
    record = repository.get_record(record_id)
    if not record:
        return

    repository.update_record_fields(
        record_id,
        ocr_status="extracting_text",
        ocr_error=None,
        kb_ingest_status="pending",
        kb_error=None,
        status_label="文本提取中",
    )

    file_state = repository.get_file_path(record_id)
    if not file_state:
        repository.update_record_fields(
            record_id,
            ocr_status="failed",
            kb_ingest_status="failed",
            kb_error="未找到已保存的原始 PDF 文件。",
            ocr_error="未找到已保存的原始 PDF 文件。",
            status_label="处理失败",
        )
        return

    file_path, current_record = file_state
    try:
        parsed = extract_pdf_content(file_path, current_record.get("file_name", "upload.pdf"))
    except Exception as exc:
        _log.warning("PDF OCR/解析失败", record_id=record_id, error=str(exc))
        repository.update_record_fields(
            record_id,
            ocr_status="ocr_failed",
            kb_ingest_status="failed",
            kb_error=None,
            ocr_error=str(exc),
            status_label="OCR 失败",
        )
        return

    repository.save_processing_artifacts(
        record_id,
        raw_text=parsed.get("raw_text", ""),
        page_summaries=parsed.get("page_summaries", []),
        structured_result=parsed.get("structured_result", {}),
        kb_markdown=parsed.get("kb_markdown", ""),
    )
    repository.update_record_fields(
        record_id,
        ocr_status=parsed["status"],
        ocr_error=parsed.get("error") or None,
        ocr_backend=parsed.get("ocr_backend"),
        kb_ingest_status="pending" if parsed["status"] == "text_extracted" else "skipped",
        kb_error=None,
        status_label=parsed.get("status_label") or "文本提取完成",
    )

    if parsed["status"] != "text_extracted":
        _log.info(
            "上传 PDF 未进入知识库归档，等待重型 OCR 或后续人工处理",
            record_id=record_id,
            ocr_status=parsed["status"],
            ocr_backend=parsed.get("ocr_backend"),
        )
        return
    repository.update_record_fields(record_id, kb_error=None, status_label="已提取文本，尚未归档知识库")


def ingest_admin_pdf_record(record_id: str) -> dict | None:
    repository = _repository()
    record = repository.get_record(record_id)
    if not record:
        return None

    if record.get("kb_ingest_status") == "succeeded" and not _has_pending_correction(record):
        return repository.get_record(record_id)

    if record.get("ocr_status") == "extracting_text":
        _log.info("上传 PDF 仍在文本提取中，暂不启动知识库归档", record_id=record_id)
        return repository.get_record(record_id)

    has_user_correction = bool(record.get("correction_file"))
    if not has_user_correction and (
        record.get("ocr_status") != "text_extracted"
        or not _has_kb_source_file(record, repository=repository)
    ):
        process_admin_pdf_record(record_id)
        record = repository.get_record(record_id)
        if not record:
            return None
        has_user_correction = bool(record.get("correction_file"))

    if record.get("ocr_status") != "text_extracted" and not has_user_correction:
        if record.get("ocr_status") in {"needs_heavy_ocr", "ocr_model_not_configured"}:
            repository.update_record_fields(
                record_id,
                kb_ingest_status="skipped",
                kb_error=None,
                status_label="文本不足，需重型 OCR 后归档",
            )
        return repository.get_record(record_id)

    repository.update_record_fields(
        record_id,
        kb_ingest_status="processing",
        kb_error=None,
        status_label="知识库归档中",
    )

    try:
        kb_status = rebuild_uploaded_pdf_knowledge_base(repository.list_records_raw())
        kb_mode = kb_status.get("mode", "lexical_corpus")
        _mark_kb_ingested_records(
            kb_status.get("record_ids", [record_id]),
            kb_mode,
            repository=repository,
        )
        _log.info(
            "上传 PDF 已归档到知识库",
            record_id=record_id,
            chunk_count=kb_status.get("chunk_count", 0),
            kb_mode=kb_mode,
        )
    except Exception as exc:
        _log.warning("上传 PDF 知识库归档失败", record_id=record_id, error=str(exc))
        repository.update_record_fields(
            record_id,
            kb_ingest_status="failed",
            kb_error=str(exc),
            status_label="知识库归档失败",
            ocr_error=None,
        )
    return repository.get_record(record_id)


def save_admin_pdf_user_correction(record_id: str, corrected_text: str) -> dict | None:
    repository = _repository()
    updated_record = repository.save_user_correction(record_id, corrected_text)
    if not updated_record:
        return None
    try:
        rebuild_uploaded_pdf_knowledge_base(repository.list_records_raw())
    except Exception as exc:
        _log.warning("保存 PDF 校正后刷新上传知识库失败，已清空上传知识库索引", record_id=record_id, error=str(exc))
        reset_uploaded_pdf_knowledge_base()
    return repository.get_record(record_id)


def schedule_ingest_admin_pdf_record(record_id: str) -> dict | None:
    repository = _repository()
    record = repository.get_record(record_id)
    if not record:
        return None

    if record.get("kb_ingest_status") == "succeeded" and not _has_pending_correction(record):
        return record

    if record.get("ocr_status") == "extracting_text":
        return record

    repository.update_record_fields(
        record_id,
        kb_ingest_status="processing",
        kb_error=None,
        status_label="知识库归档中",
    )
    return repository.get_record(record_id)


def delete_admin_pdf_record_with_artifacts(record_id: str) -> bool:
    repository = _repository()
    deleted = repository.delete_record(record_id)
    if not deleted:
        return False
    try:
        rebuild_uploaded_pdf_knowledge_base(repository.list_records_raw())
    except Exception as exc:
        _log.warning("删除上传 PDF 后重建知识库失败，已清空上传知识库索引", record_id=record_id, error=str(exc))
        reset_uploaded_pdf_knowledge_base()
    return True


__all__ = [
    "delete_admin_pdf_record_with_artifacts",
    "ingest_admin_pdf_record",
    "process_admin_pdf_record",
    "save_admin_pdf_user_correction",
    "schedule_ingest_admin_pdf_record",
]
