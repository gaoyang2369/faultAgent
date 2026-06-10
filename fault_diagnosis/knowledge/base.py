import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from glob import glob
from typing import Any

from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config import (
    EMBEDDING_MODEL,
    FAISS_PATH,
    KB_BATCH_SIZE,
    KB_BUILD_MAX_DOCUMENTS,
    KB_CHUNK_OVERLAP,
    KB_CHUNK_SIZE,
    KB_EMBED_CACHE_PATH,
    KB_EMBED_TIMEOUT_SECONDS,
    KB_INCREMENTAL_BUILD,
    OLLAMA_BASE_URL,
)
from ..common.paths import PROJECT_ROOT

# 全局变量：存储当前的知识库检索器
db_retriever = None
KB_METADATA_FILENAME = "kb_meta.json"


def _resolve_project_path(path: str | None, default_value: str) -> str:
    target = path or default_value
    if os.path.isabs(target):
        return target
    return os.path.join(PROJECT_ROOT, target)


def _resolve_cache_path(cache_path: str | None, db_save_path: str) -> str:
    if cache_path:
        return _resolve_project_path(cache_path, KB_EMBED_CACHE_PATH)
    configured_cache = os.getenv("KB_EMBED_CACHE_PATH", "").strip()
    if configured_cache:
        return _resolve_project_path(configured_cache, configured_cache)
    return os.path.join(db_save_path, "embedding_cache.sqlite3")


def _progress(message: str, **fields: Any) -> None:
    detail = " ".join(f"{key}={value}" for key, value in fields.items())
    suffix = f" {detail}" if detail else ""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}{suffix}", flush=True)


def _knowledge_base_metadata_path(db_save_path: str) -> str:
    return os.path.join(db_save_path, KB_METADATA_FILENAME)


def _read_knowledge_base_metadata(db_save_path: str) -> dict[str, Any]:
    metadata_path = _knowledge_base_metadata_path(db_save_path)
    if not os.path.exists(metadata_path):
        return {}
    try:
        with open(metadata_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_knowledge_base_metadata(db_save_path: str, payload: dict[str, Any]) -> None:
    os.makedirs(db_save_path, exist_ok=True)
    with open(_knowledge_base_metadata_path(db_save_path), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _infer_build_mode(document_count: int | None, metadata: dict[str, Any]) -> tuple[str, str]:
    explicit_mode = metadata.get("build_mode")
    if explicit_mode in {"smoke", "full"}:
        return explicit_mode, "metadata"
    if document_count is None:
        return "unknown", "unknown"
    return ("smoke", "heuristic") if document_count <= 10 else ("full", "heuristic")


def _get_embeddings_model(timeout_seconds: int | None = None):
    timeout = timeout_seconds or KB_EMBED_TIMEOUT_SECONDS
    return OllamaEmbeddings(
        model=EMBEDDING_MODEL,
        base_url=OLLAMA_BASE_URL,
        sync_client_kwargs={"timeout": timeout},
        async_client_kwargs={"timeout": timeout},
    )


class CachedEmbeddings(Embeddings):
    """给 Ollama embeddings 增加本地 SQLite 缓存，减少重复向量化。"""

    def __init__(self, inner: Embeddings, cache_path: str):
        self.inner = inner
        self.cache_path = cache_path
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        self._init_cache()

    def _init_cache(self) -> None:
        with sqlite3.connect(self.cache_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    cache_key TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _cache_key(self, text: str) -> tuple[str, str]:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cache_key = hashlib.sha256(
            f"{OLLAMA_BASE_URL}\0{EMBEDDING_MODEL}\0{text_hash}".encode("utf-8")
        ).hexdigest()
        return cache_key, text_hash

    def _read_many(self, texts: list[str]) -> tuple[list[list[float] | None], list[int], list[str]]:
        results: list[list[float] | None] = [None] * len(texts)
        missing_indices: list[int] = []
        missing_texts: list[str] = []
        with sqlite3.connect(self.cache_path) as conn:
            for index, text in enumerate(texts):
                cache_key, _ = self._cache_key(text)
                row = conn.execute(
                    "SELECT embedding_json FROM embedding_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if row:
                    results[index] = json.loads(row[0])
                    continue
                missing_indices.append(index)
                missing_texts.append(text)
        return results, missing_indices, missing_texts

    def _write_many(self, texts: list[str], vectors: list[list[float]]) -> None:
        now = time.time()
        rows = []
        for text, vector in zip(texts, vectors):
            cache_key, text_hash = self._cache_key(text)
            rows.append((cache_key, EMBEDDING_MODEL, text_hash, json.dumps(vector), now))
        with sqlite3.connect(self.cache_path) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO embedding_cache
                    (cache_key, model, text_hash, embedding_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        results, missing_indices, missing_texts = self._read_many(texts)
        if missing_texts:
            vectors = self.inner.embed_documents(missing_texts)
            self._write_many(missing_texts, vectors)
            for index, vector in zip(missing_indices, vectors):
                results[index] = vector
        if any(vector is None for vector in results):
            raise RuntimeError("embedding 缓存返回数量与请求数量不一致")
        return [vector for vector in results if vector is not None]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def _get_cached_embeddings_model(
    db_save_path: str,
    timeout_seconds: int | None = None,
    cache_path: str | None = None,
):
    return CachedEmbeddings(
        _get_embeddings_model(timeout_seconds=timeout_seconds),
        _resolve_cache_path(cache_path, db_save_path),
    )


def has_knowledge_base_index(db_save_path=None) -> bool:
    db_save_path = _resolve_project_path(db_save_path, FAISS_PATH)
    index_file = os.path.join(db_save_path, "index.faiss")
    metadata_file = os.path.join(db_save_path, "index.pkl")
    return os.path.exists(index_file) and os.path.exists(metadata_file)


def load_vector_store(db_save_path=None, embeddings_model=None, timeout_seconds: int | None = None):
    """仅加载已存在的 FAISS 向量库，不触发重建。"""
    db_save_path = _resolve_project_path(db_save_path, FAISS_PATH)
    if not has_knowledge_base_index(db_save_path):
        return None
    embeddings_model = embeddings_model or _get_cached_embeddings_model(
        db_save_path,
        timeout_seconds=timeout_seconds,
    )
    return FAISS.load_local(
        db_save_path,
        embeddings_model,
        allow_dangerous_deserialization=True,
    )


def load_knowledge_base(db_save_path=None, timeout_seconds: int | None = None):
    """仅加载已存在的知识库索引，不触发重建。"""
    vector_store = load_vector_store(db_save_path, timeout_seconds=timeout_seconds)
    return vector_store.as_retriever() if vector_store else None


def get_knowledge_base_status(db_save_path=None, load_check: bool = False) -> dict[str, Any]:
    """返回知识库索引状态，供启动日志和健康检查使用。"""
    db_save_path = _resolve_project_path(db_save_path, FAISS_PATH)
    index_file = os.path.join(db_save_path, "index.faiss")
    metadata_file = os.path.join(db_save_path, "index.pkl")
    kb_meta = _read_knowledge_base_metadata(db_save_path)
    exists = has_knowledge_base_index(db_save_path)
    status: dict[str, Any] = {
        "path": db_save_path,
        "exists": exists,
        "index_file_exists": os.path.exists(index_file),
        "metadata_file_exists": os.path.exists(metadata_file),
        "kb_metadata_file_exists": os.path.exists(_knowledge_base_metadata_path(db_save_path)),
        "loadable": None,
        "document_count": None,
        "build_mode": "missing" if not exists else "unknown",
        "build_mode_source": "filesystem" if not exists else "unknown",
        "last_built_at": kb_meta.get("last_built_at"),
        "source_count": kb_meta.get("source_count"),
    }
    if not load_check or not exists:
        if not exists:
            status["detail"] = "未检测到 FAISS 索引"
        return status
    try:
        vector_store = load_vector_store(db_save_path, timeout_seconds=1)
        status["loadable"] = vector_store is not None
        if vector_store is not None:
            status["document_count"] = len(getattr(vector_store, "index_to_docstore_id", {}))
    except Exception as exc:
        status["loadable"] = False
        status["error"] = str(exc)[:300]
    build_mode, build_mode_source = _infer_build_mode(status["document_count"], kb_meta)
    status["build_mode"] = build_mode
    status["build_mode_source"] = build_mode_source
    if build_mode == "smoke":
        status["detail"] = "当前索引为 smoke index，仅用于链路验证，不代表全量知识库"
    elif build_mode == "full":
        status["detail"] = (
            "当前索引标记为 full index"
            if build_mode_source == "metadata"
            else "当前索引按 document_count 启发式判定为 full index，建议补齐元信息确认"
        )
    else:
        status["detail"] = "索引已存在，但当前缺少足够元信息判断其构建模式"
    return status


def _build_or_extend_vector_store(db, documents, embeddings_model):
    if db is None:
        return FAISS.from_documents(documents, embeddings_model)
    db.add_documents(documents)
    return db


def _ingest_documents_with_retry(db, documents, embeddings_model, single_doc_attempt=0, stats=None):
    try:
        result = _build_or_extend_vector_store(db, documents, embeddings_model)
        if stats is not None:
            stats["success"] = stats.get("success", 0) + len(documents)
        return result
    except Exception as e:
        if len(documents) <= 1:
            if single_doc_attempt < 2:
                _progress("单文档写入失败，准备重试", attempt=single_doc_attempt + 1, error=e)
                time.sleep(1)
                return _ingest_documents_with_retry(
                    db,
                    documents,
                    embeddings_model,
                    single_doc_attempt=single_doc_attempt + 1,
                    stats=stats,
                )
            _progress("单文档写入仍失败，已跳过", error=e)
            if stats is not None:
                stats["failed"] = stats.get("failed", 0) + len(documents)
            return db
        split_at = max(1, len(documents) // 2)
        _progress("批次写入失败，尝试拆分重试", batch=len(documents), error=e)
        db = _ingest_documents_with_retry(db, documents[:split_at], embeddings_model, stats=stats)
        return _ingest_documents_with_retry(db, documents[split_at:], embeddings_model, stats=stats)


def _assign_chunk_ids(documents) -> None:
    for index, doc in enumerate(documents):
        source = str(doc.metadata.get("source", ""))
        page = str(doc.metadata.get("page", ""))
        text_hash = hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()
        chunk_id = hashlib.sha256(f"{source}\0{page}\0{index}\0{text_hash}".encode("utf-8")).hexdigest()
        doc.metadata["kb_chunk_id"] = chunk_id


def _existing_chunk_ids(vector_store) -> set[str]:
    docstore = getattr(getattr(vector_store, "docstore", None), "_dict", {})
    chunk_ids = set()
    for doc in docstore.values():
        chunk_id = getattr(doc, "metadata", {}).get("kb_chunk_id")
        if chunk_id:
            chunk_ids.add(chunk_id)
    return chunk_ids


def create_knowledge_base(
    pdf_dir="pdfs",
    db_save_path=None,
    force_rebuild=False,
    batch_size: int | None = None,
    timeout_seconds: int | None = None,
    max_documents: int | None = None,
    incremental: bool | None = None,
    cache_path: str | None = None,
):
    """创建或加载本地知识库向量库（支持批量、超时、小样本和增量构建）。"""
    started_at = time.perf_counter()
    pdf_dir = _resolve_project_path(pdf_dir, "pdfs")
    db_save_path = _resolve_project_path(db_save_path, FAISS_PATH)
    batch_size = batch_size or KB_BATCH_SIZE
    timeout_seconds = timeout_seconds or KB_EMBED_TIMEOUT_SECONDS
    max_documents = KB_BUILD_MAX_DOCUMENTS if max_documents is None else max_documents
    incremental = KB_INCREMENTAL_BUILD if incremental is None else incremental

    if not force_rebuild and not incremental and has_knowledge_base_index(db_save_path):
        retriever = load_knowledge_base(db_save_path, timeout_seconds=timeout_seconds)
        if retriever is not None:
            _progress("已加载现有知识库", path=db_save_path)
            return retriever
        _progress("现有知识库加载失败，将重新创建", path=db_save_path)

    if not os.path.exists(pdf_dir):
        raise FileNotFoundError(f"PDF文件夹不存在：{pdf_dir}，无法创建知识库")

    pdf_paths = glob(os.path.join(pdf_dir, "**", "*.pdf"), recursive=True)
    if not pdf_paths:
        raise FileNotFoundError(f"PDF文件夹中未找到任何PDF文件：{pdf_dir}")

    _progress(
        "开始构建知识库",
        pdf_count=len(pdf_paths),
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
        max_documents=max_documents or "unlimited",
        incremental=incremental,
        output=db_save_path,
    )

    all_docs = []
    failed_pdfs = 0
    for pdf_path in pdf_paths:
        try:
            loader = PyPDFLoader(pdf_path)
            docs = loader.load()
            for doc in docs:
                doc.metadata["source"] = pdf_path
            all_docs.extend(docs)
            _progress("已加载 PDF", path=pdf_path, pages=len(docs))
        except Exception as e:
            failed_pdfs += 1
            _progress("加载 PDF 失败，已跳过", path=pdf_path, error=e)

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=KB_CHUNK_SIZE,
        chunk_overlap=KB_CHUNK_OVERLAP,
        length_function=len,
    )
    texts = text_splitter.split_documents(all_docs)
    _assign_chunk_ids(texts)

    if max_documents and max_documents > 0:
        texts = texts[:max_documents]

    if not texts:
        raise ValueError("没有找到可处理的文本内容，请检查PDF文件是否有内容")

    embeddings_model = _get_cached_embeddings_model(
        db_save_path,
        timeout_seconds=timeout_seconds,
        cache_path=cache_path,
    )
    db = None
    skipped_documents = 0
    if incremental and has_knowledge_base_index(db_save_path):
        db = load_vector_store(db_save_path, embeddings_model=embeddings_model)
        existing_ids = _existing_chunk_ids(db) if db is not None else set()
        before = len(texts)
        texts = [doc for doc in texts if doc.metadata.get("kb_chunk_id") not in existing_ids]
        skipped_documents = before - len(texts)
        _progress("增量构建已过滤既有 chunks", skipped=skipped_documents, pending=len(texts))
        if not texts and db is not None:
            _progress("没有新增 chunks，跳过写入", output=db_save_path)
            return db.as_retriever()

    stats = {"success": 0, "failed": 0}
    total_batches = (len(texts) + batch_size - 1) // batch_size
    _progress(
        "开始写入向量库",
        total_documents=len(texts),
        total_batches=total_batches,
        skipped=skipped_documents,
        failed_pdfs=failed_pdfs,
    )

    for batch_index, start in enumerate(range(0, len(texts), batch_size), start=1):
        batch_texts = texts[start:start + batch_size]
        batch_started = time.perf_counter()
        db = _ingest_documents_with_retry(db, batch_texts, embeddings_model, stats=stats)
        _progress(
            "批次完成",
            batch=f"{batch_index}/{total_batches}",
            batch_documents=len(batch_texts),
            success=stats["success"],
            failed=stats["failed"],
            elapsed=f"{time.perf_counter() - started_at:.1f}s",
            batch_elapsed=f"{time.perf_counter() - batch_started:.1f}s",
        )

    if db is None:
        raise ValueError("向量库创建失败，请检查PDF文件内容和嵌入模型")

    os.makedirs(db_save_path, exist_ok=True)
    db.save_local(db_save_path)
    build_mode = "smoke" if max_documents and max_documents > 0 else "full"
    _write_knowledge_base_metadata(
        db_save_path,
        {
            "schema_version": 1,
            "build_mode": build_mode,
            "document_count": len(getattr(db, "index_to_docstore_id", {})),
            "source_count": len(pdf_paths),
            "last_built_at": datetime.now(timezone.utc).isoformat(),
            "incremental": incremental,
            "max_documents": max_documents or 0,
            "pdf_dir": pdf_dir,
        },
    )
    _progress(
        "知识库构建完成",
        pdf_count=len(pdf_paths),
        success=stats["success"],
        failed=stats["failed"],
        elapsed=f"{time.perf_counter() - started_at:.1f}s",
        build_mode=build_mode,
        output=db_save_path,
    )
    return db.as_retriever()


def init_knowledge_base(build_if_missing=True):
    """初始化知识库（项目启动时调用）。"""
    global db_retriever
    try:
        if build_if_missing:
            db_retriever = create_knowledge_base()
        else:
            db_retriever = load_knowledge_base()
    except FileNotFoundError as e:
        _progress("初始化警告", error=f"{e}（请在pdfs文件夹中放入PDF后重新初始化）")
        db_retriever = None
    except ValueError as e:
        _progress("初始化失败", error=e)
        db_retriever = None
    except Exception as e:
        _progress("初始化失败", error=e)
        db_retriever = None


def get_knowledge_retriever(build_if_missing=True, timeout_seconds: int | None = None):
    """懒加载知识库，避免 import 阶段触发外部依赖。"""
    global db_retriever
    if db_retriever is None:
        if build_if_missing or has_knowledge_base_index():
            if build_if_missing:
                init_knowledge_base(build_if_missing=True)
            else:
                db_retriever = load_knowledge_base(timeout_seconds=timeout_seconds)
    return db_retriever


def rebuild_knowledge_base(
    pdf_dir="pdfs",
    db_save_path=None,
    batch_size: int | None = None,
    timeout_seconds: int | None = None,
    max_documents: int | None = None,
    incremental: bool | None = None,
    cache_path: str | None = None,
    force_rebuild: bool = True,
):
    """独立的重建函数（重建知识库）。"""
    pdf_dir = _resolve_project_path(pdf_dir, "pdfs")
    db_save_path = _resolve_project_path(db_save_path, FAISS_PATH)
    global db_retriever
    try:
        db_retriever = create_knowledge_base(
            pdf_dir=pdf_dir,
            db_save_path=db_save_path,
            force_rebuild=force_rebuild,
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
            max_documents=max_documents,
            incremental=incremental,
            cache_path=cache_path,
        )
        pdf_count = len(glob(os.path.join(pdf_dir, "**", "*.pdf"), recursive=True))
        build_mode = "smoke" if max_documents and max_documents > 0 else "full"
        return f"✅ 知识库构建成功（模式：{build_mode}，包含{pdf_count}个PDF，输出路径：{db_save_path}，{time.strftime('%Y-%m-%d %H:%M:%S')}）"
    except Exception as e:
        return f"❌ 知识库构建失败：{str(e)}"
