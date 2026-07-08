"""管理员知识文件 应用服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fault_diagnosis.platform.persistence.repositories.admin_file_repository import FileAdminKnowledgeFileRepository, get_admin_file_repository
from .admin_knowledge_file_pipeline import (
    delete_admin_knowledge_file_record_with_artifacts,
    ingest_admin_knowledge_file_record,
    process_admin_knowledge_file_record,
    save_admin_knowledge_file_user_correction,
    schedule_ingest_admin_knowledge_file_record,
)


@dataclass(frozen=True)
class AdminKnowledgeFileUploadResult:
    payload: dict[str, Any]
    status_code: int
    process_record_id: str | None = None


@dataclass(frozen=True)
class AdminKnowledgeFileIngestResult:
    payload: dict[str, Any]
    status_code: int
    ingest_record_id: str | None = None


class AdminKnowledgeFileService:
    """封装管理员知识文件 上传、归档、校正和删除用例。"""

    def __init__(self, *, repository: FileAdminKnowledgeFileRepository | None = None) -> None:
        self.repository = repository or get_admin_file_repository()

    def list_records(self) -> dict[str, Any]:
        return {"records": self.repository.list_records()}

    def save_upload(
        self,
        *,
        filename: str | None,
        content_type: str | None,
        content: bytes,
    ) -> AdminKnowledgeFileUploadResult:
        record, duplicate = self.repository.save_record(filename, content_type, content)
        return AdminKnowledgeFileUploadResult(
            payload={
                "record": record,
                "duplicate": duplicate,
            },
            status_code=201 if not duplicate else 200,
            process_record_id=None if duplicate else record["id"],
        )

    def get_record_detail(self, record_id: str) -> dict[str, Any] | None:
        return self.repository.get_record_public(record_id)

    def get_file_state(self, record_id: str):
        return self.repository.get_file_path(record_id)

    def prepare_ingest(self, record_id: str) -> AdminKnowledgeFileIngestResult | None:
        record = self.repository.get_record_public(record_id)
        if not record:
            return None

        if record.get("kb_ingest_status") in {"succeeded", "indexed"}:
            return AdminKnowledgeFileIngestResult(
                payload={
                    "record": record,
                    "scheduled": False,
                    "already_ingested": True,
                    "message": "该文件已归档到知识库，Agent 可直接查询。",
                },
                status_code=200,
            )

        if record.get("ocr_status") in {"extracting", "extracting_text", "ocr_processing"}:
            return AdminKnowledgeFileIngestResult(
                payload={
                    "record": record,
                    "scheduled": False,
                    "already_ingested": False,
                    "message": "该文件仍在识别文本，请稍后再发起知识库归档。",
                },
                status_code=202,
            )

        staged_record = schedule_ingest_admin_knowledge_file_record(record_id) or record
        latest_record = self.repository.get_record_public(record_id) or staged_record
        return AdminKnowledgeFileIngestResult(
            payload={
                "record": latest_record,
                "scheduled": True,
                "already_ingested": False,
                "message": "已开始知识库归档任务，请等待状态刷新。",
            },
            status_code=202,
            ingest_record_id=record_id,
        )

    def save_correction(self, record_id: str, corrected_text: str) -> dict[str, Any] | None:
        record = self.repository.get_record_public(record_id)
        if not record:
            return None

        updated = save_admin_knowledge_file_user_correction(record_id, corrected_text)
        if not updated:
            return None
        latest_record = self.repository.get_record_public(record_id) or updated
        return {
            "record": latest_record,
            "message": "校对内容已保存。请重新执行知识库归档，Agent 才会使用最新内容。",
            "next_action": "请点击知识库归档，完成后再用此文件提问。",
        }

    def delete_record(self, record_id: str) -> bool:
        return delete_admin_knowledge_file_record_with_artifacts(record_id)

    def health_check(self) -> dict[str, Any]:
        return self.repository.health_check()


__all__ = [
    "AdminKnowledgeFileIngestResult",
    "AdminKnowledgeFileService",
    "AdminKnowledgeFileUploadResult",
    "ingest_admin_knowledge_file_record",
    "process_admin_knowledge_file_record",
]


