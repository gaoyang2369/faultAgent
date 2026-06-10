"""Report generation tools for Markdown and HTML outputs."""

from __future__ import annotations

import os
import re
from datetime import datetime
from html import escape
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..quality.evidence import (
    list_evidence_records,
    list_finding_links,
    list_findings,
)
from ..quality.governance import build_governance_snapshot
from ..common.paths import REPORTS_DIR, TEMPLATES_DIR
from ..runtime import get_current_quality_summary
from ..quality.safe_actions import build_safe_action_guard, store_tool_artifact_metadata

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


def _build_report_action_guard(tool_name: str, report_filename: str, extension: str, report_gate_summary: dict) -> dict:
    return build_safe_action_guard(
        tool_name=tool_name,
        target_name=(report_filename or "report").strip() or "report",
        extension=extension,
        gate=str(report_gate_summary.get("gate") or "pass"),
        risk_level=str(report_gate_summary.get("risk_level") or "high"),
        release_ready=bool(report_gate_summary.get("release_ready")),
        review_reasons=list(report_gate_summary.get("review_reasons") or []),
        allow_draft_on_fail=True,
    )


def _build_link_index() -> dict[str, dict]:
    return {
        link.get("finding_id"): link
        for link in list_finding_links()
        if isinstance(link, dict)
    }


def _normalize_report_gate_summary(summary: dict[str, Any] | None) -> dict:
    summary = dict(summary or {})
    gate = summary.get("gate") or "pass"
    coverage_summary = summary.get("coverage_summary") or {}
    findings = summary.get("findings_snapshot") or list_findings()
    links = summary.get("finding_links_snapshot") or list_finding_links()
    if gate == "pass" and findings:
        link_index = {
            link.get("finding_id"): link
            for link in links
            if isinstance(link, dict)
        }
        weak_findings = 0
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            link = link_index.get(finding.get("finding_id"), {})
            evidence_ids = link.get("evidence_ids") or []
            if not evidence_ids or str(finding.get("confidence") or "").lower() == "low":
                weak_findings += 1
        if weak_findings > 0:
            gate = "review_required"
            summary = dict(summary)
            summary["gate"] = gate
            summary["risk_level"] = "medium"
            summary["review_reasons"] = list(summary.get("review_reasons") or []) + [
                f"还有 {weak_findings} 条判断缺少更强证据，暂时不建议直接正式发布。"
            ]
    status_label_map = {
        "pass": "证据充分",
        "review_required": "需人工复核",
        "blocked": "证据不足",
    }
    return {
        "gate": gate,
        "status_label": status_label_map.get(gate, gate),
        "risk_level": summary.get("risk_level") or "low",
        "coverage_ratio": summary.get("coverage_ratio", 0.0),
        "review_reasons": list(summary.get("review_reasons") or []),
        "recommended_action": summary.get("recommended_action") or "",
        "total_findings": summary.get("total_findings", 0),
        "linked_findings": summary.get("linked_findings", 0),
        "unsupported_findings": summary.get("unsupported_findings", 0),
        "low_confidence_findings": summary.get("low_confidence_findings", 0),
        "coverage_grade": coverage_summary.get("grade") or "D",
        "coverage_score": coverage_summary.get("score") or 0,
        "release_ready": bool(summary.get("release_ready")),
        "coverage_metrics": list(coverage_summary.get("metrics") or []),
    }


def _build_report_governance_snapshot(
    *,
    report_gate_summary: dict[str, Any],
    findings_snapshot: list[dict[str, Any]] | None,
    action_guard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_governance_snapshot(
        evidence_quality=report_gate_summary,
        findings=findings_snapshot,
        action_guard=action_guard,
    )


def _build_report_gate_summary(override_summary: dict[str, Any] | None = None) -> dict:
    summary = dict(override_summary or {})
    if not summary:
        summary = get_current_quality_summary()
    return _normalize_report_gate_summary(summary)


def _resolve_findings_snapshot(findings_snapshot: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if findings_snapshot:
        return [item for item in findings_snapshot if isinstance(item, dict)]
    return [item for item in list_findings() if isinstance(item, dict)]


def _resolve_finding_links_snapshot(finding_links_snapshot: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if finding_links_snapshot:
        return [item for item in finding_links_snapshot if isinstance(item, dict)]
    return [item for item in list_finding_links() if isinstance(item, dict)]


def _resolve_evidence_records_snapshot(evidence_records_snapshot: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if evidence_records_snapshot:
        return [item for item in evidence_records_snapshot if isinstance(item, dict)]
    return [item for item in list_evidence_records() if isinstance(item, dict)]


def _format_report_gate_markdown(summary: dict) -> str:
    if not summary or summary.get("gate") == "pass":
        return ""

    coverage = round(float(summary.get("coverage_ratio", 0.0)) * 100)
    reasons = summary.get("review_reasons") or []
    lines = [
        "## 报告可信度状态",
        "",
        f"- 当前状态: {summary.get('status_label')}",
        f"- 风险等级: {summary.get('risk_level')}",
        f"- 证据覆盖率: {coverage}%",
        (
            f"- grounded_findings: {summary.get('linked_findings', 0)}/"
            f"{summary.get('total_findings', 0)}"
        ),
        f"- unsupported_findings: {summary.get('unsupported_findings', 0)}",
        f"- low_confidence_findings: {summary.get('low_confidence_findings', 0)}",
        "",
    ]
    if reasons:
        lines.append("### 风险原因")
        lines.append("")
        for reason in reasons:
            lines.append(f"- {reason}")
        lines.append("")
    if summary.get("recommended_action"):
        lines.append("### 建议处理")
        lines.append("")
        lines.append(summary["recommended_action"])
        lines.append("")
    return "\n".join(lines).strip()


def _format_report_gate_html(summary: dict) -> str:
    if not summary or summary.get("gate") == "pass":
        return ""

    coverage = round(float(summary.get("coverage_ratio", 0.0)) * 100)
    reasons = summary.get("review_reasons") or []
    status_class = f"report-gate--{_escape_html_text(summary.get('gate'))}"
    parts = [
        f"<section class='report-gate {status_class}'>",
        "<h3>报告可信度状态</h3>",
        f"<p><strong>当前状态:</strong> {_escape_html_text(summary.get('status_label'))}</p>",
        (
            f"<p><strong>风险等级:</strong> {_escape_html_text(summary.get('risk_level'))} | "
            f"<strong>证据覆盖率:</strong> {coverage}%</p>"
        ),
        (
            "<p>"
            f"<strong>Grounded findings:</strong> {summary.get('linked_findings', 0)}/"
            f"{summary.get('total_findings', 0)} | "
            f"<strong>Unsupported findings:</strong> {summary.get('unsupported_findings', 0)} | "
            f"<strong>Low confidence:</strong> {summary.get('low_confidence_findings', 0)}"
            "</p>"
        ),
    ]
    if reasons:
        parts.append("<div class='report-gate__reasons'><h4>风险原因</h4><ul>")
        for reason in reasons:
            parts.append(f"<li>{_escape_html_text(reason)}</li>")
        parts.append("</ul></div>")
    if summary.get("recommended_action"):
        parts.append("<div class='report-gate__action'><h4>建议处理</h4>")
        parts.append(f"<p>{_escape_html_text(summary.get('recommended_action'))}</p></div>")
    parts.append("</section>")
    return "".join(parts)


def _format_report_gate_markdown_v2(summary: dict) -> str:
    if not summary or summary.get("gate") == "pass":
        return ""

    coverage = round(float(summary.get("coverage_ratio", 0.0)) * 100)
    reasons = summary.get("review_reasons") or []
    coverage_metrics = summary.get("coverage_metrics") or []
    lines = [
        "## 报告可信度状态",
        "",
        f"- 当前状态: {summary.get('status_label')}",
        f"- release_ready: {'yes' if summary.get('release_ready') else 'no'}",
        f"- 风险等级: {summary.get('risk_level')}",
        f"- 证据覆盖率: {coverage}%",
        f"- coverage_scorecard: {summary.get('coverage_grade')} ({summary.get('coverage_score')})",
        (
            f"- grounded_findings: {summary.get('linked_findings', 0)}/"
            f"{summary.get('total_findings', 0)}"
        ),
        f"- unsupported_findings: {summary.get('unsupported_findings', 0)}",
        f"- low_confidence_findings: {summary.get('low_confidence_findings', 0)}",
        "",
    ]
    if coverage_metrics:
        lines.append("### Coverage Scorecard")
        lines.append("")
        for item in coverage_metrics:
            lines.append(f"- {item.get('label')}: {item.get('value')}")
        lines.append("")
    if reasons:
        lines.append("### 风险原因")
        lines.append("")
        for reason in reasons:
            lines.append(f"- {reason}")
        lines.append("")
    if summary.get("recommended_action"):
        lines.append("### 建议处理")
        lines.append("")
        lines.append(summary["recommended_action"])
        lines.append("")
    return "\n".join(lines).strip()


def _format_governance_markdown(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return ""

    findings = snapshot.get("findings") or {}
    report_gate = snapshot.get("report_gate") or {}
    lines = [
        "## 统一状态",
        "",
        f"- 判断状态: {findings.get('status_label', '待确认')}",
        f"- 初步报告: {report_gate.get('preliminary_report_label', '暂不建议出报告')}",
        f"- 正式报告: {report_gate.get('formal_report_label', '不可出正式报告')}",
    ]
    return "\n".join(lines).strip()


def _format_report_gate_html_v2(summary: dict) -> str:
    if not summary or summary.get("gate") == "pass":
        return ""

    coverage = round(float(summary.get("coverage_ratio", 0.0)) * 100)
    reasons = summary.get("review_reasons") or []
    coverage_metrics = summary.get("coverage_metrics") or []
    status_class = f"report-gate--{_escape_html_text(summary.get('gate'))}"
    parts = [
        f"<section class='report-gate {status_class}'>",
        "<h3>报告可信度状态</h3>",
        f"<p><strong>当前状态:</strong> {_escape_html_text(summary.get('status_label'))}</p>",
        (
            f"<p><strong>Release ready:</strong> {'yes' if summary.get('release_ready') else 'no'} | "
            f"<strong>风险等级:</strong> {_escape_html_text(summary.get('risk_level'))} | "
            f"<strong>证据覆盖率:</strong> {coverage}%</p>"
        ),
        (
            "<p>"
            f"<strong>Coverage scorecard:</strong> {summary.get('coverage_grade')} ({summary.get('coverage_score')}) | "
            f"<strong>Grounded findings:</strong> {summary.get('linked_findings', 0)}/"
            f"{summary.get('total_findings', 0)} | "
            f"<strong>Unsupported findings:</strong> {summary.get('unsupported_findings', 0)} | "
            f"<strong>Low confidence:</strong> {summary.get('low_confidence_findings', 0)}"
            "</p>"
        ),
    ]
    if coverage_metrics:
        parts.append("<div class='report-gate__scorecard'><h4>Coverage Scorecard</h4><ul>")
        for item in coverage_metrics:
            parts.append(f"<li><strong>{_escape_html_text(item.get('label'))}:</strong> {_escape_html_text(item.get('value'))}</li>")
        parts.append("</ul></div>")
    if reasons:
        parts.append("<div class='report-gate__reasons'><h4>风险原因</h4><ul>")
        for reason in reasons:
            parts.append(f"<li>{_escape_html_text(reason)}</li>")
        parts.append("</ul></div>")
    if summary.get("recommended_action"):
        parts.append("<div class='report-gate__action'><h4>建议处理</h4>")
        parts.append(f"<p>{_escape_html_text(summary.get('recommended_action'))}</p></div>")
    parts.append("</section>")
    return "".join(parts)


def _format_governance_html(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return ""

    findings = snapshot.get("findings") or {}
    report_gate = snapshot.get("report_gate") or {}
    return "".join(
        [
            "<section class='report-gate report-gate--governance'>",
            "<h3>统一状态</h3>",
            f"<p><strong>判断状态:</strong> {_escape_html_text(findings.get('status_label', '待确认'))}</p>",
            f"<p><strong>初步报告:</strong> {_escape_html_text(report_gate.get('preliminary_report_label', '暂不建议出报告'))}</p>",
            f"<p><strong>正式报告:</strong> {_escape_html_text(report_gate.get('formal_report_label', '不可出正式报告'))}</p>",
            "</section>",
        ]
    )


def _format_action_guard_markdown(action_guard: dict) -> str:
    if not action_guard:
        return ""

    reasons = action_guard.get("review_reasons") or []
    lines = [
        "## Report Publication Decision",
        "",
        f"- tool: {action_guard.get('tool_name', 'unknown_tool')}",
        f"- action: {action_guard.get('action', 'review')}",
        f"- publication_status: {action_guard.get('publication_status', 'draft')}",
        f"- target_filename: {action_guard.get('target_filename', 'n/a')}",
        f"- final_filename: {action_guard.get('final_filename', 'n/a')}",
    ]
    if action_guard.get("status_text"):
        lines.append(f"- status_text: {action_guard.get('status_text')}")
    if reasons:
        lines.append("")
        lines.append("### Review Reasons")
        lines.append("")
        for reason in reasons:
            lines.append(f"- {reason}")
    return "\n".join(lines).strip()


def _format_action_guard_html(action_guard: dict) -> str:
    if not action_guard:
        return ""

    reasons = action_guard.get("review_reasons") or []
    parts = [
        "<section class='report-action-guard'>",
        "<h3>Report Publication Decision</h3>",
        "<ul>",
        f"<li><strong>tool:</strong> {_escape_html_text(action_guard.get('tool_name', 'unknown_tool'))}</li>",
        f"<li><strong>action:</strong> {_escape_html_text(action_guard.get('action', 'review'))}</li>",
        f"<li><strong>publication_status:</strong> {_escape_html_text(action_guard.get('publication_status', 'draft'))}</li>",
        f"<li><strong>target_filename:</strong> {_escape_html_text(action_guard.get('target_filename', 'n/a'))}</li>",
        f"<li><strong>final_filename:</strong> {_escape_html_text(action_guard.get('final_filename', 'n/a'))}</li>",
    ]
    if action_guard.get("status_text"):
        parts.append(f"<li><strong>status_text:</strong> {_escape_html_text(action_guard.get('status_text'))}</li>")
    parts.append("</ul>")
    if reasons:
        parts.append("<div class='report-action-guard__reasons'><h4>Review Reasons</h4><ul>")
        for reason in reasons:
            parts.append(f"<li>{_escape_html_text(reason)}</li>")
        parts.append("</ul></div>")
    parts.append("</section>")
    return "".join(parts)


def _format_evidence_reference_markdown(
    findings_snapshot: list[dict[str, Any]] | None = None,
    finding_links_snapshot: list[dict[str, Any]] | None = None,
    evidence_records_snapshot: list[dict[str, Any]] | None = None,
) -> str:
    findings = _resolve_findings_snapshot(findings_snapshot)
    evidences = _resolve_evidence_records_snapshot(evidence_records_snapshot)
    if not findings and not evidences:
        return ""

    lines = [
        "",
        "---",
        "",
        "## 证据链附录",
        "",
    ]

    link_index = {
        link.get("finding_id"): link
        for link in _resolve_finding_links_snapshot(finding_links_snapshot)
        if isinstance(link, dict)
    }
    if findings:
        lines.append("### 结论与证据映射")
        lines.append("")
        for idx, finding in enumerate(findings, start=1):
            link = link_index.get(finding.get("finding_id"), {})
            evidence_ids = ", ".join(link.get("evidence_ids", [])) or "none"
            matched_keywords = ", ".join(link.get("matched_keywords", [])) or "none"
            lines.append(f"{idx}. {finding.get('text', '')}")
            lines.append(f"   - severity: {finding.get('severity') or 'unknown'}")
            lines.append(f"   - confidence: {finding.get('confidence') or 'unknown'}")
            lines.append(f"   - evidence_ids: {evidence_ids}")
            lines.append(f"   - matched_keywords: {matched_keywords}")
            lines.append(f"   - match_score: {link.get('match_score', 0)}")
        lines.append("")

    if evidences:
        lines.append("### Evidence Records")
        lines.append("")
        for evidence in evidences:
            lines.append(
                f"- `{evidence.get('evidence_id')}` | {evidence.get('type')} | "
                f"{evidence.get('title')} | {evidence.get('summary')}"
            )
        lines.append("")

    return "\n".join(lines).strip()


def _format_evidence_reference_html(
    findings_snapshot: list[dict[str, Any]] | None = None,
    finding_links_snapshot: list[dict[str, Any]] | None = None,
    evidence_records_snapshot: list[dict[str, Any]] | None = None,
) -> str:
    findings = _resolve_findings_snapshot(findings_snapshot)
    evidences = _resolve_evidence_records_snapshot(evidence_records_snapshot)
    if not findings and not evidences:
        return ""

    link_index = {
        link.get("finding_id"): link
        for link in _resolve_finding_links_snapshot(finding_links_snapshot)
        if isinstance(link, dict)
    }
    sections = [
        "<section class='evidence-reference'>",
        "<h3>证据链附录</h3>",
    ]

    if findings:
        sections.append("<div class='evidence-reference__findings'><h4>结论与证据映射</h4><ul>")
        for finding in findings:
            link = link_index.get(finding.get("finding_id"), {})
            evidence_ids = ", ".join(link.get("evidence_ids", [])) or "none"
            matched_keywords = ", ".join(link.get("matched_keywords", [])) or "none"
            sections.append(
                "<li>"
                f"<strong>{_escape_html_text(finding.get('text', ''))}</strong><br>"
                f"severity: {_escape_html_text(finding.get('severity') or 'unknown')} | "
                f"confidence: {_escape_html_text(finding.get('confidence') or 'unknown')}<br>"
                f"evidence_ids: {_escape_html_text(evidence_ids)}<br>"
                f"matched_keywords: {_escape_html_text(matched_keywords)}<br>"
                f"match_score: {_escape_html_text(link.get('match_score', 0))}"
                "</li>"
            )
        sections.append("</ul></div>")

    if evidences:
        sections.append("<div class='evidence-reference__records'><h4>Evidence Records</h4><ul>")
        for evidence in evidences:
            sections.append(
                "<li>"
                f"<code>{_escape_html_text(evidence.get('evidence_id'))}</code> | "
                f"{_escape_html_text(evidence.get('type'))} | "
                f"{_escape_html_text(evidence.get('title'))} | "
                f"{_escape_html_text(evidence.get('summary'))}"
                "</li>"
            )
        sections.append("</ul></div>")

    sections.append("</section>")
    return "".join(sections)


def _merge_text_with_evidence_section(text: str, evidence_section: str) -> str:
    base = (text or "").strip()
    if not evidence_section:
        return base
    if not base:
        return evidence_section
    return f"{base}\n\n{evidence_section}"


def _merge_html_with_evidence_section(html_text: str, evidence_section: str) -> str:
    base = (html_text or "").strip()
    if not evidence_section:
        return base
    if not base:
        return evidence_section
    return f"{base}{evidence_section}"


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
    report_gate_summary: dict[str, Any] = Field(default_factory=dict, description="Optional quality gate summary override")
    findings_snapshot: list[dict[str, Any]] = Field(default_factory=list, description="Optional findings snapshot override")
    finding_links_snapshot: list[dict[str, Any]] = Field(default_factory=list, description="Optional finding links snapshot override")
    evidence_records_snapshot: list[dict[str, Any]] = Field(default_factory=list, description="Optional evidence records snapshot override")


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
    report_gate_summary: dict[str, Any] | None = None,
    findings_snapshot: list[dict[str, Any]] | None = None,
    finding_links_snapshot: list[dict[str, Any]] | None = None,
    evidence_records_snapshot: list[dict[str, Any]] | None = None,
) -> str:
    """Save a Markdown report and append evidence-chain details."""
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        safe_report_name = _sanitize_report_filename(report_filename, "md")

        gate_summary_input = dict(report_gate_summary or {})
        if findings_snapshot:
            gate_summary_input["findings_snapshot"] = findings_snapshot
        if finding_links_snapshot:
            gate_summary_input["finding_links_snapshot"] = finding_links_snapshot
        report_gate_summary = _build_report_gate_summary(gate_summary_input)
        action_guard = _build_report_action_guard(
            "save_report",
            safe_report_name,
            "md",
            report_gate_summary,
        )
        governance_snapshot = _build_report_governance_snapshot(
            report_gate_summary=report_gate_summary,
            findings_snapshot=findings_snapshot,
            action_guard=action_guard,
        )
        governance_section = _format_governance_markdown(governance_snapshot)
        gate_section = _format_report_gate_markdown_v2(report_gate_summary)
        action_guard_section = _format_action_guard_markdown(action_guard)
        evidence_appendix = _format_evidence_reference_markdown(
            findings_snapshot=findings_snapshot,
            finding_links_snapshot=finding_links_snapshot,
            evidence_records_snapshot=evidence_records_snapshot,
        )
        merged_basis = _merge_text_with_evidence_section(diagnosis_basis, evidence_appendix)

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

{governance_section}

---

{gate_section}

---

{action_guard_section}

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

        final_filename = action_guard["final_filename"]
        target_filename = action_guard.get("target_filename") or final_filename
        report_path = _resolve_report_path(final_filename)
        web_path = _build_report_web_path(final_filename)
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(report_content)
        if target_filename != final_filename:
            preview_path = _resolve_report_path(target_filename)
            with open(preview_path, "w", encoding="utf-8") as handle:
                handle.write(report_content)

        store_tool_artifact_metadata(
            "save_report",
            {
                "artifact_path": report_path,
                "web_path": web_path,
                "publication_status": action_guard.get("publication_status"),
                "action_guard": action_guard,
                "report_gate": report_gate_summary.get("gate"),
                "release_ready": report_gate_summary.get("release_ready"),
                "governance": governance_snapshot,
            },
        )

        if action_guard.get("publication_status") == "published":
            return f"报告已保存至：{web_path}"
        return f"报告已保存至：{web_path}；当前可信度状态为 {report_gate_summary.get('status_label')}"
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
    report_gate_summary: dict[str, Any] = Field(default_factory=dict, description="Optional quality gate summary override")
    findings_snapshot: list[dict[str, Any]] = Field(default_factory=list, description="Optional findings snapshot override")
    finding_links_snapshot: list[dict[str, Any]] = Field(default_factory=list, description="Optional finding links snapshot override")
    evidence_records_snapshot: list[dict[str, Any]] = Field(default_factory=list, description="Optional evidence records snapshot override")


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
    report_gate_summary: dict[str, Any] | None = None,
    findings_snapshot: list[dict[str, Any]] | None = None,
    finding_links_snapshot: list[dict[str, Any]] | None = None,
    evidence_records_snapshot: list[dict[str, Any]] | None = None,
) -> str:
    """Save an HTML report and append evidence-chain details."""
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

        gate_summary_input = dict(report_gate_summary or {})
        if findings_snapshot:
            gate_summary_input["findings_snapshot"] = findings_snapshot
        if finding_links_snapshot:
            gate_summary_input["finding_links_snapshot"] = finding_links_snapshot
        report_gate_summary = _build_report_gate_summary(gate_summary_input)
        action_guard = _build_report_action_guard(
            "save_html_report",
            safe_report_name,
            "html",
            report_gate_summary,
        )
        governance_snapshot = _build_report_governance_snapshot(
            report_gate_summary=report_gate_summary,
            findings_snapshot=findings_snapshot,
            action_guard=action_guard,
        )
        governance_section = _format_governance_html(governance_snapshot)
        gate_section = _format_report_gate_html_v2(report_gate_summary)
        action_guard_section = _format_action_guard_html(action_guard)
        evidence_appendix = _format_evidence_reference_html(
            findings_snapshot=findings_snapshot,
            finding_links_snapshot=finding_links_snapshot,
            evidence_records_snapshot=evidence_records_snapshot,
        )
        summary = _merge_html_with_evidence_section(
            summary,
            _merge_html_with_evidence_section(
                governance_section,
                _merge_html_with_evidence_section(gate_section, action_guard_section),
            ),
        )
        findings = _merge_html_with_evidence_section(findings, evidence_appendix)

        html_content = template.replace("{{title}}", title)
        html_content = html_content.replace("{{summary}}", summary)
        html_content = html_content.replace("{{kpi_cards}}", kpi_cards)
        html_content = html_content.replace("{{charts}}", charts)
        html_content = html_content.replace("{{chart_scripts}}", chart_scripts)
        html_content = html_content.replace("{{findings}}", findings)
        html_content = html_content.replace("{{recommendations}}", recommendations)
        html_content = html_content.replace("{{timestamp}}", timestamp)

        os.makedirs(REPORTS_DIR, exist_ok=True)
        final_filename = action_guard["final_filename"]
        target_filename = action_guard.get("target_filename") or final_filename
        file_path = _resolve_report_path(final_filename)
        web_path = _build_report_web_path(final_filename)
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(html_content)
        if target_filename != final_filename:
            preview_path = _resolve_report_path(target_filename)
            with open(preview_path, "w", encoding="utf-8") as handle:
                handle.write(html_content)

        store_tool_artifact_metadata(
            "save_html_report",
            {
                "artifact_path": file_path,
                "web_path": web_path,
                "publication_status": action_guard.get("publication_status"),
                "action_guard": action_guard,
                "report_gate": report_gate_summary.get("gate"),
                "release_ready": report_gate_summary.get("release_ready"),
                "governance": governance_snapshot,
            },
        )

        if action_guard.get("publication_status") == "published":
            return f"HTML 报告已保存至：{web_path}"
        return f"HTML 报告已保存至：{web_path}；当前可信度状态为 {report_gate_summary.get('status_label')}"
    except Exception as exc:
        return f"HTML 报告保存失败：{str(exc)}"

