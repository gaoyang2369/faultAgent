"""Real SQL runtime node for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import SqlStepArtifact
from fault_diagnosis.domain.security.sql_acl import apply_sql_acl
from fault_diagnosis.domain.diagnosis.evidence.sql import build_sql_evidence_items
from fault_diagnosis.domain.diagnosis.steps.sql_result_parser import parse_sql_rows
from fault_diagnosis.domain.security.sql_safety import extract_sql_table_names
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from ..tool_runtime import ToolRuntime
from .base import auth_context, build_decision_stub, build_request, input_value, model_to_dict, models_to_dicts


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
        acl = apply_sql_acl(sql_query, auth=auth, request=request, decision=decision)
        if not acl.allowed:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "blocked_reason": acl.reason, "blocked_reason_code": acl.blocked_reason_code},
                error={"code": acl.blocked_reason_code or "sql_acl_denied", "message": acl.reason},
            )

        checked_sql = acl.sql_query
        tool_call_refs: list[str] = []
        if bool(input_value(node, "use_checker", False)):
            checked = self.tool_runtime.invoke_sql_tool("sql_db_query_checker", checked_sql)
            tool_call_refs.append("sql_db_query_checker")
            checked_text = str(checked or "").strip()
            if checked_text:
                second_acl = apply_sql_acl(checked_text, auth=auth, request=request, decision=decision)
                if not second_acl.allowed:
                    return NodeExecutionOutput(
                        status="blocked",
                        output={
                            "success": False,
                            "blocked_reason": second_acl.reason,
                            "blocked_reason_code": second_acl.blocked_reason_code,
                        },
                        error={"code": second_acl.blocked_reason_code or "sql_acl_denied", "message": second_acl.reason},
                    )
                checked_sql = second_acl.sql_query
                acl = second_acl

        raw_output = self.tool_runtime.invoke_sql_tool("sql_db_query", checked_sql)
        tool_call_refs.append("sql_db_query")
        rows = parse_sql_rows(raw_output)
        tables = sorted(extract_sql_table_names(checked_sql))
        artifact = SqlStepArtifact(
            success=True,
            summary=f"SQL 查询完成，解析出 {len(rows)} 条运行记录。",
            sql_used=[checked_sql],
            result_preview=str(raw_output)[:1200],
            raw_output=str(raw_output),
            access_scope=auth.audit_summary(),
            filters_applied=list(acl.filters_applied),
            row_count=len(rows),
            parse_status="parsed" if rows else "no_parseable_rows",
            source_table=tables[0] if tables else "",
            data_state="ok" if rows else "empty",
        )
        state.artifacts["sql_artifact"] = artifact
        state.artifacts["sql_rows"] = rows
        evidence = models_to_dicts(build_sql_evidence_items(artifact, request=request))
        return NodeExecutionOutput(
            output={
                "success": True,
                "artifact": model_to_dict(artifact),
                "normalized_rows": rows,
                "sql_used": checked_sql,
            },
            tool_call_refs=tool_call_refs,
            proposed_evidence=evidence,
            artifacts={"sql_artifact": artifact, "sql_rows": rows},
        )
