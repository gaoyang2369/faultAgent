import argparse
import sys

from fault_diagnosis.knowledge.base import rebuild_knowledge_base


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建或增量更新 FAISS 知识库索引")
    parser.add_argument("--pdf-dir", default="pdfs", help="PDF 源文档目录")
    parser.add_argument("--db-save-path", default=None, help="FAISS 索引输出目录，默认读取 FAISS_PATH")
    parser.add_argument("--batch-size", type=int, default=None, help="每批写入的 chunk 数")
    parser.add_argument("--timeout", type=int, default=None, help="单次 Ollama embedding 请求超时秒数")
    parser.add_argument("--max-documents", type=int, default=None, help="最多处理的切分文档数，便于小样本验证")
    parser.add_argument("--cache-path", default=None, help="embedding SQLite 缓存路径")
    parser.add_argument("--incremental", action="store_true", help="加载已有索引并仅追加新增 chunk")
    parser.add_argument("--no-force-rebuild", action="store_true", help="不强制重建；已有索引时直接加载或增量追加")
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    result = rebuild_knowledge_base(
        pdf_dir=args.pdf_dir,
        db_save_path=args.db_save_path,
        batch_size=args.batch_size,
        timeout_seconds=args.timeout,
        max_documents=args.max_documents,
        incremental=args.incremental,
        cache_path=args.cache_path,
        force_rebuild=not args.no_force_rebuild and not args.incremental,
    )
    print(result)
