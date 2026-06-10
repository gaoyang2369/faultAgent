"""公共知识检索 step。"""

from __future__ import annotations

from typing import Callable

from ..contracts import DiagnosisRequest, KnowledgeStepArtifact


def build_default_knowledge_query(request: DiagnosisRequest, *extra_parts: str) -> str:
    """根据统一 request 生成默认知识检索语句。"""

    query_parts = [
        request.fault_code_hint or "",
        request.equipment_hint or "",
        request.metric_hint or "",
        request.analysis_goal,
        *extra_parts,
    ]
    return " ".join(part for part in query_parts if part).strip() or request.user_message


def build_knowledge_artifact(
    query: str,
    raw_output: str,
    *,
    fallback_error_message: str,
    snippets_limit: int = 3,
) -> KnowledgeStepArtifact:
    """将知识检索文本统一归一化为 artifact。"""

    snippets = [item.strip() for item in raw_output.split("\n\n") if item.strip()][:snippets_limit]
    success = bool(raw_output.strip()) and "失败" not in raw_output and "错误" not in raw_output
    return KnowledgeStepArtifact(
        success=success,
        query=query,
        snippets=snippets,
        raw_output=raw_output,
        error=None if success else raw_output.strip() or fallback_error_message,
    )
