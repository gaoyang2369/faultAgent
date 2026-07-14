"""Deterministic-first parsing for the isolated canonical-turn preview."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fault_diagnosis.domain.canonical_turn import (
    ClauseAction,
    ClauseSource,
    CurrentUtteranceParse,
    EntitySpan,
    StructuredClause,
)


_FAULT_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,3}\d{4,6})(?![A-Za-z0-9])")
_DEVICE_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]\d{2,4})?\s*电机\s*\d+(?!\d)", re.IGNORECASE)
_TIME_RE = re.compile(
    r"(?:最近|过去|近)\s*(?:\d+|[一二两三四五六七八九十半]+)\s*(?:分钟|小时|天|周)|"
    r"(?:今天|当前|现在|此刻)"
)
_ARTIFACT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:artifact|analysis|report|diagnosis):[A-Za-z0-9._:-]+",
    re.IGNORECASE,
)
_SOURCE_RE = re.compile(
    r"(?:刚才|上一轮|上一次|前面|之前|这个|该)(?:的)?(?:诊断(?:结果)?|分析结果|运行报告|报告|数据|结果)"
)
_CORRECTION_RE = re.compile(r"(?:不是.+?是|改成|更正为|应该是)")
_DEICTIC_RE = re.compile(r"(?:它|这个|那个|该设备|刚才|上一轮|上一次|前面|之前)")
_PUNCTUATION_RE = re.compile(r"[，,。；;！？!?]+")
_LEADING_LINKER_RE = re.compile(r"^(并且|并|然后|同时|顺便|再|另外|以及)\s*")
_FORBIDDEN_MODEL_TEXT_RE = re.compile(
    r"(?:\bsql\b|\bnode\b|\btool\b|\bpermission\b|\bauthori[sz](?:e|ation|ed)\b|权限|授权|工具名?|节点名?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClauseModelRequest:
    text: str
    deterministic_entities: tuple[dict[str, Any], ...]
    temperature: Literal[0] = 0
    response_schema: str = "model_clause_parse.v1"


class StructuredClauseModel(Protocol):
    """Boundary for an optional temperature-zero structured-output model."""

    def parse(self, request: ClauseModelRequest) -> dict[str, Any]:
        """Return a JSON-like payload conforming to ``model_clause_parse.v1``."""


class _ModelAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    confidence: float = Field(ge=0.0, le=1.0)
    entity_refs: list[str] = Field(default_factory=list)
    inferred: bool = False


class _ModelSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["artifact", "prior_result", "current_message"]
    entity_refs: list[str] = Field(default_factory=list)
    relation: str = "input_to_action"


class _ModelClause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_index: int = Field(ge=0)
    text: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    action: _ModelAction | None = None
    source: _ModelSource | None = None
    slot: dict[str, list[str]] = Field(default_factory=dict)
    linker: str | None = None


class _ModelClauseParse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["model_clause_parse.v1"]
    clauses: list[_ModelClause]


class CurrentUtteranceParser:
    """Parse one utterance without consulting conversation or production frames."""

    def __init__(self, *, model: StructuredClauseModel | None = None) -> None:
        self._model = model

    def parse(self, text: str) -> CurrentUtteranceParse:
        raw_text = str(text or "")
        entities = self._extract_entities(raw_text)
        clauses = self._deterministic_clauses(raw_text, entities)
        confident = any(clause.action for clause in clauses) or bool(entities)
        model_used = False
        model_rejection_reason: str | None = None

        if self._model is not None:
            try:
                payload = self._model.parse(
                    ClauseModelRequest(
                        text=raw_text,
                        deterministic_entities=tuple(entity.model_dump(mode="json") for entity in entities),
                    )
                )
                clauses = self._validate_model_payload(raw_text, entities, payload)
                model_used = True
                confident = any(clause.action for clause in clauses) or bool(entities)
            except (ValidationError, ValueError, TypeError, RuntimeError) as exc:
                model_rejection_reason = f"{type(exc).__name__}: {exc}"

        clarification_needs: list[str] = []
        if not confident:
            clarification_needs.append("action_or_reference_not_determined")
        if self._model is not None and model_rejection_reason and not any(clause.action for clause in clauses):
            clarification_needs.append("structured_model_failed_without_high_confidence_action")

        return CurrentUtteranceParse(
            raw_text=raw_text,
            entities=entities,
            clauses=clauses,
            clarification_needs=list(dict.fromkeys(clarification_needs)),
            deterministic_confident=confident,
            model_used=model_used,
            model_rejection_reason=model_rejection_reason,
        )

    def _extract_entities(self, text: str) -> list[EntitySpan]:
        matches: list[tuple[int, int, str, str, dict[str, Any]]] = []
        specs = (
            (_FAULT_CODE_RE, "fault_code", lambda value: value.upper(), {}),
            (_DEVICE_RE, "device_reference", lambda value: re.sub(r"\s+", "", value), {}),
            (_TIME_RE, "time_window", lambda value: value.strip(), {"relative": True}),
            (_ARTIFACT_RE, "artifact_reference", lambda value: value.strip(), {"explicit": True}),
            (_SOURCE_RE, "source_reference", lambda value: value.strip(), {"historical": True}),
            (_CORRECTION_RE, "correction_reference", lambda value: value.strip(), {}),
            (_DEICTIC_RE, "deictic_reference", lambda value: value.strip(), {}),
        )
        for pattern, kind, normalize, attributes in specs:
            for match in pattern.finditer(text):
                raw = match.group(1) if kind == "fault_code" else match.group(0)
                start, end = match.span(1) if kind == "fault_code" else match.span(0)
                matches.append((start, end, kind, normalize(raw), attributes))
        matches.sort(key=lambda item: (item[0], item[1], item[2]))
        entities: list[EntitySpan] = []
        seen: set[tuple[int, int, str]] = set()
        for start, end, kind, value, attributes in matches:
            identity = (start, end, kind)
            if identity in seen:
                continue
            seen.add(identity)
            entities.append(
                EntitySpan(
                    entity_id=f"ent_{len(entities) + 1:02d}_{kind}",
                    kind=kind,
                    value=value,
                    start=start,
                    end=end,
                    text=text[start:end],
                    attributes=attributes,
                )
            )
        return entities

    def _deterministic_clauses(self, text: str, entities: list[EntitySpan]) -> list[StructuredClause]:
        clauses: list[StructuredClause] = []
        cursor = 0
        boundaries = list(_PUNCTUATION_RE.finditer(text))
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
            linker_match = _LEADING_LINKER_RE.match(clause_text)
            linker = linker_match.group(1) if linker_match else None
            overlapping = [entity for entity in entities if entity.start < raw_end and entity.end > raw_start]
            action = self._detect_action(clause_text, overlapping)
            source_entities = [
                entity
                for entity in overlapping
                if entity.kind in {"artifact_reference", "source_reference"}
            ]
            source = None
            if source_entities:
                source = ClauseSource(
                    source_kind="artifact" if any(item.kind == "artifact_reference" for item in source_entities) else "prior_result",
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
                    action=action,
                    source=source,
                    slot=slot,
                    linker=linker,
                )
            )
        return clauses

    def _detect_action(self, text: str, entities: list[EntitySpan]) -> ClauseAction | None:
        normalized = re.sub(r"\s+", "", text)
        fault_refs = [item.entity_id for item in entities if item.kind == "fault_code"]
        device_refs = [item.entity_id for item in entities if item.kind == "device_reference"]
        all_refs = [item.entity_id for item in entities]

        if re.search(r"(?:生成|整理成|出一份|制作).{0,6}(?:运行)?报告", normalized):
            return ClauseAction(capability="generate_report", entity_refs=all_refs)
        if re.search(r"(?:处理建议|处置建议|解决建议|怎么处理|如何处理|给出.{0,4}建议)", normalized):
            return ClauseAction(capability="resolution_recommendation", entity_refs=device_refs)
        if re.search(r"(?:对比|比较).{0,12}(?:状态|运行|异常|数据)", normalized):
            return ClauseAction(capability="compare_runtime_status", entity_refs=device_refs)
        if re.search(r"(?:诊断(?!结果)|判断).{0,12}(?:故障|异常|问题|原因)|(?:有没有|是否|存在).{0,5}故障", normalized):
            return ClauseAction(capability="diagnose_fault", entity_refs=device_refs)
        if fault_refs and re.search(r"(?:什么意思|含义|解释|说明|故障码)", normalized):
            return ClauseAction(capability="explain_fault_code", entity_refs=fault_refs)
        if fault_refs and re.fullmatch(r"(?:查询|查一下|查|看看)?[A-Za-z]{1,3}\d{4,6}", normalized, re.IGNORECASE):
            return ClauseAction(
                capability="explain_fault_code",
                entity_refs=fault_refs,
                inferred=True,
            )
        if device_refs and re.search(r"(?:查询|查看|检查|看看|有没有).{0,18}(?:状态|运行|异常|数据)", normalized):
            return ClauseAction(capability="check_runtime_status", entity_refs=device_refs)
        if re.search(r"(?:创建|生成|新建).{0,5}(?:工单|维修单)", normalized):
            return ClauseAction(capability="create_workorder_draft", entity_refs=all_refs)
        return None

    def _validate_model_payload(
        self,
        text: str,
        entities: list[EntitySpan],
        payload: dict[str, Any],
    ) -> list[StructuredClause]:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if _FORBIDDEN_MODEL_TEXT_RE.search(serialized):
            raise ValueError("model payload contains forbidden execution or authorization content")
        parsed = _ModelClauseParse.model_validate(payload)
        entity_ids = {entity.entity_id for entity in entities}
        clauses: list[StructuredClause] = []
        for expected_index, model_clause in enumerate(parsed.clauses):
            if model_clause.clause_index != expected_index:
                raise ValueError("model clause indexes must be contiguous and ordered")
            if model_clause.end > len(text) or text[model_clause.start : model_clause.end] != model_clause.text:
                raise ValueError("model clause span is outside the current utterance")
            refs = set(model_clause.action.entity_refs if model_clause.action else [])
            refs.update(model_clause.source.entity_refs if model_clause.source else [])
            for slot_refs in model_clause.slot.values():
                refs.update(slot_refs)
            if refs - entity_ids:
                raise ValueError("model clause references an entity not extracted deterministically")
            overlapping = [entity for entity in entities if entity.start < model_clause.end and entity.end > model_clause.start]
            deterministic_action = self._detect_action(model_clause.text, overlapping)
            if model_clause.action is not None:
                if deterministic_action is None or deterministic_action.capability != model_clause.action.capability:
                    raise ValueError("model action is not supported by deterministic action evidence")
            action = ClauseAction.model_validate(model_clause.action.model_dump()) if model_clause.action else None
            source = ClauseSource.model_validate(model_clause.source.model_dump()) if model_clause.source else None
            clauses.append(
                StructuredClause(
                    clause_index=model_clause.clause_index,
                    text=model_clause.text,
                    start=model_clause.start,
                    end=model_clause.end,
                    action=action,
                    source=source,
                    slot=model_clause.slot,
                    linker=model_clause.linker,
                    parser_source="model",
                )
            )
        return clauses
