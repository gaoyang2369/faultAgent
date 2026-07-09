"""公共知识检索 step。"""

from __future__ import annotations

import re
from typing import Callable

from ..contracts import DiagnosisRequest, FaultCodeEntry, KnowledgeStepArtifact

_FAULT_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]\d{4,})(?:-[0-9/]+)?(?![A-Z0-9])", re.IGNORECASE)
_FAULT_CODE_HEADER_RE = re.compile(r"^\s*([A-Z]\d{4,})(?:\s*\([A-Z]\))?\s+(.+?)\s*$", re.IGNORECASE)
_METADATA_RE = re.compile(r"^(来源文件|来源页码|检索方式)[：:]\s*(.*)$")
_FIELD_LABELS = {
    "信息类别": "category",
    "驱动对象": "drive_object",
    "组件": "component",
    "传播": "propagation",
    "反应": "reaction",
    "应答": "acknowledgement",
    "原因": "cause",
    "处理": "remedy",
    "参见": "references",
}
_FIELD_RE = re.compile(r"(信息类别|驱动对象|组件|传播|反应|应答|原因|处理|参见)\s*[：:]\s*")


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


def extract_fault_code_entries(
    raw_output: str,
    *,
    requested_codes: list[str] | None = None,
    limit: int = 5,
) -> list[FaultCodeEntry]:
    """Extract structured fault-code entries from RAG/manual chunks.

    The parser is intentionally deterministic: it only copies fields present in
    the retrieved manual text and never fills causes or remedies from outside
    the chunk.
    """

    requested = {code.upper() for code in requested_codes or [] if code}
    entries: list[FaultCodeEntry] = []
    for block in [item.strip() for item in (raw_output or "").split("\n\n") if item.strip()]:
        entry = _entry_from_block(block, requested_codes=requested)
        if entry is None:
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


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
    entries = extract_fault_code_entries(raw_output, requested_codes=extract_fault_codes_from_text(query))
    return KnowledgeStepArtifact(
        success=success,
        query=query,
        snippets=snippets,
        raw_output=raw_output,
        error=None if success else raw_output.strip() or fallback_error_message,
        fault_codes=[entry.code for entry in entries] or extract_fault_codes_from_text(raw_output),
        fault_code_entries=entries,
    )


def _entry_from_block(block: str, *, requested_codes: set[str]) -> FaultCodeEntry | None:
    metadata = _metadata_from_block(block)
    manual_text = _manual_text_from_block(block)
    header = _header_from_text(manual_text)
    if header is None:
        return None
    code, title = header
    fields = _fields_from_text(manual_text)
    match_type = _match_type(metadata.get("检索方式", ""), code=code, requested_codes=requested_codes)
    references = _split_references(fields.get("references", ""))
    return FaultCodeEntry(
        code=code,
        title=title,
        meaning=_meaning_from_title(title),
        cause=fields.get("cause", ""),
        remedy=fields.get("remedy", ""),
        category=fields.get("category", ""),
        drive_object=fields.get("drive_object", ""),
        component=fields.get("component", ""),
        propagation=fields.get("propagation", ""),
        reaction=fields.get("reaction", ""),
        acknowledgement=fields.get("acknowledgement", ""),
        references=references,
        source_file=metadata.get("来源文件", ""),
        page=metadata.get("来源页码", ""),
        match_type=match_type,
    )


def _metadata_from_block(block: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw_line in block.splitlines():
        matched = _METADATA_RE.match(raw_line.strip())
        if matched:
            metadata[matched.group(1)] = matched.group(2).strip()
    return metadata


def _manual_text_from_block(block: str) -> str:
    marker = "文档片段："
    if marker in block:
        return block.split(marker, 1)[1].strip()
    lines = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or _METADATA_RE.match(line) or line.startswith(("来源：", "source_type：", "file_id：", "extract_backend：")):
            continue
        if line.startswith("故障码："):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _header_from_text(text: str) -> tuple[str, str] | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched = _FAULT_CODE_HEADER_RE.match(line)
        if matched:
            return matched.group(1).upper(), _clean_value(matched.group(2))
    matched = _FAULT_CODE_RE.search(text)
    if not matched:
        return None
    code = matched.group(1).upper()
    rest = _clean_value(text[matched.end() :].splitlines()[0] if text[matched.end() :] else "")
    return code, rest


def _fields_from_text(text: str) -> dict[str, str]:
    normalized = re.sub(r"[ \t]+", " ", text.replace("\r", "\n"))
    matches = list(_FIELD_RE.finditer(normalized))
    fields: dict[str, str] = {}
    for index, matched in enumerate(matches):
        label = matched.group(1)
        start = matched.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        value = _clean_value(normalized[start:end])
        key = _FIELD_LABELS.get(label)
        if key and value:
            fields[key] = value
    return fields


def _match_type(raw_match_type: str, *, code: str, requested_codes: set[str]) -> str:
    if "精确" in raw_match_type and (not requested_codes or code.upper() in requested_codes):
        return "exact_match"
    return "candidate_match"


def _meaning_from_title(title: str) -> str:
    if "：" in title:
        return _clean_value(title.split("：", 1)[1])
    if ":" in title:
        return _clean_value(title.split(":", 1)[1])
    return title


def _split_references(value: str) -> list[str]:
    if not value:
        return []
    return [
        item.strip()
        for item in re.split(r"[,，、]\s*", value)
        if item.strip()
    ]


def _clean_value(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip(" \t\r\n；;"))
