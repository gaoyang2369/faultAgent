"""将 Wave 2 LLM 意图提议收口为可执行前的 Canonical parse。"""

from __future__ import annotations

import re

from fault_diagnosis.agent.semantics.contracts import (
    SemanticFieldDecision,
    SemanticTurnProposal,
)
from fault_diagnosis.agent.canonical_turn.entity_extractor import DeterministicEntityExtractor
from fault_diagnosis.domain.canonical_turn import (
    ClauseAction,
    ClauseModality,
    ClauseSource,
    CurrentUtteranceParse,
    EntitySpan,
    IntentResolutionMetadata,
    StructuredClause,
    capability_spec,
)
from fault_diagnosis.domain.security.assets import resolve_asset


_FAULT_CODE = re.compile(r"[A-Za-z]{1,3}\d{4,6}$")
_HIGH_RISK = {"create_workorder_draft", "dispatch_workorder"}


class IntentCanonicalizer:
    """只接受可由当前文本、资产注册表和白名单证明的模型字段。"""

    def canonicalize(
        self,
        deterministic: CurrentUtteranceParse,
        proposal: SemanticTurnProposal,
    ) -> tuple[CurrentUtteranceParse, list[SemanticFieldDecision]]:
        entities = [item.model_copy(deep=True) for item in deterministic.entities]
        clauses = [item.model_copy(deep=True) for item in deterministic.clauses]
        decisions: list[SemanticFieldDecision] = []
        proposed_entities = self._accept_entities(deterministic.raw_text, proposal, entities, decisions)
        clause_by_proposal: dict[int, int] = {}
        unsupported_high_risk = "unsupported_high_risk_action" in deterministic.clarification_needs
        for item in proposal.clauses:
            if unsupported_high_risk:
                decisions.append(_decision(
                    f"clauses[{item.clause_index}].capability",
                    "CLARIFY",
                    item.capability,
                    "unsupported_high_risk_action",
                ))
                continue
            canonical_index = self._apply_clause(
                deterministic.raw_text, item, entities, proposed_entities, clauses, decisions
            )
            if canonical_index is not None:
                clause_by_proposal[item.clause_index] = canonical_index
        clauses.sort(key=lambda item: (item.start, item.end))
        remapped = {item.clause_index: index for index, item in enumerate(clauses)}
        clauses = [item.model_copy(update={"clause_index": index}, deep=True) for index, item in enumerate(clauses)]
        clauses = self._apply_dependencies(clauses, proposal, clause_by_proposal, remapped, decisions)
        accepted = [item.field for item in decisions if item.decision == "ACCEPT"]
        rejected = [item.field for item in decisions if item.decision == "REJECT"]
        clarify = [item.field for item in decisions if item.decision == "CLARIFY"]
        clarification_needs = list(dict.fromkeys([
            *deterministic.clarification_needs,
            *("semantic_ambiguity" for _ in clarify),
            *proposal.ambiguities,
        ]))
        return deterministic.model_copy(update={
            "entities": entities,
            "clauses": clauses,
            "clarification_needs": clarification_needs,
            "deterministic_confident": bool(clauses or entities),
            "model_used": bool(accepted),
            "model_rejection_reason": "semantic_clarification_required" if clarify else None,
            "intent_resolution": IntentResolutionMetadata(
                mode="llm_primary", model_status="completed", accepted_model_fields=accepted,
                rejected_model_fields=rejected, deterministic_capabilities=[
                    item.action.capability for item in deterministic.clauses if item.action
                ], model_capabilities=[item.capability for item in proposal.clauses if item.capability],
            ),
        }, deep=True), decisions

    def _accept_entities(self, text, proposal, entities, decisions):  # noqa: ANN001
        accepted: dict[int, EntitySpan] = {}
        for index, item in enumerate(proposal.entities):
            field = f"entities[{index}]"
            if not _valid_span(text, item.start, item.end, item.text):
                decisions.append(_decision(field, "REJECT", None, "invalid_text_span"))
                continue
            value = self._normalize_entity(item.kind, item.text, item.normalized_candidate)
            if value is None:
                decision = "CLARIFY" if item.kind == "device_reference" else "REJECT"
                code = "unknown_asset_alias" if item.kind == "device_reference" else "invalid_entity_format"
                decisions.append(_decision(field, decision, None, code))
                continue
            existing = next((entity for entity in entities if entity.kind == item.kind and entity.start == item.start and entity.end == item.end), None)
            if existing is not None:
                accepted[index] = existing
                decisions.append(_decision(field, "ACCEPT", existing.value, "matches_deterministic_entity"))
                continue
            entity = EntitySpan(
                entity_id=f"ent_model_{len(entities) + 1:02d}_{item.kind}", kind=item.kind, value=value,
                start=item.start, end=item.end, text=item.text, confidence=item.confidence,
                attributes={"source": "llm_proposal"},
            )
            entities.append(entity)
            accepted[index] = entity
            decisions.append(_decision(field, "ACCEPT", value, "verified_model_entity"))
        return accepted

    @staticmethod
    def _normalize_entity(kind: str, text: str, candidate: str | None) -> str | None:
        if kind == "device_reference":
            record = resolve_asset(candidate or text)
            return record.display_name if record is not None else None
        if kind == "fault_code":
            value = (candidate or text).upper()
            return value if _FAULT_CODE.fullmatch(value) else None
        parsed = DeterministicEntityExtractor().extract(text)
        matched = next(
            (item for item in parsed if item.kind == "time_window" and item.start == 0 and item.end == len(text)),
            None,
        )
        return matched.value if matched is not None else None

    def _apply_clause(self, text, item, entities, proposed_entities, clauses, decisions):  # noqa: ANN001
        base = f"clauses[{item.clause_index}]"
        if not _valid_span(text, item.start, item.end, item.text):
            decisions.append(_decision(f"{base}.span", "REJECT", None, "invalid_text_span"))
            return None
        spec = capability_spec(item.capability or "")
        if item.capability and (spec is None or not spec.llm_may_propose):
            decisions.append(_decision(f"{base}.capability", "REJECT", None, "unknown_or_forbidden_capability"))
            return None
        referenced = [proposed_entities[index] for index in item.entity_indexes if index in proposed_entities]
        referenced.extend(entity for entity in entities if entity.start >= item.start and entity.end <= item.end)
        refs = list(dict.fromkeys(entity.entity_id for entity in referenced))
        overlapping = [clause for clause in clauses if clause.start < item.end and clause.end > item.start]
        target = max(overlapping, key=lambda clause: min(clause.end, item.end) - max(clause.start, item.start)) if overlapping else None
        if item.capability is None:
            decisions.append(_decision(f"{base}.capability", "REJECT", None, "missing_capability"))
            return None
        high_risk_conflict = next((
            clause
            for clause in overlapping
            if clause.action
            and clause.action.capability != item.capability
            and (item.capability in _HIGH_RISK or clause.action.capability in _HIGH_RISK)
        ), None)
        if high_risk_conflict is not None:
            decisions.append(_decision(f"{base}.capability", "CLARIFY", item.capability, "high_risk_capability_conflict"))
            return target.clause_index if target is not None else high_risk_conflict.clause_index
        action = ClauseAction(capability=item.capability, confidence=item.confidence, entity_refs=refs, inferred=True)
        modality = self._modality(item, target, base, decisions)
        source = ClauseSource(source_kind=item.source_kind, entity_refs=[]) if item.source_kind == "prior_result" else None
        slot = _slot_projection(referenced)
        new_clause = StructuredClause(
            clause_index=target.clause_index if target else len(clauses), text=item.text, start=item.start, end=item.end,
            action=action, source=source or (target.source if target else None), slot=slot, modality=modality, parser_source="model",
        )
        if target is None:
            clauses.append(new_clause)
            decisions.append(_decision(f"{base}.capability", "ACCEPT", item.capability, "valid_allowlisted_capability_with_grounded_span"))
            return new_clause.clause_index
        # One accepted model clause is authoritative for its grounded span.
        # Do not leave lower-priority deterministic fragments behind as extra
        # Goals merely because punctuation split the same semantic statement.
        for clause in overlapping:
            if clause is not target and clause in clauses:
                clauses.remove(clause)
        clauses[clauses.index(target)] = new_clause
        reason = "model_corrected_deterministic_capability" if target.action and target.action.capability != item.capability else "confirmed_capability"
        decisions.append(_decision(f"{base}.capability", "ACCEPT", item.capability, reason))
        return target.clause_index

    @staticmethod
    def _modality(item, target, base, decisions):  # noqa: ANN001
        deterministic = target.modality if target is not None else None
        if deterministic is not None and deterministic.negated and item.requested and not item.negated:
            decisions.append(_decision(f"{base}.requested", "REJECT", True, "deterministic_negation_conflict"))
            return deterministic
        if item.negated or not item.requested:
            decisions.append(_decision(f"{base}.requested", "ACCEPT", False, "model_non_execution_is_safe"))
        elif deterministic is None or (deterministic.requested, deterministic.negated) != (item.requested, item.negated):
            decisions.append(_decision(f"{base}.requested", "ACCEPT", item.requested, "grounded_modality"))
        return ClauseModality(
            requested=item.requested and not item.negated, negated=item.negated,
            conditional=item.conditional, condition_type=item.condition_type if item.conditional else None,
            sequence_index=item.sequence_index,
        )

    @staticmethod
    def _apply_dependencies(clauses, proposal, clause_by_proposal, remapped, decisions):  # noqa: ANN001
        result = list(clauses)
        for item in proposal.clauses:
            old_index = clause_by_proposal.get(item.clause_index)
            if old_index is None or old_index not in remapped:
                continue
            index = remapped[old_index]
            dependencies = []
            for dependency in item.depends_on_clause_indexes:
                dependency_old = clause_by_proposal.get(dependency)
                if dependency_old is None or dependency_old not in remapped or remapped[dependency_old] >= index:
                    decisions.append(_decision(f"clauses[{item.clause_index}].depends_on_clause_indexes", "REJECT", dependency, "invalid_dependency_reference"))
                    continue
                dependencies.append(remapped[dependency_old])
            if not dependencies and item.sequence_index is not None:
                earlier = [
                    remapped[clause_by_proposal[proposal_index]]
                    for proposal_index, mapped_index in clause_by_proposal.items()
                    if proposal_index < item.clause_index and mapped_index in remapped
                ]
                if earlier:
                    dependencies = [max(earlier)]
                    decisions.append(_decision(
                        f"clauses[{item.clause_index}].sequence_index", "ACCEPT", item.sequence_index,
                        "sequence_implies_prior_clause_dependency",
                    ))
            if dependencies:
                result[index] = result[index].model_copy(update={
                    "modality": result[index].modality.model_copy(update={
                        "depends_on_clause_indexes": list(dict.fromkeys(dependencies)),
                        "relation_to_previous_clause": "condition" if result[index].modality.conditional else "sequence",
                    })
                }, deep=True)
                decisions.append(_decision(f"clauses[{item.clause_index}].depends_on_clause_indexes", "ACCEPT", dependencies, "valid_prior_clause_dependencies"))
        return result


def _valid_span(text: str, start: int, end: int, value: str) -> bool:
    return 0 <= start < end <= len(text) and text[start:end] == value


def _slot_projection(entities):  # noqa: ANN001
    by_kind = {"device_reference": "device", "fault_code": "fault_code", "time_window": "time_window"}
    slots = {}
    for entity in entities:
        if entity.kind in by_kind:
            slots.setdefault(by_kind[entity.kind], []).append(entity.entity_id)
    return slots


def _decision(field: str, decision: str, value, reason_code: str) -> SemanticFieldDecision:  # noqa: ANN001
    return SemanticFieldDecision(field=field, decision=decision, value=value, reason_code=reason_code)
