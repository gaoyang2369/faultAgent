"""管理员 PDF registry repository。"""

from __future__ import annotations

import os
from typing import Any

from . import admin_pdf_registry_storage as registry_storage


class FileAdminPdfRepository:
    """管理员 PDF registry 的文件型 repository。"""

    def list_records_raw(self) -> list[dict[str, Any]]:
        return registry_storage.list_pdf_records_raw()

    def list_records(self) -> list[dict[str, Any]]:
        return registry_storage.list_pdf_records()

    def save_record(
        self,
        file_name: str | None,
        content_type: str | None,
        content: bytes,
    ) -> tuple[dict[str, Any], bool]:
        return registry_storage.save_pdf_record(file_name or "upload.pdf", content_type, content)

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        return registry_storage.get_pdf_record(record_id)

    def get_record_public(self, record_id: str) -> dict[str, Any] | None:
        return registry_storage.get_pdf_record_public(record_id)

    def update_record_fields(self, record_id: str, **fields) -> dict[str, Any] | None:
        return registry_storage.update_pdf_record_fields(record_id, **fields)

    def save_processing_artifacts(
        self,
        record_id: str,
        *,
        raw_text: str,
        page_summaries: list[dict[str, Any]],
        structured_result: dict[str, Any],
        kb_markdown: str = "",
    ) -> dict[str, Any] | None:
        return registry_storage.save_pdf_processing_artifacts(
            record_id,
            raw_text=raw_text,
            page_summaries=page_summaries,
            structured_result=structured_result,
            kb_markdown=kb_markdown,
        )

    def save_user_correction(self, record_id: str, corrected_text: str) -> dict[str, Any] | None:
        return registry_storage.save_pdf_user_correction(record_id, corrected_text)

    def get_file_path(self, record_id: str) -> tuple[str, dict[str, Any]] | None:
        return registry_storage.get_pdf_file_path(record_id)

    def artifact_path(self, folder_name: str, file_name: str) -> str:
        return registry_storage._controlled_path(folder_name, file_name)

    def delete_record(self, record_id: str) -> bool:
        return registry_storage.delete_pdf_record(record_id)

    def health_check(self) -> dict[str, Any]:
        try:
            registry_storage._ensure_store_ready()
            test_path = registry_storage._controlled_path(".pdf-registry-healthcheck.tmp")
            with open(test_path, "w", encoding="utf-8") as handle:
                handle.write("ok")
            os.remove(test_path)
            return {
                "status": "available",
                "backend": "file",
                "path": registry_storage.ADMIN_UPLOAD_DIR,
                "records_file": registry_storage._RECORDS_FILE,
                "record_count": len(registry_storage.list_pdf_records_raw()),
                "writable": True,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "backend": "file",
                "path": registry_storage.ADMIN_UPLOAD_DIR,
                "records_file": registry_storage._RECORDS_FILE,
                "writable": False,
                "detail": str(exc),
            }


def get_admin_pdf_repository() -> FileAdminPdfRepository:
    """返回管理员 PDF registry repository。"""

    return FileAdminPdfRepository()


__all__ = [
    "FileAdminPdfRepository",
    "get_admin_pdf_repository",
]
