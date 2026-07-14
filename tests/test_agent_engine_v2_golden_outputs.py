from __future__ import annotations

from pathlib import Path

import pytest

from fault_diagnosis.agent.contracts import DeliverableResult
from fault_diagnosis.agent.output.presenter import CompositePresenter


_GOLDEN_DIR = Path(__file__).parent / "golden" / "agent_engine_v2"


def _deliverable(goal: str, kind: str, payload: dict) -> DeliverableResult:
    capability = {
        "fault_code_explanation": "explain_fault_code",
        "runtime_status": "check_runtime_status",
        "runtime_comparison": "compare_runtime_status",
        "diagnosis": "diagnose_fault",
        "recommendations": "resolution_recommendation",
        "report": "generate_report",
        "workorder_draft": "create_workorder_draft",
    }[kind]
    return DeliverableResult(
        goal_id=goal,
        capability=capability,
        status="completed",
        structured_content=payload,
    )


def _status() -> DeliverableResult:
    return _deliverable(
        "goal_status",
        "runtime_status",
        {
            "assessments": [
                {
                    "device": "G120电机1",
                    "runtime_status": "attention",
                    "data_basis": {
                        "resolution_mode": "latest_available_fallback",
                        "resolved_window": {"start": "2026-07-13T09:00:00", "end": "2026-07-13T10:00:00"},
                        "latest_sample_time": "2026-07-13T10:00:00",
                    },
                    "sample_count": 60,
                    "key_findings": ["速度偏差达到关注阈值。"],
                    "limitations": ["非实时历史最新样本。"],
                }
            ]
        },
    )


def _fault_code() -> DeliverableResult:
    return _deliverable(
        "goal_fault",
        "fault_code_explanation",
        {
            "fault_code_entries": [
                {"code": "A07089", "meaning": "速度偏差", "cause": "负载突变", "remedy": "检查负载和编码器"}
            ]
        },
    )


def _diagnosis() -> DeliverableResult:
    return _deliverable(
        "goal_diagnosis",
        "diagnosis",
        {"conclusion": "速度反馈与给定偏差超限，证据支持负载或反馈链路异常。"},
    )


def _recommendations() -> DeliverableResult:
    return _deliverable(
        "goal_recommendations",
        "recommendations",
        {"recommendations": ["检查机械负载。", "检查编码器反馈。"]},
    )


def _cases() -> dict[str, list[DeliverableResult]]:
    return {
        "status": [_status()],
        "fault-code": [_fault_code()],
        "composite-diagnosis": [_fault_code(), _status(), _diagnosis(), _recommendations()],
        "comparison": [
            _deliverable(
                "goal_comparison",
                "runtime_comparison",
                {
                    "conclusion": "G120电机1 风险高于 J1号机。",
                    "comparison_dimensions": [
                        {
                            "dimension": "速度偏差",
                            "values_by_device": {"G120电机1": "8.0%", "J1号机": "2.7%"},
                            "conclusion": "该指标存在差异。",
                        },
                        {
                            "dimension": "负载",
                            "values_by_device": {"G120电机1": "88%", "J1号机": "64%"},
                            "conclusion": "该指标存在差异。",
                        },
                    ],
                },
            )
        ],
        "diagnosis-report": [
            _diagnosis(),
            _deliverable("goal_report", "report", {"report_url": "/reports/convergence-report.html"}),
        ],
        "pending-workorder": [
            _deliverable(
                "goal_workorder",
                "workorder_draft",
                {"status": "pending_manual_confirmation", "dispatch_performed": False},
            )
        ],
    }


@pytest.mark.parametrize("name", sorted(_cases()))
def test_composite_content_matches_golden(name: str) -> None:
    expected = (_GOLDEN_DIR / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")
    assert CompositePresenter().present(deliverables=_cases()[name], status="completed").content == expected
