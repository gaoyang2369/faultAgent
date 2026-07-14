"""Deterministic validation for model-written, fact-preserving answers."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from .answer_contracts import (
    AnswerSourcePacket,
    AnswerValidationResult,
    GroundedAnswerModelOutput,
)


_URL_RE = re.compile(r"https?://[^\s<>\"'，。；：！？（）【】]+|/reports/[^\s<>\"'，。；：！？（）【】]+")
_DEVICE_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])G120电机[A-Za-z0-9_-]*", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])J\d+[A-Za-z0-9_-]*", re.IGNORECASE),
)
_FAULT_CODE_RE = re.compile(r"(?<![A-Za-z0-9])[AF]\d{5}(?!\d)", re.IGNORECASE)
_FALLBACK_DISCLOSURES = ("数据库最新可用数据", "非实时数据", "未获取到当前实时窗口", "实时窗口未命中")
_LIMITATION_MARKERS = (
    "限制", "不足", "缺少", "缺失", "未获得", "不可用", "不代表", "滞后", "过期", "非实时", "最新可用",
)
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
    """Reject any answer that cannot be proven from its source packet."""

    def __init__(self, *, max_output_chars: int = 4000) -> None:
        self.max_output_chars = max(1, int(max_output_chars))

    def validate(self, raw_output: Any, *, source_packet: AnswerSourcePacket) -> AnswerValidationResult:
        parsed, schema_error = self._parse(raw_output)
        if parsed is None:
            return AnswerValidationResult(valid=False, errors=[schema_error or "invalid_schema"])

        errors: list[str] = []
        answer = parsed.answer
        if len(answer) > self.max_output_chars:
            errors.append("answer_too_long")
        self._validate_references(parsed, source_packet, errors)
        self._validate_devices_and_codes(answer, source_packet, errors)
        self._validate_urls(answer, source_packet, errors)
        self._validate_freshness(parsed, source_packet, errors)
        self._validate_actions(answer, source_packet, errors)
        self._validate_grounded_advice(answer, source_packet, errors)
        self._validate_numeric_literals(answer, source_packet, errors)
        self._validate_internal_information(answer, source_packet, errors)
        return AnswerValidationResult(valid=not errors, errors=list(dict.fromkeys(errors)), output=parsed)

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
    def _validate_references(
        output: GroundedAnswerModelOutput,
        packet: AnswerSourcePacket,
        errors: list[str],
    ) -> None:
        claims = {str(item.get("claim_id") or ""): item for item in packet.claims}
        evidence_ids = {str(item.get("evidence_id") or "") for item in packet.evidence}
        unknown_claims = [value for value in output.used_claim_ids if value not in claims]
        unknown_evidence = [value for value in output.used_evidence_ids if value not in evidence_ids]
        if unknown_claims:
            errors.append("unknown_claim_id")
        if unknown_evidence:
            errors.append("unknown_evidence_id")

        conclusion_capabilities = {"check_runtime_status", "compare_runtime_status", "diagnose_fault"}
        completed_conclusion = any(
            item.get("capability") in conclusion_capabilities and item.get("status") in {"completed", "partial"}
            for item in packet.deliverables
        )
        if completed_conclusion:
            supported = [
                claims[claim_id]
                for claim_id in output.used_claim_ids
                if claim_id in claims and claims[claim_id].get("supporting_evidence_ids")
            ]
            if not supported:
                errors.append("conclusion_without_supported_claim")
            elif any(
                not any(anchor in output.answer for anchor in _claim_anchors(claim, packet))
                for claim in supported
                if _claim_anchors(claim, packet)
            ):
                errors.append("claim_statement_not_expressed")

    @staticmethod
    def _validate_devices_and_codes(answer: str, packet: AnswerSourcePacket, errors: list[str]) -> None:
        allowed_devices = {value.casefold() for value in packet.allowed_device_refs}
        found_devices = {
            match.group(0).casefold()
            for pattern in _DEVICE_PATTERNS
            for match in pattern.finditer(answer)
        }
        if any(not any(allowed == found or allowed.startswith(found) for allowed in allowed_devices) for found in found_devices):
            errors.append("unallowed_device_reference")
        allowed_codes = {value.upper() for value in packet.allowed_fault_codes}
        found_codes = {match.group(0).upper() for match in _FAULT_CODE_RE.finditer(answer)}
        if found_codes - allowed_codes:
            errors.append("unallowed_fault_code")

    @staticmethod
    def _validate_urls(answer: str, packet: AnswerSourcePacket, errors: list[str]) -> None:
        found = {_trim_url(match.group(0)) for match in _URL_RE.finditer(answer)}
        if found - set(packet.allowed_urls):
            errors.append("unallowed_url")

    @staticmethod
    def _validate_freshness(
        output: GroundedAnswerModelOutput,
        packet: AnswerSourcePacket,
        errors: list[str],
    ) -> None:
        answer = output.answer
        modes = _resolution_modes(packet.data_basis)
        fallback = "latest_available_fallback" in modes
        stale = any(
            str(item.get("freshness", {}).get("quality") or "").lower() == "stale"
            for item in packet.evidence
        ) or any("滞后" in item or "过期" in item or "不代表真实当前" in item for item in packet.limitations)
        if fallback:
            if not output.data_basis_disclosed or not any(value in answer for value in _FALLBACK_DISCLOSURES):
                errors.append("latest_fallback_not_disclosed")
            if _describes_as_realtime(answer):
                errors.append("latest_fallback_described_as_realtime")
        if packet.limitations or stale:
            if not output.limitations_disclosed or not any(value in answer for value in _LIMITATION_MARKERS):
                errors.append("limitations_not_disclosed")

    @staticmethod
    def _validate_actions(answer: str, packet: AnswerSourcePacket, errors: list[str]) -> None:
        deliverables = packet.deliverables
        report_items = [item for item in deliverables if item.get("capability") == "generate_report"]
        report_claimed_complete = bool(re.search(r"(?:报告(?:已|已经)?生成|已生成(?:了)?报告)", answer))
        if report_claimed_complete and not any(item.get("status") == "completed" for item in report_items):
            errors.append("report_status_mismatch")

        positive_dispatch = any(value in answer for value in ("已派发", "已经派发", "派发给", "完成派发", "正式派发"))
        if positive_dispatch:
            errors.append("workorder_dispatch_mismatch")
        states = set(packet.allowed_action_states)
        if "not_recommended" in states and re.search(r"建议(?:创建|生成).{0,8}工单", answer):
            errors.append("workorder_recommendation_mismatch")
        if "recommended_draft" in states and re.search(r"工单(?:已经|已)(?:正式)?创建(?!为?草稿)", answer):
            errors.append("workorder_draft_status_mismatch")

        all_denied = (
            bool(deliverables) and all(item.get("status") == "denied" for item in deliverables)
        ) or packet.overall_status == "denied"
        if all_denied and any(value in answer for value in ("系统错误", "技术故障", "执行失败")):
            errors.append("permission_denial_misrepresented")
        if packet.overall_status in {"blocked", "denied", "failed"} and not deliverables:
            if any(value in answer for value in ("已完成", "成功完成", "报告已生成", "草稿已创建")):
                errors.append("terminal_status_mismatch")

        for item in deliverables:
            if item.get("status") not in {"blocked", "failed", "denied"}:
                continue
            capability = str(item.get("capability") or "")
            false_completion = {
                "diagnose_fault": ("诊断已完成", "已形成诊断结论", "诊断结论是"),
                "generate_report": ("报告已生成", "已经生成报告"),
                "create_workorder_draft": ("草稿已创建", "已生成工单草稿"),
            }.get(capability, ())
            if any(value in answer for value in false_completion):
                errors.append("blocked_deliverable_described_as_completed")

        runtime_states = set(_values_for_key(packet.deliverables, "runtime_status"))
        if len(runtime_states) == 1:
            expected = next(iter(runtime_states))
            contradictions = {
                "normal": ("需关注", "存在异常", "状态异常", "运行异常"),
                "attention": ("状态正常", "运行正常", "状态异常", "运行异常"),
                "abnormal": ("状态正常", "运行正常"),
                "unknown": ("状态正常", "运行正常", "需关注", "存在异常", "状态异常"),
            }.get(expected, ())
            if any(value in answer for value in contradictions):
                errors.append("runtime_status_mismatch")

    @staticmethod
    def _validate_grounded_advice(answer: str, packet: AnswerSourcePacket, errors: list[str]) -> None:
        source = _normalize_text(
            json.dumps(
                {
                    "deliverables": packet.deliverables,
                    "claims": [item.get("statement", "") for item in packet.claims],
                    "evidence": [item.get("summary", "") for item in packet.evidence],
                    "fallback": packet.deterministic_fallback,
                },
                ensure_ascii=False,
            )
        )
        for clause in re.split(r"[。；;\n]", answer):
            match = re.match(r"\s*(?:建议|下一步|请|可以|应当|应该)[：:]?\s*(.+)", clause)
            if not match:
                continue
            core = _normalize_text(match.group(1))
            if len(core) >= 4 and core not in source:
                errors.append("unsupported_advice")

    @staticmethod
    def _validate_numeric_literals(answer: str, packet: AnswerSourcePacket, errors: list[str]) -> None:
        source_text = json.dumps(
            {
                "deliverables": packet.deliverables,
                "claims": [{"statement": item.get("statement", "")} for item in packet.claims],
                "evidence": [{"summary": item.get("summary", "")} for item in packet.evidence],
                "data_basis": packet.data_basis,
                "limitations": packet.limitations,
            },
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
    def _validate_internal_information(answer: str, packet: AnswerSourcePacket, errors: list[str]) -> None:
        if _INTERNAL_LABEL_RE.search(answer) or _INTERNAL_SECRET_RE.search(answer):
            errors.append("internal_information_exposed")
        internal_ids = {
            str(item.get(key) or "")
            for collection, key in ((packet.claims, "claim_id"), (packet.evidence, "evidence_id"))
            for item in collection
            if str(item.get(key) or "")
        }
        internal_ids.update(
            str(item.get("goal_id") or "")
            for item in packet.deliverables
            if str(item.get("goal_id") or "")
        )
        if any(value in answer for value in internal_ids if _distinctive_internal_id(value)):
            errors.append("internal_identifier_exposed")


def _resolution_modes(data_basis: dict[str, Any]) -> set[str]:
    modes = {str(data_basis.get("resolution_mode") or "")}
    modes.update(str(value) for value in data_basis.get("resolution_modes", []) if value)
    for value in data_basis.get("sources", []):
        if isinstance(value, dict):
            modes.add(str(value.get("resolution_mode") or ""))
    return {value for value in modes if value}


def _trim_url(value: str) -> str:
    return value.rstrip(".,;:!?，。；：！？）)]}")


def _distinctive_internal_id(value: str) -> bool:
    return len(value) >= 12 or any(separator in value for separator in ("_", ":", "-"))


def _describes_as_realtime(answer: str) -> bool:
    negative_phrases = (
        "非实时数据显示",
        "不是实时数据显示",
        "并非实时数据显示",
        "不是当前实时状态",
        "并非当前实时状态",
        "不代表当前实时状态",
        "并非当前这一刻",
        "不是当前这一刻",
    )
    normalized = answer
    for phrase in negative_phrases:
        normalized = normalized.replace(phrase, "")
    return any(value in normalized for value in ("当前实时状态", "实时数据显示", "当前这一刻"))


def _claim_anchors(claim: dict[str, Any], packet: AnswerSourcePacket) -> list[str]:
    statement = str(claim.get("statement") or "")
    for value in [*packet.allowed_device_refs, *packet.allowed_fault_codes]:
        statement = statement.replace(value, " ")
    parts = re.split(r"[\s，。；：:、（）()]+|运行状态|诊断结论|状态|设备|目前|呈现|存在|显示|判断|为|是", statement)
    generic = {"当前", "结果", "运行", "综合", "已完成", "未知", "暂无法判断"}
    anchors = [part for part in parts if len(part) >= 2 and part not in generic]
    for label in ("正常", "需关注", "存在异常迹象", "异常", "暂无法判断"):
        if label in statement:
            anchors.append(label)
    return list(dict.fromkeys(anchors))


def _values_for_key(value: Any, target: str) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == target and str(child or "").strip():
                values.append(str(child).strip())
            values.extend(_values_for_key(child, target))
    elif isinstance(value, list):
        for child in value:
            values.extend(_values_for_key(child, target))
    return values


def _normalize_text(value: str) -> str:
    return "".join(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", value)).casefold()
