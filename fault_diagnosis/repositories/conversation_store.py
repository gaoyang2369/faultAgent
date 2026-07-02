"""Persistent conversation store for user-visible chat history."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from ..common.paths import RUN_STATE_DIR


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _json_dumps(value: Any) -> str:
    if value is None:
        return "{}"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _normalize_status(status: str | None, *, default: str = "completed") -> str:
    normalized = str(status or default).strip().lower()
    return normalized or default


def _message_status_text(status: str) -> str:
    return {
        "accepted": "已发送",
        "streaming": "正在回复",
        "completed": "回复完成",
        "cancelled": "已停止",
        "failed": "回复失败",
        "superseded": "已被编辑替换",
    }.get(status, status)


class ConversationRepository(Protocol):
    """Repository protocol for durable conversation records."""

    def ensure_thread(
        self,
        *,
        thread_id: str,
        session_id: str,
        owner_user_id: str,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or refresh a conversation thread."""

    def append_message(
        self,
        *,
        thread_id: str,
        role: str,
        content_text: str,
        status: str,
        request_id: str | None = None,
        stream_id: str | None = None,
        branch_id: str = "main",
        parent_message_id: str | None = None,
        turn_index: int | None = None,
        model: str | None = None,
        auth_scope: dict[str, Any] | None = None,
        content_json: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one message and return its stored record."""

    def update_message_status(
        self,
        *,
        message_id: str,
        status: str,
        content_text: str | None = None,
        content_json: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update a message status/content after a stream finishes."""

    def link_artifacts(
        self,
        *,
        thread_id: str,
        message_id: str,
        artifact_refs: list[dict[str, Any]],
    ) -> None:
        """Link a message to diagnosis/report/workorder artifacts."""

    def list_thread_ids(self, *, session_id: str) -> list[str]:
        """Return active thread ids for a session by recent activity."""

    def list_messages(
        self,
        *,
        thread_id: str,
        include_superseded: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return messages for one thread in chronological order."""

    def supersede_from_user_turn(self, *, thread_id: str, user_turn_index: int) -> list[dict[str, Any]]:
        """Mark the target user turn and following messages as superseded."""

    def soft_delete_thread(self, *, thread_id: str) -> None:
        """Soft-delete a thread and its active messages."""

    def health_check(self) -> dict[str, Any]:
        """Return lightweight repository health information."""


class SQLiteConversationRepository:
    """SQLite-backed implementation used as the local durable backend."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        default_path = Path(RUN_STATE_DIR) / "conversations.sqlite3"
        self.path = Path(path or os.getenv("CONVERSATION_DB_PATH") or default_path)
        self._lock = RLock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_threads (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT,
                    deleted_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_agent_threads_session_updated
                    ON agent_threads(session_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS agent_messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL DEFAULT 'main',
                    parent_message_id TEXT,
                    turn_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content_text TEXT NOT NULL DEFAULT '',
                    content_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    request_id TEXT,
                    stream_id TEXT,
                    model TEXT,
                    token_count INTEGER,
                    auth_scope_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    superseded_at TEXT,
                    deleted_at TEXT,
                    FOREIGN KEY(thread_id) REFERENCES agent_threads(id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_messages_thread_turn
                    ON agent_messages(thread_id, turn_index, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_agent_messages_request
                    ON agent_messages(request_id, stream_id);

                CREATE TABLE IF NOT EXISTS agent_message_artifact_refs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    artifact_backend TEXT NOT NULL DEFAULT 'diagnosis_artifact_store',
                    ref_role TEXT NOT NULL DEFAULT 'produced_by',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES agent_threads(id),
                    FOREIGN KEY(message_id) REFERENCES agent_messages(id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_artifact_refs_message
                    ON agent_message_artifact_refs(message_id);

                CREATE TABLE IF NOT EXISTS agent_thread_summaries (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    summary_type TEXT NOT NULL,
                    covered_from_message_id TEXT,
                    covered_to_message_id TEXT,
                    summary_text TEXT NOT NULL DEFAULT '',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    source_hash TEXT NOT NULL DEFAULT '',
                    model TEXT,
                    created_at TEXT NOT NULL,
                    invalidated_at TEXT,
                    FOREIGN KEY(thread_id) REFERENCES agent_threads(id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_summaries_thread_type
                    ON agent_thread_summaries(thread_id, summary_type, created_at DESC);
                """
            )
            connection.commit()
            self._initialized = True

    def _execute(self, callback):
        with self._lock:
            with self._connect() as connection:
                self._ensure_schema(connection)
                result = callback(connection)
                connection.commit()
                return result

    def ensure_thread(
        self,
        *,
        thread_id: str,
        session_id: str,
        owner_user_id: str,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        thread_id = str(thread_id or "").strip()
        session_id = str(session_id or "").strip()
        owner_user_id = str(owner_user_id or "").strip() or "anonymous"
        if not thread_id or not session_id:
            raise ValueError("thread_id and session_id are required")

        def op(connection: sqlite3.Connection) -> dict[str, Any]:
            now = _now_iso()
            row = connection.execute("SELECT * FROM agent_threads WHERE id = ?", (thread_id,)).fetchone()
            if row:
                existing_metadata = _json_loads(row["metadata_json"])
                if isinstance(existing_metadata, dict) and metadata:
                    existing_metadata.update(metadata)
                connection.execute(
                    """
                    UPDATE agent_threads
                    SET session_id = ?, owner_user_id = ?, title = COALESCE(NULLIF(?, ''), title),
                        status = 'active', updated_at = ?, deleted_at = NULL, metadata_json = ?
                    WHERE id = ?
                    """,
                    (
                        session_id,
                        owner_user_id,
                        str(title or "").strip(),
                        now,
                        _json_dumps(existing_metadata),
                        thread_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO agent_threads (
                        id, owner_user_id, session_id, title, status, created_at, updated_at, metadata_json
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        thread_id,
                        owner_user_id,
                        session_id,
                        str(title or "").strip(),
                        now,
                        now,
                        _json_dumps(metadata or {}),
                    ),
                )
            return self._row_to_thread(connection.execute("SELECT * FROM agent_threads WHERE id = ?", (thread_id,)).fetchone())

        return self._execute(op)

    def append_message(
        self,
        *,
        thread_id: str,
        role: str,
        content_text: str,
        status: str,
        request_id: str | None = None,
        stream_id: str | None = None,
        branch_id: str = "main",
        parent_message_id: str | None = None,
        turn_index: int | None = None,
        model: str | None = None,
        auth_scope: dict[str, Any] | None = None,
        content_json: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        thread_id = str(thread_id or "").strip()
        role = str(role or "").strip().lower()
        if not thread_id or role not in {"user", "assistant", "tool_summary", "system_note"}:
            raise ValueError("valid thread_id and role are required")

        def op(connection: sqlite3.Connection) -> dict[str, Any]:
            now = _now_iso()
            resolved_turn_index = self._next_turn_index(connection, thread_id, role, turn_index)
            message_id = f"msg_{_now_ms()}_{os.urandom(6).hex()}"
            connection.execute(
                """
                INSERT INTO agent_messages (
                    id, thread_id, branch_id, parent_message_id, turn_index, role,
                    content_text, content_json, status, request_id, stream_id, model,
                    auth_scope_json, metadata_json, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    branch_id or "main",
                    parent_message_id,
                    resolved_turn_index,
                    role,
                    content_text or "",
                    _json_dumps(content_json or {}),
                    _normalize_status(status, default="accepted"),
                    request_id,
                    stream_id,
                    model,
                    _json_dumps(auth_scope or {}),
                    _json_dumps(metadata or {}),
                    now,
                    now if _normalize_status(status, default="accepted") in {"completed", "failed", "cancelled"} else None,
                ),
            )
            connection.execute(
                "UPDATE agent_threads SET updated_at = ?, last_message_at = ?, deleted_at = NULL WHERE id = ?",
                (now, now, thread_id),
            )
            return self._get_message(connection, message_id)

        return self._execute(op)

    def update_message_status(
        self,
        *,
        message_id: str,
        status: str,
        content_text: str | None = None,
        content_json: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        message_id = str(message_id or "").strip()
        if not message_id:
            return None

        def op(connection: sqlite3.Connection) -> dict[str, Any] | None:
            row = connection.execute("SELECT * FROM agent_messages WHERE id = ?", (message_id,)).fetchone()
            if not row:
                return None
            merged_json = _json_loads(row["content_json"])
            if isinstance(merged_json, dict) and content_json:
                merged_json.update(content_json)
            merged_metadata = _json_loads(row["metadata_json"])
            if isinstance(merged_metadata, dict) and metadata:
                merged_metadata.update(metadata)
            now = _now_iso()
            normalized_status = _normalize_status(status)
            connection.execute(
                """
                UPDATE agent_messages
                SET status = ?, content_text = COALESCE(?, content_text), content_json = ?,
                    metadata_json = ?, completed_at = CASE
                        WHEN ? IN ('completed', 'failed', 'cancelled') THEN ?
                        ELSE completed_at
                    END,
                    superseded_at = CASE WHEN ? = 'superseded' THEN ? ELSE superseded_at END
                WHERE id = ?
                """,
                (
                    normalized_status,
                    content_text,
                    _json_dumps(merged_json),
                    _json_dumps(merged_metadata),
                    normalized_status,
                    now,
                    normalized_status,
                    now,
                    message_id,
                ),
            )
            connection.execute(
                "UPDATE agent_threads SET updated_at = ?, last_message_at = ? WHERE id = ?",
                (now, now, row["thread_id"]),
            )
            return self._get_message(connection, message_id)

        return self._execute(op)

    def link_artifacts(
        self,
        *,
        thread_id: str,
        message_id: str,
        artifact_refs: list[dict[str, Any]],
    ) -> None:
        if not artifact_refs:
            return

        def op(connection: sqlite3.Connection) -> None:
            now = _now_iso()
            for ref in artifact_refs:
                artifact_id = str(ref.get("artifact_id") or "").strip()
                artifact_type = str(ref.get("artifact_type") or "diagnosis").strip() or "diagnosis"
                if not artifact_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO agent_message_artifact_refs (
                        id, thread_id, message_id, artifact_id, artifact_type, artifact_backend, ref_role, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"ref_{_now_ms()}_{os.urandom(6).hex()}",
                        thread_id,
                        message_id,
                        artifact_id,
                        artifact_type,
                        str(ref.get("artifact_backend") or "diagnosis_artifact_store"),
                        str(ref.get("ref_role") or "produced_by"),
                        now,
                    ),
                )

        self._execute(op)

    def list_thread_ids(self, *, session_id: str) -> list[str]:
        session_id = str(session_id or "").strip()
        if not session_id:
            return []

        def op(connection: sqlite3.Connection) -> list[str]:
            rows = connection.execute(
                """
                SELECT id FROM agent_threads
                WHERE session_id = ? AND deleted_at IS NULL
                ORDER BY COALESCE(last_message_at, updated_at) DESC, updated_at DESC
                """,
                (session_id,),
            ).fetchall()
            return [str(row["id"]) for row in rows]

        return self._execute(op)

    def list_messages(
        self,
        *,
        thread_id: str,
        include_superseded: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return []

        def op(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            where = "thread_id = ? AND deleted_at IS NULL"
            params: list[Any] = [thread_id]
            if not include_superseded:
                where += " AND status != 'superseded'"
            sql = f"""
                SELECT * FROM agent_messages
                WHERE {where}
                ORDER BY turn_index ASC, created_at ASC, id ASC
            """
            rows = connection.execute(sql, params).fetchall()
            messages = [self._row_to_message(row) for row in rows]
            if limit is not None:
                messages = messages[-max(int(limit), 0):]
            return messages

        return self._execute(op)

    def supersede_from_user_turn(self, *, thread_id: str, user_turn_index: int) -> list[dict[str, Any]]:
        if user_turn_index < 0:
            raise ValueError("user_turn_index must be greater than or equal to 0")

        def op(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            user_rows = connection.execute(
                """
                SELECT * FROM agent_messages
                WHERE thread_id = ? AND role = 'user' AND deleted_at IS NULL AND status != 'superseded'
                ORDER BY turn_index ASC, created_at ASC, id ASC
                """,
                (thread_id,),
            ).fetchall()
            if user_turn_index >= len(user_rows):
                raise ValueError("user_turn_index is outside current history")
            target_turn = int(user_rows[user_turn_index]["turn_index"])
            now = _now_iso()
            connection.execute(
                """
                UPDATE agent_messages
                SET status = 'superseded', superseded_at = COALESCE(superseded_at, ?)
                WHERE thread_id = ? AND deleted_at IS NULL AND status != 'superseded' AND turn_index >= ?
                """,
                (now, thread_id, target_turn),
            )
            kept_rows = connection.execute(
                """
                SELECT * FROM agent_messages
                WHERE thread_id = ? AND deleted_at IS NULL AND status != 'superseded'
                ORDER BY turn_index ASC, created_at ASC, id ASC
                """,
                (thread_id,),
            ).fetchall()
            return [self._row_to_message(row) for row in kept_rows]

        return self._execute(op)

    def soft_delete_thread(self, *, thread_id: str) -> None:
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return

        def op(connection: sqlite3.Connection) -> None:
            now = _now_iso()
            connection.execute(
                "UPDATE agent_threads SET status = 'deleted', deleted_at = ?, updated_at = ? WHERE id = ?",
                (now, now, thread_id),
            )
            connection.execute(
                "UPDATE agent_messages SET deleted_at = COALESCE(deleted_at, ?) WHERE thread_id = ?",
                (now, thread_id),
            )

        self._execute(op)

    def health_check(self) -> dict[str, Any]:
        try:
            def op(connection: sqlite3.Connection) -> dict[str, Any]:
                thread_count = connection.execute("SELECT COUNT(*) AS count FROM agent_threads").fetchone()["count"]
                message_count = connection.execute("SELECT COUNT(*) AS count FROM agent_messages").fetchone()["count"]
                return {
                    "status": "available",
                    "backend": "sqlite",
                    "path": str(self.path),
                    "thread_count": int(thread_count),
                    "message_count": int(message_count),
                }

            return self._execute(op)
        except Exception as exc:
            return {"status": "failed", "backend": "sqlite", "path": str(self.path), "detail": str(exc)}

    def _next_turn_index(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
        role: str,
        explicit_turn_index: int | None,
    ) -> int:
        if explicit_turn_index is not None:
            return int(explicit_turn_index)
        max_turn = connection.execute(
            """
            SELECT MAX(turn_index) AS max_turn FROM agent_messages
            WHERE thread_id = ? AND deleted_at IS NULL AND status != 'superseded'
            """,
            (thread_id,),
        ).fetchone()["max_turn"]
        if role == "user":
            return int(max_turn) + 1 if max_turn is not None else 0
        return int(max_turn) if max_turn is not None else 0

    def _get_message(self, connection: sqlite3.Connection, message_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM agent_messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"message not found after write: {message_id}")
        return self._row_to_message(row)

    def _row_to_thread(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        data = dict(row)
        data["metadata"] = _json_loads(data.pop("metadata_json", None))
        return data

    def _row_to_message(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["content_json"] = _json_loads(data.get("content_json"))
        data["auth_scope"] = _json_loads(data.pop("auth_scope_json", None))
        data["metadata"] = _json_loads(data.pop("metadata_json", None))
        return data


class MemoryConversationRepository:
    """Small in-memory implementation for focused tests."""

    def __init__(self) -> None:
        self._threads: dict[str, dict[str, Any]] = {}
        self._messages: list[dict[str, Any]] = []
        self._refs: list[dict[str, Any]] = []
        self._lock = RLock()

    def ensure_thread(self, **kwargs) -> dict[str, Any]:
        thread_id = kwargs["thread_id"]
        now = _now_iso()
        with self._lock:
            thread = self._threads.get(thread_id, {})
            thread.update(
                {
                    "id": thread_id,
                    "session_id": kwargs["session_id"],
                    "owner_user_id": kwargs.get("owner_user_id") or "anonymous",
                    "title": kwargs.get("title") or thread.get("title") or "",
                    "status": "active",
                    "created_at": thread.get("created_at") or now,
                    "updated_at": now,
                    "last_message_at": thread.get("last_message_at"),
                    "deleted_at": None,
                    "metadata": {**(thread.get("metadata") or {}), **(kwargs.get("metadata") or {})},
                }
            )
            self._threads[thread_id] = thread
            return deepcopy(thread)

    def append_message(self, **kwargs) -> dict[str, Any]:
        with self._lock:
            role = kwargs["role"]
            explicit_turn_index = kwargs.get("turn_index")
            turn_index = (
                int(explicit_turn_index)
                if explicit_turn_index is not None
                else self._next_memory_turn(kwargs["thread_id"], role)
            )
            status = _normalize_status(kwargs.get("status"), default="accepted")
            now = _now_iso()
            message = {
                "id": f"msg_{_now_ms()}_{os.urandom(6).hex()}",
                "thread_id": kwargs["thread_id"],
                "branch_id": kwargs.get("branch_id") or "main",
                "parent_message_id": kwargs.get("parent_message_id"),
                "turn_index": turn_index,
                "role": role,
                "content_text": kwargs.get("content_text") or "",
                "content_json": kwargs.get("content_json") or {},
                "status": status,
                "request_id": kwargs.get("request_id"),
                "stream_id": kwargs.get("stream_id"),
                "model": kwargs.get("model"),
                "token_count": None,
                "auth_scope": kwargs.get("auth_scope") or {},
                "metadata": kwargs.get("metadata") or {},
                "created_at": now,
                "completed_at": now if status in {"completed", "failed", "cancelled"} else None,
                "superseded_at": None,
                "deleted_at": None,
            }
            self._messages.append(message)
            thread = self._threads.get(message["thread_id"])
            if thread:
                thread["last_message_at"] = now
                thread["updated_at"] = now
            return deepcopy(message)

    def update_message_status(self, **kwargs) -> dict[str, Any] | None:
        with self._lock:
            for message in self._messages:
                if message["id"] != kwargs["message_id"]:
                    continue
                status = _normalize_status(kwargs.get("status"))
                message["status"] = status
                if kwargs.get("content_text") is not None:
                    message["content_text"] = kwargs["content_text"]
                if kwargs.get("content_json"):
                    message["content_json"].update(kwargs["content_json"])
                if kwargs.get("metadata"):
                    message["metadata"].update(kwargs["metadata"])
                if status in {"completed", "failed", "cancelled"}:
                    message["completed_at"] = _now_iso()
                if status == "superseded":
                    message["superseded_at"] = _now_iso()
                return deepcopy(message)
        return None

    def link_artifacts(self, **kwargs) -> None:
        with self._lock:
            for ref in kwargs.get("artifact_refs") or []:
                if ref.get("artifact_id"):
                    self._refs.append({**ref, "thread_id": kwargs["thread_id"], "message_id": kwargs["message_id"]})

    def list_thread_ids(self, *, session_id: str) -> list[str]:
        with self._lock:
            threads = [
                thread
                for thread in self._threads.values()
                if thread.get("session_id") == session_id and not thread.get("deleted_at")
            ]
        threads.sort(key=lambda item: item.get("last_message_at") or item.get("updated_at") or "", reverse=True)
        return [thread["id"] for thread in threads]

    def list_messages(self, *, thread_id: str, include_superseded: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            messages = [
                deepcopy(message)
                for message in self._messages
                if message["thread_id"] == thread_id
                and not message.get("deleted_at")
                and (include_superseded or message.get("status") != "superseded")
            ]
        messages.sort(key=lambda item: (item["turn_index"], item["created_at"], item["id"]))
        return messages[-limit:] if limit is not None else messages

    def supersede_from_user_turn(self, *, thread_id: str, user_turn_index: int) -> list[dict[str, Any]]:
        with self._lock:
            user_messages = [
                item for item in self._messages
                if item["thread_id"] == thread_id and item["role"] == "user" and item["status"] != "superseded"
            ]
            user_messages.sort(key=lambda item: (item["turn_index"], item["created_at"], item["id"]))
            if user_turn_index >= len(user_messages):
                raise ValueError("user_turn_index is outside current history")
            target_turn = user_messages[user_turn_index]["turn_index"]
            now = _now_iso()
            for item in self._messages:
                if item["thread_id"] == thread_id and item["turn_index"] >= target_turn and item["status"] != "superseded":
                    item["status"] = "superseded"
                    item["superseded_at"] = now
            return self.list_messages(thread_id=thread_id)

    def soft_delete_thread(self, *, thread_id: str) -> None:
        with self._lock:
            now = _now_iso()
            if thread_id in self._threads:
                self._threads[thread_id]["deleted_at"] = now
                self._threads[thread_id]["status"] = "deleted"
            for item in self._messages:
                if item["thread_id"] == thread_id:
                    item["deleted_at"] = now

    def health_check(self) -> dict[str, Any]:
        with self._lock:
            return {"status": "available", "backend": "memory", "thread_count": len(self._threads), "message_count": len(self._messages)}

    def _next_memory_turn(self, thread_id: str, role: str) -> int:
        active = [
            item for item in self._messages
            if item["thread_id"] == thread_id and not item.get("deleted_at") and item.get("status") != "superseded"
        ]
        max_turn = max([item["turn_index"] for item in active], default=-1)
        return max_turn + 1 if role == "user" else max(max_turn, 0)


def messages_to_history_payload(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert stored messages to the existing frontend history payload shape."""

    payload: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        status = _normalize_status(message.get("status"))
        content_json = message.get("content_json") if isinstance(message.get("content_json"), dict) else {}
        item = {
            "id": message.get("id"),
            "role": role,
            "content": message.get("content_text") or "",
            "timestamp": message.get("completed_at") or message.get("created_at") or "",
            "thread_id": message.get("thread_id"),
            "threadId": message.get("thread_id"),
            "turn_index": message.get("turn_index"),
            "streamState": status,
            "statusText": _message_status_text(status),
        }
        if role == "assistant":
            item["isMarkdown"] = True
            if isinstance(content_json, dict):
                for key in (
                    "workflow_type",
                    "report_filename",
                    "report_url",
                    "sql_artifact",
                    "knowledge_artifact",
                    "analysis_artifact",
                    "report_artifact",
                    "workorder_decision",
                    "artifact",
                    "ui_payload",
                    "evidence_bundle",
                    "resolved_context",
                ):
                    if key in content_json:
                        item[key] = content_json[key]
        payload.append(item)
    return payload


_REPOSITORY: ConversationRepository | None = None
_REPOSITORY_LOCK = RLock()


def configure_conversation_repository(repository: ConversationRepository) -> ConversationRepository:
    """Inject a repository implementation."""

    global _REPOSITORY
    with _REPOSITORY_LOCK:
        _REPOSITORY = repository
        return _REPOSITORY


def reset_conversation_repository() -> None:
    """Reset cached repository."""

    global _REPOSITORY
    with _REPOSITORY_LOCK:
        _REPOSITORY = None


def get_conversation_repository() -> ConversationRepository:
    """Return the process-wide conversation repository."""

    global _REPOSITORY
    with _REPOSITORY_LOCK:
        if _REPOSITORY is None:
            if os.getenv("PYTEST_CURRENT_TEST"):
                _REPOSITORY = MemoryConversationRepository()
            else:
                _REPOSITORY = SQLiteConversationRepository()
        return _REPOSITORY
