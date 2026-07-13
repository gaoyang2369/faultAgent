"""Real deterministic analysis runtime node for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.analysis.dcma_runtime import diagnose_dcma_runtime
from fault_diagnosis.domain.artifacts import AnalysisArtifactPayload, KnowledgeArtifactPayload, SqlArtifactPayload
from fault_diagnosis.domain.diagnosis.contracts import KnowledgeStepArtifact
from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from .base import build_request, model_to_dict, models_to_dicts
from ...artifacts import ArtifactPayloadError, hydrate_artifact_lineage, require_payload, source_envelopes


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
        for key in ("source_artifact_id", "target_artifact_id"):
            source_id = str(inputs.get(key) or "")
            if source_id and not hydrate_artifact_lineage(state, source_id):
                return NodeExecutionOutput(
                    status="blocked",
                    output={"success": False, "artifact_access_error": "artifact_payload_invalid"},
                    error={"code": "artifact_payload_invalid", "message": "目标 Artifact typed payload 无法加载。"},
                )
        sources = source_envelopes("analysis", node=node, state=state)
        sql_sources = [item for item in sources if item.artifact_type == "sql_artifact"]
        if len(sql_sources) != 1:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "sql_source_count": len(sql_sources)},
                error={
                    "code": "artifact_payload_invalid" if not sql_sources else "artifact_source_ambiguous",
                    "message": "Analysis requires exactly one typed SQL source.",
                },
            )
        try:
            sql_artifact = require_payload(sql_sources[0], SqlArtifactPayload).sql_artifact
            knowledge_sources = [item for item in sources if item.artifact_type == "knowledge_artifact"]
            knowledge_artifact = (
                require_payload(knowledge_sources[0], KnowledgeArtifactPayload).knowledge_artifact
                if len(knowledge_sources) == 1
                else KnowledgeStepArtifact(success=False, query="", error="missing_knowledge_artifact")
            )
        except ArtifactPayloadError as exc:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "artifact_access_error": exc.code},
                error={"code": exc.code, "message": exc.message},
            )
        request = build_request(state, node, goal="DCMA 运行诊断分析")
        structured = diagnose_dcma_runtime(sql_artifact, knowledge_artifact, request)
        structured.analysis_artifact.artifact_id = str(inputs.get("artifact_id") or node.get("artifact_id") or "")
        return NodeExecutionOutput(
            output={
                "success": structured.analysis_artifact.success,
                "analysis_artifact": model_to_dict(structured.analysis_artifact),
                "assessment": model_to_dict(structured.assessment),
            },
            proposed_evidence=models_to_dicts(structured.evidence_items),
            proposed_claims=models_to_dicts(structured.claims),
            artifact_payload=AnalysisArtifactPayload(structured_analysis=structured),
        )
