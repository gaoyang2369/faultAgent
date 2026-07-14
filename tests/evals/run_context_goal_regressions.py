from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator
from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest
from fault_diagnosis.domain.canonical_turn import CanonicalGoal, TurnCommand
from fault_diagnosis.domain.canonical_turn.contracts import GoalProvenance
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform.persistence.repositories.pending_clarification_repository import (
    MemoryPendingClarificationRepository,
)


NOW = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)


def command(message: str, key: str) -> TurnCommand:
    return TurnCommand(
        command="preview",
        thread_id="eval-context-phase2",
        user_id="eval-user",
        turn_id=f"turn-{key}",
        message_id=f"message-{key}",
        idempotency_key=key,
        raw_message=message,
    )


def auth(role: str):
    return build_auth_context(user_id="eval-user", role=role)


def pending_coordinator() -> ConversationTurnCoordinator:
    repository = MemoryPendingClarificationRepository(clock=lambda: NOW)
    goal = CanonicalGoal(
        goal_id="goal-pending-diagnosis",
        capability="diagnose_fault",
        origin="explicit",
        user_requested=True,
        user_visible=True,
        clause_index=0,
        required_slots=["device"],
        resolved_slots={},
        missing_slots=["device"],
        provenance=GoalProvenance(parser_source="deterministic", utterance_span=(0, 4)),
    )
    repository.create_waiting(
        pending_id="pending-context-phase2",
        thread_id="eval-context-phase2",
        user_id="eval-user",
        created_turn_id="turn-original",
        created_message_id="message-original",
        idempotency_key="pending-create",
        original_goals=[goal],
        missing_slots=["device"],
        candidate_values={"device": ["G120电机1", "G120电机2"]},
        now=NOW,
    )
    return ConversationTurnCoordinator(pending_repository=repository, clock=lambda: NOW)


def analysis_context() -> dict:
    manifest = ArtifactManifest(
        artifact_id="analysis:context-eval",
        artifact_type="analysis_artifact",
        thread_id="eval-context-phase2",
        status="completed",
        artifact_status="complete",
        persistence_status="committed",
        readback_verified=True,
        device_refs=["G120电机1"],
        report_input_snapshot_schema_version="report_input_snapshot.v1",
        lineage=ArtifactLineage(
            lineage_status="complete",
            artifact_id="analysis:context-eval",
            artifact_type="analysis_artifact",
            subject_device_refs=["G120电机1"],
        ),
    )
    return {"artifact_manifests": [manifest.model_dump(mode="json")]}


def main() -> int:
    failures: list[str] = []
    results: list[dict] = []

    composite = ConversationTurnCoordinator(clock=lambda: NOW).preview_turn(
        command("A07089 是什么？查询 G120电机1 当前状态，判断是否存在故障，并给出处理建议", "composite"),
        auth_context=auth("admin"),
    )
    capabilities = [goal.capability for goal in composite.request.goals]
    expected = ["explain_fault_code", "check_runtime_status", "diagnose_fault", "resolution_recommendation"]
    if capabilities != expected:
        failures.append(f"clause_order: expected {expected}, got {capabilities}")
    results.append({"case": "clause_order", "capabilities": capabilities})

    for message, expected_kind, expected_capabilities in (
        ("是电机2", "slot_only", ["diagnose_fault"]),
        ("是电机2，顺便生成报告", "mixed", ["diagnose_fault", "generate_report"]),
    ):
        pending = pending_coordinator().preview_turn(command(message, expected_kind), auth_context=auth("admin"))
        observed = [goal.capability for goal in pending.request.goals]
        if pending.request.pending_binding.kind != expected_kind or observed != expected_capabilities:
            failures.append(f"{expected_kind}: binding={pending.request.pending_binding.kind}, goals={observed}")
        results.append({"case": expected_kind, "capabilities": observed})

    report = ConversationTurnCoordinator(clock=lambda: NOW).preview_turn(
        command("根据诊断结果生成报告", "source"),
        auth_context=auth("admin"),
        conversation_context=analysis_context(),
    )
    if report.source_resolutions[0].status != "source_for_execution":
        failures.append(f"source_resolution: got {report.source_resolutions[0].status}")
    results.append({"case": "source_resolution", "status": report.source_resolutions[0].status})

    partial = ConversationTurnCoordinator(clock=lambda: NOW).preview_turn(
        command("A07089 是什么？并生成报告", "authorization"),
        auth_context=auth("guest"),
    )
    statuses = [item.status for item in partial.authorization]
    if statuses != ["authorized", "denied"]:
        failures.append(f"authorization: got {statuses}")
    results.append({"case": "authorization", "statuses": statuses})

    summary = {
        "schema_version": "canonical_turn_context_goal_regression.v1",
        "total": len(results),
        "passed": len(results) - len(failures),
        "failed": len(failures),
        "failures": failures,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
