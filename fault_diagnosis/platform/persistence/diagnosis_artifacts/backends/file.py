"""文件系统版诊断产物 store backend。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from threading import RLock

from fault_diagnosis.platform.paths import RUN_STATE_DIR
from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope
from .base import ArtifactStoreBackend


class FileArtifactStoreBackend(ArtifactStoreBackend):
    """按 thread_id 分片保存诊断产物的文件后端。"""

    def __init__(self, *, root_dir: str | os.PathLike[str] | None = None, max_thread_entries: int = 50):
        default_root = Path(RUN_STATE_DIR) / "diagnosis_artifacts"
        self.root_dir = Path(
            root_dir
            or os.getenv("DIAGNOSIS_ARTIFACT_DIR")
            or os.getenv("WORKFLOW_ARTIFACT_DIR")
            or default_root
        )
        self.max_thread_entries = max(1, int(max_thread_entries))
        self._lock = RLock()

    def _ensure_ready(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _thread_file(self, thread_id: str) -> Path:
        digest = hashlib.sha256(str(thread_id).encode("utf-8")).hexdigest()[:32]
        return self.root_dir / f"{digest}.jsonl"

    def _artifact_file(self, thread_id: str) -> Path:
        return self._thread_file(thread_id).with_suffix(".artifacts.jsonl")

    def _read_thread_unlocked(self, thread_id: str) -> list[DiagnosisArtifactEnvelope]:
        self._ensure_ready()
        target = self._thread_file(thread_id)
        if not target.exists():
            return []
        envelopes: list[DiagnosisArtifactEnvelope] = []
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                envelope = DiagnosisArtifactEnvelope.model_validate_json(line)
            except Exception:
                continue
            if envelope.thread_id == thread_id:
                envelopes.append(envelope)
        return envelopes

    def _write_thread_unlocked(self, thread_id: str, envelopes: list[DiagnosisArtifactEnvelope]) -> None:
        self._ensure_ready()
        target = self._thread_file(thread_id)
        selected = envelopes[-self.max_thread_entries :]
        temp_path = target.with_suffix(".jsonl.tmp")
        temp_path.write_text(
            "".join(f"{envelope.model_dump_json()}\n" for envelope in selected),
            encoding="utf-8",
        )
        os.replace(temp_path, target)

    def save(self, envelope: DiagnosisArtifactEnvelope) -> DiagnosisArtifactEnvelope:
        with self._lock:
            envelopes = self._read_thread_unlocked(envelope.thread_id)
            envelopes.append(DiagnosisArtifactEnvelope.model_validate_json(envelope.model_dump_json()))
            self._write_thread_unlocked(envelope.thread_id, envelopes)
            return DiagnosisArtifactEnvelope.model_validate_json(envelope.model_dump_json())

    def get_latest(self, thread_id: str) -> DiagnosisArtifactEnvelope | None:
        artifacts = self.list_thread_artifacts(thread_id, limit=1)
        return artifacts[0] if artifacts else None

    def list_thread_artifacts(self, thread_id: str, limit: int = 20) -> list[DiagnosisArtifactEnvelope]:
        normalized_limit = max(1, limit)
        with self._lock:
            envelopes = self._read_thread_unlocked(thread_id)
        return list(reversed(envelopes))[:normalized_limit]

    def save_artifact(self, envelope):  # noqa: ANN001, ANN201
        from fault_diagnosis.domain.artifacts import ArtifactEnvelope
        with self._lock:
            self._ensure_ready()
            target = self._artifact_file(envelope.thread_id)
            records: dict[str, ArtifactEnvelope] = {}
            if target.exists():
                for line in target.read_text(encoding="utf-8").splitlines():
                    try:
                        item = ArtifactEnvelope.model_validate_json(line)
                    except Exception:
                        continue
                    records[item.artifact_id] = item
            saved = ArtifactEnvelope.model_validate_json(envelope.model_dump_json())
            records[saved.artifact_id] = saved
            temp_path = target.with_suffix(".jsonl.tmp")
            temp_path.write_text(
                "".join(f"{item.model_dump_json()}\n" for item in records.values()),
                encoding="utf-8",
            )
            os.replace(temp_path, target)
            return ArtifactEnvelope.model_validate_json(saved.model_dump_json())

    def get_artifact(self, thread_id: str, artifact_id: str):  # noqa: ANN201
        from fault_diagnosis.domain.artifacts import ArtifactEnvelope
        with self._lock:
            target = self._artifact_file(thread_id)
            if not target.exists():
                return None
            for line in target.read_text(encoding="utf-8").splitlines():
                try:
                    item = ArtifactEnvelope.model_validate_json(line)
                except Exception:
                    continue
                if item.thread_id == thread_id and item.artifact_id == artifact_id:
                    return item
        return None

    def clear_thread(self, thread_id: str) -> None:
        with self._lock:
            target = self._thread_file(thread_id)
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            try:
                self._artifact_file(thread_id).unlink()
            except FileNotFoundError:
                pass

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
