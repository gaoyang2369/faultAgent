"""HTML renderer for structured operation diagnosis reports."""

from __future__ import annotations

import json
from collections.abc import Callable
from html import escape


MarkdownRenderer = Callable[[str], str]


def load_operation_report_payload(operation_report_payload: str | None) -> dict | None:
    if not operation_report_payload:
        return None
    try:
        payload = json.loads(operation_report_payload)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload.get("title") else None


def _severity_meta(severity: object) -> tuple[str, str, str]:
    key = str(severity or "unknown").strip().lower()
    meta = {
        "critical": ("⬣", "CRITICAL / 严重", "critical"),
        "high": ("■", "HIGH / 高风险", "high"),
        "warning": ("▲", "WARNING / 需确认", "warning"),
        "notice": ("◆", "NOTICE / 提示", "notice"),
        "normal": ("●", "NORMAL / 正常", "normal"),
        "unknown": ("?", "UNKNOWN / 无法判断", "unknown"),
    }
    return meta.get(key, meta["unknown"])


def _currentness_meta(level: object) -> tuple[str, str, str]:
    key = str(level or "missing").strip().lower()
    meta = {
        "realtime": ("●", "REALTIME / 可代表实时状态", "normal"),
        "recent": ("◆", "RECENT / 近期可参考", "notice"),
        "stale": ("?", "STALE / 不代表实时状态", "unknown"),
        "missing": ("?", "MISSING / 缺少运行数据", "unknown"),
    }
    return meta.get(key, meta["missing"])


def _operation_table(headers: list[str], rows: list[list[object]]) -> str:
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(cell or '-'))}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _operation_list(items: list[object], *, ordered: bool = False) -> str:
    tag = "ol" if ordered else "ul"
    rows = "".join(f"<li>{escape(str(item))}</li>" for item in items if str(item or "").strip())
    return f"<{tag}>{rows or '<li>-</li>'}</{tag}>"


def _operation_kpi_cards(cards: list[dict]) -> str:
    rendered = []
    for card in cards[:6]:
        icon, label, class_name = _severity_meta(card.get("severity"))
        rendered.append(
            f"""
            <article class="kpi-card sev-{class_name}">
              <div class="kpi-top">
                <h3>{escape(str(card.get("name") or "-"))}</h3>
                <span class="severity-pill sev-{class_name}">{escape(icon)} {escape(label)}</span>
              </div>
              <strong>{escape(str(card.get("value") or "-"))}</strong>
              <dl class="kpi-detail">
                <div><dt>最新</dt><dd>{escape(str(card.get("latest_value") or "-"))}</dd></div>
                <div><dt>窗口最大</dt><dd>{escape(str(card.get("window_max") or "-"))}</dd></div>
                <div><dt>阈值</dt><dd>{escape(str(card.get("reference") or "-"))}</dd></div>
                <div><dt>判定</dt><dd>{escape(str(card.get("judgement") or "-"))}</dd></div>
              </dl>
            </article>
            """
        )
    return f'<div class="kpi-grid">{"".join(rendered)}</div>'


def _operation_findings_table(findings: list[dict]) -> str:
    rows = []
    for item in findings[:5]:
        icon, label, _class_name = _severity_meta(item.get("severity"))
        rows.append(
            [
                item.get("finding_id") or "-",
                item.get("title") or "-",
                f"{icon} {label}",
                item.get("engineering_meaning") or item.get("impact") or "-",
                item.get("supporting_evidence") or item.get("evidence_summary") or "-",
                item.get("missing_evidence") or "-",
                item.get("confidence") or "-",
            ]
        )
    return _operation_table(["编号", "发现", "等级", "工程含义", "支持证据", "缺失证据", "置信度"], rows)


def _operation_causes_table(causes: list[dict]) -> str:
    rows = []
    for item in causes[:3]:
        rows.append(
            [
                item.get("rank") or "-",
                item.get("cause") or "-",
                "；".join(str(value) for value in item.get("supporting_evidence") or []) or "-",
                "；".join(str(value) for value in item.get("counter_evidence_or_uncertainty") or []) or "-",
                item.get("verification_step") or "-",
                item.get("conclusion_if_verified") or "-",
            ]
        )
    return _operation_table(["排名", "候选原因", "支持证据", "反证/不确定项", "验证动作", "验证通过后的结论变化"], rows)


def _operation_actions_table(actions: list[dict]) -> str:
    rows = [
        [
            item.get("priority") or "-",
            item.get("action") or "-",
            item.get("owner_role") or "-",
            item.get("trigger_or_due") or "-",
            item.get("acceptance_criteria") or item.get("purpose") or "-",
            item.get("escalation_condition") or "-",
        ]
        for item in actions[:5]
    ]
    return _operation_table(["优先级", "动作", "责任角色", "截止/触发", "验收标准", "升级条件"], rows)


def _operation_evidence_table(evidence: list[dict]) -> str:
    rows = [
        [
            item.get("type") or "-",
            item.get("source") or "-",
            item.get("key_fact") or item.get("summary") or "-",
            item.get("quality") or "-",
            item.get("supports_conclusion") or "-",
            item.get("gap") or "-",
        ]
        for item in evidence
    ]
    return _operation_table(["证据类型", "来源", "关键事实", "质量", "是否支持结论", "缺口"], rows)


def _operation_appendix_html(
    payload: dict,
    markdown_to_html: MarkdownRenderer,
) -> str:
    appendix = payload.get("appendix") if isinstance(payload.get("appendix"), dict) else {}
    sql_body = (
        "### SQL 摘要\n"
        f"{appendix.get('sql_summary') or '无'}\n\n"
        "### SQL 语句\n"
        f"```sql\n{appendix.get('sql_query') or '无'}\n```"
    )
    raw_tables = appendix.get("raw_metric_tables") if isinstance(appendix.get("raw_metric_tables"), list) else []
    sample_html = "暂无最新采样明细。"
    if raw_tables:
        first_table = raw_tables[0] if isinstance(raw_tables[0], dict) else {}
        rows = first_table.get("rows") if isinstance(first_table.get("rows"), list) else []
        sample_rows = []
        for row in rows[:10]:
            if not isinstance(row, dict):
                continue
            sample_rows.append(
                [
                    row.get("create_time") or row.get("timestamp") or "-",
                    row.get("device_name") or "-",
                    row.get("status") or "-",
                    row.get("fault_code") or "无",
                    row.get("alarm_code") or "无",
                    row.get("dc_voltage") or "-",
                    row.get("speed_actual") or "-",
                    row.get("motor_temp") or "-",
                    row.get("inverter_temp") or "-",
                ]
            )
        sample_html = _operation_table(
            ["时间", "设备", "状态", "故障/事件码", "告警码", "母线电压", "实际转速", "电机温度", "变频器温度"],
            sample_rows,
        )
    trend_statistics = appendix.get("trend_statistics") if isinstance(appendix.get("trend_statistics"), list) else []
    trend_html = _operation_table(
        ["指标", "最新", "最小", "最大", "平均"],
        [
            [
                item.get("name") or "-",
                item.get("latest") or "-",
                item.get("min") or "-",
                item.get("max") or "-",
                item.get("average") or "-",
            ]
            for item in trend_statistics
            if isinstance(item, dict)
        ],
    )
    knowledge_sources = appendix.get("knowledge_sources") if isinstance(appendix.get("knowledge_sources"), list) else []
    knowledge_body = "\n\n".join(
        str(item.get("raw_excerpt") or "") for item in knowledge_sources if isinstance(item, dict)
    )
    control_status = appendix.get("control_status_decode") if isinstance(appendix.get("control_status_decode"), dict) else {}
    control_rows = [
        ["控制字", control_status.get("control_word") or "-"],
        ["状态字", control_status.get("status_word") or "-"],
        ["说明", control_status.get("note") or "通用解析，需以现场 PLC 映射表为准"],
    ]
    metadata = appendix.get("generation_metadata") if isinstance(appendix.get("generation_metadata"), dict) else {}
    metadata_html = _operation_table(
        ["项目", "内容"],
        [
            ["报告时间", metadata.get("report_time") or "-"],
            ["生成系统", metadata.get("system") or "工业设备故障诊断专家系统"],
            ["说明", metadata.get("note") or "主报告隐藏完整 SQL 与长证据，附录用于追溯。"],
        ],
    )
    return f"""
      <section class="report-section appendix-section" aria-label="附录">
        <div class="section-kicker">09</div>
        <h2>附录</h2>
        <details class="details-block"><summary>SQL 与执行信息</summary><div class="details-body">{markdown_to_html(sql_body)}</div></details>
        <details class="details-block"><summary>完整趋势统计</summary><div class="details-body">{trend_html}</div></details>
        <details class="details-block"><summary>最新采样明细</summary><div class="details-body">{sample_html}</div></details>
        <details class="details-block"><summary>状态字/控制字解析</summary><div class="details-body">{_operation_table(["对象", "值"], control_rows)}</div></details>
        <details class="details-block"><summary>知识库原文</summary><div class="details-body">{markdown_to_html(knowledge_body or "知识库未返回可展示片段。")}</div></details>
        <details class="details-block"><summary>报告生成元信息</summary><div class="details-body">{metadata_html}</div></details>
      </section>
    """


def build_operation_report_html(
    *,
    payload: dict,
    chart_section: str,
    chart_assets: str,
    markdown_to_html: MarkdownRenderer,
) -> str:
    icon, severity_label, severity_class = _severity_meta(payload.get("severity"))
    data_icon, data_label, data_class = _currentness_meta(payload.get("data_currentness_level"))
    meta_items = [
        ("报告对象", payload.get("asset") or "-"),
        ("报告类型", payload.get("report_type") or "-"),
        ("报告时间", payload.get("report_time") or "-"),
        ("数据窗口", payload.get("data_window") or "-"),
        ("样本数", payload.get("sample_count") if payload.get("sample_count") is not None else "-"),
        ("数据时效", payload.get("data_freshness_note") or payload.get("data_freshness_label") or "-"),
        ("生成系统", "工业设备故障诊断专家系统"),
    ]
    meta_html = "".join(
        f'<div class="meta-item"><div class="meta-label">{escape(label)}</div><div class="meta-value">{escape(str(value))}</div></div>'
        for label, value in meta_items
    )
    workorder = payload.get("workorder_suggestion") if isinstance(payload.get("workorder_suggestion"), dict) else {}
    trigger_conditions = workorder.get("trigger_conditions") if isinstance(workorder.get("trigger_conditions"), list) else []
    workorder_rows = [
        ["工单建议", workorder.get("decision") or "-"],
        ["可生成工单草稿", workorder.get("can_generate_draft") or "-"],
        ["触发条件", workorder.get("trigger") or "-"],
        ["创建工单触发条件", "；".join(str(item) for item in trigger_conditions) or "-"],
        ["说明", workorder.get("note") or "仅建议生成工单草稿，不直接派发。"],
    ]
    limits = payload.get("limitations") if isinstance(payload.get("limitations"), list) else []
    freshness_note = escape(str(payload.get("data_freshness_note") or ""))
    freshness_html = f'<div class="freshness-note">数据时效提示：{freshness_note}</div>' if freshness_note else ""
    chart_section_html = (
        chart_section
        .replace(">VIS<", ">03<", 1)
        .replace("核心趋势与事件持续性", "趋势与持续性", 1)
    )
    appendix_html = _operation_appendix_html(payload, markdown_to_html)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(str(payload.get("title") or "运行诊断报告"))}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #182233;
      --muted: #64748b;
      --line: #d8dee8;
      --panel: #ffffff;
      --surface: #f3f4f6;
      --sev-critical: #B91C1C;
      --sev-critical-bg: #FEE2E2;
      --sev-high: #C2410C;
      --sev-high-bg: #FFEDD5;
      --sev-warning: #A16207;
      --sev-warning-bg: #FEF3C7;
      --sev-notice: #1D4ED8;
      --sev-notice-bg: #DBEAFE;
      --sev-normal: #15803D;
      --sev-normal-bg: #DCFCE7;
      --sev-unknown: #64748B;
      --sev-unknown-bg: #F1F5F9;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      color: var(--ink);
      background: var(--surface);
      line-height: 1.58;
    }}
    .report-shell {{ max-width: 1180px; margin: 0 auto; padding: 24px 20px 44px; }}
    .report-hero,
    .executive-card,
    .report-section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 8px 22px rgba(15, 23, 42, 0.06);
    }}
    .report-hero {{ padding: 22px; }}
    .eyebrow {{ margin: 0 0 6px; color: var(--muted); font-weight: 800; }}
    h1 {{ margin: 0; font-size: 32px; line-height: 1.18; letter-spacing: 0; }}
    h2 {{ margin: 8px 0 14px; font-size: 22px; letter-spacing: 0; }}
    h3 {{ margin: 0; font-size: 15px; letter-spacing: 0; }}
    .meta-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 18px; }}
    .meta-item {{ min-width: 0; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px; background: #f8fafc; }}
    .meta-label {{ color: var(--muted); font-size: 12px; margin-bottom: 3px; }}
    .meta-value {{ font-weight: 800; overflow-wrap: anywhere; }}
    .executive-card {{ margin-top: 16px; padding: 22px; border-left: 8px solid var(--sev-{severity_class}); }}
    .exec-head {{ display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 12px; }}
    .risk-strip {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }}
    .severity-pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 6px;
      padding: 5px 10px;
      font-size: 13px;
      font-weight: 900;
      white-space: nowrap;
    }}
    .sev-critical {{ color: var(--sev-critical); background: var(--sev-critical-bg); border-color: var(--sev-critical); }}
    .sev-high {{ color: var(--sev-high); background: var(--sev-high-bg); border-color: var(--sev-high); }}
    .sev-warning {{ color: var(--sev-warning); background: var(--sev-warning-bg); border-color: var(--sev-warning); }}
    .sev-notice {{ color: var(--sev-notice); background: var(--sev-notice-bg); border-color: var(--sev-notice); }}
    .sev-normal {{ color: var(--sev-normal); background: var(--sev-normal-bg); border-color: var(--sev-normal); }}
    .sev-unknown {{ color: var(--sev-unknown); background: var(--sev-unknown-bg); border-color: var(--sev-unknown); }}
    .executive-card.sev-critical,
    .executive-card.sev-high,
    .executive-card.sev-warning,
    .executive-card.sev-notice,
    .executive-card.sev-normal,
    .executive-card.sev-unknown {{
      background: var(--panel);
    }}
    .kpi-card.sev-critical,
    .kpi-card.sev-high,
    .kpi-card.sev-warning,
    .kpi-card.sev-notice,
    .kpi-card.sev-normal,
    .kpi-card.sev-unknown {{
      background: #fbfcfe;
    }}
    .conclusion {{ font-size: 18px; font-weight: 800; margin: 0 0 14px; }}
    .exec-grid {{ display: grid; grid-template-columns: 1fr 1.2fr; gap: 18px; }}
    .exec-facts {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
    .exec-fact {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 9px 10px; background: #f8fafc; }}
    .exec-fact span {{ display: block; color: var(--muted); font-size: 12px; }}
    .exec-fact strong {{ overflow-wrap: anywhere; }}
    .report-section {{ margin-top: 16px; padding: 20px; }}
    .section-kicker {{
      display: inline-flex;
      min-width: 38px;
      height: 24px;
      align-items: center;
      justify-content: center;
      border-radius: 6px;
      background: #e5e7eb;
      color: #334155;
      font-size: 12px;
      font-weight: 900;
    }}
    .freshness-note {{ margin-bottom: 12px; border: 1px solid var(--sev-unknown); border-radius: 8px; background: var(--sev-unknown-bg); color: #334155; padding: 10px 12px; font-weight: 700; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .kpi-card {{ min-width: 0; border: 1px solid var(--line); border-left-width: 5px; border-radius: 8px; padding: 13px; background: #fbfcfe; }}
    .kpi-top {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; margin-bottom: 10px; }}
    .kpi-card strong {{ display: block; font-size: 21px; line-height: 1.2; overflow-wrap: anywhere; }}
    .kpi-card p {{ color: var(--muted); margin: 6px 0 0; font-size: 13px; }}
    .kpi-detail {{ margin: 10px 0 0; display: grid; gap: 6px; }}
    .kpi-detail div {{ display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 8px; }}
    .kpi-detail dt {{ color: var(--muted); font-size: 12px; }}
    .kpi-detail dd {{ margin: 0; font-weight: 700; overflow-wrap: anywhere; }}
    .table-wrap {{ width: 100%; overflow-x: auto; margin: 8px 0 12px; }}
    table {{ width: 100%; min-width: 760px; border-collapse: collapse; background: #fff; }}
    th, td {{ border: 1px solid var(--line); padding: 9px 10px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ background: #eef2f7; color: #29384f; font-weight: 900; }}
    tr:nth-child(even) td {{ background: #fbfcfe; }}
    .chart-section {{ background: #fff; }}
    .quality-summary, .chart-panel, .chart-details, .details-block {{ border: 1px solid var(--line); border-radius: 8px; background: #fff; }}
    .quality-summary {{ padding: 12px 14px; margin-bottom: 14px; }}
    .quality-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
    .quality-label {{ color: var(--muted); font-size: 12px; }}
    .quality-value {{ font-weight: 800; overflow-wrap: anywhere; }}
    .quality-note, .chart-status {{ color: var(--muted); font-size: 13px; }}
    .chart-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; align-items: stretch; }}
    .chart-grid-core {{ margin-bottom: 12px; }}
    .chart-panel {{ min-width: 0; padding: 14px; }}
    .chart-panel-wide, .event-panel {{ grid-column: 1 / -1; }}
    .chart-box {{ width: 100%; height: 340px; }}
    .chart-box-small {{ height: 260px; }}
    .chart-empty {{ min-height: 88px; display: grid; place-items: center; color: var(--muted); background: #f8fafc; border: 1px dashed var(--line); border-radius: 8px; }}
    .event-list {{ display: grid; gap: 8px; }}
    .event-row {{ display: grid; grid-template-columns: 1.1fr 0.8fr 0.7fr 1fr 0.9fr; gap: 10px; border: 1px solid #edf1f6; border-radius: 8px; padding: 10px 12px; background: #fbfcfe; }}
    .event-row em {{ font-style: normal; border-radius: 6px; padding: 2px 8px; background: var(--sev-warning-bg); color: var(--sev-warning); font-weight: 800; }}
    .metric-groups {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px 18px; }}
    .metric-row {{ display: flex; justify-content: space-between; gap: 10px; padding: 7px 0; border-bottom: 1px solid #edf1f6; }}
    details {{ margin-top: 10px; }}
    summary {{ cursor: pointer; padding: 11px 14px; font-weight: 900; }}
    .details-body {{ border-top: 1px solid var(--line); padding: 14px 16px; background: #fbfcfe; overflow-wrap: anywhere; }}
    .code-block {{ margin: 8px 0 16px; padding: 12px 14px; border: 1px solid #dbe3ee; border-radius: 6px; background: #f8fafc; overflow-x: auto; white-space: pre-wrap; }}
    code {{ background: #eef2f7; padding: 1px 5px; border-radius: 4px; color: #9b2c2c; }}
    blockquote {{ margin: 16px 0 0; padding: 12px 14px; border-left: 4px solid #64748b; background: #f8fafc; border-radius: 0 8px 8px 0; }}
    @media (max-width: 860px) {{
      .report-shell {{ padding: 16px 12px 32px; }}
      h1 {{ font-size: 27px; }}
      .meta-grid, .kpi-grid, .quality-grid, .chart-grid, .exec-grid {{ grid-template-columns: 1fr; }}
      .exec-head {{ display: block; }}
      .exec-head .severity-pill {{ margin-top: 10px; }}
      .exec-facts {{ grid-template-columns: 1fr; }}
      .event-row {{ grid-template-columns: 1fr 1fr; }}
      .metric-groups {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main class="report-shell">
    <header class="report-hero">
      <p class="eyebrow">运行诊断报告</p>
      <h1>{escape(str(payload.get("title") or "运行诊断报告"))}</h1>
      <div class="meta-grid">{meta_html}</div>
    </header>
    <section class="executive-card sev-{severity_class}" aria-label="一页结论">
      <div class="exec-head">
        <div>
          <div class="section-kicker">01</div>
          <h2>一页结论</h2>
        </div>
        <div class="risk-strip">
          <span class="severity-pill sev-{severity_class}">{escape(icon)} 设备风险：{escape(str(payload.get("asset_risk_label") or payload.get("severity_label") or severity_label))}</span>
          <span class="severity-pill sev-{data_class}">{escape(data_icon)} 数据时效：{escape(str(payload.get("data_currentness_label") or data_label))}</span>
          <span class="severity-pill sev-notice">◆ 处置优先级：{escape(str(payload.get("action_priority") or "-"))} / {escape(str(payload.get("action_priority_label") or "-"))}</span>
        </div>
      </div>
      <p class="conclusion">一句话结论：{escape(str(payload.get("one_sentence_conclusion") or "-"))}</p>
      <div class="exec-grid">
        <div class="exec-facts">
          <div class="exec-fact"><span>设备</span><strong>{escape(str(payload.get("asset") or "-"))}</strong></div>
          <div class="exec-fact"><span>事件码</span><strong>{escape(str(payload.get("event_code") or "无"))}</strong></div>
          <div class="exec-fact"><span>诊断置信度</span><strong>{escape(str(payload.get("confidence_level") or payload.get("confidence") or "-"))}</strong></div>
          <div class="exec-fact"><span>数据时效</span><strong>{escape(str(payload.get("data_freshness_label") or "-"))}</strong></div>
        </div>
        <div>
          <h3>最优先动作</h3>
          {_operation_list(payload.get("top_actions") if isinstance(payload.get("top_actions"), list) else [], ordered=True)}
        </div>
      </div>
    </section>
    <section class="report-section" aria-label="运行快照">
      <div class="section-kicker">02</div>
      <h2>运行快照</h2>
      {freshness_html}
      {_operation_kpi_cards(payload.get("kpi_cards") if isinstance(payload.get("kpi_cards"), list) else [])}
    </section>
    {chart_section_html}
    <section class="report-section" aria-label="关键发现">
      <div class="section-kicker">04</div>
      <h2>关键发现</h2>
      {_operation_findings_table(payload.get("findings") if isinstance(payload.get("findings"), list) else [])}
    </section>
    <section class="report-section" aria-label="原因候选">
      <div class="section-kicker">05</div>
      <h2>原因候选</h2>
      {_operation_causes_table(payload.get("cause_candidates") if isinstance(payload.get("cause_candidates"), list) else [])}
    </section>
    <section class="report-section" aria-label="处置计划">
      <div class="section-kicker">06</div>
      <h2>处置计划</h2>
      {_operation_actions_table(payload.get("action_plan") if isinstance(payload.get("action_plan"), list) else [])}
    </section>
    <section class="report-section" aria-label="工单建议">
      <div class="section-kicker">07</div>
      <h2>工单建议</h2>
      {_operation_table(["项目", "结论"], workorder_rows)}
    </section>
    <section class="report-section" aria-label="证据与边界">
      <div class="section-kicker">08</div>
      <h2>证据与边界</h2>
      {_operation_evidence_table(payload.get("evidence_summary") if isinstance(payload.get("evidence_summary"), list) else [])}
      <blockquote>{escape("；".join(str(item) for item in limits) if limits else "本报告用于辅助诊断，关键操作必须按现场规程确认。")}</blockquote>
    </section>
    {appendix_html}
  </main>
{chart_assets}
</body>
</html>
"""
