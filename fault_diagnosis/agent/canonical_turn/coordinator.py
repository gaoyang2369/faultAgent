"""Canonical production-turn orchestration and side-effect-free preview."""

from __future__ import annotations

import hashlib
import inspect
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fault_diagnosis.agent.canonical_turn.parser import CurrentUtteranceParser
from fault_diagnosis.domain.canonical_turn import (
    CanonicalGoal,
    CanonicalTurnRequest,
    GoalExecutionStatus,
    PendingBinding,
    PendingClarification,
    PendingTransitionProposal,
    TurnCommand,
    TurnEvent,
    TurnResult,
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


_REQUIRED_SLOTS = {
    "check_runtime_status": ["device"],
    "compare_runtime_status": ["device"],
    "create_workorder_draft": ["device"],
    "diagnose_fault": ["device"],
    "explain_fault_code": ["fault_code"],
    "generate_report": [],
    "resolution_recommendation": ["device"],
}
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
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._parser = parser or CurrentUtteranceParser()
        self._pending_repository = pending_repository or MemoryPendingClarificationRepository(clock=clock)
        self._clock = clock
        self._completed: dict[tuple[str, str, str, str, str], TurnResult] = {}
        self._execution_lock = asyncio.Lock()

    def preview(self, command: TurnCommand) -> TurnResult:
        """Phase 1 compatibility alias for :meth:`preview_turn`."""

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
        current_goals = self._build_current_goals(command, parsed)
        if not current_goals:
            current_goals = self._build_context_followup_goals(command, parsed, conversation_context)
        waiting = self._pending_repository.peek_waiting(
            command.thread_id,
            command.user_id,
            now=self._clock(),
        )
        goals, binding = self._bind_pending(waiting, current_goals, parsed)
        transition = self._transition_proposal(command, waiting, goals, binding)
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
        events = [
            TurnEvent(
                event_type="utterance_parsed",
                sequence=0,
                detail={"clause_count": len(parsed.clauses), "entity_count": len(parsed.entities)},
            ),
            TurnEvent(
                event_type="pending_loaded",
                sequence=1,
                detail={"pending_id": waiting.pending_id if waiting else None},
            ),
            TurnEvent(
                event_type="pending_bound",
                sequence=2,
                detail={"kind": binding.kind, "consumes_pending": binding.consumes_pending},
            ),
            TurnEvent(
                event_type="request_built",
                sequence=3,
                detail={"goal_ids": [goal.goal_id for goal in goals], "execution_performed": False},
            ),
        ]
        auth = auth_context or build_auth_context(user_id=command.user_id, role="guest")
        authorization = decide_goal_authorization(request, auth)
        source_resolutions = resolve_goal_sources(request, conversation_context)
        readiness = decide_goal_readiness(request, authorization, source_resolutions)
        return TurnResult(
            request=request,
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
            preview = self.preview_turn(
                command,
                auth_context=auth_context,
                conversation_context=conversation_context,
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
                    carried_slot_values[slot] = unique[0] if len(unique) == 1 else unique
            if clause.source and clause.action is None:
                carried_source_refs = list(clause.source.entity_refs)
            if clause.action is None:
                continue
            required_slots = list(_REQUIRED_SLOTS[clause.action.capability])
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
                    resolved_slots[slot] = values[0] if len(values) == 1 else values
                    carried_slot_values[slot] = resolved_slots[slot]
                elif slot in required_slots and slot in carried_slot_values:
                    resolved_slots[slot] = carried_slot_values[slot]
            missing_slots = [slot for slot in required_slots if slot not in resolved_slots]
            source_refs = list(clause.source.entity_refs) if clause.source else carried_source_refs
            source_requirements = [
                entity_by_id[ref].value
                for ref in source_refs
                if ref in entity_by_id
            ]
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
        return _attach_goal_dependencies(goals)

    def _build_context_followup_goals(
        self,
        command: TurnCommand,
        parsed,
        conversation_context: dict[str, Any] | None,
    ) -> list[CanonicalGoal]:
        text = command.raw_message.strip()
        if not any(marker in text for marker in ("详细", "展开", "多说", "字段")):
            return []
        manifests = [item for item in ((conversation_context or {}).get("artifact_manifests") or []) if isinstance(item, dict)]
        knowledge = [item for item in manifests if item.get("artifact_type") == "knowledge_artifact"]
        if len(knowledge) != 1:
            return []
        codes = [str(item) for item in (knowledge[0].get("fault_code_refs") or []) if str(item)]
        resolved = {"fault_code": codes[0]} if len(codes) == 1 else {}
        goal_id = _stable_id("goal", command.thread_id, command.turn_id, command.message_id, "0", "explain_fault_code")
        return [
            CanonicalGoal(
                goal_id=goal_id,
                capability="explain_fault_code",
                origin="inferred",
                user_requested=True,
                user_visible=True,
                clause_index=0,
                required_slots=["fault_code"],
                resolved_slots=resolved,
                missing_slots=[] if resolved else ["fault_code"],
                source_requirements=[str(knowledge[0].get("artifact_id") or "")],
                provenance=GoalProvenance(
                    parser_source="deterministic",
                    utterance_span=(0, len(text)),
                ),
            )
        ]

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

        missing_goals = [goal for goal in goals if goal.missing_slots]
        if not missing_goals:
            return PendingTransitionProposal(action="none")
        missing_slots = list(dict.fromkeys(slot for goal in missing_goals for slot in goal.missing_slots))
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
            candidate_values=command.candidate_values,
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


def _attach_goal_dependencies(goals: list[CanonicalGoal]) -> list[CanonicalGoal]:
    """Express only same-turn, clause-ordered producer dependencies."""

    result: list[CanonicalGoal] = []
    for goal in goals:
        dependencies = list(goal.dependencies)
        if goal.capability == "generate_report":
            producer = next(
                (item for item in reversed(result) if item.capability in {"diagnose_fault", "resolution_recommendation"}),
                None,
            )
            if producer is not None:
                dependencies.append(producer.goal_id)
        elif goal.capability == "create_workorder_draft":
            producer = next(
                (item for item in reversed(result) if item.capability in {"generate_report", "diagnose_fault"}),
                None,
            )
            if producer is not None:
                dependencies.append(producer.goal_id)
        result.append(goal.model_copy(update={"dependencies": list(dict.fromkeys(dependencies))}, deep=True))
    return result
