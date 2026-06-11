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
                "risk_level": str(payload.get("risk_level") or "低").strip() or "低",
                "trigger_source": str(payload.get("trigger_source") or "故障诊断 Agent").strip(),
                "diagnosis_conclusion": str(payload.get("diagnosis_conclusion") or "").strip(),
                "key_evidence": self._text_list(payload.get("key_evidence")),
                "processing_steps": self._text_list(payload.get("processing_steps")),
                "acceptance_criteria": self._text_list(payload.get("acceptance_criteria")),
                "assignee": str(payload.get("assignee") or "").strip() or None,
                "assignee_role": str(payload.get("assignee_role") or "").strip() or None,
                "suggested_completion_window": str(payload.get("suggested_completion_window") or "").strip() or None,
                "due_at": str(payload.get("due_at") or "").strip() or None,
                "status": str(payload.get("status") or "待派单").strip() or "待派单",
                "thread_id": str(payload.get("thread_id") or "").strip() or None,
                "trace_id": str(payload.get("trace_id") or "").strip() or None,
                "request_id": str(payload.get("request_id") or "").strip() or None,
                "source": payload.get("source") if isinstance(payload.get("source"), dict) else {},
            }
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
                for key in allowed_fields:
                    if key in patch and patch[key] is not None:
                        record[key] = str(patch[key]).strip() or record.get(key)
                record["updated_at"] = datetime.now().isoformat(timespec="seconds")
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
