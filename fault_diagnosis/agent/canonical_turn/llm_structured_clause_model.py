"""OpenAI-compatible adapter for the existing StructuredClauseModel protocol."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from fault_diagnosis.agent.canonical_turn.intent_prompt import INTENT_SHADOW_SYSTEM_PROMPT
from fault_diagnosis.agent.canonical_turn.model_clause_parser import ClauseModelRequest


class LLMStructuredClauseModel:
    def __init__(self, client: Any) -> None:
        self._client = client

    def parse(self, request: ClauseModelRequest) -> dict[str, Any]:
        model_input = {
            "schema_version": request.schema_version,
            "text": request.text,
            "deterministic_entities": list(request.deterministic_entities),
            "allowed_capabilities": list(request.allowed_capabilities),
            "allowed_source_kinds": list(request.allowed_source_kinds),
            "output_schema": {
                "schema_version": request.response_schema,
                "clauses": [{
                    "clause_index": 0, "text": "exact substring", "start": 0, "end": 1,
                    "action": {"capability": "allowlisted value", "confidence": 0.0, "entity_refs": [], "inferred": False},
                    "source": {"source_kind": "current_message", "entity_refs": [], "relation": "requested_action"},
                    "slot": {}, "linker": None,
                    "shadow_metadata": {
                        "requested": True, "negated": False, "conditional": False,
                        "sequence_index": None, "depends_on": [], "relation": "requested_action",
                        "unsupported_by_deterministic_action": False,
                    },
                }],
            },
        }
        try:
            response = self._client.invoke([
                SystemMessage(content=INTENT_SHADOW_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(model_input, ensure_ascii=False, separators=(",", ":"))),
            ])
        except Exception as exc:
            if "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower():
                raise TimeoutError("intent model request timed out") from exc
            raise
        content = getattr(response, "content", response)
        if not isinstance(content, str):
            raise ValueError("intent model returned non-text content")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("intent model returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("intent model JSON root must be an object")
        return payload
