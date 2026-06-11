"""HTML report generation tool for the single-agent diagnosis path."""

from __future__ import annotations

import os
import json
import re
from html import escape

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


def _load_chart_payload(chart_payload: str | None) -> dict | None:
    if not chart_payload:
        return None
    try:
        payload = json.loads(chart_payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    has_data = any(
        isinstance(payload.get(key), list) and payload.get(key)
        for key in ("trend_metrics", "status_counts", "fault_counts", "latest_metrics")
    )
    return payload if has_data else None


def _json_for_script(payload: dict) -> str:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _build_chart_section(chart_payload: dict | None) -> str:
    if not chart_payload:
        return ""
    data_json = _json_for_script(chart_payload)
    return f"""
    <section class="report-section chart-section" aria-label="运行数据可视化">
      <div class="section-kicker">VIS</div>
      <h2>运行数据可视化</h2>
      <div class="chart-grid">
        <article class="chart-panel chart-panel-wide">
          <h3>关键指标趋势</h3>
          <div id="dcma-trend-chart" class="chart-box"></div>
        </article>
        <article class="chart-panel">
          <h3>状态字分布</h3>
          <div id="dcma-status-chart" class="chart-box chart-box-small"></div>
        </article>
        <article class="chart-panel">
          <h3>异常码分布</h3>
          <div id="dcma-fault-chart" class="chart-box chart-box-small"></div>
        </article>
        <article class="chart-panel chart-panel-wide">
          <h3>最新关键指标</h3>
          <div id="dcma-latest-chart" class="chart-box chart-box-small"></div>
        </article>
      </div>
      <p class="chart-status" data-chart-status>图表加载中；若网络受限，请参考下方数据表。</p>
      <script type="application/json" id="dcma-chart-data">{data_json}</script>
    </section>
    """


def _build_chart_assets(chart_payload: dict | None) -> str:
    if not chart_payload:
        return ""
    return """
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <script>
    (function () {
      var palette = ["#0f766e", "#2563eb", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#65a30d", "#be185d"];

      function ready(callback) {
        if (document.readyState === "loading") {
          document.addEventListener("DOMContentLoaded", callback);
        } else {
          callback();
        }
      }

      function readChartData() {
        var node = document.getElementById("dcma-chart-data");
        if (!node) return null;
        try {
          return JSON.parse(node.textContent || "{}");
        } catch (error) {
          return null;
        }
      }

      function initChart(id) {
        var element = document.getElementById(id);
        if (!element || !window.echarts) return null;
        return window.echarts.init(element, null, { renderer: "canvas" });
      }

      function setEmpty(id, text) {
        var element = document.getElementById(id);
        if (element) {
          element.innerHTML = '<div class="chart-empty">' + text + '</div>';
        }
      }

      function renderPie(chart, title, data) {
        if (!chart || !Array.isArray(data) || data.length === 0) return false;
        chart.setOption({
          color: palette,
          tooltip: { trigger: "item" },
          legend: { type: "scroll", bottom: 0, textStyle: { color: "#4b5563" } },
          series: [{
            name: title,
            type: "pie",
            radius: ["42%", "68%"],
            center: ["50%", "43%"],
            avoidLabelOverlap: true,
            label: { formatter: "{b}: {c}", color: "#243047" },
            labelLine: { length: 10, length2: 8 },
            data: data
          }]
        });
        return true;
      }

      ready(function () {
        var data = readChartData();
        var status = document.querySelector("[data-chart-status]");
        if (!data || !window.echarts) {
          if (status) status.textContent = "图表资源未加载，已保留下方表格数据用于审阅。";
          return;
        }

        var charts = [];
        var trendChart = initChart("dcma-trend-chart");
        if (trendChart && Array.isArray(data.trend_metrics) && data.trend_metrics.length) {
          var hasZoom = Array.isArray(data.timestamps) && data.timestamps.length > 16;
          trendChart.setOption({
            color: palette,
            tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
            legend: { type: "scroll", top: 0, textStyle: { color: "#4b5563" } },
            grid: { left: 48, right: 28, top: 56, bottom: hasZoom ? 54 : 28, containLabel: true },
            xAxis: {
              type: "category",
              boundaryGap: false,
              data: data.timestamps || [],
              axisLabel: { color: "#64748b", hideOverlap: true }
            },
            yAxis: { type: "value", scale: true, axisLabel: { color: "#64748b" }, splitLine: { lineStyle: { color: "#e5e7eb" } } },
            dataZoom: hasZoom ? [{ type: "inside" }, { type: "slider", height: 18, bottom: 12, borderColor: "#d8dee8" }] : [],
            series: data.trend_metrics.map(function (metric, index) {
              return {
                name: metric.name,
                type: "line",
                smooth: true,
                showSymbol: false,
                connectNulls: true,
                lineStyle: { width: 2 },
                areaStyle: index === 0 ? { opacity: 0.08 } : undefined,
                emphasis: { focus: "series" },
                data: metric.values || []
              };
            })
          });
          charts.push(trendChart);
        } else {
          setEmpty("dcma-trend-chart", "暂无趋势数据");
        }

        var statusChart = initChart("dcma-status-chart");
        if (renderPie(statusChart, "状态字", data.status_counts || [])) charts.push(statusChart);
        else setEmpty("dcma-status-chart", "暂无状态分布");

        var faultChart = initChart("dcma-fault-chart");
        if (renderPie(faultChart, "异常码", data.fault_counts || [])) charts.push(faultChart);
        else setEmpty("dcma-fault-chart", "未见有效异常码");

        var latestChart = initChart("dcma-latest-chart");
        if (latestChart && Array.isArray(data.latest_metrics) && data.latest_metrics.length) {
          latestChart.setOption({
            color: ["#0f766e"],
            tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
            grid: { left: 112, right: 28, top: 18, bottom: 24 },
            xAxis: { type: "value", axisLabel: { color: "#64748b" }, splitLine: { lineStyle: { color: "#e5e7eb" } } },
            yAxis: {
              type: "category",
              inverse: true,
              data: data.latest_metrics.map(function (item) { return item.name; }),
              axisLabel: { color: "#334155" }
            },
            series: [{
              name: "最新值",
              type: "bar",
              barWidth: 14,
              itemStyle: { borderRadius: [0, 6, 6, 0] },
              label: { show: true, position: "right", color: "#243047" },
              data: data.latest_metrics.map(function (item) { return item.value; })
            }]
          });
          charts.push(latestChart);
        } else {
          setEmpty("dcma-latest-chart", "暂无关键指标");
        }

        if (status) status.textContent = "图表已生成；下方保留完整数据表、诊断依据和处置建议。";
        window.addEventListener("resize", function () {
          charts.forEach(function (chart) { chart.resize(); });
        });
      });
    })();
  </script>
    """


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
    chart_payload: str | None = None,
) -> str:
    chart_data = _load_chart_payload(chart_payload)
    chart_section = _build_chart_section(chart_data)
    chart_assets = _build_chart_assets(chart_data)
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
    .chart-section {{ background: #fbfdff; }}
    .chart-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      align-items: stretch;
    }}
    .chart-panel {{
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 14px;
    }}
    .chart-panel-wide {{ grid-column: 1 / -1; }}
    .chart-panel h3 {{ margin: 0 0 10px; font-size: 16px; }}
    .chart-box {{ width: 100%; height: 340px; }}
    .chart-box-small {{ height: 260px; }}
    .chart-status {{ margin: 12px 0 0; color: var(--muted); font-size: 13px; }}
    .chart-empty {{
      height: 100%;
      display: grid;
      place-items: center;
      color: var(--muted);
      background: #f8fafc;
      border: 1px dashed var(--line);
      border-radius: 8px;
      font-size: 14px;
    }}
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
      .chart-grid {{ grid-template-columns: 1fr; }}
      .chart-box {{ height: 300px; }}
      .chart-box-small {{ height: 240px; }}
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
    {chart_section}
    {section_html}
    <blockquote>报告生成时间：{escape(report_time)}<br />诊断系统版本：工业设备故障诊断专家系统</blockquote>
  </main>
{chart_assets}
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
    chart_payload: str | None = Field(default=None, description="Optional JSON payload for ECharts visualizations")


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
    chart_payload: str | None = None,
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
            chart_payload=chart_payload,
        )

        final_filename = f"{safe_report_name}.html"
        report_path = _resolve_report_path(final_filename)
        web_path = _build_report_web_path(final_filename)
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(report_content)
        return f"报告已保存至：{web_path}"
    except Exception as exc:
        return f"报告保存失败：{str(exc)}"
