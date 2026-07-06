"""聊天与 Agent 兼容入口应用服务。"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from ..auth.admin_auth import resolve_auth_context
from ..context.conversation_context import ConversationContextAssembler
from ..runtime.dev_mode import get_dev_messages
from ..common.logger import ensure_request_id, get_logger
from ..repositories.conversation_store import (
    ConversationRepository,
    get_conversation_repository,
    messages_to_history_payload,
)
from ..repositories.history_index import get_history_index_repository
from ..services.history_service import load_artifact_history_messages
from ..auth.session_scope import resolve_request_scope
from ..agent_runtime.stream_control import (
    build_stream_stop_payload,
    cancel_stream_handle,
    clear_stream_handle,
    register_stream_handle,
)
from ..agent_runtime.streaming import token_stream_events as default_token_stream_events
from ..single_agent.planner import build_plan_snapshot
from .conversation_persistence import ConversationPersistenceService, parse_sse_payloads
from ..common.utils import (
    sanitize_chat_history_messages,
    summarize_identifier_for_log,
    summarize_text_for_log,
)


class StopStreamPayload(BaseModel):
    stream_id: str
    reason: str = "user_stop"


class AgentChatPayload(BaseModel):
    message: str
    session_id: str | None = None
    thread_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class AgentInvocationContext:
    message: str
    thread_id: str
    session_id: str
    auth_context: Any
    history_messages: list[dict[str, Any]]
    metadata: dict[str, Any]
    channel: str
    stream_id: str
    request_id: str
    requested_thread_id: str | None
    requested_user_identity: str
    trusted_user_identity: str
    session_manager: Any
    legacy_bindings: dict[str, str]
    updated_legacy_bindings: dict[str, str]
    minted_new_thread: bool
    conversation_context: dict[str, Any] | None = None
    user_message_id: str | None = None


def summarize_session_id(session_id: str | None) -> str:
    return summarize_identifier_for_log(session_id, keep=8)


def summarize_thread_id(thread_id: str | None) -> str:
    return summarize_identifier_for_log(thread_id, keep=10)


def build_visual_action_from_stream_event(event: dict[str, Any]) -> dict[str, Any] | None:
    msg_type = event.get("type")
    if msg_type == "tool_end":
        tool_name = event.get("tool") or event.get("name") or ""
        if tool_name not in {"render_chart", "write_report", "write_todos"}:
            return None
        return {
            "type": "visual_action",
            "tool": tool_name,
            "result_preview": event.get("result_preview") or event.get("result") or "",
            "truncated": bool(event.get("truncated", False)),
        }
    if msg_type == "chat_complete" and event.get("todos"):
        return {"type": "todos_update", "todos": event.get("todos", [])}
    return None


def history_message_role(message: dict[str, Any]) -> str:
    role = str(message.get("role") or message.get("type") or "").strip().lower()
    if role in {"human", "humanmessage", "user"}:
        return "user"
    if role in {"ai", "aimessage", "assistant"}:
        return "assistant"
    return role


def history_message_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if content is None:
        content = message.get("message") or ""
    return str(content)


def truncate_history_before_user_turn(
    messages: list[dict[str, Any]],
    user_turn_index: int,
) -> tuple[list[dict[str, Any]], int]:
    """截断到目标用户消息之前，返回保留消息和历史中的用户消息总数。"""

    if user_turn_index < 0:
        raise ValueError("user_turn_index must be greater than or equal to 0")

    total_user_turns = sum(1 for message in messages if history_message_role(message) == "user")
    kept_messages: list[dict[str, Any]] = []
    seen_user_turns = 0
    found_target = False

    for message in messages:
        role = history_message_role(message)
        if role == "user":
            if seen_user_turns == user_turn_index:
                found_target = True
                break
            seen_user_turns += 1
        kept_messages.append(message)

    if not found_target and user_turn_index > seen_user_turns:
        raise ValueError("user_turn_index is outside current history")

    return kept_messages, total_user_turns


def to_langchain_history_message(message: dict[str, Any]):
    role = history_message_role(message)
    content = history_message_content(message).strip()
    if not content:
        return None
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    return None


def to_langchain_history_messages(messages: list[dict[str, Any]]) -> list:
    converted = []
    for message in messages:
        converted_message = to_langchain_history_message(message)
        if converted_message is not None:
            converted.append(converted_message)
    return converted


class ChatService:
    """封装聊天流、编辑重生成、语音 Agent 聚合和停止流用例。"""

    def __init__(self, *, stream_events=default_token_stream_events, logger=None) -> None:
        self.stream_events = stream_events
        self._log = logger or get_logger("services.chat")

    def _resolve_requested_thread_id(self, thread_id: str | None, metadata: dict[str, Any] | None) -> str | None:
        candidates = [thread_id, (metadata or {}).get("thread_id")]
        for candidate in candidates:
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized
        return None

    def _resolve_thread_id(
        self,
        session_manager,
        session_id: str,
        requested_thread_id: str | None,
        legacy_bindings: dict[str, str],
    ) -> tuple[str, bool, dict[str, str], str | None]:
        if requested_thread_id:
            legacy_thread_id = requested_thread_id if session_manager.is_legacy_thread_id(requested_thread_id) else None
            return requested_thread_id, False, dict(legacy_bindings), legacy_thread_id
        return session_manager.resolve_thread_id(session_id, None, legacy_bindings)

    def _build_complete_payload_enricher(self, context: AgentInvocationContext):
        def enrich(payload: dict[str, Any]) -> dict[str, Any]:
            enriched = dict(payload)
            enriched.setdefault("auth_context", context.auth_context.audit_summary())
            enriched.setdefault(
                "invocation_context",
                {
                    "channel": context.channel,
                    "session_id": context.session_id,
                    "thread_id": context.thread_id,
                    "requested_thread_id": context.requested_thread_id,
                    "stream_id": context.stream_id,
                    "request_id": context.request_id,
                    "metadata": context.metadata,
                },
            )
            if context.conversation_context:
                enriched.setdefault(
                    "conversation_context",
                    {
                        "version": context.conversation_context.get("version"),
                        "stats": context.conversation_context.get("stats"),
                        "artifact_refs": context.conversation_context.get("artifact_refs", []),
                    },
                )
            return enriched

        return enrich

    def _conversation_repository(self, app) -> ConversationRepository:
        return getattr(app.state, "conversation_repository", None) or get_conversation_repository()

    def _conversation_persistence(self, app) -> ConversationPersistenceService:
        return ConversationPersistenceService(
            repository=self._conversation_repository(app),
            logger=self._log,
        )

    def _build_read_only_conversation_context(self, app, context: AgentInvocationContext) -> dict[str, Any] | None:
        try:
            return ConversationContextAssembler(
                conversation_repository=self._conversation_repository(app)
            ).build(
                thread_id=context.thread_id,
                current_user_message=context.message,
                auth_context=context.auth_context,
            )
        except Exception as exc:
            self._log.warning(
                "构造只读对话上下文失败",
                thread_id=summarize_thread_id(context.thread_id),
                request_id=context.request_id,
                error=str(exc),
            )
            return None

    async def prepare_agent_invocation_context(
        self,
        request: Request,
        *,
        message: str,
        thread_id: str | None = None,
        user_identity: str = "游客",
        stream_id: str | None = None,
        channel: str = "text",
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        track_history: bool = True,
    ) -> AgentInvocationContext:
        session_manager, session_id, _, legacy_bindings = resolve_request_scope(request)
        auth_context = resolve_auth_context(request, session_id)
        identity = auth_context.identity_payload()
        trusted_user_identity = str(identity.get("user_role") or "访客")
        requested_user_identity = user_identity if user_identity in ["游客", "管理员"] else "游客"
        request_id = (request_id or ensure_request_id()).strip()
        stream_id = (stream_id or "").strip() or str(uuid4())
        requested_thread_id = self._resolve_requested_thread_id(thread_id, metadata)
        if requested_user_identity != trusted_user_identity:
            identity_log_message = "忽略不可信的 plan-only 身份参数"
            if channel == "text":
                identity_log_message = "忽略不可信的前端身份参数"
            elif channel == "voice":
                identity_log_message = "忽略不可信的语音 Agent 身份参数"
            self._log.info(
                identity_log_message,
                requested_user_identity=requested_user_identity,
                trusted_user_identity=trusted_user_identity,
                session_id=summarize_session_id(session_id),
            )

        resolved_thread_id, minted_new_thread, updated_legacy_bindings, legacy_thread_id = self._resolve_thread_id(
            session_manager,
            session_id,
            requested_thread_id,
            legacy_bindings,
        )
        if legacy_thread_id and resolved_thread_id != legacy_thread_id:
            self._log.info(
                "legacy thread_id 已绑定到当前会话的新 thread",
                legacy_thread_id=summarize_thread_id(legacy_thread_id),
                thread_id=summarize_thread_id(resolved_thread_id),
            )
        elif requested_thread_id and requested_thread_id != resolved_thread_id:
            self._log.warning("忽略未授权 thread_id", requested_thread_id=summarize_thread_id(requested_thread_id))
        if minted_new_thread:
            self._log.info("签发新的 thread_id", thread_id=summarize_thread_id(resolved_thread_id))

        history_messages = await self._load_thread_history_messages(request, resolved_thread_id)
        if track_history:
            self._record_history_thread(
                request.app,
                session_id=session_id,
                thread_id=resolved_thread_id,
                history_type="voice" if channel == "voice" else "service",
            )

        merged_metadata = dict(metadata or {})
        merged_metadata.update(
            {
                "channel": channel,
                "session_id": session_id,
                "thread_id": resolved_thread_id,
                "requested_thread_id": requested_thread_id,
                "request_id": request_id,
                "stream_id": stream_id,
                "user_identity": trusted_user_identity,
                "requested_user_identity": requested_user_identity,
            }
        )

        return AgentInvocationContext(
            message=message,
            thread_id=resolved_thread_id,
            session_id=session_id,
            auth_context=auth_context,
            history_messages=history_messages,
            metadata=merged_metadata,
            channel=channel,
            stream_id=stream_id,
            request_id=request_id,
            requested_thread_id=requested_thread_id,
            requested_user_identity=requested_user_identity,
            trusted_user_identity=trusted_user_identity,
            session_manager=session_manager,
            legacy_bindings=legacy_bindings,
            updated_legacy_bindings=updated_legacy_bindings,
            minted_new_thread=minted_new_thread,
        )

    async def plan_chat(
        self,
        request: Request,
        *,
        message: str,
        thread_id: str | None = None,
        user_identity: str = "游客",
    ):
        request_id = ensure_request_id()
        if not message:
            raise HTTPException(status_code=400, detail="message parameter is required")
        context = await self.prepare_agent_invocation_context(
            request,
            message=message,
            thread_id=thread_id,
            user_identity=user_identity,
            channel="plan",
            request_id=request_id,
            track_history=False,
        )
        self._log.info(
            "收到 plan-only 请求",
            path="/chat/plan",
            session_id=summarize_session_id(context.session_id),
            thread_id=summarize_thread_id(context.thread_id),
            auth_role=context.auth_context.role,
            auth_user_id=context.auth_context.user_id,
            auth_method=context.auth_context.auth_method,
            message_len=len(message),
            message_preview=summarize_text_for_log(message, limit=72),
        )
        snapshot = build_plan_snapshot(
            message=context.message,
            thread_id=context.thread_id,
            user_identity=context.trusted_user_identity,
            auth_context=context.auth_context,
            conversation_context=self._build_read_only_conversation_context(request.app, context),
        )
        payload = snapshot.model_dump(exclude_none=True)
        payload["thread_id"] = context.thread_id
        payload["request_id"] = context.request_id
        payload["auth_context"] = context.auth_context.audit_summary()
        payload["invocation_context"] = {
            "channel": context.channel,
            "session_id": context.session_id,
            "thread_id": context.thread_id,
            "requested_thread_id": context.requested_thread_id,
            "request_id": context.request_id,
            "metadata": context.metadata,
        }
        response = JSONResponse(content=payload)
        context.session_manager.attach_scope_cookies(response, context.session_id, context.updated_legacy_bindings)
        return response

    async def stream_chat(
        self,
        request: Request,
        *,
        message: str,
        thread_id: str | None = None,
        user_identity: str = "游客",
        stream_id: str | None = None,
    ):
        request_id = ensure_request_id()
        try:
            if not message:
                raise HTTPException(status_code=400, detail="message parameter is required")
            context = await self.prepare_agent_invocation_context(
                request,
                message=message,
                thread_id=thread_id,
                user_identity=user_identity,
                stream_id=stream_id,
                channel="text",
                request_id=request_id,
            )
            self._log.info(
                "收到聊天流式请求",
                path="/chat/stream",
                session_id=summarize_session_id(context.session_id),
                requested_thread_id=summarize_thread_id(context.requested_thread_id),
                stream_id=summarize_identifier_for_log(context.stream_id, keep=8),
                user_identity=context.trusted_user_identity,
                auth_role=context.auth_context.role,
                auth_user_id=context.auth_context.user_id,
                auth_method=context.auth_context.auth_method,
                message_len=len(message),
                message_preview=summarize_text_for_log(message, limit=72),
            )

            self._log.info(
                "Chat stream request accepted",
                path="/chat/stream",
                session_id=summarize_session_id(context.session_id),
                thread_id=summarize_thread_id(context.thread_id),
                stream_id=summarize_identifier_for_log(context.stream_id, keep=8),
                channel=context.channel,
                minted_new_thread=context.minted_new_thread,
                auth_role=context.auth_context.role,
                auth_user_id=context.auth_context.user_id,
                auth_method=context.auth_context.auth_method,
            )
            cancel_handle = await register_stream_handle(
                request.app,
                stream_id=context.stream_id,
                request_id=context.request_id,
                thread_id=context.thread_id,
                session_id=context.session_id,
            )
            self._conversation_persistence(request.app).prepare_turn(context)

            response = StreamingResponse(
                self._conversation_persistence(request.app).stream_events_with_persistence(
                    context,
                    self.stream_events(
                        request.app,
                        context.message,
                        context.thread_id,
                        context.trusted_user_identity,
                        request_id=context.request_id,
                        stream_id=context.stream_id,
                        cancel_handle=cancel_handle,
                        history_messages=context.history_messages,
                        replace_history=False,
                        auth_context=context.auth_context,
                        conversation_context=context.conversation_context,
                        complete_payload_enricher=self._build_complete_payload_enricher(context),
                    ),
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
            context.session_manager.attach_scope_cookies(response, context.session_id, context.updated_legacy_bindings)
            return response
        except HTTPException:
            raise
        except Exception as exc:
            cleanup_stream_id = context.stream_id if "context" in locals() else (stream_id or "").strip()
            if cleanup_stream_id:
                await clear_stream_handle(request.app, cleanup_stream_id)
            self._log.exception(
                "创建流式响应失败",
                error=str(exc),
                path="/chat/stream",
                session_id=summarize_session_id(context.session_id) if "context" in locals() else "",
                requested_thread_id=summarize_thread_id(thread_id),
                stream_id=summarize_identifier_for_log(cleanup_stream_id, keep=8) if cleanup_stream_id else "",
            )
            response = JSONResponse(
                status_code=500,
                content={"message": "Service temporarily unavailable, please retry later", "error_id": request_id},
            )
            if "context" in locals():
                context.session_manager.attach_scope_cookies(response, context.session_id, context.updated_legacy_bindings)
            return response

    async def stream_edit(
        self,
        request: Request,
        *,
        message: str,
        thread_id: str,
        user_turn_index: int,
        user_identity: str = "游客",
        stream_id: str | None = None,
    ):
        session_manager, session_id, _, legacy_bindings = resolve_request_scope(request)
        auth_context = resolve_auth_context(request, session_id)
        identity = auth_context.identity_payload()
        trusted_user_identity = str(identity.get("user_role") or "访客")
        request_id = ensure_request_id()
        resolved_thread_id = session_manager.resolve_history_thread_id(session_id, thread_id, legacy_bindings)
        stream_id = (stream_id or "").strip() or str(uuid4())

        try:
            normalized_message = (message or "").strip()
            if not normalized_message:
                raise HTTPException(status_code=400, detail="message parameter is required")
            if user_turn_index < 0:
                raise HTTPException(status_code=400, detail="user_turn_index must be greater than or equal to 0")
            if not resolved_thread_id:
                raise HTTPException(status_code=404, detail="未找到可编辑的咨询记录")

            requested_user_identity = user_identity if user_identity in ["游客", "管理员"] else "游客"
            if requested_user_identity != trusted_user_identity:
                self._log.info(
                    "忽略不可信的编辑重生成身份参数",
                    requested_user_identity=requested_user_identity,
                    trusted_user_identity=trusted_user_identity,
                    session_id=summarize_session_id(session_id),
                )

            history_messages = await self._load_thread_history_messages(request, resolved_thread_id)
            kept_messages, total_user_turns = truncate_history_before_user_turn(
                history_messages,
                user_turn_index,
            )
            if getattr(request.app.state, "dev_mode", False):
                await self._overwrite_thread_history_state(request, resolved_thread_id, kept_messages)
            else:
                try:
                    stored_kept_messages = self._conversation_repository(request.app).supersede_from_user_turn(
                        thread_id=resolved_thread_id,
                        user_turn_index=user_turn_index,
                    )
                    kept_payload = messages_to_history_payload(stored_kept_messages)
                    sanitized_kept = sanitize_chat_history_messages(kept_payload)
                    if isinstance(sanitized_kept, list):
                        kept_messages = sanitized_kept
                except Exception as exc:
                    self._log.warning(
                        "标记编辑后的旧对话消息失败，将继续使用内存截断历史",
                        thread_id=summarize_thread_id(resolved_thread_id),
                        user_turn_index=user_turn_index,
                        error=str(exc),
                    )

            if user_turn_index < max(total_user_turns - 1, 0):
                self._clear_stale_thread_artifact(resolved_thread_id)
            self._record_history_thread(
                request.app,
                session_id=session_id,
                thread_id=resolved_thread_id,
                history_type="service",
            )

            self._log.info(
                "收到编辑后重新生成请求",
                path="/chat/stream/edit",
                session_id=summarize_session_id(session_id),
                thread_id=summarize_thread_id(resolved_thread_id),
                stream_id=summarize_identifier_for_log(stream_id, keep=8),
                user_turn_index=user_turn_index,
                kept_message_count=len(kept_messages),
                original_message_count=len(history_messages),
                auth_role=auth_context.role,
                auth_user_id=auth_context.user_id,
                auth_method=auth_context.auth_method,
                message_len=len(normalized_message),
                message_preview=summarize_text_for_log(normalized_message, limit=72),
            )

            cancel_handle = await register_stream_handle(
                request.app,
                stream_id=stream_id,
                request_id=request_id,
                thread_id=resolved_thread_id,
                session_id=session_id,
            )
            edit_context = AgentInvocationContext(
                message=normalized_message,
                thread_id=resolved_thread_id,
                session_id=session_id,
                auth_context=auth_context,
                history_messages=kept_messages,
                metadata={
                    "channel": "text_edit",
                    "session_id": session_id,
                    "thread_id": resolved_thread_id,
                    "request_id": request_id,
                    "stream_id": stream_id,
                    "user_identity": trusted_user_identity,
                    "requested_user_identity": requested_user_identity,
                    "user_turn_index": user_turn_index,
                },
                channel="text_edit",
                stream_id=stream_id,
                request_id=request_id,
                requested_thread_id=thread_id,
                requested_user_identity=requested_user_identity,
                trusted_user_identity=trusted_user_identity,
                session_manager=session_manager,
                legacy_bindings=legacy_bindings,
                updated_legacy_bindings=legacy_bindings,
                minted_new_thread=False,
            )
            self._conversation_persistence(request.app).prepare_turn(
                edit_context,
                branch_id=f"edit-{request_id}",
                parent_message_id=(kept_messages[-1].get("id") if kept_messages else None),
            )

            response = StreamingResponse(
                self._conversation_persistence(request.app).stream_events_with_persistence(
                    edit_context,
                    self.stream_events(
                        request.app,
                        normalized_message,
                        resolved_thread_id,
                        trusted_user_identity,
                        request_id=request_id,
                        stream_id=stream_id,
                        cancel_handle=cancel_handle,
                        history_messages=to_langchain_history_messages(kept_messages),
                        replace_history=True,
                        auth_context=auth_context,
                        conversation_context=edit_context.conversation_context,
                        complete_payload_enricher=self._build_complete_payload_enricher(edit_context),
                    ),
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
            session_manager.attach_scope_cookies(response, session_id, legacy_bindings)
            return response
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            if stream_id:
                await clear_stream_handle(request.app, stream_id)
            self._log.exception(
                "创建编辑重生成流式响应失败",
                error=str(exc),
                path="/chat/stream/edit",
                session_id=summarize_session_id(session_id),
                requested_thread_id=summarize_thread_id(thread_id),
                resolved_thread_id=summarize_thread_id(resolved_thread_id),
                stream_id=summarize_identifier_for_log(stream_id, keep=8),
            )
            response = JSONResponse(
                status_code=500,
                content={"message": "Service temporarily unavailable, please retry later", "error_id": request_id},
            )
            session_manager.attach_scope_cookies(response, session_id, legacy_bindings)
            return response

    async def agent_chat(self, request: Request, payload: AgentChatPayload):
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="message is required")

        metadata = payload.metadata or {}
        request_id = ensure_request_id()
        context = await self.prepare_agent_invocation_context(
            request,
            message=message,
            thread_id=payload.thread_id or metadata.get("thread_id"),
            user_identity=str(metadata.get("user_identity") or metadata.get("identity") or "游客"),
            stream_id=metadata.get("stream_id"),
            channel="voice",
            metadata={
                **metadata,
                "client_session_id": payload.session_id,
            },
            request_id=request_id,
        )

        reply_text = ""
        accumulated_tokens: list[str] = []
        visual_actions: list[dict[str, Any]] = []
        error_message = ""

        self._log.info(
            "收到语音 Agent JSON 请求",
            path="/agent/chat",
            session_id=summarize_session_id(context.session_id),
            thread_id=summarize_thread_id(context.thread_id),
            stream_id=summarize_identifier_for_log(context.stream_id, keep=8),
            auth_role=context.auth_context.role,
            auth_user_id=context.auth_context.user_id,
            auth_method=context.auth_context.auth_method,
            message_len=len(message),
            message_preview=summarize_text_for_log(message, limit=72),
        )

        try:
            self._conversation_persistence(request.app).prepare_turn(context)
            async for chunk in self._conversation_persistence(request.app).stream_events_with_persistence(
                context,
                self.stream_events(
                    request.app,
                    context.message,
                    context.thread_id,
                    context.trusted_user_identity,
                    request_id=context.request_id,
                    stream_id=context.stream_id,
                    history_messages=context.history_messages,
                    replace_history=False,
                    auth_context=context.auth_context,
                    conversation_context=context.conversation_context,
                    complete_payload_enricher=self._build_complete_payload_enricher(context),
                ),
            ):
                for event in parse_sse_payloads(chunk):
                    msg_type = event.get("type")
                    if msg_type == "token":
                        accumulated_tokens.append(str(event.get("content") or ""))
                    elif msg_type == "chat_complete":
                        reply_text = str(
                            event.get("final_content")
                            or event.get("grounded_final_content")
                            or event.get("raw_final_content")
                            or ""
                        )
                    elif msg_type in {"error", "server_error"}:
                        error_message = str(event.get("message") or event.get("error") or "Agent 处理失败")

                    action = build_visual_action_from_stream_event(event)
                    if action:
                        visual_actions.append(action)
        except Exception as exc:
            self._log.exception(
                "语音 Agent JSON 请求失败",
                error=str(exc),
                session_id=summarize_session_id(context.session_id),
                thread_id=summarize_thread_id(context.thread_id),
            )
            raise HTTPException(status_code=500, detail="Agent service temporarily unavailable") from exc

        if not reply_text:
            reply_text = "".join(accumulated_tokens).strip()

        if not reply_text and error_message:
            return JSONResponse(
                status_code=502,
                content={
                    "reply_text": "",
                    "visual_actions": visual_actions,
                    "session_id": payload.session_id,
                    "thread_id": context.thread_id,
                    "error": error_message,
                },
            )

        return {
            "reply_text": reply_text,
            "visual_actions": visual_actions,
            "session_id": payload.session_id,
            "thread_id": context.thread_id,
            "metadata": {
                "request_id": context.request_id,
                "thread_id": context.thread_id,
                "session_id": context.session_id,
                "channel": context.channel,
                "auth_context": context.auth_context.audit_summary(),
            },
        }

    async def stop_stream(self, request: Request, payload: StopStreamPayload):
        _, session_id, _, _ = resolve_request_scope(request)
        stream_id = (payload.stream_id or "").strip()
        reason = (payload.reason or "user_stop").strip() or "user_stop"

        self._log.info(
            "收到停止流式请求",
            path="/chat/stop",
            session_id=summarize_session_id(session_id),
            stream_id=summarize_identifier_for_log(stream_id, keep=8),
            reason=reason,
        )

        if not stream_id:
            raise HTTPException(status_code=400, detail="stream_id is required")

        status, handle = await cancel_stream_handle(
            request.app,
            stream_id=stream_id,
            session_id=session_id,
            reason=reason,
        )

        if status == "forbidden":
            self._log.warning(
                "拒绝停止不属于当前会话的流式请求",
                path="/chat/stop",
                session_id=summarize_session_id(session_id),
                stream_id=summarize_identifier_for_log(stream_id, keep=8),
            )
            raise HTTPException(status_code=403, detail="stream does not belong to current session")

        response_payload = build_stream_stop_payload(status, handle)
        self._log.info(
            "停止流式请求处理完成",
            path="/chat/stop",
            session_id=summarize_session_id(session_id),
            stream_id=summarize_identifier_for_log(stream_id, keep=8),
            status=status,
            thread_id=summarize_thread_id(handle.thread_id if handle else None),
            reason=reason,
        )
        return response_payload

    async def _load_thread_history_messages(self, request: Request, thread_id: str) -> list[dict[str, Any]]:
        if getattr(request.app.state, "dev_mode", False):
            raw_messages = get_dev_messages(request.app, thread_id)
            sanitized = sanitize_chat_history_messages(raw_messages)
            return sanitized if isinstance(sanitized, list) else []

        try:
            messages = self._conversation_repository(request.app).list_messages(thread_id=thread_id)
            history_payload = messages_to_history_payload(messages)
            sanitized = sanitize_chat_history_messages(history_payload)
            if isinstance(sanitized, list) and sanitized:
                return sanitized
        except Exception as exc:
            self._log.warning(
                "读取 conversation DB 历史失败，回退旧历史来源",
                thread_id=summarize_thread_id(thread_id),
                error=str(exc),
            )

        checkpointer = getattr(request.app.state, "checkpointer", None)
        if not checkpointer:
            return load_artifact_history_messages(thread_id, logger=self._log)
        checkpoint = await checkpointer.aget({"configurable": {"thread_id": thread_id}})
        if not checkpoint or not checkpoint.get("channel_values"):
            return load_artifact_history_messages(thread_id, logger=self._log)
        sanitized = sanitize_chat_history_messages(checkpoint["channel_values"].get("messages", []))
        if isinstance(sanitized, list) and sanitized:
            return sanitized
        return load_artifact_history_messages(thread_id, logger=self._log)

    async def _overwrite_thread_history_state(
        self,
        request: Request,
        thread_id: str,
        kept_messages: list[dict[str, Any]],
    ) -> None:
        if getattr(request.app.state, "dev_mode", False):
            app_state = request.app.state
            app_state.dev_messages[thread_id] = kept_messages
            app_state.dev_todos.pop(thread_id, None)
            app_state.dev_updated_at[thread_id] = datetime.now().isoformat()
            return

        raise RuntimeError("当前单 Agent 运行模式不支持覆盖非 dev 历史状态")


    def _clear_stale_thread_artifact(self, thread_id: str) -> None:
        try:
            from ..diagnosis.artifact_store import clear_thread_artifact

            clear_thread_artifact(thread_id)
        except Exception as artifact_error:
            self._log.warning(
                "清理编辑后的旧对话产物失败",
                thread_id=summarize_thread_id(thread_id),
                error=str(artifact_error),
            )

    def _record_history_thread(
        self,
        app,
        *,
        session_id: str,
        thread_id: str,
        history_type: str,
    ) -> None:
        try:
            repository = getattr(app.state, "history_index_repository", None) or get_history_index_repository()
            repository.record_thread(
                session_id=session_id,
                thread_id=thread_id,
                history_type=history_type,
            )
        except Exception as index_error:
            self._log.warning(
                "登记聊天历史索引失败",
                session_id=summarize_session_id(session_id),
                thread_id=summarize_thread_id(thread_id),
                error=str(index_error),
            )
