"""In-memory/preview orchestration for Canonical Turn Phase 1."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from fault_diagnosis.agent.canonical_turn.parser import CurrentUtteranceParser
from fault_diagnosis.domain.canonical_turn import (
    CanonicalGoal,
    CanonicalTurnRequest,
    PendingBinding,
    PendingClarification,
    PendingTransitionProposal,
    TurnCommand,
    TurnEvent,
    TurnResult,
)
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
    """Build a canonical request and persistence proposal without execution."""

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

    def preview(self, command: TurnCommand) -> TurnResult:
        parsed = self._parser.parse(command.raw_message)
        current_goals = self._build_current_goals(command, parsed)
        waiting = self._pending_repository.get_waiting(
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
        return TurnResult(
            request=request,
            events=events,
            pending_transition=transition,
            execution_performed=False,
        )

    def _build_current_goals(self, command: TurnCommand, parsed) -> list[CanonicalGoal]:
        entity_by_id = {entity.entity_id: entity for entity in parsed.entities}
        values_by_slot: dict[str, list[str]] = {}
        for slot, kind in _ENTITY_KIND_BY_SLOT.items():
            values_by_slot[slot] = [entity.value for entity in parsed.entities if entity.kind == kind]

        goals: list[CanonicalGoal] = []
        for clause in parsed.clauses:
            if clause.action is None:
                continue
            required_slots = list(_REQUIRED_SLOTS[clause.action.capability])
            resolved_slots: dict[str, Any] = {}
            for slot in required_slots:
                values = list(dict.fromkeys(values_by_slot.get(slot, [])))
                if values:
                    resolved_slots[slot] = values[0] if len(values) == 1 else values
            missing_slots = [slot for slot in required_slots if slot not in resolved_slots]
            source_requirements: list[str] = []
            if clause.source:
                source_requirements = [
                    entity_by_id[ref].value
                    for ref in clause.source.entity_refs
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
        return goals

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
