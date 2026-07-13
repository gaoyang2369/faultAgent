"""Deterministic comparison over independently produced runtime assessments."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.runtime_status import (
    ComparisonFinding,
    RuntimeComparisonArtifact,
    RuntimeStatusAssessment,
)

from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from .base import model_to_dict


class ComparisonNode:
    node_type = "comparison"

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        assessments = _assessments(state.artifacts.get("runtime_status_assessments"))
        if len(assessments) < 2:
            return NodeExecutionOutput(
                status="failed",
                output={"success": False, "available_assessments": len(assessments)},
                error={"code": "comparison_requires_two_assessments", "message": "运行比较至少需要两台设备的状态评估。"},
            )
        status_values = {item.device: item.runtime_status for item in assessments}
        sample_values = {item.device: item.sample_count for item in assessments}
        findings = [
            ComparisonFinding(
                dimension="runtime_status",
                values_by_device=status_values,
                conclusion=_status_conclusion(status_values),
            ),
            ComparisonFinding(
                dimension="sample_count",
                values_by_device=sample_values,
                conclusion="各设备样本数已独立统计。",
            ),
        ]
        source_ids = [str(item) for item in state.artifacts.get("sql_artifact_ids", []) if str(item)]
        artifact = RuntimeComparisonArtifact(
            devices=[item.device for item in assessments],
            assessments=assessments,
            comparison_dimensions=findings,
            conclusion=_status_conclusion(status_values),
            source_artifact_ids=source_ids,
        )
        state.artifacts["comparison_artifact"] = artifact
        return NodeExecutionOutput(
            output={"success": True, "artifact": model_to_dict(artifact)},
            artifacts={"comparison_artifact": artifact},
        )


def _assessments(value: Any) -> list[RuntimeStatusAssessment]:
    raw = list(value.values()) if isinstance(value, dict) else list(value or []) if isinstance(value, list) else []
    result: list[RuntimeStatusAssessment] = []
    for item in raw:
        if isinstance(item, RuntimeStatusAssessment):
            result.append(item)
        elif isinstance(item, dict):
            result.append(RuntimeStatusAssessment.model_validate(item))
    return result


def _status_conclusion(values: dict[str, str]) -> str:
    abnormal = [device for device, status in values.items() if status == "abnormal"]
    attention = [device for device, status in values.items() if status == "attention"]
    if abnormal:
        return f"{ '、'.join(abnormal) } 存在异常，风险高于其余比较设备。"
    if attention:
        return f"{ '、'.join(attention) } 需要关注，其他设备未显示同等级异常。"
    if len(set(values.values())) == 1:
        return "两台设备当前评估等级一致。"
    return "设备运行状态存在差异，详见分项结果。"

