"""Canonical production-turn orchestration and side-effect-free preview."""

from __future__ import annotations

import hashlib
import inspect
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fault_diagnosis.agent.canonical_turn.parser import CurrentUtteranceParser
from fault_diagnosis.agent.semantics import SemanticResolutionService
from fault_diagnosis.agent.canonical_turn.context_binding import (
    CanonicalContextBinder,
    project_authorized_context_candidates,
)
from fault_diagnosis.agent.semantics.context_interpreter import project_pending_semantic_summary
from fault_diagnosis.domain.canonical_turn import (
    CanonicalGoal,
    CanonicalTurnRequest,
    GoalExecutionCondition,
    GoalExecutionStatus,
    PendingBinding,
    PendingClarification,
    PendingTransitionProposal,
    TurnCommand,
    TurnEvent,
    TurnResult,
    capability_spec,
)
from fault_diagnosis.agent.canonical_turn.decisions import (
    decide_goal_authorization,
    decide_goal_readiness,
    resolve_goal_sources,
)
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.domain.canonical_turn.contracts import GoalProvenance
from fault_diagnosis.platform.persistence.repositories.pending_clarification_repository import (
    DEFAULT_PENDING_TTL,
    MemoryPendingClarificationRepository,
    PendingClarificationRepository,
)
from fault_diagnosis.platform import settings


_ENTITY_KIND_BY_SLOT = {
    "device": "device_reference",
    "fault_code": "fault_code",
    "time_window": "time_window",
}


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


class ConversationTurnCoordinator:
    """Own request construction, pending CAS and idempotent turn execution."""

    def __init__(
        self,
        *,
        parser: CurrentUtteranceParser | None = None,
        pending_repository: PendingClarificationRepository | None = None,
        semantic_service: SemanticResolutionService | None = None,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._parser = parser or CurrentUtteranceParser()
        self._semantic_service = semantic_service or SemanticResolutionService(parser=self._parser)
        self._pending_repository = pending_repository or MemoryPendingClarificationRepository(clock=clock)
        self._clock = clock
        self._completed: dict[tuple[str, str, str, str, str], TurnResult] = {}
        self._execution_lock = asyncio.Lock()

    def preview(self, command: TurnCommand) -> TurnResult:
        """Deprecated compatibility alias for :meth:`preview_turn`."""

        return self.preview_turn(command)

    def preview_turn(
        self,
        command: TurnCommand,
        *,
        auth_context=None,
        conversation_context: dict[str, Any] | None = None,
    ) -> TurnResult:
        """Build the exact production request and decisions without repository writes."""

        parsed = self._parser.parse(command.raw_message)
        return self._preview_turn_from_parsed(
            command,
            parsed,
            auth_context=auth_context,
            conversation_context=conversation_context,
        )

    async def preview_turn_async(
        self,
        command: TurnCommand,
        *,
        auth_context=None,
        conversation_context: dict[str, Any] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> TurnResult:
        """生产预览主入口：每轮至多产生一次可取消的语义调用。"""

        auth = auth_context or build_auth_context(user_id=command.user_id, role="guest")
        candidates = project_authorized_context_candidates(conversation_context, auth)
        waiting = self._pending_repository.peek_waiting(
            command.thread_id, command.user_id, now=self._clock(),
        )
        semantic = await self._semantic_service.resolve(
            command.raw_message,
            cancel_event=cancel_event,
            context_candidates=candidates,
            pending_summary=project_pending_semantic_summary(waiting),
        )
        semantic_trace = semantic.trace.model_dump(mode="json", by_alias=True, exclude_none=True)
        semantic_trace["field_decisions"] = [item.model_dump(mode="json") for item in semantic.field_decisions]
        if semantic.context_proposal:
            semantic_trace["context_constraints"] = semantic.context_proposal.model_dump(mode="json")
        return self._preview_turn_from_parsed(
            command,
            semantic.parsed,
            auth_context=auth,
            conversation_context=conversation_context,
            semantic_trace=semantic_trace,
            candidates=candidates,
            context_proposal=semantic.context_proposal,
            context_clarification_reason=semantic.context_clarification_reason,
        )

    def _preview_turn_from_parsed(
        self,
        command: TurnCommand,
        parsed,
        *,
        auth_context=None,
        conversation_context: dict[str, Any] | None = None,
        semantic_trace: dict[str, Any] | None = None,
        candidates=None,
        context_proposal=None,
        context_clarification_reason: str | None = None,
    ) -> TurnResult:
        """由已经解析的当前消息构造唯一 Canonical 请求。"""

        current_goals = self._build_current_goals(command, parsed)
        waiting = self._pending_repository.peek_waiting(
            command.thread_id,
            command.user_id,
            now=self._clock(),
        )
        goals, binding = self._bind_pending(waiting, current_goals, parsed)
        request = CanonicalTurnRequest(
            request_id=_stable_id("canonical_request", command.thread_id, command.turn_id, command.message_id),
            thread_id=command.thread_id,
            user_id=command.user_id,
            turn_id=command.turn_id,
            message_id=command.message_id,
            idempotency_key=command.idempotency_key,
            raw_message=command.raw_message,
            current_parse=parsed,
            goals=goals,
            pending_binding=binding,
            authorization_required=True,
            authorization_result=None,
        )
        auth = auth_context or build_auth_context(user_id=command.user_id, role="guest")
        candidates = candidates if candidates is not None else project_authorized_context_candidates(conversation_context, auth)
        bound_turn = CanonicalContextBinder().bind(
            request, candidates, context_proposal=context_proposal,
            context_clarification_reason=context_clarification_reason,
        )
        transition = self._transition_proposal(command, waiting, goals, binding, bound_turn.goal_bindings)
        events = [
            TurnEvent(
                event_type="utterance_parsed",
                sequence=0,
                detail={
                    "clause_count": len(parsed.clauses),
                    "entity_count": len(parsed.entities),
                    "clause_semantics": [_clause_semantics(item) for item in parsed.clauses],
                },
            ),
            TurnEvent(
                event_type="intent_resolution",
                sequence=1,
                detail={
                    "mode": parsed.intent_resolution.mode,
                    "model_status": parsed.intent_resolution.model_status,
                    "accepted_fields": parsed.intent_resolution.accepted_model_fields,
                    "rejected_fields": parsed.intent_resolution.rejected_model_fields,
                    "duration_ms": parsed.intent_resolution.duration_ms,
                    "semantic": semantic_trace or _legacy_semantic_trace(parsed),
                },
            ),
            TurnEvent(
                event_type="pending_loaded",
                sequence=2,
                detail={"pending_id": waiting.pending_id if waiting else None},
            ),
            TurnEvent(
                event_type="pending_bound",
                sequence=3,
                detail={"kind": binding.kind, "consumes_pending": binding.consumes_pending},
            ),
            TurnEvent(
                event_type="request_built",
                sequence=4,
                detail={
                    "goal_ids": [goal.goal_id for goal in goals],
                    "execution_performed": False,
                    "excluded_clauses": [
                        {
                            "clause_index": item.clause_index,
                            "reason": "negated_action" if item.modality.negated else "not_requested",
                        }
                        for item in parsed.clauses
                        if item.action and (item.modality.negated or not item.modality.requested)
                    ],
                },
            ),
            TurnEvent(
                event_type="context_binding",
                sequence=5,
                detail=_binding_trace(bound_turn, candidates),
            ),
        ]
        authorization = decide_goal_authorization(bound_turn, auth)
        source_resolutions = resolve_goal_sources(bound_turn, candidates)
        readiness = decide_goal_readiness(bound_turn, authorization, source_resolutions)
        return TurnResult(
            request=request,
            bound_turn=bound_turn,
            events=events,
            pending_transition=transition,
            authorization=authorization,
            readiness=readiness,
            source_resolutions=source_resolutions,
            goal_statuses=[
                GoalExecutionStatus(
                    goal_id=goal.goal_id,
                    status=(
                        "denied"
                        if next(item for item in authorization if item.goal_id == goal.goal_id).status == "denied"
                        else "satisfied"
                        if next(item for item in readiness if item.goal_id == goal.goal_id).status == "satisfied_by_artifact"
                        else "blocked"
                        if next(item for item in readiness if item.goal_id == goal.goal_id).status.startswith("blocked_")
                        else "pending"
                    ),
                )
                for goal in request.goals
            ],
            execution_performed=False,
        )

    async def execute_turn(
        self,
        command: TurnCommand,
        *,
        auth_context,
        conversation_context: dict[str, Any] | None = None,
        cancel_event: asyncio.Event | None = None,
        executor=None,
    ) -> TurnResult:
        """Execute one idempotent turn through an injected existing runtime executor.

        The coordinator owns pending transitions.  The injected callable owns only
        plan/runtime work and receives the already-decided preview result.
        """

        key = (
            command.thread_id,
            command.user_id,
            command.turn_id,
            command.message_id,
            command.idempotency_key,
        )
        async with self._execution_lock:
            replay = self._completed.get(key)
            if replay is not None:
                return replay.model_copy(deep=True)
            self._pending_repository.get_waiting(
                command.thread_id,
                command.user_id,
                now=self._clock(),
            )
            preview = await self.preview_turn_async(
                command,
                auth_context=auth_context,
                conversation_context=conversation_context,
                cancel_event=cancel_event,
            )
            transition = preview.pending_transition
            resumed = None
            if transition.action == "create_waiting" and transition.proposed_pending is not None:
                pending = transition.proposed_pending
                self._pending_repository.create_waiting(
                    pending_id=pending.pending_id,
                    thread_id=pending.thread_id,
                    user_id=pending.user_id,
                    created_turn_id=pending.created_turn_id,
                    created_message_id=pending.created_message_id,
                    idempotency_key=pending.idempotency_key,
                    original_goals=pending.original_goals,
                    missing_slots=pending.missing_slots,
                    candidate_values=pending.candidate_values,
                    source_bindings=pending.source_bindings,
                    historical_authorization_audit=pending.historical_authorization_audit,
                    now=self._clock(),
                )
            elif transition.action == "resume_and_consume" and transition.pending_id:
                resumed = self._pending_repository.mark_resumed(
                    transition.pending_id,
                    thread_id=command.thread_id,
                    user_id=command.user_id,
                    expected_version=int(transition.expected_version or 1),
                    turn_id=command.turn_id,
                    message_id=command.message_id,
                    idempotency_key=transition.transition_idempotency_keys[0],
                    now=self._clock(),
                )

            try:
                outcome = executor(preview) if executor is not None else {}
                if inspect.isawaitable(outcome):
                    outcome = await outcome
            except Exception as exc:
                failed = preview.model_copy(
                    update={
                        "events": [
                            *preview.events,
                            TurnEvent(
                                event_type="turn_failed",
                                sequence=len(preview.events),
                                detail={"error_type": type(exc).__name__},
                            ),
                        ],
                        "execution_performed": False,
                        "terminal_status": "failed",
                        "goal_statuses": _terminal_goal_statuses(preview, None, "failed"),
                    },
                    deep=True,
                )
                if resumed is not None:
                    self._pending_repository.mark_consumed(
                        resumed.pending_id,
                        thread_id=command.thread_id,
                        user_id=command.user_id,
                        expected_version=resumed.version,
                        turn_id=command.turn_id,
                        message_id=command.message_id,
                        idempotency_key=transition.transition_idempotency_keys[1],
                        now=self._clock(),
                    )
                self._completed[key] = failed.model_copy(deep=True)
                raise
            if outcome is None:
                outcome = {}
            snapshot = outcome.get("plan_snapshot") if isinstance(outcome, dict) else outcome
            payload = dict(outcome.get("complete_payload") or {}) if isinstance(outcome, dict) else {}
            extra_events = list(outcome.get("events") or []) if isinstance(outcome, dict) else []
            terminal = str(outcome.get("terminal_status") or "completed") if isinstance(outcome, dict) else "completed"
            performed = bool(outcome.get("execution_performed", True)) if isinstance(outcome, dict) else True
            if resumed is not None and terminal in {"completed", "blocked", "failed", "cancelled"}:
                self._pending_repository.mark_consumed(
                    resumed.pending_id,
                    thread_id=command.thread_id,
                    user_id=command.user_id,
                    expected_version=resumed.version,
                    turn_id=command.turn_id,
                    message_id=command.message_id,
                    idempotency_key=transition.transition_idempotency_keys[1],
                    now=self._clock(),
                )
            result = preview.model_copy(
                update={
                    "events": [*preview.events, *extra_events],
                    "execution_performed": performed,
                    "terminal_status": terminal,
                    "plan_snapshot": snapshot,
                    "complete_payload": payload,
                    "goal_statuses": _terminal_goal_statuses(preview, snapshot, terminal),
                },
                deep=True,
            )
            self._completed[key] = result.model_copy(deep=True)
            return result

    def finalize_interrupted_turn(
        self,
        *,
        idempotency_key: str,
        turn_id: str,
        message_id: str,
    ) -> bool:
        """Terminally close a durable resumed pending without rerunning its executor."""

        resumed = self._pending_repository.get_by_idempotency_key(f"{idempotency_key}:resume")
        if resumed is None or resumed.status != "resumed":
            return False
        self._pending_repository.mark_consumed(
            resumed.pending_id,
            thread_id=resumed.thread_id,
            user_id=resumed.user_id,
            expected_version=resumed.version,
            turn_id=turn_id,
            message_id=message_id,
            idempotency_key=f"{idempotency_key}:consume",
            now=self._clock(),
        )
        return True

    def _build_current_goals(self, command: TurnCommand, parsed) -> list[CanonicalGoal]:
        entity_by_id = {entity.entity_id: entity for entity in parsed.entities}
        goals: list[CanonicalGoal] = []
        carried_source_refs: list[str] = []
        carried_slot_values: dict[str, Any] = {}
        for clause in parsed.clauses:
            for slot, kind in _ENTITY_KIND_BY_SLOT.items():
                observed = [
                    entity.value
                    for entity in parsed.entities
                    if entity.kind == kind and clause.start <= entity.start and entity.end <= clause.end
                ]
                if observed:
                    unique = list(dict.fromkeys(observed))
                    if slot == "device" and any(
                        entity.kind == "correction_reference"
                        and clause.start <= entity.start < clause.end
                        for entity in parsed.entities
                    ):
                        # A correction is a deterministic safety signal: only
                        # the replacement target can be inherited downstream.
                        unique = [unique[-1]]
                    if slot == "device" and clause.action and clause.action.capability == "compare_runtime_status" and slot in carried_slot_values:
                        previous = carried_slot_values[slot]
                        unique = list(dict.fromkeys([*(previous if isinstance(previous, list) else [previous]), *unique]))
                    carried_slot_values[slot] = unique[0] if len(unique) == 1 else unique
            if clause.source:
                explicit_source_refs = [
                    ref for ref in clause.source.entity_refs if ref in entity_by_id
                    and entity_by_id[ref].kind in {"artifact_reference", "source_reference"}
                ]
                if explicit_source_refs:
                    carried_source_refs = explicit_source_refs
            if clause.action is None:
                continue
            if not clause.modality.requested or clause.modality.negated:
                continue
            spec = capability_spec(clause.action.capability)
            required_slots = list(spec.required_slots) if spec is not None else []
            resolved_slots: dict[str, Any] = {}
            for slot, kind in _ENTITY_KIND_BY_SLOT.items():
                values = list(
                    dict.fromkeys(
                        entity_by_id[ref].value
                        for ref in clause.action.entity_refs
                        if ref in entity_by_id and entity_by_id[ref].kind == kind
                    )
                )
                if values:
                    if slot == "device" and any(
                        entity.kind == "correction_reference"
                        and clause.start <= entity.start < clause.end
                        for entity in parsed.entities
                    ):
                        values = [values[-1]]
                    if slot == "device" and clause.action.capability == "compare_runtime_status" and slot in carried_slot_values:
                        previous = carried_slot_values[slot]
                        values = list(dict.fromkeys([*(previous if isinstance(previous, list) else [previous]), *values]))
                    resolved_slots[slot] = values[0] if len(values) == 1 else values
                    carried_slot_values[slot] = resolved_slots[slot]
                elif slot in required_slots and slot in carried_slot_values:
                    resolved_slots[slot] = carried_slot_values[slot]
            missing_slots = [slot for slot in required_slots if slot not in resolved_slots]
            local_source_refs = [
                ref for ref in (clause.source.entity_refs if clause.source else []) if ref in entity_by_id
                and entity_by_id[ref].kind in {"artifact_reference", "source_reference"}
            ]
            source_refs = local_source_refs or carried_source_refs or list(clause.source.entity_refs if clause.source else [])
            source_requirements = [entity_by_id[ref].value for ref in source_refs if ref in entity_by_id]
            goal_id = _stable_id(
                "goal",
                command.thread_id,
                command.turn_id,
                command.message_id,
                str(clause.clause_index),
                clause.action.capability,
            )
            goals.append(
                CanonicalGoal(
                    goal_id=goal_id,
                    capability=clause.action.capability,
                    origin="inferred" if clause.action.inferred else "explicit",
                    user_requested=True,
                    user_visible=True,
                    clause_index=clause.clause_index,
                    required_slots=required_slots,
                    resolved_slots=resolved_slots,
                    missing_slots=missing_slots,
                    source_requirements=source_requirements,
                    dependencies=[],
                    provenance=GoalProvenance(
                        parser_source=clause.parser_source,
                        utterance_span=(clause.start, clause.end),
                        entity_refs=list(clause.action.entity_refs),
                    ),
                )
            )
        return _attach_goal_dependencies(goals, parsed.clauses)

    def _bind_pending(
        self,
        pending: PendingClarification | None,
        current_goals: list[CanonicalGoal],
        parsed,
    ) -> tuple[list[CanonicalGoal], PendingBinding]:
        if pending is None:
            return current_goals, PendingBinding(
                kind="new_action" if current_goals else "no_match",
                appended_goal_ids=[goal.goal_id for goal in current_goals],
                reason="no waiting pending clarification in this thread/user scope",
            )

        resolved = self._resolve_pending_slots(pending, parsed)
        slot_attempt = any(
            entity.kind in set(_ENTITY_KIND_BY_SLOT.values()) | {"correction_reference", "deictic_reference"}
            for entity in parsed.entities
        )
        if resolved:
            restored = [self._restore_goal(goal, pending, resolved) for goal in pending.original_goals]
            complete = all(not goal.missing_slots for goal in restored)
            kind = "mixed" if current_goals else "slot_only"
            goals = [*restored, *current_goals]
            return goals, PendingBinding(
                kind=kind,
                pending_id=pending.pending_id,
                resolved_slots=resolved,
                restored_goal_ids=[goal.goal_id for goal in restored],
                appended_goal_ids=[goal.goal_id for goal in current_goals],
                consumes_pending=complete,
                reason="pending slots matched current deterministic entities",
            )
        if current_goals:
            return current_goals, PendingBinding(
                kind="new_action",
                pending_id=pending.pending_id,
                appended_goal_ids=[goal.goal_id for goal in current_goals],
                consumes_pending=False,
                reason="explicit current action does not bind the waiting pending slots",
            )
        return [], PendingBinding(
            kind="no_match" if slot_attempt else "unrelated",
            pending_id=pending.pending_id,
            consumes_pending=False,
            reason="current message does not resolve a waiting slot",
        )

    @staticmethod
    def _resolve_pending_slots(pending: PendingClarification, parsed) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for slot in pending.missing_slots:
            kind = _ENTITY_KIND_BY_SLOT.get(slot)
            observed = [entity.value for entity in parsed.entities if entity.kind == kind]
            if not observed:
                continue
            candidates = pending.candidate_values.get(slot, [])
            if not candidates:
                resolved[slot] = observed[0] if len(observed) == 1 else observed
                continue
            matches: list[str] = []
            for value in observed:
                normalized = value.casefold().replace(" ", "")
                compatible = [
                    candidate
                    for candidate in candidates
                    if candidate.casefold().replace(" ", "") == normalized
                    or candidate.casefold().replace(" ", "").endswith(normalized)
                ]
                if len(compatible) == 1:
                    matches.append(compatible[0])
            matches = list(dict.fromkeys(matches))
            if len(matches) == 1:
                resolved[slot] = matches[0]
        return resolved

    @staticmethod
    def _restore_goal(
        goal: CanonicalGoal,
        pending: PendingClarification,
        resolved: dict[str, Any],
    ) -> CanonicalGoal:
        restored_slots = {**goal.resolved_slots}
        for slot in goal.missing_slots:
            if slot in resolved:
                restored_slots[slot] = resolved[slot]
        missing = [slot for slot in goal.required_slots if slot not in restored_slots]
        return goal.model_copy(
            update={
                "resolved_slots": restored_slots,
                "missing_slots": missing,
                "provenance": GoalProvenance(
                    parser_source="pending",
                    pending_id=pending.pending_id,
                    original_goal_id=goal.goal_id,
                    entity_refs=goal.provenance.entity_refs,
                    utterance_span=goal.provenance.utterance_span,
                ),
            },
            deep=True,
        )

    def _transition_proposal(
        self,
        command: TurnCommand,
        waiting: PendingClarification | None,
        goals: list[CanonicalGoal],
        binding: PendingBinding,
        goal_bindings=None,
    ) -> PendingTransitionProposal:
        if waiting is not None and binding.consumes_pending:
            return PendingTransitionProposal(
                action="resume_and_consume",
                pending_id=waiting.pending_id,
                expected_status="waiting",
                expected_version=waiting.version,
                transition_idempotency_keys=[
                    f"{command.idempotency_key}:resume",
                    f"{command.idempotency_key}:consume",
                ],
            )
        if waiting is not None and binding.resolved_slots:
            return PendingTransitionProposal(
                action="keep_waiting",
                pending_id=waiting.pending_id,
                expected_status="waiting",
                expected_version=waiting.version,
            )
        if waiting is not None:
            return PendingTransitionProposal(action="none", pending_id=waiting.pending_id)

        binding_by_goal = {item.goal_id: item for item in (goal_bindings or [])}
        missing_goals: list[CanonicalGoal] = []
        for goal in goals:
            decision = binding_by_goal.get(goal.goal_id)
            if decision is None and goal.missing_slots:
                missing_goals.append(goal)
            elif (
                decision is not None
                and decision.binding_status == "needs_clarification"
                and decision.clarification is not None
                and decision.clarification.reason_code in {"ambiguous_asset_reference", "missing_asset"}
            ):
                missing_goals.append(goal)
        if not missing_goals:
            return PendingTransitionProposal(action="none")
        missing_slots = list(dict.fromkeys(
            slot for goal in missing_goals for slot in (
                goal.missing_slots or ["device"]
            )
        ))
        safe_candidates = {
            "device": list(dict.fromkeys(
                option.asset_ref
                for goal in missing_goals
                for option in ((binding_by_goal.get(goal.goal_id).clarification.options if binding_by_goal.get(goal.goal_id) and binding_by_goal[goal.goal_id].clarification else []))
                if option.asset_ref
            ))
        }
        now = self._clock()
        pending_id = _stable_id("pending", command.thread_id, command.user_id, command.turn_id, command.message_id)
        proposed = PendingClarification(
            pending_id=pending_id,
            thread_id=command.thread_id,
            user_id=command.user_id,
            status="waiting",
            version=1,
            idempotency_key=f"{command.idempotency_key}:create-pending",
            created_turn_id=command.turn_id,
            created_message_id=command.message_id,
            created_at=now,
            updated_at=now,
            expires_at=now + DEFAULT_PENDING_TTL,
            original_goals=missing_goals,
            missing_slots=missing_slots,
            candidate_values={**safe_candidates, **command.candidate_values},
            source_bindings=command.source_bindings,
            historical_authorization_audit=command.historical_authorization_audit,
        )
        return PendingTransitionProposal(
            action="create_waiting",
            pending_id=pending_id,
            proposed_pending=proposed,
            transition_idempotency_keys=[proposed.idempotency_key],
        )


def _terminal_goal_statuses(preview: TurnResult, snapshot: Any, terminal: str) -> list[GoalExecutionStatus]:
    plan = getattr(snapshot, "execution_plan", None)
    nodes = list(getattr(plan, "nodes", []) or [])
    node_ids_by_goal: dict[str, list[str]] = {}
    for node in nodes:
        for goal_id in list(getattr(node, "goal_ids", []) or []):
            node_ids_by_goal.setdefault(str(goal_id), []).append(str(getattr(node, "node_id", "")))
    readiness = {item.goal_id: item.status for item in preview.readiness}
    authorization = {item.goal_id: item.status for item in preview.authorization}
    results: list[GoalExecutionStatus] = []
    for goal in preview.request.goals:
        if authorization.get(goal.goal_id) == "denied":
            status = "denied"
        elif readiness.get(goal.goal_id) == "satisfied_by_artifact":
            status = "satisfied"
        elif str(readiness.get(goal.goal_id, "")).startswith("blocked_"):
            status = "blocked"
        elif terminal == "completed":
            status = "completed"
        elif terminal in {"blocked", "cancelled"}:
            status = "blocked"
        else:
            status = "failed"
        results.append(
            GoalExecutionStatus(
                goal_id=goal.goal_id,
                status=status,
                node_ids=node_ids_by_goal.get(goal.goal_id, []),
            )
        )
    return results


def _attach_goal_dependencies(goals: list[CanonicalGoal], clauses) -> list[CanonicalGoal]:  # noqa: ANN001
    """Project explicit clause relations; never serialize clause indexes as Goal ids."""

    goal_by_clause = {goal.clause_index: goal for goal in goals}
    clause_by_index = {clause.clause_index: clause for clause in clauses}
    predicates = {
        "if_abnormal": "diagnosis_is_abnormal",
        "if_fault_confirmed": "fault_is_confirmed",
        "if_high_risk": "risk_is_high",
        "if_workorder_recommended": "workorder_is_recommended",
    }
    result: list[CanonicalGoal] = []
    for goal in goals:
        clause = clause_by_index[goal.clause_index]
        dependencies = [
            goal_by_clause[index].goal_id
            for index in clause.modality.depends_on_clause_indexes
            if index in goal_by_clause
        ]
        if not dependencies and goal.capability in {"generate_report", "evaluate_workorder_need", "create_workorder_draft"}:
            producer = next(
                (
                    item for item in reversed(result)
                    if item.capability in {"diagnose_fault", "resolution_recommendation", "generate_report"}
                ),
                None,
            )
            if producer is not None:
                dependencies = [producer.goal_id]
        condition = None
        if clause.modality.conditional and clause.modality.condition_type:
            condition = GoalExecutionCondition(
                predicate=predicates[clause.modality.condition_type],
                source_goal_id=dependencies[-1] if dependencies else "",
            )
        result.append(goal.model_copy(
            update={"dependencies": list(dict.fromkeys(dependencies)), "execution_condition": condition},
            deep=True,
        ))
    return result


def _clause_semantics(clause) -> dict[str, Any]:  # noqa: ANN001
    modality = clause.modality
    return {
        "clause_index": clause.clause_index,
        "capability": clause.action.capability if clause.action else None,
        **modality.model_dump(mode="json"),
    }


def _legacy_semantic_trace(parsed) -> dict[str, Any]:  # noqa: ANN001
    """同步兼容 preview 也输出同形 trace，避免消费者猜测字段。"""

    return {
        "attempted": False,
        "mode": "off",
        "schema": "semantic_turn_proposal.v1",
        "status": "not_attempted",
        "accepted": [],
        "rejected": [],
        "clarify": [],
        "fallback": False,
        "fallback_reason": "",
        "latency_ms": 0,
        "input_tokens": None,
        "output_tokens": None,
        "model_name": "",
        "concurrency_limited": False,
        "deterministic_capabilities": [item.action.capability for item in parsed.clauses if item.action],
        "proposal_capabilities": [],
    }


def _binding_trace(bound_turn, candidates) -> dict[str, Any]:  # noqa: ANN001
    bindings = bound_turn.goal_bindings
    type_by_ref = {item.artifact_ref: item.artifact_type for item in candidates if item.artifact_ref}
    sources = list(dict.fromkeys(
        slot.provenance
        for binding in bindings
        for slot in binding.slots
        if slot.provenance
    ))
    return {
        "status": "bound" if all(item.binding_status == "bound" for item in bindings) else "attention_required",
        "goal_count": len(bindings),
        "bound_goal_count": sum(item.binding_status == "bound" for item in bindings),
        "clarification_goal_count": sum(item.binding_status == "needs_clarification" for item in bindings),
        "binding_sources": sources,
        "selected_candidate_types": list(dict.fromkeys(
            type_by_ref.get(ref)
            for item in bindings for ref in item.source_artifact_refs
            if type_by_ref.get(ref)
        )),
        "selected_candidate_refs": list(dict.fromkeys(
            ref for item in bindings for ref in item.source_artifact_refs
        )),
        "blockers": list(dict.fromkeys(value for item in bindings for value in item.blockers)),
        "explicit_override_count": sum(
            slot.provenance == "explicit_correction" for item in bindings for slot in item.slots
        ),
        "ambiguous_slot_count": sum(
            slot.status == "ambiguous" for item in bindings for slot in item.slots
        ),
        "goals": [
            {
                "goal_id": item.goal_id,
                "capability": item.capability,
                "asset_refs": item.asset_refs,
                "source_artifact_count": len(item.source_artifact_refs),
                "binding_status": item.binding_status,
            }
            for item in bindings
        ],
    }
