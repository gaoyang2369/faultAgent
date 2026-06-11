"""维修工单应用服务。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from ..repositories.workorder_repository import FileWorkOrderRepository


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

    def create_work_order(self, payload: CreateWorkOrderPayload) -> dict[str, Any]:
        if not payload.thread_id.strip():
            raise ValueError("thread_id is required")
        if not payload.trace_id.strip():
            raise ValueError("trace_id is required")

        data = payload.model_dump()
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
    ) -> dict[str, Any]:
        return self.repository.list(
            thread_id=thread_id,
            trace_id=trace_id,
            status=status,
            limit=limit,
        )

    def get_work_order(self, work_order_id: str) -> dict[str, Any] | None:
        return self.repository.get(work_order_id)

    def update_work_order(self, payload: UpdateWorkOrderPayload) -> dict[str, Any] | None:
        record = self.repository.update(payload.work_order_id, payload.model_dump(exclude={"work_order_id"}))
        if record is None:
            return None
        return {"ok": True, "work_order": record}

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
