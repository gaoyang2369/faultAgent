"""Deterministic comparison over independently produced runtime assessments."""

from __future__ import annotations

from fault_diagnosis.domain.artifacts import ComparisonArtifactPayload, SqlArtifactPayload
from fault_diagnosis.domain.diagnosis.runtime_status import (
    ComparisonFinding,
    RuntimeComparisonArtifact,
    RuntimeStatusAssessment,
)
from fault_diagnosis.agent.evidence.claims import build_v2_claim

from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from ...artifacts import ArtifactPayloadError, load_bound_artifacts, require_payload
from .base import model_to_dict


class ComparisonNode:
    node_type = "comparison"

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        try:
            sql_sources = load_bound_artifacts(node=node, state=state, roles=("comparison_member",))
            assessments = [require_payload(envelope, SqlArtifactPayload).runtime_status_assessment for envelope in sql_sources]
        except ArtifactPayloadError as exc:
            return NodeExecutionOutput(
                status="blocked",
                output={"success": False, "artifact_access_error": exc.code},
                error={"code": exc.code, "message": exc.message},
            )
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
        for dimension, aliases in (
            ("speed_deviation", ("speed_deviation_percent", "speed_deviation", "速度偏差")),
            ("load", ("max_load_rate", "load_rate", "load", "负载")),
            ("temperature", ("temperature_level", "temperature", "温度")),
        ):
            values = _metric_values(assessments, aliases)
            if values:
                findings.append(
                    ComparisonFinding(
                        dimension=dimension,
                        values_by_device=values,
                        conclusion=_metric_conclusion(values),
                    )
                )
        findings.extend(
            [
                ComparisonFinding(
                    dimension="event_codes",
                    values_by_device={item.device: list(item.event_codes) for item in assessments},
                    conclusion="已按设备分别列出事件码。",
                ),
                ComparisonFinding(
                    dimension="sample_time",
                    values_by_device={
                        item.device: item.data_basis.latest_sample_time.isoformat() if item.data_basis.latest_sample_time else ""
                        for item in assessments
                    },
                    conclusion="样本时间按设备独立披露。",
                ),
                ComparisonFinding(
                    dimension="resolution_mode",
                    values_by_device={item.device: item.data_basis.resolution_mode for item in assessments},
                    conclusion="数据解析模式按设备独立披露。",
                ),
            ]
        )
        source_ids = [envelope.artifact_id for envelope in sql_sources]
        similarities, differences = _similarities_and_differences(findings)
        ranking = _ranking(assessments)
        artifact = RuntimeComparisonArtifact(
            devices=[item.device for item in assessments],
            assessments=assessments,
            device_summaries=[_device_summary(item) for item in assessments],
            comparison_dimensions=findings,
            similarities=similarities,
            differences=differences,
            ranking=ranking,
            conclusion=_status_conclusion(status_values),
            source_artifact_ids=source_ids,
        )
        supporting = [
            evidence_id
            for item in assessments
            for evidence_id in item.supporting_evidence_ids[:1]
            if evidence_id
        ]
        claim = build_v2_claim(
            claim_id=f"claim_{node.get('node_id') or 'comparison'}_runtime_comparison",
            claim_type="runtime_comparison",
            statement=artifact.conclusion,
            supporting_evidence_ids=supporting,
            status="final",
            created_by=str(node.get("node_id") or "comparison"),
            confidence="high" if len(supporting) >= len(assessments) else "medium",
        )
        return NodeExecutionOutput(
            output={"success": True, "artifact": model_to_dict(artifact)},
            proposed_claims=[claim],
            artifact_payload=ComparisonArtifactPayload(comparison_artifact=artifact),
        )


def _status_conclusion(values: dict[str, str]) -> str:
    abnormal = [device for device, status in values.items() if status == "abnormal"]
    attention = [device for device, status in values.items() if status == "attention"]
    if abnormal:
        peers = [device for device in values if device not in abnormal]
        return f"{'、'.join(abnormal)} 存在异常" + (f"，风险高于 {'、'.join(peers)}。" if peers else "。")
    if attention:
        peers = [device for device in values if device not in attention]
        return f"{'、'.join(attention)} 需要关注" + (f"；{'、'.join(peers)} 未显示同等级异常。" if peers else "。")
    if len(set(values.values())) == 1:
        return "两台设备当前评估等级一致。"
    return "设备运行状态存在差异，详见分项结果。"


def _metric_values(assessments: list[RuntimeStatusAssessment], aliases: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    wanted = {item.casefold() for item in aliases}
    for assessment in assessments:
        finding = next((item for item in assessment.key_metrics if item.metric.casefold() in wanted), None)
        if finding is not None:
            result[assessment.device] = {"value": finding.value, "unit": finding.unit, "level": finding.level}
    return result


def _metric_conclusion(values: dict[str, Any]) -> str:
    rendered = [str(item.get("value")) for item in values.values() if isinstance(item, dict)]
    return "该指标一致。" if rendered and len(set(rendered)) == 1 else "该指标存在差异。"


def _device_summary(assessment: RuntimeStatusAssessment) -> dict[str, Any]:
    return {
        "device": assessment.device,
        "runtime_status": assessment.runtime_status,
        "event_codes": list(assessment.event_codes),
        "sample_count": assessment.sample_count,
        "sample_time": assessment.data_basis.latest_sample_time.isoformat() if assessment.data_basis.latest_sample_time else "",
        "resolution_mode": assessment.data_basis.resolution_mode,
        "key_metrics": [model_to_dict(item) for item in assessment.key_metrics],
        "supporting_evidence_ids": list(assessment.supporting_evidence_ids),
    }


def _similarities_and_differences(findings: list[ComparisonFinding]) -> tuple[list[str], list[str]]:
    similarities: list[str] = []
    differences: list[str] = []
    for item in findings:
        values = [str(value) for value in item.values_by_device.values()]
        target = similarities if values and len(set(values)) == 1 else differences
        target.append(f"{item.dimension}：{item.conclusion}")
    return similarities, differences


def _ranking(assessments: list[RuntimeStatusAssessment]) -> list[dict[str, Any]]:
    scores = {"abnormal": 3, "attention": 2, "unknown": 1, "normal": 0}
    values = [scores[item.runtime_status] for item in assessments]
    if len(set(values)) <= 1:
        return []
    ordered = sorted(assessments, key=lambda item: scores[item.runtime_status], reverse=True)
    return [
        {"rank": index, "device": item.device, "runtime_status": item.runtime_status}
        for index, item in enumerate(ordered, start=1)
    ]
