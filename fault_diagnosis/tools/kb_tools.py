"""知识库检索工具。"""
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from ..config import KB_QUERY_TIMEOUT_SECONDS
from ..quality.evidence import register_evidence


def _invoke_retriever_with_timeout(db_retriever, query: str, timeout_seconds: int):
    import queue
    import threading

    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def worker():
        try:
            result_queue.put(("ok", db_retriever.invoke(query)))
        except Exception as exc:
            result_queue.put(("error", exc))

    thread = threading.Thread(target=worker, name="kb-query", daemon=True)
    thread.start()
    try:
        status, payload = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise TimeoutError from exc
    if status == "error":
        raise payload
    return payload


class KnowledgeBaseQuerySchema(BaseModel):
    query: str = Field(description="检索查询字符串，需明确具体")


def _format_doc_result(doc) -> str:
    metadata = getattr(doc, "metadata", {}) or {}
    source_type = metadata.get("source_type", "knowledge_base")
    file_name = metadata.get("file_name", "")
    file_id = metadata.get("file_id") or metadata.get("uploaded_pdf_id") or ""
    extract_backend = metadata.get("extract_backend") or metadata.get("ocr_backend") or ""
    source_label = "上传PDF知识库" if source_type == "uploaded_pdf" else "基础PDF知识库"
    page_label = metadata.get("page", "未知")
    extra_lines = [f"来源：{source_label}"]
    if file_name:
        extra_lines.append(f"来源文件：{file_name}")
    if file_id:
        extra_lines.append(f"file_id：{file_id}")
    if source_type:
        extra_lines.append(f"source_type：{source_type}")
    if extract_backend:
        extra_lines.append(f"extract_backend：{extract_backend}")
    if metadata.get("corrected"):
        extra_lines.append("corrected：true")
        if metadata.get("correction_source"):
            extra_lines.append(f"correction_source：{metadata.get('correction_source')}")
    extra_lines.append(f"来源页码：{page_label}")
    extra_lines.append(f"文档片段：{doc.page_content}")
    return "\n".join(extra_lines)


@tool(args_schema=KnowledgeBaseQuerySchema)
def query_knowledge_base(query: str) -> str:
    """
    查询本地知识库获取元信息（故障代码含义）。

    使用场景：
    - 需要查询故障代码含义、触发原因、处理步骤（如：F01002的触发原因、处理步骤）

    注意：知识库仅包含元信息，不包含实际数据。
    """
    try:
        from ..knowledge.base import get_knowledge_retriever, has_knowledge_base_index

        base_docs = []
        uploaded_docs = []
        base_error = ""
        uploaded_error = ""
        has_base_index = has_knowledge_base_index()
        try:
            from ..knowledge.uploaded_pdf_kb import (
                has_uploaded_pdf_corpus,
                has_uploaded_pdf_index,
                load_uploaded_pdf_retriever,
                query_uploaded_pdf_corpus,
            )

            has_uploaded_index = has_uploaded_pdf_index()
            has_uploaded_corpus = has_uploaded_pdf_corpus()
        except Exception:
            has_uploaded_index = False
            has_uploaded_corpus = False

        if not has_base_index and not has_uploaded_index and not has_uploaded_corpus:
            return (
                "知识库尚未预构建，当前请求不会在线触发全量建库。"
                "请先执行 `python rebuild_kb.py` 生成本地向量索引后再重试。"
            )
        timeout_seconds = KB_QUERY_TIMEOUT_SECONDS
        if has_base_index:
            db_retriever = get_knowledge_retriever(
                build_if_missing=False,
                timeout_seconds=timeout_seconds,
            )
            if db_retriever is None:
                base_error = "错误：本地知识库索引存在但加载失败，请检查 faiss_db 后重试。"
            else:
                try:
                    base_docs = _invoke_retriever_with_timeout(db_retriever, query, timeout_seconds)
                except TimeoutError:
                    base_error = f"超时：知识库检索超过 {timeout_seconds}s 未返回，请稍后重试或缩小查询范围。"
                except Exception as e:
                    base_error = f"知识库检索失败：{e}"

        if has_uploaded_index:
            try:
                uploaded_retriever = load_uploaded_pdf_retriever(timeout_seconds=timeout_seconds)
                if uploaded_retriever is not None:
                    uploaded_docs = _invoke_retriever_with_timeout(uploaded_retriever, query, timeout_seconds)
                else:
                    uploaded_error = "上传 PDF 知识库索引存在但加载失败。"
            except TimeoutError:
                uploaded_error = f"超时：上传 PDF 知识库检索超过 {timeout_seconds}s 未返回，请稍后重试。"
            except Exception as e:
                uploaded_error = f"上传 PDF 知识库检索失败：{e}"
        if has_uploaded_corpus and not uploaded_docs and not base_docs:
            uploaded_docs = query_uploaded_pdf_corpus(query, limit=3)

        combined = []
        seen_keys = set()
        for doc in list(uploaded_docs) + list(base_docs):
            if isinstance(doc, dict):
                key = (doc.get("preview", ""), doc.get("file_name", ""), doc.get("source_type", ""))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                combined.append(doc)
                continue
            key = (
                getattr(doc, "page_content", ""),
                tuple(sorted((getattr(doc, "metadata", {}) or {}).items())),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            combined.append(doc)

        if not combined:
            if uploaded_error and not base_docs:
                return uploaded_error
            if base_error and not uploaded_docs:
                return base_error
            return "未检索到相关知识片段，请尝试缩小问题范围或更换关键词。"

        rendered = []
        for idx, doc in enumerate(combined[:4], start=1):
            if isinstance(doc, dict):
                file_name = doc.get("file_name", "")
                file_id = doc.get("file_id", "")
                register_evidence(
                    evidence_type="rag",
                    source=f"uploaded_pdf:{file_id or file_name or 'unknown'}",
                    title=f"上传 PDF 知识库命中文档片段 {idx}",
                    summary=doc.get("preview", ""),
                    raw_ref=f"file_id={file_id};file_name={file_name};query={query}",
                    stage="retrieve",
                    tool_name="query_knowledge_base",
                    metadata={"file_id": file_id, "file_name": file_name, "query": query},
                )
                rendered.append(
                    "来源：上传PDF知识库\n"
                    f"来源文件：{doc.get('file_name', '')}\n"
                    f"file_id：{doc.get('file_id', '')}\n"
                    f"source_type：{doc.get('source_type', 'uploaded_pdf')}\n"
                    f"extract_backend：{doc.get('extract_backend') or doc.get('ocr_backend', '')}\n"
                    f"corrected：{'true' if doc.get('corrected') else 'false'}\n"
                    f"correction_source：{doc.get('correction_source', '')}\n"
                    f"文档片段：{doc.get('preview', '')}"
                )
                continue
            metadata = getattr(doc, "metadata", {}) or {}
            page = metadata.get("page", "未知")
            source_type = metadata.get("source_type", "knowledge_base")
            register_evidence(
                evidence_type="rag",
                source=f"{source_type}:page_{page}",
                title=f"知识库命中文档片段 {idx}",
                summary=getattr(doc, "page_content", ""),
                raw_ref=f"page={page};query={query}",
                stage="retrieve",
                tool_name="query_knowledge_base",
                metadata={"page": page, "query": query, **metadata},
            )
            rendered.append(_format_doc_result(doc))
        return "\n\n".join(rendered)
    except ImportError:
        return "错误：知识库模块未找到"
