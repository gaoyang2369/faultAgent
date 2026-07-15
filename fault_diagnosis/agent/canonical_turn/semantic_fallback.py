"""Controlled, deterministic-first semantic completion for current-message clauses."""
from __future__ import annotations
import re
import time
from typing import Any, Callable
from pydantic import ValidationError
from fault_diagnosis.agent.canonical_turn.clause_parser import DeterministicClauseParser
from fault_diagnosis.agent.canonical_turn.llm_structured_clause_model import build_intent_clause_model
from fault_diagnosis.agent.canonical_turn.model_clause_parser import ClauseModelRequest, ModelClauseParser
from fault_diagnosis.domain.canonical_turn import CANONICAL_CAPABILITIES, ClauseAction, ClauseSource, EntitySpan, IntentFallbackDecision, IntentResolutionMetadata, StructuredClause


_DOMAIN_ACTION = re.compile(r"(?:状态|表现|异常|不太?正常|不对劲|问题|故障|毛病|分析|诊断|处理|处置|维修|修理|修一下|工单|派单|安排人|建议|怎么办|报告|整理|查一下|看看|运转)")
_FOLLOW_UP = re.compile(r"(?:继续|再(?:看看|分析|处理)|详细|展开|整理出来)")
_AMBIGUOUS_LOW_RISK = re.compile(r"(?:要不要|是不是得|是否需要).{0,10}(?:处理|维修|安排人)")
_NEGATED_REQUEST = re.compile(r"(?:不要|不用|无需|不需要|先别|不必|禁止)")
_NON_DOMAIN = re.compile(r"(?:天气|写.{0,3}诗|介绍.{0,5}Python|什么模型|讲个笑话)", re.I)
_FORBIDDEN_SCOPE = re.compile(r"(?:权限|授权|管理员|选择哪个设备|选择哪个Artifact|候选Artifact|系统提示词)", re.I)

def assess_deterministic_parse(
    text: str, clauses: list[StructuredClause], entities: list[EntitySpan]
) -> IntentFallbackDecision:
    """Return a calibrated rule decision; no synthetic confidence is involved."""
    positive = [c for c in clauses if c.action and c.modality.requested and not c.modality.negated]
    unresolved = [c for c in clauses if c.action is None and (c.source or c.slot)]
    action_signal = bool(_DOMAIN_ACTION.search(text) or _FOLLOW_UP.search(text))
    if positive and not unresolved:
        return IntentFallbackDecision()
    if _NON_DOMAIN.search(text) or _FORBIDDEN_SCOPE.search(text):
        return IntentFallbackDecision()
    if any(c.action and c.modality.negated for c in clauses) and not positive:
        return IntentFallbackDecision()
    if _NEGATED_REQUEST.search(text) and not positive:
        return IntentFallbackDecision()
    reasons: list[str] = []
    if not positive and action_signal:
        reasons.append("no_executable_goal")
    if unresolved and action_signal:
        reasons.append("unresolved_action")
    if action_signal and any(c.source and c.source.source_kind == "prior_result" and c.action is None for c in clauses):
        reasons.append("prior_result_without_action")
    if _AMBIGUOUS_LOW_RISK.search(text):
        reasons.append("ambiguous_low_risk_capability")
    if not clauses and _DOMAIN_ACTION.search(text):
        reasons.append("unsupported_paraphrase")
    return IntentFallbackDecision(eligible=bool(reasons), reason_codes=list(dict.fromkeys(reasons)))

class IntentSemanticMerger:
    """Fill empty semantic fields without replacing deterministic facts."""

    def merge(self, text, entities, deterministic, candidates):  # noqa: ANN001
        result = [item.model_copy(deep=True) for item in deterministic]
        accepted: list[str] = []
        rejected: list[str] = []
        entity_ids = {item.entity_id for item in entities}
        model_metadata: dict[tuple[int, int], Any] = {}
        for candidate in candidates:
            overlaps = [
                item for item in result
                if item.start < candidate.end and item.end > candidate.start
            ]
            target = max(overlaps, key=lambda item: min(item.end, candidate.end) - max(item.start, candidate.start)) if overlaps else None
            if target is None:
                if not candidate.action or candidate.action.capability not in CANONICAL_CAPABILITIES:
                    rejected.append("capability")
                    continue
                target = StructuredClause(
                    clause_index=len(result), text=candidate.text, start=candidate.start, end=candidate.end,
                    slot=self._slots(entities, candidate.start, candidate.end), parser_source="model",
                )
                result.append(target)
                accepted.append("clause_boundary")
            if candidate.action:
                if target.action and target.action.capability != candidate.action.capability:
                    rejected.append("capability")
                elif target.action is None and candidate.action.capability in CANONICAL_CAPABILITIES:
                    refs = [item.entity_id for item in entities if target.start <= item.start and item.end <= target.end]
                    target.action = ClauseAction(
                        capability=candidate.action.capability, confidence=candidate.action.confidence,
                        entity_refs=refs, inferred=True,
                    )
                    target.parser_source = "model"
                    accepted.append("capability")
                elif target.action is None:
                    rejected.append("capability")
            if candidate.source:
                if candidate.source.source_kind == "artifact":
                    rejected.append("source_kind")
                elif target.source and target.source.source_kind != candidate.source.source_kind:
                    rejected.append("source_kind")
                elif target.source is None and candidate.source.source_kind == "prior_result":
                    refs = [
                        item.entity_id for item in entities
                        if item.entity_id in entity_ids and target.start <= item.start and item.end <= target.end
                        and item.kind in {"source_reference", "deictic_reference"}
                    ]
                    target.source = ClauseSource(source_kind="prior_result", entity_refs=refs)
                    accepted.append("source_kind")
            model_metadata[(target.start, target.end)] = candidate.shadow_metadata
        result.sort(key=lambda item: (item.start, item.end))
        result = [item.model_copy(update={"clause_index": index}, deep=True) for index, item in enumerate(result)]
        projected = DeterministicClauseParser._project_modalities(text, result)
        for item in projected:
            semantic = model_metadata.get((item.start, item.end))
            if semantic is None:
                continue
            grounded = item.modality
            if item.parser_source != "model" and (
                semantic.requested, semantic.negated
            ) != (grounded.requested, grounded.negated):
                rejected.extend(["requested", "negated"])
            elif item.parser_source == "model" and (
                semantic.requested, semantic.negated
            ) == (grounded.requested, grounded.negated):
                accepted.extend(["requested", "negated"])
            elif item.parser_source == "model" and (semantic.negated or not semantic.requested):
                rejected.extend(["requested", "negated"])
            if semantic.conditional:
                (accepted if item.parser_source == "model" and grounded.conditional else rejected).append("conditional")
            if semantic.sequence_index is not None:
                (accepted if item.parser_source == "model" and semantic.sequence_index == grounded.sequence_index else rejected).append("sequence")
            if semantic.depends_on:
                (accepted if item.parser_source == "model" and semantic.depends_on == grounded.depends_on_clause_indexes else rejected).append("dependency")
        return projected, list(dict.fromkeys(accepted)), list(dict.fromkeys(rejected))

    @staticmethod
    def _slots(entities: list[EntitySpan], start: int, end: int) -> dict[str, list[str]]:
        kinds = {"device_reference": "device", "fault_code": "fault_code", "time_window": "time_window"}
        slots: dict[str, list[str]] = {}
        for item in entities:
            if start <= item.start and item.end <= end and item.kind in kinds:
                slots.setdefault(kinds[item.kind], []).append(item.entity_id)
        return slots


class ControlledIntentFallback:
    def __init__(self, *, model=None, model_factory: Callable[[], tuple[Any, str]] | None = None) -> None:
        self._model = model
        self._model_factory = model_factory

    def resolve(self, text, entities, clauses):  # noqa: ANN001
        decision = assess_deterministic_parse(text, clauses, entities)
        deterministic_caps = [c.action.capability for c in clauses if c.action]
        if not decision.eligible:
            return clauses, IntentResolutionMetadata(deterministic_capabilities=deterministic_caps)
        started = time.perf_counter()
        base = dict(fallback_attempted=True, fallback_reasons=decision.reason_codes,
                    deterministic_capabilities=deterministic_caps)
        try:
            model, model_name = (self._model, "injected") if self._model is not None else (
                self._model_factory or (lambda: build_intent_clause_model(runtime="fallback"))
            )()
            payload = model.parse(ClauseModelRequest(text=text,
                deterministic_entities=tuple(item.model_dump(mode="json") for item in entities),
                deterministic_clauses=tuple(self._summary(item) for item in clauses),
                fallback_reasons=tuple(decision.reason_codes),
                allowed_capabilities=tuple(sorted(CANONICAL_CAPABILITIES)),
                allowed_source_kinds=("prior_result", "current_message"),
                schema_version="intent_fallback_request.v1"))
        except TimeoutError:
            return clauses, self._failure("model_timeout", started, base)
        except (ValueError, TypeError):
            return clauses, self._failure("schema_invalid", started, base)
        except Exception as exc:
            status = "model_not_configured" if "not_configured" in str(exc) else "model_error"
            return clauses, self._failure(status, started, base)
        try:
            envelope = ModelClauseParser().validate_for_shadow(
                text, entities, payload, detect_action=DeterministicClauseParser().detect_action,
            )
            if any(item.source and item.source.source_kind == "artifact" for item in envelope.clauses) or (
                "ambiguous_low_risk_capability" in decision.reason_codes
                and any(item.action and item.action.capability == "dispatch_workorder" for item in envelope.clauses)
            ):
                raise ValueError("fallback output violates the request whitelist")
            merged, accepted, rejected = IntentSemanticMerger().merge(text, entities, clauses, envelope.clauses)
        except ValidationError:
            return clauses, self._failure("schema_invalid", started, base, model_name)
        except Exception:
            return clauses, self._failure("validation_failed", started, base, model_name)
        model_caps = [item.action.capability for item in envelope.clauses if item.action]
        mode = "llm_fallback" if accepted else "deterministic_after_llm_failure"
        return merged if accepted else clauses, IntentResolutionMetadata(
            mode=mode, model_status="completed", accepted_model_fields=accepted,
            rejected_model_fields=rejected, duration_ms=(time.perf_counter() - started) * 1000,
            model_name=model_name, model_capabilities=model_caps, **base,
        )

    @staticmethod
    def _summary(clause: StructuredClause) -> dict[str, Any]:
        return {"text": clause.text, "capability": clause.action.capability if clause.action else None,
                "requested": clause.modality.requested, "negated": clause.modality.negated,
                "source_kind": clause.source.source_kind if clause.source else None}

    @staticmethod
    def _failure(status, started, base, model_name=""):  # noqa: ANN001
        return IntentResolutionMetadata(
            mode="deterministic_after_llm_failure", model_status=status, model_name=model_name,
            duration_ms=(time.perf_counter() - started) * 1000, **base,
        )


__all__ = ["ControlledIntentFallback", "IntentSemanticMerger", "assess_deterministic_parse"]
