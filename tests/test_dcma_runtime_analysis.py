from __future__ import annotations

import pytest

from fault_diagnosis.diagnosis.analysis import diagnose_dcma_runtime
from fault_diagnosis.diagnosis.contracts import DiagnosisRequest, KnowledgeStepArtifact, SqlStepArtifact


def _request() -> DiagnosisRequest:
    return DiagnosisRequest(
        user_message="生成dcma运行报告",
        user_identity="管理员",
        equipment_hint="G120电机1",
        metric_hint=None,
        fault_code_hint=None,
        time_range_hint="最近",
        needs_report=True,
        report_format="markdown",
        analysis_goal="生成运行状态报告",
    )


def _row(row_id: int) -> tuple[object, ...]:
    return (
        row_id,
        "2026/01/14 18:27:24",
        "G120电机1",
        "G120电机1",
        "2026/01/14",
        "18:27:24 000ms",
        "45",
        "0",
        "A07089",
        "5120",
        "8384",
        563.5,
        823.41,
        442.21,
        0,
        0,
        0,
        -200,
        20.09,
        23.3,
        0,
        0,
        0,
        "24.7",
        20.09,
        78.47,
        78.47,
        2,
        0.44,
        0,
        "2026-01-14 18:27:24",
    )


def _sql_artifact() -> SqlStepArtifact:
    return SqlStepArtifact(
        success=True,
        summary="查询 real_data_01 最近 50 条运行状态、异常码和关键运行指标。",
        raw_output=str([_row(index) for index in range(50, 0, -1)]),
    )


def _knowledge_artifact() -> KnowledgeStepArtifact:
    return KnowledgeStepArtifact(
        success=True,
        query="A07089 含义 处理",
        snippets=["故障码：A07089\n文档片段：A07089 单位转换激活异常。处理：恢复单位参数后重新激活功能块。"],
        raw_output="故障码：A07089\n文档片段：A07089 单位转换激活异常。处理：恢复单位参数后重新激活功能块。",
    )


def _feature(artifact, feature_id: str):
    return next(feature for feature in artifact.assessment.features if feature.feature_id == feature_id)


def test_dcma_runtime_detects_a07089_persistent_speed_load_temperature_and_currentness() -> None:
    artifact = diagnose_dcma_runtime(_sql_artifact(), _knowledge_artifact(), _request())
    assessment = artifact.assessment

    assert assessment.success is True
    assert "A07089" in assessment.event_codes
    assert any("A07089 50/50" in finding.summary for finding in assessment.findings)

    speed = _feature(artifact, "speed_deviation")
    assert speed.status == "warning"
    assert speed.value == pytest.approx(46.3, abs=0.1)

    load = _feature(artifact, "load_rate")
    assert load.status == "warning"
    assert load.window_max == pytest.approx(78.47)

    temperature = _feature(artifact, "temperature")
    assert temperature.status == "normal"
    assert "未超过温度阈值" in temperature.summary

    assert assessment.currentness_level == "stale"
    assert assessment.currentness_warning
    assert "不代表当前实时状态" in assessment.currentness_warning


def test_dcma_runtime_claims_all_have_supporting_evidence_ids() -> None:
    artifact = diagnose_dcma_runtime(_sql_artifact(), _knowledge_artifact(), _request())

    assert artifact.claims
    assert all(claim.supporting_evidence_ids for claim in artifact.claims)
