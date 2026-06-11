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
