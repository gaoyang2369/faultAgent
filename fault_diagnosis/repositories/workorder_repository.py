"""本地 mock 维修工单 repository。"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from ..common.paths import RUN_STATE_DIR

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_workorder_id(value: str | None) -> str:
    raw = str(value or "").strip()
    return _SAFE_ID_RE.sub("", raw)[:64]


class FileWorkOrderRepository:
    """基于 JSONL 文件的演示工单表。"""

    def __init__(self, *, root_dir: str | os.PathLike[str] | None = None) -> None:
        self.root_dir = Path(root_dir or os.getenv("WORKORDER_DIR") or Path(RUN_STATE_DIR) / "workorders")
        self.path = self.root_dir / "workorders.jsonl"
        self._lock = RLock()

    def _ensure_ready(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _read_records_unlocked(self) -> list[dict[str, Any]]:
        self._ensure_ready()
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("work_order_id"):
                records.append(record)
        return records

    def _write_records_unlocked(self, records: list[dict[str, Any]]) -> None:
        self._ensure_ready()
        temp_path = self.path.with_suffix(".jsonl.tmp")
        temp_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)

    def _next_work_order_id(self, records: list[dict[str, Any]], now: datetime) -> str:
        date_part = now.strftime("%Y%m%d")
        prefix = f"WO-{date_part}-"
        max_sequence = 0
        for record in records:
            record_id = str(record.get("work_order_id") or "")
            if not record_id.startswith(prefix):
                continue
            try:
                max_sequence = max(max_sequence, int(record_id.rsplit("-", 1)[-1]))
            except ValueError:
                continue
        return f"{prefix}{max_sequence + 1:04d}"

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now()
        with self._lock:
            records = self._read_records_unlocked()
            work_order_id = self._next_work_order_id(records, now)
            record = {
                "work_order_id": work_order_id,
                "created_at": now.isoformat(timespec="seconds"),
                "updated_at": now.isoformat(timespec="seconds"),
                "title": str(payload.get("title") or "").strip() or "维修工单",
                "equipment_object": str(payload.get("equipment_object") or "").strip() or "DCMA 系统",
                "fault_code": str(payload.get("fault_code") or "").strip() or None,
                "workorder_type": str(payload.get("workorder_type") or "").strip() or "运行异常排查",
                "priority": str(payload.get("priority") or "P2").strip() or "P2",
                "priority_label": str(payload.get("priority_label") or "").strip() or None,
                "risk_level": str(payload.get("risk_level") or "低").strip() or "低",
                "trigger_source": str(payload.get("trigger_source") or "故障诊断 Agent").strip(),
                "diagnosis_conclusion": str(payload.get("diagnosis_conclusion") or "").strip(),
                "key_evidence": self._text_list(payload.get("key_evidence")),
                "processing_steps": self._text_list(payload.get("processing_steps")),
                "acceptance_criteria": self._text_list(payload.get("acceptance_criteria")),
                "task_mappings": self._task_mappings(payload.get("task_mappings")),
                "assignee": str(payload.get("assignee") or "").strip() or None,
                "assignee_role": str(payload.get("assignee_role") or "").strip() or None,
                "suggested_completion_window": str(payload.get("suggested_completion_window") or "").strip() or None,
                "due_at": str(payload.get("due_at") or "").strip() or None,
                "status": str(payload.get("status") or "待派单").strip() or "待派单",
                "thread_id": str(payload.get("thread_id") or "").strip() or None,
                "trace_id": str(payload.get("trace_id") or "").strip() or None,
                "request_id": str(payload.get("request_id") or "").strip() or None,
                "source": payload.get("source") if isinstance(payload.get("source"), dict) else {},
                "created_by": str(payload.get("created_by") or "").strip() or None,
                "created_by_role": str(payload.get("created_by_role") or "").strip() or None,
                "authorized_asset_scope": self._text_list(payload.get("authorized_asset_scope")),
            }
            record["operation_logs"] = self._initial_operation_logs(record, now)
            records.append(record)
            self._write_records_unlocked(records)
            return deepcopy(record)

    def list(
        self,
        *,
        thread_id: str | None = None,
        trace_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        with self._lock:
            records = self._read_records_unlocked()
        thread_filter = str(thread_id or "").strip()
        trace_filter = str(trace_id or "").strip()
        status_filter = str(status or "").strip()
        items = []
        for record in records:
            if thread_filter and record.get("thread_id") != thread_filter:
                continue
            if trace_filter and record.get("trace_id") != trace_filter:
                continue
            if status_filter and record.get("status") != status_filter:
                continue
            items.append(record)
        items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        items = items[: max(1, min(limit, 100))]
        return {
            "items": deepcopy(items),
            "summary": self._summary(items),
            "filters": {
                "thread_id": thread_id,
                "trace_id": trace_id,
                "status": status,
            },
            "limit": max(1, min(limit, 100)),
        }

    def get(self, work_order_id: str) -> dict[str, Any] | None:
        normalized_id = sanitize_workorder_id(work_order_id)
        if not normalized_id:
            return None
        with self._lock:
            records = self._read_records_unlocked()
        for record in records:
            if record.get("work_order_id") == normalized_id:
                return deepcopy(record)
        return None

    def update(self, work_order_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        normalized_id = sanitize_workorder_id(work_order_id)
        if not normalized_id:
            return None
        allowed_fields = {"status", "assignee", "assignee_role", "due_at", "priority"}
        with self._lock:
            records = self._read_records_unlocked()
            updated: dict[str, Any] | None = None
            for record in records:
                if record.get("work_order_id") != normalized_id:
                    continue
                before_status = str(record.get("status") or "待派单")
                changed_fields: list[str] = []
                for key in allowed_fields:
                    if key in patch and patch[key] is not None:
                        value = str(patch[key]).strip() or record.get(key)
                        if record.get(key) != value:
                            record[key] = value
                            changed_fields.append(key)
                now = datetime.now()
                if changed_fields:
                    record.setdefault("operation_logs", [])
                    if not isinstance(record["operation_logs"], list):
                        record["operation_logs"] = []
                    record["operation_logs"].append(
                        self._update_operation_log(
                            record,
                            now,
                            before_status=before_status,
                            changed_fields=changed_fields,
                            operator=patch.get("operator"),
                            note=patch.get("note"),
                        )
                    )
                record["updated_at"] = now.isoformat(timespec="seconds")
                updated = deepcopy(record)
                break
            if updated is None:
                return None
            self._write_records_unlocked(records)
            return updated

    def health_check(self) -> dict[str, Any]:
        try:
            self._ensure_ready()
            probe = self.root_dir / ".workorder-healthcheck.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            records = self._read_records_unlocked()
            return {
                "status": "available",
                "backend": "file",
                "path": str(self.path),
                "record_count": len(records),
                "writable": True,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "backend": "file",
                "path": str(self.path),
                "writable": False,
                "detail": str(exc),
            }

    @staticmethod
    def _text_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return list(dict.fromkeys(cleaned))

    @classmethod
    def _task_mappings(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        mappings: list[dict[str, Any]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            evidence = str(item.get("evidence") or "").strip()
            tasks = cls._text_list(item.get("tasks"))
            if not evidence or not tasks:
                continue
            key = (evidence, tuple(tasks))
            if key in seen:
                continue
            seen.add(key)
            mappings.append({"evidence": evidence, "tasks": tasks})
        return mappings[:10]

    @staticmethod
    def _initial_operation_logs(record: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
        timestamp = now.isoformat(timespec="seconds")
        actor = record.get("trigger_source") or "故障诊断 Agent"
        logs = [
            {
                "time": timestamp,
                "actor": actor,
                "action": "创建工单",
                "detail": f"创建维修工单 {record.get('work_order_id')}",
                "status": record.get("status") or "待派单",
            }
        ]
        if record.get("trace_id"):
            logs.append(
                {
                    "time": timestamp,
                    "actor": actor,
                    "action": "绑定诊断链路",
                    "detail": f"绑定诊断链路 {record.get('trace_id')}",
                    "status": record.get("status") or "待派单",
                }
            )
        logs.append(
            {
                "time": timestamp,
                "actor": actor,
                "action": "状态初始化",
                "detail": f"当前状态：{record.get('status') or '待派单'}",
                "status": record.get("status") or "待派单",
            }
        )
        return logs

    @staticmethod
    def _update_operation_log(
        record: dict[str, Any],
        now: datetime,
        *,
        before_status: str,
        changed_fields: list[str],
        operator: Any,
        note: Any,
    ) -> dict[str, Any]:
        status = str(record.get("status") or "待派单")
        assignee = str(record.get("assignee") or record.get("assignee_role") or "电气维护人员").strip()
        status_details = {
            "已派单": f"工单派发给 {assignee}",
            "处理中": "维修人员开始处理工单",
            "待复核": "处理结果已提交，等待复核",
            "已关闭": "复核通过，工单关闭",
        }
        if "status" in changed_fields and before_status != status:
            action = "状态流转"
            detail = status_details.get(status, f"状态由 {before_status} 更新为 {status}")
        elif "assignee" in changed_fields or "assignee_role" in changed_fields:
            action = "更新负责人"
            detail = f"负责人更新为 {assignee}"
        else:
            action = "更新工单"
            detail = "更新工单字段：" + "、".join(changed_fields)
        note_text = str(note or "").strip()
        if note_text:
            detail = f"{detail}；{note_text}"
        return {
            "time": now.isoformat(timespec="seconds"),
            "actor": str(operator or "").strip() or "演示用户",
            "action": action,
            "detail": detail,
            "status": status,
        }

    @staticmethod
    def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        priority_counts: dict[str, int] = {}
        for record in records:
            status = str(record.get("status") or "待派单")
            priority = str(record.get("priority") or "P2")
            status_counts[status] = status_counts.get(status, 0) + 1
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
        return {
            "total": len(records),
            "status_counts": status_counts,
            "priority_counts": priority_counts,
        }


__all__ = ["FileWorkOrderRepository", "sanitize_workorder_id"]
