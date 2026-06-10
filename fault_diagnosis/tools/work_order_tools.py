"""Work-order tools for the minimal agent runtime."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..common.paths import REPORTS_DIR

_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)


def _strip_script_tags(value: str) -> str:
    return _SCRIPT_TAG_RE.sub("", value or "")


class CreateWorkOrderSchema(BaseModel):
    work_order_id: str = Field(description="Work-order identifier or filename prefix.")
    title: str = Field(description="Short work-order title.")
    severity: str = Field(description="Severity such as P0/P1/P2 or high/medium/low.")
    summary: str = Field(description="Work-order problem summary.")
    assignee: str = Field(default="maintenance-team", description="Target assignee or group.")
    source_report: str = Field(default="", description="Optional report filename or source artifact path.")


@tool(args_schema=CreateWorkOrderSchema)
def create_work_order(
    work_order_id: str,
    title: str,
    severity: str,
    summary: str,
    assignee: str = "maintenance-team",
    source_report: str = "",
) -> str:
    """Create a local work-order payload."""
    try:
        title = _strip_script_tags(title).strip()
        severity = _strip_script_tags(severity).strip()
        summary = _strip_script_tags(summary).strip()
        assignee = _strip_script_tags(assignee).strip() or "maintenance-team"
        source_report = _strip_script_tags(source_report).strip()

        work_orders_dir = os.path.join(REPORTS_DIR, "work_orders")
        os.makedirs(work_orders_dir, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", (work_order_id or "work-order").strip()).strip("-._")
        final_filename = f"{safe_id or 'work-order'}.json"
        file_path = os.path.join(work_orders_dir, final_filename)
        web_path = f"work_orders/{final_filename}"

        payload = {
            "work_order_id": work_order_id,
            "title": title,
            "severity": severity,
            "summary": summary,
            "assignee": assignee,
            "source_report": source_report,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(file_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

        return f"工单已创建：{web_path}"
    except Exception as exc:
        return f"工单创建失败：{str(exc)}"
