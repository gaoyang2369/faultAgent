"""Conversation persistence orchestration around chat streams."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from ..common.utils import summarize_identifier_for_log
from ..context.conversation_context import ConversationContextAssembler
from ..repositories.conversation_store import ConversationRepository


def parse_sse_payloads(chunk: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for block in chunk.split("\n\n"):
        data_lines = [
            line[len("data:"):].strip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        try:
            payloads.append(json.loads("\n".join(data_lines)))
        except json.JSONDecodeError:
            continue
    return payloads


class ConversationPersistenceService:
    """Persist user/assistant messages around a streamed agent turn."""

    def __init__(self, *, repository: ConversationRepository, logger) -> None:
        self.repository = repository
        self._log = logger

    def prepare_turn(
        self,
        context: Any,
        *,
        branch_id: str = "main",
        parent_message_id: str | None = None,
    ) -> None:
        try:
            self.repository.ensure_thread(
                thread_id=context.thread_id,
                session_id=context.session_id,
                owner_user_id=str(context.auth_context.user_id or context.auth_context.role or "anonymous"),
                title=context.message[:40],
                metadata={
                    "channel": context.channel,
                    "auth_role": context.auth_context.role,
                    "auth_method": context.auth_context.auth_method,
                },
            )
            user_message = self.repository.append_message(
                thread_id=context.thread_id,
                role="user",
                content_text=context.message,
                status="accepted",
                request_id=context.request_id,
                stream_id=context.stream_id,
                branch_id=branch_id,
                parent_message_id=parent_message_id,
                auth_scope=context.auth_context.audit_summary(),
                metadata={
                    "channel": context.channel,
                    "session_id": context.session_id,
                    "requested_thread_id": context.requested_thread_id,
                },
            )
            context.user_message_id = str(user_message.get("id") or "")
            assembler = ConversationContextAssembler(conversation_repository=self.repository)
            context.conversation_context = assembler.build(
                thread_id=context.thread_id,
                current_user_message=context.message,
                auth_context=context.auth_context,
            )
            context.metadata["conversation_context_stats"] = (context.conversation_context or {}).get("stats", {})
            context.metadata["user_message_id"] = context.user_message_id
        except Exception as exc:
            self._log.warning(
                "写入用户消息或装配对话上下文失败，继续执行本轮诊断",
                thread_id=summarize_identifier_for_log(context.thread_id, keep=10),
                request_id=context.request_id,
                error=str(exc),
            )

    async def stream_events_with_persistence(
        self,
        context: Any,
        chunks: AsyncGenerator[str, None],
    ) -> AsyncGenerator[str, None]:
        terminal_saved = False
        try:
            async for chunk in chunks:
                for event in parse_sse_payloads(chunk):
                    msg_type = event.get("type")
                    if msg_type == "chat_complete":
                        self._append_assistant_message_from_complete(context, event)
                        terminal_saved = True
                    elif msg_type in {"server_error", "error"}:
                        self._append_assistant_message_from_error(context, event)
                        terminal_saved = True
                yield chunk
        finally:
            if not terminal_saved and getattr(context, "user_message_id", None):
                self._append_assistant_message_from_cancel(context)

    def _append_assistant_message_from_complete(self, context: Any, event: dict[str, Any]) -> None:
        status = "cancelled" if event.get("cancelled") else "completed"
        content = str(
            event.get("final_content")
            or event.get("grounded_final_content")
            or event.get("raw_final_content")
            or ""
        )
        self._append_assistant_message(
            context,
            status=status,
            content=content,
            content_json=_assistant_content_json(event),
            metadata={"terminal_event": "chat_complete"},
            artifact_refs=_artifact_refs_from_complete(event),
        )

    def _append_assistant_message_from_error(self, context: Any, event: dict[str, Any]) -> None:
        content = str(event.get("message") or event.get("error") or "Agent 处理失败")
        self._append_assistant_message(
            context,
            status="failed",
            content=content,
            content_json={"error": event},
            metadata={"terminal_event": str(event.get("type") or "error")},
            artifact_refs=[],
        )

    def _append_assistant_message_from_cancel(self, context: Any) -> None:
        self._append_assistant_message(
            context,
            status="cancelled",
            content="",
            content_json={"cancelled": True},
            metadata={"terminal_event": "stream_closed_without_complete"},
            artifact_refs=[],
        )

    def _append_assistant_message(
        self,
        context: Any,
        *,
        status: str,
        content: str,
        content_json: dict[str, Any],
        metadata: dict[str, Any],
        artifact_refs: list[dict[str, Any]],
    ) -> None:
        try:
            user_messages = [
                item
                for item in self.repository.list_messages(thread_id=context.thread_id, include_superseded=False)
                if item.get("id") == context.user_message_id
            ]
            turn_index = user_messages[0].get("turn_index") if user_messages else None
            assistant_message = self.repository.append_message(
                thread_id=context.thread_id,
                role="assistant",
                content_text=content,
                status=status,
                request_id=context.request_id,
                stream_id=context.stream_id,
                parent_message_id=context.user_message_id,
                turn_index=int(turn_index) if turn_index is not None else None,
                auth_scope=context.auth_context.audit_summary(),
                content_json=content_json,
                metadata={
                    **metadata,
                    "channel": context.channel,
                    "session_id": context.session_id,
                    "conversation_context_stats": (context.conversation_context or {}).get("stats", {}),
                },
            )
            self.repository.link_artifacts(
                thread_id=context.thread_id,
                message_id=str(assistant_message.get("id") or ""),
                artifact_refs=artifact_refs,
            )
        except Exception as exc:
            self._log.warning(
                "写入助手消息失败",
                thread_id=summarize_identifier_for_log(context.thread_id, keep=10),
                request_id=context.request_id,
                status=status,
                error=str(exc),
            )


def _assistant_content_json(event: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
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
        "conversation_context",
    ):
        if key in event:
            payload[key] = event[key]
    return payload


def _artifact_refs_from_complete(event: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    artifact = event.get("artifact") if isinstance(event.get("artifact"), dict) else {}
    if artifact:
        artifact_id = str(artifact.get("created_at") or artifact.get("id") or "").strip()
        if artifact_id:
            refs.append(
                {
                    "artifact_id": artifact_id,
                    "artifact_type": "diagnosis",
                    "artifact_backend": "diagnosis_artifact_store",
                    "ref_role": "produced_by",
                }
            )
    report_id = str(event.get("report_filename") or event.get("report_url") or "").strip()
    if report_id:
        refs.append(
            {
                "artifact_id": report_id,
                "artifact_type": "report",
                "artifact_backend": "diagnosis_artifact_store",
                "ref_role": "produced_by",
            }
        )
    evidence_bundle = event.get("evidence_bundle") if isinstance(event.get("evidence_bundle"), dict) else {}
    bundle_id = str(evidence_bundle.get("bundle_id") or evidence_bundle.get("id") or "").strip()
    if bundle_id:
        refs.append(
            {
                "artifact_id": bundle_id,
                "artifact_type": "evidence_bundle",
                "artifact_backend": "diagnosis_artifact_store",
                "ref_role": "produced_by",
            }
        )
    return refs
