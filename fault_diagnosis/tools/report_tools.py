"""Markdown report generation tool for the single-agent diagnosis path."""

from __future__ import annotations

import os
import re
from html import escape
from datetime import datetime

from pydantic import BaseModel, Field

from ..common.paths import REPORTS_DIR

try:
    from langchain_core.tools import tool
except ImportError:  # pragma: no cover - local unit tests may not install LangChain
    def tool(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator

_SAFE_REPORT_STEM_RE = re.compile(r"[^A-Za-z0-9._-]+")
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
}


def _sanitize_report_filename(report_filename: str, extension: str) -> str:
    """将报告文件名归一化为不含扩展名的安全 basename。"""
    raw_name = str(report_filename or "").strip()
    extension = (extension or "").lstrip(".")

    if raw_name.lower().startswith("/reports/"):
        raw_name = raw_name[len("/reports/") :]
    elif raw_name.lower().startswith("reports/"):
        raw_name = raw_name[len("reports/") :]

    raw_name = raw_name.replace("\\", "/")
    raw_name = raw_name.rsplit("/", 1)[-1]
    if extension and raw_name.lower().endswith(f".{extension.lower()}"):
        raw_name = raw_name[: -(len(extension) + 1)]

    safe_name = _SAFE_REPORT_STEM_RE.sub("-", raw_name).strip(" .-_")
    safe_name = safe_name[:120].strip(" .-_")
    if safe_name.lower() in _WINDOWS_RESERVED_NAMES:
        safe_name = f"report-{safe_name}"
    return safe_name or "report"


def _resolve_report_path(filename: str) -> str:
    reports_dir = os.path.abspath(REPORTS_DIR)
    report_path = os.path.abspath(os.path.join(reports_dir, filename))
    if os.path.commonpath([reports_dir, report_path]) != reports_dir:
        raise ValueError("报告保存路径越界，已阻止写入。")
    return report_path


def _build_report_web_path(filename: str) -> str:
    return f"/reports/{filename}"


def _format_inline(text: str) -> str:
    escaped = escape(text, quote=True)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def _render_table(lines: list[str]) -> str:
    headers = _split_table_row(lines[0])
    rows = [_split_table_row(line) for line in lines[2:]]
    head_html = "".join(f"<th>{_format_inline(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        padded = row + [""] * max(0, len(headers) - len(row))
        body_rows.append(
            "<tr>" + "".join(f"<td>{_format_inline(cell)}</td>" for cell in padded[: len(headers)]) + "</tr>"
        )
    return f"<div class=\"table-wrap\"><table><thead><tr>{head_html}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>"


def _markdown_to_html(markdown: str) -> str:
    """Render the limited Markdown produced by the diagnosis pipeline."""

    lines = (markdown or "").splitlines()
    html_parts: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    quote_lines: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            html_parts.append(f"<p>{_format_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            html_parts.append("<ul>" + "".join(f"<li>{_format_inline(item)}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    def flush_quote() -> None:
        if quote_lines:
            html_parts.append("<blockquote>" + "<br>".join(_format_inline(item) for item in quote_lines) + "</blockquote>")
            quote_lines.clear()

    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_list()
            flush_quote()
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and _is_table_separator(lines[index + 1]):
            flush_paragraph()
            flush_list()
            flush_quote()
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].rstrip())
                index += 1
            html_parts.append(_render_table(table_lines))
            continue
        heading_match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading_match:
            flush_paragraph()
            flush_list()
            flush_quote()
            level = min(len(heading_match.group(1)) + 1, 4)
            html_parts.append(f"<h{level}>{_format_inline(heading_match.group(2))}</h{level}>")
            index += 1
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            flush_quote()
            list_items.append(stripped[2:].strip())
            index += 1
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            quote_lines.append(stripped.lstrip(">").strip())
            index += 1
            continue
        flush_list()
        flush_quote()
        paragraph.append(stripped)
        index += 1

    flush_paragraph()
    flush_list()
    flush_quote()
    return "\n".join(html_parts)


def _build_report_html(
    *,
    title: str,
    report_time: str,
    diagnosis_object: str,
    diagnosis_type: str,
    executive_summary: str,
    diagnosis_overview: str,
    diagnosis_details: str,
    fault_inference: str,
    repair_recommendations: str,
    preventive_maintenance: str,
    diagnosis_basis: str,
) -> str:
    sections = [
        ("01", "执行摘要", executive_summary),
        ("02", "诊断过程概述", diagnosis_overview),
        ("03", "运行数据与可视化", diagnosis_details),
        ("04", "故障原因推断", fault_inference),
        ("05", "检查与维修建议", repair_recommendations),
        ("06", "预防性维护建议", preventive_maintenance),
        ("07", "诊断依据", diagnosis_basis),
    ]
    section_html = "\n".join(
        f"""
        <section class="report-section">
          <div class="section-kicker">{number}</div>
          <h2>{escape(name)}</h2>
          <div class="section-body">{_markdown_to_html(body)}</div>
        </section>
        """
        for number, name, body in sections
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #162033;
      --muted: #5f6b7a;
      --line: #d8dee8;
      --panel: #ffffff;
      --surface: #f6f8fb;
      --teal: #0f8b8d;
      --green: #2e7d32;
      --amber: #b7791f;
      --red: #c2413a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      color: var(--ink);
      background: var(--surface);
      line-height: 1.62;
    }}
    .report-shell {{ max-width: 1180px; margin: 0 auto; padding: 28px 24px 48px; }}
    .report-hero {{
      background: linear-gradient(135deg, #ffffff 0%, #eef7f6 56%, #fff7e7 100%);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 28px;
      box-shadow: 0 12px 34px rgba(22, 32, 51, 0.08);
    }}
    .eyebrow {{ color: var(--teal); font-weight: 700; letter-spacing: 0; margin: 0 0 8px; }}
    h1 {{ margin: 0; font-size: 34px; line-height: 1.18; letter-spacing: 0; }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 24px;
    }}
    .meta-item {{
      background: rgba(255,255,255,0.82);
      border: 1px solid rgba(216, 222, 232, 0.9);
      border-radius: 8px;
      padding: 12px 14px;
    }}
    .meta-label {{ color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
    .meta-value {{ font-weight: 700; overflow-wrap: anywhere; }}
    .report-section {{
      margin-top: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      box-shadow: 0 8px 22px rgba(22, 32, 51, 0.05);
    }}
    .section-kicker {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 38px;
      height: 24px;
      border-radius: 6px;
      background: #e6f4f1;
      color: var(--teal);
      font-weight: 800;
      font-size: 12px;
    }}
    h2 {{ margin: 10px 0 14px; font-size: 22px; letter-spacing: 0; }}
    h3 {{ margin: 18px 0 10px; font-size: 18px; letter-spacing: 0; }}
    h4 {{ margin: 14px 0 8px; font-size: 15px; letter-spacing: 0; color: var(--muted); }}
    p {{ margin: 0 0 12px; }}
    ul {{ margin: 0 0 12px 20px; padding: 0; }}
    li {{ margin: 4px 0; }}
    blockquote {{
      margin: 14px 0 0;
      padding: 12px 14px;
      border-left: 4px solid var(--teal);
      background: #f1faf8;
      color: #304057;
      border-radius: 0 8px 8px 0;
    }}
    .table-wrap {{ width: 100%; overflow-x: auto; margin: 10px 0 18px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 680px; background: #fff; }}
    th, td {{ border: 1px solid var(--line); padding: 9px 10px; text-align: left; vertical-align: top; }}
    th {{ background: #f0f5f8; color: #29384f; font-weight: 800; }}
    tr:nth-child(even) td {{ background: #fbfcfe; }}
    code {{ background: #eef2f7; padding: 1px 5px; border-radius: 4px; color: #9b2c2c; }}
    strong {{ color: #111827; }}
    @media (max-width: 860px) {{
      .report-shell {{ padding: 18px 12px 32px; }}
      .report-hero {{ padding: 20px; }}
      h1 {{ font-size: 27px; }}
      .meta-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .report-section {{ padding: 18px; }}
    }}
  </style>
</head>
<body>
  <main class="report-shell">
    <header class="report-hero">
      <p class="eyebrow">DCMA Intelligent Diagnosis Report</p>
      <h1>{escape(title)}</h1>
      <div class="meta-grid">
        <div class="meta-item"><div class="meta-label">报告时间</div><div class="meta-value">{escape(report_time)}</div></div>
        <div class="meta-item"><div class="meta-label">诊断对象</div><div class="meta-value">{escape(diagnosis_object)}</div></div>
        <div class="meta-item"><div class="meta-label">诊断类型</div><div class="meta-value">{escape(diagnosis_type)}</div></div>
        <div class="meta-item"><div class="meta-label">生成系统</div><div class="meta-value">工业设备故障诊断专家系统</div></div>
      </div>
    </header>
    {section_html}
    <blockquote>报告生成时间：{escape(report_time)}<br />诊断系统版本：工业设备故障诊断专家系统</blockquote>
  </main>
</body>
</html>
"""


class SaveReportSchema(BaseModel):
    title: str = Field(description="Report title")
    report_time: str = Field(description="Report timestamp")
    diagnosis_object: str = Field(description="Diagnosis object")
    diagnosis_type: str = Field(description="Diagnosis type")
    executive_summary: str = Field(description="Executive summary")
    diagnosis_overview: str = Field(description="Diagnosis overview")
    diagnosis_details: str = Field(description="Diagnosis details")
    fault_inference: str = Field(description="Fault inference")
    repair_recommendations: str = Field(description="Repair recommendations")
    preventive_maintenance: str = Field(description="Preventive maintenance suggestions")
    diagnosis_basis: str = Field(description="Diagnosis basis")
    report_filename: str = Field(description="Output filename without extension")


@tool(args_schema=SaveReportSchema)
def save_report(
    title: str,
    report_time: str,
    diagnosis_object: str,
    diagnosis_type: str,
    executive_summary: str,
    diagnosis_overview: str,
    diagnosis_details: str,
    fault_inference: str,
    repair_recommendations: str,
    preventive_maintenance: str,
    diagnosis_basis: str,
    report_filename: str,
) -> str:
    """Save a visual HTML report."""
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        safe_report_name = _sanitize_report_filename(report_filename, "html")
        merged_basis = diagnosis_basis

        report_content = _build_report_html(
            title=title,
            report_time=report_time,
            diagnosis_object=diagnosis_object,
            diagnosis_type=diagnosis_type,
            executive_summary=executive_summary,
            diagnosis_overview=diagnosis_overview,
            diagnosis_details=diagnosis_details,
            fault_inference=fault_inference,
            repair_recommendations=repair_recommendations,
            preventive_maintenance=preventive_maintenance,
            diagnosis_basis=merged_basis,
        )

        final_filename = f"{safe_report_name}.html"
        report_path = _resolve_report_path(final_filename)
        web_path = _build_report_web_path(final_filename)
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(report_content)
        return f"报告已保存至：{web_path}"
    except Exception as exc:
        return f"报告保存失败：{str(exc)}"
