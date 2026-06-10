"""文件系统版 Workflow artifact store backend。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from threading import RLock

from ...paths import RUN_STATE_DIR
from ..contracts import WorkflowArtifactEnvelope
from .base import ArtifactStoreBackend


class FileArtifactStoreBackend(ArtifactStoreBackend):
    """按 thread_id 分片保存 Workflow artifact 的文件后端。"""

    def __init__(self, *, root_dir: str | os.PathLike[str] | None = None, max_thread_entries: int = 50):
        default_root = Path(RUN_STATE_DIR) / "workflow_artifacts"
        self.root_dir = Path(root_dir or os.getenv("WORKFLOW_ARTIFACT_DIR") or default_root)
        self.max_thread_entries = max(1, int(max_thread_entries))
        self._lock = RLock()

    def _ensure_ready(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _thread_file(self, thread_id: str) -> Path:
        digest = hashlib.sha256(str(thread_id).encode("utf-8")).hexdigest()[:32]
        return self.root_dir / f"{digest}.jsonl"

    def _read_thread_unlocked(self, thread_id: str) -> list[WorkflowArtifactEnvelope]:
        self._ensure_ready()
        target = self._thread_file(thread_id)
        if not target.exists():
            return []
        envelopes: list[WorkflowArtifactEnvelope] = []
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                envelope = WorkflowArtifactEnvelope.model_validate_json(line)
            except Exception:
                continue
            if envelope.thread_id == thread_id:
                envelopes.append(envelope)
        return envelopes

    def _write_thread_unlocked(self, thread_id: str, envelopes: list[WorkflowArtifactEnvelope]) -> None:
        self._ensure_ready()
        target = self._thread_file(thread_id)
        selected = envelopes[-self.max_thread_entries :]
        temp_path = target.with_suffix(".jsonl.tmp")
        temp_path.write_text(
            "".join(f"{envelope.model_dump_json()}\n" for envelope in selected),
            encoding="utf-8",
        )
        os.replace(temp_path, target)

    def save(self, envelope: WorkflowArtifactEnvelope) -> WorkflowArtifactEnvelope:
        with self._lock:
            envelopes = self._read_thread_unlocked(envelope.thread_id)
            envelopes.append(WorkflowArtifactEnvelope.model_validate_json(envelope.model_dump_json()))
            self._write_thread_unlocked(envelope.thread_id, envelopes)
            return WorkflowArtifactEnvelope.model_validate_json(envelope.model_dump_json())

    def get_latest(self, thread_id: str) -> WorkflowArtifactEnvelope | None:
        artifacts = self.list_thread_artifacts(thread_id, limit=1)
        return artifacts[0] if artifacts else None

    def list_thread_artifacts(self, thread_id: str, limit: int = 20) -> list[WorkflowArtifactEnvelope]:
        normalized_limit = max(1, limit)
        with self._lock:
            envelopes = self._read_thread_unlocked(thread_id)
        return list(reversed(envelopes))[:normalized_limit]

    def clear_thread(self, thread_id: str) -> None:
        with self._lock:
            target = self._thread_file(thread_id)
            try:
                target.unlink()
            except FileNotFoundError:
                return

    def clear_all(self) -> None:
        with self._lock:
            self._ensure_ready()
            for entry in self.root_dir.glob("*.jsonl"):
                try:
                    entry.unlink()
                except OSError:
                    continue

    def health_check(self) -> dict:
        try:
            self._ensure_ready()
            test_path = self.root_dir / ".artifact-store-healthcheck.tmp"
            test_path.write_text("ok", encoding="utf-8")
            test_path.unlink(missing_ok=True)
            return {
                "status": "available",
                "backend": "file",
                "path": str(self.root_dir),
                "exists": self.root_dir.exists(),
                "writable": True,
                "thread_file_count": len(list(self.root_dir.glob("*.jsonl"))),
            }
        except Exception as exc:
            return {
                "status": "failed",
                "backend": "file",
                "path": str(self.root_dir),
                "writable": False,
                "detail": str(exc),
            }
