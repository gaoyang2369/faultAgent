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
from ...security.assets import resolve_asset
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
        if key == "report_status":
            if _report_generated(context):
                return "报告已生成。", [], []
            if _report_blocked_by_authorization(context):
                return "报告未生成：当前身份无报告生成权限，仅返回权限范围内的状态摘要。", [], []
            return "报告未生成。", [], []
        if key == "report_title":
            title = _report_title(context)
            return title, [], []
        if key == "report_summary":
            summary_items = _limited_items(
                [
                    analysis.conclusion if analysis else "",
                    *(analysis.basis if analysis else []),
                    *(analysis.recommendations if analysis else []),
                ],
                limit=5,
            )
            fallback = (
                "权限受限摘要已返回，但没有生成诊断报告文件。"
                if not _report_generated(context)
                else "报告已生成，但当前结构化摘要不足。"
            )
            return _numbered(summary_items, fallback), context.evidence_ids(), context.missing_evidence()
        if key == "report_link":
            return _report_link(context), [], []
        if key == "missing_evidence_notice":
            return _limitations_text(context), [], context.missing_evidence()

    if contract.task_type.value == "permission_scope_query":
        if key == "identity_scope":
            return _identity_scope_text(context), [], []
        if key == "accessible_assets":
            return _accessible_assets_text(context), [], []
        if key == "available_capabilities":
            return _available_capabilities_text(context), [], []
        if key == "unavailable_capabilities":
            return _unavailable_capabilities_text(context), [], []

    if key in {"diagnosis_conclusion", "brief_judgement", "event_summary", "health_score"}:
        data_state = _sql_data_state(context)
        if key == "brief_judgement" and data_state in {"out_of_scope", "blocked", "empty"}:
            return "当前没有可用的授权运行数据，不能判断设备正常或异常。", [], context.missing_evidence()
        claim = context.claim("diagnosis_summary")
        content = _first_text([claim.statement if claim else "", analysis.conclusion if analysis else ""])
        if context.missing_evidence() and content and "不能" not in content:
            content = f"{content}\n目前不能确认唯一根因，需补充证据后再定论。"
        return content, _claim_or_all_evidence_ids(claim, context), context.missing_evidence()

    if key in {"current_status", "current_alarm_status", "trend_analysis"}:
        data_state = _sql_data_state(context)
        if data_state in {"out_of_scope", "blocked"}:
            return _sql_boundary_text(context), [], context.missing_evidence()
        if data_state == "empty":
            return "授权范围内未查询到该设备的运行数据，无法判断当前状态。", [], context.missing_evidence()
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
        status = next((section.content for section in sections if section.key == "report_status"), "")
        title = next((section.content for section in sections if section.key == "report_title"), "")
        summary = next((section.content for section in sections if section.key == "report_summary"), "")
        link = next((section.content for section in sections if section.key == "report_link"), "")
        missing = next((section.content for section in sections if section.key == "missing_evidence_notice"), "")
        return "\n\n".join(
            item
            for item in [
                f"报告状态：{status}",
                f"报告标题：{title}",
                f"报告链接：{link}",
                f"报告摘要：\n{summary}",
                f"证据不足提示：{missing}",
            ]
            if item
        )
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
    data_state = _sql_data_state(context)
    if data_state in {"out_of_scope", "blocked"}:
        return _sql_boundary_text(context)
    if data_state == "empty":
        return "授权范围内没有返回可用运行数据，本次不能判断设备正常、异常或根因。"
    if context.decision.primary_task_type == "report_generation" and _report_blocked_by_authorization(context):
        return "当前身份缺少报告生成权限，本次不形成故障诊断报告、根因结论或健康评估。"
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


def _report_title(context: _RenderContext) -> str:
    artifact = context.report_artifact
    if artifact is not None:
        title = str(getattr(artifact, "report_title", "") or "").strip()
        if title:
            return title
    if context.decision.primary_task_type == "report_generation" and not _report_generated(context):
        return "DCMA 权限受限状态摘要"
    goal = str(context.decision.user_goal or "").strip()
    if "运行" in goal and "报告" in goal:
        return "DCMA 运行诊断报告"
    if "RCA" in goal.upper() or "根因" in goal:
        return "DCMA 根因分析报告"
    return "DCMA 故障诊断报告"


def _report_link(context: _RenderContext) -> str:
    if not _report_generated(context):
        return "报告未生成"
    artifact = context.report_artifact
    if artifact is None:
        return "未返回报告链接"
    explicit_url = str(getattr(artifact, "report_url", "") or "").strip()
    if explicit_url:
        return explicit_url
    save_result_url = extract_report_url(artifact.save_result)
    return save_result_url or artifact.report_filename or "未返回报告链接"


def _report_generated(context: _RenderContext) -> bool:
    artifact = context.report_artifact
    if artifact is None or not artifact.success:
        return False
    return bool(
        str(getattr(artifact, "report_url", "") or "").strip()
        or str(getattr(artifact, "report_filename", "") or "").strip()
        or extract_report_url(getattr(artifact, "save_result", "") or "")
    )


def _report_blocked_by_authorization(context: _RenderContext) -> bool:
    authorization = context.decision.authorization or {}
    denied_nodes = authorization.get("denied_nodes") if isinstance(authorization, dict) else {}
    return (
        isinstance(denied_nodes, dict)
        and denied_nodes.get("report") == "missing_report_permission"
    ) or (
        isinstance(authorization, dict)
        and authorization.get("mode") == "degrade"
        and not _report_generated(context)
    )


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


def _sql_data_state(context: _RenderContext) -> str:
    return str(getattr(context.sql_artifact, "data_state", "") or "").strip()


def _sql_boundary_text(context: _RenderContext) -> str:
    error = str(getattr(context.sql_artifact, "error", "") or "").strip()
    return error or "请求设备不在当前账号授权范围内，未执行运行数据查询。"


def _role_label(role: str) -> str:
    return {"guest": "游客", "engineer": "维修工程师", "admin": "管理员"}.get(role, role or "未知")


def _data_scope(context: _RenderContext) -> dict[str, Any]:
    authorization = context.decision.authorization or {}
    if isinstance(authorization, dict) and isinstance(authorization.get("data_scope"), dict):
        return dict(authorization["data_scope"])
    return dict(context.decision.access_scope or {})


def _authorized_asset_labels(context: _RenderContext) -> list[str]:
    labels: list[str] = []
    for asset_id in _data_scope(context).get("asset_ids") or []:
        record = resolve_asset(str(asset_id))
        labels.append(record.display_name if record is not None else str(asset_id))
    return _dedupe(labels)


def _identity_scope_text(context: _RenderContext) -> str:
    scope = _data_scope(context)
    purpose = str(scope.get("authorized_purpose") or "")
    role = str(scope.get("role") or "")
    if not role:
        role = "guest" if purpose == "status_or_visualization_only" else "engineer/admin"
    return f"{_role_label(role)}；权限边界来自服务端会话，不使用前端传入身份。"


def _accessible_assets_text(context: _RenderContext) -> str:
    scope = _data_scope(context)
    labels = _authorized_asset_labels(context)
    asset_text = "、".join(labels) if labels else "当前账号未配置具体设备范围"
    table_text = "、".join(str(item) for item in (scope.get("allowed_tables") or [])) or "未配置数据表"
    max_hours = scope.get("max_lookback_hours")
    window = f"最近 {max_hours} 小时" if max_hours else "授权时间窗口"
    return f"{asset_text}；可查询数据表：{table_text}；数据窗口：{window}。"


def _available_capabilities_text(context: _RenderContext) -> str:
    purpose = str(_data_scope(context).get("authorized_purpose") or "")
    if purpose == "status_or_visualization_only":
        return "查看授权设备最近一小时运行状态；查询公开知识库处理意见。"
    return "查看授权设备运行数据；进行授权范围内的诊断、健康评估和报告草稿生成。"


def _unavailable_capabilities_text(context: _RenderContext) -> str:
    purpose = str(_data_scope(context).get("authorized_purpose") or "")
    if purpose == "status_or_visualization_only":
        return "不能访问未授权设备；不能生成诊断报告；不能形成故障诊断、根因结论、健康评估或工单派发。"
    return "不能直接执行设备控制、参数修改、告警关闭或工单派发；这些动作仍需人工确认。"
