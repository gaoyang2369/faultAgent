"""Production adapter for the single ConversationTurnCoordinator authority."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator

from fault_diagnosis.agent.engine import AgentEngineV2
from fault_diagnosis.agent.planning import PlanPolicyBridge
from fault_diagnosis.agent.skills.router import skill_for_capability
from fault_diagnosis.agent.runtime.plan_preparer import prepare_v2_execution_plan
from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator
from fault_diagnosis.domain.canonical_turn import TurnCommand, TurnEvent
from fault_diagnosis.domain.context import ArtifactBackedCaseStore
from fault_diagnosis.domain.context.conversation_context import ConversationContextAssembler
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import (
    get_artifact_manifest_exact,
    get_thread_artifact,
    list_thread_artifacts,
)
from fault_diagnosis.platform.persistence.repositories.conversation_store import get_conversation_repository
from fault_diagnosis.platform.persistence.repositories.pending_clarification_repository import (
    MemoryPendingClarificationRepository,
    SQLitePendingClarificationRepository,
)
from fault_diagnosis.server.use_cases.conversation_persistence import (
    ConversationPersistenceService,
    parse_sse_payloads,
)


class ProductionTurnCoordinator:
    """Bind HTTP-neutral turn context to canonical planning, runtime and persistence."""

    def __init__(self, *, app, stream_events, logger) -> None:
        self.app = app
        self.stream_events = stream_events
        self.logger = logger
        self.repository = getattr(app.state, "conversation_repository", None) or get_conversation_repository()
        self.persistence = ConversationPersistenceService(repository=self.repository, logger=logger)
        pending_repository = MemoryPendingClarificationRepository()
        conversation_path = getattr(self.repository, "path", None)
        if conversation_path is not None and str(conversation_path) != ":memory:":
            pending_repository = SQLitePendingClarificationRepository(
                path=Path(conversation_path).with_name("pending_clarifications.sqlite3")
            )
        self.coordinator = ConversationTurnCoordinator(pending_repository=pending_repository)
        self._stream_replays: dict[tuple[str, str, str], list[str]] = {}

    def build_read_only_context(self, context) -> dict[str, Any] | None:
        try:
            return ConversationContextAssembler(
                conversation_repository=self.repository,
                case_store=ArtifactBackedCaseStore(
                    artifact_lister=lambda thread_id, limit: list_thread_artifacts(thread_id, limit=limit)
                ),
                artifact_getter=get_thread_artifact,
                artifact_manifest_getter=get_artifact_manifest_exact,
            ).build(
                thread_id=context.thread_id,
                current_user_message=context.message,
                auth_context=context.auth_context,
            )
        except Exception as exc:
            self.logger.warning("构造 canonical 只读对话上下文失败", thread_id=context.thread_id, error=str(exc))
            return None

    def preview_turn(self, context):
        conversation_context = self.build_read_only_context(context)
        command = self._command(context, command="preview")
        canonical = self.coordinator.preview_turn(
            command,
            auth_context=context.auth_context,
            conversation_context=conversation_context,
        )
        snapshot = AgentEngineV2().build_plan_snapshot(
            raw_message=context.message,
            thread_id=context.thread_id,
            request_id=context.request_id,
            auth_context=context.auth_context,
            conversation_context=conversation_context,
            metadata={"source": "chat_plan"},
            canonical_result=canonical,
        )
        plan = prepare_v2_execution_plan(snapshot=snapshot, thread_id=context.thread_id, auth_context=context.auth_context)
        return canonical, snapshot, plan

    async def execute_turn(
        self,
        context,
        *,
        cancel_handle=None,
        history_messages=None,
        replace_history: bool = False,
        complete_payload_enricher=None,
        model_name: str | None = None,
        branch_id: str = "main",
        parent_message_id: str | None = None,
    ) -> list[str]:
        key = self._replay_key(context)
        if key in self._stream_replays:
            return list(self._stream_replays[key])
        durable_replay = self._load_durable_replay(context)
        if durable_replay:
            self._stream_replays[key] = list(durable_replay)
            return durable_replay
        conversation_context = self.build_read_only_context(context)
        context.conversation_context = conversation_context
        self.persistence.prepare_turn(context, branch_id=branch_id, parent_message_id=parent_message_id)
        command = self._command(context, command="execute")

        async def run(preview):
            snapshot = AgentEngineV2().build_plan_snapshot(
                raw_message=context.message,
                thread_id=context.thread_id,
                request_id=context.request_id,
                auth_context=context.auth_context,
                conversation_context=context.conversation_context,
                metadata={"stream_id": context.stream_id, "source": "canonical_execute", "model": model_name or ""},
                canonical_result=preview,
            )
            plan = prepare_v2_execution_plan(snapshot=snapshot, thread_id=context.thread_id, auth_context=context.auth_context)
            runtime_events: list[TurnEvent] = []
            complete: dict[str, Any] = {}
            terminal = "failed"
            source = self.stream_events(
                self.app,
                context.message,
                context.thread_id,
                context.trusted_user_identity,
                request_id=context.request_id,
                stream_id=context.stream_id,
                cancel_handle=cancel_handle,
                history_messages=history_messages,
                replace_history=replace_history,
                auth_context=context.auth_context,
                model_name=model_name,
                conversation_context=context.conversation_context,
                complete_payload_enricher=complete_payload_enricher,
                canonical_snapshot=snapshot,
                canonical_plan=plan,
            )
            async for chunk in self.persistence.stream_events_with_persistence(context, source):
                payloads = parse_sse_payloads(chunk)
                event_types: list[str] = []
                for payload in payloads:
                    event_type = str(payload.get("type") or payload.get("event_type") or "runtime_event")
                    event_types.append(event_type)
                    if event_type == "chat_complete":
                        complete = payload
                        terminal = "cancelled" if payload.get("cancelled") else str(payload.get("status") or "completed")
                    elif event_type in {"server_error", "error"}:
                        terminal = "failed"
                runtime_events.append(
                    TurnEvent(
                        event_type=event_types[0] if len(event_types) == 1 else "runtime_event_batch",
                        sequence=len(preview.events) + len(runtime_events),
                        detail={"payloads": payloads, "serialized_sse": chunk},
                    )
                )
            return {
                "plan_snapshot": snapshot,
                "complete_payload": complete,
                "events": runtime_events,
                "execution_performed": bool(plan.nodes),
                "terminal_status": terminal,
            }

        result = await self.coordinator.execute_turn(
            command,
            auth_context=context.auth_context,
            conversation_context=context.conversation_context,
            executor=run,
        )
        chunks = [
            str(event.detail["serialized_sse"])
            for event in result.events
            if event.detail.get("serialized_sse")
        ]
        self._stream_replays[key] = list(chunks)
        return chunks

    def _load_durable_replay(self, context) -> list[str]:
        """Rebuild a terminal SSE event without rerunning tools after process restart."""

        idempotency_key = str(context.metadata.get("idempotency_key") or context.request_id)
        try:
            messages = self.repository.list_messages(
                thread_id=context.thread_id,
                include_superseded=False,
            )
        except Exception:
            return []
        for message in reversed(messages):
            if message.get("role") != "assistant" or message.get("status") not in {"completed", "failed", "cancelled"}:
                continue
            metadata = dict(message.get("metadata") or {})
            same_key = str(metadata.get("idempotency_key") or "") == idempotency_key
            same_request = str(message.get("request_id") or "") == str(context.request_id)
            if not same_key and not same_request:
                continue
            payload = {
                **dict(message.get("content_json") or {}),
                "type": "chat_complete",
                "status": str(message.get("status") or "completed"),
                "final_content": str(message.get("content_text") or ""),
                "thread_id": context.thread_id,
                "request_id": context.request_id,
                "replayed": True,
            }
            return [f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"]
        interrupted = next(
            (
                message
                for message in reversed(messages)
                if message.get("role") == "user"
                and message.get("status") == "accepted"
                and str((message.get("metadata") or {}).get("idempotency_key") or "") == idempotency_key
            ),
            None,
        )
        if interrupted is not None:
            self.coordinator.finalize_interrupted_turn(
                idempotency_key=idempotency_key,
                turn_id=context.request_id,
                message_id=str(interrupted.get("id") or context.request_id),
            )
            payload = {
                "type": "chat_complete",
                "status": "failed",
                "final_content": "上次同一请求在执行中中断，已阻止重复执行；请发起新的请求。",
                "thread_id": context.thread_id,
                "request_id": context.request_id,
                "replayed": True,
                "recovery_required": True,
            }
            return [f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"]
        return []

    async def stream_turn(self, context, **kwargs) -> AsyncGenerator[str, None]:
        for chunk in await self.execute_turn(context, **kwargs):
            yield chunk

    def _command(self, context, *, command: str) -> TurnCommand:
        idempotency_key = str(context.metadata.get("idempotency_key") or context.request_id)
        message_id = str(context.user_message_id or context.request_id)
        candidate_devices = list(dict.fromkeys(
            str(device)
            for manifest in ((context.conversation_context or {}).get("artifact_manifests") or [])
            if isinstance(manifest, dict)
            for device in (manifest.get("device_refs") or [])
            if str(device)
        ))
        return TurnCommand(
            command=command,
            thread_id=context.thread_id,
            user_id=context.auth_context.user_id,
            turn_id=context.request_id,
            message_id=message_id,
            idempotency_key=idempotency_key,
            raw_message=context.message,
            candidate_values={"device": candidate_devices} if candidate_devices else {},
            channel=context.channel,
            metadata=dict(context.metadata),
        )

    @staticmethod
    def _replay_key(context) -> tuple[str, str, str]:
        return (
            context.thread_id,
            str(context.metadata.get("message_id") or context.message),
            str(context.metadata.get("idempotency_key") or context.request_id),
        )


def get_production_turn_coordinator(app, *, stream_events, logger) -> ProductionTurnCoordinator:
    existing = getattr(app.state, "conversation_turn_coordinator", None)
    if existing is None or existing.stream_events is not stream_events:
        existing = ProductionTurnCoordinator(app=app, stream_events=stream_events, logger=logger)
        app.state.conversation_turn_coordinator = existing
    return existing


def plan_compat_payload(*, snapshot: Any, plan: Any) -> dict[str, Any]:
    """Deprecated plan fields kept under compatibility_debug only."""

    bridge = PlanPolicyBridge()
    selected_skill = (
        snapshot.skill_route.selected_skills[0]
        if snapshot.skill_route.selected_skills
        else skill_for_capability(str(plan.goals[0].capability))
        if plan.goals
        else "clarification"
    )
    policy_id = bridge.policy_id_for_skill(selected_skill)
    task_family = "composite" if snapshot.execution_plan.execution_capability == "composite" else bridge.task_family_for_skill(selected_skill)
    enabled_nodes = bridge.legacy_enabled_nodes(plan)
    skipped = {node: "not_planned_by_canonical_goal" for node in ("analysis", "sql", "knowledge", "report", "workorder_decision") if not enabled_nodes.get(node)}
    authorization = dict((snapshot.output_frame.guardrail_result or {}).get("authorization") or {})
    resolved_context = snapshot.context_frame.model_dump(mode="json", exclude_none=True)
    return {
        "compatibility_only": True,
        "schema_version": snapshot.schema_version,
        "engine_version": "v2",
        "task_family": task_family,
        "policy_id": policy_id,
        "plan_mode": "canonical_turn",
        "context_relation": snapshot.context_frame.relation_to_previous,
        "resolved_context": resolved_context,
        "canonical_request": snapshot.metadata["canonical_request"],
        "goal_authorization": snapshot.metadata["goal_authorization"],
        "goal_readiness": snapshot.metadata["goal_readiness"],
        "goal_source_resolution": snapshot.metadata["goal_source_resolution"],
        "goal_set": {"primary_goal_id": str(plan.goals[0].goal_id if plan.goals else ""), "goals": [goal.model_dump(mode="json") for goal in plan.goals], "goal_types": [goal.goal_type for goal in plan.goals], "expected_outputs": list(plan.expected_outputs)},
        "goals": [goal.model_dump(mode="json") for goal in plan.goals],
        "workflow_route": {"task_family": task_family, "policy_id": policy_id, "risk_level": plan.risk_level, "required_evidence": list(plan.required_evidence)},
        "workflow_policy": {"policy_id": policy_id, "enabled_nodes": dict(enabled_nodes), "allowed_tools": bridge.v2_to_legacy_tools(list(plan.allowed_tools)), "forbidden_tools": bridge.v2_to_legacy_tools(list(plan.forbidden_tools))},
        "enabled_nodes": dict(enabled_nodes),
        "skipped_nodes": skipped,
        "skip_reasons": skipped,
        "planned_tools": bridge.v2_to_legacy_tools(list(plan.allowed_tools)),
        "forbidden_tools": bridge.v2_to_legacy_tools(list(plan.forbidden_tools)),
        "missing_slots": [slot for item in snapshot.metadata["canonical_request"]["goals"] for slot in item.get("missing_slots", [])],
        "evidence_gaps": {"required_evidence": list(plan.required_evidence), "missing_or_stale_evidence": []},
        "readiness": {item["goal_id"]: item for item in snapshot.metadata["goal_readiness"]},
        "manual_confirmation": {},
        "authorization": authorization,
    }


def build_plan_preview_payload(*, snapshot: Any, plan: Any) -> dict[str, Any]:
    """Serialize the read-only canonical preview with one explicit debug boundary."""

    canonical_request = dict(snapshot.metadata["canonical_request"])
    artifact_bindings = [
        dict(binding)
        for node in plan.nodes
        for binding in (node.inputs.get("artifact_role_bindings") or [])
        if isinstance(binding, dict)
    ]
    compatibility = {
        **plan_compat_payload(snapshot=snapshot, plan=plan),
        "intent_frame": snapshot.intent_frame.model_dump(mode="json", exclude_none=True),
        "rewrite_frame": snapshot.rewrite_frame.model_dump(mode="json", exclude_none=True),
        "context_frame": snapshot.context_frame.model_dump(mode="json", exclude_none=True),
        "effective_request_frame": snapshot.effective_request_frame.model_dump(mode="json", exclude_none=True),
        "skill_route": snapshot.skill_route.model_dump(mode="json", exclude_none=True),
    }
    return {
        "schema_version": "canonical_plan_preview.v1",
        "engine_version": "v2",
        "status": snapshot.status,
        "plan_mode": "canonical_turn",
        "canonical_request": canonical_request,
        "goals": list(canonical_request.get("goals") or []),
        "goal_authorization": list(snapshot.metadata["goal_authorization"]),
        "goal_readiness": list(snapshot.metadata["goal_readiness"]),
        "goal_source_resolution": list(snapshot.metadata["goal_source_resolution"]),
        "pending_transition": dict(snapshot.metadata["pending_transition"]),
        "execution_plan": plan.model_dump(mode="json", exclude_none=True),
        "artifact_role_bindings": artifact_bindings,
        "compatibility_debug": compatibility,
    }


def build_collect_compat_plan(
    *,
    message: str,
    thread_id: str,
    request_id: str,
    auth_context,
    conversation_context: dict[str, Any] | None,
):
    """Route direct collect callers through canonical preview, never a legacy engine path."""

    coordinator = ConversationTurnCoordinator()
    preview = coordinator.preview_turn(
        TurnCommand(
            command="preview",
            thread_id=thread_id,
            user_id=auth_context.user_id,
            turn_id=request_id,
            message_id=request_id,
            idempotency_key=request_id,
            raw_message=message,
            channel="collect",
        ),
        auth_context=auth_context,
        conversation_context=conversation_context,
    )
    snapshot = AgentEngineV2().build_plan_snapshot(
        raw_message=message,
        thread_id=thread_id,
        request_id=request_id,
        auth_context=auth_context,
        conversation_context=conversation_context,
        metadata={"source": "canonical_collect"},
        canonical_result=preview,
    )
    plan = prepare_v2_execution_plan(snapshot=snapshot, thread_id=thread_id, auth_context=auth_context)
    return snapshot, plan
