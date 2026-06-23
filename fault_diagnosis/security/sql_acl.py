"""Deterministic SQL resource filtering for the currently supported query shape."""

from __future__ import annotations

import re
from typing import Any

from .. import config
from .assets import asset_is_in_scope, data_source_terms_for_table
from ..single_agent.sql_safety import (
    ALLOWED_SQL_TABLES,
    extract_sql_table_names,
    has_unknown_sql_table,
    is_readonly_sql,
    sql_literal,
)
from .contracts import AuthContext, SqlAclResult
from .permissions import effective_resource_scope

_LIMIT_RE = re.compile(r"\blimit\s+(\d+)(?:\s*,\s*(\d+))?\s*$", re.IGNORECASE)
_TRAILING_CLAUSE_RE = re.compile(r"\b(group\s+by|having|order\s+by|limit)\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bwhere\b", re.IGNORECASE)
_UNSUPPORTED_SQL_RE = re.compile(
    r"(?:--|/\*|\*/|#)|\b(union|intersect|except|for\s+update|into\s+outfile)\b",
    re.IGNORECASE,
)


def _deny(reason: str, code: str) -> SqlAclResult:
    return SqlAclResult(allowed=False, reason=reason, blocked_reason_code=code)


def _clean_query(sql_query: str) -> str:
    return (sql_query or "").strip().rstrip(";").strip()


def _insert_predicate(sql_query: str, predicate: str) -> str:
    match = _TRAILING_CLAUSE_RE.search(sql_query)
    head = sql_query[: match.start()].rstrip() if match else sql_query.rstrip()
    tail = sql_query[match.start():].lstrip() if match else ""
    connector = " AND " if _WHERE_RE.search(head) else " WHERE "
    rewritten = f"{head}{connector}({predicate})"
    return f"{rewritten} {tail}".strip() if tail else rewritten


def _enforce_limit(sql_query: str, max_rows: int) -> tuple[str, bool]:
    match = _LIMIT_RE.search(sql_query)
    if not match:
        return f"{sql_query.rstrip()} LIMIT {max_rows}", True
    if match.group(2):
        offset = int(match.group(1))
        count = min(int(match.group(2)), max_rows)
        replacement = f"LIMIT {offset}, {count}"
    else:
        replacement = f"LIMIT {min(int(match.group(1)), max_rows)}"
    rewritten = f"{sql_query[:match.start()]}{replacement}"
    return rewritten, rewritten != sql_query


def _time_window_predicate(
    table_name: str,
    column: str,
    amount: int,
    unit: str,
    *,
    scope_predicate: str = "",
) -> str:
    live_window = f"{column} >= NOW() - INTERVAL {amount} {unit}"
    if config.SQL_TIME_ANCHOR_MODE != "latest_row_if_stale":
        return live_window
    subquery_window = live_window
    max_time_source = f"(SELECT MAX({column}) FROM {table_name})"
    if scope_predicate:
        subquery_window = f"{live_window} AND ({scope_predicate})"
        max_time_source = f"(SELECT MAX({column}) FROM {table_name} WHERE {scope_predicate})"
    return (
        f"({live_window} OR ("
        f"NOT EXISTS (SELECT 1 FROM {table_name} WHERE {subquery_window} LIMIT 1) "
        f"AND {column} >= {max_time_source} - INTERVAL {amount} {unit}"
        f"))"
    )


def _requested_assets(request: Any, decision: Any) -> list[str]:
    assets: list[str] = []
    equipment = str(getattr(request, "equipment_hint", "") or "").strip()
    if equipment:
        assets.append(equipment)
    objects = getattr(decision, "objects", {}) or {}
    assets.extend(str(value).strip() for value in objects.get("device_ids", []) if str(value).strip())
    return list(dict.fromkeys(assets))


def _in_predicate(column: str, values: list[str]) -> str:
    if not values:
        return ""
    literals = ", ".join(sql_literal(value) for value in values)
    return f"{column} IN ({literals})"


def _asset_predicate(table_name: str, assets: list[str]) -> str:
    terms = data_source_terms_for_table(table_name, assets)
    if table_name.startswith("real_data_"):
        predicates = [
            predicate
            for predicate in (
                _in_predicate("device_name", terms.get("device_name", [])),
                _in_predicate("inverter_name", terms.get("inverter_name", [])),
            )
            if predicate
        ]
        return "(" + " OR ".join(predicates) + ")" if predicates else ""
    if table_name in {"device_alarm", "device_fault_data"}:
        predicates = [
            predicate
            for predicate in (
                _in_predicate("device_name", terms.get("device_name", [])),
                _in_predicate("device_id", terms.get("device_id", [])),
            )
            if predicate
        ]
        return "(" + " OR ".join(predicates) + ")" if predicates else ""
    if table_name == "device_metric":
        return _in_predicate("device_id", terms.get("device_id", []))
    return ""


def _time_column(table_name: str) -> str:
    if table_name.startswith("real_data_"):
        return "create_time"
    return {
        "device_alarm": "timestamp",
        "device_metric": "record_time",
        "device_fault_data": "event_time",
    }.get(table_name, "")


def apply_sql_acl(
    sql_query: str,
    *,
    auth: AuthContext,
    request: Any = None,
    decision: Any = None,
) -> SqlAclResult:
    query = _clean_query(sql_query)
    if not is_readonly_sql(query):
        return _deny("只允许执行单条只读 SELECT 查询。", "sql_not_readonly")
    if query.casefold().startswith("with ") or ";" in query or _UNSUPPORTED_SQL_RE.search(query):
        return _deny("当前 SQL 结构超出安全重写能力，已拒绝执行。", "unsupported_sql_shape")
    if has_unknown_sql_table(query):
        return _deny("SQL 包含未授权数据表。", "unknown_table")
    tables = extract_sql_table_names(query)
    if not tables:
        return _deny("SQL 未识别到可授权的数据表。", "missing_table")
    if len(tables) != 1:
        return _deny("第一版权限重写仅支持单表 SQL。", "scoped_multi_table_not_supported")

    scope = effective_resource_scope(auth)
    if auth.role == "guest" and tables != {"real_data_01"}:
        return _deny("访客仅可查询 real_data_01。", "guest_table_out_of_scope")
    if auth.role == "engineer":
        allowed_tables = set(auth.table_scope).intersection(ALLOWED_SQL_TABLES)
        if not allowed_tables:
            return _deny("工程师账号未配置可查询数据表。", "missing_table_scope")
        denied_tables = tables - allowed_tables
        if denied_tables:
            return _deny(
                f"数据表不在当前账号范围：{', '.join(sorted(denied_tables))}",
                "table_out_of_scope",
            )

    filters: list[str] = []
    table_name = next(iter(tables))
    asset_filter_predicate = ""
    if auth.role == "engineer":
        requested = _requested_assets(request, decision)
        denied_assets = [asset for asset in requested if not asset_is_in_scope(asset, auth.asset_scope)]
        if denied_assets:
            return _deny(
                f"请求设备不在当前账号范围：{', '.join(denied_assets)}",
                "asset_out_of_scope",
            )
        predicate = _asset_predicate(table_name, auth.asset_scope)
        if predicate:
            if not auth.asset_scope:
                return _deny("该数据表查询要求配置设备范围。", "missing_asset_scope")
            asset_filter_predicate = predicate
            query = _insert_predicate(query, predicate)
            filters.append("engineer_asset_scope")
        elif table_name != "fault_records":
            return _deny("当前账号负责设备没有匹配该数据表的数据源。", "asset_filter_not_supported")

    if auth.role == "guest":
        time_predicate = _time_window_predicate(table_name, "create_time", 1, "HOUR")
        query = _insert_predicate(query, time_predicate)
        filters.append("guest_last_1_hour")
    else:
        time_column = _time_column(table_name)
        if time_column:
            query = _insert_predicate(
                query,
                _time_window_predicate(
                    table_name,
                    time_column,
                    scope.max_time_window_days,
                    "DAY",
                    scope_predicate=asset_filter_predicate,
                ),
            )
            filters.append(f"max_last_{scope.max_time_window_days}_days")

    query, limit_changed = _enforce_limit(query, scope.max_rows)
    if limit_changed:
        filters.append(f"limit_{scope.max_rows}")
    return SqlAclResult(
        allowed=True,
        sql_query=query,
        reason="SQL 数据权限校验通过。",
        filters_applied=filters,
    )
