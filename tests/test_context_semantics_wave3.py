"""Wave 3：模型只能提出 ACL-safe 的上下文筛选约束。"""

from __future__ import annotations

import json

import pytest

from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator, CurrentUtteranceParser
from fault_diagnosis.agent.canonical_turn.context_binding import project_authorized_context_candidates
from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest
from fault_diagnosis.agent.semantics import SemanticResolutionService
from fault_diagnosis.agent.semantics.context_interpreter import project_context_semantic_input
from fault_diagnosis.agent.semantics.model_gateway import ModelGatewayResult
from fault_diagnosis.domain.canonical_turn import TurnCommand
from fault_diagnosis.domain.security.permissions import build_auth_context


def _manifest(artifact_id: str, device: str, *, freshness: str = "fresh", owner: str = "") -> dict:
    return ArtifactManifest(
        artifact_id=artifact_id,
        artifact_type="analysis_artifact",
        thread_id="wave3-thread",
        status="completed",
        artifact_status="complete",
        persistence_status="committed",
        readback_verified=True,
        device_refs=[device],
        freshness=freshness,
        owner_user_id=owner,
        lineage=ArtifactLineage(
            lineage_status="complete", artifact_id=artifact_id, artifact_type="analysis_artifact",
            subject_device_refs=[device], source_artifact_ids=["lineage-secret"],
        ),
    ).model_dump(mode="json")


def _context(*items: dict) -> dict:
    return {"artifact_manifests": list(items), "immediately_previous_assistant_turn": {}}


def _command(message: str = "生成报告") -> TurnCommand:
    return TurnCommand(
        command="preview", thread_id="wave3-thread", user_id="wave3-user", turn_id="wave3-turn",
        message_id="wave3-message", idempotency_key="wave3-key", raw_message=message,
    )


def _payload(message: str, context: dict) -> dict:
    return {
        "schema_version": "semantic_turn_proposal.v1", "entities": [], "ambiguities": [],
        "clauses": [{
            "clause_index": 0, "text": message, "start": 0, "end": len(message),
            "capability": "generate_report", "confidence": 0.95,
        }],
        "context": context,
    }


class _Gateway:
    model_name = "wave3-test-model"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests = []

    async def invoke_clause_model(self, request, *, cancel_event=None):  # noqa: ANN001
        self.requests.append(request)
        return ModelGatewayResult(self.payload, 1.0, 3, 2, False)


async def _preview(message: str, context: dict, proposal_context: dict, *, auth=None):
    parser = CurrentUtteranceParser(enable_fallback=False)
    gateway = _Gateway(_payload(message, proposal_context))
    service = SemanticResolutionService(parser=parser, gateway_factory=lambda _semaphore: gateway, mode="primary")
    result = await ConversationTurnCoordinator(parser=parser, semantic_service=service).preview_turn_async(
        _command(message),
        auth_context=auth or build_auth_context(user_id="wave3-user", role="admin"),
        conversation_context=context,
    )
    return result, gateway


def test_context_model_projection_never_contains_artifact_or_lineage_identifiers() -> None:
    context = _context(_manifest("artifact-secret-42", "G120电机1"))
    candidates = project_authorized_context_candidates(context, build_auth_context(user_id="wave3-user", role="admin"))
    serialized = json.dumps(project_context_semantic_input(candidates), ensure_ascii=False)

    assert "artifact-secret-42" not in serialized
    assert "lineage-secret" not in serialized
    assert "candidate_id" not in serialized and "artifact_ref" not in serialized


@pytest.mark.asyncio
async def test_earliest_constraint_selects_internal_candidate_without_exposing_id() -> None:
    context = _context(_manifest("analysis.first", "G120电机1"), _manifest("analysis.second", "G120电机2"))
    result, gateway = await _preview("生成报告", context, {
        "reference_target": "prior_diagnosis_result", "temporal_relation": "earliest",
        "requested_reuse": True, "freshness_intent": "historical_ok", "confidence": 0.91,
    })

    assert result.bound_turn.goal_bindings[0].source_artifact_refs == ["analysis.first"]
    assert "analysis.first" not in json.dumps(gateway.requests[0].context_candidates, ensure_ascii=False)
    semantic = next(item.detail["semantic"] for item in result.events if item.event_type == "intent_resolution")
    assert semantic["context_constraints"]["temporal_relation"] == "earliest"


@pytest.mark.asyncio
async def test_latest_constraint_selects_last_visible_candidate() -> None:
    context = _context(_manifest("analysis.first", "G120电机1"), _manifest("analysis.latest", "G120电机2"))
    result, _ = await _preview("生成报告", context, {
        "reference_target": "prior_diagnosis_result", "temporal_relation": "latest",
        "requested_reuse": True, "freshness_intent": "historical_ok", "confidence": 0.91,
    })

    assert result.bound_turn.goal_bindings[0].source_artifact_refs == ["analysis.latest"]


@pytest.mark.asyncio
async def test_include_asset_constraint_binds_only_authorized_matching_candidate() -> None:
    context = _context(_manifest("analysis.j1", "G120电机1"), _manifest("analysis.j2", "G120电机2"))
    result, _ = await _preview("生成报告", context, {
        "reference_target": "prior_diagnosis_result", "include_asset_refs": ["二号机"],
        "requested_reuse": True, "freshness_intent": "historical_ok", "confidence": 0.91,
    })

    assert result.bound_turn.goal_bindings[0].source_artifact_refs == ["analysis.j2"]


@pytest.mark.asyncio
async def test_unavailable_asset_constraint_clarifies_and_cannot_fall_back_to_visible_artifact() -> None:
    context = _context(_manifest("analysis.j1", "G120电机1"), _manifest("analysis.j2-secret", "G120电机2", owner="admin.other"))
    auth = build_auth_context(
        user_id="wave3-user", role="engineer", asset_scope=["G120电机1"], table_scope=["real_data_01"],
    )
    result, gateway = await _preview("生成报告", context, {
        "reference_target": "prior_diagnosis_result", "include_asset_refs": ["G120电机2"],
        "requested_reuse": True, "freshness_intent": "historical_ok", "confidence": 0.91,
    }, auth=auth)

    binding = result.bound_turn.goal_bindings[0]
    assert binding.source_artifact_refs == []
    assert binding.clarification.reason_code == "unknown_or_unavailable_context_asset"
    assert "G120电机2" not in json.dumps(gateway.requests[0].context_candidates, ensure_ascii=False)


@pytest.mark.asyncio
async def test_ordinal_out_of_range_clarifies_without_selecting_artifact() -> None:
    context = _context(_manifest("analysis.only", "G120电机1"))
    result, _ = await _preview("生成报告", context, {
        "reference_target": "prior_diagnosis_result", "temporal_relation": "ordinal", "ordinal": 3,
        "requested_reuse": True, "freshness_intent": "historical_ok", "confidence": 0.91,
    })

    binding = result.bound_turn.goal_bindings[0]
    assert binding.source_artifact_refs == []
    assert binding.clarification.reason_code == "context_ordinal_out_of_range"


@pytest.mark.asyncio
async def test_ordinal_constraint_selects_the_requested_visible_candidate() -> None:
    context = _context(_manifest("analysis.first", "G120电机1"), _manifest("analysis.second", "G120电机2"))
    result, _ = await _preview("生成报告", context, {
        "reference_target": "prior_diagnosis_result", "temporal_relation": "ordinal", "ordinal": 2,
        "requested_reuse": True, "freshness_intent": "historical_ok", "confidence": 0.91,
    })

    assert result.bound_turn.goal_bindings[0].source_artifact_refs == ["analysis.second"]


@pytest.mark.asyncio
async def test_comparison_role_requires_a_verified_device_constraint() -> None:
    context = _context(_manifest("analysis.j1", "G120电机1"))
    result, _ = await _preview("生成报告", context, {
        "reference_target": "prior_comparison", "relation": "worse_device_from_previous_comparison",
        "requested_reuse": True, "freshness_intent": "historical_ok", "confidence": 0.91,
    })

    binding = result.bound_turn.goal_bindings[0]
    assert binding.source_artifact_refs == []
    assert binding.clarification.reason_code == "comparison_role_requires_asset_constraint"


@pytest.mark.asyncio
async def test_current_freshness_intent_never_reuses_stale_artifact() -> None:
    context = _context(_manifest("analysis.stale", "G120电机1", freshness="stale"))
    result, _ = await _preview("生成报告", context, {
        "reference_target": "prior_diagnosis_result", "requested_reuse": True,
        "freshness_intent": "current_required", "confidence": 0.91,
    })

    binding = result.bound_turn.goal_bindings[0]
    assert binding.source_artifact_refs == ["analysis.stale"]
    assert binding.binding_status == "refresh_required"
