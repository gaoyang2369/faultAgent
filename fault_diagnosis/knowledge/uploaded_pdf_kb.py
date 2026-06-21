"""管理员上传 PDF 的独立知识库索引。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..repositories.admin_pdf_registry_storage import list_pdf_records_raw
from ..config import (
    ADMIN_UPLOAD_DIR,
    KB_CHUNK_OVERLAP,
    KB_CHUNK_SIZE,
    UPLOADED_PDF_KB_ENABLE_VECTOR_INDEX,
    UPLOADED_PDF_KB_VECTOR_TIMEOUT_SECONDS,
)
from .base import (
    _assign_chunk_ids,
    _get_cached_embeddings_model,
    _ingest_documents_with_retry,
)

_UPLOADED_KB_ROOT = os.path.join(ADMIN_UPLOAD_DIR, "uploaded_pdf_kb")
_UPLOADED_KB_INDEX_DIR = os.path.join(_UPLOADED_KB_ROOT, "faiss")
_UPLOADED_KB_META_FILE = os.path.join(_UPLOADED_KB_ROOT, "kb_meta.json")
_UPLOADED_KB_CORPUS_FILE = os.path.join(_UPLOADED_KB_ROOT, "corpus.json")


def _ensure_parent_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _clear_uploaded_pdf_index() -> None:
    if os.path.exists(_UPLOADED_KB_ROOT):
        shutil.rmtree(_UPLOADED_KB_ROOT, ignore_errors=True)


def _record_kb_source_path(record: dict) -> str:
    correction_file = str(record.get("correction_file", "")).strip()
    if correction_file:
        correction_path = os.path.join(ADMIN_UPLOAD_DIR, "corrections", correction_file)
        if os.path.exists(correction_path):
            return correction_path
    kb_source_file = record.get("kb_source_file", "")
    return os.path.join(ADMIN_UPLOAD_DIR, "kb_docs", kb_source_file) if kb_source_file else ""


def _record_is_corrected(record: dict) -> bool:
    return bool(str(record.get("correction_file", "")).strip())


def _active_kb_records(records: list[dict] | None = None) -> list[dict]:
    source_records = records if records is not None else list_pdf_records_raw()
    active_records: list[dict] = []
    for record in source_records:
        if record.get("ocr_status") not in {"text_extracted", "succeeded"} and not _record_is_corrected(record):
            continue
        if record.get("kb_ingest_status") not in {"processing", "succeeded"}:
            continue
        kb_source_path = _record_kb_source_path(record)
        if not os.path.exists(kb_source_path):
            continue
        active_records.append(record)
    return active_records


def has_uploaded_pdf_index() -> bool:
    return os.path.exists(os.path.join(_UPLOADED_KB_INDEX_DIR, "index.faiss")) and os.path.exists(
        os.path.join(_UPLOADED_KB_INDEX_DIR, "index.pkl")
    )


def has_uploaded_pdf_corpus() -> bool:
    return os.path.exists(_UPLOADED_KB_CORPUS_FILE)


def load_uploaded_pdf_vector_store(timeout_seconds: int | None = None):
    if not has_uploaded_pdf_index():
        return None
    embeddings_model = _get_cached_embeddings_model(
        _UPLOADED_KB_INDEX_DIR,
        timeout_seconds=timeout_seconds,
    )
    return FAISS.load_local(
        _UPLOADED_KB_INDEX_DIR,
        embeddings_model,
        allow_dangerous_deserialization=True,
    )


def load_uploaded_pdf_retriever(timeout_seconds: int | None = None):
    vector_store = load_uploaded_pdf_vector_store(timeout_seconds=timeout_seconds)
    return vector_store.as_retriever(search_kwargs={"k": 3}) if vector_store is not None else None


def _write_meta(output_path: str, payload: dict) -> None:
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _write_corpus(active_records: list[dict], output_path: str) -> list[dict]:
    corpus: list[dict] = []
    for record in active_records:
        kb_source_path = _record_kb_source_path(record)
        with open(kb_source_path, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
        if not content:
            continue
        corrected = _record_is_corrected(record)
        corpus.append(
            {
                "id": record["id"],
                "file_name": record.get("file_name", ""),
                "source_type": "uploaded_pdf",
                "visibility": str(record.get("visibility") or "internal"),
                "allowed_roles": record.get("allowed_roles") or ["engineer", "admin"],
                "allowed_systems": record.get("allowed_systems") or [],
                "allowed_asset_ids": record.get("allowed_asset_ids") or [],
                "sensitivity": str(record.get("sensitivity") or "normal"),
                "extract_backend": record.get("ocr_backend", ""),
                "file_id": record["id"],
                "ocr_backend": record.get("ocr_backend", ""),
                "corrected": corrected,
                "correction_source": record.get("correction_source", "") if corrected else "",
                "correction_version": record.get("correction_version", 0) if corrected else 0,
                "source": kb_source_path,
                "content": content,
                "preview": content[:600],
            }
        )
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(corpus, handle, ensure_ascii=False, indent=2)
    return corpus


def query_uploaded_pdf_corpus(query: str, limit: int = 3) -> list[dict]:
    if not has_uploaded_pdf_corpus():
        return []
    try:
        with open(_UPLOADED_KB_CORPUS_FILE, "r", encoding="utf-8") as handle:
            corpus = json.load(handle)
    except Exception:
        return []
    if not isinstance(corpus, list):
        return []

    normalized_query = " ".join(query.split()).lower()
    keywords = [item.strip().lower() for item in query.split() if item.strip()]
    if not keywords and normalized_query:
        keywords = [normalized_query]

    scored = []
    for item in corpus:
        content = str(item.get("content", ""))
        preview = str(item.get("preview", "")) or content[:600]
        metadata_text = " ".join(
            str(item.get(field_name, ""))
            for field_name in ("file_name", "file_id", "id", "source_type")
        )
        haystack = " ".join(f"{metadata_text}\n{content}".split()).lower()
        score = 0
        if normalized_query and normalized_query in haystack:
            score += max(10, len(normalized_query))
        score += sum(haystack.count(keyword) for keyword in keywords if keyword)
        if score <= 0 and normalized_query:
            fragments = {normalized_query[index:index + 2] for index in range(max(0, len(normalized_query) - 1))}
            score += sum(haystack.count(fragment) for fragment in fragments if fragment.strip())
        if score <= 0:
            continue
        scored.append(
            {
                "score": score,
                "file_name": item.get("file_name", ""),
                "preview": preview,
                "source_type": item.get("source_type", "uploaded_pdf"),
                "visibility": item.get("visibility", "internal"),
                "allowed_roles": item.get("allowed_roles") or ["engineer", "admin"],
                "allowed_systems": item.get("allowed_systems") or [],
                "allowed_asset_ids": item.get("allowed_asset_ids") or [],
                "sensitivity": item.get("sensitivity", "normal"),
                "file_id": item.get("file_id", ""),
                "extract_backend": item.get("extract_backend", ""),
                "ocr_backend": item.get("ocr_backend", ""),
                "corrected": bool(item.get("corrected", False)),
                "correction_source": item.get("correction_source", ""),
                "correction_version": item.get("correction_version", 0),
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


def rebuild_uploaded_pdf_knowledge_base(records: list[dict] | None = None) -> dict:
    active_records = _active_kb_records(records)
    if not active_records:
        _clear_uploaded_pdf_index()
        return {
            "record_count": 0,
            "chunk_count": 0,
            "index_exists": False,
            "mode": "empty",
            "record_ids": [],
        }

    documents: list[Document] = []
    for record in active_records:
        kb_source_path = _record_kb_source_path(record)
        with open(kb_source_path, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
        if not content:
            continue
        corrected = _record_is_corrected(record)
        documents.append(
            Document(
                page_content=content,
                metadata={
                    "source": kb_source_path,
                    "source_type": "uploaded_pdf",
                    "visibility": str(record.get("visibility") or "internal"),
                    "allowed_roles": record.get("allowed_roles") or ["engineer", "admin"],
                    "allowed_systems": record.get("allowed_systems") or [],
                    "allowed_asset_ids": record.get("allowed_asset_ids") or [],
                    "sensitivity": str(record.get("sensitivity") or "normal"),
                    "extract_backend": record.get("ocr_backend", ""),
                    "uploaded_pdf_id": record["id"],
                    "file_id": record["id"],
                    "ocr_backend": record.get("ocr_backend", ""),
                    "file_name": record.get("file_name", ""),
                    "corrected": corrected,
                    "correction_source": record.get("correction_source", "") if corrected else "",
                    "correction_version": record.get("correction_version", 0) if corrected else 0,
                },
            )
        )

    if not documents:
        if os.path.exists(_UPLOADED_KB_INDEX_DIR):
            shutil.rmtree(_UPLOADED_KB_INDEX_DIR, ignore_errors=True)
        return {
            "record_count": len(active_records),
            "chunk_count": 0,
            "index_exists": False,
            "mode": "lexical_corpus",
            "record_ids": [record["id"] for record in active_records],
        }

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=KB_CHUNK_SIZE,
        chunk_overlap=KB_CHUNK_OVERLAP,
        length_function=len,
    )
    chunks = splitter.split_documents(documents)
    _assign_chunk_ids(chunks)

    os.makedirs(_UPLOADED_KB_ROOT, exist_ok=True)
    corpus = _write_corpus(active_records, _UPLOADED_KB_CORPUS_FILE)

    build_mode = "lexical_corpus"
    vector_error = ""
    temp_root = f"{_UPLOADED_KB_ROOT}.tmp"
    if os.path.exists(temp_root):
        shutil.rmtree(temp_root, ignore_errors=True)
    if os.path.exists(_UPLOADED_KB_INDEX_DIR):
        shutil.rmtree(_UPLOADED_KB_INDEX_DIR, ignore_errors=True)

    if UPLOADED_PDF_KB_ENABLE_VECTOR_INDEX:
        os.makedirs(temp_root, exist_ok=True)
        temp_index_dir = os.path.join(temp_root, "faiss")
        os.makedirs(temp_index_dir, exist_ok=True)
        try:
            embeddings_model = _get_cached_embeddings_model(
                temp_index_dir,
                timeout_seconds=UPLOADED_PDF_KB_VECTOR_TIMEOUT_SECONDS,
            )
            db = _ingest_documents_with_retry(None, chunks, embeddings_model)
            if db is None:
                raise RuntimeError("上传 PDF 知识库构建失败。")
            db.save_local(temp_index_dir)
            os.makedirs(_UPLOADED_KB_ROOT, exist_ok=True)
            shutil.move(temp_index_dir, _UPLOADED_KB_INDEX_DIR)
            build_mode = "faiss"
        except Exception as exc:
            vector_error = str(exc)
            build_mode = "lexical_corpus"
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
    metadata_payload = {
        "schema_version": 1,
        "record_count": len(active_records),
        "chunk_count": len(chunks),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "mode": build_mode,
        "corpus_count": len(corpus),
    }
    if vector_error:
        metadata_payload["vector_error"] = vector_error[:500]
    _write_meta(_UPLOADED_KB_META_FILE, metadata_payload)
    return {
        "record_count": len(active_records),
        "chunk_count": len(chunks),
        "index_exists": build_mode == "faiss",
        "mode": build_mode,
        "record_ids": [record["id"] for record in active_records],
        "vector_error": vector_error,
    }


def reset_uploaded_pdf_knowledge_base() -> None:
    _clear_uploaded_pdf_index()


def get_uploaded_pdf_kb_status() -> dict:
    if not os.path.exists(_UPLOADED_KB_META_FILE):
        return {
            "exists": False,
            "path": _UPLOADED_KB_INDEX_DIR,
        }
    try:
        with open(_UPLOADED_KB_META_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        payload = {}
    payload["exists"] = has_uploaded_pdf_index()
    payload["path"] = _UPLOADED_KB_INDEX_DIR
    return payload
