"""Deterministic validation for model-written, fact-preserving answers."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from .answer_contracts import AnswerFacts, AnswerValidationResult, GroundedAnswerModelOutput


_URL_RE = re.compile(r"https?://[^\s<>\"'，。；：！？（）【】]+|/reports/[^\s<>\"'，。；：！？（）【】]+")
_DEVICE_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])G120电机[A-Za-z0-9_-]*", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])J\d+[A-Za-z0-9_-]*", re.IGNORECASE),
)
_FAULT_CODE_RE = re.compile(r"(?<![A-Za-z0-9])[AF]\d{5}(?!\d)", re.IGNORECASE)
_SHORT_REF_RE = re.compile(r"(?<![A-Za-z0-9])[CE]\d+(?![A-Za-z0-9])")
_FALLBACK_DISCLOSURES = ("数据库最新可用数据", "非实时数据", "未获取到当前实时窗口", "实时窗口未命中")
_REALTIME_ASSERTION_RE = re.compile(r"(?:^|根据|当前|[，。；：\s])(?:实时数据显示|当前实时状态|当前这一刻)")
_INTERNAL_LABEL_RE = re.compile(
    r"(?i)(session[ _-]?id|thread[ _-]?id|artifact[ _-]?id|claim[ _-]?id|"
    r"evidence[ _-]?id|node[ _-]?id|trace[ _-]?id|request[ _-]?id|bundle[ _-]?id|"
    r"draft[ _-]?id|goal[ _-]?id|plan[ _-]?id)"
)
_INTERNAL_SECRET_RE = re.compile(
    r"(?i)(mysql(?:\+\w+)?://|postgres(?:ql)?://|api[_ -]?key|database[_ -]?password|"
    r"traceback \(most recent call last\)|/home/|[A-Za-z]:\\)"
)
_HIGH_RISK_LITERAL_PATTERNS = (
    re.compile(r"\d{4}(?:-|/|年)\d{1,2}(?:-|/|月)\d{1,2}日?(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"),
    re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)"),
    re.compile(
        r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:个\s*)?"
        r"(?:%|℃|°C|V|伏|rpm|转/分|样本|小时|分钟|秒)(?![A-Za-z])",
        re.IGNORECASE,
    ),
    re.compile(r"(?:样本数量|样本数|共)\s*(?:为|是)?\s*[：:]?\s*\d+"),
)


class GroundedAnswerValidator:
    """Reject any answer that cannot be proven from compact AnswerFacts."""

    def __init__(self, *, max_output_chars: int = 4000) -> None:
        self.max_output_chars = max(1, int(max_output_chars))

    def validate(self, raw_output: Any, *, source_packet: AnswerFacts) -> AnswerValidationResult:
        parsed, schema_error = self._parse(raw_output)
        if parsed is None:
            return AnswerValidationResult(valid=False, schema_valid=False, errors=[schema_error or "invalid_schema"])

        errors: list[str] = []
        answer = parsed.answer
        if len(answer) > self.max_output_chars:
            errors.append("answer_too_long")
        mapped_output = self._validate_and_map_references(parsed, source_packet, errors)
        self._validate_devices_and_codes(answer, source_packet, errors)
        self._validate_urls(answer, source_packet, errors)
        self._validate_freshness(parsed, source_packet, errors)
        self._validate_actions(answer, source_packet, errors)
        self._validate_numeric_literals(answer, source_packet, errors)
        self._validate_internal_information(answer, source_packet, errors)
        return AnswerValidationResult(
            valid=not errors,
            schema_valid=True,
            errors=list(dict.fromkeys(errors)),
            output=mapped_output,
        )

    @staticmethod
    def _parse(raw_output: Any) -> tuple[GroundedAnswerModelOutput | None, str]:
        if not isinstance(raw_output, str):
            return None, "model_output_not_json_text"
        try:
            payload = json.loads(raw_output.strip())
        except (TypeError, json.JSONDecodeError):
            return None, "invalid_json"
        try:
            return GroundedAnswerModelOutput.model_validate(payload), ""
        except ValidationError:
            return None, "schema_validation_failed"

    @staticmethod
    def _validate_and_map_references(
        output: GroundedAnswerModelOutput,
        packet: AnswerFacts,
        errors: list[str],
    ) -> GroundedAnswerModelOutput:
        unknown_claims = [value for value in output.used_claim_ids if value not in packet.claim_ref_map]
        unknown_evidence = [value for value in output.used_evidence_ids if value not in packet.evidence_ref_map]
        if unknown_claims:
            errors.append("unknown_claim_ref")
        if unknown_evidence:
            errors.append("unknown_evidence_ref")

        if packet.claim_ref_map:
            supported = [
                claim_ref for claim_ref in output.used_claim_ids
                if claim_ref in packet.claim_ref_map and packet.claim_support_refs.get(claim_ref)
            ]
            if not supported:
                errors.append("conclusion_without_supported_claim")
        return output.model_copy(update={
            "used_claim_ids": [packet.claim_ref_map[value] for value in output.used_claim_ids if value in packet.claim_ref_map],
            "used_evidence_ids": [
                packet.evidence_ref_map[value] for value in output.used_evidence_ids if value in packet.evidence_ref_map
            ],
        }, deep=True)

    @staticmethod
    def _validate_devices_and_codes(answer: str, packet: AnswerFacts, errors: list[str]) -> None:
        allowed_devices = {value.casefold() for value in packet.allowed_device_refs}
        found_devices = {
            match.group(0).casefold()
            for pattern in _DEVICE_PATTERNS
            for match in pattern.finditer(answer)
        }
        if found_devices - allowed_devices:
            errors.append("unallowed_device_reference")
        allowed_codes = {value.upper() for value in packet.allowed_fault_codes}
        found_codes = {match.group(0).upper() for match in _FAULT_CODE_RE.finditer(answer)}
        if found_codes - allowed_codes:
            errors.append("unallowed_fault_code")

    @staticmethod
    def _validate_urls(answer: str, packet: AnswerFacts, errors: list[str]) -> None:
        found = {_trim_url(match.group(0)) for match in _URL_RE.finditer(answer)}
        if found - set(packet.allowed_urls):
            errors.append("unallowed_url")

    @staticmethod
    def _validate_freshness(
        output: GroundedAnswerModelOutput,
        packet: AnswerFacts,
        errors: list[str],
    ) -> None:
        fallback = packet.data_basis_required
        if fallback:
            if not output.data_basis_disclosed or not any(value in output.answer for value in _FALLBACK_DISCLOSURES):
                errors.append("latest_fallback_not_disclosed")
            if _REALTIME_ASSERTION_RE.search(output.answer):
                errors.append("latest_fallback_described_as_realtime")
        if packet.limitations_required and not output.limitations_disclosed:
            errors.append("limitations_not_disclosed")

    @staticmethod
    def _validate_actions(answer: str, packet: AnswerFacts, errors: list[str]) -> None:
        report_items = [item for item in packet.results if item.capability == "generate_report"]
        report_claimed_complete = bool(re.search(r"(?:报告(?:已|已经)?生成|已生成(?:了)?报告)", answer))
        if report_claimed_complete and not any(item.status == "completed" for item in report_items):
            errors.append("report_status_mismatch")

        positive_dispatch = any(value in answer for value in ("已派发", "已经派发", "派发给", "完成派发", "正式派发"))
        if positive_dispatch:
            errors.append("workorder_dispatch_mismatch")
        states = set(packet.allowed_action_states)
        if "not_recommended" in states and re.search(r"建议(?:创建|生成).{0,8}工单", answer):
            errors.append("workorder_recommendation_mismatch")
        if "recommended_draft" in states and re.search(r"工单(?:已经|已)(?:正式)?创建(?!为?草稿)", answer):
            errors.append("workorder_draft_status_mismatch")

        false_completion_by_capability = {
            "generate_report": ("报告已生成", "已经生成报告"),
            "create_workorder_draft": ("草稿已创建", "已生成工单草稿"),
            "check_runtime_status": ("查询成功", "状态查询完成"),
            "diagnose_fault": ("诊断已完成", "已完成诊断"),
        }
        for item in packet.results:
            if item.status not in {"blocked", "failed", "denied"}:
                continue
            if any(value in answer for value in false_completion_by_capability.get(item.capability, ())):
                errors.append("blocked_deliverable_described_as_completed")

    @staticmethod
    def _validate_numeric_literals(answer: str, packet: AnswerFacts, errors: list[str]) -> None:
        source_text = json.dumps(
            {"results": [item.model_dump(mode="json") for item in packet.results]},
            ensure_ascii=False,
            default=str,
        ).replace("T", " ")
        literals = {
            match.group(0).strip()
            for pattern in _HIGH_RISK_LITERAL_PATTERNS
            for match in pattern.finditer(answer.replace("T", " "))
        }
        if any(value not in source_text for value in literals):
            errors.append("unproven_numeric_literal")

    @staticmethod
    def _validate_internal_information(answer: str, packet: AnswerFacts, errors: list[str]) -> None:
        if _INTERNAL_LABEL_RE.search(answer) or _INTERNAL_SECRET_RE.search(answer):
            errors.append("internal_information_exposed")
        if _SHORT_REF_RE.search(answer):
            errors.append("short_reference_exposed")
        if any(value in answer for value in packet.internal_identifiers if _distinctive_internal_id(value)):
            errors.append("internal_identifier_exposed")


def _trim_url(value: str) -> str:
    return value.rstrip(".,;:!?，。；：！？）)]}")


def _distinctive_internal_id(value: str) -> bool:
    return len(value) >= 12 or any(separator in value for separator in ("_", ":", "-"))
