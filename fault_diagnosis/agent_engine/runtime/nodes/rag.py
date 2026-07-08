"""Real RAG runtime node for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from ....diagnosis.contracts import KnowledgeStepArtifact
from ....diagnosis.steps.knowledge_lookup import extract_fault_codes_from_text
from ....security.runtime_context import reset_current_auth_context, set_current_auth_context
from ....diagnosis.evidence.knowledge import build_knowledge_evidence_items
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
        try:
            raw_output = self.tool_runtime.query_knowledge_base(query)
        finally:
            reset_current_auth_context(token)

        text = str(raw_output or "").strip()
        success = bool(text) and "未检索到" not in text and "知识库不可用" not in text
        snippets = [block.strip() for block in text.split("\n\n") if block.strip()][:3]
        artifact = KnowledgeStepArtifact(
            success=success,
            query=query,
            snippets=snippets,
            raw_output=text,
            error=None if success else text or "知识库未返回内容",
            hit_count=len(snippets),
            fault_codes=extract_fault_codes_from_text(text),
        )
        request = build_request(state, node, goal="知识库检索")
        state.artifacts["knowledge_artifact"] = artifact
        evidence = models_to_dicts(build_knowledge_evidence_items(artifact, request=request))
        return NodeExecutionOutput(
            output={"success": success, "artifact": model_to_dict(artifact), "snippets": snippets},
            tool_call_refs=["query_knowledge_base"],
            proposed_evidence=evidence,
            artifacts={"knowledge_artifact": artifact},
        )
