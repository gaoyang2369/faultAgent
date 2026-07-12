"""Build canonical span-based traces from V2 plan/runtime payloads."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import FaultCodeEntry, KnowledgeStepArtifact
from fault_diagnosis.platform.settings import AGENT_TRACE_CAPTURE_CONTENT, AGENT_TRACE_PREVIEW_CHARS

from .payloads import sanitize_trace_value
from .trace_schema import TraceEnvelope, TraceEvent, TraceSpan, utc_now_iso

_FINAL_NODE_STATUSES = {"completed", "skipped", "blocked", "failed", "cancelled"}
_RAW_TEXT_KEYS = {"raw_output", "snippets", "result_preview", "sql_used", "final_answer", "prompt", "query"}


class TraceRecorder:
    """Request-scoped helper that emits a canonical trace envelope."""

    def __init__(
        self,
        *,
        trace_id: str,
        request_id: str,
        thread_id: str,
        stream_id: str = "",
        endpoint: str = "/chat/stream",
        user_message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.trace_id = trace_id
        self.request_id = request_id
        self.thread_id = thread_id
        self.stream_id = stream_id
        self.endpoint = endpoint
        self.user_message = user_message
        self.started_at = utc_now_iso()
        self._started_monotonic = time.monotonic()
        self.metadata = dict(metadata or {})
        self.spans: list[TraceSpan] = []
        self.events: list[TraceEvent] = []
        self.errors: list[dict[str, Any]] = []
        self.evidence_refs: list[str] = []
        self.artifact_refs: list[dict[str, Any]] = []
        self._span_ids: set[str] = set()
        self._add_span(
            span_id="span.chat.request",
            parent_span_id="",
            name="chat.request",
            kind="request",
            status="running",
            attributes={
                "endpoint": endpoint,
                "message": _safe_text(user_message),
                "stream_id": stream_id,
            },
        )

    def add_plan_snapshot(self, snapshot: Any) -> None:
        intent = snapshot.intent_frame
        rewrite = snapshot.rewrite_frame
        context = snapshot.context_frame
        effective = getattr(snapshot, "effective_request_frame", None)
        route = snapshot.skill_route
        plan = snapshot.execution_plan
        trace = snapshot.trace if isinstance(snapshot.trace, dict) else {}
        validation = trace.get("validation") if isinstance(trace.get("validation"), dict) else {}
        candidate_plan = trace.get("candidate_plan") if isinstance(trace.get("candidate_plan"), dict) else plan.model_dump(mode="json")
        skipped_nodes = _skipped_nodes(plan)

        self._add_span(
            span_id="span.plan.understand",
            parent_span_id="span.chat.request",
            name="request.understand",
            kind="planner",
            attributes={
                "raw_message_hash": _hash_text(intent.raw_message),
                "raw_message_preview": _preview_if_enabled(intent.raw_message),
                "user_rewrite": rewrite.user_rewrite,
                "rewrite_used": bool(rewrite.user_rewrite and rewrite.user_rewrite != intent.raw_message),
                "rewrite_reason": rewrite.rewrite_reason,
                "fallback_used": bool(intent.model_trace.get("fallback_used")),
                "detected_fault_codes": list(intent.fault_code_refs),
            },
        )
        self._add_span(
            span_id="span.plan.context",
            parent_span_id="span.chat.request",
            name="context.resolve",
            kind="planner",
            attributes={
                "relation_to_previous": context.relation_to_previous,
                "reuse_decision": context.reuse_decision,
                "missing_context": list(context.missing_context),
                "reuse_blockers": list(context.reuse_blockers),
                "inherited_device": context.inherited_slots.get("device") or context.inherited_slots.get("asset"),
                "inherited_fault_codes": context.inherited_slots.get("fault_codes") or [],
                "selected_artifact_id": context.referenced_artifact_id,
                "effective_device_refs": list(getattr(effective, "effective_device_refs", []) or []),
                "effective_fault_code_refs": list(getattr(effective, "effective_fault_code_refs", []) or []),
                "effective_semantic_intent": getattr(effective, "semantic_intent", ""),
                "original_semantic_intent": getattr(effective, "original_semantic_intent", ""),
                "authorized_semantic_intent": getattr(effective, "authorized_semantic_intent", ""),
                "executed_semantic_intent": getattr(effective, "executed_semantic_intent", ""),
                "effective_target_artifact_id": getattr(effective, "target_artifact_id", None),
                "effective_target_artifact_type": getattr(effective, "target_artifact_type", None),
                "effective_slot_sources": dict(getattr(effective, "slot_sources", {}) or {}),
                "semantic_fallback_used": any(
                    item.get("stage") == "semantic.fallback"
                    for item in (getattr(effective, "resolution_trace", []) or [])
                    if isinstance(item, dict)
                ),
                "candidate_artifact_count": _candidate_artifact_count(context.inherited_slots),
                "deictic_refs": context.permission_context.get("deictic_refs", []),
                "recent_corrections": context.permission_context.get("recent_corrections", []),
            },
        )
        capability_auth = dict(getattr(effective, "authorization_decision", {}) or {})
        self._add_span(
            span_id="span.plan.capability_preflight",
            parent_span_id="span.chat.request",
            name="capability.preflight",
            kind="guardrail",
            status="blocked" if capability_auth.get("mode") == "deny" else "completed",
            attributes={
                "requested_capability": getattr(effective, "original_semantic_intent", ""),
                "effective_capability": getattr(effective, "effective_semantic_intent", ""),
                "authorization": capability_auth,
                "runtime_invoked": False if capability_auth.get("mode") == "deny" else None,
            },
        )
        self._add_span(
            span_id="span.plan.goal",
            parent_span_id="span.chat.request",
            name="goal.build",
            kind="planner",
            attributes={
                "task_family": _task_family(route.primary_skill, intent.primary_intent),
                "goals": [_dump_goal(goal) for goal in plan.goals],
                "primary_goal": plan.goals[0].goal_type if plan.goals else "",
                "fault_codes": list(intent.fault_code_refs),
                "devices": list(intent.device_refs),
                "effective_fault_codes": list(getattr(effective, "effective_fault_code_refs", []) or []),
                "effective_devices": list(getattr(effective, "effective_device_refs", []) or []),
                "confidence": intent.confidence,
                "missing_slots": list(intent.ambiguities),
            },
        )
        self._add_span(
            span_id="span.plan.skill",
            parent_span_id="span.chat.request",
            name="skill.route",
            kind="planner",
            attributes={
                "primary_skill": route.primary_skill,
                "selected_skills": list(route.selected_skills),
                "load_set": list(route.load_set),
                "blocked_skills": dict(route.blocked_skills),
                "route_reasons": route.routing_reason,
            },
        )
        self._add_span(
            span_id="span.plan.compile",
            parent_span_id="span.chat.request",
            name="plan.compile",
            kind="planner",
            attributes={
                "plan_id": plan.plan_id,
                "candidate_nodes": _node_summaries(candidate_plan.get("nodes", [])),
                "candidate_edges": candidate_plan.get("edges", []),
                "node_order": [node.node_id for node in plan.nodes],
                "dedupe_summary": {"planned_node_count": len(plan.nodes), "unique_node_count": len({node.node_id for node in plan.nodes})},
            },
        )
        self._add_span(
            span_id="span.plan.validate",
            parent_span_id="span.chat.request",
            name="plan.validate",
            kind="planner",
            status="blocked" if snapshot.status == "blocked" else "completed",
            attributes={
                "status": validation.get("status") or snapshot.status,
                "issues": validation.get("issues", []),
                "enabled_nodes": [node.node_id for node in plan.nodes if node.node_id not in {item["node_id"] for item in skipped_nodes}],
                "skipped_nodes": skipped_nodes,
                "removed_tools": validation.get("removed_tools", []),
                "approval_requirements": list(plan.approval_requirements),
                "authorization": validation.get("authorization", {}),
                "forbidden_tools": list(plan.forbidden_tools),
                "validation_warnings": list(snapshot.warnings),
            },
        )
        self._merge_metadata(
            planned_nodes=[node.node_id for node in plan.nodes],
            enabled_nodes=[node.node_id for node in plan.nodes if node.node_id not in {item["node_id"] for item in skipped_nodes}],
            skipped_nodes=skipped_nodes,
        )

    def add_runtime_result(self, *, plan: Any, result: Any) -> None:
        runtime_span = self._add_span(
            span_id="span.workflow.execute",
            parent_span_id="span.chat.request",
            name="workflow.execute",
            kind="runtime",
            status=result.status,
            attributes={
                "plan_id": plan.plan_id,
                "node_count": len(plan.nodes),
                "result_status": result.status,
            },
        )
        by_node = {item.node_id: item for item in result.node_results}
        event_by_node = _events_by_node(result.trace.get("events", []) if isinstance(result.trace, dict) else [])
        executed_nodes: list[str] = []
        skipped_nodes: list[dict[str, Any]] = []

        for node in plan.nodes:
            node_id = node.node_id
            node_type = node.node_type
            result_item = by_node.get(node_id)
            node_events = event_by_node.get(node_id, [])
            final_event = _final_event(node_events)
            status = str(getattr(result_item, "status", "") or final_event.get("status") or "skipped")
            duration_ms = float(getattr(result_item, "duration_ms", 0.0) or final_event.get("duration_ms") or 0.0)
            span_start, span_end, duration_ms = _node_span_timing(node_events, fallback_duration_ms=duration_ms)
            output = getattr(result_item, "output", {}) if result_item is not None else {}
            error = getattr(result_item, "error", None) if result_item is not None else final_event.get("error")
            attributes = {
                "node_id": node_id,
                "node_type": node_type,
                "skill": node.skill,
                "goal_id": node.goal_id,
                "required_tools": list(node.required_tools),
                "input": _safe_mapping(node.inputs),
                "evidence_refs": list(getattr(result_item, "evidence_refs", []) if result_item else []),
                "tool_call_refs": list(getattr(result_item, "tool_call_refs", []) if result_item else []),
            }
            if status == "skipped":
                attributes["reason"] = _skip_reason(output, final_event)
                skipped_nodes.append({"node_id": node_id, "reason": attributes["reason"]})
            else:
                executed_nodes.append(node_id)
            if node_type == "sql" and isinstance(output, dict):
                artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
                basis = artifact.get("data_basis") if isinstance(artifact.get("data_basis"), dict) else {}
                attributes.update(
                    {
                        "requested_window": basis.get("requested_window"),
                        "resolved_window": basis.get("resolved_window"),
                        "resolution_mode": basis.get("resolution_mode"),
                        "fallback_used": basis.get("fallback_used"),
                        "fallback_reason": basis.get("fallback_reason"),
                        "latest_sample_time": basis.get("latest_sample_time"),
                        "data_environment": basis.get("data_environment"),
                    }
                )
            node_span = self._add_span(
                span_id=f"span.node.{node_id}",
                parent_span_id=runtime_span.span_id,
                name=f"node.{node_type}",
                kind="node",
                status=status,
                duration_ms=duration_ms,
                start_time=span_start,
                end_time=span_end,
                attributes=attributes,
                events=_lifecycle_events(node_events),
                error=error,
                retry_count=int(getattr(result_item, "retry_count", 0) if result_item else final_event.get("retry_count") or 0),
            )
            self._add_node_children(node_span=node_span, node_type=node_type, result_item=result_item, runtime_result=result)

        self._add_evidence_span(result, runtime_span.span_id)
        self._add_output_spans(result, runtime_span.span_id)
        self._merge_metadata(
            executed_nodes=executed_nodes,
            skipped_nodes=skipped_nodes or self.metadata.get("skipped_nodes", []),
            node_order=list(result.trace.get("node_order", [])) if isinstance(result.trace, dict) else [],
        )
        self.errors.extend(list(result.trace.get("errors", [])) if isinstance(result.trace, dict) else [])
        self.evidence_refs = _evidence_refs(result)
        self.artifact_refs = _artifact_refs(result)

    def add_error(self, *, error: Exception | str, status: str = "failed", attributes: dict[str, Any] | None = None) -> None:
        payload = {"code": type(error).__name__, "message": str(error)} if isinstance(error, Exception) else {"message": str(error)}
        self.errors.append(payload)
        self._add_span(
            span_id=f"span.error.{len(self.errors)}",
            parent_span_id="span.chat.request",
            name="request.error",
            kind="guardrail",
            status=status,
            attributes=attributes or {},
            error=payload,
        )

    def finish(self, *, status: str = "completed") -> TraceEnvelope:
        ended_at = utc_now_iso()
        duration_ms = round((time.monotonic() - self._started_monotonic) * 1000, 1)
        for span in self.spans:
            if span.span_id == "span.chat.request":
                span.status = status  # type: ignore[assignment]
                span.end_time = ended_at
                span.duration_ms = duration_ms
                break
        envelope = TraceEnvelope(
            trace_id=self.trace_id,
            request_id=self.request_id,
            thread_id=self.thread_id,
            stream_id=self.stream_id,
            endpoint=self.endpoint,
            status=status,  # type: ignore[arg-type]
            started_at=self.started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            metadata=_sanitize(self.metadata),
            spans=self.spans,
            events=self.events,
            errors=_sanitize(self.errors),
            evidence_refs=list(dict.fromkeys(self.evidence_refs)),
            artifact_refs=_sanitize(_dedupe_artifact_refs(self.artifact_refs)),
        )
        return envelope

    def _add_node_children(self, *, node_span: TraceSpan, node_type: str, result_item: Any, runtime_result: Any) -> None:
        output = getattr(result_item, "output", {}) if result_item is not None else {}
        if node_type == "rag":
            artifact = _knowledge_artifact_from_output(output)
            self._add_kb_search_span(node_span.span_id, artifact, _tool_metric(output, "kb.search"))
            self._add_fault_code_parse_spans(node_span.span_id, artifact)
        if node_type == "kg" and isinstance(output, dict) and output.get("skipped_reason"):
            node_span.attributes["reason"] = output.get("skipped_reason")

    def _add_kb_search_span(self, parent_span_id: str, artifact: KnowledgeStepArtifact | None, metric: dict[str, Any]) -> None:
        if artifact is None:
            return
        selected = [_source_from_entry(entry) for entry in artifact.fault_code_entries[:5]]
        if not selected:
            selected = _sources_from_snippets(artifact.snippets)
        match_type = _best_match_type(artifact)
        duration_ms = _float_or_zero(metric.get("duration_ms") or metric.get("latency_ms"))
        self._add_span(
            span_id=f"{parent_span_id}.tool.kb.search",
            parent_span_id=parent_span_id,
            name="tool.kb.search",
            kind="tool",
            status="completed" if artifact.success else "failed",
            duration_ms=duration_ms,
            start_time=str(metric.get("started_at") or "") or None,
            end_time=str(metric.get("ended_at") or "") or None,
            attributes={
                "query": _safe_text(artifact.query),
                "normalized_query": " ".join(str(artifact.query or "").split()).upper(),
                "retrieval_mode": "fault_code_exact_match" if match_type == "exact_match" else "semantic_search",
                "kb_name": "default",
                "top_k": len(artifact.snippets),
                "hit_count": artifact.hit_count if artifact.hit_count is not None else len(artifact.snippets),
                "match_type": match_type,
                "selected_sources": selected,
                "latency_ms": _float_or_none(metric.get("latency_ms") or duration_ms),
                "phase_latencies_ms": metric.get("phase_latencies_ms") or ({"total": duration_ms} if duration_ms else {}),
            },
            error={"message": artifact.error} if artifact.error else None,
        )

    def _add_fault_code_parse_spans(self, parent_span_id: str, artifact: KnowledgeStepArtifact | None) -> None:
        if artifact is None:
            return
        for index, entry in enumerate(artifact.fault_code_entries or [], start=1):
            missing = [
                field
                for field in ("meaning", "category", "cause", "remedy", "references")
                if not getattr(entry, field)
            ]
            self._add_span(
                span_id=f"{parent_span_id}.parser.fault_code.{entry.code or index}",
                parent_span_id=parent_span_id,
                name="fault_code.entry_parse",
                kind="parser",
                attributes={
                    "code": entry.code,
                    "has_meaning": bool(entry.meaning or entry.title),
                    "has_category": bool(entry.category),
                    "has_cause": bool(entry.cause),
                    "has_remedy": bool(entry.remedy),
                    "has_references": bool(entry.references),
                    "references": list(entry.references),
                    "source_file": entry.source_file,
                    "source_page": entry.page,
                    "parse_status": "completed" if entry.code else "failed",
                    "missing_fields": missing,
                },
            )

    def _add_evidence_span(self, result: Any, parent_span_id: str) -> None:
        ledger = result.evidence_ledger
        evidence_ids = [str(item.get("evidence_id") or "") for item in ledger.evidence_items if item.get("evidence_id")]
        claim_types = [str(item.get("claim_type") or "") for item in ledger.claims if item.get("claim_type")]
        quality = dict(ledger.quality_checks)
        self._add_span(
            span_id="span.evidence.ledger",
            parent_span_id=parent_span_id,
            name="evidence.ledger",
            kind="evidence",
            attributes={
                "evidence_count": len(ledger.evidence_items),
                "evidence_ids": evidence_ids,
                "claim_count": len(ledger.claims),
                "claim_types": claim_types,
                "final_claim_ids": list(ledger.final_claim_ids),
                "all_final_claims_have_evidence": _all_final_claims_have_evidence(ledger),
                "unauthorized_evidence_count": quality.get("unauthorized_evidence_count", 0),
                "stale_evidence_count": quality.get("stale_evidence_count", 0),
                "passed": bool(quality.get("passed", not quality.get("missing_evidence") and not quality.get("final_claims_without_evidence"))),
                "warnings": quality.get("warnings", []),
            },
        )

    def _add_output_spans(self, result: Any, parent_span_id: str) -> None:
        frame = result.output_frame
        artifact_payload = frame.artifact_payload or {}
        knowledge = _coerce_knowledge(artifact_payload.get("knowledge_artifact"))
        source_count = len(knowledge.fault_code_entries) if knowledge else 0
        self._add_span(
            span_id="span.answer.render",
            parent_span_id=parent_span_id,
            name="answer.render",
            kind="output",
            attributes={
                "answer_template": _answer_template(frame.answer_variant, knowledge),
                "output_mode": _output_mode(frame.answer_variant),
                "llm_used": False,
                "source_count": source_count,
                "citation_count": source_count,
                "answer_length": len(frame.final_answer or ""),
                "blocked_by_guardrail": result.status in {"blocked", "failed"},
            },
        )
        guardrail = dict(frame.guardrail_result or {})
        self._add_span(
            span_id="span.guardrail.check",
            parent_span_id=parent_span_id,
            name="guardrail.check",
            kind="guardrail",
            status="blocked" if result.status == "blocked" else "completed",
            attributes={
                "evidence_satisfied": (
                    guardrail.get("contract_satisfied")
                    if "contract_satisfied" in guardrail
                    else not guardrail.get("missing_evidence") and not guardrail.get("final_claims_without_evidence")
                ),
                "blocked": result.status == "blocked",
                "status": guardrail.get("status") or result.status,
                "missing_evidence": guardrail.get("missing_evidence", []),
                "stale_evidence": guardrail.get("stale_evidence", []),
                "required_claim_types": guardrail.get("required_claim_types", []),
                "present_claim_types": guardrail.get("present_claim_types", []),
                "missing_claim_types": guardrail.get("missing_claim_types", []),
                "ledger_passed": guardrail.get("ledger_passed"),
                "contract_satisfied": guardrail.get("contract_satisfied"),
            },
        )

    def _add_legacy_runtime_spans(self, trace_payload: dict[str, Any]) -> None:
        runtime_span = self._add_span(
            span_id="span.workflow.execute",
            parent_span_id="span.chat.request",
            name="workflow.execute",
            kind="runtime",
            status=str(trace_payload.get("status") or "completed"),
            attributes={
                "plan_id": trace_payload.get("plan_id", ""),
                "legacy_runtime_trace": True,
            },
        )
        grouped = _events_by_node(trace_payload.get("events", []) if isinstance(trace_payload.get("events"), list) else [])
        for node_id, events in grouped.items():
            final_event = _final_event(events)
            node_type = str(final_event.get("node_type") or "")
            status = str(final_event.get("status") or "completed")
            duration_ms = float(final_event.get("duration_ms") or 0.0)
            span_start, span_end, duration_ms = _node_span_timing(events, fallback_duration_ms=duration_ms)
            self._add_span(
                span_id=f"span.node.{node_id}",
                parent_span_id=runtime_span.span_id,
                name=f"node.{node_type or 'unknown'}",
                kind="node",
                status=status,
                duration_ms=duration_ms,
                start_time=span_start,
                end_time=span_end,
                attributes={
                    "node_id": node_id,
                    "node_type": node_type,
                    "input": _safe_text(str(final_event.get("input_summary") or "")),
                    "output": _safe_text(str(final_event.get("output_summary") or "")),
                },
                events=_lifecycle_events(events),
                error=final_event.get("error"),
                retry_count=int(final_event.get("retry_count") or 0),
            )

    def _add_span(
        self,
        *,
        span_id: str,
        parent_span_id: str,
        name: str,
        kind: str,
        status: str = "completed",
        duration_ms: float = 0.0,
        attributes: dict[str, Any] | None = None,
        events: list[TraceEvent] | None = None,
        error: dict[str, Any] | None = None,
        retry_count: int = 0,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> TraceSpan:
        unique_span_id = _unique_span_id(span_id, self._span_ids)
        self._span_ids.add(unique_span_id)
        now = utc_now_iso()
        resolved_start = start_time or now
        resolved_end = end_time or start_time or now
        resolved_duration = _duration_between_ms(resolved_start, resolved_end) if (start_time or end_time) else None
        if resolved_duration is not None and (resolved_duration > 0 or duration_ms <= 0):
            duration_ms = resolved_duration
        span = TraceSpan(
            trace_id=self.trace_id,
            span_id=unique_span_id,
            parent_span_id=parent_span_id,
            name=name,
            kind=kind,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            start_time=resolved_start,
            end_time=resolved_end,
            duration_ms=duration_ms,
            attributes=_sanitize(attributes or {}),
            events=events or [],
            error=_sanitize(error) if error else None,
            retry_count=retry_count,
        )
        self.spans.append(span)
        return span

    def _merge_metadata(self, **items: Any) -> None:
        for key, value in items.items():
            self.metadata[key] = value


def canonical_trace_from_payload(
    *,
    trace_payload: dict[str, Any],
    trace_context: Any | None = None,
    metadata: dict[str, Any] | None = None,
    output: Any | None = None,
    error: str | None = None,
) -> TraceEnvelope:
    """Compatibility adapter for old callers that only have runtime trace payloads."""

    trace_id = str(trace_payload.get("trace_id") or getattr(trace_context, "trace_id", "") or (metadata or {}).get("trace_id") or "")
    recorder = TraceRecorder(
        trace_id=trace_id,
        request_id=str(trace_payload.get("request_id") or getattr(trace_context, "request_id", "") or (metadata or {}).get("request_id") or ""),
        thread_id=str(trace_payload.get("thread_id") or getattr(trace_context, "thread_id", "") or (metadata or {}).get("thread_id") or ""),
        stream_id=str(getattr(trace_context, "stream_id", "") or (metadata or {}).get("stream_id") or ""),
        user_message=str(getattr(trace_context, "user_message", "") or ""),
        metadata=metadata,
    )
    status = str(trace_payload.get("status") or (metadata or {}).get("status") or ("failed" if error else "completed"))
    recorder._add_legacy_runtime_spans(trace_payload)
    if error:
        recorder.add_error(error=error, status="failed")
    return recorder.finish(status=status)


def _sanitize(value: Any) -> Any:
    return sanitize_trace_value(
        value,
        capture_content=True,
        preview_chars=AGENT_TRACE_PREVIEW_CHARS,
    )


def _safe_text(value: str) -> dict[str, Any]:
    text = str(value or "")
    payload = {
        "chars": len(text),
        "sha256_12": _hash_text(text),
    }
    if AGENT_TRACE_CAPTURE_CONTENT and text:
        payload["preview"] = text[:AGENT_TRACE_PREVIEW_CHARS]
    return payload


def _safe_mapping(value: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, item in dict(value or {}).items():
        if str(key) in _RAW_TEXT_KEYS:
            safe[str(key)] = _safe_text(str(item or ""))
        else:
            safe[str(key)] = item
    return safe


def _preview_if_enabled(value: str) -> str:
    return str(value or "")[:AGENT_TRACE_PREVIEW_CHARS] if AGENT_TRACE_CAPTURE_CONTENT else ""


def _hash_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def _unique_span_id(span_id: str, existing: set[str]) -> str:
    if span_id not in existing:
        return span_id
    index = 2
    while f"{span_id}.{index}" in existing:
        index += 1
    return f"{span_id}.{index}"


def _node_span_timing(events: list[dict[str, Any]], *, fallback_duration_ms: float) -> tuple[str | None, str | None, float]:
    running = _first_node_status_event(events, "running") or _first_node_status_event(events, "pending")
    final = _first_final_node_status_event(events)
    start_time = str((running or {}).get("timestamp") or "") or None
    end_time = str((final or {}).get("timestamp") or "") or None
    duration = _duration_between_ms(start_time, end_time)
    if duration is not None and (duration > 0 or fallback_duration_ms <= 0):
        return start_time, end_time, duration
    event_duration = _float_or_none((final or {}).get("duration_ms"))
    if event_duration is not None:
        return start_time, end_time, event_duration
    return start_time, end_time, fallback_duration_ms


def _first_node_status_event(events: list[dict[str, Any]], status: str) -> dict[str, Any] | None:
    return next((event for event in events if event.get("event_type") == "node_status" and event.get("status") == status), None)


def _first_final_node_status_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if event.get("event_type") == "node_status" and event.get("status") in _FINAL_NODE_STATUSES:
            return event
    return None


def _duration_between_ms(start_time: str | None, end_time: str | None) -> float | None:
    start = _parse_iso_datetime(start_time)
    end = _parse_iso_datetime(end_time)
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() * 1000, 1)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _tool_metric(output: Any, tool_name: str) -> dict[str, Any]:
    if not isinstance(output, dict):
        return {}
    metrics = output.get("tool_metrics")
    if not isinstance(metrics, dict):
        return {}
    metric = metrics.get(tool_name)
    return dict(metric) if isinstance(metric, dict) else {}


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_zero(value: Any) -> float:
    return _float_or_none(value) or 0.0


def _dump_goal(goal: Any) -> dict[str, Any]:
    data = goal.model_dump(mode="json") if hasattr(goal, "model_dump") else dict(goal or {})
    return {
        "goal_id": data.get("goal_id"),
        "goal_type": data.get("goal_type") or data.get("goal"),
        "skill": data.get("skill"),
        "description": data.get("description"),
        "fault_code_refs": data.get("fault_code_refs", []),
        "device_refs": data.get("device_refs", []),
    }


def _node_summaries(nodes: Any) -> list[dict[str, Any]]:
    items = []
    for node in nodes or []:
        data = node.model_dump(mode="json") if hasattr(node, "model_dump") else dict(node or {})
        items.append({"node_id": data.get("node_id"), "node_type": data.get("node_type"), "required_tools": data.get("required_tools", [])})
    return items


def _task_family(primary_skill: str, primary_intent: str) -> str:
    mapping = {
        "fault_code_explain": "knowledge_lookup",
        "runtime_status": "status_query",
        "alarm_triage": "alarm_triage",
        "root_cause": "root_cause_analysis",
        "report_generation": "report_generation",
        "workorder_decision": "action_or_workorder",
        "clarification": "clarification",
    }
    return mapping.get(primary_skill) or primary_intent or "unknown"


def _candidate_artifact_count(slots: dict[str, Any]) -> int:
    return sum(1 for key in ("evidence_bundle", "report", "sql_artifact_id", "analysis_artifact_id") if slots.get(key))


def _skipped_nodes(plan: ExecutionPlan) -> list[dict[str, Any]]:
    skipped = []
    for node in plan.nodes:
        if node.node_type == "kg":
            skipped.append({"node_id": node.node_id, "reason": "not_configured"})
    return skipped


def _events_by_node(events: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        node_id = str(event.get("node_id") or "")
        if node_id:
            grouped.setdefault(node_id, []).append(event)
    return grouped


def _final_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("event_type") == "node_status" and event.get("status") in _FINAL_NODE_STATUSES:
            return event
    return events[-1] if events else {}


def _lifecycle_events(events: list[dict[str, Any]]) -> list[TraceEvent]:
    lifecycle = []
    for event in events:
        if event.get("event_type") != "node_status":
            continue
        lifecycle.append(
            TraceEvent(
                trace_id=str(event.get("trace_id") or ""),
                span_id=str(event.get("node_id") or ""),
                name=f"node.{event.get('status') or 'status'}",
                timestamp=str(event.get("timestamp") or utc_now_iso()),
                attributes=_sanitize({key: value for key, value in event.items() if key not in {"input_summary", "output_summary"}}),
            )
        )
    return lifecycle


def _skip_reason(output: Any, event: dict[str, Any]) -> str:
    if isinstance(output, dict):
        return str(output.get("skipped_reason") or output.get("reason") or "")
    if isinstance(event.get("output_summary"), str) and "not_configured" in event["output_summary"]:
        return "not_configured"
    return "skipped"


def _knowledge_artifact_from_output(output: Any) -> KnowledgeStepArtifact | None:
    if not isinstance(output, dict):
        return None
    return _coerce_knowledge(output.get("artifact") or output.get("knowledge_artifact"))


def _coerce_knowledge(value: Any) -> KnowledgeStepArtifact | None:
    if isinstance(value, KnowledgeStepArtifact):
        return value
    if isinstance(value, dict):
        try:
            return KnowledgeStepArtifact.model_validate(value)
        except Exception:
            return None
    return None


def _source_from_entry(entry: FaultCodeEntry) -> dict[str, Any]:
    seed = f"{entry.source_file}:{entry.page}:{entry.code}:{entry.title}"
    return {
        "file": entry.source_file,
        "page": entry.page,
        "chunk_id": f"{entry.code}:{entry.page}",
        "chunk_hash": _hash_text(seed),
        "score": None,
        "match_type": entry.match_type,
    }


def _sources_from_snippets(snippets: list[str]) -> list[dict[str, Any]]:
    sources = []
    for index, snippet in enumerate(snippets[:5], start=1):
        sources.append({"file": "", "page": "", "chunk_id": f"snippet_{index}", "chunk_hash": _hash_text(snippet), "score": None, "match_type": "candidate_match"})
    return sources


def _best_match_type(artifact: KnowledgeStepArtifact) -> str:
    if any(entry.match_type == "exact_match" for entry in artifact.fault_code_entries):
        return "exact_match"
    if artifact.fault_code_entries:
        return "candidate_match"
    return "no_match" if not artifact.success else "candidate_match"


def _evidence_refs(result: Any) -> list[str]:
    return [str(item.get("evidence_id") or "") for item in result.evidence_ledger.evidence_items if item.get("evidence_id")]


def _artifact_refs(result: Any) -> list[dict[str, Any]]:
    refs = []
    for key, value in (result.output_frame.artifact_payload or {}).items():
        if value is not None:
            refs.append({"artifact_type": key, "available": True})
    refs.extend(list(result.evidence_ledger.artifact_refs or []))
    return _dedupe_artifact_refs(refs)


def _dedupe_artifact_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        artifact_type = str(ref.get("artifact_type") or ref.get("type") or "")
        artifact_id = str(ref.get("artifact_id") or ref.get("id") or "")
        key = (artifact_type, artifact_id or artifact_type or str(ref))
        if key in seen:
            continue
        seen.add(key)
        output.append(ref)
    return output


def _all_final_claims_have_evidence(ledger: Any) -> bool:
    by_id = {str(claim.get("claim_id") or ""): claim for claim in ledger.claims}
    for claim_id in ledger.final_claim_ids:
        claim = by_id.get(str(claim_id))
        if not claim or not claim.get("supporting_evidence_ids"):
            return False
    return True


def _answer_template(answer_variant: str, knowledge: KnowledgeStepArtifact | None) -> str:
    if answer_variant == "knowledge_answer" and knowledge and knowledge.fault_code_entries:
        return "fault_code_concise_v1"
    return f"{answer_variant or 'answer'}_v1"


def _output_mode(answer_variant: str) -> str:
    if answer_variant == "knowledge_answer":
        return "concise"
    if answer_variant == "report_ready":
        return "detailed"
    return "concise"
