"""公共知识检索 step。"""

from __future__ import annotations

import re
from typing import Callable

from ..contracts import DiagnosisRequest, KnowledgeStepArtifact

_FAULT_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]\d{3,5})(?:-[0-9/]+)?(?![A-Z0-9])", re.IGNORECASE)


def extract_fault_codes_from_text(text: str, *, limit: int = 5) -> list[str]:
    """Extract normalized base fault codes such as F1030 from SQL/tool text."""

    codes: list[str] = []
    for match in _FAULT_CODE_RE.finditer(text or ""):
        code = match.group(1).upper()
        if code not in codes:
            codes.append(code)
        if len(codes) >= limit:
            break
    return codes


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
    failure_markers = (
        "失败",
        "错误",
        "超时",
        "未检索到",
        "尚未预构建",
        "索引存在但加载失败",
    )
    success = bool(raw_output.strip()) and not any(marker in raw_output for marker in failure_markers)
    return KnowledgeStepArtifact(
        success=success,
        query=query,
        snippets=snippets,
        raw_output=raw_output,
        error=None if success else raw_output.strip() or fallback_error_message,
    )
