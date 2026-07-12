"""Typed data-resolution and runtime-status domain contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import Claim, ClaimConfidence, EvidenceItem


ResolutionStrategy = Literal["realtime_then_latest", "realtime_only", "latest_only"]
ResolutionMode = Literal["realtime_window", "latest_available_fallback", "no_data"]
QueryStatus = Literal["success", "empty", "failed"]
RuntimeStatus = Literal["normal", "attention", "abnormal", "unknown"]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TimeWindow(_Contract):
    start: datetime
    end: datetime


class DataBasis(_Contract):
    requested_window: TimeWindow | None = None
    resolved_window: TimeWindow | None = None
    resolution_mode: ResolutionMode = "no_data"
    latest_sample_time: datetime | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    data_environment: Literal["simulation", "production"] = "simulation"
    freshness: Literal["realtime", "recent", "historical_latest", "unknown"] = "unknown"
    usable_for_status: bool = False
    usable_for_diagnosis: bool = False
    usable_for_report: bool = False
    usable_for_workorder_draft: bool = False


class MetricFinding(_Contract):
    metric: str
    value: float | str | None = None
    unit: str = ""
    threshold: float | str | None = None
    level: Literal["normal", "attention", "strong_abnormal", "unknown"] = "unknown"
    summary: str
    evidence_id: str


class RuntimeStatusAssessment(_Contract):
    device: str
    query_status: QueryStatus
    runtime_status: RuntimeStatus
    status_reasons: list[str] = Field(default_factory=list)
    data_basis: DataBasis
    sample_count: int = Field(default=0, ge=0)
    event_codes: list[str] = Field(default_factory=list)
    abnormal_sample_count: int | None = None
    key_metrics: list[MetricFinding] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class DataResolutionPolicy(_Contract):
    strategy: ResolutionStrategy = "realtime_then_latest"
    data_environment: Literal["simulation", "production"] = "simulation"
    high_risk_requires_realtime: bool = False

    @property
    def queries_realtime(self) -> bool:
        return self.strategy in {"realtime_then_latest", "realtime_only"}

    @property
    def allows_latest(self) -> bool:
        return self.strategy in {"realtime_then_latest", "latest_only"}

    def resolve(
        self,
        *,
        requested_window: TimeWindow | None,
        realtime_row_count: int = 0,
        realtime_window: TimeWindow | None = None,
        latest_sample_time: datetime | None = None,
        fallback_window: TimeWindow | None = None,
        fallback_row_count: int = 0,
    ) -> DataBasis:
        if self.queries_realtime and realtime_row_count > 0:
            resolved = realtime_window or requested_window
            return DataBasis(
                requested_window=requested_window,
                resolved_window=resolved,
                resolution_mode="realtime_window",
                latest_sample_time=latest_sample_time or (resolved.end if resolved else None),
                data_environment=self.data_environment,
                freshness="realtime",
                usable_for_status=True,
                usable_for_diagnosis=True,
                usable_for_report=True,
                usable_for_workorder_draft=True,
            )
        if self.allows_latest and latest_sample_time is not None and fallback_row_count > 0:
            return DataBasis(
                requested_window=requested_window,
                resolved_window=fallback_window,
                resolution_mode="latest_available_fallback",
                latest_sample_time=latest_sample_time,
                fallback_used=True,
                fallback_reason="requested_window_empty" if self.queries_realtime else "latest_only_policy",
                data_environment=self.data_environment,
                freshness="historical_latest",
                usable_for_status=True,
                usable_for_diagnosis=True,
                usable_for_report=True,
                usable_for_workorder_draft=True,
            )
        return DataBasis(
            requested_window=requested_window,
            resolution_mode="no_data",
            latest_sample_time=latest_sample_time,
            fallback_used=False,
            fallback_reason=(
                "latest_available_window_empty"
                if latest_sample_time is not None
                else "no_authorized_data"
                if self.allows_latest
                else "fallback_disabled_by_policy"
            ),
            data_environment=self.data_environment,
            freshness="unknown",
        )


def build_runtime_status_assessment(
    *,
    device: str,
    query_status: QueryStatus,
    data_basis: DataBasis,
    evidence_items: list[EvidenceItem],
) -> RuntimeStatusAssessment:
    supporting_ids = [item.evidence_id for item in evidence_items if item.evidence_id]
    findings = [item.summary for item in evidence_items if item.summary and item.evidence_type != "stale_evidence_disclosure"]
    sample_count = 0
    abnormal_sample_count: int | None = None
    event_codes: list[str] = []
    status_abnormal = False
    status_evaluable = False
    active_fault = False
    metrics: list[MetricFinding] = []

    for item in evidence_items:
        content = item.content if isinstance(item.content, dict) else {}
        sample_count = max(sample_count, _int(content.get("sample_count")))
        if item.evidence_type == "device_status":
            status_text = str(content.get("latest_status") or "").strip().lower()
            status_evaluable = status_evaluable or status_text not in {"", "-", "unknown", "未知", "none"}
            status_abnormal = status_abnormal or any(word in status_text for word in ("异常", "故障", "fault", "alarm", "trip"))
        if item.evidence_type == "alarm_event":
            event_codes.extend(_texts(content.get("effective_codes")))
            active_fault = active_fault or _int(content.get("active_fault_count")) > 0
            if content.get("abnormal_count") is not None:
                abnormal_sample_count = _int(content.get("abnormal_count"))
        if item.evidence_type in {"timeseries_feature", "metric_snapshot"}:
            raw_status = str(content.get("status") or "unknown")
            level = "attention" if raw_status == "abnormal" else "normal" if raw_status == "normal" else "unknown"
            metrics.append(
                MetricFinding(
                    metric=str(content.get("metric") or _metric_name(content, item.evidence_type)),
                    value=content.get("value", _metric_value(content)),
                    unit=str(content.get("unit") or ""),
                    threshold=content.get("threshold"),
                    level=level,
                    summary=item.summary,
                    evidence_id=item.evidence_id,
                )
            )

    event_codes = list(dict.fromkeys(event_codes))
    attention_metrics = [item for item in metrics if item.level == "attention"]
    reasons: list[str] = []
    if query_status != "success" or not data_basis.usable_for_status or sample_count <= 0:
        runtime_status: RuntimeStatus = "unknown"
        reasons.append("insufficient_status_evidence")
    elif active_fault:
        runtime_status = "abnormal"
        reasons.append("active_fault_present")
    elif status_abnormal:
        runtime_status = "abnormal"
        reasons.append("abnormal_runtime_status")
    elif len(attention_metrics) >= 2:
        runtime_status = "abnormal"
        reasons.append("multiple_strong_abnormal_signals")
    elif event_codes or attention_metrics:
        runtime_status = "attention"
        if event_codes:
            reasons.append("event_codes_present")
        if attention_metrics:
            reasons.append("attention_metric_threshold_exceeded")
    elif metrics or status_evaluable:
        runtime_status = "normal"
        reasons.append("evaluated_signals_within_normal_range")
    else:
        runtime_status = "unknown"
        reasons.append("insufficient_status_evidence")

    limitations: list[str] = []
    if data_basis.resolution_mode == "latest_available_fallback":
        limitations.append("当前实时窗口未命中；结论基于数据库最新可用窗口，不代表真实当前时刻状态。")
    if data_basis.resolution_mode == "no_data":
        limitations.append("当前授权范围内未获得可用运行数据。")
    return RuntimeStatusAssessment(
        device=device or "未识别设备",
        query_status=query_status,
        runtime_status=runtime_status,
        status_reasons=reasons,
        data_basis=data_basis,
        sample_count=sample_count,
        event_codes=event_codes,
        abnormal_sample_count=abnormal_sample_count,
        key_metrics=metrics,
        key_findings=findings,
        limitations=limitations,
        supporting_evidence_ids=supporting_ids,
    )


def build_runtime_status_claim(assessment: RuntimeStatusAssessment, *, claim_id: str) -> Claim:
    labels = {"normal": "正常", "attention": "需关注", "abnormal": "存在异常迹象", "unknown": "暂无法判断"}
    return Claim(
        claim_id=claim_id,
        claim_type="runtime_status_assessment",
        asset_id=assessment.device,
        statement=f"{assessment.device} 运行状态：{labels[assessment.runtime_status]}。",
        confidence=ClaimConfidence(level="low" if assessment.runtime_status == "unknown" else "medium"),
        supporting_evidence_ids=list(assessment.supporting_evidence_ids),
        status="final" if assessment.supporting_evidence_ids else "candidate",
        created_by="runtime_status_assessment",
        reason_codes=list(assessment.status_reasons),
    )


def _texts(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value or "").strip() else []


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _metric_name(content: dict[str, Any], evidence_type: str) -> str:
    if "motor_temp_max" in content:
        return "temperature_level"
    return evidence_type


def _metric_value(content: dict[str, Any]) -> float | str | None:
    values = [value for key, value in content.items() if key.endswith("_max") and value is not None]
    return max(values) if values else None
