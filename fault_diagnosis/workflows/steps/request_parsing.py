"""公共请求理解 step。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..contracts import DiagnosisRequest


def build_request_from_payload(
    message: str,
    user_identity: str,
    payload: dict[str, Any],
    *,
    needs_report: bool | None,
    report_format: str = "markdown",
) -> DiagnosisRequest:
    """根据结构化 payload 构建统一 DiagnosisRequest。"""

    return DiagnosisRequest(
        user_message=message,
        user_identity=user_identity,
        equipment_hint=payload.get("equipment_hint"),
        metric_hint=payload.get("metric_hint"),
        fault_code_hint=payload.get("fault_code_hint"),
        time_range_hint=payload.get("time_range_hint"),
        needs_report=bool(payload.get("needs_report", False)) if needs_report is None else needs_report,
        report_format=report_format,
        analysis_goal=str(payload.get("analysis_goal") or message),
    )


async def parse_request_from_prompt(
    message: str,
    user_identity: str,
    prompt: str,
    invoke_json_model: Callable[[str], Awaitable[dict[str, Any]]],
    *,
    needs_report: bool | None,
    report_format: str = "markdown",
) -> DiagnosisRequest:
    """执行结构化理解 prompt，并输出统一请求对象。"""

    payload = await invoke_json_model(prompt)
    return build_request_from_payload(
        message,
        user_identity,
        payload,
        needs_report=needs_report,
        report_format=report_format,
    )
