"""Real RAG runtime node for Agent Engine V2."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import KnowledgeStepArtifact
from fault_diagnosis.domain.diagnosis.steps.knowledge_lookup import extract_fault_code_entries, extract_fault_codes_from_text
from fault_diagnosis.domain.security.runtime_context import reset_current_auth_context, set_current_auth_context
from fault_diagnosis.domain.diagnosis.evidence.knowledge import build_knowledge_evidence_items
from ...context.artifact_access import resolve_target_artifact
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
        reused_artifact = _previous_knowledge_artifact(node=node, state=state)
        if reused_artifact is not None:
            artifact = reused_artifact.model_copy(
                update={
                    "query": query,
                    "available": True,
                    "evidence_usable": True,
                }
            )
            evidence = models_to_dicts(build_knowledge_evidence_items(artifact, request=build_request(state, node, goal="知识库检索")))
            claims = _fault_code_explanation_claims(node=node, artifact=artifact, evidence=evidence)
            state.artifacts["knowledge_artifact"] = artifact
            return NodeExecutionOutput(
                output={
                    "success": True,
                    "artifact": model_to_dict(artifact),
                    "snippets": list(artifact.snippets),
                    "reused_artifact": True,
                    "source_artifact_refs": input_value(node, "source_artifact_refs", []) or [],
                },
                tool_call_refs=[],
                proposed_evidence=evidence,
                proposed_claims=claims,
                artifacts={"knowledge_artifact": artifact},
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

        classified = _classify_kb_output(raw_output)
        text = classified["text"]
        success = bool(classified["success"])
        error_code = str(classified.get("error_code") or "")
        error_message = str(classified.get("error") or "")
        top_k = max(1, min(int(input_value(node, "top_k", 3) or 3), 10))
        snippets = [block.strip() for block in text.split("\n\n") if block.strip()][:top_k] if success else []
        requested_codes = extract_fault_codes_from_text(query)
        entries = extract_fault_code_entries(text, requested_codes=requested_codes)
        fault_codes = [entry.code for entry in entries] or extract_fault_codes_from_text(text)
        artifact = KnowledgeStepArtifact(
            artifact_id=f"knowledge:{state.trace_id or state.request_id or state.plan.plan_id}:{str(node.get('node_id') or 'rag')}",
            success=success,
            query=query,
            snippets=snippets,
            raw_output=text,
            error=None if success else error_message or "知识库未返回内容",
            error_code=error_code,
            evidence_usable=success,
            available=success,
            hit_count=len(snippets) if success else 0,
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
                "retrieval_strategy": str(input_value(node, "retrieval_strategy", "") or "semantic_search"),
                "top_k": top_k,
            }
        }
        return NodeExecutionOutput(
            output={
                "success": success,
                "artifact": model_to_dict(artifact),
                "snippets": snippets,
                "error": {"code": error_code, "message": error_message} if error_code else None,
                "tool_metrics": tool_metrics,
            },
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


def _classify_kb_output(raw_output: Any) -> dict[str, Any]:
    text = str(raw_output or "").strip()
    if not text:
        return {
            "success": False,
            "text": "",
            "error_code": "empty_result",
            "error": "知识库未返回内容。",
        }
    if "超时" in text and ("知识库" in text or "检索" in text):
        return {
            "success": False,
            "text": "",
            "error_code": "kb_timeout",
            "error": "知识库检索超时，未获得可靠证据，请稍后重试或缩小查询范围。",
        }
    if any(marker in text for marker in ("知识库不可用", "检索失败", "工具执行失败")):
        return {
            "success": False,
            "text": "",
            "error_code": "tool_error",
            "error": text,
        }
    if "未检索到" in text:
        return {
            "success": False,
            "text": "",
            "error_code": "empty_result",
            "error": text,
        }
    return {"success": True, "text": text, "error_code": "", "error": ""}


def _previous_knowledge_artifact(*, node: dict[str, Any], state: RuntimeState) -> KnowledgeStepArtifact | None:
    semantic_intent = str(input_value(node, "semantic_intent", "") or "")
    if semantic_intent != "expand_previous_answer":
        return None
    source_refs = input_value(node, "source_artifact_refs", []) or []
    if not source_refs or not state.thread_id:
        return None
    target_id = str((source_refs[0] if isinstance(source_refs[0], dict) else {}).get("artifact_id") or "")
    access = resolve_target_artifact(
        thread_id=state.thread_id,
        artifact_id=target_id,
        auth=auth_context(state),
        expected_types={"knowledge_artifact"},
        expected_devices=[],
        require_complete_lineage=True,
    )
    if not access.allowed or access.record is None:
        return None
    artifact = _find_knowledge_artifact(access.record.payload)
    if artifact is None or not artifact.success:
        return None
    requested_codes = {str(code).upper() for code in input_value(node, "fault_code_refs", []) or []}
    artifact_codes = {str(code).upper() for code in artifact.fault_codes}
    if requested_codes and artifact_codes and not requested_codes.intersection(artifact_codes):
        return None
    return artifact


def _find_knowledge_artifact(value: Any) -> KnowledgeStepArtifact | None:
    if isinstance(value, KnowledgeStepArtifact):
        return value
    if isinstance(value, dict):
        if "knowledge_artifact" in value:
            found = _find_knowledge_artifact(value.get("knowledge_artifact"))
            if found is not None:
                return found
        if {"success", "query"}.issubset(value.keys()) and ("raw_output" in value or "snippets" in value):
            try:
                return KnowledgeStepArtifact.model_validate(value)
            except Exception:
                pass
        for item in value.values():
            found = _find_knowledge_artifact(item)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_knowledge_artifact(item)
            if found is not None:
                return found
    return None


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
