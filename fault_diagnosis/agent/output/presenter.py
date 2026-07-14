"""Canonical renderer for Agent Engine V2 deliverables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import EvidenceBundle

from ..contracts import DeliverableResult


@dataclass(frozen=True)
class PresentedOutput:
    content: str
    answer_variant: str
    status_brief: str


class CompositePresenter:
    """Render terminal states and all deliverables through one content path."""

    _HEADINGS = {
        "fault_code_explanation": "故障码解释",
        "runtime_status": "运行状态",
        "runtime_comparison": "运行比较",
        "diagnosis": "综合诊断",
        "recommendations": "处理建议",
        "report": "运行报告",
        "workorder_draft": "工单草稿",
        "clarification": "需要确认",
        "permission_denied": "权限限制",
    }
    _FAILURE_TEXT = {
        "fault_code_explanation": "知识库未获得可靠释义。",
        "runtime_status": "未获得可用运行数据。",
        "runtime_comparison": "比较所需的设备数据不完整。",
        "diagnosis": "依赖的运行证据不可用，未形成诊断结论。",
        "recommendations": "缺少可靠诊断依据，未形成处理建议。",
        "report": "依赖来源不可用，未生成报告。",
        "workorder_draft": "来源或设备不满足要求，未生成工单草稿。",
        "permission_denied": "当前身份无权执行该子目标。",
        "clarification": "请确认缺失信息后继续。",
    }
    _VARIANT_BY_CAPABILITY = {
        "explain_fault_code": "fault_code_answer",
        "check_runtime_status": "runtime_status_answer",
        "compare_runtime_status": "comparison_answer",
        "diagnose_fault": "diagnosis_answer",
        "resolution_recommendation": "recommendation_answer",
        "generate_report": "report_answer",
        "create_workorder_draft": "workorder_answer",
    }
    _LEGACY_CAPABILITY_BY_TYPE = {
        "fault_code_explanation": "explain_fault_code",
        "runtime_status": "check_runtime_status",
        "runtime_comparison": "compare_runtime_status",
        "diagnosis": "diagnose_fault",
        "recommendations": "resolution_recommendation",
        "report": "generate_report",
        "workorder_draft": "create_workorder_draft",
    }

    def present(
        self,
        *,
        deliverables: list[DeliverableResult],
        status: str,
        evidence_bundle: EvidenceBundle | None = None,
        error: dict[str, Any] | None = None,
        cancelled: bool = False,
        cancel_reason: str | None = None,
        contract_validation: dict[str, Any] | None = None,  # compatibility serialization only
        degraded_notice: str = "",
    ) -> PresentedOutput:
        ordered = [
            item
            for _, item in sorted(
                enumerate(deliverables),
                key=lambda pair: (pair[1].clause_index, pair[0], pair[1].goal_id),
            )
        ]
        variant = self._answer_variant(deliverables=ordered, status=status, cancelled=cancelled)
        if cancelled:
            return PresentedOutput(content="", answer_variant=variant, status_brief="")
        if not ordered:
            content = self._terminal_body(
                status=status,
                error=error,
                cancel_reason=cancel_reason,
            )
            return PresentedOutput(content=content, answer_variant=variant, status_brief=content)

        sections: list[str] = []
        for item in ordered:
            if item.status in {"failed", "blocked", "denied"}:
                body = item.error_message or self._FAILURE_TEXT.get(item.deliverable_type, "该交付物未完成。")
            else:
                body = self._body(
                    item,
                    evidence_bundle=_bundle_for_deliverable(evidence_bundle, item),
                    degraded_notice=degraded_notice,
                )
                if item.status == "partial" and item.error_code:
                    body = f"{body}\n说明：部分证据不可用，结论已降级。".strip()
            heading = item.title or self._HEADINGS[item.deliverable_type]
            sections.append(f"【{heading}】\n{body}".strip())
        content = "\n\n".join(sections)
        return PresentedOutput(
            content=content,
            answer_variant=variant,
            status_brief=self._status_brief(ordered, status=status),
        )

    def _body(
        self,
        item: DeliverableResult,
        *,
        evidence_bundle: EvidenceBundle | None,
        degraded_notice: str,
    ) -> str:
        payload = item.payload
        if item.deliverable_type == "fault_code_explanation":
            return self._fault_code_body(payload)
        if item.deliverable_type == "runtime_status":
            return self._runtime_status_body(payload, degraded_notice=degraded_notice)
        if item.deliverable_type == "runtime_comparison":
            return self._comparison_body(payload)
        if item.deliverable_type == "diagnosis":
            return self._diagnosis_body(payload, evidence_bundle=evidence_bundle)
        if item.deliverable_type == "recommendations":
            recommendations = _text_list(payload.get("recommendations"))
            return "\n".join(f"{index}. {value}" for index, value in enumerate(recommendations, start=1)) or "暂无额外处理建议。"
        if item.deliverable_type == "report":
            link = payload.get("report_url") or payload.get("report_filename") or ""
            return f"报告已生成：{link}" if link else "报告已生成。"
        if item.deliverable_type == "workorder_draft":
            return self._workorder_body(payload)
        if item.deliverable_type == "clarification":
            return str(payload.get("clarification_question") or payload.get("message") or "请确认缺失信息后继续。")
        return str(payload.get("message") or "已完成。")

    @staticmethod
    def _answer_variant(
        *,
        deliverables: list[DeliverableResult],
        status: str,
        cancelled: bool,
    ) -> str:
        if len(deliverables) > 1:
            return "composite_answer"
        if not deliverables:
            return "clarification_answer" if cancelled or status in {"blocked", "cancelled"} else "meta_answer"
        item = deliverables[0]
        capability = item.capability or CompositePresenter._LEGACY_CAPABILITY_BY_TYPE.get(item.deliverable_type, "")
        return CompositePresenter._VARIANT_BY_CAPABILITY.get(capability, "clarification_answer")

    @staticmethod
    def _terminal_body(
        *,
        status: str,
        error: dict[str, Any] | None,
        cancel_reason: str | None,
    ) -> str:
        message = str((error or {}).get("message") or "").strip()
        if status == "failed":
            return message or "V2 执行失败，未生成可靠诊断结果。"
        if status == "blocked":
            return message or "当前请求被安全边界阻止，未执行受限动作。"
        if cancel_reason:
            return f"需要补充信息后才能继续处理。当前停止原因：{cancel_reason}"
        return "需要补充设备、故障码或时间窗口等关键信息后才能继续处理。"

    @staticmethod
    def _fault_code_body(payload: dict[str, Any]) -> str:
        entries = payload.get("fault_code_entries") or []
        if entries and isinstance(entries[0], dict):
            entry = entries[0]
            requested_codes = [value.upper() for value in _text_list(payload.get("fault_codes"))]
            exact_match = entry.get("match_type") in {None, "", "exact_match"}
            requested_match = not requested_codes or str(entry.get("code") or "").upper() in requested_codes
            if not exact_match or not requested_match:
                requested = "、".join(requested_codes) or "请求中的故障码"
                title = entry.get("title") or entry.get("meaning") or "手册未明确给出"
                return f"未找到精确匹配：{requested}。\n候选：\n1. {entry.get('code') or '未知编码'}：{title}"
            meaning = entry.get("meaning") or entry.get("title") or "手册未明确给出"
            return "\n".join(
                (
                    f"一句话解释：{entry.get('code') or '故障码'}：{meaning}",
                    f"可能原因：{entry.get('cause') or '手册未明确给出'}",
                    f"手册处理：{entry.get('remedy') or '手册未明确给出'}",
                )
            )
        codes = "、".join(_text_list(payload.get("fault_codes")))
        return f"未获得 {codes} 的结构化手册解析结果。" if codes else "未检索到当前权限范围内可用的故障码说明。"

    @staticmethod
    def _runtime_status_body(payload: dict[str, Any], *, degraded_notice: str) -> str:
        assessments = [item for item in payload.get("assessments", []) if isinstance(item, dict)]
        if not assessments:
            sql = payload.get("legacy_sql") if isinstance(payload.get("legacy_sql"), dict) else {}
            summary = str(sql.get("summary") or "已完成运行状态查询。")
            data_state = f" 数据状态：{sql.get('data_state')}。" if sql.get("data_state") else ""
            return f"{summary}{data_state}".strip()
        blocks = []
        for assessment in assessments:
            basis = assessment.get("data_basis") if isinstance(assessment.get("data_basis"), dict) else {}
            status_label = {
                "normal": "正常",
                "attention": "需关注",
                "abnormal": "存在异常迹象",
                "unknown": "暂无法判断",
            }.get(str(assessment.get("runtime_status") or "unknown"), "暂无法判断")
            mode_label = {
                "realtime_window": "实时窗口",
                "latest_available_fallback": "数据库最新可用数据",
                "no_data": "无可用数据",
            }.get(str(basis.get("resolution_mode") or ""), str(basis.get("resolution_mode") or "未说明"))
            window = basis.get("resolved_window") if isinstance(basis.get("resolved_window"), dict) else {}
            lines = [
                f"设备：{assessment.get('device') or '未说明'}",
                f"状态：{status_label}",
                f"数据模式：{mode_label}",
                f"数据窗口：{_time_text(window.get('start'))} ～ {_time_text(window.get('end'))}" if window else "数据窗口：未提供",
                f"最新样本：{_time_text(basis.get('latest_sample_time'))}",
                f"样本数量：{assessment.get('sample_count', 0)}",
            ]
            findings = _text_list(assessment.get("key_findings"))
            limitations = _text_list(assessment.get("limitations"))
            lines.append("关键发现：" + ("；".join(findings[:5]) if findings else "未发现可补充的结构化要点"))
            lines.append("限制说明：" + ("；".join(limitations) if limitations else "无额外限制说明"))
            blocks.append("\n".join(lines))
        prefix = f"{degraded_notice}\n" if degraded_notice else ""
        return prefix + "\n\n".join(blocks)

    @staticmethod
    def _comparison_body(payload: dict[str, Any]) -> str:
        dimensions = [item for item in payload.get("comparison_dimensions", []) if isinstance(item, dict)]
        device_names = _text_list(payload.get("devices"))
        if not device_names:
            device_names = _dedupe(
                [device for item in dimensions for device in (item.get("values_by_device") or {}).keys()]
            )
        lines = []
        if device_names:
            lines.append(f"设备：{'、'.join(device_names)}")
        lines.append(f"结论：{payload.get('conclusion') or '已完成设备运行比较。'}")
        for item in dimensions:
            values = "，".join(f"{device}={value}" for device, value in (item.get("values_by_device") or {}).items())
            suffix = f"；{item.get('conclusion')}" if item.get("conclusion") else ""
            lines.append(f"{item.get('dimension') or '比较维度'}：{values}{suffix}")
        return "\n".join(lines)

    @staticmethod
    def _diagnosis_body(payload: dict[str, Any], *, evidence_bundle: EvidenceBundle | None) -> str:
        if evidence_bundle and evidence_bundle.final_claim_ids:
            by_id = {claim.claim_id: claim for claim in evidence_bundle.claims}
            final_claims = [
                by_id[claim_id]
                for claim_id in evidence_bundle.final_claim_ids
                if claim_id in by_id and by_id[claim_id].supporting_evidence_ids
            ]
            if final_claims:
                evidence_by_id = {item.evidence_id: item for item in evidence_bundle.evidence_items}
                lines = _numbered("诊断结论", [claim.statement for claim in final_claims])
                lines += _numbered(
                    "依据",
                    _dedupe(
                        [
                            evidence_by_id[evidence_id].summary
                            for claim in final_claims
                            for evidence_id in claim.supporting_evidence_ids
                            if evidence_id in evidence_by_id and evidence_by_id[evidence_id].summary
                        ]
                    )[:5],
                )
                lines += _numbered("仍需补充", _dedupe([value for claim in final_claims for value in claim.missing_evidence]))
                lines += _stale_lines(evidence_bundle)
                return "\n".join(lines)
        if evidence_bundle and evidence_bundle.claims and not evidence_bundle.final_claim_ids:
            unsupported = [claim.statement for claim in evidence_bundle.claims if not claim.supporting_evidence_ids and claim.statement]
            missing = _missing_evidence(evidence_bundle, payload)
            lines = _numbered("待确认/需补充", unsupported or missing or ["当前诊断判断缺少可引用证据，不能作为最终结论。"])
            if unsupported and missing:
                lines += _numbered("缺失证据", missing)
            lines += _stale_lines(evidence_bundle)
            return "\n".join(lines)
        conclusion = str(payload.get("conclusion") or "").strip()
        if not conclusion:
            return "待确认/需补充：当前诊断判断缺少可引用证据，不能作为最终结论。"
        lines = [f"诊断结论：{conclusion}"]
        lines += _numbered("依据", _text_list(payload.get("basis")))
        lines += _numbered("建议", _text_list(payload.get("recommendations")))
        lines += _numbered("仍需补充", _missing_evidence(evidence_bundle, payload))
        if payload.get("risk_notice"):
            lines.append(f"风险提示：{payload['risk_notice']}")
        lines += _stale_lines(evidence_bundle)
        return "\n".join(lines)

    @staticmethod
    def _workorder_body(payload: dict[str, Any]) -> str:
        suggestion = payload.get("workorder_suggestion") if isinstance(payload.get("workorder_suggestion"), dict) else {}
        pending = payload.get("workorder_pending_action") if isinstance(payload.get("workorder_pending_action"), dict) else {}
        draft = payload.get("workorder_draft") if isinstance(payload.get("workorder_draft"), dict) else {}
        if draft:
            headline = "已基于上一轮报告生成工单草稿建议（未派发）。"
        elif suggestion.get("lifecycle_status") == "recommended_draft" or suggestion.get("need_workorder"):
            headline = "已生成工单建议，当前处于待人工确认状态（未派发）。"
        else:
            headline = "工单草稿已生成，需人工确认后方可进入后续流程；本轮未派发。"
        values = (
            ("设备", _first_text(draft.get("device"), suggestion.get("equipment_object"))),
            ("故障码/事件码", _first_text(draft.get("fault_code"), suggestion.get("fault_code"))),
            ("生命周期状态", _first_text(draft.get("status"), suggestion.get("lifecycle_status"), pending.get("status"))),
            ("工单类型", _first_text(draft.get("workorder_type"), suggestion.get("workorder_type"))),
            ("优先级", " / ".join(value for value in (str(draft.get("priority") or suggestion.get("priority") or ""), str(suggestion.get("priority_label") or "")) if value)),
            ("风险等级", suggestion.get("risk_level")),
            ("处理角色", _first_text(draft.get("recommended_assignee_role"), suggestion.get("assignee_role"), pending.get("required_role"))),
            ("建议完成窗口", suggestion.get("suggested_completion_window")),
            ("诊断依据摘要", _first_text(suggestion.get("diagnosis_conclusion"), suggestion.get("reason"))),
        )
        lines = [headline, *[f"{label}：{value}" for label, value in values if str(value or "").strip()]]
        evidence = _dedupe([*_text_list(suggestion.get("key_evidence")), *_text_list(pending.get("required_evidence"))])
        lines += _numbered("关键证据", evidence[:5])
        if draft.get("stale_warning") or payload.get("stale_evidence_disclosure_required"):
            lines.append(f"数据时效性提示：{draft.get('stale_warning') or '当前判断复用了非实时历史证据；草稿结论不代表设备实时状态。'}")
        if payload.get("target_evidence_bundle_id"):
            lines.append(f"证据来源：继承上一轮证据包 {payload['target_evidence_bundle_id']}")
        lines.append("人工确认：工单草稿需要工程师确认后才能继续。")
        lines.append("安全边界：当前仅生成草稿/建议，未自动派发，也不会执行设备控制、告警关闭或配置写入。")
        return "\n".join(lines)

    @staticmethod
    def _status_brief(deliverables: list[DeliverableResult], *, status: str) -> str:
        status_item = next((item for item in deliverables if item.deliverable_type == "runtime_status"), None)
        if status_item:
            assessments = status_item.payload.get("assessments") or []
            if assessments and isinstance(assessments[0], dict):
                assessment = assessments[0]
                label = {"normal": "正常", "attention": "需关注", "abnormal": "存在异常迹象", "unknown": "暂无法判断"}.get(
                    str(assessment.get("runtime_status") or "unknown"), "暂无法判断"
                )
                return f"{assessment.get('device') or '设备'}：{label}。"
            sql = status_item.payload.get("legacy_sql") or {}
            if isinstance(sql, dict) and sql.get("summary"):
                return str(sql["summary"])
        return f"V2 runtime {status}."


def _missing_evidence(bundle: EvidenceBundle | None, payload: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    if bundle:
        for claim in bundle.claims:
            values.extend(claim.missing_evidence)
        if isinstance(bundle.quality_checks.get("missing_evidence"), list):
            values.extend(bundle.quality_checks["missing_evidence"])
    values.extend(_text_list(payload.get("missing_information")))
    return _dedupe(values)


def _bundle_for_deliverable(
    bundle: EvidenceBundle | None,
    item: DeliverableResult,
) -> EvidenceBundle | None:
    if bundle is None:
        return None
    evidence_ids = set(item.evidence_ids)
    claim_ids = set(item.claim_ids)
    evidence = [value for value in bundle.evidence_items if value.evidence_id in evidence_ids]
    claims = [value for value in bundle.claims if value.claim_id in claim_ids]
    final_ids = [value for value in bundle.final_claim_ids if value in claim_ids]
    return bundle.model_copy(
        update={"evidence_items": evidence, "claims": claims, "final_claim_ids": final_ids},
        deep=True,
    )


def _stale_lines(bundle: EvidenceBundle | None) -> list[str]:
    if not bundle:
        return []
    stale_ids = {str(item) for item in bundle.quality_checks.get("stale_evidence_ids", []) or []}
    summaries = []
    for item in bundle.evidence_items:
        freshness = str(getattr(item.quality, "freshness", "") or item.metadata.get("freshness", "")).lower()
        if item.evidence_id in stale_ids or freshness == "stale" or item.evidence_type == "stale_evidence_disclosure":
            summaries.append(item.summary)
    return _numbered("时效性提示", _dedupe(summaries)[:3] or (["存在滞后证据，不能代表当前实时状态。"] if stale_ids else []))


def _numbered(title: str, values: list[Any]) -> list[str]:
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    return [f"{title}：", *[f"{index}. {item}" for index, item in enumerate(cleaned, start=1)]] if cleaned else []


def _text_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _dedupe(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() != "none":
            return text
    return ""


def _time_text(value: Any) -> str:
    if not value:
        return "未提供"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value).replace("T", " ")
