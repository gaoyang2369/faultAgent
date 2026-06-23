"""Stable contracts shared by HTTP, workflow and data authorization layers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["guest", "engineer", "admin"]
AuthorizationMode = Literal["allow", "degrade", "deny", "clarify"]


class AuthContext(BaseModel):
    """Server-trusted identity and resource assignments for one request."""

    user_id: str
    display_name: str = ""
    role: Role = "guest"
    permissions: set[str] = Field(default_factory=set)
    asset_scope: list[str] = Field(default_factory=list)
    table_scope: list[str] = Field(default_factory=list)
    system_scope: list[str] = Field(default_factory=list)
    location_scope: list[str] = Field(default_factory=list)
    kb_scopes: list[str] = Field(default_factory=list)
    session_id: str = ""
    auth_method: str | None = None

    def is_admin(self) -> bool:
        return self.role == "admin"

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def audit_summary(self) -> dict[str, Any]:
        """Return a token/cookie-free identity summary safe for artifacts."""

        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "role": self.role,
            "asset_scope": list(self.asset_scope),
            "table_scope": list(self.table_scope),
            "system_scope": list(self.system_scope),
            "location_scope": list(self.location_scope),
            "kb_scopes": list(self.kb_scopes),
            "auth_method": self.auth_method,
        }

    def identity_payload(self) -> dict[str, Any]:
        labels = {"guest": "访客", "engineer": "维修工程师", "admin": "管理员"}
        return {
            "user_id": self.user_id,
            "user_role": labels[self.role],
            "display_name": self.display_name or labels[self.role],
            "role": self.role,
            "is_admin": self.is_admin(),
            "permissions": sorted(self.permissions),
            "asset_scope": list(self.asset_scope),
            "table_scope": list(self.table_scope),
            "allowed_tables": list(self.table_scope),
            "system_scope": list(self.system_scope),
            "location_scope": list(self.location_scope),
            "kb_scopes": list(self.kb_scopes),
            "auth_method": self.auth_method,
            "available_auth_methods": ["password", "voice_signed", "voice_exchange"],
        }


class ResourceScope(BaseModel):
    asset_ids: list[str] = Field(default_factory=list)
    allowed_tables: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    max_rows: int = Field(default=50, ge=1, le=1000)
    max_time_window_days: int = Field(default=7, ge=1, le=365)
    max_lookback_hours: int | None = Field(default=None, ge=1, le=24 * 365)
    allowed_kb_visibility: list[str] = Field(default_factory=list)
    authorized_purpose: str = "diagnosis"


class AuthorizationDecision(BaseModel):
    allowed: bool
    mode: AuthorizationMode = "allow"
    reason: str = ""
    denied_reason_code: str = ""
    allowed_nodes: dict[str, bool] = Field(default_factory=dict)
    denied_nodes: dict[str, str] = Field(default_factory=dict)
    runtime_tools: list[str] = Field(default_factory=list)
    data_scope: dict[str, Any] = Field(default_factory=dict)
    kb_scope: dict[str, Any] = Field(default_factory=dict)
    user_message: str = ""


class SqlAclResult(BaseModel):
    allowed: bool
    sql_query: str = ""
    reason: str = ""
    filters_applied: list[str] = Field(default_factory=list)
    blocked_reason_code: str = ""
