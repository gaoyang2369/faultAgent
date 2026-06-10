"""治理快照与治理台账 repository。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..common.paths import REPORTS_DIR


def sanitize_governance_thread_hint(thread_id: str | None) -> str:
    raw = (thread_id or "session").strip()
    sanitized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    sanitized = sanitized.strip("-_")
    if not sanitized:
        return "session"
    return sanitized[:48]


class FileGovernanceRepository:
    """治理快照和治理台账的文件型 repository。"""

    def __init__(self, *, reports_dir: str = REPORTS_DIR) -> None:
        self.reports_dir = reports_dir

    def health_check(self) -> dict[str, Any]:
        governance_dir = self._governance_dir()
        reports_exists = os.path.exists(self.reports_dir)
        reports_is_dir = os.path.isdir(self.reports_dir)
        if not reports_exists:
            return {
                "status": "not_configured",
                "backend": "file",
                "path": governance_dir,
                "reports_dir": self.reports_dir,
                "writable": False,
                "detail": "报告目录不存在，治理快照会在报告目录恢复后写入",
            }
        if not reports_is_dir:
            return {
                "status": "failed",
                "backend": "file",
                "path": governance_dir,
                "reports_dir": self.reports_dir,
                "writable": False,
                "detail": "报告路径存在但不是目录",
            }

        try:
            probe_dir = governance_dir if os.path.isdir(governance_dir) else self.reports_dir
            test_path = os.path.join(probe_dir, ".governance-healthcheck.tmp")
            with open(test_path, "w", encoding="utf-8") as handle:
                handle.write("ok")
            os.remove(test_path)
            return {
                "status": "available",
                "backend": "file",
                "path": governance_dir,
                "reports_dir": self.reports_dir,
                "governance_dir_exists": os.path.isdir(governance_dir),
                "writable": True,
            }
        except OSError as exc:
            return {
                "status": "failed",
                "backend": "file",
                "path": governance_dir,
                "writable": False,
                "detail": str(exc),
            }

    def save_snapshot(self, payload: Any) -> dict[str, Any]:
        saved_paths = self._save_snapshot_files(payload)
        return {
            "ok": True,
            "thread_id": payload.thread_id,
            **saved_paths,
        }

    def list_snapshots(self, *, thread_id: str | None = None, limit: int = 10) -> dict[str, Any]:
        return {
            "items": self._list_snapshot_entries(thread_id=thread_id, limit=limit),
            "thread_id": thread_id,
            "limit": max(1, min(limit, 50)),
        }

    def create_ledger_record(self, payload: Any) -> dict[str, Any]:
        return {"ok": True, **self._create_ledger_record(payload)}

    def list_ledger_records(
        self,
        *,
        thread_id: str | None = None,
        limit: int = 10,
        status: str | None = None,
        priority: str | None = None,
        owner: str | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        items = self._list_ledger_records(
            thread_id=thread_id,
            limit=limit,
            status=status,
            priority=priority,
            owner=owner,
            tag=tag,
        )
        return {
            "items": items,
            "summary": self._summarize_ledger_records(items),
            "thread_id": thread_id,
            "limit": max(1, min(limit, 50)),
            "filters": {
                "status": status,
                "priority": priority,
                "owner": owner,
                "tag": tag,
            },
        }

    def update_ledger_record(self, payload: Any) -> dict[str, Any] | None:
        record = self._update_ledger_record(payload)
        if not record:
            return None
        return {"ok": True, **record}

    def _governance_dir(self) -> str:
        return os.path.join(self.reports_dir, "governance")

    def _save_snapshot_files(self, payload: Any) -> dict[str, str]:
        governance_dir = self._governance_dir()
        os.makedirs(governance_dir, exist_ok=True)

        thread_hint = sanitize_governance_thread_hint(payload.thread_id)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = uuid4().hex[:6]
        base_name = f"governance-{thread_hint}-{timestamp}-{suffix}"

        markdown_filename = f"{base_name}.md"
        json_filename = f"{base_name}.json"
        doc_template_filename = f"governance-doc-{thread_hint}-{timestamp}-{suffix}.md"
        report_filename = f"governance-report-{thread_hint}-{timestamp}-{suffix}.md"
        backlog_filename = f"governance-backlog-{thread_hint}-{timestamp}-{suffix}.md"

        markdown_path = os.path.join(governance_dir, markdown_filename)
        json_path = os.path.join(governance_dir, json_filename)
        doc_template_path = os.path.join(governance_dir, doc_template_filename)
        report_path = os.path.join(governance_dir, report_filename)
        backlog_path = os.path.join(governance_dir, backlog_filename)

        with open(markdown_path, "w", encoding="utf-8") as markdown_file:
            markdown_file.write(payload.markdown.rstrip() + "\n")

        with open(json_path, "w", encoding="utf-8") as json_file:
            json.dump(payload.json_content, json_file, ensure_ascii=False, indent=2)
            json_file.write("\n")

        with open(doc_template_path, "w", encoding="utf-8") as doc_template_file:
            doc_template_file.write(payload.doc_template.rstrip() + "\n")

        if payload.report_markdown and payload.report_markdown.strip():
            with open(report_path, "w", encoding="utf-8") as report_file:
                report_file.write(payload.report_markdown.rstrip() + "\n")

        if payload.backlog_markdown and payload.backlog_markdown.strip():
            with open(backlog_path, "w", encoding="utf-8") as backlog_file:
                backlog_file.write(payload.backlog_markdown.rstrip() + "\n")

        response = {
            "markdown_path": f"/reports/governance/{markdown_filename}",
            "json_path": f"/reports/governance/{json_filename}",
            "doc_template_path": f"/reports/governance/{doc_template_filename}",
        }
        if payload.report_markdown and payload.report_markdown.strip():
            response["report_path"] = f"/reports/governance/{report_filename}"
        if payload.backlog_markdown and payload.backlog_markdown.strip():
            response["backlog_path"] = f"/reports/governance/{backlog_filename}"
        return response

    def _list_snapshot_entries(self, thread_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        governance_dir = self._governance_dir()
        if not os.path.exists(governance_dir):
            return []

        thread_hint = sanitize_governance_thread_hint(thread_id) if thread_id else None
        grouped: dict[str, dict[str, Any]] = {}

        def _extract_thread_hint_from_snapshot_id(snapshot_id: str) -> str:
            prefix = re.sub(r"-\d{8}-\d{6}-[0-9a-fA-F]{6}$", "", snapshot_id)
            return prefix or snapshot_id

        for entry in os.scandir(governance_dir):
            if not entry.is_file():
                continue
            file_name = entry.name
            if not (file_name.endswith(".md") or file_name.endswith(".json")):
                continue
            if file_name.startswith("governance-doc-"):
                base_key = file_name[len("governance-doc-"):-3]
                bucket = grouped.setdefault(
                    base_key,
                    {
                        "snapshot_id": base_key,
                        "thread_hint": _extract_thread_hint_from_snapshot_id(base_key),
                        "created_at": None,
                        "updated_at": 0.0,
                        "markdown_path": None,
                        "json_path": None,
                        "doc_template_path": None,
                    },
                )
                bucket["doc_template_path"] = f"/reports/governance/{file_name}"
            elif file_name.startswith("governance-"):
                base_key = file_name[len("governance-"):]
                base_key = base_key[:-5] if file_name.endswith(".json") else base_key[:-3]
                bucket = grouped.setdefault(
                    base_key,
                    {
                        "snapshot_id": base_key,
                        "thread_hint": _extract_thread_hint_from_snapshot_id(base_key),
                        "created_at": None,
                        "updated_at": 0.0,
                        "markdown_path": None,
                        "json_path": None,
                        "doc_template_path": None,
                    },
                )
                path_key = "json_path" if file_name.endswith(".json") else "markdown_path"
                bucket[path_key] = f"/reports/governance/{file_name}"
            else:
                continue

            modified_at = entry.stat().st_mtime
            bucket["updated_at"] = max(bucket["updated_at"], modified_at)
            bucket["created_at"] = datetime.fromtimestamp(bucket["updated_at"]).isoformat(timespec="seconds")

        items = list(grouped.values())
        if thread_hint:
            items = [item for item in items if item.get("thread_hint") == thread_hint]

        items.sort(key=lambda item: item.get("updated_at", 0.0), reverse=True)
        for item in items:
            item.pop("updated_at", None)
        return items[: max(1, min(limit, 50))]

    def _ledger_dir(self) -> str:
        ledger_dir = os.path.join(self._governance_dir(), "ledger")
        os.makedirs(ledger_dir, exist_ok=True)
        return ledger_dir

    def _derive_next_action(self, payload: Any) -> str:
        if payload.next_action and payload.next_action.strip():
            return payload.next_action.strip()

        for item in payload.items:
            goal = str(item.get("goal") or "").strip()
            if goal:
                return goal

        if payload.risks:
            first_risk = payload.risks[0]
            risk_text = str(first_risk.get("text") or "").strip()
            if risk_text:
                return f"Investigate risk: {risk_text}"

        return "Review the current governance risks and define the first remediation step."

    def _create_ledger_record(self, payload: Any) -> dict[str, Any]:
        ledger_dir = self._ledger_dir()
        thread_hint = sanitize_governance_thread_hint(payload.thread_id)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = uuid4().hex[:6]
        record_id = f"ledger-{thread_hint}-{timestamp}-{suffix}"
        created_at = datetime.now().isoformat(timespec="seconds")

        record = {
            "record_id": record_id,
            "thread_id": payload.thread_id,
            "thread_hint": thread_hint,
            "created_at": created_at,
            "summary": payload.summary,
            "risks": payload.risks,
            "items": payload.items,
            "timeline": payload.timeline,
            "source_snapshot_paths": payload.source_snapshot_paths or {},
            "status": (payload.status or "open").strip() or "open",
            "owner": (payload.owner or "unassigned").strip() or "unassigned",
            "next_action": self._derive_next_action(payload),
            "verified_result": (payload.verified_result or "").strip(),
            "due_date": (payload.due_date or "").strip() or None,
            "priority": (payload.priority or "P2").strip() or "P2",
            "tags": [str(tag).strip() for tag in (payload.tags or []) if str(tag).strip()],
        }

        detail_filename = f"{record_id}.json"
        detail_path = os.path.join(ledger_dir, detail_filename)
        with open(detail_path, "w", encoding="utf-8") as detail_file:
            json.dump(record, detail_file, ensure_ascii=False, indent=2)
            detail_file.write("\n")

        index_entry = {
            "record_id": record_id,
            "thread_id": payload.thread_id,
            "thread_hint": thread_hint,
            "created_at": created_at,
            "risk_count": len(payload.risks),
            "item_count": len(payload.items),
            "priority_summary": payload.summary,
            "detail_path": f"/reports/governance/ledger/{detail_filename}",
            "status": record["status"],
            "owner": record["owner"],
            "next_action": record["next_action"],
            "verified_result": record["verified_result"],
            "due_date": record["due_date"],
            "priority": record["priority"],
            "tags": record["tags"],
        }
        index_path = os.path.join(ledger_dir, "index.jsonl")
        with open(index_path, "a", encoding="utf-8") as index_file:
            index_file.write(json.dumps(index_entry, ensure_ascii=False) + "\n")

        return index_entry

    def _update_ledger_record(self, payload: Any) -> dict[str, Any] | None:
        ledger_dir = self._ledger_dir()
        detail_path = os.path.join(ledger_dir, f"{payload.record_id}.json")
        if not os.path.exists(detail_path):
            return None

        with open(detail_path, "r", encoding="utf-8") as detail_file:
            record = json.load(detail_file)

        if payload.status is not None:
            record["status"] = payload.status.strip() or record.get("status") or "open"
        if payload.owner is not None:
            record["owner"] = payload.owner.strip() or "unassigned"
        if payload.next_action is not None:
            record["next_action"] = payload.next_action.strip()
        if payload.verified_result is not None:
            record["verified_result"] = payload.verified_result.strip()
        if payload.due_date is not None:
            record["due_date"] = payload.due_date.strip() or None
        if payload.priority is not None:
            record["priority"] = payload.priority.strip() or record.get("priority") or "P2"
        if payload.tags is not None:
            record["tags"] = [str(tag).strip() for tag in payload.tags if str(tag).strip()]

        with open(detail_path, "w", encoding="utf-8") as detail_file:
            json.dump(record, detail_file, ensure_ascii=False, indent=2)
            detail_file.write("\n")

        index_path = os.path.join(ledger_dir, "index.jsonl")
        index_entries: list[dict[str, Any]] = []
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as index_file:
                for line in index_file:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("record_id") == payload.record_id:
                        entry["status"] = record.get("status", "open")
                        entry["owner"] = record.get("owner", "unassigned")
                        entry["next_action"] = record.get("next_action", "")
                        entry["verified_result"] = record.get("verified_result", "")
                        entry["due_date"] = record.get("due_date")
                        entry["priority"] = record.get("priority", "P2")
                        entry["tags"] = record.get("tags", [])
                    index_entries.append(entry)

            with open(index_path, "w", encoding="utf-8") as index_file:
                for entry in index_entries:
                    index_file.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return {
            "record_id": record["record_id"],
            "thread_id": record.get("thread_id"),
            "thread_hint": record.get("thread_hint"),
            "created_at": record.get("created_at"),
            "risk_count": len(record.get("risks") or []),
            "item_count": len(record.get("items") or []),
            "priority_summary": record.get("summary") or [],
            "detail_path": f"/reports/governance/ledger/{payload.record_id}.json",
            "status": record.get("status", "open"),
            "owner": record.get("owner", "unassigned"),
            "next_action": record.get("next_action", ""),
            "verified_result": record.get("verified_result", ""),
            "due_date": record.get("due_date"),
            "priority": record.get("priority", "P2"),
            "tags": record.get("tags", []),
        }

    def _list_ledger_records(
        self,
        thread_id: str | None = None,
        limit: int = 10,
        status: str | None = None,
        priority: str | None = None,
        owner: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        ledger_dir = self._ledger_dir()
        index_path = os.path.join(ledger_dir, "index.jsonl")
        if not os.path.exists(index_path):
            return []

        thread_hint = sanitize_governance_thread_hint(thread_id) if thread_id else None
        status_filter = (status or "").strip()
        priority_filter = (priority or "").strip()
        owner_filter = (owner or "").strip().lower()
        tag_filter = (tag or "").strip().lower()
        records: list[dict[str, Any]] = []
        with open(index_path, "r", encoding="utf-8") as index_file:
            for line in index_file:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if thread_hint and record.get("thread_hint") != thread_hint:
                    continue
                if status_filter and str(record.get("status") or "open") != status_filter:
                    continue
                if priority_filter and str(record.get("priority") or "P2") != priority_filter:
                    continue
                if owner_filter and owner_filter not in str(record.get("owner") or "").lower():
                    continue
                record_tags = [str(item).strip().lower() for item in (record.get("tags") or []) if str(item).strip()]
                if tag_filter and tag_filter not in record_tags:
                    continue
                records.append(record)

        records.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return records[: max(1, min(limit, 50))]

    def _summarize_ledger_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        priority_counts: dict[str, int] = {}
        for record in records:
            status = str(record.get("status") or "open")
            priority = str(record.get("priority") or "P2")
            status_counts[status] = status_counts.get(status, 0) + 1
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
        return {
            "total": len(records),
            "status_counts": status_counts,
            "priority_counts": priority_counts,
        }


__all__ = [
    "FileGovernanceRepository",
    "sanitize_governance_thread_hint",
]
