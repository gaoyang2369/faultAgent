"""Deterministic final-answer rendering from structured single-agent artifacts."""

from __future__ import annotations

import re
from typing import Any

from ...diagnosis.contracts import (
    AnalysisStepArtifact,
    Claim,
    EvidenceBundle,
    EvidenceItem,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from ..contracts import SingleAgentDecision
from ..reporting import extract_report_url
from .contracts import OutputContract, OutputSectionContract, RenderedAnswer, RenderedSection
from .templates import coerce_task_type, get_output_contract

ACTION_SAFE_FALLBACK = (
    "我不能直接执行该动作。当前只能提供操作建议、工单草稿或人工审批所需信息。"
)
DANGEROUS_ACTION_COMPLETION_PATTERNS = (
    "已重启",
    "已经重启",
    "已停机",
    "已经停机",
    "已关闭告警",
    "已经关闭告警",
    "已派发工单",
    "已经派发工单",
    "已修改参数",
    "已经修改参数",
    "操作已完成",
    "已执行",
    "已经执行",
)


def render_final_answer(
    *,
    decision: SingleAgentDecision,
    evidence_bundle: EvidenceBundle | None,
    analysis_artifact: AnalysisStepArtifact | None = None,
    workorder_suggestion: WorkOrderSuggestion | None = None,
    report_artifact: ReportStepArtifact | None = None,
    sql_artifact: SqlStepArtifact | None = None,
    knowledge_artifact: KnowledgeStepArtifact | None = None,
) -> RenderedAnswer:
    """Render the final answer selected by ``decision.primary_task_type``."""

    task_type = coerce_task_type(decision.primary_task_type)
    contract = get_output_contract(task_type)
    context = _RenderContext(
        decision=decision,
        evidence_bundle=evidence_bundle,
        analysis_artifact=analysis_artifact,
        workorder_suggestion=workorder_suggestion,
        report_artifact=report_artifact,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
    )
    sections = [
        _build_section(section_contract, contract=contract, context=context)
        for section_contract in contract.sections
    ]
    sections = [
        section
        for section, section_contract in zip(sections, contract.sections, strict=True)
        if section.content.strip() or section_contract.required or section_contract.allow_empty
    ]
    used_evidence_ids = _dedupe(
        evidence_id
        for section in sections
        for evidence_id in section.evidence_ids
    )
    missing_evidence = _dedupe(
        item
        for section in sections
        for item in section.missing_evidence
    )
    guardrail_notes: list[str] = []
    content = _join_sections(contract, sections)
    if task_type.value == "action_request" and _contains_dangerous_completion(content):
        guardrail_notes.append("unsafe_action_completion_rewritten")
        content = _join_sections(contract, _action_request_sections(context, contract))
    content = _truncate_content(content, contract.max_chars)
    return RenderedAnswer(
        task_type=task_type,
        template_id=contract.template_id,
        content=content,
        sections=sections,
        used_evidence_ids=used_evidence_ids,
        missing_evidence=missing_evidence,
        guardrail_notes=guardrail_notes,
    )


class _RenderContext:
    def __init__(
        self,
        *,
        decision: SingleAgentDecision,
        evidence_bundle: EvidenceBundle | None,
        analysis_artifact: AnalysisStepArtifact | None,
        workorder_suggestion: WorkOrderSuggestion | None,
        report_artifact: ReportStepArtifact | None,
        sql_artifact: SqlStepArtifact | None,
        knowledge_artifact: KnowledgeStepArtifact | None,
    ) -> None:
        self.decision = decision
        self.evidence_bundle = evidence_bundle
        self.analysis_artifact = analysis_artifact
        self.workorder_suggestion = workorder_suggestion
        self.report_artifact = report_artifact
        self.sql_artifact = sql_artifact
        self.knowledge_artifact = knowledge_artifact
        self.evidence_items = list(evidence_bundle.evidence_items if evidence_bundle else [])
        self.claims = list(evidence_bundle.claims if evidence_bundle else [])

    def evidence_ids(self, *kinds: str) -> list[str]:
        if not kinds:
            return [item.evidence_id for item in self.evidence_items if item.evidence_id]
        matched: list[str] = []
        for item in self.evidence_items:
            text = " ".join([item.evidence_type, item.source_type, item.source_name, item.summary]).lower()
            if item.evidence_id and any(kind.lower() in text for kind in kinds):
                matched.append(item.evidence_id)
        return _dedupe(matched)

    def claim(self, *claim_types: str) -> Claim | None:
        for claim in self.claims:
            if claim.claim_type in claim_types:
                return claim
        return None

    def missing_evidence(self) -> list[str]:
        items = []
        for claim in self.claims:
            items.extend(claim.missing_evidence)
        if self.analysis_artifact is not None:
            items.extend(self.analysis_artifact.missing_information)
        items.extend(str(slot) for slot in self.decision.missing_slots if str(slot).strip())
        return _dedupe(items)


def _build_section(
    section_contract: OutputSectionContract,
    *,
    contract: OutputContract,
    context: _RenderContext,
) -> RenderedSection:
    custom = _custom_section(section_contract.key, context, contract)
    if custom is not None:
        content, evidence_ids, missing = custom
    else:
        content, evidence_ids, missing = _generic_section(section_contract.key, context, contract)

    content = content.strip()
    evidence_ids = _dedupe(evidence_ids)
    missing = _dedupe(missing)
    if not content and section_contract.fallback_when_missing:
        content = section_contract.fallback_when_missing
    if section_contract.require_evidence and not evidence_ids:
        missing.append(f"{section_contract.title}缺少可追溯证据")
        if not content:
            content = section_contract.fallback_when_missing or "当前证据不足，暂不能形成可靠判断。"
    if section_contract.required and not content:
        content = section_contract.fallback_when_missing or "当前信息不足，暂不能形成可靠内容。"
    return RenderedSection(
        key=section_contract.key,
        title=section_contract.title,
        content=content,
        evidence_ids=evidence_ids,
        missing_evidence=missing,
    )


def _custom_section(
    key: str,
    context: _RenderContext,
    contract: OutputContract,
) -> tuple[str, list[str], list[str]] | None:
    analysis = context.analysis_artifact
    workorder = context.workorder_suggestion

    if contract.task_type.value == "action_request":
        section = next((item for item in _action_request_sections(context, contract) if item.key == key), None)
        if section:
            return section.content, section.evidence_ids, section.missing_evidence

    if contract.task_type.value == "report_generation":
        if key == "report_summary":
            summary_items = _limited_items(
                [
                    analysis.conclusion if analysis else "",
                    *(analysis.basis if analysis else []),
                    *(analysis.recommendations if analysis else []),
                ],
                limit=5,
            )
            return _numbered(summary_items, "报告已生成，但当前结构化摘要不足。"), context.evidence_ids(), context.missing_evidence()
        if key == "report_link":
            report_name = context.report_artifact.report_filename if context.report_artifact else ""
            report_url = extract_report_url(context.report_artifact.save_result) if context.report_artifact else ""
            title = report_name or "诊断报告"
            return f"已生成《{title}》。\n报告链接：{report_url or report_name or '未返回报告链接'}", [], []
        if key == "risk_and_limitations":
            return _limitations_text(context), [], context.missing_evidence()

    if key in {"diagnosis_conclusion", "brief_judgement", "event_summary", "health_score"}:
        claim = context.claim("diagnosis_summary")
        content = _first_text([claim.statement if claim else "", analysis.conclusion if analysis else ""])
        if context.missing_evidence() and content and "不能" not in content:
            content = f"{content}\n目前不能确认唯一根因，需补充证据后再定论。"
        return content, _claim_or_all_evidence_ids(claim, context), context.missing_evidence()

    if key in {"current_status", "current_alarm_status", "trend_analysis"}:
        evidence_ids = context.evidence_ids("sql", "device_status", "metric", "alarm_event", "timeseries")
        items = _evidence_summaries(context, evidence_ids, limit=contract.max_bullets_per_section)
        if key == "current_alarm_status" and not context.evidence_ids("alarm_event"):
            missing = [*context.missing_evidence(), "缺少实时或历史告警状态"]
            return "暂未查询到该告警的实时状态，不能确认当前是否仍在发生。", evidence_ids, missing
        return _bullets(items, "暂未获得足够的当前运行状态数据。"), evidence_ids, context.missing_evidence()

    if key in {"key_evidence", "diagnosis_basis", "sources"}:
        evidence_ids = context.evidence_ids()
        items = _evidence_summaries(context, evidence_ids, limit=contract.max_bullets_per_section)
        return _numbered(items, "本次未获得足够的可用证据。"), evidence_ids, context.missing_evidence()

    if key in {"possible_causes", "root_cause_candidates", "risk_items"}:
        claim_ids = _claim_evidence_ids(context, "root_cause_candidate")
        causes = list(analysis.probable_causes if analysis else [])
        return _numbered(causes, "证据不足，暂不能形成可靠的原因排序。"), claim_ids, context.missing_evidence()

    if key in {"recommendations", "recommended_actions", "maintenance_advice", "prevention_recommendations"}:
        claim = context.claim("recommendation")
        items = list(analysis.recommendations if analysis else [])
        return _numbered(items, "先补充运行数据、告警状态和现场检查结果，再制定处置动作。"), _claim_or_all_evidence_ids(claim, context), context.missing_evidence()

    if key in {"workorder_decision", "workorder_suggestion"}:
        if not workorder:
            return "当前未生成工单建议。", [], context.missing_evidence()
        lines = [
            f"是否建议：{'是' if workorder.need_workorder else '否'}",
            f"原因：{workorder.reason or '当前证据不足，暂不建议自动派发工单'}",
            f"优先级：{workorder.priority} {workorder.priority_label}".strip(),
        ]
        if workorder.title:
            lines.append(f"工单标题：{workorder.title}")
        return _bullets(lines, "当前未生成工单建议。"), _claim_evidence_ids(context, "workorder_decision"), context.missing_evidence()

    if key in {"limitations", "missing_evidence", "prediction_boundary", "data_boundary", "risk_and_limitations"}:
        return _limitations_text(context), [], context.missing_evidence()

    if key == "alarm_explanation":
        evidence_ids = context.evidence_ids("knowledge", "kb")
        snippets = list(context.knowledge_artifact.snippets if context.knowledge_artifact else [])
        content = _first_text([*(snippets[:2]), analysis.conclusion if analysis else ""])
        return _truncate(content, 420), evidence_ids, context.missing_evidence()

    if key in {"severity_assessment", "impact_assessment"}:
        claim = context.claim("risk_assessment", "diagnosis_summary")
        content = _first_text([claim.statement if claim else "", analysis.risk_notice if analysis else ""])
        return content or "由于缺少完整实时状态，当前只做辅助风险判断。", _claim_or_all_evidence_ids(claim, context), context.missing_evidence()

    if key == "answer":
        evidence_ids = context.evidence_ids("knowledge", "kb")
        content = _first_text([
            *(context.knowledge_artifact.snippets[:2] if context.knowledge_artifact else []),
            analysis.conclusion if analysis else "",
        ])
        return _truncate(content, 520), evidence_ids, context.missing_evidence()

    if key == "scope":
        return "以上回答仅适用于本次检索命中的手册/知识库范围，不能替代实时设备状态判断。", context.evidence_ids("knowledge", "kb"), []

    if key == "safety_note":
        return "这只是知识库/手册解释，不代表设备当前一定存在该故障；如需判断当前告警状态，需要查询实时运行数据或告警记录。", [], []

    if key in {"key_metrics", "watch_metrics"}:
        evidence_ids = context.evidence_ids("metric", "timeseries", "sql")
        return _bullets(_evidence_summaries(context, evidence_ids, limit=contract.max_bullets_per_section), "本次未获得足够的关键指标数据。"), evidence_ids, context.missing_evidence()

    return None


def _generic_section(
    key: str,
    context: _RenderContext,
    contract: OutputContract,
) -> tuple[str, list[str], list[str]]:
    analysis = context.analysis_artifact
    if analysis is None:
        return "", [], context.missing_evidence()
    if key in {"excluded_causes", "timeline"}:
        return "当前证据不足，暂未形成独立时间线或排除项。", context.evidence_ids(), context.missing_evidence()
    content = _first_text([analysis.conclusion, analysis.risk_notice or ""])
    return _truncate(content, 420), context.evidence_ids(), context.missing_evidence()


def _action_request_sections(context: _RenderContext, contract: OutputContract) -> list[RenderedSection]:
    action = context.decision.action_type or context.decision.user_goal or "该动作"
    return [
        RenderedSection(
            key="cannot_execute",
            title="无法直接执行",
            content=(
                f"我不能直接执行“{_strip_unsafe_action_text(action)}”操作。\n"
                "这类操作可能影响设备状态、生产安全或工单流程，必须由有权限人员确认后执行。"
            ),
        ),
        RenderedSection(
            key="available_help",
            title="可提供的帮助",
            content=_numbered(["操作建议", "工单草稿", "审批前检查清单", "风险提示"], ""),
        ),
        RenderedSection(
            key="required_confirmation",
            title="执行前需要确认",
            content=_numbered(["设备对象", "当前运行状态", "影响范围", "审批人/权限", "回退方案"], ""),
        ),
        RenderedSection(
            key="next_step",
            title="建议下一步",
            content="建议先补充设备对象、当前状态和操作原因；我可以整理草稿，供人工审核后处理。",
        ),
    ]


def _join_sections(contract: OutputContract, sections: list[RenderedSection]) -> str:
    if contract.task_type.value == "report_generation":
        report_line = next((section.content for section in sections if section.key == "report_link"), "")
        summary = next((section.content for section in sections if section.key == "report_summary"), "")
        boundary = next((section.content for section in sections if section.key == "risk_and_limitations"), "")
        return "\n\n".join(item for item in [report_line, f"报告摘要：\n{summary}", f"边界说明：{boundary}"] if item)
    if contract.task_type.value == "action_request":
        return "\n\n".join(section.content for section in sections if section.content)
    chunks = []
    for section in sections:
        if not section.content:
            continue
        if "\n" in section.content:
            chunks.append(f"{section.title}：\n{section.content}")
        else:
            chunks.append(f"{section.title}：{section.content}")
    return "\n\n".join(chunks)


def _claim_or_all_evidence_ids(claim: Claim | None, context: _RenderContext) -> list[str]:
    if claim is not None and claim.supporting_evidence_ids:
        return _dedupe(claim.supporting_evidence_ids)
    return context.evidence_ids()


def _claim_evidence_ids(context: _RenderContext, *claim_types: str) -> list[str]:
    refs: list[str] = []
    for claim in context.claims:
        if claim.claim_type in claim_types:
            refs.extend(claim.supporting_evidence_ids)
    return _dedupe(refs) or context.evidence_ids()


def _evidence_summaries(context: _RenderContext, evidence_ids: list[str], *, limit: int) -> list[str]:
    by_id = {item.evidence_id: item for item in context.evidence_items}
    selected: list[EvidenceItem] = [by_id[evidence_id] for evidence_id in evidence_ids if evidence_id in by_id]
    if not selected and context.evidence_items:
        selected = context.evidence_items
    return _limited_items([item.summary or item.title for item in selected], limit=limit)


def _limitations_text(context: _RenderContext) -> str:
    missing = context.missing_evidence()
    if missing:
        return (
            "目前不能确认唯一根因，原因是缺少以下证据：\n"
            f"{_numbered(missing, '缺少可用补充证据。')}\n"
            "因此，本次只能给出候选判断，不能直接判定为确定故障原因。"
        )
    quality = context.evidence_bundle.quality_checks if context.evidence_bundle else {}
    if quality and quality.get("all_claims_have_evidence") and quality.get("no_dangling_evidence_refs"):
        return "本次结论仅基于当前可查询到的数据、知识库和证据链；高风险操作仍需现场人工确认。"
    return "当前证据链不完整，结论仅作辅助参考，需补充实时数据、历史趋势或现场记录后确认。"


def _numbered(items: list[str], fallback: str) -> str:
    cleaned = _limited_items(items, limit=8)
    if not cleaned:
        return f"1. {fallback}" if fallback else ""
    return "\n".join(f"{index}. {item}" for index, item in enumerate(cleaned, start=1))


def _bullets(items: list[str], fallback: str) -> str:
    cleaned = _limited_items(items, limit=8)
    if not cleaned:
        cleaned = [fallback] if fallback else []
    return "\n".join(f"- {item}" for item in cleaned)


def _limited_items(items: list[str], *, limit: int) -> list[str]:
    return [_truncate(item, 150) for item in _dedupe(str(item).strip() for item in items if str(item).strip())[:limit]]


def _dedupe(items: Any) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _first_text(items: list[str]) -> str:
    return next((str(item).strip() for item in items if str(item or "").strip()), "")


def _truncate(text: str, limit: int) -> str:
    text = " ".join(str(text or "").strip().split())
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _truncate_content(content: str, max_chars: int | None) -> str:
    if max_chars is None or len(content) <= max_chars:
        return content
    return f"{content[:max_chars].rstrip()}\n\n证据不足说明：内容已按模板长度要求截断，完整证据请查看结构化产物。"


def _contains_dangerous_completion(text: str) -> bool:
    return any(pattern in text for pattern in DANGEROUS_ACTION_COMPLETION_PATTERNS)


def _strip_unsafe_action_text(text: str) -> str:
    text = re.sub(r"^(?:已|已经|我已经|请|帮我|直接)", "", str(text or "")).strip(" ：:，,。")
    return text or "该动作"
