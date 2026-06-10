"""High-risk work-order tools with safe-action guarding."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..quality.governance import build_governance_snapshot
from ..common.paths import REPORTS_DIR
from ..runtime import get_current_quality_summary
from ..quality.safe_actions import build_safe_action_guard, store_tool_artifact_metadata

_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)


def _strip_script_tags(value: str) -> str:
    return _SCRIPT_TAG_RE.sub("", value or "")


def _build_work_order_action_guard(work_order_id: str, quality_summary: dict) -> dict:
    return build_safe_action_guard(
        tool_name="create_work_order",
        target_name=(work_order_id or "work-order").strip() or "work-order",
        extension="json",
        gate=str(quality_summary.get("gate") or "pass"),
        risk_level=str(quality_summary.get("risk_level") or "high"),
        release_ready=bool(quality_summary.get("release_ready")),
        review_reasons=list(quality_summary.get("review_reasons") or []),
        allow_draft_on_fail=False,
    )


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
    """Create a locally-audited work-order payload, but block execution when evidence is insufficient."""
    try:
        title = _strip_script_tags(title).strip()
        severity = _strip_script_tags(severity).strip()
        summary = _strip_script_tags(summary).strip()
        assignee = _strip_script_tags(assignee).strip() or "maintenance-team"
        source_report = _strip_script_tags(source_report).strip()

        quality_summary = get_current_quality_summary()
        action_guard = _build_work_order_action_guard(work_order_id, quality_summary)
        governance_snapshot = build_governance_snapshot(
            evidence_quality=quality_summary,
            action_guard=action_guard,
        )

        work_orders_dir = os.path.join(REPORTS_DIR, "work_orders")
        os.makedirs(work_orders_dir, exist_ok=True)
        final_filename = action_guard["final_filename"]
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
            "action_guard": action_guard,
            "governance": governance_snapshot,
            "evidence_gate": {
                "gate": quality_summary.get("gate"),
                "risk_level": quality_summary.get("risk_level"),
                "release_ready": quality_summary.get("release_ready"),
                "review_reasons": list(quality_summary.get("review_reasons") or []),
            },
        }
        with open(file_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

        store_tool_artifact_metadata(
            "create_work_order",
            {
                "artifact_path": file_path,
                "web_path": web_path,
                "publication_status": action_guard.get("publication_status"),
                "action_guard": action_guard,
                "report_gate": quality_summary.get("gate"),
                "release_ready": quality_summary.get("release_ready"),
                "work_order_id": work_order_id,
                "governance": governance_snapshot,
            },
        )

        if action_guard.get("publication_status") == "published":
            return (
                f"工单已创建，可直接进入执行：{web_path}；"
                f"当前状态为 {governance_snapshot['work_order_gate']['status_label']}"
            )

        return (
            f"工单已生成审计记录，但当前不建议直接执行：{web_path}；"
            f"当前状态为 {governance_snapshot['work_order_gate']['status_label']}，"
            f"正式报告状态为 {governance_snapshot['report_gate']['formal_report_label']}"
        )
    except Exception as exc:
        return f"工单创建失败：{str(exc)}"
