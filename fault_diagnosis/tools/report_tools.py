"""HTML report generation tool for the single-agent diagnosis path."""

from __future__ import annotations

import json
import os
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
    metrics = chart_payload.get("trend_metrics")
    if isinstance(metrics, list) and metrics:
        return [{"key": "legacy", "name": "关键指标趋势", "metrics": metrics, "thresholds": []}]
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


def _build_status_summary_section(chart_payload: dict | None) -> str:
    if not chart_payload:
        return ""
    summary = chart_payload.get("status_summary")
    if not isinstance(summary, dict):
        return ""
    status_items = [
        ("状态等级", summary.get("status_level") or "未知"),
        ("当前事件", summary.get("current_event") or "无"),
        ("关键现象", summary.get("key_phenomenon") or "-"),
        ("处置优先级", summary.get("priority") or "未知"),
    ]
    info_items = [
        ("数据源与窗口", f"{summary.get('source_table') or '-'}；{summary.get('sample_window') or '-'}"),
        ("设备映射", summary.get("device_mapping") or summary.get("device") or "-"),
        ("一句话诊断结论", summary.get("initial_assessment") or "-"),
    ]
    cards = "".join(
        f"""
        <div class="status-card-item">
          <div class="status-card-label">{escape(str(label))}</div>
          <div class="status-card-value">{escape(str(value))}</div>
        </div>
        """
        for label, value in status_items
    )
    info_cards = "".join(
        f"""
        <div class="status-info-item">
          <div class="status-card-label">{escape(str(label))}</div>
          <div class="status-info-value">{escape(str(value))}</div>
        </div>
        """
        for label, value in info_items
    )
    next_action = escape(str(summary.get("next_action") or "-"))
    return f"""
    <section class="status-summary-card" aria-label="当前运行状态摘要">
      <div class="status-card-head">
        <div>
          <div class="section-kicker">STATUS</div>
          <h2>当前运行诊断摘要</h2>
        </div>
        <strong>{escape(str(summary.get("status_level") or "未知"))}</strong>
      </div>
      <div class="status-card-grid">{cards}</div>
      <div class="status-info-grid">{info_cards}</div>
      <div class="status-card-notes">
        <p><strong>下一步动作：</strong>{next_action}</p>
      </div>
    </section>
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
    metrics = chart_payload.get("latest_metrics")
    if isinstance(metrics, list) and metrics:
        return [{"key": "legacy", "name": "关键指标", "metrics": metrics}]
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
    preferred_keys = ("speed", "health_overview")
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
        if (Array.isArray(data.trend_metrics) && data.trend_metrics.length) {
          return [{ key: "legacy", name: "关键指标趋势", metrics: data.trend_metrics, thresholds: [] }];
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
    status_summary_section = _build_status_summary_section(chart_data)
    chart_section = _build_chart_section(chart_data)
    chart_assets = _build_chart_assets(chart_data)
    sections = [
        ("01", "核心诊断摘要", executive_summary, False),
        ("02", "数据来源与采样窗口", diagnosis_overview, False),
        ("03", "诊断证据链", diagnosis_details, False),
        ("04", "诊断判断与不确定性", fault_inference, False),
        ("05", "分级处置建议", repair_recommendations, False),
        ("06", "复测验证与能力边界", preventive_maintenance, False),
        ("07", "详细附录", diagnosis_basis, True),
    ]
    section_html = "\n".join(
        f"""
        <section class="report-section">
          <div class="section-kicker">{number}</div>
          <h2>{escape(name)}</h2>
          <div class="section-body">{
            f'<details class="details-block"><summary>展开查看：SQL 与执行信息</summary><div class="details-body">{_markdown_to_html(body)}</div></details>'
            if collapsed
            else _markdown_to_html(body)
          }</div>
        </section>
        """
        for number, name, body, collapsed in sections
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
    .status-summary-card {{
      margin-top: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      box-shadow: 0 8px 22px rgba(22, 32, 51, 0.05);
    }}
    .status-card-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 14px;
    }}
    .status-card-head h2 {{ margin-bottom: 0; }}
    .status-card-head strong {{
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 5px 10px;
      border-radius: 6px;
      background: #fff7ed;
      color: #9a3412;
      font-size: 15px;
      white-space: nowrap;
    }}
    .status-card-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}
    .status-card-item {{
      min-width: 0;
      border: 1px solid #edf1f6;
      border-radius: 8px;
      padding: 10px 12px;
      background: #fbfcfe;
    }}
    .status-card-label {{ color: var(--muted); font-size: 12px; margin-bottom: 3px; }}
    .status-card-value {{ font-weight: 800; overflow-wrap: anywhere; }}
    .status-info-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 10px;
    }}
    .status-info-item {{
      min-width: 0;
      border: 1px solid #e4ebf2;
      border-radius: 8px;
      padding: 12px 14px;
      background: #ffffff;
    }}
    .status-info-value {{ font-weight: 700; color: #243047; overflow-wrap: anywhere; }}
    .status-card-notes {{
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      color: #304057;
    }}
    .status-card-notes p:last-child {{ margin-bottom: 0; }}
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
    .quality-summary {{
      margin: 0 0 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px 14px;
    }}
    .quality-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}
    .quality-item {{ min-width: 0; }}
    .quality-label {{ color: var(--muted); font-size: 12px; margin-bottom: 3px; }}
    .quality-value {{ font-weight: 800; overflow-wrap: anywhere; }}
    .quality-note {{ margin: 8px 0 0; color: var(--muted); font-size: 13px; }}
    .chart-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      align-items: stretch;
    }}
    .chart-grid-core {{ margin-bottom: 12px; }}
    .chart-grid-detail {{ margin-top: 12px; }}
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
    .chart-empty-inline {{ min-height: 88px; height: auto; }}
    .event-panel {{ grid-column: 1 / -1; }}
    .event-list {{ display: grid; gap: 8px; }}
    .event-row {{
      display: grid;
      grid-template-columns: 1.1fr 0.8fr 0.7fr 1fr 0.9fr;
      gap: 10px;
      align-items: center;
      border: 1px solid #edf1f6;
      border-radius: 8px;
      padding: 10px 12px;
      background: #fbfcfe;
      overflow-wrap: anywhere;
    }}
    .event-row strong {{ color: #0f766e; }}
    .event-row em {{
      font-style: normal;
      justify-self: start;
      border-radius: 6px;
      padding: 2px 8px;
      background: #fff7ed;
      color: #9a3412;
      font-size: 12px;
      font-weight: 800;
    }}
    .chart-details,
    .details-block {{
      margin: 12px 0 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
    }}
    .chart-details summary,
    .details-block summary {{
      cursor: pointer;
      padding: 11px 14px;
      color: #243047;
      font-weight: 800;
    }}
    .chart-details summary::marker,
    .details-block summary::marker {{ color: var(--teal); }}
    .details-body {{
      border-top: 1px solid var(--line);
      padding: 14px 16px 4px;
      background: #fbfcfe;
      overflow-wrap: anywhere;
    }}
    .metric-panel h3 {{ margin-bottom: 4px; }}
    .metric-groups {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px 18px;
      margin-top: 10px;
    }}
    .metric-group {{ min-width: 0; }}
    .metric-group-title {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
      margin-bottom: 6px;
    }}
    .metric-list {{ border-top: 1px solid var(--line); }}
    .metric-row {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      padding: 7px 0;
      border-bottom: 1px solid #edf1f6;
    }}
    .metric-name {{ color: #334155; font-size: 13px; }}
    .metric-row strong {{ font-size: 15px; white-space: nowrap; }}
    p {{ margin: 0 0 12px; }}
    .section-body h3,
    .section-body h4 {{
      margin: 16px 0 8px;
      color: #1f2a3d;
      font-size: 16px;
      line-height: 1.35;
    }}
    .details-body h3:first-child,
    .details-body h4:first-child {{ margin-top: 0; }}
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
    th, td {{
      border: 1px solid var(--line);
      padding: 9px 10px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    th {{ background: #f0f5f8; color: #29384f; font-weight: 800; }}
    tr:nth-child(even) td {{ background: #fbfcfe; }}
    .code-block {{
      margin: 8px 0 16px;
      padding: 12px 14px;
      border: 1px solid #dbe3ee;
      border-radius: 6px;
      background: #f8fafc;
      color: #1e293b;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.55;
    }}
    .code-block code {{
      padding: 0;
      background: transparent;
      color: inherit;
      border-radius: 0;
    }}
    code {{ background: #eef2f7; padding: 1px 5px; border-radius: 4px; color: #9b2c2c; }}
    strong {{ color: #111827; }}
    @media (max-width: 860px) {{
      .report-shell {{ padding: 18px 12px 32px; }}
      .report-hero {{ padding: 20px; }}
      h1 {{ font-size: 27px; }}
      .meta-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .status-card-head {{ display: block; }}
      .status-card-head strong {{ margin-top: 10px; }}
      .status-card-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .status-info-grid {{ grid-template-columns: 1fr; }}
      .report-section {{ padding: 18px; }}
      .quality-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .chart-grid {{ grid-template-columns: 1fr; }}
      .chart-box {{ height: 300px; }}
      .chart-box-small {{ height: 240px; }}
      .event-row {{ grid-template-columns: 1fr 1fr; }}
      .metric-groups {{ grid-template-columns: 1fr; }}
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
        <div class="meta-item"><div class="meta-label">报告类型</div><div class="meta-value">{escape(diagnosis_type)}</div></div>
        <div class="meta-item"><div class="meta-label">生成系统</div><div class="meta-value">工业设备故障诊断专家系统</div></div>
      </div>
    </header>
    {status_summary_section}
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
