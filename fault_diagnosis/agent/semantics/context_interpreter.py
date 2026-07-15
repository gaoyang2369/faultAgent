"""ACL 投影后的上下文语义输入；这里绝不暴露 Artifact 标识。"""

from __future__ import annotations

from typing import Any

from fault_diagnosis.domain.canonical_turn import ContextCandidate


def project_context_semantic_input(candidates: list[ContextCandidate]) -> tuple[dict[str, Any], ...]:
    """将已授权候选压缩成供模型筛选的不可逆摘要。"""

    return tuple({
        "ordinal": index,
        "artifact_type": item.artifact_type,
        "asset_refs": list(item.asset_refs),
        "fault_codes": list(item.fault_codes),
        "has_time_window": bool(item.time_window),
        "produced_by_immediately_previous_turn": item.produced_by_immediately_previous_turn,
        "completed": item.completed,
        "reportable": item.reportable,
        "freshness_state": item.freshness_state,
        "source_kind": item.source_kind,
        "pending_action_type": item.pending_action_type,
    } for index, item in enumerate(candidates, start=1))


def project_pending_semantic_summary(waiting: Any) -> dict[str, Any] | None:
    """仅投影待澄清的类型和缺槽，不携带对话或内部引用。"""

    if waiting is None:
        return None
    payload = waiting.model_dump(mode="json") if hasattr(waiting, "model_dump") else {}
    return {
        "kind": payload.get("kind") or "clarification",
        "missing_slots": list(payload.get("missing_slots") or []),
    }
