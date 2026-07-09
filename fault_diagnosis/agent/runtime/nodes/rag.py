"""Real RAG runtime node for Agent Engine V2."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import KnowledgeStepArtifact
from fault_diagnosis.domain.diagnosis.steps.knowledge_lookup import extract_fault_code_entries, extract_fault_codes_from_text
from fault_diagnosis.domain.security.runtime_context import reset_current_auth_context, set_current_auth_context
from fault_diagnosis.domain.diagnosis.evidence.knowledge import build_knowledge_evidence_items
from fault_diagnosis.agent.evidence.claims import build_v2_claim
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from ..tool_runtime import ToolRuntime
from .base import auth_context, build_request, input_value, model_to_dict, models_to_dicts


class RagNode:
    node_type = "rag"

    def __init__(self, tool_runtime: ToolRuntime) -> None:
        self.tool_runtime = tool_runtime

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        query = str(input_value(node, "query", "") or input_value(node, "kb_query", "") or "").strip()
        if not query:
            return NodeExecutionOutput(
                status="failed",
                output={"success": False, "error": "missing_query"},
                error={"code": "missing_query", "message": "RAG node requires inputs.query."},
            )
        auth = auth_context(state)
        token = set_current_auth_context(auth)
        started_at = _utc_now_iso()
        started = time.monotonic()
        try:
            raw_output = self.tool_runtime.query_knowledge_base(query)
        finally:
            duration_ms = max(0.1, round((time.monotonic() - started) * 1000, 1))
            ended_at = _utc_now_iso()
            reset_current_auth_context(token)

        text = str(raw_output or "").strip()
        success = bool(text) and "未检索到" not in text and "知识库不可用" not in text
        snippets = [block.strip() for block in text.split("\n\n") if block.strip()][:3]
        requested_codes = extract_fault_codes_from_text(query)
        entries = extract_fault_code_entries(text, requested_codes=requested_codes)
        fault_codes = [entry.code for entry in entries] or extract_fault_codes_from_text(text)
        artifact = KnowledgeStepArtifact(
            success=success,
            query=query,
            snippets=snippets,
            raw_output=text,
            error=None if success else text or "知识库未返回内容",
            hit_count=len(snippets),
            fault_codes=fault_codes,
            fault_code_entries=entries,
        )
        request = build_request(state, node, goal="知识库检索")
        state.artifacts["knowledge_artifact"] = artifact
        evidence = models_to_dicts(build_knowledge_evidence_items(artifact, request=request))
        claims = _fault_code_explanation_claims(node=node, artifact=artifact, evidence=evidence)
        tool_metrics = {
            "kb.search": {
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": duration_ms,
                "latency_ms": duration_ms,
                "phase_latencies_ms": {"total": duration_ms},
            }
        }
        return NodeExecutionOutput(
            output={"success": success, "artifact": model_to_dict(artifact), "snippets": snippets, "tool_metrics": tool_metrics},
            tool_call_refs=["query_knowledge_base"],
            proposed_evidence=evidence,
            proposed_claims=claims,
            artifacts={"knowledge_artifact": artifact},
        )


def _fault_code_explanation_claims(
    *,
    node: dict[str, Any],
    artifact: KnowledgeStepArtifact,
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not artifact.success or not evidence:
        return []
    supporting_ids = [str(item.get("evidence_id") or "") for item in evidence if item.get("evidence_id")]
    if not supporting_ids:
        return []
    entry = next((item for item in artifact.fault_code_entries if item.match_type == "exact_match"), None)
    if entry is None and artifact.fault_code_entries:
        entry = artifact.fault_code_entries[0]
    if entry is not None:
        meaning = entry.meaning or entry.title or "手册未明确给出"
        statement = f"{entry.code}：{meaning}"
    else:
        codes = "、".join(artifact.fault_codes)
        statement = f"{codes or artifact.query} 的说明来自知识库检索结果。"
    node_id = str(node.get("node_id") or "rag")
    return [
        build_v2_claim(
            claim_id=f"claim_{node_id}_fault_code_explanation_001",
            claim_type="fault_code_explanation",
            statement=statement,
            supporting_evidence_ids=supporting_ids,
            status="final",
            created_by=node_id,
            confidence="high",
        )
    ]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
