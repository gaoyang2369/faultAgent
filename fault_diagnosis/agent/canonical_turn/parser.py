"""当前消息的确定性基础解析，不直接调用模型。"""

from __future__ import annotations

import re

from fault_diagnosis.agent.canonical_turn.clause_parser import DeterministicClauseParser
from fault_diagnosis.agent.canonical_turn.entity_extractor import DeterministicEntityExtractor
from fault_diagnosis.domain.canonical_turn import CurrentUtteranceParse, IntentResolutionMetadata


_UNSUPPORTED_HIGH_RISK_ACTION = re.compile(
    r"(?:执行|立即|现在|马上|请|帮我|给我)?(?:停机|关机|重启|复位(?!方法|步骤|说明)|"
    r"(?:修改|调整|设置).{0,8}(?:参数|阈值|限值|转速|电流))"
)


class CurrentUtteranceParser:
    """Parse one utterance without consulting conversation or production frames."""

    def __init__(self) -> None:
        self._entity_extractor = DeterministicEntityExtractor()
        self._clause_parser = DeterministicClauseParser()

    def parse(self, text: str) -> CurrentUtteranceParse:
        raw_text = str(text or "")
        entities = self._entity_extractor.extract(raw_text)
        unsupported_high_risk = bool(_UNSUPPORTED_HIGH_RISK_ACTION.search(raw_text))
        clauses = [] if unsupported_high_risk else self._clause_parser.parse(raw_text, entities)
        resolution = IntentResolutionMetadata(
            deterministic_capabilities=[clause.action.capability for clause in clauses if clause.action]
        )
        confident = any(clause.action for clause in clauses) or bool(entities)
        deterministic_confident = bool(resolution.deterministic_capabilities) or bool(entities)
        model_used = False
        model_rejection_reason = None

        clarification_needs: list[str] = []
        if unsupported_high_risk:
            clarification_needs.append("unsupported_high_risk_action")
        if not confident:
            clarification_needs.append("action_or_reference_not_determined")
        return CurrentUtteranceParse(
            raw_text=raw_text,
            entities=entities,
            clauses=clauses,
            clarification_needs=list(dict.fromkeys(clarification_needs)),
            deterministic_confident=deterministic_confident,
            model_used=model_used,
            model_rejection_reason=model_rejection_reason,
            intent_resolution=resolution,
        )


__all__ = ["CurrentUtteranceParser"]
