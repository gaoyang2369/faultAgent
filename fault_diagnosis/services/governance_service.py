"""治理快照与治理台账应用服务。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..common.paths import REPORTS_DIR
from ..repositories.governance_repository import FileGovernanceRepository, sanitize_governance_thread_hint


class GovernanceSnapshotPayload(BaseModel):
    markdown: str
    json_content: dict[str, Any]
    doc_template: str
    report_markdown: str | None = None
    backlog_markdown: str | None = None
    thread_id: str | None = None


class GovernanceLedgerPayload(BaseModel):
    thread_id: str | None = None
    summary: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    source_snapshot_paths: dict[str, str] | None = None
    status: str = "open"
    owner: str = "unassigned"
    next_action: str = ""
    verified_result: str = ""
    due_date: str | None = None
    priority: str = "P2"
    tags: list[str] = Field(default_factory=list)


class GovernanceLedgerUpdatePayload(BaseModel):
    record_id: str
    status: str | None = None
    owner: str | None = None
    next_action: str | None = None
    verified_result: str | None = None
    due_date: str | None = None
    priority: str | None = None
    tags: list[str] | None = None


class GovernanceService:
    """治理快照和治理台账应用服务。"""

    def __init__(
        self,
        *,
        reports_dir: str = REPORTS_DIR,
        repository: FileGovernanceRepository | None = None,
    ) -> None:
        self.repository = repository or FileGovernanceRepository(reports_dir=reports_dir)

    def save_snapshot(self, payload: GovernanceSnapshotPayload) -> dict[str, Any]:
        return self.repository.save_snapshot(payload)

    def list_snapshots(self, *, thread_id: str | None = None, limit: int = 10) -> dict[str, Any]:
        return self.repository.list_snapshots(thread_id=thread_id, limit=limit)

    def create_ledger_record(self, payload: GovernanceLedgerPayload) -> dict[str, Any]:
        return self.repository.create_ledger_record(payload)

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
        return self.repository.list_ledger_records(
            thread_id=thread_id,
            limit=limit,
            status=status,
            priority=priority,
            owner=owner,
            tag=tag,
        )

    def update_ledger_record(self, payload: GovernanceLedgerUpdatePayload) -> dict[str, Any] | None:
        return self.repository.update_ledger_record(payload)

    def health_check(self) -> dict[str, Any]:
        return self.repository.health_check()
