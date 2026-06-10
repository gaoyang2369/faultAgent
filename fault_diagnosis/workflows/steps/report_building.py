"""公共报告构建 step。"""

from __future__ import annotations

from typing import Any, Callable

from ..contracts import ReportStepArtifact


def build_skipped_report_artifact(save_result: str) -> ReportStepArtifact:
    """构建未生成报告时的统一 artifact。"""

    return ReportStepArtifact(
        success=False,
        report_filename=None,
        save_result=save_result,
        error=None,
    )


def save_markdown_report_artifact(
    report_payload: dict[str, Any],
    save_report: Callable[..., str],
) -> ReportStepArtifact:
    """基于统一 payload 调用报告保存器并输出 artifact。"""

    save_result = save_report(**report_payload)
    return ReportStepArtifact(
        success=True,
        report_filename=report_payload.get("report_filename"),
        save_result=save_result,
        error=None,
    )
