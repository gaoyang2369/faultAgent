"""管理员知识文件 上传记录的轻量运行态存储。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from uuid import uuid4

from ..config import ADMIN_PDF_MAX_FILE_SIZE, ADMIN_UPLOAD_DIR


_FILES_DIR = os.path.join(ADMIN_UPLOAD_DIR, "files")
_RENDERED_PAGES_DIR = os.path.join(ADMIN_UPLOAD_DIR, "rendered_pages")
_PREPROCESSED_DIR = os.path.join(ADMIN_UPLOAD_DIR, "preprocessed")
_OCR_RESULTS_DIR = os.path.join(ADMIN_UPLOAD_DIR, "ocr_results")
_STRUCTURED_RESULTS_DIR = os.path.join(ADMIN_UPLOAD_DIR, "structured_results")
_KB_DOCS_DIR = os.path.join(ADMIN_UPLOAD_DIR, "kb_docs")
_REVIEWED_DOCS_DIR = os.path.join(ADMIN_UPLOAD_DIR, "reviewed_docs")
_CORRECTIONS_DIR = os.path.join(ADMIN_UPLOAD_DIR, "corrections")
_RECORDS_FILE = os.path.join(ADMIN_UPLOAD_DIR, "records.json")
_STORE_LOCK = threading.Lock()
_SUPPORTED_UPLOAD_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_TERMINAL_OCR_STATUSES = {
    "needs_review",
    "reviewed",
    "indexed",
    "text_extracted",
    "needs_heavy_ocr",
    "ocr_model_not_configured",
    "ocr_failed",
    "failed",
}
_TERMINAL_KB_STATUSES = {"succeeded", "indexed", "failed", "skipped"}


def _ensure_store_ready() -> None:
    for path in (
        _FILES_DIR,
        _RENDERED_PAGES_DIR,
        _PREPROCESSED_DIR,
        _OCR_RESULTS_DIR,
        _STRUCTURED_RESULTS_DIR,
        _KB_DOCS_DIR,
        _REVIEWED_DOCS_DIR,
        _CORRECTIONS_DIR,
    ):
        os.makedirs(path, exist_ok=True)


def _controlled_path(*parts: str) -> str:
    root = os.path.abspath(ADMIN_UPLOAD_DIR)
    candidate = os.path.abspath(os.path.join(ADMIN_UPLOAD_DIR, *parts))
    if os.path.commonpath([root, candidate]) != root:
        raise ValueError("检测到非法文件路径。")
    return candidate


def _read_records_unlocked() -> list[dict]:
    _ensure_store_ready()
    if not os.path.exists(_RECORDS_FILE):
        return []
    try:
        with open(_RECORDS_FILE, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) else []


def _write_records_unlocked(records: list[dict]) -> None:
    _ensure_store_ready()
    temp_file = f"{_RECORDS_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as handle:
        json.dump(records, handle, ensure_ascii=False, indent=2)
    os.replace(temp_file, _RECORDS_FILE)


def _normalize_file_name(file_name: str) -> str:
    return Path(file_name or "upload.bin").name.strip() or "upload.bin"


def _normalize_content_type(content_type: str | None) -> str:
    normalized = (content_type or "").strip().lower()
    return normalized or "application/pdf"


def _derive_agent_ingest_status(record: dict) -> str:
    kb_status = (record.get("kb_ingest_status") or "").strip()
    ocr_status = (record.get("ocr_status") or "").strip()
    if _correction_needs_reingest(record):
        return "stale"
    if kb_status in {"succeeded", "indexed"}:
        return "completed"
    if kb_status in {"processing", "indexing"}:
        return "processing"
    if kb_status == "failed":
        return "failed"
    if ocr_status in {"needs_heavy_ocr", "ocr_model_not_configured"}:
        return ocr_status
    if ocr_status in {"needs_review", "reviewed", "text_extracted"}:
        return "pending"
    if ocr_status in {"extracting", "extracting_text", "ocr_processing", "processing"}:
        return "extracting_text"
    return "pending"


def _last_error(record: dict) -> str:
    return str(record.get("kb_error") or record.get("ocr_error") or "").strip()


def _has_correction(record: dict) -> bool:
    return bool(record.get("reviewed_file") or record.get("correction_file") or record.get("corrected_at"))


def _correction_needs_reingest(record: dict) -> bool:
    if not _has_correction(record):
        return False
    corrected_at = int(record.get("corrected_at") or 0)
    ingested_at = int(record.get("correction_ingested_at") or 0)
    if corrected_at and corrected_at > ingested_at:
        return True
    return (record.get("kb_ingest_status") or "").strip() not in {"succeeded", "indexed"}


def _agent_queryable(record: dict) -> bool:
    return (record.get("kb_ingest_status") or "").strip() in {"succeeded", "indexed"} and not _correction_needs_reingest(record)


def _status_node(
    key: str,
    label: str,
    description: str,
    status: str,
    *,
    timestamp: int | None = None,
    error: str | None = None,
) -> dict:
    return {
        "key": key,
        "label": label,
        "description": description,
        "status": status,
        "timestamp": timestamp,
        "error": error or "",
    }


def _build_status_timeline(record: dict) -> list[dict]:
    ocr_status = (record.get("ocr_status") or "").strip()
    kb_status = (record.get("kb_ingest_status") or "").strip()
    uploaded_at = record.get("uploaded_at")
    processed_at = record.get("processed_at")
    updated_at = record.get("updated_at") or processed_at or uploaded_at
    ocr_error = record.get("ocr_error")
    kb_error = record.get("kb_error")

    text_done = ocr_status in {"needs_review", "reviewed", "text_extracted", "succeeded", "indexed"}
    extracting = ocr_status in {"uploaded", "extracting", "extracting_text", "ocr_processing", "processing", ""}
    needs_ocr = ocr_status in {"needs_heavy_ocr", "ocr_model_not_configured"}
    ocr_failed = ocr_status in {"ocr_failed", "failed"}
    kb_processing = kb_status in {"processing", "indexing"}
    kb_done = kb_status in {"succeeded", "indexed"}
    kb_failed = kb_status == "failed"
    kb_waiting = text_done and kb_status in {"pending", ""}
    has_correction = _has_correction(record)
    correction_stale = _correction_needs_reingest(record)
    corrected_at = record.get("corrected_at")
    correction_ingested_at = record.get("correction_ingested_at")

    timeline = [
        _status_node(
            "uploaded",
            "已上传",
            "PDF 已上传并完成服务端登记，但不代表已进入知识库。",
            "done",
            timestamp=uploaded_at,
        )
    ]

    if ocr_failed:
        extract_status = "failed"
        extract_description = "文本提取失败，暂时无法进入知识库归档。"
    elif text_done:
        extract_status = "done"
        extract_description = "文本已提取，可以归档到知识库。"
    elif needs_ocr:
        extract_status = "skipped"
        extract_description = "轻量文本提取不足，当前正文不能可靠读取。"
    elif extracting:
        extract_status = "current"
        extract_description = "正在提取 PDF 文本，请等待处理完成。"
    else:
        extract_status = "pending"
        extract_description = "等待文本提取。"

    timeline.append(
        _status_node(
            "extracting_text",
            "文本提取中",
            extract_description,
            extract_status,
            timestamp=updated_at if extract_status in {"current", "failed"} else None,
            error=ocr_error if extract_status == "failed" else None,
        )
    )

    timeline.append(
        _status_node(
            "text_extracted",
            "文本已提取",
            "已获得可归档正文。" if text_done else "尚未获得可归档正文。",
            "done" if text_done else ("skipped" if needs_ocr or ocr_failed else "pending"),
            timestamp=processed_at if text_done else None,
        )
    )

    if needs_ocr:
        timeline.append(
            _status_node(
                "needs_heavy_ocr",
                "需要重型 OCR",
                (
                    "该文件 可能是扫描件，需要重型 OCR 后才能读取正文。"
                    if ocr_status == "needs_heavy_ocr"
                    else "该文件 可能是扫描件，当前未启用重型 OCR 模型。"
                ),
                "current",
                timestamp=processed_at or updated_at,
                error=ocr_error,
            )
        )

    if has_correction:
        timeline.append(
            _status_node(
                "correction_saved",
                "已保存人工校正",
                "人工校正已保存，但不会立即进入知识库。",
                "done",
                timestamp=corrected_at,
            )
        )
        timeline.append(
            _status_node(
                "waiting_reingest",
                "等待重新归档",
                "校正内容尚未归档，重新归档后 Agent 才会使用最新内容。"
                if correction_stale
                else "校正内容已完成重新归档。",
                "current" if correction_stale and not kb_processing and not kb_failed else "done",
                timestamp=corrected_at if correction_stale else correction_ingested_at,
            )
        )

    if kb_processing or kb_done or kb_failed:
        waiting_status = "done"
    elif needs_ocr or ocr_failed:
        waiting_status = "skipped"
    elif kb_waiting:
        waiting_status = "current"
    else:
        waiting_status = "pending"

    timeline.append(
        _status_node(
            "waiting_kb_ingest",
            "等待知识库归档",
            "文本已提取，但还没有进入知识库。" if kb_waiting else "等待满足归档条件。",
            waiting_status,
            timestamp=processed_at if kb_waiting else None,
        )
    )

    timeline.append(
        _status_node(
            "kb_ingesting",
            "知识库归档中",
            "正在将该文件 正文写入上传知识库。" if kb_processing else "尚未开始归档。",
            "current" if kb_processing else ("done" if kb_done else "failed" if kb_failed else "pending"),
            timestamp=updated_at if kb_processing or kb_failed else None,
            error=kb_error if kb_failed else None,
        )
    )

    if kb_failed:
        timeline.append(
            _status_node(
                "kb_failed",
                "重新归档失败" if has_correction else "归档失败",
                "校正内容重新归档失败，Agent 暂不可查询该文件。"
                if has_correction
                else "知识库归档失败，Agent 暂不可查询该文件。",
                "failed",
                timestamp=processed_at or updated_at,
                error=kb_error,
            )
        )

    timeline.append(
        _status_node(
            "kb_ingested",
            "校正内容已归档" if has_correction and kb_done and not correction_stale else "已归档知识库",
            (
                "校正内容已归档到上传知识库。"
                if has_correction and kb_done and not correction_stale
                else "该文件 已归档到上传知识库。"
                if kb_done
                else "该文件 尚未归档到知识库。"
            ),
            "done" if kb_done and not correction_stale else ("failed" if kb_failed else "pending"),
            timestamp=correction_ingested_at if has_correction and kb_done else processed_at if kb_done else None,
            error=kb_error if kb_failed else None,
        )
    )
    timeline.append(
        _status_node(
            "agent_queryable",
            "Agent 可查询",
            (
                "已归档，Agent 可以基于校正后的文件内容 回答问题。"
                if _agent_queryable(record) and has_correction
                else "已归档，Agent 可以基于该文件 回答问题。"
                if _agent_queryable(record)
                else "Agent 还不能基于该文件 回答问题。"
            ),
            "done" if _agent_queryable(record) else ("failed" if kb_failed else "pending"),
            timestamp=correction_ingested_at if has_correction and _agent_queryable(record) else processed_at if _agent_queryable(record) else None,
            error=kb_error if kb_failed else None,
        )
    )
    return timeline


def _default_status_label(record: dict) -> str:
    ocr_status = (record.get("ocr_status") or "").strip()
    kb_status = (record.get("kb_ingest_status") or "").strip()
    if _correction_needs_reingest(record):
        return "已保存校正，等待重新归档"
    if _has_correction(record) and kb_status in {"succeeded", "indexed"}:
        return "确认内容已归档，Agent 可查询"
    if kb_status in {"succeeded", "indexed"}:
        return "已归档知识库，Agent 可查询"
    if kb_status == "failed":
        return "知识库归档失败"
    if kb_status in {"processing", "indexing"}:
        return "知识库归档中"
    if ocr_status == "reviewed":
        return "已确认内容，等待归档知识库"
    if ocr_status == "needs_review":
        return "已识别文本，等待管理员校对"
    if ocr_status in {"extracting", "ocr_processing"}:
        return "OCR 识别中"
    if ocr_status == "extracting_text":
        return "文本提取中"
    if ocr_status == "text_extracted":
        return "已提取文本，尚未归档知识库"
    if ocr_status == "needs_heavy_ocr":
        return "文本不足，需重型 OCR 后归档"
    if ocr_status == "ocr_model_not_configured":
        return "文本不足，需重型 OCR（当前未配置）"
    if ocr_status == "ocr_failed":
        return "OCR 失败"
    if ocr_status == "failed":
        return "处理失败"
    return "已上传，等待文本提取"


def _normalize_record(record: dict) -> dict:
    normalized = dict(record)
    normalized.setdefault("file_type", "application/pdf")
    normalized.setdefault("mime_type", normalized.get("file_type", "application/pdf"))
    normalized.setdefault("file_kind", "pdf" if normalized.get("file_type") == "application/pdf" else "image")
    normalized.setdefault("ocr_status", "uploaded")
    normalized.setdefault("ocr_error", None)
    normalized.setdefault("ocr_backend", None)
    normalized.setdefault("ocr_result_file", None)
    normalized.setdefault("structured_result_file", None)
    normalized.setdefault("kb_ingest_status", "pending")
    normalized.setdefault("kb_error", None)
    normalized.setdefault("kb_document_id", None)
    normalized.setdefault("kb_index_mode", "")
    normalized.setdefault("kb_source_file", None)
    normalized.setdefault("reviewed_file", normalized.get("correction_file"))
    normalized.setdefault("correction_file", None)
    normalized.setdefault("corrected_at", None)
    normalized.setdefault("correction_source", None)
    normalized.setdefault("correction_version", 0)
    normalized.setdefault("correction_preview", "")
    normalized.setdefault("correction_ingested_at", None)
    normalized.setdefault("processed_at", None)
    normalized.setdefault("updated_at", normalized.get("processed_at") or normalized.get("uploaded_at"))
    normalized.setdefault("result_preview", "")
    normalized["status_label"] = _default_status_label(normalized)
    normalized["agent_ingest_status"] = _derive_agent_ingest_status(normalized)
    return normalized


def _safe_read_json(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _build_public_record(record: dict, include_details: bool = False) -> dict:
    normalized = _normalize_record(record)
    payload = {
        "id": normalized["id"],
        "file_name": normalized["file_name"],
        "file_size": normalized["file_size"],
        "file_type": normalized["file_type"],
        "uploaded_at": normalized["uploaded_at"],
        "status_label": normalized.get("status_label") or _default_status_label(normalized),
        "file_url": f"/admin/knowledge-files/{normalized['id']}/file",
        "ocr_status": normalized["ocr_status"],
        "ocr_error": normalized["ocr_error"],
        "ocr_backend": normalized["ocr_backend"],
        "kb_ingest_status": normalized["kb_ingest_status"],
        "kb_error": normalized["kb_error"],
        "kb_document_id": normalized["kb_document_id"],
        "kb_index_mode": normalized.get("kb_index_mode", ""),
        "agent_ingest_status": normalized["agent_ingest_status"],
        "agent_query_ready": _agent_queryable(normalized),
        "agent_queryable": _agent_queryable(normalized),
        "knowledge_source_type": "uploaded_file",
        "upload_status": "uploaded",
        "extract_status": normalized["ocr_status"],
        "last_error": _last_error(normalized),
        "has_correction": _has_correction(normalized),
        "correction_source": normalized["correction_source"],
        "corrected_at": normalized["corrected_at"],
        "correction_version": normalized["correction_version"],
        "correction_preview": normalized["correction_preview"],
        "correction_ingested_at": normalized["correction_ingested_at"],
        "correction_needs_reingest": _correction_needs_reingest(normalized),
        "processed_at": normalized["processed_at"],
        "updated_at": normalized["updated_at"],
        "result_preview": normalized.get("result_preview", ""),
        "status_timeline": _build_status_timeline(normalized),
    }
    if include_details:
        structured_result_file = normalized.get("structured_result_file")
        if structured_result_file:
            payload["structured_result"] = _safe_read_json(
                _controlled_path("structured_results", structured_result_file)
            )
        else:
            payload["structured_result"] = None
        kb_source_file = normalized.get("kb_source_file")
        if kb_source_file:
            kb_source_path = _controlled_path("kb_docs", kb_source_file)
            try:
                with open(kb_source_path, "r", encoding="utf-8") as handle:
                    kb_text = handle.read()
            except OSError:
                kb_text = ""
        else:
            kb_text = ""
        payload["kb_text"] = kb_text
        payload["kb_markdown"] = kb_text
        correction_file = normalized.get("reviewed_file") or normalized.get("correction_file")
        if correction_file:
            folder = "reviewed_docs" if normalized.get("reviewed_file") else "corrections"
            correction_path = _controlled_path(folder, correction_file)
            try:
                with open(correction_path, "r", encoding="utf-8") as handle:
                    payload["correction_text"] = handle.read()
            except OSError:
                payload["correction_text"] = ""
        else:
            payload["correction_text"] = ""
        payload["next_action"] = (
            "请重新执行知识库归档，Agent 才会使用校正后的内容。"
            if _correction_needs_reingest(normalized)
            else "当前文件可用于 Agent 查询。"
            if _agent_queryable(normalized)
            else "请等待文本提取完成后执行知识库归档。"
        )
    return payload


def validate_pdf_upload(
    file_name: str,
    content_type: str | None,
    content_size: int,
    content_prefix: bytes | None = None,
) -> tuple[str, str]:
    normalized_name = _normalize_file_name(file_name)
    normalized_type = _normalize_content_type(content_type)
    suffix = Path(normalized_name).suffix.lower()

    if suffix not in _SUPPORTED_UPLOAD_TYPES:
        raise ValueError("仅支持上传文件、PNG、JPG、JPEG、WEBP 文件。")
    expected_type = _SUPPORTED_UPLOAD_TYPES[suffix]
    allowed_types = {expected_type, "application/octet-stream"}
    if suffix in {".jpg", ".jpeg"}:
        allowed_types.add("image/jpg")
    if normalized_type not in allowed_types:
        raise ValueError("仅支持上传文件、PNG、JPG、JPEG、WEBP 文件。")
    if content_size <= 0:
        raise ValueError("上传文件为空。")
    if content_size > ADMIN_PDF_MAX_FILE_SIZE:
        raise ValueError("文件过大，单文件最多 50MB。")
    if suffix == ".pdf" and content_prefix is not None and not content_prefix.lstrip().startswith(b"%PDF-"):
        raise ValueError("文件头校验失败，仅支持上传标准 PDF 文件。")

    return normalized_name, expected_type if normalized_type == "application/octet-stream" else normalized_type


def list_file_records_raw() -> list[dict]:
    with _STORE_LOCK:
        records = [_normalize_record(record) for record in _read_records_unlocked()]
    records.sort(key=lambda item: item.get("uploaded_at", 0), reverse=True)
    return records


def list_file_records() -> list[dict]:
    return [_build_public_record(record) for record in list_file_records_raw()]


def save_file_record(file_name: str, content_type: str | None, content: bytes) -> tuple[dict, bool]:
    normalized_name, normalized_type = validate_pdf_upload(
        file_name,
        content_type,
        len(content),
        content_prefix=content[:8],
    )
    file_hash = hashlib.sha256(content).hexdigest()

    with _STORE_LOCK:
        records = _read_records_unlocked()
        for existing in records:
            existing_hash = str(existing.get("file_hash", "")).strip()
            if existing_hash and existing_hash == file_hash:
                return _build_public_record(existing), True
            if (
                existing.get("file_name") == normalized_name
                and int(existing.get("file_size", 0)) == len(content)
            ):
                return _build_public_record(existing), True

        record_id = uuid4().hex
        suffix = Path(normalized_name).suffix.lower()
        stored_name = f"{record_id}{suffix}"
        stored_path = _controlled_path("files", stored_name)
        with open(stored_path, "wb") as handle:
            handle.write(content)

        record = {
            "id": record_id,
            "file_name": normalized_name,
            "file_size": len(content),
            "file_type": normalized_type,
            "mime_type": normalized_type,
            "file_kind": "pdf" if suffix == ".pdf" else "image",
            "uploaded_at": int(time.time() * 1000),
            "status_label": "已上传，等待识别",
            "stored_name": stored_name,
            "file_hash": file_hash,
            "ocr_status": "uploaded",
            "ocr_error": None,
            "ocr_backend": None,
            "ocr_result_file": None,
            "structured_result_file": None,
            "kb_ingest_status": "pending",
            "kb_error": None,
            "kb_document_id": None,
            "kb_index_mode": "",
            "agent_ingest_status": "pending",
            "kb_source_file": None,
            "reviewed_file": None,
            "correction_file": None,
            "corrected_at": None,
            "correction_source": None,
            "correction_version": 0,
            "correction_preview": "",
            "correction_ingested_at": None,
            "processed_at": None,
            "updated_at": int(time.time() * 1000),
            "result_preview": "",
        }
        records.insert(0, record)
        _write_records_unlocked(records)
        return _build_public_record(record), False


def get_file_record(record_id: str) -> dict | None:
    records = list_file_records_raw()
    for record in records:
        if record.get("id") == record_id:
            return record
    return None


def get_file_record_public(record_id: str) -> dict | None:
    record = get_file_record(record_id)
    return _build_public_record(record, include_details=True) if record else None


def update_file_record_fields(record_id: str, **fields) -> dict | None:
    updated_record: dict | None = None
    with _STORE_LOCK:
        records = _read_records_unlocked()
        for record in records:
            if record.get("id") != record_id:
                continue
            fields.setdefault("updated_at", int(time.time() * 1000))
            record.update(fields)
            record["agent_ingest_status"] = _derive_agent_ingest_status(record)
            if "status_label" not in fields:
                record["status_label"] = _default_status_label(record)
            if record.get("ocr_status") in _TERMINAL_OCR_STATUSES or record.get("kb_ingest_status") in _TERMINAL_KB_STATUSES:
                record["processed_at"] = int(time.time() * 1000)
            updated_record = _normalize_record(record)
            break
        if updated_record is not None:
            _write_records_unlocked(records)
    return updated_record


def save_file_processing_artifacts(
    record_id: str,
    *,
    raw_text: str,
    page_summaries: list[dict],
    structured_result: dict,
    kb_markdown: str = "",
) -> dict | None:
    ocr_result_file = f"{record_id}.txt"
    structured_result_file = f"{record_id}.json"
    kb_source_file = f"{record_id}.md" if (kb_markdown or "").strip() else None

    _ensure_store_ready()
    with open(_controlled_path("ocr_results", ocr_result_file), "w", encoding="utf-8") as handle:
        handle.write(raw_text or "")
    structured_payload = dict(structured_result)
    structured_payload["page_summaries"] = page_summaries[:50]
    with open(_controlled_path("structured_results", structured_result_file), "w", encoding="utf-8") as handle:
        json.dump(structured_payload, handle, ensure_ascii=False, indent=2)
    if kb_source_file:
        with open(_controlled_path("kb_docs", kb_source_file), "w", encoding="utf-8") as handle:
            handle.write(kb_markdown)

    return update_file_record_fields(
        record_id,
        ocr_result_file=ocr_result_file,
        structured_result_file=structured_result_file,
        kb_source_file=kb_source_file,
        result_preview=structured_payload.get("preview_text", "")[:4000],
    )


def save_file_user_correction(record_id: str, corrected_text: str) -> dict | None:
    normalized_text = (corrected_text or "").strip()
    if not normalized_text:
        raise ValueError("校正内容不能为空。")

    record = get_file_record(record_id)
    if not record:
        return None

    correction_file = f"{record_id}.md"
    corrected_at = int(time.time() * 1000)
    correction_version = int(record.get("correction_version") or 0) + 1
    _ensure_store_ready()
    with open(_controlled_path("reviewed_docs", correction_file), "w", encoding="utf-8") as handle:
        handle.write(normalized_text)

    structured_result_file = record.get("structured_result_file")
    if structured_result_file:
        structured_path = _controlled_path("structured_results", structured_result_file)
        structured_payload = _safe_read_json(structured_path) or {}
        structured_payload["corrected_result"] = {
            "text": normalized_text,
            "corrected_at": corrected_at,
            "correction_source": "user",
            "correction_version": correction_version,
        }
        with open(structured_path, "w", encoding="utf-8") as handle:
            json.dump(structured_payload, handle, ensure_ascii=False, indent=2)

    return update_file_record_fields(
        record_id,
        ocr_status="reviewed",
        reviewed_file=correction_file,
        correction_file=correction_file,
        corrected_at=corrected_at,
        correction_source="admin_review",
        correction_version=correction_version,
        correction_preview=normalized_text[:4000],
        kb_ingest_status="pending",
        kb_error=None,
        kb_document_id=None,
        kb_index_mode="",
        correction_ingested_at=None,
        status_label="已确认内容，等待知识库归档",
    )


def get_uploaded_file_path(record_id: str) -> tuple[str, dict] | None:
    record = get_file_record(record_id)
    if not record:
        return None
    stored_name = record.get("stored_name")
    if not stored_name:
        return None
    stored_path = _controlled_path("files", stored_name)
    if not os.path.exists(stored_path):
        return None
    return stored_path, record


def _safe_remove(path: str | None) -> None:
    if not path:
        return
    try:
        controlled = _controlled_path(os.path.relpath(path, ADMIN_UPLOAD_DIR))
    except ValueError:
        return
    if os.path.exists(controlled):
        try:
            os.remove(controlled)
        except OSError:
            pass


def delete_file_record(record_id: str) -> bool:
    files_to_remove: list[str] = []
    deleted = False

    with _STORE_LOCK:
        records = _read_records_unlocked()
        kept_records: list[dict] = []
        for record in records:
            if record.get("id") == record_id:
                deleted = True
                for folder_name, field_name in (
                    ("files", "stored_name"),
                    ("ocr_results", "ocr_result_file"),
                    ("structured_results", "structured_result_file"),
                    ("kb_docs", "kb_source_file"),
                    ("reviewed_docs", "reviewed_file"),
                    ("corrections", "correction_file"),
                ):
                    file_name = str(record.get(field_name, "")).strip()
                    if file_name:
                        files_to_remove.append(_controlled_path(folder_name, file_name))
                continue
            kept_records.append(record)
        if deleted:
            _write_records_unlocked(kept_records)

    for path in files_to_remove:
        _safe_remove(path)
    for folder_name in ("rendered_pages", "preprocessed"):
        folder_path = _controlled_path(folder_name, record_id)
        if os.path.isdir(folder_path):
            shutil.rmtree(folder_path, ignore_errors=True)
    return deleted


