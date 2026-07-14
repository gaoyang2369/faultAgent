"""Deterministic clause and action parsing driven by catalog rules."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.agent.canonical_turn.entity_extractor import compile_rule, rules_for
from fault_diagnosis.domain.canonical_turn import ClauseAction, ClauseSource, EntitySpan, StructuredClause


class DeterministicClauseParser:
    def __init__(self) -> None:
        self._boundary_rule = rules_for("clause_boundary")[0]
        self._linker_rules = sorted(rules_for("linker"), key=lambda rule: int(rule["priority"]), reverse=True)
        self._action_rules = sorted(
            rules_for("action_predicate"),
            key=lambda rule: int(rule["priority"]),
            reverse=True,
        )

    def parse(self, text: str, entities: list[EntitySpan]) -> list[StructuredClause]:
        clauses: list[StructuredClause] = []
        cursor = 0
        boundaries = list(compile_rule(self._boundary_rule).finditer(text))
        for boundary in [*boundaries, None]:
            end = boundary.start() if boundary is not None else len(text)
            raw_start, raw_end = cursor, end
            cursor = boundary.end() if boundary is not None else len(text)
            while raw_start < raw_end and text[raw_start].isspace():
                raw_start += 1
            while raw_end > raw_start and text[raw_end - 1].isspace():
                raw_end -= 1
            if raw_start >= raw_end:
                continue
            clause_text = text[raw_start:raw_end]
            overlapping = [entity for entity in entities if entity.start < raw_end and entity.end > raw_start]
            source_entities = [
                entity
                for entity in overlapping
                if entity.kind in {"artifact_reference", "source_reference"}
            ]
            source = None
            if source_entities:
                source = ClauseSource(
                    source_kind=(
                        "artifact"
                        if any(item.kind == "artifact_reference" for item in source_entities)
                        else "prior_result"
                    ),
                    entity_refs=[item.entity_id for item in source_entities],
                )
            slot: dict[str, list[str]] = {}
            for slot_name, kind in (
                ("fault_code", "fault_code"),
                ("device", "device_reference"),
                ("time_window", "time_window"),
            ):
                refs = [item.entity_id for item in overlapping if item.kind == kind]
                if refs:
                    slot[slot_name] = refs
            clauses.append(
                StructuredClause(
                    clause_index=len(clauses),
                    text=clause_text,
                    start=raw_start,
                    end=raw_end,
                    action=self.detect_action(clause_text, overlapping),
                    source=source,
                    slot=slot,
                    linker=self._detect_linker(clause_text),
                )
            )
        return clauses

    def detect_action(self, text: str, entities: list[EntitySpan]) -> ClauseAction | None:
        normalized = "".join(text.split())
        observed_kinds = {entity.kind for entity in entities}
        for rule in self._action_rules:
            required_kind = str(rule.get("requires_entity_kind") or "")
            if required_kind and required_kind not in observed_kinds:
                continue
            pattern = compile_rule(rule)
            matched = pattern.fullmatch(normalized) if rule.get("match_mode") == "fullmatch" else pattern.search(normalized)
            if matched is None:
                continue
            ref_kinds = tuple(rule.get("entity_ref_kinds") or ())
            refs = [
                entity.entity_id
                for entity in entities
                if "*" in ref_kinds or entity.kind in ref_kinds
            ]
            return ClauseAction(
                capability=str(rule["semantic_value"]),
                confidence=float(rule["confidence"]),
                entity_refs=refs,
                inferred=bool(rule.get("inferred", False)),
            )
        return None

    def _detect_linker(self, text: str) -> str | None:
        for rule in self._linker_rules:
            match = compile_rule(rule).match(text)
            if match is not None:
                return match.group(int(rule.get("capture_group", 0)))
        return None
