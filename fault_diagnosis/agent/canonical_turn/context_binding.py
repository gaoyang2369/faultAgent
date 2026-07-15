"""Deterministic canonical context binding over ACL-filtered safe candidates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fault_diagnosis.agent.context.artifact_access import artifact_manifest_access
from fault_diagnosis.domain.canonical_turn import (
    BoundCanonicalTurn,
    CanonicalTurnRequest,
    ClarificationOption,
    ClarificationRequirement,
    ContextCandidate,
    ContextSlotBinding,
    GoalContextBinding,
)
from fault_diagnosis.domain.security.assets import asset_is_in_scope, resolve_asset
from fault_diagnosis.domain.security.contracts import AuthContext
from fault_diagnosis.agent.semantics.contracts import ContextSemanticProposal


ARTIFACT_COMPATIBILITY: dict[str, tuple[str, ...]] = {
    "explain_fault_code": ("knowledge_artifact",),
    "diagnose_fault": ("sql_artifact", "analysis_artifact"),
    "resolution_recommendation": ("analysis_artifact", "sql_artifact"),
    "generate_report": ("analysis_artifact", "sql_artifact"),
    "evaluate_workorder_need": ("analysis_artifact", "report_artifact"),
    "create_workorder_draft": ("report_artifact", "analysis_artifact"),
    "check_runtime_status": ("sql_artifact",),
    "compare_runtime_status": ("comparison_artifact", "sql_artifact"),
}
_MULTI_ASSET = {"check_runtime_status", "compare_runtime_status", "diagnose_fault"}


def project_authorized_context_candidates(
    package: dict[str, Any] | None,
    auth: AuthContext,
) -> list[ContextCandidate]:
    """Project only visible manifest metadata, CaseState refs and PendingActions."""

    context = package or {}
    previous_ids = {
        str(item.get("artifact_id") or "")
        for item in (context.get("immediately_previous_assistant_turn") or {}).get("produced_artifacts", [])
        if isinstance(item, dict)
    }
    candidates: list[ContextCandidate] = []
    visible: dict[str, ContextCandidate] = {}
    for raw in context.get("artifact_manifests") or []:
        if not isinstance(raw, dict) or not artifact_manifest_access(raw, auth).allowed:
            continue
        artifact_id = str(raw.get("artifact_id") or "")
        if not artifact_id:
            continue
        lineage = raw.get("lineage") if isinstance(raw.get("lineage"), dict) else {}
        candidate = ContextCandidate(
            candidate_id=f"artifact:{artifact_id}",
            artifact_ref=artifact_id,
            artifact_type=str(raw.get("artifact_type") or "") or None,
            asset_refs=_assets(lineage.get("subject_device_refs") or raw.get("device_refs")),
            fault_codes=_texts(lineage.get("fault_code_refs") or raw.get("fault_code_refs")),
            time_window=_window(raw),
            produced_turn_id=str(raw.get("turn_id") or "") or None,
            produced_by_immediately_previous_turn=artifact_id in previous_ids,
            completed=_complete(raw),
            reportable=bool(raw.get("reportable")),
            actionable=bool(raw.get("actionable")),
            freshness_state=str(raw.get("freshness") or raw.get("currentness") or "unknown"),
            lineage_refs=list(dict.fromkeys([artifact_id, *_texts(lineage.get("source_artifact_ids")), *_texts(lineage.get("source_evidence_bundle_ids"))])),
            report_input_snapshot_schema_version=str(raw.get("report_input_snapshot_schema_version") or ""),
            tabular_source_artifact_ref=str(raw.get("report_tabular_source_sql_artifact_id") or "") or None,
            source_kind="artifact_manifest",
        )
        candidates.append(candidate)
        visible[artifact_id] = candidate
    previous_turn = context.get("immediately_previous_assistant_turn") or {}
    previous_is_unavailable = (previous_ids or previous_turn.get("context_unavailable")) and not any(item.completed and item.produced_by_immediately_previous_turn for item in candidates)
    if previous_is_unavailable:
        candidates.append(ContextCandidate(
            candidate_id="previous:unavailable",
            completed=False,
            freshness_state="unavailable",
            source_kind="explicit_reference",
        ))

    case = context.get("latest_case_state") if isinstance(context.get("latest_case_state"), dict) else {}
    case_asset = _assets([case.get("active_asset")])
    case_ref = str(case.get("latest_artifact_id") or "")
    case_is_visible = not case_asset or auth.is_admin() or all(asset_is_in_scope(item, auth.asset_scope) for item in case_asset)
    if case and case_is_visible and (not case_ref or case_ref in visible):
        source = visible.get(case_ref)
        candidates.append((source or ContextCandidate(
            candidate_id=f"case:{case.get('case_id') or 'active'}",
            artifact_ref=case_ref or None,
            artifact_type=str(case.get("latest_artifact_type") or "") or None,
            completed=True,
            source_kind="case_state",
        )).model_copy(update={
            "candidate_id": f"case:{case.get('case_id') or 'active'}",
            "asset_refs": case_asset or (source.asset_refs if source else []),
            "fault_codes": _texts(case.get("active_fault_codes")) or (source.fault_codes if source else []),
            "time_window": case.get("active_time_window") or case.get("data_window") or (source.time_window if source else None),
            "reportable": bool(case.get("reportable")) or bool(source and source.reportable),
            "freshness_state": str(case.get("evidence_freshness") or case.get("freshness_label") or (source.freshness_state if source else "unknown")),
            "source_kind": "case_state",
        }))
        for index, action in enumerate(case.get("pending_actions") or []):
            if not isinstance(action, dict) or action.get("status", "pending") != "pending" or _expired(action.get("expires_at")):
                continue
            ref = next((str(action.get(key) or "") for key in ("source_report_artifact_id", "source_diagnosis_artifact_id", "artifact_id") if action.get(key)), "")
            source = visible.get(ref)
            if source is None:
                continue
            candidates.append(source.model_copy(update={
                "candidate_id": f"pending:{index}:{ref}",
                "source_kind": "pending_action",
                "pending_action_type": str(action.get("action_type") or "") or None,
            }))
    return candidates


class CanonicalContextBinder:
    """The sole production authority for binding existing context to Goals."""

    def bind(
        self,
        request: CanonicalTurnRequest,
        candidates: list[ContextCandidate],
        *,
        context_proposal: ContextSemanticProposal | None = None,
        context_clarification_reason: str | None = None,
    ) -> BoundCanonicalTurn:
        bindings: list[GoalContextBinding] = []
        by_goal: dict[str, GoalContextBinding] = {}
        for goal in request.goals:
            binding = self._bind_goal(request, goal, candidates, context_proposal, context_clarification_reason)
            dependencies = [by_goal[item] for item in goal.dependencies if item in by_goal]
            if len(dependencies) == 1:
                binding = _inherit_dependency_context(binding, dependencies[0])
            bindings.append(binding)
            by_goal[goal.goal_id] = binding
        return BoundCanonicalTurn(canonical_turn=request, goal_bindings=bindings)

    def _bind_goal(self, request, goal, candidates, context_proposal=None, context_clarification_reason=None) -> GoalContextBinding:  # noqa: ANN001
        correction = any(item.kind == "correction_reference" for item in request.current_parse.entities)
        explicit_assets = _assets(_values(goal.resolved_slots.get("device")))
        if correction and explicit_assets:
            explicit_assets = explicit_assets[-1:]
        explicit_codes = _texts(_values(goal.resolved_slots.get("fault_code")))
        explicit_window = goal.resolved_slots.get("time_window")
        if explicit_window and not isinstance(explicit_window, dict):
            explicit_window = {"raw": explicit_window}
        explicit_refs = [item for item in goal.source_requirements if any(candidate.artifact_ref == item for candidate in candidates)]
        prior = _prior_result(request, goal.clause_index)
        explicit_artifact = _clause_source_kind(request, goal.clause_index) == "artifact"
        reusable_runtime = bool(_reuse_requested(request.raw_message))
        if context_proposal and context_proposal.requested_reuse and context_proposal.freshness_intent == "historical_ok":
            reusable_runtime = True
        compatible_types = ARTIFACT_COMPATIBILITY.get(goal.capability, ())
        pool = [item for item in candidates if item.completed and item.artifact_type in compatible_types]
        if goal.capability == "generate_report":
            pool = [item for item in pool if item.artifact_type != "sql_artifact" or item.reportable]
        if explicit_assets:
            pool = [item for item in pool if not item.asset_refs or set(item.asset_refs) == set(explicit_assets)]
        if context_proposal:
            pool = _apply_context_constraints(pool, context_proposal)
        if context_clarification_reason:
            pool = []
        unavailable_previous = prior and any(item.candidate_id == "previous:unavailable" for item in candidates)
        if (explicit_artifact and not explicit_refs) or unavailable_previous:
            pool = []
        selected, ambiguous_source = _select(pool, compatible_types, explicit_refs, context_proposal=context_proposal)
        if goal.capability in {"check_runtime_status", "compare_runtime_status"} and not reusable_runtime:
            selected, ambiguous_source = None, False

        semantic_assets = list(context_proposal.include_asset_refs) if context_proposal else []
        assets = list(explicit_assets or semantic_assets)
        codes = list(explicit_codes)
        window = explicit_window
        context_candidates = [selected] if selected else _focus_candidates(candidates)
        inherited_assets = list(dict.fromkeys(asset for item in context_candidates for asset in item.asset_refs))
        singular = "它们" not in request.raw_message and "分别" not in request.raw_message
        ambiguous_asset = not assets and len(inherited_assets) > 1 and (singular or goal.capability not in _MULTI_ASSET)
        if not assets and not ambiguous_asset:
            assets = inherited_assets
        if not codes:
            codes = list(dict.fromkeys(code for item in context_candidates for code in item.fault_codes))
        if window is None:
            window = next((item.time_window for item in context_candidates if item.time_window), None)

        source_refs = [selected.artifact_ref] if selected and selected.artifact_ref else []
        stale = bool(selected and selected.freshness_state in {"stale", "expired", "refresh_required"})
        if selected and _current_freshness_required(request.raw_message, context_proposal):
            stale = True
        missing_asset = "device" in goal.required_slots and not assets and not ambiguous_asset
        unavailable_source = (explicit_artifact and not explicit_refs) or unavailable_previous
        missing_source = (
            unavailable_source
            or goal.capability in {"generate_report", "create_workorder_draft"} and not explicit_assets
        ) and not source_refs and not goal.dependencies
        clarification = None
        blockers: list[str] = []
        if context_clarification_reason:
            blockers.append(context_clarification_reason)
            clarification = ClarificationRequirement(
                reason_code=context_clarification_reason,
                question="请明确要使用的历史结果或设备范围。",
            )
        elif ambiguous_asset:
            blockers.append("ambiguous_asset_reference")
            clarification = ClarificationRequirement(
                reason_code="ambiguous_asset_reference",
                question="你是指" + "还是".join(inherited_assets) + "？",
                options=[ClarificationOption(option_id=f"asset_{index + 1}", label=asset, asset_ref=asset) for index, asset in enumerate(inherited_assets)],
            )
        elif ambiguous_source:
            blockers.append("ambiguous_source_artifact")
            clarification = ClarificationRequirement(reason_code="ambiguous_source_artifact", question="存在多个可用结果，请明确要基于哪一次结果继续。")
        elif missing_source:
            blockers.append("missing_source")
            clarification = ClarificationRequirement(reason_code="missing_source", question="请先提供可用的诊断、分析或报告结果。")
        elif missing_asset:
            blockers.append("missing_asset")
            clarification = ClarificationRequirement(reason_code="missing_asset", question="请说明要处理的设备。")
        if stale and not reusable_runtime:
            blockers.append("refresh_required")

        provenance = _provenance(selected or (context_candidates[0] if len(context_candidates) == 1 else None))
        asset_status = "explicit" if explicit_assets else "inherited" if assets and not ambiguous_asset else "ambiguous" if ambiguous_asset else "unbound"
        source_status = "unavailable" if unavailable_source else "ambiguous" if ambiguous_source or context_clarification_reason else "refresh_required" if stale else "inherited" if source_refs else "unbound"
        status = "refresh_required" if "refresh_required" in blockers else "needs_clarification" if clarification else "partially_bound" if blockers else "bound"
        return GoalContextBinding(
            goal_id=goal.goal_id,
            capability=goal.capability,
            asset_refs=assets,
            fault_codes=codes,
            time_window=window,
            source_artifact_refs=source_refs,
            slots=[
                ContextSlotBinding(slot_name="asset_refs", status=asset_status, values=assets, provenance="explicit_correction" if correction and explicit_assets else "current_message" if explicit_assets else provenance, candidate_ids=_ids(context_candidates), reason_code=blockers[0] if ambiguous_asset else ""),
                ContextSlotBinding(slot_name="fault_codes", status="explicit" if explicit_codes else "inherited" if codes else "unbound", values=codes, provenance="current_message" if explicit_codes else provenance, candidate_ids=_ids(context_candidates)),
                ContextSlotBinding(slot_name="time_window", status="explicit" if explicit_window else "inherited" if window else "unbound", values=[window] if window else [], provenance="current_message" if explicit_window else provenance, candidate_ids=_ids(context_candidates)),
                ContextSlotBinding(slot_name="source_artifact_refs", status=source_status, values=source_refs, provenance=provenance, candidate_ids=_ids([selected] if selected else []), reason_code="refresh_required" if stale else "ambiguous_source_artifact" if ambiguous_source else ""),
                ContextSlotBinding(slot_name="output_target_refs", status="unbound", values=[]),
            ],
            binding_status=status,
            blockers=blockers,
            clarification=clarification,
        )


def _select(
    candidates: list[ContextCandidate],
    types: tuple[str, ...],
    explicit_refs: list[str],
    *,
    context_proposal: ContextSemanticProposal | None = None,
) -> tuple[ContextCandidate | None, bool]:
    if explicit_refs:
        candidates = [item for item in candidates if item.artifact_ref in explicit_refs]
    if not candidates:
        return None, False
    deduped: dict[str, ContextCandidate] = {}
    for item in candidates:
        key = item.artifact_ref or item.candidate_id
        if key not in deduped or _source_rank(item) < _source_rank(deduped[key]):
            deduped[key] = item
    candidates = list(deduped.values())
    if context_proposal and context_proposal.temporal_relation == "ordinal":
        ordinal = context_proposal.ordinal or 0
        return (candidates[ordinal - 1], False) if 0 < ordinal <= len(candidates) else (None, False)
    rank = lambda item: _selection_rank(item, types, candidates, context_proposal)  # noqa: E731
    best = min(rank(item) for item in candidates)
    matches = [item for item in candidates if rank(item) == best]
    return (matches[0], False) if len(matches) == 1 else (None, True)


def _apply_context_constraints(
    candidates: list[ContextCandidate], proposal: ContextSemanticProposal,
) -> list[ContextCandidate]:
    """在兼容且 ACL 可见的候选集内应用已验证筛选，不接触 Artifact 内容。"""

    result = candidates
    if proposal.include_asset_refs:
        required = set(proposal.include_asset_refs)
        result = [item for item in result if required.issubset(set(item.asset_refs))]
    if proposal.exclude_asset_refs:
        excluded = set(proposal.exclude_asset_refs)
        result = [item for item in result if not excluded.intersection(item.asset_refs)]
    return result


def _selection_rank(
    item: ContextCandidate,
    types: tuple[str, ...],
    candidates: list[ContextCandidate],
    proposal: ContextSemanticProposal | None,
) -> tuple[int, int, int]:
    temporal_rank = 0
    if proposal and proposal.temporal_relation == "latest":
        temporal_rank = -candidates.index(item)
    elif proposal and proposal.temporal_relation == "earliest":
        temporal_rank = candidates.index(item)
    elif proposal and proposal.temporal_relation == "previous":
        temporal_rank = 0 if item.produced_by_immediately_previous_turn else 1
    return temporal_rank, _source_rank(item), types.index(item.artifact_type) if item.artifact_type in types else 99


def _current_freshness_required(text: str, proposal: ContextSemanticProposal | None) -> bool:
    return bool(proposal and proposal.freshness_intent == "current_required") or any(
        marker in text for marker in ("实时", "当前最新", "最新数据")
    )


def _inherit_dependency_context(binding: GoalContextBinding, dependency: GoalContextBinding) -> GoalContextBinding:
    """Carry canonical sibling context across an explicit Goal dependency."""

    slot_by_name = {item.slot_name: item for item in dependency.slots}
    slots = [
        slot_by_name[item.slot_name].model_copy(update={"status": "inherited"}, deep=True)
        if item.status == "unbound" and item.slot_name in slot_by_name and slot_by_name[item.slot_name].values
        else item
        for item in binding.slots
    ]
    assets = binding.asset_refs or dependency.asset_refs
    sources = binding.source_artifact_refs or dependency.source_artifact_refs
    blockers = [
        item for item in binding.blockers
        if not (item == "missing_asset" and assets) and not (item == "missing_source" and sources)
    ]
    clarification = binding.clarification
    if clarification and clarification.reason_code in {"missing_asset", "missing_source"} and not blockers:
        clarification = None
    return binding.model_copy(update={
        "asset_refs": assets,
        "fault_codes": binding.fault_codes or dependency.fault_codes,
        "time_window": binding.time_window or dependency.time_window,
        "source_artifact_refs": sources,
        "slots": slots,
        "blockers": blockers,
        "clarification": clarification,
        "binding_status": "bound" if not blockers else binding.binding_status,
    }, deep=True)


def _focus_candidates(candidates: list[ContextCandidate]) -> list[ContextCandidate]:
    completed = [item for item in candidates if item.completed and item.asset_refs]
    if not completed:
        return []
    rank = min(_source_rank(item) for item in completed)
    return [item for item in completed if _source_rank(item) == rank]


def _source_rank(item: ContextCandidate) -> int:
    if item.produced_by_immediately_previous_turn:
        return 0
    return {"pending_action": 1, "case_state": 2, "artifact_manifest": 3}.get(item.source_kind, 4)


def _provenance(item: ContextCandidate | None) -> str | None:
    if item is None:
        return None
    if item.produced_by_immediately_previous_turn:
        return "immediately_previous_turn"
    return {"pending_action": "pending_action", "case_state": "case_state"}.get(item.source_kind, "recent_thread_artifact")


def _prior_result(request: CanonicalTurnRequest, clause_index: int) -> bool:
    clause = next((item for item in request.current_parse.clauses if item.clause_index == clause_index), None)
    return bool(clause and clause.source and clause.source.source_kind in {"prior_result", "artifact"})


def _clause_source_kind(request: CanonicalTurnRequest, clause_index: int) -> str:
    clause = next((item for item in request.current_parse.clauses if item.clause_index == clause_index), None)
    return str(clause.source.source_kind) if clause and clause.source else ""


def _reuse_requested(text: str) -> bool:
    return any(marker in text for marker in ("无需重新", "不用重新", "不必重新", "基于已有", "基于刚才", "用刚才"))


def _complete(raw: dict[str, Any]) -> bool:
    return raw.get("status", "completed") == "completed" and raw.get("artifact_status") == "complete" and raw.get("persistence_status") == "committed" and raw.get("readback_verified") is True and (raw.get("lineage") or {}).get("lineage_status") == "complete"


def _window(raw: dict[str, Any]) -> dict[str, Any] | None:
    return raw.get("resolved_window") or raw.get("time_window") or raw.get("data_window") or None


def _assets(values: Any) -> list[str]:
    result = []
    for value in _texts(values):
        record = resolve_asset(value)
        result.append(record.display_name if record else value)
    return list(dict.fromkeys(result))


def _texts(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)] if str(value or "").strip() else []


def _values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [] if value is None else [value]


def _ids(candidates: list[ContextCandidate | None]) -> list[str]:
    return [item.candidate_id for item in candidates if item is not None]


def _expired(value: Any) -> bool:
    if not value:
        return False
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    except ValueError:
        return True
