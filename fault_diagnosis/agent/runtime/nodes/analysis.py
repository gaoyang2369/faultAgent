"""Real deterministic analysis runtime node for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.analysis.dcma_runtime import diagnose_dcma_runtime
from fault_diagnosis.domain.diagnosis.contracts import KnowledgeStepArtifact, SqlStepArtifact
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from .base import build_request, model_to_dict, models_to_dicts


class AnalysisNode:
    node_type = "analysis"

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        inputs = dict(node.get("inputs") or {})
        access_error = str(inputs.get("artifact_access_error") or "")
        if access_error:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "artifact_access_error": access_error},
                error={"code": access_error, "message": "目标 Artifact 精确读取或 lineage 校验失败。"},
            )
        source_payload = inputs.get("source_artifact_payload")
        sql_artifact = _sql_artifact(source_payload if inputs.get("source_artifact_type") == "sql_artifact" else state.artifacts.get("sql_artifact"))
        knowledge_artifact = _knowledge_artifact(state.artifacts.get("knowledge_artifact"))
        request = build_request(state, node, goal="DCMA 运行诊断分析")
        structured = diagnose_dcma_runtime(sql_artifact, knowledge_artifact, request)
        structured.analysis_artifact.artifact_id = str(inputs.get("artifact_id") or node.get("artifact_id") or "")
        state.artifacts["analysis_artifact"] = structured.analysis_artifact
        state.artifacts["structured_analysis"] = structured
        return NodeExecutionOutput(
            output={
                "success": structured.analysis_artifact.success,
                "analysis_artifact": model_to_dict(structured.analysis_artifact),
                "assessment": model_to_dict(structured.assessment),
            },
            proposed_evidence=models_to_dicts(structured.evidence_items),
            proposed_claims=models_to_dicts(structured.claims),
            artifacts={
                "analysis_artifact": structured.analysis_artifact,
                "structured_analysis": structured,
            },
        )


def _sql_artifact(value: Any) -> SqlStepArtifact:
    if isinstance(value, SqlStepArtifact):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("sql_artifact"), dict):
            return SqlStepArtifact.model_validate(value["sql_artifact"])
        return SqlStepArtifact.model_validate(value)
    return SqlStepArtifact(
        success=False,
        summary="SQL 节点未提供运行数据。",
        error="missing_sql_artifact",
        data_state="missing",
    )


def _knowledge_artifact(value: Any) -> KnowledgeStepArtifact:
    if isinstance(value, KnowledgeStepArtifact):
        return value
    if isinstance(value, dict):
        return KnowledgeStepArtifact.model_validate(value)
    return KnowledgeStepArtifact(
        success=False,
        query="",
        raw_output="",
        error="missing_knowledge_artifact",
    )
