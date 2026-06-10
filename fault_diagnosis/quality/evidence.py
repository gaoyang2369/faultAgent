"""请求级证据注册表、结论抽取与证据映射工具。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..runtime.session_store import get_namespace
from ..common.utils import summarize_text_for_log

_REGISTRY_KEY = "__evidence_registry__"
_SQL_TOOL_NAMES = {
    "sql_db_query",
    "sql_inter",
    "sql_db_schema",
    "sql_db_list_tables",
}
_ARTIFACT_TOOL_NAMES = {
    "fig_inter": "chart",
    "save_report": "report",
    "save_html_report": "report",
    "create_work_order": "action",
}
_TABLE_NAME_RE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_ARTIFACT_PATH_RE = re.compile(
    r"([A-Za-z0-9._/\-]+\.(?:png|jpg|jpeg|gif|svg|webp|html|md))",
    re.IGNORECASE,
)
_TOOL_ARTIFACTS_KEY = "__tool_artifacts__"
_TOKEN_RE = re.compile(r"[一-鿿]{2,}|[A-Za-z][A-Za-z0-9_-]{1,}|[A-Z]?\d{3,}")
_FINDING_SPLIT_RE = re.compile(r"[。；;\n]+")
_LEADING_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*•]+|\d+[.)、])\s*")
_MARKDOWN_DECORATION_RE = re.compile(r"[*_`#>\[\]]+")
_SQL_IDENTIFIER_ALIASES = {
    "spindle_load": "主轴负载",
    "spindle_current": "主轴电流",
    "temperature": "温度",
    "temp": "温度",
    "alarm_code": "报警代码",
    "fault_code": "故障码",
    "device_id": "设备编号",
    "timestamp": "时间",
    "speed": "转速",
    "spindle_speed": "主轴转速",
    "vibration": "振动",
}
_STOPWORDS = {
    "建议",
    "需要",
    "可以",
    "进行",
    "检查",
    "处理",
    "说明",
    "结果",
    "可能",
    "根据",
    "当前",
    "数据",
    "问题",
    "设备",
    "系统",
    "分析",
    "故障",
    "and",
    "the",
    "this",
    "that",
    "with",
    "from",
    "into",
    "for",
    "are",
    "was",
    "were",
    "is",
    "not",
    "only",
    "page",
    "please",
    "check",
    "needs",
    "need",
}
_HIGH_SEVERITY_HINTS = (
    "停机",
    "中断",
    "高风险",
    "严重",
    "过载",
    "报警",
    "告警",
    "异常",
    "stop",
    "stopped",
    "interrupt",
    "critical",
    "severe",
    "overload",
    "alarm",
    "fault",
)
_MEDIUM_SEVERITY_HINTS = (
    "波动",
    "偏高",
    "偏低",
    "退化",
    "劣化",
    "超限",
    "fluctuation",
    "deviation",
    "elevated",
    "degraded",
    "warning",
)


def _empty_registry() -> dict[str, Any]:
    return {
        "records": [],
        "tool_index": {},
        "findings": [],
        "links": [],
    }


def _get_registry() -> dict[str, Any]:
    ns = get_namespace()
    registry = ns.get(_REGISTRY_KEY)
    if not isinstance(registry, dict):
        registry = _empty_registry()
        ns[_REGISTRY_KEY] = registry
    return registry


def clear_evidence_registry() -> None:
    get_namespace()[_REGISTRY_KEY] = _empty_registry()


def _next_evidence_id(prefix: str) -> str:
    return f"ev_{prefix}_{uuid4().hex[:10]}"


def _next_finding_id() -> str:
    return f"fd_{uuid4().hex[:10]}"


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _extract_sql_query(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        for key in ("query", "sql", "statement"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(tool_input, str):
        return tool_input.strip()
    return ""


def _extract_table_names(query: str) -> list[str]:
    if not query:
        return []
    names: list[str] = []
    for match in _TABLE_NAME_RE.findall(query):
        if match not in names:
            names.append(match)
    return names


def _extract_selected_columns(query: str) -> list[str]:
    if not query:
        return []
    matched = re.search(r"\bselect\b(?P<body>.*?)\bfrom\b", query, re.IGNORECASE | re.DOTALL)
    if not matched:
        return []
    body = matched.group("body")
    if not body or "*" in body:
        return []

    columns: list[str] = []
    for raw_item in body.split(","):
        item = raw_item.strip()
        if not item:
            continue
        item = re.split(r"\bas\b", item, flags=re.IGNORECASE)[0].strip()
        item = item.split(".")[-1].strip("`\"[] ")
        item = re.sub(r"\(.*?\)", "", item).strip()
        if not item:
            continue
        normalized = item.lower()
        if normalized not in columns:
            columns.append(normalized)
    return columns


def _humanize_sql_identifiers(identifiers: list[str]) -> list[str]:
    labels: list[str] = []
    for item in identifiers:
        normalized = _normalize_token(item)
        label = _SQL_IDENTIFIER_ALIASES.get(normalized)
        if label:
            if label not in labels:
                labels.append(label)
            continue
        cleaned = item.replace("_", " ").strip()
        if cleaned and cleaned not in labels:
            labels.append(cleaned)
    return labels


def _summarize_sql_output(tool_output: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(tool_output, dict):
        rows = tool_output.get("rows")
        row_count = len(rows) if isinstance(rows, list) else 0
        total_rows = tool_output.get("total_rows", row_count)
        truncated = bool(tool_output.get("truncated"))
        note = _compact_text(tool_output.get("note", ""))
        summary = f"SQL 查询返回 {row_count} 行"
        if isinstance(total_rows, int):
            summary += f"，total_rows={total_rows}"
        if truncated:
            summary += "，结果已截断"
        if note:
            summary += f"。备注：{note}"
        return summary, {
            "row_count": row_count,
            "total_rows": total_rows,
            "truncated": truncated,
            "note": note,
        }

    compact_output = _compact_text(tool_output)
    if not compact_output:
        compact_output = "SQL 工具返回空结果"
    return summarize_text_for_log(compact_output, limit=220), {
        "row_count": None,
        "total_rows": None,
        "truncated": False,
        "note": "",
    }


def _extract_artifact_path(tool_output: Any) -> str | None:
    if isinstance(tool_output, dict):
        for key in ("path", "file", "filename", "url"):
            value = tool_output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    text = _compact_text(tool_output)
    match = _ARTIFACT_PATH_RE.search(text)
    return match.group(1) if match else None


def _pop_tool_artifact_metadata(tool_name: str) -> dict[str, Any] | None:
    namespace = get_namespace()
    artifacts = namespace.get(_TOOL_ARTIFACTS_KEY)
    if not isinstance(artifacts, dict):
        return None
    bucket = artifacts.get(tool_name)
    if not isinstance(bucket, list) or not bucket:
        return None
    item = bucket.pop(0)
    return item if isinstance(item, dict) else None


def _summarize_chart_artifact(metadata: dict[str, Any], fallback_summary: str) -> str:
    figure_name = _compact_text(metadata.get("figure_name"))
    chart_type = _compact_text(metadata.get("chart_type"))
    dataframe_refs = metadata.get("dataframe_refs") or []
    row_count = metadata.get("row_count")
    ref_names = []
    for item in dataframe_refs:
        if isinstance(item, dict) and item.get("name"):
            ref_names.append(str(item["name"]))

    parts = []
    if figure_name:
        parts.append(f"Chart `{figure_name}`")
    else:
        parts.append("Chart artifact")
    if chart_type:
        parts.append(f"type={chart_type}")
    if ref_names:
        parts.append(f"dataframes={', '.join(ref_names[:3])}")
    if isinstance(row_count, int):
        parts.append(f"rows={row_count}")
    summary = "; ".join(parts).strip()
    return summary or fallback_summary


def _normalize_token(token: str) -> str:
    return token.strip().lower()


def _extract_keywords(text: str) -> list[str]:
    token_re = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{1,}|[A-Z]?\d{3,}")
    seen: list[str] = []
    for raw in token_re.findall(text or ""):
        token = _normalize_token(raw)
        if len(token) < 2:
            continue
        if token in _STOPWORDS:
            continue
        if token not in seen:
            seen.append(token)
    return seen


def _detect_severity(text: str) -> str | None:
    compact = _compact_text(text)
    if not compact:
        return None
    if any(hint in compact for hint in _HIGH_SEVERITY_HINTS):
        return "high"
    if any(hint in compact for hint in _MEDIUM_SEVERITY_HINTS):
        return "medium"
    return "low"


def _detect_confidence(match_score: int, evidence_count: int) -> str | None:
    if evidence_count <= 0:
        return "low"
    if match_score >= 4 or evidence_count >= 3:
        return "high"
    if match_score >= 2 or evidence_count >= 2:
        return "medium"
    return "low"


def register_evidence(
    *,
    evidence_type: str,
    source: str,
    title: str,
    summary: str,
    stage: str,
    tool_name: str | None = None,
    raw_ref: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = _get_registry()
    record = {
        "evidence_id": _next_evidence_id(evidence_type),
        "type": evidence_type,
        "source": source,
        "title": title,
        "summary": summary,
        "raw_ref": raw_ref,
        "stage": stage,
        "created_at": datetime.now().isoformat(),
        "metadata": metadata or {},
    }
    registry["records"].append(record)
    if tool_name:
        registry["tool_index"].setdefault(tool_name, []).append(record["evidence_id"])
    return record


def list_evidence_records() -> list[dict[str, Any]]:
    registry = _get_registry()
    return [dict(record) for record in registry.get("records", []) if isinstance(record, dict)]


def normalize_evidence_record(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") or {}
    evidence_kind = str(record.get("type") or "unknown")
    tables = metadata.get("tables") if isinstance(metadata.get("tables"), list) else []
    dataframe_refs = metadata.get("dataframe_refs") if isinstance(metadata.get("dataframe_refs"), list) else []
    action_guard = metadata.get("action_guard") if isinstance(metadata.get("action_guard"), dict) else None
    artifact_path = metadata.get("artifact_path")
    web_path = metadata.get("web_path")

    if evidence_kind == "sql":
        family = "telemetry"
        channel = "database"
    elif evidence_kind == "rag":
        family = "knowledge"
        channel = "retrieval"
    elif evidence_kind == "chart":
        family = "artifact"
        channel = "visualization"
    elif evidence_kind == "report":
        family = "artifact"
        channel = "reporting"
    elif evidence_kind == "action":
        family = "action"
        channel = "execution"
    else:
        family = "generic"
        channel = "generic"

    return {
        "evidence_id": record.get("evidence_id"),
        "kind": evidence_kind,
        "family": family,
        "channel": channel,
        "stage": record.get("stage"),
        "tool_name": metadata.get("tool_name"),
        "title": record.get("title"),
        "summary": record.get("summary"),
        "source_locator": web_path or artifact_path or record.get("source") or record.get("raw_ref"),
        "source_details": {
            "source": record.get("source"),
            "raw_ref": record.get("raw_ref"),
            "page": metadata.get("page"),
            "tables": tables,
            "query": metadata.get("query"),
            "artifact_path": artifact_path,
            "web_path": web_path,
        },
        "artifact": {
            "artifact_path": artifact_path,
            "web_path": web_path,
            "figure_name": metadata.get("figure_name"),
            "chart_type": metadata.get("chart_type"),
            "publication_status": metadata.get("publication_status"),
        },
        "governance": {
            "publication_status": metadata.get("publication_status"),
            "report_gate": metadata.get("report_gate"),
            "release_ready": metadata.get("release_ready"),
            "action_guard": action_guard,
        },
        "payload": {
            "query": metadata.get("query"),
            "tables": tables,
            "row_count": metadata.get("row_count"),
            "total_rows": metadata.get("total_rows"),
            "truncated": metadata.get("truncated"),
            "figure_name": metadata.get("figure_name"),
            "chart_type": metadata.get("chart_type"),
            "dataframe_refs": dataframe_refs,
            "work_order_id": metadata.get("work_order_id"),
            "publication_status": metadata.get("publication_status"),
            "report_gate": metadata.get("report_gate"),
            "release_ready": metadata.get("release_ready"),
        },
        "created_at": record.get("created_at"),
        "metadata": metadata,
    }


def list_normalized_evidence_records() -> list[dict[str, Any]]:
    return [normalize_evidence_record(record) for record in list_evidence_records()]


def consume_tool_evidence(tool_name: str) -> list[dict[str, Any]]:
    registry = _get_registry()
    evidence_ids = registry.get("tool_index", {}).pop(tool_name, [])
    if not evidence_ids:
        return []

    matched: list[dict[str, Any]] = []
    for evidence_id in evidence_ids:
        for record in registry.get("records", []):
            if isinstance(record, dict) and record.get("evidence_id") == evidence_id:
                matched.append(dict(record))
                break
    return matched


def build_evidence_preview(
    records: list[dict[str, Any]], limit: int = 3
) -> list[dict[str, Any]]:
    preview_items = []
    for record in records[:limit]:
        metadata = record.get("metadata") or {}
        normalized = normalize_evidence_record(record)
        preview_items.append(
            {
                "evidence_id": record.get("evidence_id"),
                "type": record.get("type"),
                "title": record.get("title"),
                "source": record.get("source"),
                "summary": summarize_text_for_log(record.get("summary"), limit=180),
                "stage": record.get("stage"),
                "chart_type": metadata.get("chart_type"),
                "figure_name": metadata.get("figure_name"),
                "artifact_path": metadata.get("artifact_path"),
                "publication_status": metadata.get("publication_status"),
                "action_guard": metadata.get("action_guard"),
                "report_gate": metadata.get("report_gate"),
                "release_ready": metadata.get("release_ready"),
                "kind": normalized.get("kind"),
                "family": normalized.get("family"),
                "channel": normalized.get("channel"),
                "tool_name": normalized.get("tool_name"),
                "source_locator": normalized.get("source_locator"),
            }
        )
    return preview_items


def _infer_sql_query(tool_name: str, tool_input: Any) -> str:
    query = _extract_sql_query(tool_input)
    if query:
        return query

    if tool_name == "sql_db_list_tables":
        return "SHOW TABLES;"

    if tool_name == "sql_db_schema" and isinstance(tool_input, dict):
        raw_names = tool_input.get("table_names") or tool_input.get("tables") or ""
        if isinstance(raw_names, str) and raw_names.strip():
            table_names = [item.strip() for item in re.split(r"[,，\s]+", raw_names) if item.strip()]
            if len(table_names) == 1:
                return f"DESCRIBE {table_names[0]};"
            if table_names:
                return "; ".join(f"DESCRIBE {name}" for name in table_names) + ";"

    return ""


def _extract_table_names_from_tool_input(tool_name: str, tool_input: Any, query: str) -> list[str]:
    names = _extract_table_names(query)
    if names:
        return names

    if tool_name == "sql_db_schema" and isinstance(tool_input, dict):
        raw_names = tool_input.get("table_names") or tool_input.get("tables") or ""
        if isinstance(raw_names, str) and raw_names.strip():
            return [item.strip() for item in re.split(r"[,，\s]+", raw_names) if item.strip()]

    return []


def _build_sql_evidence_title(tool_name: str, tables: list[str]) -> str:
    if tool_name == "sql_db_list_tables":
        return "SQL 表清单"
    if tool_name == "sql_db_schema":
        if len(tables) == 1:
            return f"数据表结构：{tables[0]}"
        if tables:
            return "多张数据表结构"
        return "数据表结构"
    if tables:
        return f"SQL 查询结果：{tables[0]}"
    return "SQL 查询结果"


def _build_sql_evidence_summary(
    tool_name: str,
    query: str,
    tables: list[str],
    tool_output: Any,
    stats: dict[str, Any],
) -> str:
    selected_columns = _extract_selected_columns(query)
    column_labels = _humanize_sql_identifiers(selected_columns)

    if tool_name == "sql_db_list_tables":
        if isinstance(tool_output, dict) and isinstance(tool_output.get("rows"), list):
            flattened: list[str] = []
            for row in tool_output["rows"][:8]:
                if isinstance(row, dict):
                    flattened.extend(str(value).strip() for value in row.values() if str(value).strip())
                elif isinstance(row, (list, tuple)):
                    flattened.extend(str(value).strip() for value in row if str(value).strip())
                else:
                    flattened.append(str(row).strip())
            flattened = [item for item in flattened if item]
            if flattened:
                return f"当前可用数据表包括：{', '.join(flattened[:8])}。"
        return "系统先查看了当前可用数据表。"

    if tool_name == "sql_db_schema":
        if tables:
            return f"系统查看了 {', '.join(tables[:3])} 的字段结构，用来确认后续能查询哪些数据。"
        return "系统查看了数据表结构，用来确认后续能查询哪些字段。"

    row_count = stats.get("row_count")
    total_rows = stats.get("total_rows")
    count = total_rows if isinstance(total_rows, int) else row_count
    if isinstance(count, int):
        if tables:
            summary = f"系统查询了 {', '.join(tables[:3])}，本次返回 {count} 条结果。"
        else:
            summary = f"系统执行了一次 SQL 查询，本次返回 {count} 条结果。"
        if column_labels:
            summary += f" 重点字段包括：{'、'.join(column_labels[:6])}。"
        return summary
    return summarize_text_for_log(_compact_text(tool_output) or query, limit=220)


def register_sql_tool_evidence(
    *,
    tool_name: str,
    tool_input: Any,
    tool_output: Any,
) -> dict[str, Any] | None:
    if tool_name not in _SQL_TOOL_NAMES:
        return None

    query = _infer_sql_query(tool_name, tool_input)
    if not query:
        return None

    tables = _extract_table_names_from_tool_input(tool_name, tool_input, query)
    _, stats = _summarize_sql_output(tool_output)
    summary = _build_sql_evidence_summary(tool_name, query, tables, tool_output, stats)
    source = f"sql:{','.join(tables)}" if tables else "sql:unknown_table"
    title = _build_sql_evidence_title(tool_name, tables)
    return register_evidence(
        evidence_type="sql",
        source=source,
        title=title,
        summary=summary,
        stage="collect",
        tool_name=tool_name,
        raw_ref=query,
        metadata={
            "tool_name": tool_name,
            "query": query,
            "tables": tables,
            **stats,
        },
    )


def register_chart_tool_evidence(
    *,
    tool_name: str,
    tool_output: Any,
) -> dict[str, Any] | None:
    if tool_name != "fig_inter":
        return None

    artifact_metadata = _pop_tool_artifact_metadata(tool_name) or {}
    artifact_path = (
        _extract_artifact_path(tool_output)
        or artifact_metadata.get("web_path")
        or artifact_metadata.get("artifact_path")
    )
    summary = _compact_text(tool_output)
    if not summary:
        summary = "Chart artifact generated."
    summary = _summarize_chart_artifact(artifact_metadata, summary)

    source = (
        artifact_metadata.get("web_path")
        or artifact_metadata.get("artifact_path")
        or artifact_path
        or "fig_inter:artifact"
    )
    title = (
        f"图表证据：{artifact_metadata.get('figure_name')}"
        if artifact_metadata.get("figure_name")
        else "图表证据"
    )
    return register_evidence(
        evidence_type="chart",
        source=source,
        title=title,
        summary=summarize_text_for_log(summary, limit=220),
        stage="analyze",
        tool_name=tool_name,
        raw_ref=artifact_path,
        metadata={
            "tool_name": tool_name,
            "artifact_path": artifact_path,
            **artifact_metadata,
        },
    )


def register_artifact_tool_evidence(
    *,
    tool_name: str,
    tool_output: Any,
) -> dict[str, Any] | None:
    artifact_type = _ARTIFACT_TOOL_NAMES.get(tool_name)
    if artifact_type is None:
        return None

    artifact_metadata = _pop_tool_artifact_metadata(tool_name) or {}
    artifact_path = _extract_artifact_path(tool_output)
    summary = _compact_text(tool_output)
    if not summary:
        summary = f"{tool_name} 已生成 {artifact_type} 产物"

    tool_output_metadata: dict[str, Any] = {}
    if isinstance(tool_output, dict):
        action_guard = tool_output.get("action_guard")
        if isinstance(action_guard, dict):
            tool_output_metadata["action_guard"] = action_guard
            tool_output_metadata["publication_status"] = (
                action_guard.get("publication_status")
                or action_guard.get("status")
            )
        for key in ("publication_status", "report_gate", "release_ready", "work_order_id", "web_path"):
            if tool_output.get(key) is not None:
                tool_output_metadata[key] = tool_output.get(key)

    source = (
        tool_output_metadata.get("web_path")
        or artifact_metadata.get("web_path")
        or artifact_metadata.get("artifact_path")
        or artifact_path
        or f"{tool_name}:artifact"
    )
    title = f"{artifact_type} 产物：{tool_name}"
    return register_evidence(
        evidence_type=artifact_type,
        source=source,
        title=title,
        summary=summarize_text_for_log(summary, limit=220),
        stage="report" if artifact_type in {"report", "action"} else "analyze",
        tool_name=tool_name,
        raw_ref=artifact_path,
        metadata={
            "tool_name": tool_name,
            "artifact_path": artifact_path,
            **artifact_metadata,
            **tool_output_metadata,
        },
    )


def register_finding(
    *,
    text: str,
    evidence_ids: list[str] | None = None,
    severity: str | None = None,
    confidence: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = _get_registry()
    finding = {
        "finding_id": _next_finding_id(),
        "text": text.strip(),
        "severity": severity,
        "confidence": confidence,
        "metadata": metadata or {},
    }
    registry["findings"].append(finding)
    if evidence_ids:
        registry["links"].append(
            {
                "finding_id": finding["finding_id"],
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
                "chart_evidence_ids": list(dict.fromkeys((metadata or {}).get("chart_evidence_ids", []))),
                "matched_keywords": (metadata or {}).get("matched_keywords", []),
                "match_score": (metadata or {}).get("match_score", 0),
            }
        )
    return finding


def list_findings() -> list[dict[str, Any]]:
    registry = _get_registry()
    return [dict(finding) for finding in registry.get("findings", []) if isinstance(finding, dict)]


def list_finding_links() -> list[dict[str, Any]]:
    registry = _get_registry()
    links = []
    for link in registry.get("links", []):
        if not isinstance(link, dict):
            continue
        links.append(
            {
                "finding_id": link.get("finding_id"),
                "evidence_ids": list(link.get("evidence_ids", [])),
                "chart_evidence_ids": list(link.get("chart_evidence_ids", [])),
                "matched_keywords": list(link.get("matched_keywords", [])),
                "match_score": link.get("match_score", 0),
            }
        )
    return links


def _split_findings_from_text(final_content: str) -> list[str]:
    fragments: list[str] = []
    for part in _FINDING_SPLIT_RE.split(final_content or ""):
        cleaned = _LEADING_LIST_PREFIX_RE.sub("", part).strip()
        if _is_actionable_finding_text(cleaned) and cleaned not in fragments:
            fragments.append(cleaned)
    return fragments[:5]


def _normalize_finding_text(text: str) -> str:
    compact = _compact_text(text)
    compact = _MARKDOWN_DECORATION_RE.sub("", compact)
    compact = compact.replace("**", "").replace("__", "")
    return compact.strip("：:;；- ")


def _is_actionable_finding_text(text: str) -> bool:
    cleaned = _normalize_finding_text(text)
    if len(cleaned) < 6:
        return False

    lowered = cleaned.lower()
    generic_prefixes = (
        "【结论】",
        "已完成 sql 查询",
        "当前诊断结论仍待确认",
        "当前仅能保留以下待确认判断",
        "回复完成",
        "evidence gate blocked",
        "建议下一步：",
    )
    if cleaned.startswith("【") and cleaned.endswith("】"):
        return False
    if any(lowered.startswith(item) for item in generic_prefixes):
        return False
    if len(_extract_keywords(cleaned)) == 0:
        return False
    return True


def _build_evidence_text(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") or {}
    dataframe_refs = metadata.get("dataframe_refs") or []
    dataframe_text_parts = []
    for item in dataframe_refs:
        if not isinstance(item, dict):
            continue
        dataframe_text_parts.append(_compact_text(item.get("name")))
        columns = item.get("columns") or []
        if isinstance(columns, list):
            dataframe_text_parts.extend(_compact_text(column) for column in columns[:8])
    return " ".join(
        [
            _compact_text(record.get("title")),
            _compact_text(record.get("summary")),
            _compact_text(record.get("source")),
            _compact_text(record.get("raw_ref")),
            _compact_text(metadata.get("query")),
            _compact_text(metadata.get("figure_name")),
            _compact_text(metadata.get("chart_type")),
            _compact_text(metadata.get("web_path")),
            _compact_text(metadata.get("report_path")),
            " ".join(part for part in dataframe_text_parts if part),
        ]
    ).strip()


def _match_evidence_for_finding(finding_text: str) -> tuple[list[str], list[str], int, list[str]]:
    finding_keywords = _extract_keywords(finding_text)
    if not finding_keywords:
        finding_keywords = _extract_keywords(_compact_text(finding_text))
    compact_finding_text = _compact_text(finding_text)

    scored_matches: list[tuple[int, dict[str, Any], list[str]]] = []
    for record in list_evidence_records():
        evidence_text = _build_evidence_text(record)
        evidence_keywords = _extract_keywords(evidence_text)
        compact_evidence_text = _compact_text(evidence_text)
        overlap = [token for token in finding_keywords if token in evidence_keywords]
        if not overlap:
            overlap = [
                token
                for token in finding_keywords
                if token and token in compact_evidence_text
            ]
        if not overlap and compact_finding_text and compact_evidence_text:
            overlap = [
                token
                for token in evidence_keywords
                if token and token in compact_finding_text
            ][:3]
        score = len(overlap)
        if record.get("type") in {"sql", "rag"} and score > 0:
            score += 1
        if record.get("stage") in {"collect", "retrieve"} and score > 0:
            score += 1
        if score > 0:
            scored_matches.append((score, record, overlap))

    scored_matches.sort(
        key=lambda item: (
            item[0],
            1 if item[1].get("type") == "sql" else 0,
            1 if item[1].get("type") == "rag" else 0,
        ),
        reverse=True,
    )

    if not scored_matches:
        fallback_records = list_evidence_records()[:2]
        fallback_ids = [record["evidence_id"] for record in fallback_records]
        chart_ids = [record["evidence_id"] for record in fallback_records if record.get("type") == "chart"]
        return fallback_ids, [], 0, chart_ids

    selected = scored_matches[:3]
    evidence_ids = [record["evidence_id"] for _, record, _ in selected]
    chart_evidence_ids = [
        record["evidence_id"]
        for _, record, _ in scored_matches
        if record.get("type") == "chart"
    ][:2]
    if not chart_evidence_ids:
        chart_evidence_ids = [
            record["evidence_id"]
            for _, record, _ in selected
            if record.get("type") == "chart"
        ]
    matched_keywords = list(dict.fromkeys(token for _, _, overlap in selected for token in overlap))
    top_score = selected[0][0]
    return evidence_ids, matched_keywords[:6], top_score, chart_evidence_ids


def create_grounded_findings(final_content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing_findings = list_findings()
    if existing_findings:
        return existing_findings, list_finding_links()

    if not final_content.strip():
        return [], []

    findings: list[dict[str, Any]] = []
    for item in _split_findings_from_text(final_content):
        evidence_ids, matched_keywords, match_score, chart_evidence_ids = _match_evidence_for_finding(item)
        findings.append(
            register_finding(
                text=item,
                evidence_ids=evidence_ids,
                severity=_detect_severity(item),
                confidence=_detect_confidence(match_score, len(evidence_ids)),
                metadata={
                    "matched_keywords": matched_keywords,
                    "match_score": match_score,
                    "chart_evidence_ids": chart_evidence_ids,
                },
            )
        )
    return findings, list_finding_links()


def summarize_evidence_coverage(
    findings: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    findings = findings if findings is not None else list_findings()
    links = links if links is not None else list_finding_links()
    records = records if records is not None else list_evidence_records()

    sql_count = sum(1 for record in records if isinstance(record, dict) and record.get("type") == "sql")
    rag_count = sum(1 for record in records if isinstance(record, dict) and record.get("type") == "rag")
    chart_records = [
        record for record in records if isinstance(record, dict) and record.get("type") == "chart"
    ]
    chart_count = len(chart_records)
    total_evidences = len(records)
    total_findings = len(findings)

    linked_findings = 0
    linked_chart_ids: set[str] = set()
    for finding in findings:
        finding_id = finding.get("finding_id")
        if not finding_id:
            continue
        matched = next(
            (
                item for item in links
                if isinstance(item, dict) and item.get("finding_id") == finding_id
            ),
            None,
        ) or {}
        evidence_ids = matched.get("evidence_ids") or []
        if isinstance(evidence_ids, list) and evidence_ids:
            linked_findings += 1
        chart_evidence_ids = matched.get("chart_evidence_ids") or []
        if isinstance(chart_evidence_ids, list):
            linked_chart_ids.update(str(item) for item in chart_evidence_ids if item)

    linked_chart_count = len(linked_chart_ids)
    orphan_chart_count = max(chart_count - linked_chart_count, 0)
    finding_binding_rate = round((linked_findings / total_findings) * 100) if total_findings else 0
    chart_coverage_rate = round((linked_chart_count / chart_count) * 100) if chart_count else 0

    score = (
        (25 if sql_count > 0 else 0)
        + (25 if rag_count > 0 else 0)
        + min(finding_binding_rate, 100) * 0.3
        + min(chart_coverage_rate, 100) * 0.2
    )
    rounded_score = round(score)

    if rounded_score >= 85:
        grade = "A"
    elif rounded_score >= 70:
        grade = "B"
    elif rounded_score >= 50:
        grade = "C"
    else:
        grade = "D"

    return {
        "grade": grade,
        "score": rounded_score,
        "total_evidences": total_evidences,
        "sql_count": sql_count,
        "rag_count": rag_count,
        "chart_count": chart_count,
        "linked_chart_count": linked_chart_count,
        "orphan_chart_count": orphan_chart_count,
        "total_findings": total_findings,
        "linked_findings": linked_findings,
        "finding_binding_rate": finding_binding_rate,
        "chart_coverage_rate": chart_coverage_rate,
        "metrics": [
            {"label": "SQL coverage", "value": "Yes" if sql_count > 0 else "No"},
            {"label": "RAG coverage", "value": "Yes" if rag_count > 0 else "No"},
            {"label": "Chart coverage", "value": f"{linked_chart_count}/{chart_count}"},
            {"label": "Finding binding", "value": f"{linked_findings}/{total_findings}"},
            {"label": "Orphan charts", "value": str(orphan_chart_count)},
            {"label": "Evidence count", "value": str(total_evidences)},
        ],
    }


def summarize_evidence_quality(
    findings: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    findings = findings if findings is not None else list_findings()
    links = links if links is not None else list_finding_links()
    records = records if records is not None else list_evidence_records()

    link_index = {link.get("finding_id"): link for link in links if isinstance(link, dict)}
    evidence_index = {
        record.get("evidence_id"): record
        for record in records
        if isinstance(record, dict) and record.get("evidence_id")
    }

    coverage_summary = summarize_evidence_coverage(
        findings=findings,
        links=links,
        records=records,
    )
    total_findings = len(findings)
    total_evidences = len(records)
    linked_findings = 0
    unsupported_findings = 0
    weak_match_findings = 0
    low_confidence_findings = 0
    medium_confidence_findings = 0
    high_confidence_findings = 0
    missing_evidence_ids: list[str] = []
    review_reasons: list[str] = []

    for finding in findings:
        finding_id = finding.get("finding_id")
        link = link_index.get(finding_id, {})
        evidence_ids = [
            evidence_id
            for evidence_id in (link.get("evidence_ids") or [])
            if evidence_id in evidence_index
        ]
        match_score = int(link.get("match_score") or 0)
        confidence = str(finding.get("confidence") or "unknown").lower()

        if evidence_ids and match_score > 0:
            linked_findings += 1
        else:
            unsupported_findings += 1
            for evidence_id in evidence_ids:
                if evidence_id not in missing_evidence_ids:
                    missing_evidence_ids.append(evidence_id)

        if match_score <= 1:
            weak_match_findings += 1

        if confidence == "low":
            low_confidence_findings += 1
        elif confidence == "medium":
            medium_confidence_findings += 1
        elif confidence == "high":
            high_confidence_findings += 1

    coverage_ratio = round(linked_findings / total_findings, 3) if total_findings else 0.0

    gate = "pass"
    risk_level = "low"
    recommended_action = "Current evidence is sufficient for response and report generation."

    if total_findings == 0:
        gate = "review_required"
        risk_level = "medium"
        review_reasons.append("No structured findings were extracted from the final answer.")
    if total_evidences == 0:
        gate = "blocked"
        risk_level = "high"
        review_reasons.append("No evidence records were captured in the request pipeline.")
    if unsupported_findings > 0:
        review_reasons.append(
            f"{unsupported_findings} finding(s) do not have a strong evidence match."
        )
        if unsupported_findings >= max(1, total_findings // 2):
            gate = "blocked"
            risk_level = "high"
        elif gate == "pass":
            gate = "review_required"
            risk_level = "medium"
    if low_confidence_findings > 0:
        review_reasons.append(
            f"{low_confidence_findings} finding(s) are still marked as low confidence."
        )
        if gate == "pass":
            gate = "review_required"
            risk_level = "medium"
    if weak_match_findings > 0 and gate == "pass":
        review_reasons.append(
            f"{weak_match_findings} finding(s) only have weak claim-to-evidence matching."
        )
        gate = "review_required"
        risk_level = "medium"

    coverage_grade = str(coverage_summary.get("grade") or "D")
    coverage_score = int(coverage_summary.get("score") or 0)
    release_ready = gate == "pass"
    if coverage_grade in {"C", "D"}:
        review_reasons.append(
            "Evidence coverage scorecard is below release threshold "
            f"(grade={coverage_grade}, score={coverage_score})."
        )
        release_ready = gate == "pass"
        if gate == "pass" and int(coverage_summary.get("rag_count") or 0) == 0:
            low_specificity_without_rag = any(
                len((link_index.get(finding.get("finding_id"), {}).get("matched_keywords") or [])) <= 1
                for finding in findings
                if isinstance(finding, dict)
            )
            if low_specificity_without_rag:
                gate = "blocked"
                risk_level = "high"
                release_ready = False

    if gate == "blocked":
        recommended_action = (
            "Downgrade the conclusion to a pending hypothesis, explain the evidence gap, "
            "and request more data or another retrieval/tool step before generating a final report."
        )
    elif gate == "review_required":
        recommended_action = (
            "Keep the answer conservative, label uncertain findings clearly, "
            "and prioritize adding stronger SQL/RAG/tool evidence before finalizing the report."
        )

    return {
        "gate": gate,
        "risk_level": risk_level,
        "total_findings": total_findings,
        "total_evidences": total_evidences,
        "linked_findings": linked_findings,
        "unsupported_findings": unsupported_findings,
        "weak_match_findings": weak_match_findings,
        "low_confidence_findings": low_confidence_findings,
        "medium_confidence_findings": medium_confidence_findings,
        "high_confidence_findings": high_confidence_findings,
        "coverage_ratio": coverage_ratio,
        "review_reasons": review_reasons,
        "recommended_action": recommended_action,
        "missing_evidence_ids": missing_evidence_ids,
        "coverage_summary": coverage_summary,
        "release_ready": release_ready,
    }


def apply_grounding_status_to_findings(
    findings: list[dict[str, Any]] | None,
    links: list[dict[str, Any]] | None,
    summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    findings = findings or []
    links = links or []
    gate = str((summary or {}).get("gate") or "pass").lower()
    link_index = {
        str(link.get("finding_id")): link
        for link in links
        if isinstance(link, dict) and link.get("finding_id")
    }

    adjusted: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        item = dict(finding)
        metadata = dict(item.get("metadata") or {})
        link = link_index.get(str(item.get("finding_id")), {})
        evidence_ids = [str(evidence_id) for evidence_id in (link.get("evidence_ids") or []) if evidence_id]
        match_score = int(link.get("match_score") or 0)
        confidence = str(item.get("confidence") or "").lower()

        is_supported = bool(evidence_ids) and match_score > 0
        needs_downgrade = (
            not is_supported
            or confidence == "low"
            or (gate == "blocked" and match_score <= 1)
        )

        original_text = str(item.get("text") or "").strip()
        display_text = original_text
        grounding_status = "grounded"
        if needs_downgrade and original_text:
            grounding_status = "pending"
            if not original_text.startswith("待确认："):
                display_text = f"待确认：{original_text}"

        metadata["grounding_status"] = grounding_status
        metadata["original_text"] = original_text
        metadata["display_text"] = display_text
        metadata["evidence_bound"] = is_supported
        metadata["match_score"] = match_score

        item["metadata"] = metadata
        item["text"] = display_text
        adjusted.append(item)

    return adjusted


def build_quality_gate_notice(summary: dict[str, Any] | None) -> str | None:
    if not isinstance(summary, dict):
        return None

    gate = str(summary.get("gate") or "pass")
    if gate == "pass":
        return None

    review_reasons = summary.get("review_reasons") or []
    reason_text = " ".join(str(item).strip() for item in review_reasons[:2] if str(item).strip())

    if gate == "blocked":
        base = "Evidence gate blocked: the current diagnosis is not sufficiently supported."
    else:
        base = "Evidence gate warning: some findings still need stronger support."

    if reason_text:
        return f"{base} {reason_text}"
    return base


def build_quality_gated_response(
    final_content: str,
    grounded_final_content: str,
    summary: dict[str, Any] | None,
    findings: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    if not isinstance(summary, dict):
        return final_content, grounded_final_content

    gate = str(summary.get("gate") or "pass")
    if gate == "pass":
        return final_content, grounded_final_content

    findings = findings or []
    hypotheses = [
        str(finding.get("text") or "").strip()
        for finding in findings[:3]
        if str(finding.get("text") or "").strip()
    ]
    notice = build_quality_gate_notice(summary) or ""
    action = str(summary.get("recommended_action") or "").strip()

    if gate == "blocked":
        lines = [
            "当前诊断结论仍待确认，现有证据不足以支撑确定性判断。",
        ]
        if notice:
            lines.append(f"证据门禁说明：{notice}")
        if hypotheses:
            lines.append("当前仅能保留以下待确认判断：")
            lines.extend([f"- {item}" for item in hypotheses])
        if action:
            lines.append(f"建议下一步：{action}")
        gated = "\n".join(lines)
        return gated, gated

    lines = [
        "以下诊断结论需要保守解读，当前证据仍不足以完全确认。",
    ]
    if notice:
        lines.append(f"证据门禁说明：{notice}")
    lines.append("")
    lines.append(grounded_final_content or final_content)
    if action:
        lines.append("")
        lines.append(f"建议下一步：{action}")
    gated_grounded = "\n".join(lines).strip()
    gated_final = gated_grounded
    return gated_final, gated_grounded


def build_grounded_final_content(
    final_content: str,
    findings: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
) -> str:
    findings = findings if findings is not None else list_findings()
    links = links if links is not None else list_finding_links()
    if not findings:
        return final_content

    link_index = {link["finding_id"]: link for link in links}
    grounded_lines: list[str] = []
    for finding in findings:
        link = link_index.get(finding["finding_id"], {})
        evidence_ids = link.get("evidence_ids", [])
        if evidence_ids:
            ids = ", ".join(evidence_ids)
            grounded_lines.append(f"{finding['text']} [证据: {ids}]")
        else:
            grounded_lines.append(finding["text"])
    return "\n".join(grounded_lines)
