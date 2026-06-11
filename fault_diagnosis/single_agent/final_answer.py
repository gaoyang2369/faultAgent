"""Final user-answer formatting for the restricted single-agent runtime."""

from __future__ import annotations

from ..diagnosis.contracts import AnalysisStepArtifact


def _clean_items(items: list[str]) -> list[str]:
    cleaned = [item.strip() for item in items if item and item.strip()]
    return list(dict.fromkeys(cleaned))


def _strip_action_prefix(text: str) -> str:
    for prefix in (
        "立即处置：",
        "手册建议处置：",
        "故障码处置：",
        "验证步骤：",
        "关联排查：",
        "数据关联排查：",
        "根因排查：",
        "复位后验证：",
        "闭环确认：",
    ):
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _numbered_lines(items: list[str], fallback: str, *, limit: int) -> str:
    cleaned = [_truncate(_strip_action_prefix(item), 110) for item in _clean_items(items)]
    if not cleaned:
        return f"1. {fallback}"
    return "\n".join(f"{index}. {item}" for index, item in enumerate(cleaned[:limit], start=1))


def _inline_summary(items: list[str], fallback: str, *, limit: int) -> str:
    cleaned = _clean_items(items)
    if not cleaned:
        return fallback
    return "；".join(_truncate(item, 74) for item in cleaned[:limit])


def _confidence_summary(analysis_artifact: AnalysisStepArtifact) -> str:
    details = _clean_items(analysis_artifact.confidence_details)
    if not details:
        return analysis_artifact.confidence
    compact_details = []
    for item in details[:4]:
        if "：" in item:
            label, value = item.split("：", 1)
            compact_details.append(f"{label.strip()} {value.split('，', 1)[0].strip()}")
        else:
            compact_details.append(item)
    return f"{analysis_artifact.confidence}（{'；'.join(compact_details)}）"


def build_final_answer_fallback(analysis_artifact: AnalysisStepArtifact, report_name: str | None = None) -> str:
    """Build a concise deterministic final answer from the analysis artifact.

    The detailed facts, possible causes, verification checklist and evidence are
    intentionally left in ``analysis_artifact`` for structured frontend cards
    and reports. ``final_content`` stays short enough for the chat transcript.
    """

    action_lines = _numbered_lines(analysis_artifact.recommendations, "按现场规程确认安全状态后再处理", limit=3)
    basis_summary = _inline_summary(analysis_artifact.basis, "暂无明确数据支撑", limit=4)
    risk_notice = analysis_artifact.risk_notice or "当前未发现额外风险提示。"

    sections = [
        f"【结论】{_truncate(analysis_artifact.conclusion, 180)}",
        f"【优先动作】\n{action_lines}",
        f"【关键依据】{basis_summary}",
        f"【风险提示】{_truncate(risk_notice, 130)}",
        f"【置信度】{_confidence_summary(analysis_artifact)}",
    ]
    if report_name:
        sections.append(f"【报告文件】{report_name}")
    return "\n".join(sections)
