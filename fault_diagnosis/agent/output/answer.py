"""Build V2 output frames from structured runtime payloads."""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    EvidenceBundle,
    FaultCodeEntry,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
)
from fault_diagnosis.domain.diagnosis.steps.knowledge_lookup import extract_fault_codes_from_text
from ..contracts import NodeResult, OutputFrame


def build_output_frame(
    *,
    status: str = "completed",
    artifacts: dict[str, Any] | None = None,
    evidence_bundle: EvidenceBundle | dict[str, Any] | None = None,
    node_results: list[NodeResult] | list[dict[str, Any]] | None = None,
    requested_variant: str | None = None,
    error: dict[str, Any] | None = None,
    cancelled: bool = False,
    cancel_reason: str | None = None,
) -> OutputFrame:
    """Return the internal V2 output frame before frontend compatibility projection."""

    artifact_map = dict(artifacts or {})
    bundle = _model(evidence_bundle, EvidenceBundle)
    sql_artifact = _model(artifact_map.get("sql_artifact"), SqlStepArtifact)
    knowledge_artifact = _model(artifact_map.get("knowledge_artifact"), KnowledgeStepArtifact)
    analysis_artifact = _model(artifact_map.get("analysis_artifact"), AnalysisStepArtifact)
    report_artifact = _model(artifact_map.get("report_artifact"), ReportStepArtifact)
    variant = requested_variant or _infer_variant(
        status=status,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
        report_artifact=report_artifact,
        cancelled=cancelled,
    )
    final_answer = _render_answer(
        variant=variant,
        status=status,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
        analysis_artifact=analysis_artifact,
        report_artifact=report_artifact,
        evidence_bundle=bundle,
        error=error,
        cancel_reason=cancel_reason,
    )
    status_brief = _status_brief(sql_artifact=sql_artifact, evidence_bundle=bundle, status=status)
    diagnosis_payload = {
        "sql_artifact": _dump(sql_artifact),
        "knowledge_artifact": _dump(knowledge_artifact),
        "analysis_artifact": _dump(analysis_artifact),
        "report_artifact": _dump(report_artifact),
        "evidence_bundle": _dump(bundle),
        "node_results": [_dump(item) for item in node_results or []],
    }
    guardrail = {
        "status": status,
        "cancelled": bool(cancelled),
        "missing_evidence": _missing_evidence(bundle, analysis_artifact),
        "final_claim_ids": list(bundle.final_claim_ids if bundle else []),
        "final_claims_without_evidence": _final_claims_without_evidence(bundle),
        "stale_evidence": _stale_evidence(bundle),
    }
    if error:
        guardrail["error"] = dict(error)
    if cancel_reason:
        guardrail["cancel_reason"] = cancel_reason
    return OutputFrame(
        answer_variant=variant,
        final_answer=final_answer,
        status_brief=status_brief,
        diagnosis_report_payload=diagnosis_payload,
        artifact_payload={key: _dump(value) for key, value in artifact_map.items()},
        guardrail_result=guardrail,
    )


def _infer_variant(
    *,
    status: str,
    sql_artifact: SqlStepArtifact | None,
    knowledge_artifact: KnowledgeStepArtifact | None,
    analysis_artifact: AnalysisStepArtifact | None,
    report_artifact: ReportStepArtifact | None,
    cancelled: bool,
) -> str:
    if cancelled or status == "blocked":
        return "blocked"
    if status == "failed":
        return "error"
    if report_artifact and report_artifact.success:
        return "report_ready"
    if analysis_artifact and analysis_artifact.success:
        return "diagnosis_answer"
    if knowledge_artifact and knowledge_artifact.success:
        return "knowledge_answer"
    if sql_artifact and sql_artifact.success:
        return "status_brief"
    return "clarification"


def _render_answer(
    *,
    variant: str,
    status: str,
    sql_artifact: SqlStepArtifact | None,
    knowledge_artifact: KnowledgeStepArtifact | None,
    analysis_artifact: AnalysisStepArtifact | None,
    report_artifact: ReportStepArtifact | None,
    evidence_bundle: EvidenceBundle | None,
    error: dict[str, Any] | None,
    cancel_reason: str | None,
) -> str:
    if variant == "blocked":
        if status == "cancelled":
            return "已停止本次处理，未继续执行后续步骤。"
        message = str((error or {}).get("message") or "").strip()
        return message or "当前请求被安全边界阻止，未执行受限动作。"
    if variant == "error":
        message = str((error or {}).get("message") or "").strip()
        return message or "V2 执行失败，未生成可靠诊断结果。"
    if variant == "report_ready" and report_artifact:
        link = report_artifact.report_url or report_artifact.report_filename or ""
        suffix = f" 报告地址：{link}" if link else ""
        return f"报告已生成。{suffix}".strip()
    if variant == "diagnosis_answer":
        claim_answer = _render_claim_answer(evidence_bundle)
        if claim_answer:
            stale_lines = _stale_disclosure_lines(evidence_bundle)
            return "\n".join([claim_answer, *stale_lines]).strip()
        if evidence_bundle and evidence_bundle.claims and not evidence_bundle.final_claim_ids:
            pending = _pending_claim_lines(evidence_bundle)
            stale_lines = _stale_disclosure_lines(evidence_bundle)
            return "\n".join([*pending, *stale_lines]).strip()
        if not analysis_artifact:
            return "待确认/需补充：当前诊断判断缺少可引用证据，不能作为最终结论。"
        parts = [_line("诊断结论", analysis_artifact.conclusion)]
        parts.extend(_numbered("依据", analysis_artifact.basis))
        parts.extend(_numbered("建议", analysis_artifact.recommendations))
        missing = _missing_evidence(evidence_bundle, analysis_artifact)
        if missing:
            parts.extend(_numbered("仍需补充", missing))
        if analysis_artifact.risk_notice:
            parts.append(_line("风险提示", analysis_artifact.risk_notice))
        stale_lines = _stale_disclosure_lines(evidence_bundle)
        return "\n".join([*(item for item in parts if item), *stale_lines]).strip()
    if variant == "knowledge_answer":
        return _render_knowledge_answer(knowledge_artifact, evidence_bundle)
    if variant == "status_brief":
        return _status_brief(sql_artifact=sql_artifact, evidence_bundle=evidence_bundle, status=status)
    if cancel_reason:
        return f"需要补充信息后才能继续处理。当前停止原因：{cancel_reason}"
    return "需要补充设备、故障码或时间窗口等关键信息后才能继续处理。"


def _status_brief(
    *,
    sql_artifact: SqlStepArtifact | None,
    evidence_bundle: EvidenceBundle | None,
    status: str,
) -> str:
    if sql_artifact:
        data_state = f" 数据状态：{sql_artifact.data_state}。" if sql_artifact.data_state else ""
        return f"{sql_artifact.summary}{data_state}".strip()
    summaries = [
        item.summary
        for item in (evidence_bundle.evidence_items if evidence_bundle else [])
        if getattr(item, "summary", "")
    ][:3]
    if summaries:
        return "；".join(summaries)
    return f"V2 runtime {status}."


def _render_knowledge_answer(
    knowledge_artifact: KnowledgeStepArtifact | None,
    evidence_bundle: EvidenceBundle | None,
) -> str:
    entries = list(knowledge_artifact.fault_code_entries if knowledge_artifact else [])
    if entries:
        return _render_fault_code_entries(knowledge_artifact, entries)

    snippets = _dedupe(list(knowledge_artifact.snippets if knowledge_artifact else []))
    if not snippets and evidence_bundle:
        snippets = _dedupe(
            [
                item.summary
                for item in evidence_bundle.evidence_items
                if item.evidence_type in {"fault_code_reference", "manual_reference"} and item.summary
            ]
        )
    codes = _dedupe(list(knowledge_artifact.fault_codes if knowledge_artifact else []))
    parts: list[str] = []
    if codes:
        parts.append(_line("故障码", "、".join(codes)))
    parts.extend(_numbered("知识库结果", [_clean_knowledge_snippet(item) for item in snippets[:3]]))
    stale_lines = _stale_disclosure_lines(evidence_bundle)
    if parts:
        return "\n".join([*parts, *stale_lines]).strip()
    return "未检索到当前权限范围内可用的故障码说明。"


def _render_fault_code_entries(knowledge_artifact: KnowledgeStepArtifact | None, entries: list[FaultCodeEntry]) -> str:
    requested_codes = extract_fault_codes_from_text(knowledge_artifact.query if knowledge_artifact else "")
    exact_entries = [
        entry
        for entry in entries
        if entry.match_type == "exact_match" and (not requested_codes or entry.code.upper() in requested_codes)
    ]
    if not exact_entries:
        requested = "、".join(requested_codes) if requested_codes else "请求中的故障码"
        lines = [f"未找到精确匹配：{requested}。"]
        lines.extend(_numbered("候选", [_candidate_line(entry) for entry in entries]))
        return "\n".join(lines).strip()

    entry = exact_entries[0]
    detailed = _wants_fault_code_detail(knowledge_artifact.query if knowledge_artifact else "")
    return _render_fault_code_entry_detail(entry) if detailed else _render_fault_code_entry_concise(entry)


def _render_fault_code_entry_concise(entry: FaultCodeEntry) -> str:
    parts = [
        _line("一句话解释", f"{entry.code}：{_entry_meaning(entry)}"),
        _line("可能原因", _manual_or_missing(entry.cause)),
        _line("建议处理", _manual_or_missing(entry.remedy)),
        _line("相关参数", _references_text(entry.references)),
        _line("来源", _source_text(entry)),
        "详细信息：如需查看信息类别、驱动对象、传播、反应、应答等手册字段，请继续追问“详细点”或“手册字段”。",
    ]
    return "\n".join(item for item in parts if item).strip()


def _render_fault_code_entry_detail(entry: FaultCodeEntry) -> str:
    parts = [
        _render_fault_code_entry_concise(entry),
        "",
        "详细手册信息：",
        f"- 故障码：{entry.code}",
        f"- 标题：{_manual_or_missing(entry.title)}",
        f"- 含义：{_manual_or_missing(entry.meaning)}",
        f"- 信息类别：{_manual_or_missing(entry.category)}",
        f"- 驱动对象：{_manual_or_missing(entry.drive_object)}",
        f"- 组件：{_manual_or_missing(entry.component)}",
        f"- 传播：{_manual_or_missing(entry.propagation)}",
        f"- 反应：{_manual_or_missing(entry.reaction)}",
        f"- 应答：{_manual_or_missing(entry.acknowledgement)}",
        f"- 原因：{_manual_or_missing(entry.cause)}",
        f"- 处理：{_manual_or_missing(entry.remedy)}",
        f"- 参见：{_references_text(entry.references)}",
        f"- 匹配方式：{entry.match_type}",
        f"- 来源文件：{_manual_or_missing(entry.source_file)}",
        f"- 页码：{_manual_or_missing(entry.page)}",
    ]
    return "\n".join(parts).strip()


def _entry_meaning(entry: FaultCodeEntry) -> str:
    return entry.meaning or entry.title or "手册未明确给出"


def _manual_or_missing(value: Any) -> str:
    text = str(value or "").strip()
    return text or "手册未明确给出"


def _references_text(references: list[str]) -> str:
    return "、".join(_dedupe(references)) if references else "手册未明确给出"


def _source_text(entry: FaultCodeEntry) -> str:
    source = entry.source_file or "知识库"
    page = f"，第 {entry.page} 页" if entry.page else ""
    return f"{source}{page}"


def _candidate_line(entry: FaultCodeEntry) -> str:
    source = _source_text(entry)
    title = entry.title or entry.meaning or "手册未明确给出"
    return f"{entry.code}：{title}（来源：{source}，匹配方式：{entry.match_type}）"


def _wants_fault_code_detail(query: str) -> bool:
    return any(keyword in str(query or "") for keyword in ("详细", "原文", "手册字段", "完整字段", "展开"))


def _clean_knowledge_snippet(value: str) -> str:
    lines = []
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("来源：", "来源文件：", "file_id：", "source_type：", "extract_backend：", "来源页码：", "检索方式：")):
            continue
        lines.append(line.removeprefix("文档片段：").strip())
    text = "；".join(lines).strip()
    return text[:600] if text else str(value or "").strip()[:600]


def _missing_evidence(
    evidence_bundle: EvidenceBundle | None,
    analysis_artifact: AnalysisStepArtifact | None,
) -> list[str]:
    items: list[str] = []
    if evidence_bundle:
        for claim in evidence_bundle.claims:
            items.extend(claim.missing_evidence)
        quality_missing = evidence_bundle.quality_checks.get("missing_evidence")
        if isinstance(quality_missing, list):
            items.extend(str(item) for item in quality_missing)
    if analysis_artifact:
        items.extend(analysis_artifact.missing_information)
    return _dedupe(items)


def _render_claim_answer(evidence_bundle: EvidenceBundle | None) -> str:
    if not evidence_bundle or not evidence_bundle.final_claim_ids:
        return ""
    by_id = {claim.claim_id: claim for claim in evidence_bundle.claims}
    final_claims = [
        by_id[claim_id]
        for claim_id in evidence_bundle.final_claim_ids
        if claim_id in by_id and by_id[claim_id].supporting_evidence_ids
    ]
    if not final_claims:
        return ""
    evidence_by_id = {item.evidence_id: item for item in evidence_bundle.evidence_items}
    parts: list[str] = []
    parts.extend(_numbered("诊断结论", [claim.statement for claim in final_claims]))
    supporting = _dedupe(
        [
            evidence_by_id[evidence_id].summary
            for claim in final_claims
            for evidence_id in claim.supporting_evidence_ids
            if evidence_id in evidence_by_id and evidence_by_id[evidence_id].summary
        ]
    )
    parts.extend(_numbered("依据", supporting[:5]))
    missing = _dedupe([item for claim in final_claims for item in claim.missing_evidence])
    if missing:
        parts.extend(_numbered("仍需补充", missing))
    return "\n".join(parts)


def _pending_claim_lines(evidence_bundle: EvidenceBundle) -> list[str]:
    unsupported = [
        claim.statement
        for claim in evidence_bundle.claims
        if not claim.supporting_evidence_ids and claim.statement
    ]
    missing = _missing_evidence(evidence_bundle, None)
    values = unsupported or missing or ["当前诊断判断缺少可引用证据，不能作为最终结论。"]
    lines = _numbered("待确认/需补充", values)
    if missing and unsupported:
        lines.extend(_numbered("缺失证据", missing))
    return lines


def _final_claims_without_evidence(evidence_bundle: EvidenceBundle | None) -> list[str]:
    if not evidence_bundle:
        return []
    final = set(evidence_bundle.final_claim_ids)
    return [
        claim.claim_id
        for claim in evidence_bundle.claims
        if (claim.claim_id in final or claim.status == "final") and not claim.supporting_evidence_ids
    ]


def _stale_evidence(evidence_bundle: EvidenceBundle | None) -> list[dict[str, Any]]:
    if not evidence_bundle:
        return []
    stale_ids = set(str(item) for item in evidence_bundle.quality_checks.get("stale_evidence_ids", []) or [])
    result = []
    for item in evidence_bundle.evidence_items:
        freshness = str(getattr(item.quality, "freshness", "") or item.metadata.get("freshness", "")).lower()
        if item.evidence_id in stale_ids or freshness == "stale" or item.evidence_type == "stale_evidence_disclosure":
            result.append({"evidence_id": item.evidence_id, "summary": item.summary, "evidence_type": item.evidence_type})
    return result


def _stale_disclosure_lines(evidence_bundle: EvidenceBundle | None) -> list[str]:
    stale = _stale_evidence(evidence_bundle)
    if not stale:
        return []
    summaries = _dedupe([item.get("summary") for item in stale if item.get("summary")])
    return _numbered("时效性提示", summaries[:3] or ["存在滞后证据，不能代表当前实时状态。"])


def _numbered(title: str, values: list[Any]) -> list[str]:
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    if not cleaned:
        return []
    return [f"{title}：", *[f"{index}. {item}" for index, item in enumerate(cleaned, start=1)]]


def _line(title: str, value: Any) -> str:
    text = str(value or "").strip()
    return f"{title}：{text}" if text else ""


def _dedupe(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _model(value: Any, model_type: Any) -> Any:
    if isinstance(value, model_type):
        return value
    if isinstance(value, dict):
        return model_type.model_validate(value)
    return None


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return value
