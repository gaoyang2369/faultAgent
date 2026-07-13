"""Compose context-aware EffectiveRequestFrame for Agent Engine V2."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fault_diagnosis.agent.contracts import ArtifactManifest, ContextFrame, EffectiveRequestFrame, IntentFrame
from .goals import build_goal_query_specs, build_target_scope, canonicalize_requested_goals, evaluate_goal_slots
from .source_selector import GoalScopedSourceSelector


DETAIL_WORDS = ("详细", "展开", "手册字段", "完整字段", "原文")
WORKORDER_WORDS = ("工单", "派单", "派人", "维修单")
WORKORDER_CONFIRM_WORDS = ("确认工单草稿", "确认该工单草稿", "确认这个工单草稿", "确认草稿")
REPORT_WORDS = ("报告", "导出")
STATUS_WORDS = ("当前", "现在", "最新", "还在", "还故障", "状态")


class ContextSemanticResolver:
    """Controlled semantic fallback.

    This default implementation is deterministic. A model client can be injected
    later; validated outputs must still flow through the same EffectiveRequest
    contract and never execute tools directly.
    """

    def __init__(self, model_client: Callable[[dict[str, Any]], dict[str, Any] | str] | None = None) -> None:
        self.model_client = model_client

    def resolve(self, *, raw_message: str, base: EffectiveRequestFrame, candidates: list[ArtifactManifest]) -> dict[str, Any]:
        model_result = self._resolve_with_model(raw_message=raw_message, base=base, candidates=candidates)
        if model_result:
            return model_result
        compact = (raw_message or "").replace(" ", "")
        if not compact:
            return {}
        if any(word in compact for word in DETAIL_WORDS) and (base.effective_fault_code_refs or _unique_fault_codes(candidates)):
            return {
                "semantic_intent": "expand_previous_answer",
                "requested_output_mode": "detailed",
                "confidence": 0.78,
                "rationale_short": "short_detail_followup",
            }
        if any(word in compact for word in WORKORDER_WORDS) and (base.effective_device_refs or _unique_device_refs(candidates)):
            return {
                "semantic_intent": "create_workorder_draft" if any(word in compact for word in ("创建", "生成")) else "decide_workorder",
                "requested_action": "create_workorder_draft" if any(word in compact for word in ("创建", "生成")) else "decide_workorder",
                "confidence": 0.78,
                "rationale_short": "workorder_followup",
            }
        if any(word in compact for word in REPORT_WORDS):
            return {
                "semantic_intent": "generate_report_from_previous" if base.target_artifact_id else "generate_report",
                "requested_output_mode": "report",
                "confidence": 0.72,
                "rationale_short": "report_followup",
            }
        if any(word in compact for word in STATUS_WORDS):
            return {
                "semantic_intent": "check_runtime_status",
                "confidence": 0.68,
                "rationale_short": "status_followup",
            }
        return {}

    def _resolve_with_model(
        self,
        *,
        raw_message: str,
        base: EffectiveRequestFrame,
        candidates: list[ArtifactManifest],
    ) -> dict[str, Any]:
        if self.model_client is None or not _should_call_model(raw_message, base, candidates):
            return {}
        payload = {
            "raw_message": raw_message,
            "active_focus": {
                "device_refs": list(base.effective_device_refs),
                "fault_code_refs": list(base.effective_fault_code_refs),
                "target_artifact_id": base.target_artifact_id,
                "target_artifact_type": base.target_artifact_type,
            },
            "candidate_targets": [
                {
                    "artifact_id": item.artifact_id,
                    "artifact_type": item.artifact_type,
                    "status": item.status,
                    "followupable": item.followupable,
                    "actionable": item.actionable,
                    "device_refs": list(item.device_refs),
                    "fault_code_refs": list(item.fault_code_refs),
                    "available_actions": list(item.available_actions),
                }
                for item in candidates[:8]
            ],
            "allowed_semantic_intents": [
                "explain_fault_code",
                "expand_previous_answer",
                "show_manual_fields",
                "check_runtime_status",
                "diagnose_from_runtime",
                "generate_report",
                "generate_report_from_previous",
                "decide_workorder",
                "create_workorder_draft",
                "confirm_workorder_draft",
                "clarify_target",
            ],
            "forbidden_outputs": ["sql", "tool_call", "dispatch_decision", "device_control_action", "unverified_claim"],
        }
        try:
            raw = self.model_client(payload)
            data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:
            return {}
        return _validated_semantic_result(data, candidates)


class EffectiveRequestBuilder:
    """Build the effective request from current parse and context package."""

    def __init__(self, semantic_resolver: ContextSemanticResolver | None = None) -> None:
        self.semantic_resolver = semantic_resolver or ContextSemanticResolver()

    def build(
        self,
        *,
        raw_message: str,
        thread_id: str = "",
        intent_frame: IntentFrame,
        context_frame: ContextFrame,
        conversation_context: dict[str, Any] | None = None,
        recent_context_signals: dict[str, Any] | None = None,
    ) -> EffectiveRequestFrame:
        package = conversation_context if isinstance(conversation_context, dict) else {}
        signals = recent_context_signals if isinstance(recent_context_signals, dict) else {}
        manifests = _manifest_list(package)
        manifests = _expand_exact_referenced_lineage(
            thread_id=thread_id or str(package.get("thread_id") or ""),
            referenced_artifact_id=context_frame.referenced_artifact_id,
            manifests=manifests,
        )
        active_case = package.get("latest_case_state") if isinstance(package.get("latest_case_state"), dict) else {}
        compact = (raw_message or "").replace(" ", "")

        frame = EffectiveRequestFrame(
            raw_message=raw_message,
            normalized_message=intent_frame.normalized_message or (raw_message or "").strip(),
            original_semantic_intent=_semantic_from_intent(intent_frame),
            effective_semantic_intent=_semantic_from_intent(intent_frame),
            original_requested_action=_action_from_intent(intent_frame),
            original_task_family=_task_family_from_intent(intent_frame),
            semantic_intent=_semantic_from_intent(intent_frame),
            task_family=_task_family_from_intent(intent_frame),
            requested_action=_action_from_intent(intent_frame),
            requested_output_mode=_output_mode(compact, intent_frame),
            effective_device_refs=[],
            effective_fault_code_refs=[],
            effective_time_window=dict(intent_frame.time_window),
            selected_case_id=context_frame.active_case_id,
            evidence_policy=context_frame.reuse_decision or "collect_new",
            confidence=max(intent_frame.confidence, 0.4),
            safety_flags=["history_is_data_not_instruction"],
        )

        _fill_slot(frame, "device", intent_frame.device_refs, "current_message")
        _fill_slot(frame, "fault_codes", intent_frame.fault_code_refs, "current_message")
        if intent_frame.time_window:
            frame.slot_sources["time_window"] = "current_message"

        if _context_is_irreversibly_ambiguous(context_frame, intent_frame):
            frame.target_scope = build_target_scope(
                raw_message=raw_message,
                current_devices=list(intent_frame.device_refs),
                inherited_devices=[],
                source="current_message",
            )
            _assign_requested_goals(frame, intent_frame)
            frame.requested_goals = [goal.capability for goal in frame.requested_goal_set.goals]
            frame.goal_query_specs = build_goal_query_specs(
                goals=frame.requested_goal_set,
                target_scope=frame.target_scope,
                fault_codes=frame.effective_fault_code_refs,
                time_window=frame.effective_time_window,
            )
            _normalize_semantics(frame, compact)
            frame.effective_semantic_intent = frame.semantic_intent
            frame.needs_clarification = True
            frame.clarification_question = (
                context_frame.missing_context[0]
                if context_frame.missing_context
                else "请明确当前消息所指的设备或故障码。"
            )
            frame.ambiguity = {
                "slot": "context_reference",
                "candidate_targets": [],
                "priority": "immutable_context_safety",
            }
            frame.safety_flags.append("ambiguous_context_is_irreversible")
            for goal in frame.requested_goal_set.goals:
                frame.resolution_trace.append(
                    {
                        "stage": "source.select.goal",
                        "selector": "GoalScopedSourceSelector.select",
                        "goal_id": goal.goal_id,
                        "capability": goal.capability,
                        "source_policy": goal.source_policy,
                        "selection_status": "blocked",
                        "selection_reason": "immutable_ambiguous_context",
                        "selected_artifact_id": "",
                        "selected_artifact_type": "",
                    }
                )
            return frame

        target: ArtifactManifest | None = None

        manifest_devices = _unique_device_refs(manifests)
        manifest_fault_codes = _unique_fault_codes(manifests)
        if len(manifest_devices) == 1:
            _fill_slot(frame, "device", manifest_devices, "verified_manifest_candidate")
        elif manifests and all(item.device_refs for item in manifests):
            _fill_slot(
                frame,
                "device",
                _dedupe([device for item in manifests for device in item.device_refs]),
                "verified_manifest_candidate",
            )
        if len(manifest_fault_codes) == 1:
            _fill_slot(frame, "fault_codes", manifest_fault_codes, "verified_manifest_candidate")

        inherited = context_frame.inherited_slots
        _fill_slot(frame, "device", _as_list(inherited.get("device") or inherited.get("asset")), "context_frame")
        _fill_slot(frame, "fault_codes", _as_list(inherited.get("fault_codes")), "context_frame")
        if inherited.get("time_window") and not frame.effective_time_window:
            frame.effective_time_window = dict(inherited.get("time_window") or {})
            frame.slot_sources["time_window"] = "context_frame"
        if inherited.get("evidence_bundle") and not frame.target_evidence_bundle_id:
            frame.target_evidence_bundle_id = str(inherited.get("evidence_bundle"))
        if inherited.get("report") and not frame.target_report_id:
            frame.target_report_id = str(inherited.get("report"))
        _fill_slot(frame, "device", _as_list(active_case.get("active_asset")), "case_state")
        _fill_slot(frame, "fault_codes", _as_list(active_case.get("active_fault_codes")), "case_state")
        if not frame.effective_time_window and isinstance(active_case.get("active_time_window"), dict):
            frame.effective_time_window = dict(active_case.get("active_time_window") or {})
            if frame.effective_time_window:
                frame.slot_sources["time_window"] = "case_state"
        if active_case.get("evidence_freshness") == "stale":
            frame.stale_evidence_disclosure_required = True
            frame.freshness = "stale"

        _fill_slot(frame, "device", _as_list(signals.get("current_message_assets")), "signals")
        _fill_slot(frame, "fault_codes", _as_list(signals.get("current_message_fault_codes")), "signals")
        if not frame.effective_device_refs:
            _fill_slot(frame, "device", _as_list(signals.get("mentioned_assets")), "signals")
        if not frame.effective_fault_code_refs:
            _fill_slot(frame, "fault_codes", _as_list(signals.get("mentioned_fault_codes")), "signals")

        fallback = self.semantic_resolver.resolve(raw_message=raw_message, base=frame, candidates=manifests)
        if fallback:
            frame.resolution_trace.append({"stage": "semantic.fallback", **fallback})
            if not frame.original_semantic_intent or frame.original_semantic_intent == "clarify_target":
                frame.semantic_intent = str(fallback.get("semantic_intent") or frame.semantic_intent)
                frame.requested_action = str(fallback.get("requested_action") or frame.requested_action)
            frame.requested_output_mode = str(fallback.get("requested_output_mode") or frame.requested_output_mode)
            _fill_slot(frame, "device", _as_list(fallback.get("effective_device_refs")), "semantic_fallback")
            _fill_slot(frame, "fault_codes", _as_list(fallback.get("effective_fault_code_refs")), "semantic_fallback")
            if fallback.get("needs_clarification"):
                frame.needs_clarification = True
                frame.clarification_question = str(fallback.get("clarification_question") or frame.clarification_question)
            frame.confidence = max(frame.confidence, _float(fallback.get("confidence"), 0.0))

        if (
            manifests
            and frame.evidence_policy == "collect_new"
            and not intent_frame.device_refs
            and not intent_frame.fault_code_refs
            and frame.semantic_intent
            in {
                "expand_previous_answer",
                "show_manual_fields",
                "diagnose_fault",
                "diagnose_from_runtime",
                "generate_report",
                "generate_report_from_previous",
                "create_workorder_draft",
                "confirm_workorder_draft",
            }
        ):
            frame.evidence_policy = "reuse_verified_artifact"
            frame.resolution_trace.append(
                {
                    "stage": "source.policy",
                    "source_policy": "reuse_verified_artifact",
                    "reason": "verified_conversation_manifest_candidate",
                }
            )

        inherited_devices = [item for item in frame.effective_device_refs if item not in intent_frame.device_refs]
        scope_source = "case_state" if active_case else "context_signal"
        frame.target_scope = build_target_scope(
            raw_message=raw_message,
            current_devices=list(intent_frame.device_refs),
            inherited_devices=inherited_devices,
            source=scope_source,
        )
        frame.effective_device_refs = list(frame.target_scope.resolved_devices)
        goal_intent = _intent_for_goal_canonicalization(intent_frame, frame.semantic_intent)
        _assign_requested_goals(frame, goal_intent)
        frame.requested_goals = [goal.capability for goal in frame.requested_goal_set.goals]
        selector = GoalScopedSourceSelector()
        selections = []
        for goal in frame.requested_goal_set.goals:
            selection = selector.select(
                goal=goal,
                manifests=manifests,
                explicit_artifact_id=context_frame.referenced_artifact_id,
                expected_devices=list(frame.target_scope.resolved_devices),
                thread_id=thread_id or str(package.get("thread_id") or ""),
            )
            selections.append(selection)
            frame.resolution_trace.append(selection.observation)
            if selection.binding is not None:
                frame.source_bindings.append(selection.binding)
        primary_selection = next(
            (
                item
                for goal, item in zip(frame.requested_goal_set.goals, selections, strict=False)
                if goal.goal_id == frame.requested_goal_set.primary_goal_id and item.manifest is not None
            ),
            None,
        )
        if primary_selection is None:
            primary_selection = next((item for item in selections if item.manifest is not None), None)
        if primary_selection is not None:
            target = primary_selection.manifest
            frame.target_artifact_id = target.artifact_id
            frame.target_artifact_type = target.artifact_type
            frame.target_evidence_bundle_id = target.evidence_bundle_id or target.linked_evidence_bundle_id or None
            frame.target_report_id = target.report_url or target.report_filename or None
            frame.available_actions = list(target.available_actions)
            frame.freshness = target.freshness or "unknown"
            frame.stale_evidence_disclosure_required = target.freshness == "stale"
            _fill_slot(frame, "device", target.device_refs, "artifact")
            _fill_slot(frame, "fault_codes", target.fault_code_refs, "artifact")
            if not frame.target_scope.resolved_devices and target.device_refs:
                frame.target_scope = frame.target_scope.model_copy(
                    update={
                        "resolved_devices": list(target.device_refs),
                        "source": "artifact",
                    }
                )
            if target.time_window and not frame.effective_time_window:
                frame.effective_time_window = dict(target.time_window)
                frame.slot_sources["time_window"] = "artifact"
        ambiguous_selection = next((item for item in selections if item.status == "ambiguous"), None)
        if ambiguous_selection is not None:
            frame.needs_clarification = True
            frame.clarification_question = "存在多个兼容的历史产物，请明确要继续使用哪一个。"
            frame.ambiguity = {"slot": "source_artifact", "candidate_targets": []}
        frame.goal_query_specs = build_goal_query_specs(
            goals=frame.requested_goal_set,
            target_scope=frame.target_scope,
            fault_codes=frame.effective_fault_code_refs,
            time_window=frame.effective_time_window,
        )
        _normalize_semantics(frame, compact)
        frame.effective_semantic_intent = frame.semantic_intent
        _validate_ambiguity(frame, target=target, manifests=manifests, compact=compact)
        lineage_status = target.lineage.lineage_status if target is not None else ""
        frame.clarification_reasons = evaluate_goal_slots(
            goals=frame.requested_goal_set,
            devices=frame.effective_device_refs,
            fault_codes=frame.effective_fault_code_refs,
            target_artifact_type=frame.target_artifact_type,
            target_lineage_status=lineage_status,
        )
        if frame.clarification_reasons:
            reason = frame.clarification_reasons[0]
            frame.needs_clarification = True
            frame.ambiguity = {
                "goal_id": reason["goal_id"],
                "slot": reason["missing_slot"],
                "valid_slots": reason["valid_slots"],
                "inheritance_failure": reason["inheritance_failure"],
            }
            frame.clarification_question = _clarification_question(str(reason["missing_slot"]))
        _finalize_contract(frame, context_frame=context_frame)
        return frame


def _validate_ambiguity(
    frame: EffectiveRequestFrame,
    *,
    target: ArtifactManifest | None,
    manifests: list[ArtifactManifest],
    compact: str,
) -> None:
    if frame.semantic_intent in {"check_runtime_status", "diagnose_fault", "diagnose_from_runtime", "health_assessment", "root_cause_analysis"} and not frame.effective_device_refs:
        frame.needs_clarification = True
        frame.clarification_question = "请确认要查询或诊断的设备。"
        frame.ambiguity = {"slot": "device", "candidate_count": 0, "priority": "current_request"}
        return
    if frame.effective_device_refs and frame.effective_fault_code_refs:
        return
    if target is not None:
        target_or_effective_devices = list(target.device_refs or frame.effective_device_refs)
        target_or_effective_faults = list(target.fault_code_refs or frame.effective_fault_code_refs)
        if _has_any(compact, WORKORDER_WORDS) and len(target_or_effective_devices) != 1:
            frame.needs_clarification = True
            frame.clarification_question = "请确认要为哪个设备创建或判断工单。"
            frame.ambiguity = {"slot": "device", "candidate_count": len(target_or_effective_devices), "priority": "target_artifact"}
        return
    if _has_any(compact, WORKORDER_WORDS) and len(_unique_device_refs(manifests)) > 1:
        frame.needs_clarification = True
        frame.clarification_question = "请确认要为哪个设备创建或判断工单。"
        frame.ambiguity = {"slot": "device", "candidate_count": len(_unique_device_refs(manifests)), "priority": "history"}


def _clarification_question(slot: str) -> str:
    if slot == "fault_code":
        return "请确认需要解释的故障码。"
    if slot == "at_least_two_devices":
        return "请至少确认两台需要比较的设备。"
    if slot == "exactly_one_device":
        return "该操作必须绑定一台设备，请明确要处理的设备。"
    if slot in {"reportable_source", "complete_analysis_or_report_lineage"}:
        return "缺少可安全继承的完整来源产物，请先重新查询或分析。"
    return "请确认要查询或诊断的设备。"


def _normalize_semantics(frame: EffectiveRequestFrame, compact: str) -> None:
    if frame.semantic_intent in {"", "clarify_target"}:
        if _has_any(compact, WORKORDER_WORDS):
            frame.semantic_intent = "create_workorder_draft" if _has_any(compact, ("创建", "生成")) else "decide_workorder"
        elif _has_any(compact, REPORT_WORDS):
            frame.semantic_intent = "generate_report_from_previous" if frame.target_artifact_id else "generate_report"
        elif _has_any(compact, DETAIL_WORDS):
            frame.semantic_intent = "expand_previous_answer"
        elif frame.effective_fault_code_refs:
            frame.semantic_intent = "explain_fault_code"
        elif frame.effective_device_refs:
            frame.semantic_intent = "check_runtime_status"
    if frame.semantic_intent == "expand_previous_answer" and frame.effective_fault_code_refs:
        frame.task_family = "knowledge"
    elif frame.semantic_intent in {"decide_workorder", "create_workorder_draft", "confirm_workorder_draft"}:
        frame.task_family = "action_or_workorder"
        frame.requested_action = frame.requested_action or frame.semantic_intent
    elif frame.semantic_intent.startswith("generate_report"):
        frame.task_family = "report"
    elif frame.semantic_intent in {"explain_fault_code", "show_manual_fields"}:
        frame.task_family = "knowledge"
    elif frame.semantic_intent in {"check_runtime_status", "diagnose_from_runtime", "diagnose_fault", "health_assessment", "root_cause_analysis"}:
        frame.task_family = "diagnosis"


def _finalize_contract(frame: EffectiveRequestFrame, *, context_frame: ContextFrame) -> None:
    if frame.semantic_intent == "generate_report_from_previous" and not frame.target_artifact_id:
        frame.semantic_intent = "generate_report"
        frame.resolution_trace.append(
            {
                "stage": "contract.normalize",
                "event": "explicit_report_without_target_uses_generate_report",
            }
        )


def _context_is_irreversibly_ambiguous(context_frame: ContextFrame, intent_frame: IntentFrame) -> bool:
    if intent_frame.device_refs or intent_frame.fault_code_refs:
        return False
    return context_frame.relation_to_previous in {"ambiguous", "unresolved"}


def _manifest_list(package: dict[str, Any]) -> list[ArtifactManifest]:
    raw: list[Any] = []
    for key in ("artifact_manifests", "latest_artifact_manifests"):
        if isinstance(package.get(key), list):
            raw.extend(package[key])
    previous = package.get("immediately_previous_assistant_turn")
    if isinstance(previous, dict) and isinstance(previous.get("produced_artifacts"), list):
        raw.extend(previous["produced_artifacts"])
    latest_case = package.get("latest_case_state") if isinstance(package.get("latest_case_state"), dict) else {}
    if isinstance(latest_case.get("artifact_manifests"), list):
        raw.extend(latest_case["artifact_manifests"])
    manifests: list[ArtifactManifest] = []
    for item in raw:
        if isinstance(item, ArtifactManifest):
            manifests.append(item)
        elif isinstance(item, dict):
            try:
                if "artifact_backend" in item and "artifact_type" in item and "status" not in item:
                    continue
                manifests.append(ArtifactManifest.model_validate(item))
            except Exception:
                continue
    return _dedupe_manifests(manifests)


def _expand_exact_referenced_lineage(
    *,
    thread_id: str,
    referenced_artifact_id: str | None,
    manifests: list[ArtifactManifest],
) -> list[ArtifactManifest]:
    if not thread_id or not referenced_artifact_id:
        return manifests
    if manifests and referenced_artifact_id not in {item.artifact_id for item in manifests}:
        return manifests
    from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import get_artifact_manifest_exact

    by_id = {item.artifact_id: item for item in manifests}
    pending = [referenced_artifact_id]
    while pending:
        artifact_id = str(pending.pop(0) or "")
        if not artifact_id or artifact_id in by_id:
            continue
        try:
            raw = get_artifact_manifest_exact(thread_id, artifact_id)
            manifest = ArtifactManifest.model_validate(raw) if isinstance(raw, dict) else None
        except Exception:
            manifest = None
        if manifest is None:
            continue
        by_id[manifest.artifact_id] = manifest
        pending.extend(manifest.lineage.source_artifact_ids)
    return list(by_id.values())


def _semantic_from_intent(intent: IntentFrame) -> str:
    mapping = {
        "explain_fault_code": "explain_fault_code",
        "check_current_status": "check_runtime_status",
        "generate_report": "generate_report",
        "decide_workorder": "decide_workorder",
        "create_workorder_draft": "create_workorder_draft",
        "confirm_workorder_draft": "confirm_workorder_draft",
        "dispatch_workorder": "dispatch_workorder",
        "diagnose_fault": "diagnose_fault",
        "health_assessment": "health_assessment",
        "root_cause_analysis": "root_cause_analysis",
    }
    return mapping.get(intent.primary_intent, intent.primary_intent or "")


def _assign_requested_goals(frame: EffectiveRequestFrame, intent: IntentFrame) -> None:
    frame.requested_goal_set = canonicalize_requested_goals(
        intent,
        target_scope=frame.target_scope,
        source_policy=frame.evidence_policy,
    )


def _intent_for_goal_canonicalization(intent: IntentFrame, semantic_intent: str) -> IntentFrame:
    if intent.sub_intents or intent.primary_intent:
        return intent
    inferred = {
        "expand_previous_answer": "explain_fault_code",
        "show_manual_fields": "explain_fault_code",
        "generate_report_from_previous": "generate_report",
        "diagnose_from_runtime": "diagnose_fault",
    }.get(semantic_intent, semantic_intent)
    if not inferred:
        return intent
    return intent.model_copy(update={"primary_intent": inferred, "sub_intents": [inferred]}, deep=True)


def _task_family_from_intent(intent: IntentFrame) -> str:
    if intent.primary_intent == "explain_fault_code":
        return "knowledge"
    if intent.primary_intent == "generate_report":
        return "report"
    if intent.primary_intent in {"decide_workorder", "create_workorder_draft", "confirm_workorder_draft", "dispatch_workorder"}:
        return "action_or_workorder"
    if intent.primary_intent:
        return "diagnosis"
    return ""


def _action_from_intent(intent: IntentFrame) -> str:
    if intent.primary_intent in {"decide_workorder", "create_workorder_draft", "confirm_workorder_draft", "dispatch_workorder"}:
        return intent.primary_intent
    return ""


def _output_mode(compact: str, intent: IntentFrame) -> str:
    if _has_any(compact, DETAIL_WORDS):
        return "detailed"
    if "report" in intent.requested_outputs:
        return "report"
    return "concise"


def _fill_slot(frame: EffectiveRequestFrame, slot: str, values: list[Any], source: str) -> None:
    clean = _dedupe(values)
    if not clean:
        return
    if slot == "device" and not frame.effective_device_refs:
        frame.effective_device_refs = clean
        frame.slot_sources["device_refs"] = source
    if slot == "fault_codes" and not frame.effective_fault_code_refs:
        frame.effective_fault_code_refs = [item.upper() for item in clean]
        frame.slot_sources["fault_code_refs"] = source


def _unique_device_refs(manifests: list[ArtifactManifest]) -> list[str]:
    return _dedupe([value for item in manifests for value in item.device_refs])


def _unique_fault_codes(manifests: list[ArtifactManifest]) -> list[str]:
    return _dedupe([value for item in manifests for value in item.fault_code_refs])


def _dedupe_manifests(manifests: list[ArtifactManifest]) -> list[ArtifactManifest]:
    seen: set[str] = set()
    result: list[ArtifactManifest] = []
    for item in manifests:
        key = item.artifact_id or f"{item.artifact_type}:{len(result)}"
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _should_call_model(raw_message: str, base: EffectiveRequestFrame, candidates: list[ArtifactManifest]) -> bool:
    compact = (raw_message or "").replace(" ", "")
    return bool(
        candidates
        and (
            base.confidence < 0.5
            or len(compact) <= 12
            or base.needs_clarification
            or (not base.effective_device_refs and not base.effective_fault_code_refs)
            or _has_any(compact, ("详细", "展开", "那就", "看起来", "这个", "它", "刚才", "创建工单", "导出报告"))
        )
    )


def _validated_semantic_result(data: dict[str, Any], candidates: list[ArtifactManifest]) -> dict[str, Any]:
    allowed = {
        "explain_fault_code",
        "expand_previous_answer",
        "show_manual_fields",
        "check_runtime_status",
        "diagnose_from_runtime",
        "generate_report",
        "generate_report_from_previous",
        "decide_workorder",
        "create_workorder_draft",
        "confirm_workorder_draft",
        "clarify_target",
    }
    semantic = str(data.get("semantic_intent") or "").strip()
    if semantic not in allowed:
        return {}
    target_id = str(data.get("target_artifact_id") or "").strip()
    if target_id:
        by_id = {item.artifact_id: item for item in candidates}
        target = by_id.get(target_id)
        if target is None or target.status != "completed":
            return {}
        if semantic in {"create_workorder_draft", "confirm_workorder_draft", "decide_workorder"} and not (target.actionable or target.available_actions):
            return {}
        if semantic in {"expand_previous_answer", "show_manual_fields"} and not target.followupable:
            return {}
    result = {
        "semantic_intent": semantic,
        "target_artifact_id": target_id or None,
        "target_artifact_type": data.get("target_artifact_type"),
        "effective_device_refs": _as_list(data.get("effective_device_refs")),
        "effective_fault_code_refs": _as_list(data.get("effective_fault_code_refs")),
        "requested_action": str(data.get("requested_action") or ""),
        "requested_output_mode": str(data.get("requested_output_mode") or ""),
        "evidence_policy": str(data.get("evidence_policy") or ""),
        "needs_clarification": bool(data.get("needs_clarification", False)),
        "clarification_question": str(data.get("clarification_question") or ""),
        "confidence": max(0.0, min(1.0, _float(data.get("confidence"), 0.0))),
        "rationale_short": str(data.get("rationale_short") or "model_semantic_resolver"),
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words if word)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _dedupe(value)
    if str(value or "").strip():
        return [str(value).strip()]
    return []


def _dedupe(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item or "").strip()))


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
