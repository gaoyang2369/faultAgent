"""Central role permissions and effective resource-scope construction."""

from __future__ import annotations

from typing import Iterable

from .contracts import AuthContext, ResourceScope, Role

WORKFLOW_KNOWLEDGE_QA = "workflow.knowledge_qa"
WORKFLOW_STATUS_QUERY = "workflow.status_query"
WORKFLOW_ALARM_TRIAGE = "workflow.alarm_triage"
WORKFLOW_FAULT_DIAGNOSIS = "workflow.fault_diagnosis"
WORKFLOW_ROOT_CAUSE_ANALYSIS = "workflow.root_cause_analysis"
WORKFLOW_HEALTH_ASSESSMENT = "workflow.health_assessment"
WORKFLOW_REPORT_GENERATION = "workflow.report_generation"
WORKFLOW_ACTION_REQUEST = "workflow.action_request"
WORKFLOW_PERMISSION_SCOPE_QUERY = "workflow.permission_scope_query"

TOOL_SQL_READ = "tool.sql.read"
TOOL_KB_SEARCH = "tool.kb.search"
TOOL_REPORT_WRITE_DRAFT = "tool.report.write_draft"
TOOL_WORKORDER_CREATE = "tool.workorder.create"
TOOL_WORKORDER_DISPATCH = "tool.workorder.dispatch"

OUTPUT_CHART_GENERATE = "output.chart.generate"
DATA_RUNTIME_READ = "data.runtime.read"
DATA_RUNTIME_READ_ALL = "data.runtime.read_all"
DATA_ALARM_READ = "data.alarm.read"
DATA_ALARM_READ_ALL = "data.alarm.read_all"
DATA_REPORT_READ = "data.report.read"
DATA_REPORT_READ_ALL = "data.report.read_all"
KB_PUBLIC_READ = "kb.public.read"
KB_INTERNAL_READ = "kb.internal.read"
KB_RESTRICTED_READ = "kb.restricted.read"
ADMIN_PDF_MANAGE = "admin.pdf.manage"
ADMIN_AUDIT_READ = "admin.audit.read"

AUTHORIZED_BUSINESS_TABLES = [
    "real_data_01",
    "real_data_02",
    "real_data_03",
    "device_alarm",
    "device_metric",
    "device_fault_data",
    "fault_records",
]

GUEST_DEFAULT_ASSETS = ["g120_motor_1"]

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    "guest": frozenset(
        {
            WORKFLOW_KNOWLEDGE_QA,
            WORKFLOW_STATUS_QUERY,
            WORKFLOW_ALARM_TRIAGE,
            WORKFLOW_PERMISSION_SCOPE_QUERY,
            TOOL_SQL_READ,
            TOOL_KB_SEARCH,
            OUTPUT_CHART_GENERATE,
            DATA_RUNTIME_READ,
            KB_PUBLIC_READ,
        }
    ),
    "engineer": frozenset(
        {
            WORKFLOW_KNOWLEDGE_QA,
            WORKFLOW_STATUS_QUERY,
            WORKFLOW_ALARM_TRIAGE,
            WORKFLOW_FAULT_DIAGNOSIS,
            WORKFLOW_ROOT_CAUSE_ANALYSIS,
            WORKFLOW_HEALTH_ASSESSMENT,
            WORKFLOW_REPORT_GENERATION,
            WORKFLOW_ACTION_REQUEST,
            WORKFLOW_PERMISSION_SCOPE_QUERY,
            TOOL_SQL_READ,
            TOOL_KB_SEARCH,
            TOOL_REPORT_WRITE_DRAFT,
            TOOL_WORKORDER_CREATE,
            OUTPUT_CHART_GENERATE,
            DATA_RUNTIME_READ,
            DATA_ALARM_READ,
            DATA_REPORT_READ,
            KB_PUBLIC_READ,
            KB_INTERNAL_READ,
        }
    ),
    "admin": frozenset(
        {
            WORKFLOW_KNOWLEDGE_QA,
            WORKFLOW_STATUS_QUERY,
            WORKFLOW_ALARM_TRIAGE,
            WORKFLOW_FAULT_DIAGNOSIS,
            WORKFLOW_ROOT_CAUSE_ANALYSIS,
            WORKFLOW_HEALTH_ASSESSMENT,
            WORKFLOW_REPORT_GENERATION,
            WORKFLOW_ACTION_REQUEST,
            WORKFLOW_PERMISSION_SCOPE_QUERY,
            TOOL_SQL_READ,
            TOOL_KB_SEARCH,
            TOOL_REPORT_WRITE_DRAFT,
            TOOL_WORKORDER_CREATE,
            OUTPUT_CHART_GENERATE,
            DATA_RUNTIME_READ,
            DATA_RUNTIME_READ_ALL,
            DATA_ALARM_READ,
            DATA_ALARM_READ_ALL,
            DATA_REPORT_READ,
            DATA_REPORT_READ_ALL,
            KB_PUBLIC_READ,
            KB_INTERNAL_READ,
            KB_RESTRICTED_READ,
            ADMIN_PDF_MANAGE,
            ADMIN_AUDIT_READ,
        }
    ),
}

KB_VISIBILITY_BY_ROLE: dict[Role, tuple[str, ...]] = {
    "guest": ("public",),
    "engineer": ("public", "internal"),
    "admin": ("public", "internal", "restricted"),
}


def _clean_scope(values: Iterable[str] | None) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values or [] if str(value).strip()))


def _clean_authorized_tables(values: Iterable[str] | None) -> list[str]:
    allowed = set(AUTHORIZED_BUSINESS_TABLES)
    return [table for table in _clean_scope(values) if table in allowed]


def build_auth_context(
    *,
    user_id: str = "guest",
    display_name: str = "",
    role: Role = "guest",
    asset_scope: Iterable[str] | None = None,
    table_scope: Iterable[str] | None = None,
    system_scope: Iterable[str] | None = None,
    location_scope: Iterable[str] | None = None,
    kb_scopes: Iterable[str] | None = None,
    session_id: str = "",
    auth_method: str | None = None,
) -> AuthContext:
    """Build effective permissions server-side; persisted permissions are ignored."""

    effective_tables = (
        ["real_data_01"]
        if role == "guest"
        else list(AUTHORIZED_BUSINESS_TABLES)
        if role == "admin"
        else _clean_scope(table_scope)
    )
    effective_kb_scopes = list(KB_VISIBILITY_BY_ROLE[role])
    requested_kb_scopes = _clean_scope(kb_scopes)
    if requested_kb_scopes:
        effective_kb_scopes = [scope for scope in effective_kb_scopes if scope in requested_kb_scopes]
    return AuthContext(
        user_id=(user_id or "guest").strip() or "guest",
        display_name=(display_name or "").strip(),
        role=role,
        permissions=set(ROLE_PERMISSIONS[role]),
        asset_scope=GUEST_DEFAULT_ASSETS if role == "guest" else _clean_scope(asset_scope),
        table_scope=effective_tables,
        system_scope=_clean_scope(system_scope),
        location_scope=_clean_scope(location_scope),
        kb_scopes=effective_kb_scopes,
        session_id=session_id,
        auth_method=auth_method,
    )


def effective_resource_scope(auth: AuthContext) -> ResourceScope:
    if auth.role == "guest":
        return ResourceScope(
            asset_ids=list(auth.asset_scope or GUEST_DEFAULT_ASSETS),
            allowed_tables=["real_data_01"],
            max_rows=50,
            max_time_window_days=1,
            max_lookback_hours=1,
            allowed_kb_visibility=["public"],
            authorized_purpose="status_or_visualization_only",
        )
    return ResourceScope(
        asset_ids=list(auth.asset_scope),
        allowed_tables=list(AUTHORIZED_BUSINESS_TABLES) if auth.role == "admin" else list(auth.table_scope),
        systems=list(auth.system_scope),
        locations=list(auth.location_scope),
        max_rows=50,
        max_time_window_days=7,
        allowed_kb_visibility=list(auth.kb_scopes or KB_VISIBILITY_BY_ROLE[auth.role]),
        authorized_purpose="diagnosis",
    )


def build_dev_auth_context(
    role: Role,
    *,
    session_id: str = "",
    user_id: str | None = None,
    asset_scope: Iterable[str] | None = None,
    allowed_tables: Iterable[str] | None = None,
) -> AuthContext:
    """Build local identities for the signed development-auth cookie flow."""

    if role == "engineer":
        default_asset_scope = ["J1号机"]
        default_allowed_tables = ["real_data_01", "device_alarm", "fault_records"]
        return build_auth_context(
            user_id=(user_id or "engineer_01").strip() or "engineer_01",
            display_name="本地维修工程师",
            role=role,
            asset_scope=default_asset_scope if asset_scope is None else _clean_scope(asset_scope),
            table_scope=(
                default_allowed_tables
                if allowed_tables is None
                else _clean_authorized_tables(allowed_tables)
            ),
            system_scope=["DCMA_LINE_1"],
            location_scope=["一号车间"],
            session_id=session_id,
            auth_method="dev-login",
        )
    if role == "admin":
        return build_auth_context(
            user_id=(user_id or "admin").strip() or "admin",
            display_name="本地管理员",
            role=role,
            session_id=session_id,
            auth_method="dev-login",
        )
    return build_auth_context(
        user_id=(user_id or "guest").strip() or "guest",
        display_name="本地访客",
        role="guest",
        session_id=session_id,
        auth_method="dev-login",
    )
