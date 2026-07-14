"""Deterministic entity extraction driven by the canonical rule catalog."""

from __future__ import annotations

import re
from typing import Any

from fault_diagnosis.agent.canonical_turn.rule_catalog import RULE_CATALOG
from fault_diagnosis.domain.canonical_turn import EntitySpan


_ENTITY_CATEGORIES = {
    "fault_code_shape",
    "device_pattern",
    "time_pattern",
    "artifact_reference",
    "source_marker",
    "correction_marker",
    "deictic_marker",
}


def compile_rule(rule: dict[str, Any]) -> re.Pattern[str]:
    flags = re.IGNORECASE if rule.get("flags") == "IGNORECASE" else 0
    return re.compile(str(rule["pattern"]), flags)


def rules_for(category: str) -> tuple[dict[str, Any], ...]:
    return tuple(rule for rule in RULE_CATALOG if rule["category"] == category)


class DeterministicEntityExtractor:
    def __init__(self) -> None:
        self._rules = tuple(rule for rule in RULE_CATALOG if rule["category"] in _ENTITY_CATEGORIES)

    def extract(self, text: str) -> list[EntitySpan]:
        matches: list[tuple[int, int, str, str, float, dict[str, Any]]] = []
        for rule in self._rules:
            pattern = compile_rule(rule)
            capture_group = int(rule.get("capture_group", 0))
            for match in pattern.finditer(text):
                raw = match.group(capture_group)
                start, end = match.span(capture_group)
                value = self._normalize(raw, str(rule.get("normalizer") or "strip"))
                matches.append(
                    (
                        start,
                        end,
                        str(rule["semantic_value"]),
                        value,
                        float(rule["confidence"]),
                        dict(rule.get("attributes") or {}),
                    )
                )
        matches.sort(key=lambda item: (item[0], item[1], item[2]))
        entities: list[EntitySpan] = []
        seen: set[tuple[int, int, str]] = set()
        for start, end, kind, value, confidence, attributes in matches:
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
                    confidence=confidence,
                    attributes=attributes,
                )
            )
        return entities

    @staticmethod
    def _normalize(value: str, normalizer: str) -> str:
        if normalizer == "uppercase":
            return value.upper()
        if normalizer == "remove_spaces":
            return re.sub(r"\s+", "", value)
        return value.strip()
