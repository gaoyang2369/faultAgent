"""Report generation tools for Markdown and HTML outputs."""

from __future__ import annotations

import os
import re
from datetime import datetime
from html import escape
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..common.paths import REPORTS_DIR, TEMPLATES_DIR

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
_DANGEROUS_TAG_BLOCK_RE = re.compile(
    r"<\s*(script|iframe|object|embed|style|base|form|meta|link)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DANGEROUS_TAG_RE = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|style|base|form|meta|link)\b[^>]*>",
    re.IGNORECASE,
)
_EVENT_HANDLER_ATTR_RE = re.compile(
    r"\s+on[a-zA-Z0-9_-]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE,
)
_URL_ATTR_RE = re.compile(
    r"\s+(href|src|xlink:href|formaction)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE,
)
_STYLE_ATTR_RE = re.compile(r"\s+style\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)


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


def _escape_html_text(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _is_dangerous_url_attr(match: re.Match) -> str:
    raw_value = match.group(2).strip().strip("\"'")
    if re.match(r"^(javascript|data|vbscript):", raw_value, re.IGNORECASE):
        return ""
    return match.group(0)


def _sanitize_style_attr(match: re.Match) -> str:
    raw_value = match.group(1).strip().strip("\"'")
    if re.search(r"expression\s*\(|url\s*\(\s*['\"]?\s*(javascript|data|vbscript):", raw_value, re.IGNORECASE):
        return ""
    return match.group(0)


def _sanitize_html_fragment(value: str) -> str:
    """保留报告 HTML 片段，同时移除主动执行内容。"""
    safe_value = _DANGEROUS_TAG_BLOCK_RE.sub("", value or "")
    safe_value = _DANGEROUS_TAG_RE.sub("", safe_value)
    safe_value = _EVENT_HANDLER_ATTR_RE.sub("", safe_value)
    safe_value = _URL_ATTR_RE.sub(_is_dangerous_url_attr, safe_value)
    safe_value = _STYLE_ATTR_RE.sub(_sanitize_style_attr, safe_value)
    return safe_value


def _sanitize_chart_scripts(value: str) -> str:
    safe_value = _strip_script_tags(value)
    safe_value = re.sub(r"</?\s*script\b[^>]*>", "", safe_value, flags=re.IGNORECASE)
    safe_value = re.sub(
        r"\b(eval|Function|setTimeout|setInterval)\s*\(",
        "/* 已移除危险脚本调用 */(",
        safe_value,
        flags=re.IGNORECASE,
    )
    safe_value = re.sub(
        r"\b(fetch|XMLHttpRequest|localStorage|sessionStorage|document\.cookie)\b",
        "/* 已移除危险浏览器接口 */",
        safe_value,
        flags=re.IGNORECASE,
    )
    return safe_value.strip()


def _strip_script_tags(value: str) -> str:
    """Remove inline script tags to reduce report injection risk."""
    return _SCRIPT_TAG_RE.sub("", value or "")


class SaveReportSchema(BaseModel):
    title: str = Field(description="Markdown report title")
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
    """Save a Markdown report."""
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        safe_report_name = _sanitize_report_filename(report_filename, "md")
        merged_basis = diagnosis_basis

        report_content = f"""# {title}

**报告时间**: {report_time}
**诊断对象**: {diagnosis_object}
**诊断类型**: {diagnosis_type}
**报告生成**: 工业设备故障诊断专家系统

---

## 目录

- [执行摘要](#执行摘要)
- [诊断过程概述](#诊断过程概述)
- [诊断结果详情](#诊断结果详情)
- [故障原因推断](#故障原因推断)
- [检查与维修建议](#检查与维修建议)
- [预防性维护建议](#预防性维护建议)
- [诊断依据](#诊断依据)

---

## 执行摘要

{executive_summary}

---

## 诊断过程概述

{diagnosis_overview}

---

## 诊断结果详情

{diagnosis_details}

---

## 故障原因推断

{fault_inference}

---

## 检查与维修建议

{repair_recommendations}

---

## 预防性维护建议

{preventive_maintenance}

---

## 诊断依据

{merged_basis}

---

**报告生成时间**: {report_time}
**诊断系统版本**: 工业设备故障诊断专家系统
"""

        final_filename = f"{safe_report_name}.md"
        report_path = _resolve_report_path(final_filename)
        web_path = _build_report_web_path(final_filename)
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(report_content)
        return f"报告已保存至：{web_path}"
    except Exception as exc:
        return f"报告保存失败：{str(exc)}"


class SaveHTMLReportSchema(BaseModel):
    title: str = Field(description="HTML report title")
    summary: str = Field(description="HTML report summary")
    kpi_cards: str = Field(description="KPI card HTML")
    charts: str = Field(description="Chart HTML")
    chart_scripts: str = Field(description="Chart JavaScript")
    findings: str = Field(description="Findings HTML")
    recommendations: str = Field(description="Recommendations HTML")
    report_filename: str = Field(description="Output filename without extension")


@tool(args_schema=SaveHTMLReportSchema)
def save_html_report(
    title: str,
    summary: str,
    kpi_cards: str,
    charts: str,
    chart_scripts: str,
    findings: str,
    recommendations: str,
    report_filename: str,
) -> str:
    """Save an HTML report."""
    try:
        template_path = os.path.join(TEMPLATES_DIR, "html_template.html")
        if not os.path.exists(template_path):
            repo_template_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "templates", "html_template.html")
            )
            if os.path.exists(repo_template_path):
                template_path = repo_template_path
        with open(template_path, "r", encoding="utf-8") as handle:
            template = handle.read()

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        safe_report_name = _sanitize_report_filename(report_filename, "html")
        title = _escape_html_text(_strip_script_tags(title))
        summary = _sanitize_html_fragment(summary)
        kpi_cards = _sanitize_html_fragment(kpi_cards)
        charts = _sanitize_html_fragment(charts)
        findings = _sanitize_html_fragment(findings)
        recommendations = _sanitize_html_fragment(recommendations)
        chart_scripts = _sanitize_chart_scripts(chart_scripts)

        html_content = template.replace("{{title}}", title)
        html_content = html_content.replace("{{summary}}", summary)
        html_content = html_content.replace("{{kpi_cards}}", kpi_cards)
        html_content = html_content.replace("{{charts}}", charts)
        html_content = html_content.replace("{{chart_scripts}}", chart_scripts)
        html_content = html_content.replace("{{findings}}", findings)
        html_content = html_content.replace("{{recommendations}}", recommendations)
        html_content = html_content.replace("{{timestamp}}", timestamp)

        os.makedirs(REPORTS_DIR, exist_ok=True)
        final_filename = f"{safe_report_name}.html"
        file_path = _resolve_report_path(final_filename)
        web_path = _build_report_web_path(final_filename)
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(html_content)
        return f"HTML 报告已保存至：{web_path}"
    except Exception as exc:
        return f"HTML 报告保存失败：{str(exc)}"
