"""维修工单应用服务。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from ..repositories.workorder_repository import FileWorkOrderRepository
from ..security.contracts import AuthContext
from ..security.permissions import build_auth_context
from ..security.policy_engine import asset_is_in_scope


class CreateWorkOrderPayload(BaseModel):
    title: str
    equipment_object: str
    fault_code: str | None = None
    workorder_type: str = "运行异常排查"
    priority: str = "P2"
    priority_label: str | None = None
    risk_level: str = "低"
    trigger_source: str = "故障诊断 Agent"
    diagnosis_conclusion: str = ""
    key_evidence: list[str] = Field(default_factory=list)
    processing_steps: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    task_mappings: list[dict[str, Any]] = Field(default_factory=list)
    assignee: str | None = None
    assignee_role: str | None = None
    suggested_completion_window: str | None = None
    due_at: str | None = None
    status: str = "待派单"
    thread_id: str
    trace_id: str
    request_id: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)


class UpdateWorkOrderPayload(BaseModel):
    work_order_id: str
    status: str | None = None
    assignee: str | None = None
    assignee_role: str | None = None
    due_at: str | None = None
    priority: str | None = None
    operator: str | None = None
    note: str | None = None


class WorkOrderService:
    """本地 mock 工单服务。"""

    def __init__(self, *, repository: FileWorkOrderRepository | None = None) -> None:
        self.repository = repository or FileWorkOrderRepository()

    def create_work_order(
        self,
        payload: CreateWorkOrderPayload,
        *,
        auth_context: AuthContext | None = None,
    ) -> dict[str, Any]:
        if not payload.thread_id.strip():
            raise ValueError("thread_id is required")
        if not payload.trace_id.strip():
            raise ValueError("trace_id is required")

        auth = auth_context or build_auth_context(role="guest")
        self._require_create_permission(auth, payload.equipment_object)

        data = payload.model_dump()
        data["status"] = "待派单"
        data["created_by"] = auth.user_id
        data["created_by_role"] = auth.role
        data["authorized_asset_scope"] = list(auth.asset_scope)
        if not data.get("due_at"):
            data["due_at"] = self._derive_due_at(payload.suggested_completion_window)
        return {"ok": True, "work_order": self.repository.create(data)}

    def list_work_orders(
        self,
        *,
        thread_id: str | None = None,
        trace_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        auth_context: AuthContext | None = None,
    ) -> dict[str, Any]:
        result = self.repository.list(
            thread_id=thread_id,
            trace_id=trace_id,
            status=status,
            limit=limit,
        )
        auth = auth_context or build_auth_context(role="guest")
        if auth.is_admin():
            return result
        if not auth.has_permission("tool.workorder.create"):
            raise PermissionError("当前身份无权查看维修工单。")
        items = [item for item in result["items"] if self._can_read_record(auth, item)]
        result["items"] = items
        result["summary"] = self._summarize_records(items)
        return result

    def get_work_order(
        self,
        work_order_id: str,
        *,
        auth_context: AuthContext | None = None,
    ) -> dict[str, Any] | None:
        record = self.repository.get(work_order_id)
        auth = auth_context or build_auth_context(role="guest")
        if record is not None and not self._can_read_record(auth, record):
            raise PermissionError("当前账号无权查看该工单。")
        return record

    def update_work_order(
        self,
        payload: UpdateWorkOrderPayload,
        *,
        auth_context: AuthContext | None = None,
    ) -> dict[str, Any] | None:
        auth = auth_context or build_auth_context(role="guest")
        record = self.get_work_order(payload.work_order_id, auth_context=auth)
        if record is None:
            return None
        if payload.status and payload.status not in {"待派单", "draft", "pending"}:
            raise PermissionError("工单派发或执行状态变更需要独立审批，当前接口不允许。")
        record = self.repository.update(payload.work_order_id, payload.model_dump(exclude={"work_order_id"}))
        if record is None:
            return None
        return {"ok": True, "work_order": record}

    @staticmethod
    def _require_create_permission(auth: AuthContext, equipment_object: str) -> None:
        if not auth.has_permission("tool.workorder.create"):
            raise PermissionError("当前身份无权创建维修工单。")
        if auth.role == "engineer" and not asset_is_in_scope(equipment_object, auth.asset_scope):
            raise PermissionError("只能为当前账号负责范围内的设备创建工单。")

    @staticmethod
    def _can_read_record(auth: AuthContext, record: dict[str, Any]) -> bool:
        if auth.is_admin():
            return True
        if not auth.has_permission("tool.workorder.create"):
            return False
        if record.get("created_by") == auth.user_id:
            return True
        return asset_is_in_scope(str(record.get("equipment_object") or ""), auth.asset_scope)

    @staticmethod
    def _summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        priority_counts: dict[str, int] = {}
        for record in records:
            status = str(record.get("status") or "待派单")
            priority = str(record.get("priority") or "P2")
            status_counts[status] = status_counts.get(status, 0) + 1
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
        return {"total": len(records), "status_counts": status_counts, "priority_counts": priority_counts}

    def health_check(self) -> dict[str, Any]:
        return self.repository.health_check()

    @staticmethod
    def _derive_due_at(window: str | None) -> str | None:
        text = str(window or "").strip()
        if not text:
            return None
        now = datetime.now()
        if "4小时" in text:
            return (now + timedelta(hours=4)).isoformat(timespec="minutes")
        if "24小时" in text:
            return (now + timedelta(hours=24)).isoformat(timespec="minutes")
        if "72小时" in text:
            return (now + timedelta(hours=72)).isoformat(timespec="minutes")
        if "小时" in text:
            try:
                hours = int("".join(ch for ch in text.split("小时", 1)[0] if ch.isdigit()))
                return (now + timedelta(hours=max(1, min(hours, 168)))).isoformat(timespec="minutes")
            except ValueError:
                return None
        return None
