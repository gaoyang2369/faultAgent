"""Markdown section helpers for generated diagnosis reports."""

from __future__ import annotations

from collections import OrderedDict


def details_block(title: str, body: str) -> str:
    """Return a collapsible Markdown block understood by report_tools."""

    clean_title = str(title or "").strip() or "展开查看详情"
    clean_body = str(body or "").strip() or "暂无可展示数据。"
    return f":::details {clean_title}\n{clean_body}\n:::"


def merge_unique(items: list[str]) -> list[str]:
    """Preserve order while removing empty and duplicate strings."""

    merged: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def _table_cell(value: object) -> str:
    text = str(value or "").strip() or "-"
    return text.replace("\r\n", " ").replace("\n", " ").replace("|", r"\|")


def _simple_table(headers: list[str], rows: list[list[object]]) -> str:
    header_line = "| " + " | ".join(_table_cell(header) for header in headers) + " |"
    sep_line = "| " + " | ".join("---" for _ in headers) + " |"
    row_lines = ["| " + " | ".join(_table_cell(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header_line, sep_line, *row_lines])


def _compact_key_evidence(items: list[str], *, limit: int) -> list[str]:
    compacted: list[str] = []
    for item in merge_unique(items):
        text = item.strip()
        if text.startswith("温度正常"):
            continue
        if text.startswith("母线电压") and not any(keyword in text for keyword in ("异常", "超", "波动")):
            continue
        compacted.append(text)
        if len(compacted) >= limit:
            break
    return compacted or merge_unique(items)[:limit]


def _compact_acceptance_criteria(items: list[str], *, limit: int) -> list[str]:
    filtered = [
        item
        for item in merge_unique(items)
        if item.strip() not in {"温度和母线电压无新增异常"}
    ]
    return filtered[:limit]


def _compact_processing_steps(items: list[str], *, limit: int) -> list[str]:
    merged = merge_unique(items)
    preferred_keywords = (
        "备份当前参数快照",
        "核查单位制相关参数",
        "重新激活功能块",
        "复核速度设定与反馈链路",
        "检查负载波动",
        "检查供电",
        "检查散热",
    )
    compacted: list[str] = []
    for keyword in preferred_keywords:
        matched = next((item for item in merged if keyword in item and item not in compacted), "")
        if matched:
            compacted.append(matched)
        if len(compacted) >= limit:
            return compacted
    for item in merged:
        if item not in compacted:
            compacted.append(item)
        if len(compacted) >= limit:
            break
    return compacted


def _markdown_items(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) or "- 暂无"


def build_workorder_todo_markdown(
    *,
    title: object,
    workorder_type: object,
    risk_level: object,
    priority: object,
    priority_label: object,
    assignee_role: object,
    suggested_completion_window: object,
    key_evidence: list[str],
    processing_steps: list[str],
    acceptance_criteria: list[str],
    max_evidence: int = 3,
    max_steps: int = 4,
    max_criteria: int = 3,
) -> str:
    """Build a compact report section for pending work-order actions."""

    priority_text = " ".join(
        part for part in (str(priority or "").strip(), str(priority_label or "").strip()) if part
    ) or "-"
    summary_table = _simple_table(
        ["项目", "内容"],
        [
            ["建议动作", "生成维修工单"],
            ["工单标题", title],
            ["工单类型", workorder_type],
            ["风险 / 优先级", f"{str(risk_level or '-').strip()} / {priority_text}"],
            ["建议负责人", assignee_role],
            ["建议完成时间", suggested_completion_window],
        ],
    )
    evidence = _compact_key_evidence(key_evidence, limit=max_evidence)
    steps = _compact_processing_steps(processing_steps, limit=max_steps)
    criteria = _compact_acceptance_criteria(acceptance_criteria, limit=max_criteria)
    return "\n\n".join(
        [
            "### 待处理事项",
            summary_table,
            "#### 关键证据",
            _markdown_items(evidence),
            "#### 处理步骤",
            _markdown_items(steps),
            "#### 验收标准",
            _markdown_items(criteria),
        ]
    )


def _strip_known_prefix(text: str) -> tuple[str, str]:
    prefixes = (
        "立即确认",
        "参数/配置检查",
        "故障码处置",
        "运行验证",
        "关联排查",
        "闭环确认",
    )
    for prefix in prefixes:
        marker = f"{prefix}："
        if text.startswith(marker):
            return prefix, text[len(marker) :].strip()
    return "", text


def _recommendation_bucket(text: str) -> str:
    prefix, stripped = _strip_known_prefix(text)
    if prefix == "立即确认":
        return "immediate"
    if prefix in {"参数/配置检查", "故障码处置"}:
        return "code_check"
    if prefix in {"运行验证", "闭环确认"}:
        return "verification"
    if prefix == "关联排查":
        return "correlation"
    if "继续跟踪" in stripped or "持续观察" in stripped:
        return "observation"
    return "agent"


def build_sop_recommendations_markdown(
    generated_items: list[str],
    analysis_items: list[str],
    *,
    max_items_per_section: int = 5,
) -> str:
    """Group recommendation sentences into an operator-friendly SOP layout."""

    section_titles = OrderedDict(
        [
            ("immediate", "A. 立即确认"),
            ("code_check", "B. 参数 / 事件码检查"),
            ("verification", "C. 复测验证"),
            ("correlation", "D. 关联排查"),
            ("observation", "E. 持续观察"),
            ("agent", "Agent 补充建议"),
        ]
    )
    grouped: dict[str, list[str]] = {key: [] for key in section_titles}

    for item in merge_unique([*generated_items, *analysis_items]):
        bucket = _recommendation_bucket(item)
        _prefix, stripped = _strip_known_prefix(item)
        target = grouped.setdefault(bucket, [])
        if stripped and stripped not in target:
            target.append(stripped)

    sections: list[str] = []
    for bucket, title in section_titles.items():
        items = grouped.get(bucket, [])[:max_items_per_section]
        if not items:
            continue
        body = "\n".join(f"- {item}" for item in items)
        sections.append(f"### {title}\n{body}")

    return "\n\n".join(sections) or "- 暂无具体处置建议"


def build_capability_boundary_markdown(source_table: str) -> str:
    """Describe the report boundary without exposing unnecessary internals."""

    return (
        f"> 本报告基于 {source_table} 最新采样窗口、可用故障码知识库和预设诊断规则生成。"
        "结论用于辅助诊断，不直接替代现场联锁、安全规程和工程师确认。"
        "若设备处于调试、限速、点动或非自动运行状态，速度跟随偏差需结合现场控制模式重新判断。"
    )
