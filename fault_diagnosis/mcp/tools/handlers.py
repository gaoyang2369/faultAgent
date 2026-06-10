"""Business handlers for first-batch MCP tools."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv

from ...config import DCMA_DB_NAME, KB_QUERY_TIMEOUT_SECONDS, MYSQL_USER
from ...quality.evidence import build_quality_gate_notice, summarize_evidence_quality
from ...quality.governance import build_governance_snapshot
from ...knowledge.base import get_knowledge_retriever, has_knowledge_base_index
from ...paths import PROJECT_ENV_FILE
from ...tools.report_tools import save_html_report
from ...workflows.artifact_store import get_thread_artifact
from ...workflows.contracts import WorkflowType
from ...workflows.report_mapper import map_artifact_to_report_payload
from ...workflows.runner import WorkflowExecutionError
from ...workflows.scenarios.fault_diagnosis import FaultDiagnosisRunner
from ...workflows.scenarios.report_generation import ReportGenerationRunner
from ..adapters import (
    build_artifact_items,
    build_diagnosis_findings,
    build_evidence_items,
    build_governance_info,
    build_resource_references,
    build_timeline_entries,
    extract_report_filename,
)
from ..errors import McpErrorCode, McpProtocolError
from ..resources.readers import read_diagnosis_evidence_summary, read_diagnosis_report_markdown
from ..resources.store import put_resource_content
from ..schemas import (
    DiagnoseFaultRequest,
    DiagnoseFaultResponse,
    AnalyzeFaultRequest,
    AnalyzeFaultResponse,
    ExplainFaultCodeRequest,
    ExplainFaultCodeResponse,
    ExplainReportGateRequest,
    ExplainReportGateResponse,
    EvaluateEvidenceQualityRequest,
    EvaluateEvidenceQualityResponse,
    CreateWorkOrderDraftRequest,
    CreateWorkOrderDraftResponse,
    GetEquipmentSnapshotRequest,
    GetEquipmentSnapshotResponse,
    GetEquipmentInfoRequest,
    GetEquipmentInfoResponse,
    GetEquipmentStatusRequest,
    GetEquipmentStatusResponse,
    GetFaultContextRequest,
    GetFaultContextResponse,
    GenerateDiagnosisArtifactRequest,
    GenerateDiagnosisArtifactResponse,
    GenerateDiagnosisReportRequest,
    GenerateDiagnosisReportResponse,
    ListEquipmentRequest,
    ListEquipmentResponse,
    McpEquipmentItem,
    McpHistoryItem,
    McpKnowledgeItem,
    McpMetricPoint,
    McpResourceReference,
    QueryAlarmHistoryRequest,
    QueryAlarmHistoryResponse,
    QueryEquipmentDataRequest,
    QueryEquipmentDataResponse,
    QueryEquipmentMetricsRequest,
    QueryEquipmentMetricsResponse,
    QueryEventHistoryRequest,
    QueryEventHistoryResponse,
    QueryFaultHistoryRequest,
    QueryFaultHistoryResponse,
    QueryMetricTrendRequest,
    QueryMetricTrendResponse,
    RankPossibleCausesRequest,
    RankPossibleCausesResponse,
    RetrieveFaultKnowledgeRequest,
    RetrieveFaultKnowledgeResponse,
    SearchFaultKnowledgeRequest,
    SearchFaultKnowledgeResponse,
    AnalyzeMetricTrendRequest,
    AnalyzeMetricTrendResponse,
    SuggestFaultActionsRequest,
    SuggestFaultActionsResponse,
)


_DEFAULT_METRICS = [
    "spindle_current",
    "spindle_speed",
    "spindle_load",
    "motor_temp",
    "vibration",
    "alarm_status",
]

_ALLOWED_METRICS = {
    "fault_code",
    "spindle_current",
    "spindle_speed",
    "spindle_load",
    "motor_temp",
    "vibration",
    "feed_rate",
    "workpiece_material",
    "alarm_status",
    "line_name",
    "shift_name",
    "source",
}

_DEFAULT_TREND_THRESHOLDS = {
    "spindle_load": 90.0,
    "motor_temp": 70.0,
    "vibration": 3.0,
    "spindle_current": 40.0,
}


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized_row: dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            normalized_row[key] = value.isoformat()
        elif hasattr(value, "quantize"):
            normalized_row[key] = float(value)
        else:
            normalized_row[key] = value
    return normalized_row


def _run_mysql_query(sql_query: str, params: list[Any] | tuple[Any, ...]) -> list[dict[str, Any]]:
    import pymysql

    host, port, password = _load_mysql_config()
    try:
        connection = pymysql.connect(
            host=host,
            port=port,
            user=MYSQL_USER,
            password=password,
            database=DCMA_DB_NAME,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql_query, params)
                rows = list(cursor.fetchall())
        finally:
            connection.close()
    except McpProtocolError:
        raise
    except Exception as exc:
        raise McpProtocolError(
            code=McpErrorCode.UPSTREAM_UNAVAILABLE,
            message="数据库查询不可用",
            details={"exception_type": exc.__class__.__name__},
        ) from exc
    return [_normalize_row(row) for row in rows]


def _normalize_metric_names(metric_names: list[str] | None) -> list[str]:
    metrics = [item.strip() for item in metric_names or [] if item and item.strip()]
    if not metrics:
        return list(_DEFAULT_METRICS)
    for metric in metrics:
        if metric not in _ALLOWED_METRICS:
            raise McpProtocolError(
                code=McpErrorCode.INVALID_ARGUMENT,
                message=f"暂不支持的指标字段：{metric}",
                details={"metric_name": metric},
            )
    return metrics


def _parse_datetime(value: Any) -> datetime | None:
    text = _compact(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _equipment_status_from_row(row: dict[str, Any], *, stale_after_minutes: int = 30) -> str:
    latest_time = _parse_datetime(row.get("timestamp") or row.get("latest_time"))
    if latest_time is not None:
        now = datetime.now(latest_time.tzinfo) if latest_time.tzinfo else datetime.now()
        if now - latest_time > timedelta(minutes=stale_after_minutes):
            return "offline"
    alarm_status = _compact(row.get("alarm_status")).upper()
    fault_code = _compact(row.get("fault_code"))
    if alarm_status in {"ACTIVE", "ALARM", "ERROR"} or fault_code:
        return "alarm"
    if alarm_status in {"WARN", "WARNING", "ACKED"}:
        return "warning"
    if alarm_status == "NORMAL":
        return "normal"
    return "unknown"


def _metric_value(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_route_payload() -> dict[str, Any]:
    return {
        "workflow_type": WorkflowType.FAULT_DIAGNOSIS.value,
        "confidence": "high",
        "reason": "MCP 首批能力直接进入故障诊断 workflow",
        "needs_sql": True,
        "needs_knowledge": True,
        "needs_report": True,
    }


def _build_fault_message(request: DiagnoseFaultRequest) -> str:
    parts = [_compact(request.user_message)]
    if request.equipment_id:
        parts.append(f"设备编号：{request.equipment_id}")
    if request.equipment_name:
        parts.append(f"设备名称：{request.equipment_name}")
    if request.fault_code:
        parts.append(f"故障码：{request.fault_code}")
    if request.symptoms:
        parts.append("症状：" + "；".join(_compact(item) for item in request.symptoms if _compact(item)))
    if request.start_time or request.end_time:
        parts.append(f"时间范围：{_compact(request.start_time)} ~ {_compact(request.end_time)}")
    if request.needs_report:
        parts.append(f"需要生成{request.report_format}报告")
    return "\n".join(part for part in parts if part)


async def _run_fault_diagnosis_runner(request: DiagnoseFaultRequest, thread_id: str, user_identity: str):
    runner = FaultDiagnosisRunner(
        message=_build_fault_message(request),
        thread_id=thread_id,
        user_identity=user_identity,
    )
    runner.route_result = _default_route_payload()
    return await runner.run()


async def _run_report_generation_runner(message: str, thread_id: str, user_identity: str):
    runner = ReportGenerationRunner(message=message, thread_id=thread_id, user_identity=user_identity)
    runner.route_result = {
        "workflow_type": WorkflowType.REPORT_GENERATION.value,
        "confidence": "high",
        "reason": "MCP 直接请求报告生成",
        "needs_sql": False,
        "needs_knowledge": False,
        "needs_report": True,
    }
    return await runner.run()


def _load_mysql_config() -> tuple[str, int, str]:
    load_dotenv(dotenv_path=PROJECT_ENV_FILE, override=False)
    host = (os.getenv("HOST") or "").strip()
    port_text = (os.getenv("PORT") or "3306").strip()
    password = os.getenv("MYSQL_PW") or ""
    if not host:
        raise McpProtocolError(
            code=McpErrorCode.UPSTREAM_UNAVAILABLE,
            message="未配置数据库连接信息 HOST",
            details={"env": "HOST"},
        )
    return host, int(port_text or "3306"), password


def _query_equipment_rows(request: QueryEquipmentDataRequest) -> tuple[list[dict[str, Any]], list[str], str]:
    metric_names = _normalize_metric_names(request.metric_names)
    selected_columns = ["timestamp", "device_name", "device_id", *metric_names]
    if len(selected_columns) == 3:
        selected_columns.extend(_DEFAULT_METRICS)

    where_clauses = ["(device_name = %s OR device_id = %s)"]
    params: list[Any] = [request.equipment_id, request.equipment_id]
    if request.start_time:
        where_clauses.append("timestamp >= %s")
        params.append(request.start_time)
    if request.end_time:
        where_clauses.append("timestamp <= %s")
        params.append(request.end_time)
    sql_query = (
        f"SELECT {', '.join(selected_columns)} "
        "FROM real_data "
        f"WHERE {' AND '.join(where_clauses)} "
        "ORDER BY timestamp DESC "
        "LIMIT %s"
    )
    params.append(request.limit)
    rows = _run_mysql_query(sql_query, params)
    return rows, selected_columns, sql_query


def _list_equipment_rows(keyword: str | None, limit: int) -> tuple[list[dict[str, Any]], str]:
    where_clauses: list[str] = []
    params: list[Any] = []
    if keyword:
        like_keyword = f"%{keyword}%"
        where_clauses.append("(device_id LIKE %s OR device_name LIKE %s OR line_name LIKE %s)")
        params.extend([like_keyword, like_keyword, like_keyword])
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sql_query = (
        "SELECT device_id, "
        "MAX(device_name) AS device_name, "
        "MAX(line_name) AS line_name, "
        "MAX(timestamp) AS latest_time, "
        "SUBSTRING_INDEX(GROUP_CONCAT(alarm_status ORDER BY timestamp DESC), ',', 1) AS alarm_status, "
        "SUBSTRING_INDEX(GROUP_CONCAT(fault_code ORDER BY timestamp DESC), ',', 1) AS fault_code "
        "FROM real_data "
        f"{where_sql} "
        "GROUP BY device_id "
        "ORDER BY latest_time DESC "
        "LIMIT %s"
    )
    params.append(limit)
    return _run_mysql_query(sql_query, params), sql_query


def _query_latest_equipment_row(equipment_id: str) -> tuple[dict[str, Any] | None, str]:
    sql_query = (
        "SELECT timestamp, device_name, device_id, fault_code, spindle_current, spindle_speed, "
        "spindle_load, motor_temp, vibration, feed_rate, workpiece_material, alarm_status, line_name, shift_name "
        "FROM real_data "
        "WHERE device_name = %s OR device_id = %s "
        "ORDER BY timestamp DESC "
        "LIMIT 1"
    )
    rows = _run_mysql_query(sql_query, [equipment_id, equipment_id])
    return (rows[0] if rows else None), sql_query


def _query_recent_equipment_rows(
    equipment_id: str,
    metric_names: list[str],
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], str]:
    selected_columns = ["timestamp", "device_name", "device_id", *metric_names]
    sql_query = (
        f"SELECT {', '.join(selected_columns)} "
        "FROM real_data "
        "WHERE (device_name = %s OR device_id = %s) "
    )
    params: list[Any] = [equipment_id, equipment_id]
    if start_time:
        sql_query += "AND timestamp >= %s "
        params.append(start_time)
    if end_time:
        sql_query += "AND timestamp <= %s "
        params.append(end_time)
    sql_query += "ORDER BY timestamp DESC LIMIT %s"
    params.append(limit)
    return _run_mysql_query(sql_query, params), sql_query


def _query_fault_history_rows(
    equipment_id: str | None,
    fault_code: str | None,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], str]:
    where_clauses = ["1=1"]
    params: list[Any] = []
    if equipment_id:
        where_clauses.append("(r.device_name = %s OR r.device_id = %s)")
        params.extend([equipment_id, equipment_id])
    if fault_code:
        where_clauses.append("r.fault_code = %s")
        params.append(fault_code)
    if start_time:
        where_clauses.append("r.timestamp >= %s")
        params.append(start_time)
    if end_time:
        where_clauses.append("r.timestamp <= %s")
        params.append(end_time)
    sql_query = (
        "SELECT r.timestamp AS event_time, r.device_name, r.device_id, r.fault_code, "
        "r.alarm_status, f.description, f.possible_cause, f.suggestion, f.severity "
        "FROM real_data r "
        "LEFT JOIN fault_records f ON r.fault_code = f.fault_code "
        f"WHERE {' AND '.join(where_clauses)} "
        "AND r.fault_code IS NOT NULL "
        "ORDER BY r.timestamp DESC "
        "LIMIT %s"
    )
    params.append(limit)
    return _run_mysql_query(sql_query, params), sql_query


def _query_alarm_history_rows(
    equipment_id: str | None,
    fault_code: str | None,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], str]:
    where_clauses = ["1=1"]
    params: list[Any] = []
    if equipment_id:
        where_clauses.append("(device_name = %s OR device_id = %s)")
        params.extend([equipment_id, equipment_id])
    if fault_code:
        where_clauses.append("fault_code = %s")
        params.append(fault_code)
    if start_time:
        where_clauses.append("timestamp >= %s")
        params.append(start_time)
    if end_time:
        where_clauses.append("timestamp <= %s")
        params.append(end_time)
    sql_query = (
        "SELECT COALESCE(alarm_time, timestamp) AS event_time, device_name, device_id, "
        "alarm_code, fault_code, alarm_level, status, alarm_message, line_name, shift_name "
        "FROM device_alarm "
        f"WHERE {' AND '.join(where_clauses)} "
        "ORDER BY event_time DESC "
        "LIMIT %s"
    )
    params.append(limit)
    return _run_mysql_query(sql_query, params), sql_query


def _query_fault_record_rows(fault_code: str) -> tuple[list[dict[str, Any]], str]:
    sql_query = (
        "SELECT fault_code, description, possible_cause, suggestion, severity "
        "FROM fault_records "
        "WHERE fault_code = %s "
        "LIMIT 1"
    )
    return _run_mysql_query(sql_query, [fault_code]), sql_query


def _retrieve_knowledge_docs(query: str, top_k: int) -> list[dict[str, Any]]:
    if not has_knowledge_base_index():
        raise McpProtocolError(
            code=McpErrorCode.UPSTREAM_UNAVAILABLE,
            message="当前知识库索引尚未构建，无法执行 MCP 知识检索",
            details={"action": "python rebuild_kb.py"},
        )
    retriever = get_knowledge_retriever(build_if_missing=False)
    if retriever is None:
        raise McpProtocolError(
            code=McpErrorCode.UPSTREAM_UNAVAILABLE,
            message="知识库检索器加载失败",
            details={"component": "knowledge_retriever"},
        )
    docs = retriever.invoke(query)
    result: list[dict[str, Any]] = []
    for index, doc in enumerate(docs[:top_k], start=1):
        metadata = getattr(doc, "metadata", {}) or {}
        result.append(
            {
                "knowledge_id": f"kb_{index}",
                "title": f"知识片段 {index}",
                "snippet": getattr(doc, "page_content", "") or "",
                "source_uri": f"kb://page/{metadata.get('page', 'unknown')}",
            }
        )
    return result


async def _retrieve_knowledge_docs_with_timeout(
    query: str,
    top_k: int,
    *,
    required: bool,
    trace_id: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_retrieve_knowledge_docs, query, top_k),
            timeout=KB_QUERY_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        if required:
            raise McpProtocolError(
                code=McpErrorCode.UPSTREAM_UNAVAILABLE,
                message=f"知识库检索超过 {KB_QUERY_TIMEOUT_SECONDS} 秒",
                details={"query": query, "timeout_seconds": KB_QUERY_TIMEOUT_SECONDS},
                trace_id=trace_id,
                run_id=run_id,
            ) from exc
        return []
    except McpProtocolError:
        if required:
            raise
        return []


def _knowledge_items_from_docs(docs: list[dict[str, Any]]) -> list[McpKnowledgeItem]:
    return [
        McpKnowledgeItem(
            knowledge_id=_compact(item.get("knowledge_id")) or f"kb_{index}",
            title=_compact(item.get("title")) or f"知识片段 {index}",
            snippet=str(item.get("snippet") or ""),
            source_uri=item.get("source_uri"),
            score=item.get("score"),
        )
        for index, item in enumerate(docs, start=1)
    ]


def _build_metric_points(rows: list[dict[str, Any]], metric_names: list[str]) -> list[McpMetricPoint]:
    points: list[McpMetricPoint] = []
    for row in reversed(rows):
        timestamp = _compact(row.get("timestamp"))
        for metric in metric_names:
            if metric == "alarm_status":
                continue
            value = _metric_value(row.get(metric))
            if value is not None:
                points.append(McpMetricPoint(timestamp=timestamp, metric_name=metric, value=value))
    return points


def _latest_metrics(row: dict[str, Any], metric_names: list[str]) -> dict[str, Any]:
    return {metric: row.get(metric) for metric in metric_names if metric in row}


def _aggregate_metric_rows(
    rows: list[dict[str, Any]],
    metric_names: list[str],
    aggregation: str,
) -> dict[str, Any]:
    if aggregation == "none":
        return {}
    result: dict[str, Any] = {}
    for metric in metric_names:
        if metric == "alarm_status":
            continue
        if aggregation == "latest":
            result[metric] = rows[0].get(metric) if rows else None
            continue
        values = [_metric_value(row.get(metric)) for row in rows]
        values = [value for value in values if value is not None]
        if not values:
            result[metric] = None
        elif aggregation == "avg":
            result[metric] = sum(values) / len(values)
        elif aggregation == "max":
            result[metric] = max(values)
        elif aggregation == "min":
            result[metric] = min(values)
    return result


def _summarize_metric_trends(
    rows: list[dict[str, Any]],
    metric_names: list[str],
    thresholds: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    thresholds = {**_DEFAULT_TREND_THRESHOLDS, **(thresholds or {})}
    summaries: list[dict[str, Any]] = []
    ordered_rows = list(reversed(rows))
    for metric in metric_names:
        if metric == "alarm_status":
            continue
        values = [_metric_value(row.get(metric)) for row in ordered_rows]
        values = [value for value in values if value is not None]
        if not values:
            continue
        first_value = values[0]
        last_value = values[-1]
        change = last_value - first_value
        if change > 0:
            direction = "up"
        elif change < 0:
            direction = "down"
        else:
            direction = "flat"
        threshold = thresholds.get(metric)
        over_limit_count = sum(1 for value in values if threshold is not None and value >= threshold)
        summaries.append(
            {
                "metric_name": metric,
                "first_value": first_value,
                "last_value": last_value,
                "change": round(change, 4),
                "direction": direction,
                "min_value": min(values),
                "max_value": max(values),
                "threshold": threshold,
                "over_limit_count": over_limit_count,
                "sample_count": len(values),
            }
        )
    return summaries


def _history_items_from_fault_rows(rows: list[dict[str, Any]]) -> list[McpHistoryItem]:
    return [
        McpHistoryItem(
            event_time=_compact(row.get("event_time")),
            equipment_id=_compact(row.get("device_id")),
            equipment_name=_compact(row.get("device_name")),
            code=_compact(row.get("fault_code")),
            level=_compact(row.get("severity")) or "unknown",
            status=_compact(row.get("alarm_status")),
            message=_compact(row.get("description")) or f"故障码 {_compact(row.get('fault_code'))}",
            metadata={
                "possible_cause": row.get("possible_cause") or "",
                "suggestion": row.get("suggestion") or "",
            },
        )
        for row in rows
    ]


def _history_items_from_alarm_rows(rows: list[dict[str, Any]]) -> list[McpHistoryItem]:
    return [
        McpHistoryItem(
            event_time=_compact(row.get("event_time")),
            equipment_id=_compact(row.get("device_id")),
            equipment_name=_compact(row.get("device_name")),
            code=_compact(row.get("alarm_code") or row.get("fault_code")),
            level=_compact(row.get("alarm_level")) or "unknown",
            status=_compact(row.get("status")),
            message=_compact(row.get("alarm_message")),
            metadata={
                "fault_code": row.get("fault_code") or "",
                "line_name": row.get("line_name") or "",
                "shift_name": row.get("shift_name") or "",
            },
        )
        for row in rows
    ]


def _split_text_items(value: Any) -> list[str]:
    text = _compact(value)
    if not text:
        return []
    separators = ["；", ";", "。", "\n"]
    items = [text]
    for separator in separators:
        next_items: list[str] = []
        for item in items:
            next_items.extend(part.strip(" -") for part in item.split(separator))
        items = next_items
    return [item for item in items if item]


def _build_html_report_from_artifact(thread_id: str, report_title: str | None = None) -> str | None:
    envelope = get_thread_artifact(thread_id)
    if envelope is None:
        return None
    payload = map_artifact_to_report_payload(envelope)
    findings_snapshot = list(payload.get("findings_snapshot") or [])
    report_gate_summary = dict(payload.get("report_gate_summary") or {})

    summary = f"<p>{payload.get('executive_summary') or envelope.final_answer}</p>"
    findings_html = "".join(
        f"<li>{_compact(item.get('text'))}</li>" for item in findings_snapshot if isinstance(item, dict) and _compact(item.get("text"))
    ) or "<li>暂无结构化结论</li>"
    recommendations_html = "".join(
        f"<li>{line[2:]}</li>"
        for line in str(payload.get("repair_recommendations") or "").splitlines()
        if line.strip().startswith("- ")
    ) or "<li>暂无具体处置建议</li>"

    html_result = save_html_report.invoke(
        {
            "title": report_title or payload.get("title") or "诊断报告",
            "summary": summary,
            "kpi_cards": "",
            "charts": "",
            "chart_scripts": "",
            "findings": f"<ul>{findings_html}</ul>",
            "recommendations": f"<ul>{recommendations_html}</ul>",
            "report_filename": f"{payload.get('report_filename') or 'diagnosis_report'}_html",
            "report_gate_summary": report_gate_summary,
            "findings_snapshot": findings_snapshot,
            "finding_links_snapshot": list(payload.get("finding_links_snapshot") or []),
            "evidence_records_snapshot": list(payload.get("evidence_records_snapshot") or []),
        }
    )
    return _compact(html_result)


async def get_equipment_info_handler(request: GetEquipmentInfoRequest, context) -> GetEquipmentInfoResponse:
    common = {
        "request_id": request.request_id,
        "user_identity": request.user_identity,
        "metadata": request.metadata,
    }
    if request.query_type == "list":
        keyword = _compact(request.keyword) or _compact(request.filters.get("keyword"))
        response = await list_equipment_handler(
            ListEquipmentRequest(keyword=keyword or None, limit=request.limit, **common),
            context,
        )
        return GetEquipmentInfoResponse(
            summary=response.summary,
            query_type=request.query_type,
            equipments=[item.model_dump() for item in response.equipments],
            total_count=response.total_count,
            findings=response.findings,
            evidence=response.evidence,
            timeline=response.timeline,
            artifacts=response.artifacts,
            resources=response.resources,
            governance=response.governance,
        )

    if not request.equipment_id:
        raise McpProtocolError(
            code=McpErrorCode.INVALID_ARGUMENT,
            message=f"{request.query_type} 查询必须提供 equipment_id",
            details={"field": "equipment_id", "query_type": request.query_type},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    if request.query_type == "status":
        response = await get_equipment_status_handler(
            GetEquipmentStatusRequest(
                equipment_id=request.equipment_id,
                stale_after_minutes=request.stale_after_minutes,
                **common,
            ),
            context,
        )
        status = {
            "status": response.status,
            "latest_time": response.latest_time,
            "alarm_status": response.alarm_status,
            "active_fault_code": response.active_fault_code,
            "metrics": response.metrics if request.include_metrics_summary else {},
        }
        return GetEquipmentInfoResponse(
            summary=response.summary,
            query_type=request.query_type,
            total_count=1,
            equipment_id=response.equipment_id,
            equipment_name=response.equipment_name,
            status=status,
            findings=response.findings,
            evidence=response.evidence,
            timeline=response.timeline,
            artifacts=response.artifacts,
            resources=response.resources,
            governance=response.governance,
        )

    response = await get_equipment_snapshot_handler(
        GetEquipmentSnapshotRequest(
            equipment_id=request.equipment_id,
            metric_names=request.metric_names,
            window_minutes=request.window_minutes,
            limit=request.limit,
            **common,
        ),
        context,
    )
    snapshot = {
        "metrics": response.metrics if request.include_metrics_summary else {},
        "rows": response.rows,
        "sample_count": response.sample_count,
    }
    return GetEquipmentInfoResponse(
        summary=response.summary,
        query_type=request.query_type,
        total_count=response.sample_count,
        equipment_id=response.equipment_id,
        equipment_name=response.equipment_name,
        snapshot=snapshot,
        findings=response.findings,
        evidence=response.evidence,
        timeline=response.timeline,
        artifacts=response.artifacts,
        resources=response.resources,
        governance=response.governance,
    )


async def query_equipment_metrics_handler(
    request: QueryEquipmentMetricsRequest,
    context,
) -> QueryEquipmentMetricsResponse:
    metric_names = _normalize_metric_names(request.metric_names)
    common = {
        "request_id": request.request_id,
        "user_identity": request.user_identity,
        "metadata": request.metadata,
    }

    if request.metric_mode == "trend":
        response = await query_metric_trend_handler(
            QueryMetricTrendRequest(
                equipment_id=request.equipment_id,
                metric_names=metric_names,
                start_time=request.start_time,
                end_time=request.end_time,
                limit=request.limit,
                **common,
            ),
            context,
        )
        trend_summaries = _summarize_metric_trends(response.rows, metric_names)
        return QueryEquipmentMetricsResponse(
            summary=response.summary,
            equipment_id=request.equipment_id,
            metric_mode=request.metric_mode,
            aggregation=request.aggregation,
            metrics=response.metrics,
            rows=response.rows,
            points=[point.model_dump() for point in response.points],
            trend_summaries=trend_summaries,
            aggregation_result=_aggregate_metric_rows(response.rows, metric_names, request.aggregation),
            sample_count=response.sample_count,
            findings=response.findings,
            evidence=response.evidence,
            timeline=response.timeline,
            artifacts=response.artifacts,
            resources=response.resources,
            governance=response.governance,
        )

    response = await query_equipment_data_handler(
        QueryEquipmentDataRequest(
            equipment_id=request.equipment_id,
            metric_names=metric_names,
            start_time=request.start_time,
            end_time=request.end_time,
            limit=request.limit,
            include_summary=request.include_summary,
            **common,
        ),
        context,
    )
    points = _build_metric_points(response.rows, metric_names) if request.metric_mode == "series" else []
    return QueryEquipmentMetricsResponse(
        summary=response.summary,
        equipment_id=request.equipment_id,
        metric_mode=request.metric_mode,
        aggregation=request.aggregation,
        metrics=response.metrics,
        rows=response.rows,
        points=[point.model_dump() for point in points],
        aggregation_result=_aggregate_metric_rows(response.rows, metric_names, request.aggregation),
        sample_count=response.sample_count,
        findings=response.findings,
        evidence=response.evidence,
        timeline=response.timeline,
        artifacts=response.artifacts,
        resources=response.resources,
        governance=response.governance,
    )


async def query_event_history_handler(request: QueryEventHistoryRequest, context) -> QueryEventHistoryResponse:
    common = {
        "request_id": request.request_id,
        "user_identity": request.user_identity,
        "metadata": request.metadata,
    }
    fault_records: list[McpHistoryItem] = []
    alarm_records: list[McpHistoryItem] = []
    evidence = []
    timeline = []
    artifacts = []
    resources = []
    emitted_events: list[str] = []
    governance_payload: dict[str, Any] = {}

    if request.event_type in {"fault", "all"}:
        response = await query_fault_history_handler(
            QueryFaultHistoryRequest(
                equipment_id=request.equipment_id,
                fault_code=request.fault_code,
                start_time=request.start_time,
                end_time=request.end_time,
                limit=request.limit,
                **common,
            ),
            context,
        )
        fault_records = response.records
        evidence.extend(response.evidence)
        timeline.extend(response.timeline)
        artifacts.extend(response.artifacts)
        resources.extend(response.resources)
        emitted_events.extend(response.governance.emitted_events)
        governance_payload["fault_history"] = response.governance.metadata

    if request.event_type in {"alarm", "all"}:
        response = await query_alarm_history_handler(
            QueryAlarmHistoryRequest(
                equipment_id=request.equipment_id,
                fault_code=request.fault_code,
                start_time=request.start_time,
                end_time=request.end_time,
                limit=request.limit,
                **common,
            ),
            context,
        )
        alarm_records = response.records
        evidence.extend(response.evidence)
        timeline.extend(response.timeline)
        artifacts.extend(response.artifacts)
        resources.extend(response.resources)
        emitted_events.extend(response.governance.emitted_events)
        governance_payload["alarm_history"] = response.governance.metadata

    records = [*fault_records, *alarm_records]
    if request.severity:
        severity = request.severity.lower()
        records = [item for item in records if _compact(item.level).lower() == severity]
    records.sort(key=lambda item: item.event_time, reverse=True)
    records = records[: request.limit]
    summary = f"已查询到 {len(records)} 条事件历史。"
    return QueryEventHistoryResponse(
        summary=summary,
        event_type=request.event_type,
        records=records,
        total_count=len(records),
        fault_count=len(fault_records),
        alarm_count=len(alarm_records),
        evidence=evidence,
        timeline=timeline,
        artifacts=artifacts,
        resources=resources,
        governance=build_governance_info(
            governance_payload,
            emitted_events=emitted_events or ["sql_query"],
            extra_metadata={"event_type": request.event_type},
        ),
    )


async def generate_diagnosis_artifact_handler(
    request: GenerateDiagnosisArtifactRequest,
    context,
) -> GenerateDiagnosisArtifactResponse:
    common = {
        "request_id": request.request_id,
        "user_identity": request.user_identity,
        "metadata": request.metadata,
    }
    diagnosis_result = dict(request.diagnosis_result or {})
    thread_id = _compact(request.thread_id) or _compact(request.metadata.get("thread_id")) or _compact(
        diagnosis_result.get("thread_id")
    )

    if request.artifact_type == "report":
        response = await generate_diagnosis_report_handler(
            GenerateDiagnosisReportRequest(
                thread_id=thread_id or None,
                report_title=request.report_title,
                report_format="html" if request.format == "html" else request.format,
                include_html=request.format == "html",
                summary=request.summary or _compact(diagnosis_result.get("summary")),
                **common,
            ),
            context,
        )
        return GenerateDiagnosisArtifactResponse(
            summary=response.summary,
            artifact_type=request.artifact_type,
            artifact={
                "report_title": response.report_title,
                "report_format": response.report_format,
                "audience": request.audience,
            },
            report_resource=response.report_resource,
            html_resource=response.html_resource,
            findings=response.findings,
            evidence=response.evidence,
            timeline=response.timeline,
            artifacts=response.artifacts,
            resources=response.resources,
            governance=response.governance,
        )

    if request.artifact_type == "gate_explanation":
        response = await explain_report_gate_handler(
            ExplainReportGateRequest(thread_id=thread_id or None, report_gate=request.report_gate, **common),
            context,
        )
        return GenerateDiagnosisArtifactResponse(
            summary=response.summary,
            artifact_type=request.artifact_type,
            artifact={
                "report_gate": response.report_gate,
                "explanation": response.explanation,
                "recommendation": response.recommendation,
            },
            findings=response.findings,
            evidence=response.evidence,
            timeline=response.timeline,
            artifacts=response.artifacts,
            resources=response.resources,
            governance=response.governance,
        )

    if request.artifact_type == "action_suggestion":
        equipment_id = _compact(request.equipment_id) or _compact(diagnosis_result.get("equipment_id"))
        if not equipment_id:
            raise McpProtocolError(
                code=McpErrorCode.INVALID_ARGUMENT,
                message="生成处置建议必须提供 equipment_id",
                details={"field": "equipment_id", "artifact_type": request.artifact_type},
                trace_id=context.trace_id,
                run_id=context.run_id,
            )
        response = await suggest_fault_actions_handler(
            SuggestFaultActionsRequest(
                equipment_id=equipment_id,
                fault_code=request.fault_code or diagnosis_result.get("fault_code"),
                conclusion=request.conclusion or _compact(diagnosis_result.get("conclusion")),
                report_gate=request.report_gate or dict(diagnosis_result.get("report_gate") or {}),
                **common,
            ),
            context,
        )
        return GenerateDiagnosisArtifactResponse(
            summary=response.summary,
            artifact_type=request.artifact_type,
            artifact={"work_order_hint": response.work_order_hint},
            recommended_actions=response.recommended_actions,
            findings=response.findings,
            evidence=response.evidence,
            timeline=response.timeline,
            artifacts=response.artifacts,
            resources=response.resources,
            governance=response.governance,
        )

    work_order_id = _compact(request.work_order_id) or _compact(diagnosis_result.get("work_order_id")) or context.run_id
    title = _compact(request.title) or _compact(diagnosis_result.get("title")) or "故障诊断处置工单"
    summary = request.summary or _compact(diagnosis_result.get("summary")) or _compact(request.conclusion)
    response = await create_work_order_draft_handler(
        CreateWorkOrderDraftRequest(
            work_order_id=work_order_id,
            title=title,
            severity=request.severity,
            summary=summary,
            assignee=request.assignee,
            source_report=_compact(diagnosis_result.get("source_report")),
            report_gate=request.report_gate or dict(diagnosis_result.get("report_gate") or {}),
            **common,
        ),
        context,
    )
    return GenerateDiagnosisArtifactResponse(
        summary=response.summary,
        artifact_type=request.artifact_type,
        artifact=response.draft,
        work_order_draft=response.draft,
        findings=response.findings,
        evidence=response.evidence,
        timeline=response.timeline,
        artifacts=response.artifacts,
        resources=response.resources,
        governance=response.governance,
    )


async def list_equipment_handler(request: ListEquipmentRequest, context) -> ListEquipmentResponse:
    rows, sql_query = await asyncio.to_thread(_list_equipment_rows, _compact(request.keyword) or None, request.limit)
    if not rows:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message="未查询到设备列表",
            details={"table": "real_data", "keyword": request.keyword},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    equipments = [
        McpEquipmentItem(
            equipment_id=_compact(row.get("device_id")),
            equipment_name=_compact(row.get("device_name")),
            line_name=_compact(row.get("line_name")),
            latest_time=_compact(row.get("latest_time")) or None,
            status=_equipment_status_from_row(row),
            metadata={"alarm_status": row.get("alarm_status") or "", "fault_code": row.get("fault_code") or ""},
        )
        for row in rows
    ]
    summary = f"已发现 {len(equipments)} 台设备。"
    return ListEquipmentResponse(
        summary=summary,
        equipments=equipments,
        total_count=len(equipments),
        findings=build_diagnosis_findings(
            [{"finding_id": "equipment_count", "text": summary, "confidence": "high"}],
            fallback_text=summary,
            fallback_confidence="high",
        ),
        evidence=build_evidence_items(
            [{"source_type": "sql", "title": "real_data 设备发现", "content": summary}],
            [{"evidence_id": "ev_list_equipment_sql", "type": "sql", "title": "SQL 查询", "summary": sql_query}],
        ),
        timeline=build_timeline_entries(
            [{"step_name": "list_equipment", "status": "success", "summary": summary, "finished_at": context.requested_at}]
        ),
        governance=build_governance_info({"sql_query": sql_query}, emitted_events=["sql_query"]),
    )


async def get_equipment_status_handler(request: GetEquipmentStatusRequest, context) -> GetEquipmentStatusResponse:
    row, sql_query = await asyncio.to_thread(_query_latest_equipment_row, request.equipment_id)
    if row is None:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"未查询到设备 {request.equipment_id} 的最近状态",
            details={"equipment_id": request.equipment_id, "table": "real_data"},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    metrics = _latest_metrics(row, _DEFAULT_METRICS)
    status = _equipment_status_from_row(row, stale_after_minutes=request.stale_after_minutes)
    summary = f"设备 {row.get('device_id') or request.equipment_id} 当前状态为 {status}。"
    return GetEquipmentStatusResponse(
        summary=summary,
        equipment_id=_compact(row.get("device_id")) or request.equipment_id,
        equipment_name=_compact(row.get("device_name")),
        status=status,
        latest_time=_compact(row.get("timestamp")) or None,
        alarm_status=_compact(row.get("alarm_status")),
        active_fault_code=_compact(row.get("fault_code")) or None,
        metrics=metrics,
        findings=build_diagnosis_findings(
            [{"finding_id": "equipment_status", "text": summary, "confidence": "high", "severity": "medium"}],
            fallback_text=summary,
            fallback_confidence="high",
        ),
        evidence=build_evidence_items(
            [{"source_type": "sql", "title": "real_data 最近状态", "content": summary}],
            [{"evidence_id": "ev_status_sql", "type": "sql", "title": "SQL 查询", "summary": sql_query}],
        ),
        timeline=build_timeline_entries(
            [{"step_name": "get_equipment_status", "status": "success", "summary": summary, "finished_at": context.requested_at}]
        ),
        governance=build_governance_info({"sql_query": sql_query}, emitted_events=["sql_query"]),
    )


async def get_equipment_snapshot_handler(request: GetEquipmentSnapshotRequest, context) -> GetEquipmentSnapshotResponse:
    metric_names = _normalize_metric_names(request.metric_names)
    start_time = (datetime.now() - timedelta(minutes=request.window_minutes)).isoformat(timespec="seconds")
    rows, sql_query = await asyncio.to_thread(
        _query_recent_equipment_rows,
        request.equipment_id,
        metric_names,
        start_time=start_time,
        limit=request.limit,
    )
    if not rows:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"未查询到设备 {request.equipment_id} 的快照数据",
            details={"equipment_id": request.equipment_id, "table": "real_data"},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    latest_row = rows[0]
    summary = f"已读取设备 {latest_row.get('device_id') or request.equipment_id} 最近 {len(rows)} 条快照数据。"
    return GetEquipmentSnapshotResponse(
        summary=summary,
        equipment_id=_compact(latest_row.get("device_id")) or request.equipment_id,
        equipment_name=_compact(latest_row.get("device_name")),
        metrics=_latest_metrics(latest_row, metric_names),
        rows=rows,
        sample_count=len(rows),
        evidence=build_evidence_items(
            [{"source_type": "sql", "title": "real_data 快照", "content": summary}],
            [{"evidence_id": "ev_snapshot_sql", "type": "sql", "title": "SQL 查询", "summary": sql_query}],
        ),
        timeline=build_timeline_entries(
            [{"step_name": "get_equipment_snapshot", "status": "success", "summary": summary, "finished_at": context.requested_at}]
        ),
        governance=build_governance_info({"sql_query": sql_query}, emitted_events=["sql_query"]),
    )


async def query_metric_trend_handler(request: QueryMetricTrendRequest, context) -> QueryMetricTrendResponse:
    metric_names = _normalize_metric_names(request.metric_names)
    rows, sql_query = await asyncio.to_thread(
        _query_recent_equipment_rows,
        request.equipment_id,
        metric_names,
        start_time=request.start_time,
        end_time=request.end_time,
        limit=request.limit,
    )
    if not rows:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"未查询到设备 {request.equipment_id} 的趋势数据",
            details={"equipment_id": request.equipment_id, "table": "real_data"},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    points = _build_metric_points(rows, metric_names)
    summary = f"已查询设备 {request.equipment_id} 的 {len(points)} 个指标趋势点。"
    return QueryMetricTrendResponse(
        summary=summary,
        equipment_id=request.equipment_id,
        metrics=metric_names,
        points=points,
        rows=rows,
        sample_count=len(rows),
        evidence=build_evidence_items(
            [{"source_type": "sql", "title": "real_data 趋势查询", "content": summary}],
            [{"evidence_id": "ev_metric_trend_sql", "type": "sql", "title": "SQL 查询", "summary": sql_query}],
        ),
        timeline=build_timeline_entries(
            [{"step_name": "query_metric_trend", "status": "success", "summary": summary, "finished_at": context.requested_at}]
        ),
        governance=build_governance_info({"sql_query": sql_query}, emitted_events=["sql_query"]),
    )


async def analyze_metric_trend_handler(request: AnalyzeMetricTrendRequest, context) -> AnalyzeMetricTrendResponse:
    metric_names = _normalize_metric_names(request.metric_names)
    if request.trend_data:
        rows = [_normalize_row(dict(row)) for row in request.trend_data]
        sql_query = "provided_trend_data"
    else:
        rows, sql_query = await asyncio.to_thread(
            _query_recent_equipment_rows,
            request.equipment_id,
            metric_names,
            start_time=request.start_time,
            end_time=request.end_time,
            limit=request.limit,
        )
    if not rows:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"未查询到设备 {request.equipment_id} 的趋势分析数据",
            details={"equipment_id": request.equipment_id, "table": "real_data"},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    trend_summaries = _summarize_metric_trends(rows, metric_names, request.thresholds)
    rising_metrics = [item["metric_name"] for item in trend_summaries if item.get("direction") == "up"]
    over_limit_metrics = [item["metric_name"] for item in trend_summaries if item.get("over_limit_count")]
    if over_limit_metrics:
        conclusion = f"{'、'.join(over_limit_metrics)} 存在超限样本。"
    elif rising_metrics:
        conclusion = f"{'、'.join(rising_metrics)} 呈上升趋势。"
    else:
        conclusion = "未发现明显上升或超限趋势。"

    return AnalyzeMetricTrendResponse(
        summary=conclusion,
        equipment_id=request.equipment_id,
        conclusion=conclusion,
        trend_summaries=trend_summaries,
        sample_count=len(rows),
        findings=build_diagnosis_findings(
            [{"finding_id": "trend_conclusion", "text": conclusion, "confidence": "medium"}],
            fallback_text=conclusion,
            fallback_confidence="medium",
        ),
        evidence=build_evidence_items(
            [{"source_type": "sql", "title": "real_data 趋势分析", "content": conclusion}],
            [{"evidence_id": "ev_analyze_trend_sql", "type": "sql", "title": "SQL 查询", "summary": sql_query}],
        ),
        timeline=build_timeline_entries(
            [{"step_name": "analyze_metric_trend", "status": "success", "summary": conclusion, "finished_at": context.requested_at}]
        ),
        governance=build_governance_info({"sql_query": sql_query, "trend_summaries": trend_summaries}, emitted_events=["sql_query"]),
    )


async def query_fault_history_handler(request: QueryFaultHistoryRequest, context) -> QueryFaultHistoryResponse:
    rows, sql_query = await asyncio.to_thread(
        _query_fault_history_rows,
        request.equipment_id,
        request.fault_code,
        start_time=request.start_time,
        end_time=request.end_time,
        limit=request.limit,
    )
    if not rows:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message="未查询到匹配的故障历史",
            details={"equipment_id": request.equipment_id, "fault_code": request.fault_code},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )
    records = _history_items_from_fault_rows(rows)
    summary = f"已查询到 {len(records)} 条故障历史。"
    return QueryFaultHistoryResponse(
        summary=summary,
        records=records,
        total_count=len(records),
        evidence=build_evidence_items(
            [{"source_type": "sql", "title": "故障历史查询", "content": summary}],
            [{"evidence_id": "ev_fault_history_sql", "type": "sql", "title": "SQL 查询", "summary": sql_query}],
        ),
        governance=build_governance_info({"sql_query": sql_query}, emitted_events=["sql_query"]),
    )


async def query_alarm_history_handler(request: QueryAlarmHistoryRequest, context) -> QueryAlarmHistoryResponse:
    rows, sql_query = await asyncio.to_thread(
        _query_alarm_history_rows,
        request.equipment_id,
        request.fault_code,
        start_time=request.start_time,
        end_time=request.end_time,
        limit=request.limit,
    )
    if not rows:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message="未查询到匹配的告警历史",
            details={"equipment_id": request.equipment_id, "fault_code": request.fault_code},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )
    records = _history_items_from_alarm_rows(rows)
    summary = f"已查询到 {len(records)} 条告警历史。"
    return QueryAlarmHistoryResponse(
        summary=summary,
        records=records,
        total_count=len(records),
        evidence=build_evidence_items(
            [{"source_type": "sql", "title": "告警历史查询", "content": summary}],
            [{"evidence_id": "ev_alarm_history_sql", "type": "sql", "title": "SQL 查询", "summary": sql_query}],
        ),
        governance=build_governance_info({"sql_query": sql_query}, emitted_events=["sql_query"]),
    )


async def search_fault_knowledge_handler(
    request: SearchFaultKnowledgeRequest,
    context,
) -> SearchFaultKnowledgeResponse:
    query_parts = [_compact(request.query)]
    if request.fault_code:
        query_parts.append(_compact(request.fault_code))
    if request.equipment_id:
        query_parts.append(_compact(request.equipment_id))
    query = " ".join(query_parts)
    docs = await _retrieve_knowledge_docs_with_timeout(
        query,
        request.top_k,
        required=True,
        trace_id=context.trace_id,
        run_id=context.run_id,
    )
    if not docs:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"未检索到与 `{query}` 相关的知识片段",
            details={"query": query},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    key = context.run_id
    put_resource_content("fault_knowledge_reference", key, {"query": query, "items": docs, "total_hits": len(docs)})
    knowledge_items = _knowledge_items_from_docs(docs)
    return SearchFaultKnowledgeResponse(
        summary=f"已检索到 {len(knowledge_items)} 条知识片段。",
        knowledge_items=knowledge_items,
        total_hits=len(knowledge_items),
        resources=[
            McpResourceReference(
                uri=f"knowledge://thread/{key}/latest",
                name="fault_knowledge_reference",
                media_type="application/json",
                description="知识命中详情",
            )
        ],
        governance=build_governance_info(
            {"query": query, "top_k": request.top_k},
            emitted_events=["knowledge_query"],
            extra_metadata={"thread_id": key},
        ),
    )


async def explain_fault_code_handler(request: ExplainFaultCodeRequest, context) -> ExplainFaultCodeResponse:
    rows, sql_query = await asyncio.to_thread(_query_fault_record_rows, request.fault_code)
    docs = await _retrieve_knowledge_docs_with_timeout(
        request.fault_code,
        request.top_k,
        required=False,
        trace_id=context.trace_id,
        run_id=context.run_id,
    )

    if not rows and not docs:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"未找到故障码 {request.fault_code} 的解释信息",
            details={"fault_code": request.fault_code},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    record = rows[0] if rows else {}
    meaning = _compact(record.get("description")) or (docs[0].get("snippet", "")[:200] if docs else "")
    possible_causes = _split_text_items(record.get("possible_cause"))
    suggestions = _split_text_items(record.get("suggestion"))
    knowledge_items = _knowledge_items_from_docs(docs)
    summary = f"故障码 {request.fault_code}：{meaning}"
    return ExplainFaultCodeResponse(
        summary=summary,
        fault_code=request.fault_code,
        meaning=meaning,
        possible_causes=possible_causes,
        suggestions=suggestions,
        knowledge_items=knowledge_items,
        findings=build_diagnosis_findings(
            [{"finding_id": "fault_code_meaning", "text": summary, "confidence": "high"}],
            fallback_text=summary,
            fallback_confidence="high",
        ),
        evidence=build_evidence_items(
            [{"source_type": "sql", "title": "fault_records 故障码解释", "content": summary}],
            [{"evidence_id": "ev_fault_code_sql", "type": "sql", "title": "SQL 查询", "summary": sql_query}],
        ),
        governance=build_governance_info({"sql_query": sql_query, "knowledge_hits": len(knowledge_items)}, emitted_events=["sql_query"]),
    )


async def get_fault_context_handler(request: GetFaultContextRequest, context) -> GetFaultContextResponse:
    metric_names = _normalize_metric_names(request.metric_names)
    latest_row, status_sql = await asyncio.to_thread(_query_latest_equipment_row, request.equipment_id)
    if latest_row is None:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"未查询到设备 {request.equipment_id} 的上下文数据",
            details={"equipment_id": request.equipment_id},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    rows, trend_sql = await asyncio.to_thread(
        _query_recent_equipment_rows,
        request.equipment_id,
        metric_names,
        start_time=request.start_time,
        end_time=request.end_time,
        limit=50,
    )
    fault_rows, fault_sql = await asyncio.to_thread(
        _query_fault_history_rows,
        request.equipment_id,
        request.fault_code,
        start_time=request.start_time,
        end_time=request.end_time,
        limit=10,
    )
    alarm_rows, alarm_sql = await asyncio.to_thread(
        _query_alarm_history_rows,
        request.equipment_id,
        request.fault_code,
        start_time=request.start_time,
        end_time=request.end_time,
        limit=10,
    )
    query = " ".join(part for part in [request.equipment_id, request.fault_code, *request.symptoms, "故障 上下文"] if part)
    docs = await _retrieve_knowledge_docs_with_timeout(
        query,
        request.top_k,
        required=False,
        trace_id=context.trace_id,
        run_id=context.run_id,
    )

    status = {
        "equipment_id": latest_row.get("device_id") or request.equipment_id,
        "equipment_name": latest_row.get("device_name") or "",
        "status": _equipment_status_from_row(latest_row),
        "latest_time": latest_row.get("timestamp"),
        "alarm_status": latest_row.get("alarm_status") or "",
        "fault_code": latest_row.get("fault_code") or request.fault_code,
    }
    snapshot = {
        "metrics": _latest_metrics(latest_row, metric_names),
        "sample_count": len(rows),
    }
    trend_summary = _summarize_metric_trends(rows, metric_names)
    fault_history = _history_items_from_fault_rows(fault_rows)
    alarm_history = _history_items_from_alarm_rows(alarm_rows)
    knowledge_items = _knowledge_items_from_docs(docs)
    key = context.run_id
    context_payload = {
        "status": status,
        "snapshot": snapshot,
        "trend_summary": trend_summary,
        "fault_history_count": len(fault_history),
        "alarm_history_count": len(alarm_history),
        "knowledge_hits": len(knowledge_items),
    }
    put_resource_content("diagnosis_evidence_summary", key, context_payload)
    summary = f"已聚合设备 {status['equipment_id']} 的故障上下文。"
    return GetFaultContextResponse(
        summary=summary,
        equipment_id=_compact(status["equipment_id"]) or request.equipment_id,
        fault_code=request.fault_code,
        status=status,
        snapshot=snapshot,
        trend_summary=trend_summary,
        fault_history=fault_history,
        alarm_history=alarm_history,
        knowledge_items=knowledge_items,
        resources=build_resource_references(
            thread_id=key,
            include_knowledge=False,
            include_evidence_summary=True,
            key=key,
        ),
        findings=build_diagnosis_findings(
            [{"finding_id": "fault_context", "text": summary, "confidence": "medium"}],
            fallback_text=summary,
            fallback_confidence="medium",
        ),
        evidence=build_evidence_items(
            [{"source_type": "sql", "title": "故障上下文聚合", "content": summary}],
            [
                {"evidence_id": "ev_context_status_sql", "type": "sql", "title": "状态查询", "summary": status_sql},
                {"evidence_id": "ev_context_trend_sql", "type": "sql", "title": "趋势查询", "summary": trend_sql},
                {"evidence_id": "ev_context_fault_sql", "type": "sql", "title": "故障历史查询", "summary": fault_sql},
                {"evidence_id": "ev_context_alarm_sql", "type": "sql", "title": "告警历史查询", "summary": alarm_sql},
            ],
        ),
        governance=build_governance_info(
            {"context": context_payload},
            emitted_events=["sql_query", "knowledge_query"],
            extra_metadata={"thread_id": key},
        ),
    )


async def diagnose_fault_handler(request: DiagnoseFaultRequest, context) -> DiagnoseFaultResponse:
    thread_id = _compact(request.metadata.get("thread_id")) or f"mcp-thread-{context.run_id}"
    try:
        result = await _run_fault_diagnosis_runner(request, thread_id, request.user_identity)
    except WorkflowExecutionError as exc:
        raise McpProtocolError(
            code=McpErrorCode.UPSTREAM_UNAVAILABLE,
            message=str(exc),
            details={"tool_name": "diagnose_fault"},
            trace_id=context.trace_id,
            run_id=context.run_id,
        ) from exc

    envelope = get_thread_artifact(thread_id)
    findings_snapshot = list((envelope.payload or {}).get("findings_snapshot") or []) if envelope else []
    evidence_snapshot = list((envelope.payload or {}).get("evidence_records_snapshot") or []) if envelope else []
    governance_payload = dict((envelope.payload or {}).get("governance") or {}) if envelope else {}
    report_filename = extract_report_filename(
        result.report_artifact.save_result if result.report_artifact else "",
        result.report_artifact.report_filename if result.report_artifact else None,
    )

    if envelope and report_filename:
        try:
            markdown_content = read_diagnosis_report_markdown({"filename": report_filename})
            put_resource_content("diagnosis_report_markdown", thread_id, markdown_content)
        except Exception:
            pass

        put_resource_content(
            "fault_knowledge_reference",
            thread_id,
            {
                "thread_id": thread_id,
                "query": ((envelope.payload or {}).get("knowledge_artifact") or {}).get("query") or "",
                "snippets": ((envelope.payload or {}).get("knowledge_artifact") or {}).get("snippets") or [],
                "raw_output": ((envelope.payload or {}).get("knowledge_artifact") or {}).get("raw_output") or "",
            },
        )
        put_resource_content(
            "diagnosis_evidence_summary",
            thread_id,
            {
                "thread_id": thread_id,
                "governance": governance_payload,
                "report_gate_summary": (envelope.payload or {}).get("report_gate_summary") or {},
                "findings_snapshot": findings_snapshot,
                "evidence_records_snapshot": evidence_snapshot,
            },
        )

    analysis_artifact = result.analysis_artifact.model_dump() if result.analysis_artifact else {}
    diagnosis_text = _compact(analysis_artifact.get("conclusion") or result.final_answer)
    recommended_actions = list(analysis_artifact.get("recommendations") or [])
    evidence_quality = {}
    if request.include_evidence_quality:
        evidence_quality = dict(governance_payload.get("evidence_quality") or governance_payload.get("report_gate") or {})
    ranked_causes = []
    if request.include_ranked_causes:
        ranked_causes = list(
            analysis_artifact.get("cause_rankings")
            or analysis_artifact.get("ranked_causes")
            or governance_payload.get("ranked_causes")
            or []
        )

    return DiagnoseFaultResponse(
        summary=_compact(result.final_answer)[:500],
        diagnosis=diagnosis_text,
        diagnosis_summary=diagnosis_text,
        confidence=_compact(analysis_artifact.get("confidence")) or "unknown",
        risk_level=_compact(governance_payload.get("report_gate", {}).get("risk_level"))
        or _compact(governance_payload.get("risk_level"))
        or "unknown",
        recommended_actions=recommended_actions,
        recommended_next_steps=recommended_actions,
        root_causes=list(analysis_artifact.get("missing_information") or []),
        ranked_causes=ranked_causes,
        evidence_quality=evidence_quality,
        resource_refs=[resource.uri for resource in build_resource_references(
            thread_id=thread_id,
            report_filename=report_filename,
            include_knowledge=True,
            include_evidence_summary=True,
        )],
        findings=build_diagnosis_findings(
            findings_snapshot,
            fallback_text=diagnosis_text,
            fallback_confidence=_compact(analysis_artifact.get("confidence")) or "unknown",
            severity="high",
        ),
        evidence=build_evidence_items(envelope.evidence if envelope else [], evidence_snapshot),
        timeline=build_timeline_entries(result.steps),
        artifacts=build_artifact_items(
            thread_id=thread_id,
            report_filename=report_filename,
            workflow_type=str(envelope.workflow_type) if envelope else WorkflowType.FAULT_DIAGNOSIS.value,
        ),
        resources=build_resource_references(
            thread_id=thread_id,
            report_filename=report_filename,
            include_knowledge=True,
            include_evidence_summary=True,
        ),
        governance=build_governance_info(
            governance_payload,
            emitted_events=["tool_progress", "tool_stream", "artifact_saved"],
            extra_metadata={"thread_id": thread_id},
        ),
    )


async def query_equipment_data_handler(request: QueryEquipmentDataRequest, context) -> QueryEquipmentDataResponse:
    rows, selected_columns, sql_query = await asyncio.to_thread(_query_equipment_rows, request)
    if not rows:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"未查询到设备 {request.equipment_id} 的实时数据",
            details={"equipment_id": request.equipment_id, "table": "real_data"},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    summary = f"已查询设备 {request.equipment_id} 的 {len(rows)} 条 real_data 数据。"
    key = context.run_id
    put_resource_content(
        "diagnosis_evidence_summary",
        key,
        {
            "equipment_id": request.equipment_id,
            "sample_count": len(rows),
            "metrics": selected_columns,
            "query": sql_query,
        },
    )

    return QueryEquipmentDataResponse(
        summary=summary,
        metrics=selected_columns,
        rows=rows,
        sample_count=len(rows),
        findings=build_diagnosis_findings(
            [{"finding_id": "finding_1", "text": summary, "confidence": "high", "severity": "medium"}],
            fallback_text=summary,
            fallback_confidence="high",
        ),
        evidence=build_evidence_items(
            [{"source_type": "sql", "title": "real_data 查询结果", "content": summary}],
            [{"evidence_id": "ev_sql_query", "type": "sql", "title": "SQL 查询", "summary": sql_query}],
        ),
        timeline=build_timeline_entries(
            [{"step_name": "query_real_data", "status": "success", "summary": summary, "finished_at": context.requested_at}]
        ),
        artifacts=build_artifact_items(thread_id=key, workflow_type="query_equipment_data"),
        resources=build_resource_references(
            thread_id=key,
            include_knowledge=False,
            include_evidence_summary=True,
            key=key,
        ),
        governance=build_governance_info(
            {"sql_query": sql_query, "equipment_id": request.equipment_id},
            emitted_events=["sql_query"],
            extra_metadata={"thread_id": key},
        ),
    )


async def retrieve_fault_knowledge_handler(
    request: RetrieveFaultKnowledgeRequest,
    context,
) -> RetrieveFaultKnowledgeResponse:
    if request.knowledge_type == "fault_code":
        fault_code = _compact(request.fault_code) or _compact(request.query)
        if not fault_code:
            raise McpProtocolError(
                code=McpErrorCode.INVALID_ARGUMENT,
                message="解释故障码时必须提供 fault_code 或 query",
                details={"field": "fault_code", "knowledge_type": request.knowledge_type},
                trace_id=context.trace_id,
                run_id=context.run_id,
            )
        rows, sql_query = await asyncio.to_thread(_query_fault_record_rows, fault_code)
        docs = await _retrieve_knowledge_docs_with_timeout(
            fault_code,
            request.top_k,
            required=False,
            trace_id=context.trace_id,
            run_id=context.run_id,
        )
        if not rows and not docs:
            raise McpProtocolError(
                code=McpErrorCode.DATA_NOT_FOUND,
                message=f"未找到故障码 {fault_code} 的知识说明",
                details={"fault_code": fault_code},
                trace_id=context.trace_id,
                run_id=context.run_id,
            )

        record = rows[0] if rows else {}
        record_items = []
        if record:
            record_items.append(
                McpKnowledgeItem(
                    knowledge_id=f"fault_code_{fault_code}",
                    title=f"故障码 {fault_code}",
                    snippet=_compact(record.get("description") or record.get("possible_cause")),
                    source_uri="mysql://fault_records",
                    score=None,
                )
            )
        knowledge_items = [*record_items, *_knowledge_items_from_docs(docs)]
        key = context.run_id
        put_resource_content(
            "fault_knowledge_reference",
            key,
            {"query": fault_code, "items": [item.model_dump() for item in knowledge_items], "total_hits": len(knowledge_items)},
        )
        return RetrieveFaultKnowledgeResponse(
            summary=f"已检索到 {len(knowledge_items)} 条故障码知识。",
            knowledge_items=knowledge_items,
            total_hits=len(knowledge_items),
            evidence=build_evidence_items(
                [{"source_type": "sql", "title": "fault_records 故障码知识", "content": _compact(record)}],
                [{"evidence_id": "ev_fault_code_sql", "type": "sql", "title": "SQL 查询", "summary": sql_query}],
            ),
            resources=[
                McpResourceReference(
                    uri=f"knowledge://thread/{key}/latest",
                    name="fault_knowledge_reference",
                    media_type="application/json",
                    description="知识命中详情",
                )
            ],
            governance=build_governance_info(
                {"query": fault_code, "knowledge_type": request.knowledge_type},
                emitted_events=["sql_query", "knowledge_query"],
                extra_metadata={"thread_id": key},
            ),
        )

    query = _compact(request.query) or _compact(request.fault_code)
    if not query:
        raise McpProtocolError(
            code=McpErrorCode.INVALID_ARGUMENT,
            message="检索知识时必须提供 query 或 fault_code",
            details={"field": "query", "knowledge_type": request.knowledge_type},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )
    docs = await _retrieve_knowledge_docs_with_timeout(
        query,
        request.top_k,
        required=True,
        trace_id=context.trace_id,
        run_id=context.run_id,
    )
    if not docs:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"未检索到与 `{request.query}` 相关的知识片段",
            details={"query": request.query},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    key = context.run_id
    put_resource_content(
        "fault_knowledge_reference",
        key,
        {
            "query": query,
            "items": docs,
            "total_hits": len(docs),
        },
    )

    return RetrieveFaultKnowledgeResponse(
        summary=f"已检索到 {len(docs)} 条知识片段。",
        knowledge_items=[
            McpKnowledgeItem(
                knowledge_id=item["knowledge_id"],
                title=item["title"],
                snippet=item["snippet"],
                source_uri=item["source_uri"],
                score=None,
            )
            for item in docs
        ],
        total_hits=len(docs),
        findings=build_diagnosis_findings(
            [{"finding_id": "finding_1", "text": f"已命中 {len(docs)} 条知识片段", "confidence": "high"}],
            fallback_text=f"已命中 {len(docs)} 条知识片段",
            fallback_confidence="high",
        ),
        evidence=build_evidence_items(
            [],
            [
                {
                    "evidence_id": item["knowledge_id"],
                    "type": "rag",
                    "title": item["title"],
                    "summary": item["snippet"],
                    "raw_ref": item["source_uri"],
                }
                for item in docs
            ],
        ),
        timeline=build_timeline_entries(
            [{"step_name": "retrieve_knowledge", "status": "success", "summary": f"查询：{query}", "finished_at": context.requested_at}]
        ),
        artifacts=build_artifact_items(thread_id=key, workflow_type="retrieve_fault_knowledge"),
        resources=[
            McpResourceReference(
                uri=f"knowledge://thread/{key}/latest",
                name="fault_knowledge_reference",
                media_type="application/json",
                description="知识命中详情",
            )
        ],
        governance=build_governance_info(
            {"query": query, "top_k": request.top_k, "knowledge_type": request.knowledge_type},
            emitted_events=["knowledge_query"],
            extra_metadata={"thread_id": key},
        ),
    )


async def generate_diagnosis_report_handler(
    request: GenerateDiagnosisReportRequest,
    context,
) -> GenerateDiagnosisReportResponse:
    thread_id = _compact(request.thread_id) or _compact(request.metadata.get("thread_id"))
    if not thread_id:
        raise McpProtocolError(
            code=McpErrorCode.INVALID_ARGUMENT,
            message="生成报告时必须提供 thread_id",
            details={"field": "thread_id"},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    try:
        result = await _run_report_generation_runner(
            request.summary or request.report_title or "请生成诊断报告",
            thread_id,
            request.user_identity,
        )
    except WorkflowExecutionError as exc:
        raise McpProtocolError(
            code=McpErrorCode.UPSTREAM_UNAVAILABLE,
            message=str(exc),
            details={"tool_name": "generate_diagnosis_report"},
            trace_id=context.trace_id,
            run_id=context.run_id,
        ) from exc

    upstream = get_thread_artifact(thread_id)
    if upstream is None:
        raise McpProtocolError(
            code=McpErrorCode.DATA_NOT_FOUND,
            message=f"当前线程 {thread_id} 没有可用于生成报告的结构化产物",
            details={"thread_id": thread_id},
            trace_id=context.trace_id,
            run_id=context.run_id,
        )

    report_filename = extract_report_filename(
        result.report_artifact.save_result if result.report_artifact else "",
        result.report_artifact.report_filename if result.report_artifact else None,
    )
    report_resource = None
    if report_filename:
        markdown_content = read_diagnosis_report_markdown({"filename": report_filename})
        put_resource_content("diagnosis_report_markdown", thread_id, markdown_content)
        report_resource = McpResourceReference(
            uri=f"reports://thread/{thread_id}/markdown?filename={report_filename}",
            name="diagnosis_report_markdown",
            media_type="text/markdown",
            description="Markdown 诊断报告",
        )

    html_resource = None
    if request.include_html:
        html_result = await asyncio.to_thread(_build_html_report_from_artifact, thread_id, request.report_title)
        html_filename = extract_report_filename(html_result, None)
        if html_filename:
            html_resource = McpResourceReference(
                uri=f"reports://thread/{thread_id}/html?filename={html_filename}",
                name="diagnosis_report_markdown",
                media_type="text/html",
                description="HTML 诊断报告",
            )

    put_resource_content(
        "diagnosis_evidence_summary",
        thread_id,
        {
            "thread_id": thread_id,
            "governance": (upstream.payload or {}).get("governance") or {},
            "report_gate_summary": (upstream.payload or {}).get("report_gate_summary") or {},
            "findings_snapshot": list((upstream.payload or {}).get("findings_snapshot") or []),
            "evidence_records_snapshot": list((upstream.payload or {}).get("evidence_records_snapshot") or []),
        },
    )

    return GenerateDiagnosisReportResponse(
        summary=_compact(result.final_answer),
        report_title=request.report_title or "诊断报告",
        report_format=request.report_format,
        report_resource=report_resource,
        html_resource=html_resource,
        findings=build_diagnosis_findings(
            list((upstream.payload or {}).get("findings_snapshot") or []),
            fallback_text=upstream.final_answer,
            fallback_confidence="medium",
        ),
        evidence=build_evidence_items(
            upstream.evidence,
            list((upstream.payload or {}).get("evidence_records_snapshot") or []),
        ),
        timeline=build_timeline_entries(result.steps),
        artifacts=build_artifact_items(
            thread_id=thread_id,
            report_filename=report_filename,
            workflow_type=str(upstream.workflow_type),
        ),
        resources=build_resource_references(
            thread_id=thread_id,
            report_filename=report_filename,
            include_knowledge=False,
            include_evidence_summary=True,
        ),
        governance=build_governance_info(
            (upstream.payload or {}).get("governance") or {},
            emitted_events=["report_saved"],
            extra_metadata={"thread_id": thread_id},
        ),
    )


def _split_cause_text(value: str | None) -> list[str]:
    text = _compact(value)
    if not text:
        return []
    parts = []
    for chunk in text.replace("；", "，").replace(";", "，").replace("\n", "，").split("，"):
        item = _compact(chunk)
        if item and item not in parts:
            parts.append(item)
    return parts


def _load_phase8_bundle(
    *,
    thread_id: str | None = None,
    run_id: str | None = None,
    findings_snapshot: list[dict[str, Any]] | None = None,
    finding_links_snapshot: list[dict[str, Any]] | None = None,
    evidence_records_snapshot: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    key = _compact(thread_id) or _compact(run_id)
    if key:
        try:
            payload = read_diagnosis_evidence_summary({"thread_id": thread_id, "run_id": run_id, "key": key})
        except McpProtocolError:
            payload = {}

    if payload:
        return payload

    return {
        "thread_id": thread_id,
        "run_id": run_id,
        "report_gate_summary": summarize_evidence_quality(
            findings=findings_snapshot or [],
            links=finding_links_snapshot or [],
            records=evidence_records_snapshot or [],
        ),
        "findings_snapshot": list(findings_snapshot or []),
        "finding_links_snapshot": list(finding_links_snapshot or []),
        "evidence_records_snapshot": list(evidence_records_snapshot or []),
        "governance": {},
        "evidence": [],
    }


def _rank_causes(
    candidate_causes: list[str],
    *,
    fault_record_text: str = "",
    evidence_text: str = "",
    top_k: int = 5,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    combined_text = f"{fault_record_text} {evidence_text}".strip()
    for index, cause in enumerate(candidate_causes, start=1):
        normalized = _compact(cause)
        if not normalized:
            continue
        score = 20
        if normalized in combined_text:
            score += 35
        if any(token in combined_text for token in _split_cause_text(normalized)):
            score += 20
        if any(keyword in normalized for keyword in ("过载", "负载", "电流", "温度", "振动", "刀具", "轴承")):
            score += 10
        ranked.append(
            {
                "rank": index,
                "cause": normalized,
                "score": min(score, 100),
                "reason": "与故障记录和当前证据有重合" if score >= 50 else "可疑但证据还不够强",
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    for index, item in enumerate(ranked[:top_k], start=1):
        item["rank"] = index
    return ranked[:top_k]


async def analyze_fault_handler(request: AnalyzeFaultRequest, context) -> AnalyzeFaultResponse:
    latest_row, _ = await asyncio.to_thread(_query_latest_equipment_row, request.equipment_id)
    fault_rows = []
    fault_sql = ""
    if request.fault_code:
        fault_rows, fault_sql = await asyncio.to_thread(_query_fault_record_rows, request.fault_code)

    bundle = _load_phase8_bundle(thread_id=request.thread_id, run_id=context.run_id)
    report_gate = dict(bundle.get("report_gate_summary") or {})
    evidence_quality = dict(bundle.get("governance", {}).get("evidence_quality") or report_gate or {})
    findings_snapshot = list(bundle.get("findings_snapshot") or [])
    evidence_records_snapshot = list(bundle.get("evidence_records_snapshot") or [])

    if not evidence_quality:
        evidence_quality = summarize_evidence_quality(
            findings=findings_snapshot,
            links=list(bundle.get("finding_links_snapshot") or []),
            records=evidence_records_snapshot,
        )
    report_gate = dict(evidence_quality or report_gate)

    fault_record = fault_rows[0] if fault_rows else {}
    possible_causes = _split_cause_text(fault_record.get("possible_cause")) or [
        "切削负载过大",
        "刀具磨损",
        "主轴轴承阻力增大",
        "电机电流异常",
        "加工参数过高",
    ]
    evidence_text = " ".join(
        _compact(item.get("text"))
        for item in findings_snapshot
        if isinstance(item, dict)
    )
    if latest_row:
        for field in ("spindle_load", "spindle_current", "motor_temp", "vibration"):
            value = latest_row.get(field)
            if value is not None:
                evidence_text += f" {field}={value}"

    cause_rankings = _rank_causes(
        possible_causes[: max(request.top_k, 5)],
        fault_record_text=_compact(fault_record.get("possible_cause")),
        evidence_text=evidence_text,
        top_k=request.top_k,
    )

    high_pressure = False
    if latest_row:
        try:
            high_pressure = (
                float(latest_row.get("spindle_load") or 0) >= 90
                or float(latest_row.get("spindle_current") or 0) >= 40
                or float(latest_row.get("motor_temp") or 0) >= 70
            )
        except (TypeError, ValueError):
            high_pressure = False

    if report_gate.get("gate") == "pass":
        conclusion = "当前证据已足够支撑初步判断，可以继续出报告。"
    elif report_gate.get("gate") == "review_required":
        conclusion = "当前可以出初步分析，但还不建议直接锁死唯一根因。"
    else:
        conclusion = "当前证据不足，建议继续补数据后再下正式结论。"
    if high_pressure and request.fault_code:
        conclusion = f"{request.fault_code} 对应的过载特征已经出现，{conclusion}"

    findings = build_diagnosis_findings(
        [{"finding_id": "analyze_fault", "text": conclusion, "confidence": "medium", "severity": "medium"}],
        fallback_text=conclusion,
        fallback_confidence="medium",
    )
    evidence = build_evidence_items(
        [
            {"source_type": "sql", "title": "fault_records", "content": _compact(fault_record.get("possible_cause"))},
            {"source_type": "sql", "title": "real_data", "content": _compact(latest_row or {})},
        ],
        list(evidence_records_snapshot or []),
    )
    governance = build_governance_info(
        {
            "evidence_quality": evidence_quality,
            "report_gate": report_gate,
            "analysis": conclusion,
            "fault_sql": fault_sql,
        },
        emitted_events=["sql_query", "knowledge_query"],
        extra_metadata={"thread_id": request.thread_id or context.run_id},
    )
    return AnalyzeFaultResponse(
        summary=conclusion,
        equipment_id=request.equipment_id,
        fault_code=request.fault_code,
        conclusion=conclusion,
        cause_rankings=cause_rankings,
        report_gate=report_gate,
        evidence_quality=evidence_quality,
        findings=findings,
        evidence=evidence,
        artifacts=build_artifact_items(thread_id=request.thread_id or context.run_id, workflow_type="analyze_fault"),
        resources=build_resource_references(
            thread_id=request.thread_id or context.run_id,
            include_knowledge=False,
            include_evidence_summary=True,
        ),
        governance=governance,
    )


async def rank_possible_causes_handler(
    request: RankPossibleCausesRequest,
    context,
) -> RankPossibleCausesResponse:
    fault_rows = []
    if request.fault_code:
        fault_rows, _ = await asyncio.to_thread(_query_fault_record_rows, request.fault_code)
    bundle = _load_phase8_bundle(thread_id=request.thread_id, run_id=context.run_id)
    evidence_text = " ".join(
        _compact(item.get("text"))
        for item in list(bundle.get("findings_snapshot") or [])
        if isinstance(item, dict)
    )
    fault_record = fault_rows[0] if fault_rows else {}
    causes = request.candidate_causes or _split_cause_text(fault_record.get("possible_cause"))
    if not causes:
        causes = ["切削负载过大", "刀具磨损", "主轴轴承阻力增大", "电机电流异常", "加工参数过高"]
    ranked_causes = _rank_causes(causes, fault_record_text=_compact(fault_record.get("possible_cause")), evidence_text=evidence_text, top_k=request.top_k)
    summary = "已完成候选原因排序。"
    return RankPossibleCausesResponse(
        summary=summary,
        equipment_id=request.equipment_id,
        fault_code=request.fault_code,
        ranked_causes=ranked_causes,
        findings=build_diagnosis_findings(
            [{"finding_id": "rank_possible_causes", "text": summary, "confidence": "high", "severity": "low"}],
            fallback_text=summary,
            fallback_confidence="high",
        ),
        evidence=build_evidence_items(
            [],
            list(bundle.get("evidence_records_snapshot") or []),
        ),
        governance=build_governance_info(
            {"ranked_causes": ranked_causes},
            emitted_events=["sql_query"],
            extra_metadata={"thread_id": request.thread_id or context.run_id},
        ),
    )


async def evaluate_evidence_quality_handler(
    request: EvaluateEvidenceQualityRequest,
    context,
) -> EvaluateEvidenceQualityResponse:
    bundle = _load_phase8_bundle(
        thread_id=request.thread_id,
        run_id=context.run_id,
        findings_snapshot=request.findings_snapshot,
        finding_links_snapshot=request.finding_links_snapshot,
        evidence_records_snapshot=request.evidence_records_snapshot,
    )
    findings_snapshot = list(bundle.get("findings_snapshot") or request.findings_snapshot or [])
    finding_links_snapshot = list(bundle.get("finding_links_snapshot") or request.finding_links_snapshot or [])
    evidence_records_snapshot = list(bundle.get("evidence_records_snapshot") or request.evidence_records_snapshot or [])
    evidence_quality = summarize_evidence_quality(findings_snapshot, finding_links_snapshot, evidence_records_snapshot)
    report_gate = dict(bundle.get("report_gate_summary") or {})
    if not report_gate:
        report_gate = dict(evidence_quality)
    summary = "当前证据质量已评估完成。"
    return EvaluateEvidenceQualityResponse(
        summary=summary,
        evidence_quality=evidence_quality,
        report_gate=report_gate,
        findings=build_diagnosis_findings(
            [{"finding_id": "evaluate_evidence_quality", "text": summary, "confidence": "high", "severity": "low"}],
            fallback_text=summary,
            fallback_confidence="high",
        ),
        evidence=build_evidence_items([], evidence_records_snapshot),
        governance=build_governance_info(
            {"evidence_quality": evidence_quality, "report_gate": report_gate},
            emitted_events=["evidence_review"],
            extra_metadata={"thread_id": request.thread_id or context.run_id},
        ),
    )


async def explain_report_gate_handler(
    request: ExplainReportGateRequest,
    context,
) -> ExplainReportGateResponse:
    bundle = _load_phase8_bundle(thread_id=request.thread_id, run_id=context.run_id)
    report_gate = dict(request.report_gate or bundle.get("report_gate_summary") or {})
    evidence_quality = dict(bundle.get("governance", {}).get("evidence_quality") or report_gate or {})
    explanation = build_quality_gate_notice(report_gate) or "当前门禁通过，可以继续出报告。"
    recommendation = _compact(report_gate.get("recommended_action")) or _compact(evidence_quality.get("recommended_action"))
    if not recommendation:
        recommendation = "先保留初步判断，若证据还不够就继续补 SQL 或知识证据。"
    summary = "已解释当前报告门禁。"
    return ExplainReportGateResponse(
        summary=summary,
        report_gate=report_gate,
        explanation=explanation,
        recommendation=recommendation,
        findings=build_diagnosis_findings(
            [{"finding_id": "explain_report_gate", "text": explanation, "confidence": "high", "severity": "low"}],
            fallback_text=explanation,
            fallback_confidence="high",
        ),
        evidence=build_evidence_items([], list(bundle.get("evidence_records_snapshot") or [])),
        governance=build_governance_info(
            {"report_gate": report_gate, "evidence_quality": evidence_quality},
            emitted_events=["evidence_review"],
            extra_metadata={"thread_id": request.thread_id or context.run_id},
        ),
    )


async def suggest_fault_actions_handler(
    request: SuggestFaultActionsRequest,
    context,
) -> SuggestFaultActionsResponse:
    fault_rows = []
    if request.fault_code:
        fault_rows, _ = await asyncio.to_thread(_query_fault_record_rows, request.fault_code)
    fault_record = fault_rows[0] if fault_rows else {}
    bundle = _load_phase8_bundle(thread_id=None, run_id=context.run_id)
    report_gate = dict(request.report_gate or bundle.get("report_gate_summary") or {})
    possible_actions = _split_cause_text(fault_record.get("suggestion"))
    if not possible_actions:
        possible_actions = [
            "先降低切削负载",
            "检查刀具磨损和装夹状态",
            "核对主轴电流和温升",
            "检查润滑和轴承阻力",
        ]
    work_order_hint = "建议先出初步报告，再根据现场复核结果决定是否生成正式工单。"
    if report_gate.get("gate") == "pass":
        work_order_hint = "证据已足够，可以直接整理工单草稿并进入执行。"
    summary = "已生成处置建议。"
    return SuggestFaultActionsResponse(
        summary=summary,
        equipment_id=request.equipment_id,
        fault_code=request.fault_code,
        recommended_actions=possible_actions[: request.top_k],
        work_order_hint=work_order_hint,
        findings=build_diagnosis_findings(
            [{"finding_id": "suggest_fault_actions", "text": work_order_hint, "confidence": "medium", "severity": "low"}],
            fallback_text=work_order_hint,
            fallback_confidence="medium",
        ),
        evidence=build_evidence_items([], list(bundle.get("evidence_records_snapshot") or [])),
        governance=build_governance_info(
            {"report_gate": report_gate, "recommended_actions": possible_actions},
            emitted_events=["action_suggestion"],
            extra_metadata={"thread_id": context.run_id},
        ),
    )


async def create_work_order_draft_handler(
    request: CreateWorkOrderDraftRequest,
    context,
) -> CreateWorkOrderDraftResponse:
    report_gate = dict(request.report_gate or {})
    draft = {
        "work_order_id": request.work_order_id,
        "title": request.title,
        "severity": request.severity,
        "summary": request.summary,
        "assignee": request.assignee,
        "source_report": request.source_report,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "report_gate": report_gate,
        "publication_status": "draft",
    }
    summary = "工单草稿已生成。"
    return CreateWorkOrderDraftResponse(
        summary=summary,
        work_order_id=request.work_order_id,
        draft=draft,
        publication_status="draft",
        findings=build_diagnosis_findings(
            [{"finding_id": "create_work_order_draft", "text": summary, "confidence": "high", "severity": "low"}],
            fallback_text=summary,
            fallback_confidence="high",
        ),
        evidence=build_evidence_items([], []),
        governance=build_governance_info(
            {"report_gate": report_gate, "draft": draft},
            emitted_events=["work_order_draft"],
            extra_metadata={"thread_id": context.run_id},
        ),
    )
