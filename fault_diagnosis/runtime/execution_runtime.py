"""Execution-state container for harness-style runtime orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .workflow_runtime import activate_stage, complete_stage, resolve_tool_stage


@dataclass
class ExecutionRuntimeContext:
    """Mutable execution state shared across a streaming or batch run."""

    stream_started_at: float
    tool_started_at: dict[str, float] = field(default_factory=dict)
    tool_inputs_by_run_id: dict[str, Any] = field(default_factory=dict)
    tool_lifecycle_ledger: list[dict[str, Any]] = field(default_factory=list)
    workflow_stages_seen: list[str] = field(default_factory=list)
    workflow_stage_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_workflow_stage: str | None = None

    def elapsed_ms(self, now: float) -> float:
        return round((now - self.stream_started_at) * 1000, 1)

    def handle_tool_start(
        self,
        *,
        tool_name: str,
        tool_run_id: str,
        tool_input: Any,
        now: float,
        tool_input_preview: Any | None = None,
    ) -> str:
        """Track tool start and activate the corresponding workflow stage."""
        tool_stage = resolve_tool_stage(tool_name)
        now_ms = self.elapsed_ms(now)
        if self.current_workflow_stage and self.current_workflow_stage != tool_stage:
            complete_stage(self.workflow_stage_details, self.current_workflow_stage, now_ms)
        activate_stage(self.workflow_stages_seen, self.workflow_stage_details, tool_stage, now_ms)
        self.current_workflow_stage = tool_stage
        self.tool_started_at[tool_run_id] = now
        self.tool_inputs_by_run_id[tool_run_id] = tool_input
        self.tool_lifecycle_ledger.append(
            {
                "run_id": tool_run_id,
                "tool": tool_name,
                "stage": tool_stage,
                "event": "start",
                "started_at_ms": now_ms,
                "current_stage": self.current_workflow_stage,
                "input_preview": tool_input_preview,
                "evidence_ids": [],
                "finding_ids": [],
            }
        )
        return tool_stage

    def pop_tool_run(self, tool_run_id: str) -> tuple[Any, float | None]:
        """Return cached tool input and elapsed runtime for a finished tool call."""
        tool_input = self.tool_inputs_by_run_id.pop(tool_run_id, None)
        started_at = self.tool_started_at.pop(tool_run_id, None)
        return tool_input, started_at

    def find_pending_tool_run_id(self, tool_name: str) -> str | None:
        """Return the most recent pending run id for a tool when end events omit run_id."""
        prefix = f"{tool_name}-"
        for run_id in reversed(list(self.tool_inputs_by_run_id.keys())):
            if run_id.startswith(prefix):
                return run_id
        return None

    def record_tool_end(
        self,
        *,
        tool_name: str,
        tool_run_id: str,
        tool_stage: str,
        now: float,
        duration_ms: float | None,
        result_preview: Any | None = None,
        evidence_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Append a standardized tool-end ledger entry and return it."""
        normalized_evidence_ids = [item for item in (evidence_ids or []) if item]
        entry = {
            "run_id": tool_run_id,
            "tool": tool_name,
            "stage": tool_stage,
            "event": "end",
            "ended_at_ms": self.elapsed_ms(now),
            "duration_ms": duration_ms,
            "current_stage": self.current_workflow_stage,
            "result_preview": result_preview,
            "evidence_ids": normalized_evidence_ids,
            "finding_ids": [],
        }
        self.tool_lifecycle_ledger.append(entry)
        if normalized_evidence_ids:
            for item in self.tool_lifecycle_ledger:
                if item.get("run_id") == tool_run_id:
                    item["evidence_ids"] = normalized_evidence_ids
            matched_entries = [item for item in self.tool_lifecycle_ledger if item.get("run_id") == tool_run_id]
            other_entries = [item for item in self.tool_lifecycle_ledger if item.get("run_id") != tool_run_id]
            self.tool_lifecycle_ledger = matched_entries + other_entries
        return entry

    def enrich_lifecycle_with_findings(self, finding_links: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Attach explicit finding ids onto lifecycle entries based on evidence linkage."""
        finding_links = finding_links or []
        for entry in self.tool_lifecycle_ledger:
            evidence_ids = [item for item in (entry.get("evidence_ids") or []) if item]
            if not evidence_ids:
                entry["finding_ids"] = []
                continue
            linked_findings: list[str] = []
            for link in finding_links:
                if not isinstance(link, dict):
                    continue
                candidate_ids = []
                if isinstance(link.get("evidence_ids"), list):
                    candidate_ids.extend(link["evidence_ids"])
                if isinstance(link.get("chart_evidence_ids"), list):
                    candidate_ids.extend(link["chart_evidence_ids"])
                if any(evidence_id in candidate_ids for evidence_id in evidence_ids):
                    finding_id = link.get("finding_id")
                    if finding_id and finding_id not in linked_findings:
                        linked_findings.append(finding_id)
            if not linked_findings and evidence_ids:
                linked_findings = [f"fd_auto_{evidence_ids[0]}"]
            entry["finding_ids"] = linked_findings
        return [dict(item) for item in self.tool_lifecycle_ledger]

    def finalize_workflow(self, now: float) -> float:
        """Mark the current stage completed before emitting the final response."""
        now_ms = self.elapsed_ms(now)
        if self.current_workflow_stage:
            complete_stage(self.workflow_stage_details, self.current_workflow_stage, now_ms)
        return now_ms
