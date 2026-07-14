"""Deterministic clause and action parsing driven by catalog rules."""

from __future__ import annotations

import re
from typing import Any

from fault_diagnosis.agent.canonical_turn.entity_extractor import compile_rule, rules_for
from fault_diagnosis.domain.canonical_turn import (
    ClauseAction,
    ClauseModality,
    ClauseSource,
    EntitySpan,
    StructuredClause,
)


_NEGATION = re.compile(r"(?:(?<!要)不要|不用|无需|不需要|先别|不是要|不必|别(?:再)?|禁止)")
_CONTRAST = re.compile(r"^(?:我)?(?:只|而是|只是|只需|只要)")
_SEQUENCE = re.compile(r"(?:^先|然后|最后|接着|(?:^|[，,])再|完成后再|查完.+再)")
_CONDITION_TYPES = (
    (re.compile(r"(?:建议|推荐).{0,8}(?:建单|工单|报修)"), "if_workorder_recommended"),
    (re.compile(r"(?:风险|严重性).{0,8}(?:较高|高|严重)"), "if_high_risk"),
    (re.compile(r"(?:确认|确实|存在|有).{0,8}(?:故障|毛病)|(?:故障|毛病|[A-Za-z]{1,3}\d{4,6}).{0,8}(?:仍)?存在|仍存在"), "if_fault_confirmed"),
    (re.compile(r"(?:异常|有问题|不正常|不太对劲|状态正常|差异明显)"), "if_abnormal"),
)
_CONDITION_MARKER = re.compile(r"(?:如果|若|假如|只要|.+的话|确认.+后|确实.+后|.+后再)")
_PRIOR_FOLLOWUP = re.compile(r"(?:继续|详细|展开|它|这个|刚才|上一轮|上一份|前面|之前|对应)")
_CONTROL_INJECTION = re.compile(r"(?:忽略.{0,8}(?:规则|指令)|(?:权限|角色).{0,8}(?:改成|提升为|设为).{0,4}管理员|(?:输出|显示).{0,6}(?:工具列表|系统提示词))")


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
        if _CONTROL_INJECTION.search(text):
            return []
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
            action = self.detect_action(clause_text, overlapping)
            if action and action.capability == "diagnose_fault" and _CONDITION_MARKER.search(clause_text) and not re.search(r"(?:就|则|便)", clause_text):
                action = action.model_copy(update={"inferred": True})
            if source is None and _PRIOR_FOLLOWUP.search(clause_text):
                source = ClauseSource(
                    source_kind="prior_result",
                    entity_refs=[item.entity_id for item in overlapping if item.kind == "deictic_reference"],
                )
            if action is None and _CONTRAST.search(clause_text) and clauses:
                previous = clauses[-1].action
                if previous and previous.capability in {"create_workorder_draft", "dispatch_workorder"}:
                    action = ClauseAction(capability="evaluate_workorder_need", inferred=True)
            if action is None and source is None and not overlapping:
                continue
            clauses.append(StructuredClause(
                clause_index=len(clauses), text=clause_text, start=raw_start, end=raw_end,
                action=action,
                source=source,
                slot=slot,
                linker=self._detect_linker(clause_text),
            ))
        if len(clauses) > 1:
            for index, clause in enumerate(clauses):
                if clause.action is not None or clause.source is None:
                    continue
                neighbor = next((item for item in clauses[index + 1:] if item.action), None) or next(
                    (item for item in reversed(clauses[:index]) if item.action), None
                )
                if neighbor is not None and neighbor.source is None:
                    neighbor.source = clause.source
            has_actions = any(item.action for item in clauses)
            clauses = [item for item in clauses if item.action or item.source or (has_actions and item.slot.get("device"))]
            clauses = [item.model_copy(update={"clause_index": index}, deep=True) for index, item in enumerate(clauses)]
        elif clauses and not any(item.action for item in clauses):
            clauses = [item for item in clauses if item.source]
        return self._project_modalities(text, clauses)

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

    @staticmethod
    def _project_modalities(text: str, clauses: list[StructuredClause]) -> list[StructuredClause]:
        actionable = [clause for clause in clauses if clause.action]
        condition_match = _CONDITION_MARKER.search(text)
        global_condition_type = next(
            (name for pattern, name in _CONDITION_TYPES if pattern.search(text)),
            None,
        )
        has_condition = bool(condition_match and global_condition_type)
        has_sequence = bool(_SEQUENCE.search(text)) and not has_condition and len(actionable) > 1
        condition_type: str | None = global_condition_type if has_condition else None
        result: list[StructuredClause] = []
        last_action_index: int | None = None
        for clause in clauses:
            negated = bool(_NEGATION.search(clause.text)) if clause.action else False
            detected_condition = next(
                (name for pattern, name in _CONDITION_TYPES if pattern.search(clause.text)),
                None,
            )
            contains_condition_marker = bool(_CONDITION_MARKER.search(clause.text))
            if contains_condition_marker and detected_condition:
                condition_type = detected_condition
            inferred_antecedent = bool(
                clause.action
                and clause.action.inferred
                and contains_condition_marker
                and not re.search(r"(?:就|则|便)", clause.text)
            )
            conditional = bool(
                clause.action
                and condition_type
                and not inferred_antecedent
                and (
                    contains_condition_marker
                    or (condition_match is not None and clause.start >= condition_match.start())
                )
            )
            relation = None
            dependencies: list[int] = []
            if clause.action:
                if _CONTRAST.search(clause.text):
                    relation = "contrast"
                elif conditional:
                    relation = "condition"
                    if last_action_index is not None:
                        dependencies = [last_action_index]
                elif has_sequence and last_action_index is not None:
                    relation = "sequence"
                    if not negated:
                        dependencies = [last_action_index]
                elif clause.linker in {"并", "并且", "同时", "以及"}:
                    relation = "parallel"
                modality = ClauseModality(
                    requested=not negated,
                    negated=negated,
                    conditional=conditional,
                    condition_type=condition_type if conditional else None,
                    sequence_index=actionable.index(clause) if has_sequence else None,
                    depends_on_clause_indexes=dependencies,
                    relation_to_previous_clause=relation,
                )
                last_action_index = clause.clause_index
            else:
                modality = ClauseModality(requested=clause.source is not None)
            result.append(clause.model_copy(update={"modality": modality}, deep=True))
        return result
