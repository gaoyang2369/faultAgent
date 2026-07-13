"""Real SQL runtime node for Agent Engine V2."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import SqlStepArtifact
from fault_diagnosis.domain.diagnosis.runtime_status import (
    DataResolutionPolicy,
    TimeWindow,
    build_runtime_status_assessment,
    build_runtime_status_claim,
)
from fault_diagnosis.domain.security.sql_acl import apply_sql_acl
from fault_diagnosis.domain.diagnosis.evidence.sql import build_sql_evidence_items
from fault_diagnosis.domain.diagnosis.steps.sql_result_parser import parse_sql_rows, parse_sql_scalar_datetime
from fault_diagnosis.domain.security.sql_safety import extract_sql_table_names
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from ..tool_runtime import ToolRuntime
from .base import auth_context, build_decision_stub, build_request, input_value, model_to_dict, models_to_dicts
from fault_diagnosis import config


class SqlNode:
    node_type = "sql"

    def __init__(self, tool_runtime: ToolRuntime) -> None:
        self.tool_runtime = tool_runtime

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        sql_query = str(input_value(node, "sql_query", "") or "").strip()
        if not sql_query:
            return NodeExecutionOutput(
                status="failed",
                output={"success": False, "error": "missing_sql_query"},
                error={"code": "missing_sql_query", "message": "SQL node requires inputs.sql_query."},
            )

        request = build_request(state, node, goal="SQL 运行数据查询")
        decision = build_decision_stub(node)
        auth = auth_context(state)
        policy = DataResolutionPolicy(
            strategy=str(getattr(config, "DATA_RESOLUTION_STRATEGY", "realtime_then_latest")),
            data_environment=str(getattr(config, "DATA_ENVIRONMENT", "simulation")),
            high_risk_requires_realtime=bool(getattr(config, "HIGH_RISK_REQUIRES_REALTIME_DATA", False)),
        )
        now = datetime.now()
        requested_window = TimeWindow(start=now - timedelta(hours=1), end=now)
        tool_call_refs: list[str] = []
        sql_used: list[str] = []
        filters_applied: list[str] = []
        raw_output: Any = []
        rows: list[dict[str, Any]] = []
        latest_sample_time: datetime | None = None
        fallback_window: TimeWindow | None = None
        realtime_count = 0

        if policy.queries_realtime:
            acl = apply_sql_acl(
                sql_query,
                auth=auth,
                request=request,
                decision=decision,
                resolution_phase="realtime",
            )
            denied = _blocked_output(acl)
            if denied is not None:
                return denied
            checked_sql = acl.sql_query
            if bool(input_value(node, "use_checker", False)):
                checked_candidate = str(self.tool_runtime.invoke_sql_tool("sql_db_query_checker", checked_sql) or "").strip()
                tool_call_refs.append("sql_db_query_checker")
                if checked_candidate:
                    checked_acl = apply_sql_acl(
                        checked_candidate,
                        auth=auth,
                        request=request,
                        decision=decision,
                        resolution_phase="realtime",
                    )
                    denied = _blocked_output(checked_acl)
                    if denied is not None:
                        return denied
                    checked_sql = checked_acl.sql_query
                    filters_applied.extend(checked_acl.filters_applied)
            raw_output = self.tool_runtime.invoke_sql_tool("sql_db_query", checked_sql)
            tool_call_refs.append("sql_db_query")
            sql_used.append(checked_sql)
            filters_applied.extend(acl.filters_applied)
            rows = parse_sql_rows(raw_output)
            realtime_count = len(rows)
            latest_sample_time = _latest_row_time(rows)

        tables = sorted(extract_sql_table_names(sql_query))
        table = tables[0] if tables else ""
        if not rows and policy.allows_latest and table:
            latest_sql = f"SELECT DATE_FORMAT(MAX(create_time), '%Y-%m-%d %H:%i:%s') FROM {table} WHERE 1=1"
            latest_acl = apply_sql_acl(
                latest_sql,
                auth=auth,
                request=request,
                decision=decision,
                resolution_phase="latest_lookup",
            )
            denied = _blocked_output(latest_acl)
            if denied is not None:
                return denied
            latest_raw = self.tool_runtime.invoke_sql_tool("sql_db_query", latest_acl.sql_query)
            tool_call_refs.append("sql_db_query")
            sql_used.append(latest_acl.sql_query)
            filters_applied.extend(latest_acl.filters_applied)
            latest_sample_time = parse_sql_scalar_datetime(latest_raw)
            if latest_sample_time is not None:
                fallback_window = TimeWindow(start=latest_sample_time - timedelta(hours=1), end=latest_sample_time)
                fallback_acl = apply_sql_acl(
                    sql_query,
                    auth=auth,
                    request=request,
                    decision=decision,
                    resolution_phase="latest_window",
                    resolution_anchor=latest_sample_time,
                )
                denied = _blocked_output(fallback_acl)
                if denied is not None:
                    return denied
                raw_output = self.tool_runtime.invoke_sql_tool("sql_db_query", fallback_acl.sql_query)
                tool_call_refs.append("sql_db_query")
                sql_used.append(fallback_acl.sql_query)
                filters_applied.extend(fallback_acl.filters_applied)
                rows = parse_sql_rows(raw_output)

        resolved_window = _row_window(rows)
        basis = policy.resolve(
            requested_window=requested_window,
            realtime_row_count=realtime_count,
            realtime_window=resolved_window if realtime_count else None,
            latest_sample_time=latest_sample_time,
            fallback_window=resolved_window or fallback_window,
            fallback_row_count=len(rows) if not realtime_count else 0,
        )
        degraded_notice = str(input_value(node, "degraded_notice", "") or "").strip()
        summary = f"SQL 查询完成，解析出 {len(rows)} 条运行记录。"
        if degraded_notice:
            summary = f"{degraded_notice} {summary}"
        artifact = SqlStepArtifact(
            success=True,
            summary=summary,
            sql_used=sql_used,
            result_preview=str(raw_output)[:1200],
            raw_output=str(raw_output),
            access_scope=auth.audit_summary(),
            filters_applied=list(dict.fromkeys(filters_applied)),
            row_count=len(rows),
            parse_status="parsed" if rows else "no_parseable_rows",
            source_table=table,
            data_state="ok" if rows else "empty",
            query_status="success" if rows else "empty",
            data_basis=basis.model_dump(mode="json"),
            requested_window=requested_window.model_dump(mode="json"),
            resolved_window=basis.resolved_window.model_dump(mode="json") if basis.resolved_window else {},
            latest_sample_time=basis.latest_sample_time.isoformat(sep=" ") if basis.latest_sample_time else "",
            sample_count=len(rows),
        )
        evidence_models = build_sql_evidence_items(artifact, request=request)
        assessment = build_runtime_status_assessment(
            device=request.equipment_hint or "",
            query_status=artifact.query_status,
            data_basis=basis,
            evidence_items=evidence_models,
        )
        artifact.runtime_status = assessment.runtime_status
        artifact.status_reasons = list(assessment.status_reasons)
        artifact.key_findings = list(assessment.key_findings)
        artifact.supporting_evidence_ids = list(assessment.supporting_evidence_ids)
        node_id = str(node.get("node_id") or "sql")
        runtime_artifact_id = str(input_value(node, "artifact_id", "") or node.get("artifact_id") or "")
        artifact.artifact_id = runtime_artifact_id
        sql_artifacts = state.artifacts.setdefault("sql_artifacts", {})
        sql_rows_by_device = state.artifacts.setdefault("sql_rows_by_device", {})
        assessments = state.artifacts.setdefault("runtime_status_assessments", {})
        sql_artifacts[runtime_artifact_id] = artifact
        sql_rows_by_device[assessment.device] = rows
        assessments[assessment.device] = assessment
        state.artifacts.setdefault("sql_artifact_ids", []).append(runtime_artifact_id)
        state.artifacts["sql_artifact"] = artifact
        state.artifacts["sql_rows"] = rows
        state.artifacts["runtime_status_assessment"] = assessment
        evidence = models_to_dicts(evidence_models)
        claim = build_runtime_status_claim(assessment, claim_id=f"claim_{str(node.get('node_id') or 'sql')}_runtime_status")
        return NodeExecutionOutput(
            output={
                "success": True,
                "artifact": model_to_dict(artifact),
                "runtime_status_assessment": model_to_dict(assessment),
                "normalized_rows": rows,
                "sql_used": sql_used,
                "claim_type": "runtime_status_assessment",
                "artifact_id": runtime_artifact_id,
                "device": assessment.device,
            },
            tool_call_refs=tool_call_refs,
            proposed_evidence=evidence,
            proposed_claims=[model_to_dict(claim)],
            artifacts={
                "sql_artifact": artifact,
                "sql_rows": rows,
                "runtime_status_assessment": assessment,
                "sql_artifacts": sql_artifacts,
                "sql_rows_by_device": sql_rows_by_device,
                "runtime_status_assessments": assessments,
                "sql_artifact_ids": state.artifacts["sql_artifact_ids"],
            },
        )


def _blocked_output(acl: Any) -> NodeExecutionOutput | None:
    if acl.allowed:
        return None
    return NodeExecutionOutput(
        status="blocked",
        output={"success": False, "blocked_reason": acl.reason, "blocked_reason_code": acl.blocked_reason_code},
        error={"code": acl.blocked_reason_code or "sql_acl_denied", "message": acl.reason},
    )


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("T", " "))
    except ValueError:
        return None


def _latest_row_time(rows: list[dict[str, Any]]) -> datetime | None:
    return _parse_time(rows[0].get("create_time")) if rows else None


def _row_window(rows: list[dict[str, Any]]) -> TimeWindow | None:
    values = [_parse_time(row.get("create_time")) for row in rows]
    clean = [item for item in values if item is not None]
    return TimeWindow(start=min(clean), end=max(clean)) if clean else None
