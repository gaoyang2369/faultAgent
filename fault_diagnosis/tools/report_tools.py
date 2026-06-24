"""HTML report generation tool for the single-agent diagnosis path."""

from __future__ import annotations

import json
import os
import re
from html import escape

from pydantic import BaseModel, Field

from ..common.paths import REPORTS_DIR
from ..security.runtime_context import get_current_auth_context
from .operation_report_renderer import build_operation_report_html, load_operation_report_payload

try:
    from langchain_core.tools import tool
except ImportError:  # pragma: no cover - local unit tests may not install LangChain
    def tool(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator

_SAFE_REPORT_STEM_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DETAILS_START_RE = re.compile(r"^:::details\s+(.+)$")
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
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in text:
        if escaped:
            if char == "|":
                current.append("|")
            else:
                current.append("\\")
                current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current.clear()
            continue
        current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


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
        details_match = _DETAILS_START_RE.match(stripped)
        if details_match:
            flush_paragraph()
            flush_list()
            flush_quote()
            summary = details_match.group(1).strip()
            nested_lines: list[str] = []
            depth = 1
            index += 1
            while index < len(lines):
                current = lines[index].strip()
                if _DETAILS_START_RE.match(current):
                    depth += 1
                elif current == ":::":
                    depth -= 1
                    if depth == 0:
                        break
                nested_lines.append(lines[index])
                index += 1
            if index < len(lines) and lines[index].strip() == ":::":
                index += 1
            html_parts.append(
                f"<details class=\"details-block\"><summary>{_format_inline(summary)}</summary>"
                f"<div class=\"details-body\">{_markdown_to_html(chr(10).join(nested_lines))}</div></details>"
            )
            continue
        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            flush_quote()
            language = re.sub(r"[^A-Za-z0-9_-]+", "", stripped[3:].strip())
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index].rstrip())
                index += 1
            if index < len(lines) and lines[index].strip().startswith("```"):
                index += 1
            class_name = f' class="language-{escape(language, quote=True)}"' if language else ""
            html_parts.append(
                f"<pre class=\"code-block\"><code{class_name}>{escape(chr(10).join(code_lines))}</code></pre>"
            )
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
        for key in (
            "trend_groups",
            "trend_metrics",
            "status_counts",
            "fault_counts",
            "latest_metric_groups",
            "latest_metrics",
        )
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


def _chart_trend_groups(chart_payload: dict) -> list[dict]:
    groups = chart_payload.get("trend_groups")
    if isinstance(groups, list) and groups:
        return [group for group in groups if isinstance(group, dict)]
    return []


def _chart_trend_id(index: int) -> str:
    return "dcma-trend-chart" if index == 0 else f"dcma-trend-chart-{index}"


def _build_quality_summary(chart_payload: dict) -> str:
    quality = chart_payload.get("data_quality")
    if not isinstance(quality, dict):
        return ""
    items = [
        ("最新采样", quality.get("latest_sample_time") or "-"),
        ("样本量", f"{quality.get('sample_count', 0)} 条"),
        ("数据时效", quality.get("freshness_label") or "未知"),
        ("指标完整率", quality.get("metric_availability") or "未评估"),
    ]
    cards = "".join(
        f"""
        <div class="quality-item">
          <div class="quality-label">{escape(str(label))}</div>
          <div class="quality-value">{escape(str(value))}</div>
        </div>
        """
        for label, value in items
    )
    currentness = quality.get("currentness")
    note = f"<p class=\"quality-note\">{escape(str(currentness))}</p>" if currentness else ""
    return f"""
      <div class="quality-summary" aria-label="数据质量摘要">
        <div class="quality-grid">{cards}</div>
        {note}
      </div>
    """


def _format_chart_metric_value(value: object, unit: object = "") -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
        text = f"{number:.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        text = str(value)
    unit_text = str(unit or "").strip()
    return f"{text} {unit_text}".strip()


def _latest_metric_groups(chart_payload: dict) -> list[dict]:
    groups = chart_payload.get("latest_metric_groups")
    if isinstance(groups, list) and groups:
        return [group for group in groups if isinstance(group, dict)]
    return []


def _build_latest_snapshot(chart_payload: dict) -> str:
    groups = _latest_metric_groups(chart_payload)
    if not groups:
        return """
        <article class="chart-panel chart-panel-wide metric-panel">
          <h3>最新关键指标</h3>
          <div class="chart-empty chart-empty-inline">暂无关键指标</div>
        </article>
        """
    group_html = []
    for group in groups:
        metrics = group.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            continue
        metric_rows = "".join(
            f"""
            <div class="metric-row">
              <span class="metric-name">{escape(str(metric.get("name") or "-"))}</span>
              <strong>{escape(_format_chart_metric_value(metric.get("value"), metric.get("unit")))}</strong>
            </div>
            """
            for metric in metrics
            if isinstance(metric, dict)
        )
        if metric_rows:
            group_html.append(
                f"""
                <div class="metric-group">
                  <div class="metric-group-title">{escape(str(group.get("name") or "关键指标"))}</div>
                  <div class="metric-list">{metric_rows}</div>
                </div>
                """
            )
    return f"""
      <article class="chart-panel chart-panel-wide metric-panel">
        <h3>最新关键指标</h3>
        <div class="metric-groups">{''.join(group_html) or '<div class="chart-empty chart-empty-inline">暂无关键指标</div>'}</div>
      </article>
    """


def _trend_panel_html(group: dict, index: int, *, wide: bool = True) -> str:
    wide_class = " chart-panel-wide" if wide else ""
    return f"""
        <article class="chart-panel{wide_class}">
          <h3>{escape(str(group.get("name") or "关键指标趋势"))}</h3>
          <div id="{_chart_trend_id(index)}" class="chart-box"></div>
        </article>
        """


def _core_trend_group_indexes(trend_groups: list[dict]) -> list[int]:
    if not trend_groups:
        return []
    preferred_keys = ("speed", "load")
    indexes: list[int] = []
    for key in preferred_keys:
        matched = next(
            (index for index, group in enumerate(trend_groups) if str(group.get("key") or "") == key),
            None,
        )
        if matched is not None and matched not in indexes:
            indexes.append(matched)
    if not indexes:
        indexes.append(0)
    return indexes[:2]


def _build_event_timeline_panel(chart_payload: dict) -> str:
    timeline = chart_payload.get("event_timeline")
    if not isinstance(timeline, list) or not timeline:
        return """
        <article class="chart-panel event-panel">
          <h3>事件码时间线</h3>
          <div class="chart-empty chart-empty-inline">样本窗口内未见有效事件码或告警码</div>
        </article>
        """
    rows = "".join(
        f"""
        <div class="event-row">
          <strong>{escape(str(item.get("code") or "-"))}</strong>
          <span>{escape(str(item.get("count") or 0))}/{escape(str(item.get("sample_count") or 0))}</span>
          <span>{escape(str(item.get("ratio") or "-"))}</span>
          <span>连续 {escape(str(item.get("latest_streak") or 0))} 条</span>
          <em>{escape(str(item.get("continuity") or "-"))}</em>
        </div>
        """
        for item in timeline[:5]
        if isinstance(item, dict)
    )
    return f"""
      <article class="chart-panel event-panel">
        <h3>事件码时间线</h3>
        <div class="event-list">{rows}</div>
      </article>
    """


def _build_chart_section(chart_payload: dict | None) -> str:
    if not chart_payload:
        return ""
    data_json = _json_for_script(chart_payload)
    trend_groups = _chart_trend_groups(chart_payload)
    core_indexes = _core_trend_group_indexes(trend_groups)
    core_trend_panels = "".join(
        _trend_panel_html(trend_groups[index], index, wide=True)
        for index in core_indexes
        if index < len(trend_groups)
    )
    detail_trend_panels = "".join(
        _trend_panel_html(group, index, wide=True)
        for index, group in enumerate(trend_groups)
        if index not in core_indexes
    )
    detail_html = ""
    if detail_trend_panels:
        detail_html = f"""
        <details class="chart-details">
          <summary>展开查看：完整趋势图、状态分布和指标快照</summary>
          <div class="chart-grid chart-grid-detail">
            {detail_trend_panels}
            <article class="chart-panel">
              <h3>状态字分布</h3>
              <div id="dcma-status-chart" class="chart-box chart-box-small"></div>
            </article>
            <article class="chart-panel">
              <h3>异常码分布</h3>
              <div id="dcma-fault-chart" class="chart-box chart-box-small"></div>
            </article>
            {_build_latest_snapshot(chart_payload)}
          </div>
        </details>
        """
    else:
        detail_html = f"""
        <details class="chart-details">
          <summary>展开查看：状态分布和指标快照</summary>
          <div class="chart-grid chart-grid-detail">
            <article class="chart-panel">
              <h3>状态字分布</h3>
              <div id="dcma-status-chart" class="chart-box chart-box-small"></div>
            </article>
            <article class="chart-panel">
              <h3>异常码分布</h3>
              <div id="dcma-fault-chart" class="chart-box chart-box-small"></div>
            </article>
            {_build_latest_snapshot(chart_payload)}
          </div>
        </details>
        """
    return f"""
    <section class="report-section chart-section" aria-label="运行数据可视化">
      <div class="section-kicker">VIS</div>
      <h2>核心趋势与事件持续性</h2>
      {_build_quality_summary(chart_payload)}
      <div class="chart-grid chart-grid-core">
        {core_trend_panels}
        {_build_event_timeline_panel(chart_payload)}
      </div>
      {detail_html}
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

      function trendChartId(index) {
        return index === 0 ? "dcma-trend-chart" : "dcma-trend-chart-" + index;
      }

      function trendGroups(data) {
        if (Array.isArray(data.trend_groups) && data.trend_groups.length) {
          return data.trend_groups;
        }
        return [];
      }

      function uniqueUnits(metrics) {
        var units = [];
        (metrics || []).forEach(function (metric) {
          var unit = metric.unit || "数值";
          if (units.indexOf(unit) === -1) units.push(unit);
        });
        return units.length ? units : ["数值"];
      }

      function buildMarkLine(group, unit) {
        var thresholds = Array.isArray(group.thresholds) ? group.thresholds : [];
        var data = thresholds
          .filter(function (item) { return (item.unit || "数值") === unit && typeof item.value === "number"; })
          .map(function (item) { return { name: item.name, yAxis: item.value }; });
        if (!data.length) return undefined;
        return {
          silent: true,
          symbol: "none",
          label: { formatter: "{b}", color: "#64748b" },
          lineStyle: { type: "dashed", color: "#d97706", width: 1 },
          data: data
        };
      }

      function renderTrendGroup(chart, group, timestamps) {
        var metrics = Array.isArray(group.metrics) ? group.metrics : [];
        if (!chart || !metrics.length) return false;
        var units = uniqueUnits(metrics);
        var unitIndex = {};
        units.forEach(function (unit, index) { unitIndex[unit] = index; });
        var hasZoom = Array.isArray(timestamps) && timestamps.length > 16;
        chart.setOption({
          color: palette,
          tooltip: {
            trigger: "axis",
            axisPointer: { type: "cross" },
            valueFormatter: function (value) {
              return typeof value === "number" ? String(Math.round(value * 100) / 100) : String(value);
            }
          },
          legend: { type: "scroll", top: 0, textStyle: { color: "#4b5563" } },
          grid: {
            left: 52,
            right: units.length > 1 ? 64 + Math.max(0, units.length - 2) * 42 : 28,
            top: 56,
            bottom: hasZoom ? 54 : 28,
            containLabel: true
          },
          xAxis: {
            type: "category",
            boundaryGap: false,
            data: timestamps || [],
            axisLabel: { color: "#64748b", hideOverlap: true }
          },
          yAxis: units.map(function (unit, index) {
            return {
              type: "value",
              name: unit,
              scale: true,
              position: index % 2 === 0 ? "left" : "right",
              offset: Math.floor(index / 2) * 42,
              axisLabel: { color: "#64748b" },
              splitLine: { show: index === 0, lineStyle: { color: "#e5e7eb" } }
            };
          }),
          dataZoom: hasZoom ? [{ type: "inside" }, { type: "slider", height: 18, bottom: 12, borderColor: "#d8dee8" }] : [],
          series: metrics.map(function (metric, index) {
            var unit = metric.unit || "数值";
            return {
              name: metric.unit ? metric.name + " (" + metric.unit + ")" : metric.name,
              type: "line",
              smooth: true,
              showSymbol: false,
              connectNulls: true,
              yAxisIndex: unitIndex[unit] || 0,
              lineStyle: { width: 2 },
              areaStyle: index === 0 ? { opacity: 0.08 } : undefined,
              emphasis: { focus: "series" },
              markLine: buildMarkLine(group, unit),
              data: metric.values || []
            };
          })
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
        var groups = trendGroups(data);
        if (groups.length) {
          groups.forEach(function (group, index) {
            var trendChart = initChart(trendChartId(index));
            if (renderTrendGroup(trendChart, group, data.timestamps || [])) charts.push(trendChart);
            else setEmpty(trendChartId(index), "暂无趋势数据");
          });
        }

        var statusChart = initChart("dcma-status-chart");
        if (renderPie(statusChart, "状态字", data.status_counts || [])) charts.push(statusChart);
        else setEmpty("dcma-status-chart", "暂无状态分布");

        var faultChart = initChart("dcma-fault-chart");
        if (renderPie(faultChart, "异常码", data.fault_counts || [])) charts.push(faultChart);
        else setEmpty("dcma-fault-chart", "未见有效异常码");

        if (status) status.textContent = "图表已生成；下方保留完整数据表、诊断依据和处置建议。";
        document.querySelectorAll("details").forEach(function (details) {
          details.addEventListener("toggle", function () {
            if (details.open) {
              setTimeout(function () {
                charts.forEach(function (chart) { chart.resize(); });
              }, 0);
            }
          });
        });
        window.addEventListener("resize", function () {
          charts.forEach(function (chart) { chart.resize(); });
        });
      });
    })();
  </script>
    """


def _build_report_html(
    *,
    operation_report_payload: str,
    chart_payload: str | None = None,
) -> str:
    chart_data = _load_chart_payload(chart_payload)
    operation_payload = load_operation_report_payload(operation_report_payload)
    if not operation_payload:
        raise ValueError("缺少结构化运行诊断报告 payload。")
    return build_operation_report_html(
        payload=operation_payload,
        chart_section=_build_chart_section(chart_data),
        chart_assets=_build_chart_assets(chart_data),
        markdown_to_html=_markdown_to_html,
    )


class SaveReportSchema(BaseModel):
    report_filename: str = Field(description="Output filename without extension")
    chart_payload: str | None = Field(default=None, description="Optional JSON payload for ECharts visualizations")
    operation_report_payload: str = Field(description="Structured operation diagnosis report JSON")


@tool(args_schema=SaveReportSchema)
def save_report(
    report_filename: str,
    chart_payload: str | None = None,
    operation_report_payload: str = "",
) -> str:
    """Save a visual HTML report."""
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        safe_report_name = _sanitize_report_filename(report_filename, "html")

        report_content = _build_report_html(
            operation_report_payload=operation_report_payload,
            chart_payload=chart_payload,
        )
        operation_payload = load_operation_report_payload(operation_report_payload) or {}

        final_filename = f"{safe_report_name}.html"
        report_path = _resolve_report_path(final_filename)
        web_path = _build_report_web_path(final_filename)
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(report_content)
        auth_context = get_current_auth_context()
        if auth_context is not None:
            access_path = f"{report_path}.access.json"
            access_payload = {
                "created_by": auth_context.user_id,
                "created_by_role": auth_context.role,
                "authorized_asset_scope": list(auth_context.asset_scope),
                "authorized_table_scope": list(auth_context.table_scope),
                "diagnosis_object": operation_payload.get("asset") or "DCMA 系统",
            }
            with open(access_path, "w", encoding="utf-8") as handle:
                json.dump(access_payload, handle, ensure_ascii=False, indent=2)
        return f"报告已保存至：{web_path}"
    except Exception as exc:
        return f"报告保存失败：{str(exc)}"
