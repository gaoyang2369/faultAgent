"""Postgres 版 Workflow artifact store backend。"""

from __future__ import annotations

import json
import os
import re
from threading import RLock

try:
    from psycopg import connect
except ModuleNotFoundError:  # pragma: no cover - 依赖缺失时走运行时保护
    connect = None

from ..contracts import WorkflowArtifactEnvelope
from .base import ArtifactStoreBackend


class PostgresArtifactStoreBackend(ArtifactStoreBackend):
    """基于 Postgres JSONB 的 artifact store backend。"""

    def __init__(self, *, dsn: str | None = None, table_name: str = "workflow_artifacts"):
        self.dsn = dsn or self._build_default_dsn()
        self.table_name = self._normalize_table_name(table_name)
        self._schema_ready = False
        self._schema_lock = RLock()

    def _build_default_dsn(self) -> str:
        return (
            f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
            f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )

    def _normalize_table_name(self, table_name: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_]", "_", (table_name or "").strip())
        normalized = normalized.strip("_") or "workflow_artifacts"
        return normalized

    def _connect(self):
        if connect is None:
            raise RuntimeError("未安装 psycopg，无法启用 postgres artifact backend")
        return connect(self.dsn, autocommit=True)

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {self.table_name} (
                            id BIGSERIAL PRIMARY KEY,
                            thread_id TEXT NOT NULL,
                            workflow_type TEXT NOT NULL,
                            envelope_created_at TEXT NOT NULL,
                            saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            envelope JSONB NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE INDEX IF NOT EXISTS {self.table_name}_thread_saved_idx
                        ON {self.table_name} (thread_id, saved_at DESC, id DESC)
                        """
                    )
            self._schema_ready = True

    def save(self, envelope: WorkflowArtifactEnvelope) -> WorkflowArtifactEnvelope:
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.table_name} (thread_id, workflow_type, envelope_created_at, envelope)
                    VALUES (%s, %s, %s, %s::jsonb)
                    """,
                    (
                        envelope.thread_id,
                        str(envelope.workflow_type),
                        envelope.created_at,
                        envelope.model_dump_json(),
                    ),
                )
        return WorkflowArtifactEnvelope.model_validate_json(envelope.model_dump_json())

    def get_latest(self, thread_id: str) -> WorkflowArtifactEnvelope | None:
        artifacts = self.list_thread_artifacts(thread_id, limit=1)
        return artifacts[0] if artifacts else None

    def list_thread_artifacts(self, thread_id: str, limit: int = 20) -> list[WorkflowArtifactEnvelope]:
        self._ensure_schema()
        normalized_limit = max(1, limit)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT envelope::text
                    FROM {self.table_name}
                    WHERE thread_id = %s
                    ORDER BY saved_at DESC, id DESC
                    LIMIT %s
                    """,
                    (thread_id, normalized_limit),
                )
                rows = cur.fetchall()
        return [WorkflowArtifactEnvelope.model_validate(json.loads(row[0])) for row in rows]

    def clear_thread(self, thread_id: str) -> None:
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self.table_name} WHERE thread_id = %s", (thread_id,))

    def clear_all(self) -> None:
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self.table_name}")

    def health_check(self) -> dict:
        return {
            "status": "available" if self._schema_ready else "degraded",
            "backend": "postgres",
            "table_name": self.table_name,
            "schema_ready": self._schema_ready,
            "detail": "schema 尚未初始化，将在首次读写时创建" if not self._schema_ready else "artifact 表已初始化",
        }
