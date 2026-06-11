"""历史会话与 Todo 应用服务。"""

from __future__ import annotations

from typing import Any

from ..runtime.dev_mode import get_dev_messages, get_dev_todos_payload, list_dev_threads
from ..common.logger import get_logger
from ..repositories.history_index import get_history_index_repository
from ..common.utils import sanitize_chat_history_messages, summarize_identifier_for_log


def summarize_session_id(session_id: str | None) -> str:
    return summarize_identifier_for_log(session_id, keep=8)


def summarize_thread_id(thread_id: str | None) -> str:
    return summarize_identifier_for_log(thread_id, keep=10)


def history_title(history_type: str, thread_id: str) -> str:
    suffix = thread_id[-6:] if thread_id else ""
    if history_type == "pdf":
        return f"PDF对话 {suffix}"
    if history_type == "service":
        return f"咨询 {suffix}"
    return f"对话 {suffix}"


def parse_history_limit(raw_value: str | None) -> int:
    try:
        return min(max(int(raw_value or "30"), 1), 50)
    except (TypeError, ValueError):
        return 30


def parse_history_cursor(raw_value: str | None) -> int:
    try:
        return max(int(raw_value or "0"), 0)
    except (TypeError, ValueError):
        return 0


def build_history_page_payload(
    history_type: str,
    thread_ids: list[str],
    *,
    limit: int,
    cursor: int,
    keyword: str,
) -> dict:
    normalized_keyword = keyword.strip().lower()
    if normalized_keyword:
        filtered_thread_ids = [
            thread_id for thread_id in thread_ids
            if normalized_keyword in thread_id.lower()
            or normalized_keyword in history_title(history_type, thread_id).lower()
        ]
    else:
        filtered_thread_ids = thread_ids

    page_thread_ids = filtered_thread_ids[cursor:cursor + limit + 1]
    visible_thread_ids = page_thread_ids[:limit]
    next_cursor = cursor + limit if len(page_thread_ids) > limit else None
    return {
        "items": [
            {"id": thread_id, "title": history_title(history_type, thread_id)}
            for thread_id in visible_thread_ids
        ],
        "has_more": next_cursor is not None,
        "next_cursor": str(next_cursor) if next_cursor is not None else None,
        "limit": limit,
        "cursor": str(cursor),
        "keyword": keyword,
        "total_returned": len(visible_thread_ids),
    }


def empty_todos_payload(thread_id: str) -> dict:
    return {
        "thread_id": thread_id,
        "todos": [],
        "summary": {
            "total": 0,
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
        },
    }


def summarize_todos(todos: list[dict]) -> dict:
    return {
        "total": len(todos),
        "pending": sum(1 for todo in todos if todo.get("status") == "pending"),
        "in_progress": sum(1 for todo in todos if todo.get("status") == "in_progress"),
        "completed": sum(1 for todo in todos if todo.get("status") == "completed"),
    }


def filter_todos_by_status(todos: list[dict], status: str | None) -> list[dict]:
    if status and status in ["pending", "in_progress", "completed"]:
        return [todo for todo in todos if todo.get("status") == status]
    return todos


class HistoryService:
    """封装历史会话和 Todo 查询用例。"""

    def __init__(
        self,
        *,
        app,
        session_manager,
        session_id: str,
        legacy_bindings: dict,
        history_index_repository=None,
        logger=None,
    ) -> None:
        self.app = app
        self.session_manager = session_manager
        self.session_id = session_id
        self.legacy_bindings = legacy_bindings
        self.history_index_repository = (
            history_index_repository
            or getattr(app.state, "history_index_repository", None)
            or get_history_index_repository()
        )
        self._log = logger or get_logger("services.history")

    async def list_history(
        self,
        *,
        history_type: str,
        paged_response: bool,
        limit: int,
        cursor: int,
        keyword: str,
    ) -> list[str] | dict:
        self._log.info(
            "收到聊天历史列表请求",
            path=f"/ai/history/{history_type}",
            history_type=history_type,
            session_id=summarize_session_id(self.session_id),
            dev_mode=bool(getattr(self.app.state, "dev_mode", False)),
            paged_response=paged_response,
            limit=limit if paged_response else None,
            cursor=cursor if paged_response else None,
            keyword_len=len(keyword),
        )

        if getattr(self.app.state, "dev_mode", False):
            thread_ids = self.session_manager.filter_owned_thread_ids(self.session_id, list_dev_threads(self.app))
            self._log_history_list_returned(history_type, thread_ids, paged_response)
            return self._history_list_payload(history_type, thread_ids, paged_response, limit, cursor, keyword)

        try:
            indexed_thread_ids = self.history_index_repository.list_thread_ids(session_id=self.session_id)
            owned_chat_ids = self.session_manager.filter_owned_thread_ids(self.session_id, indexed_thread_ids)
            self._log_history_list_returned(history_type, owned_chat_ids, paged_response)
            return self._history_list_payload(history_type, owned_chat_ids, paged_response, limit, cursor, keyword)
        except Exception as exc:
            self._log.warning(
                "获取聊天历史失败",
                path=f"/ai/history/{history_type}",
                history_type=history_type,
                session_id=summarize_session_id(self.session_id),
                error=str(exc),
                paged_response=paged_response,
            )
            return self._history_list_payload(history_type, [], paged_response, limit, cursor, keyword)

    async def get_messages(self, *, history_type: str, chat_id: str) -> list[dict[str, Any]]:
        self._log.info(
            "收到对话历史详情请求",
            path=f"/ai/history/{history_type}/:chat_id",
            history_type=history_type,
            session_id=summarize_session_id(self.session_id),
            requested_chat_id=summarize_thread_id(chat_id),
        )
        resolved_chat_id = self.session_manager.resolve_history_thread_id(
            self.session_id,
            chat_id,
            self.legacy_bindings,
        )
        if not resolved_chat_id:
            self._log.warning(
                "Rejected unauthorized chat history access",
                history_type=history_type,
                session_id=summarize_session_id(self.session_id),
                requested_chat_id=summarize_thread_id(chat_id),
            )
            return []

        if getattr(self.app.state, "dev_mode", False):
            messages = get_dev_messages(self.app, resolved_chat_id)
            self._log.info(
                "返回对话历史详情",
                history_type=history_type,
                session_id=summarize_session_id(self.session_id),
                chat_id=summarize_thread_id(resolved_chat_id),
                message_count=len(messages),
                dev_mode=True,
            )
            return messages

        try:
            config = {"configurable": {"thread_id": resolved_chat_id}}
            checkpoint = await self.app.state.checkpointer.aget(config)

            if checkpoint and checkpoint.get("channel_values"):
                messages = checkpoint["channel_values"].get("messages", [])
                sanitized_messages = sanitize_chat_history_messages(messages)
                self._record_history_thread(history_type, resolved_chat_id)
                self._log.info(
                    "返回对话历史详情",
                    history_type=history_type,
                    session_id=summarize_session_id(self.session_id),
                    chat_id=summarize_thread_id(resolved_chat_id),
                    message_count=len(sanitized_messages) if isinstance(sanitized_messages, list) else 0,
                    checkpoint_hit=True,
                )
                return sanitized_messages
            self._log.info(
                "返回对话历史详情",
                history_type=history_type,
                session_id=summarize_session_id(self.session_id),
                chat_id=summarize_thread_id(resolved_chat_id),
                message_count=0,
                checkpoint_hit=False,
            )
            return []
        except Exception as exc:
            self._log.warning(
                "获取对话历史失败",
                history_type=history_type,
                session_id=summarize_session_id(self.session_id),
                chat_id=summarize_thread_id(resolved_chat_id),
                error=str(exc),
            )
            return []

    async def delete_history(self, *, history_type: str, chat_id: str) -> dict | None:
        self._log.info(
            "收到删除对话历史请求",
            path=f"/ai/history/{history_type}/:chat_id",
            history_type=history_type,
            session_id=summarize_session_id(self.session_id),
            requested_chat_id=summarize_thread_id(chat_id),
        )
        resolved_chat_id = self.session_manager.resolve_history_thread_id(
            self.session_id,
            chat_id,
            self.legacy_bindings,
        )
        if not resolved_chat_id:
            self._log.warning(
                "拒绝删除未授权对话历史",
                history_type=history_type,
                session_id=summarize_session_id(self.session_id),
                requested_chat_id=summarize_thread_id(chat_id),
            )
            return None

        if getattr(self.app.state, "dev_mode", False):
            for attr_name in ("dev_messages", "dev_todos", "dev_updated_at"):
                state_value = getattr(self.app.state, attr_name, None)
                if isinstance(state_value, dict):
                    state_value.pop(resolved_chat_id, None)
            self._log.info(
                "已删除本地开发模式对话历史",
                history_type=history_type,
                session_id=summarize_session_id(self.session_id),
                chat_id=summarize_thread_id(resolved_chat_id),
            )
            return {"deleted": True, "server_deleted": True, "thread_id": chat_id}

        try:
            checkpointer = getattr(self.app.state, "checkpointer", None)
            if checkpointer and hasattr(checkpointer, "adelete_thread"):
                await checkpointer.adelete_thread(resolved_chat_id)
            self._remove_history_thread(resolved_chat_id)
            self._clear_thread_artifact(history_type, resolved_chat_id)
            self._log.info(
                "已删除对话历史",
                history_type=history_type,
                session_id=summarize_session_id(self.session_id),
                chat_id=summarize_thread_id(resolved_chat_id),
            )
            return {"deleted": True, "server_deleted": True, "thread_id": chat_id}
        except Exception as exc:
            self._log.warning(
                "删除对话历史失败",
                history_type=history_type,
                session_id=summarize_session_id(self.session_id),
                chat_id=summarize_thread_id(resolved_chat_id),
                error=str(exc),
            )
            raise RuntimeError("删除咨询记录失败") from exc

    async def get_todos(self, *, thread_id: str, status: str | None = None) -> dict:
        self._log.info(
            "收到任务清单请求",
            path="/api/todos/:thread_id",
            session_id=summarize_session_id(self.session_id),
            requested_thread_id=summarize_thread_id(thread_id),
            status_filter=status or "all",
        )
        resolved_thread_id = self.session_manager.resolve_history_thread_id(
            self.session_id,
            thread_id,
            self.legacy_bindings,
        )
        if not resolved_thread_id:
            self._log.warning(
                "Rejected unauthorized todo access",
                session_id=summarize_session_id(self.session_id),
                requested_thread_id=summarize_thread_id(thread_id),
                status_filter=status or "all",
            )
            return empty_todos_payload(thread_id)

        if getattr(self.app.state, "dev_mode", False):
            payload = get_dev_todos_payload(self.app, resolved_thread_id)
            if status and status in ["pending", "in_progress", "completed"]:
                payload["todos"] = filter_todos_by_status(payload["todos"], status)
                payload["summary"] = summarize_todos(payload["todos"])
            payload["thread_id"] = thread_id
            self._log_todos_returned(resolved_thread_id, status, payload, dev_mode=True)
            return payload

        try:
            config = {"configurable": {"thread_id": resolved_thread_id}}
            checkpoint = await self.app.state.checkpointer.aget(config)

            todos = []
            if checkpoint and checkpoint.get("channel_values"):
                todos = checkpoint["channel_values"].get("todos", [])

            todos = filter_todos_by_status(todos, status)
            summary = summarize_todos(todos)
            payload = {
                "thread_id": thread_id,
                "todos": todos,
                "summary": summary,
            }
            self._log_todos_returned(resolved_thread_id, status, payload)
            return payload
        except Exception as exc:
            self._log.warning(
                "获取任务清单失败",
                session_id=summarize_session_id(self.session_id),
                thread_id=summarize_thread_id(thread_id),
                status_filter=status or "all",
                error=str(exc),
            )
            return empty_todos_payload(thread_id)

    def _history_list_payload(
        self,
        history_type: str,
        thread_ids: list[str],
        paged_response: bool,
        limit: int,
        cursor: int,
        keyword: str,
    ) -> list[str] | dict:
        if paged_response:
            return build_history_page_payload(
                history_type,
                thread_ids,
                limit=limit,
                cursor=cursor,
                keyword=keyword,
            )
        return thread_ids

    def _log_history_list_returned(
        self,
        history_type: str,
        thread_ids: list[str],
        paged_response: bool,
    ) -> None:
        self._log.info(
            "返回聊天历史列表",
            path=f"/ai/history/{history_type}",
            history_type=history_type,
            session_id=summarize_session_id(self.session_id),
            thread_count=len(thread_ids),
            paged_response=paged_response,
        )

    def _log_todos_returned(
        self,
        resolved_thread_id: str,
        status: str | None,
        payload: dict,
        *,
        dev_mode: bool = False,
    ) -> None:
        summary = payload["summary"]
        self._log.info(
            "返回任务清单",
            session_id=summarize_session_id(self.session_id),
            thread_id=summarize_thread_id(resolved_thread_id),
            status_filter=status or "all",
            todo_total=summary["total"],
            completed=summary["completed"],
            in_progress=summary["in_progress"],
            pending=summary["pending"],
            dev_mode=dev_mode or None,
        )

    def _clear_thread_artifact(self, history_type: str, thread_id: str) -> None:
        try:
            from ..workflows.artifact_store import clear_thread_artifact

            clear_thread_artifact(thread_id)
        except Exception as artifact_error:
            self._log.warning(
                "清理对话产物失败",
                history_type=history_type,
                chat_id=summarize_thread_id(thread_id),
                error=str(artifact_error),
            )

    def _record_history_thread(self, history_type: str, thread_id: str) -> None:
        try:
            self.history_index_repository.record_thread(
                session_id=self.session_id,
                thread_id=thread_id,
                history_type=history_type,
            )
        except Exception as index_error:
            self._log.warning(
                "刷新历史索引失败",
                history_type=history_type,
                chat_id=summarize_thread_id(thread_id),
                error=str(index_error),
            )

    def _remove_history_thread(self, thread_id: str) -> None:
        try:
            self.history_index_repository.remove_thread(
                session_id=self.session_id,
                thread_id=thread_id,
            )
        except Exception as index_error:
            self._log.warning(
                "清理历史索引失败",
                chat_id=summarize_thread_id(thread_id),
                error=str(index_error),
            )
