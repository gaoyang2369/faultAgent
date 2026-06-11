"""Markdown report generation tool for the single-agent diagnosis path."""

from __future__ import annotations

import os
import re
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
