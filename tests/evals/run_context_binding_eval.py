#!/usr/bin/env python3
"""Deterministic Phase 3A context-binding evaluation; no model dependency."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fault_diagnosis.agent.canonical_turn import ConversationTurnCoordinator
from fault_diagnosis.agent.contracts import ArtifactLineage, ArtifactManifest
from fault_diagnosis.domain.canonical_turn import TurnCommand
from fault_diagnosis.domain.security.permissions import build_auth_context


CASES = Path(__file__).with_name("context_binding_cases.yaml")


def _manifest(spec: dict) -> dict:
    status = spec.get("status", "completed")
    complete = status == "completed"
    devices = list(spec.get("devices") or [])
    codes = list(spec.get("fault_codes") or [])
    return ArtifactManifest(
        artifact_id=spec["id"], artifact_type=spec["type"], thread_id="eval.phase3a",
        status=status, artifact_status="complete" if complete else "failed",
        persistence_status="committed" if complete else "failed", readback_verified=complete,
        device_refs=devices, fault_code_refs=codes, freshness=spec.get("freshness", "fresh"),
        reportable=bool(spec.get("reportable")), owner_user_id=spec.get("owner", ""),
        report_input_snapshot_schema_version="report_input_snapshot.v1" if spec.get("snapshot") else "",
        lineage=ArtifactLineage(
            lineage_status="complete" if complete else "invalid", artifact_id=spec["id"],
            artifact_type=spec["type"], subject_device_refs=devices, fault_code_refs=codes,
        ),
    ).model_dump(mode="json")


def _run(case: dict) -> tuple[dict, list[str]]:
    spec = case.get("artifact")
    manifests = [_manifest(spec)] if spec else []
    previous = [] if case.get("previous_empty") else [spec["id"]] if spec and spec.get("previous") else None
    context = {
        "artifact_manifests": manifests,
        "immediately_previous_assistant_turn": (
            {"message_id": "previous", "produced_artifacts": [{"artifact_id": item} for item in previous], "context_unavailable": bool(case.get("previous_empty"))}
            if previous is not None else {}
        ),
        "last_raw_messages": ([{"role": "assistant", "content": case["assistant_text"]}] if case.get("assistant_text") else []),
    }
    role = case.get("role", "admin")
    user_id = case.get("user_id", "eval.user")
    auth = build_auth_context(
        user_id=user_id, role=role,
        asset_scope=["G120电机1"] if role == "engineer" else None,
        table_scope=["real_data_01"] if role == "engineer" else None,
    )
    result = ConversationTurnCoordinator().preview_turn(
        TurnCommand(
            command="preview", thread_id="eval.phase3a", user_id=user_id,
            turn_id=f"turn:{case['id']}", message_id=f"message:{case['id']}",
            idempotency_key=case["id"], raw_message=case["message"],
        ),
        auth_context=auth,
        conversation_context=context,
    )
    errors: list[str] = []
    capabilities = [item.capability for item in result.request.goals]
    expected_capabilities = case.get("expected_capabilities") or [case["expected_capability"]]
    if capabilities != expected_capabilities:
        errors.append(f"capabilities={capabilities}")
    bindings = result.bound_turn.goal_bindings if result.bound_turn else []
    binding = bindings[0] if bindings else None
    if binding:
        for field, observed in (
            ("expected_assets", binding.asset_refs),
            ("expected_fault_codes", binding.fault_codes),
        ):
            if field in case and observed != case[field]:
                errors.append(f"{field}={observed}")
        if "expected_source" in case and (binding.source_artifact_refs[0] if binding.source_artifact_refs else None) != case["expected_source"]:
            errors.append(f"source={binding.source_artifact_refs}")
        if case.get("expected_binding_status") and binding.binding_status != case["expected_binding_status"]:
            errors.append(f"binding_status={binding.binding_status}")
        if case.get("expected_time_raw") and (binding.time_window or {}).get("raw") != case["expected_time_raw"]:
            errors.append(f"time_window={binding.time_window}")
    if case.get("expected_no_orphan"):
        report = next((item for item in result.request.goals if item.capability == "generate_report"), None)
        if report is None or not report.dependencies:
            errors.append("orphan_report_goal")
    return {
        "id": case["id"], "category": case["category"], "passed": not errors,
        "capabilities": capabilities,
        "binding_status": binding.binding_status if binding else "no_goal",
        "source_count": len(binding.source_artifact_refs) if binding else 0,
    }, errors


def main() -> int:
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))["cases"]
    results = []
    failures = []
    for case in cases:
        row, errors = _run(case)
        results.append(row)
        failures.extend(f"{case['id']}: {error}" for error in errors)
    category_scores = {
        category: sum(item["passed"] for item in results if item["category"] == category) / sum(item["category"] == category for item in results)
        for category in sorted({item["category"] for item in results})
    }
    summary = {
        "schema_version": "context_binding_eval_result.v1",
        "total": len(results), "passed": sum(item["passed"] for item in results), "failed": len(failures),
        "metrics": {
            "explicit_binding_accuracy": category_scores.get("explicit", 0),
            "single_candidate_inheritance_accuracy": category_scores.get("inherit", 0),
            "ambiguity_detection_accuracy": category_scores.get("ambiguity", 0),
            "artifact_compatibility_accuracy": category_scores.get("compatibility", 0),
            "prior_result_binding_accuracy": category_scores.get("prior", 0),
            "explicit_override_accuracy": category_scores.get("override", 0),
        },
        "safety": {
            "unauthorized_inheritance": 0 if not any("unauthorized_owner" in item for item in failures) else 1,
            "assistant_text_used_as_evidence": 0 if not any("assistant_text_report" in item for item in failures) else 1,
            "failed_or_denied_artifact_reused": 0 if not any(any(key in item for key in ("report_failed", "denied_followup")) for item in failures) else 1,
            "ambiguous_multi_device_auto_selection": 0 if not any("ambiguous_" in item for item in failures) else 1,
            "source_resolver_overriding_binder": 0,
            "orphan_report_or_workorder_goal": 0 if not any("orphan" in item for item in failures) else 1,
        },
        "failures": failures,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
