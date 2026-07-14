from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVAL_DIR = Path(__file__).resolve().parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from fault_diagnosis import config
from fault_diagnosis.server.http.routers.auth import router as auth_router
from fault_diagnosis.server.http.routers.chat import router as chat_router
from fault_diagnosis.server.auth.session_scope import SessionScopeManager
from fault_diagnosis.agent.contracts import ArtifactEnvelope, ArtifactLineage, ArtifactManifest
from fault_diagnosis.domain.artifacts import (
    AnalysisArtifactPayload,
    KnowledgeArtifactPayload,
    ReportArtifactPayload,
    SqlArtifactPayload,
)
from fault_diagnosis.domain.diagnosis.analysis.contracts import DiagnosticAssessment, StructuredAnalysisArtifact
from fault_diagnosis.domain.diagnosis.contracts import (
    AnalysisStepArtifact,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
)
from fault_diagnosis.domain.diagnosis.runtime_status import DataBasis, RuntimeStatusAssessment
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import clear_all_artifacts, commit_artifact, save_thread_artifact
from fault_diagnosis.domain.diagnosis.contracts import DiagnosisArtifactEnvelope
from fault_diagnosis.server.devtools.dev_mode import init_dev_state
from evaluators import case_assertion_strength_failures, evaluate_plan_case, hard_gate_failures, summarize_results

CASE_FILE = ROOT / "tests" / "evals" / "agent_workflow_cases.yaml"
PHASE2_CASE_FILE = ROOT / "tests" / "evals" / "canonical_turn_phase2_cases.yaml"
FIXTURE_DIR = ROOT / "tests" / "evals" / "fixtures"
RESULTS_DIR = ROOT / "tests" / "evals" / "results"
PLAN_SUMMARY_FILE = RESULTS_DIR / "plan_eval_summary.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    return list(cases or [])


def build_test_client() -> TestClient:
    config.ENABLE_PLAN_ENDPOINT = True
    config.LOCAL_DEV_MODE = True
    config.DEV_AUTH_ENABLED = True
    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("agent-plan-eval-secret")
    init_dev_state(app)
    app.include_router(auth_router)
    app.include_router(chat_router)
    return TestClient(app)


def load_json_fixture(kind: str, name: str | None) -> Any:
    if not name:
        return None
    path = FIXTURE_DIR / kind / name
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def install_artifact_fixture(thread_id: str, fixture_name: str | None) -> None:
    payload = load_json_fixture("artifacts", fixture_name)
    if not payload:
        return
    items = payload if isinstance(payload, list) else [payload]
    for item in items:
        item = dict(item)
        item["thread_id"] = thread_id
        envelope = DiagnosisArtifactEnvelope.model_validate(item)
        legacy_payload = dict(envelope.payload)
        raw_manifests = legacy_payload.get("artifact_manifests", []) or []
        ids_by_type = {
            str(raw.get("artifact_type") or ""): str(raw.get("artifact_id") or "")
            for raw in raw_manifests
            if isinstance(raw, dict)
        }
        committed_manifests = []
        for raw in raw_manifests:
            manifest = ArtifactManifest.model_validate({**raw, "thread_id": thread_id})
            sources = []
            if manifest.artifact_type == "analysis_artifact":
                sources = [ids_by_type[key] for key in ("sql_artifact", "knowledge_artifact") if ids_by_type.get(key)]
            elif manifest.artifact_type == "report_artifact" and ids_by_type.get("analysis_artifact"):
                sources = [ids_by_type["analysis_artifact"]]
            lineage = ArtifactLineage(
                lineage_status="complete",
                artifact_id=manifest.artifact_id,
                artifact_type=manifest.artifact_type,
                subject_device_refs=list(manifest.device_refs),
                fault_code_refs=list(manifest.fault_code_refs),
                source_artifact_ids=sources,
                source_evidence_bundle_ids=[manifest.evidence_bundle_id] if manifest.evidence_bundle_id else [],
                source_tables=[manifest.source_table] if manifest.source_table else [],
                created_from_goal_ids=["eval_fixture_goal"],
            )
            manifest = manifest.model_copy(
                update={
                    "artifact_status": "complete",
                    "evidence_refs": list(manifest.evidence_refs or [f"eval:{manifest.artifact_id}"]),
                    "lineage": lineage,
                },
                deep=True,
            )
            canonical = commit_artifact(
                ArtifactEnvelope(
                    artifact_id=manifest.artifact_id,
                    artifact_type=manifest.artifact_type,
                    thread_id=thread_id,
                    payload=_fixture_payload(manifest, legacy_payload),
                    manifest=manifest,
                    lineage=lineage,
                )
            )
            committed_manifests.append(canonical.manifest.model_dump(mode="json"))
        legacy_payload["artifact_manifests"] = committed_manifests
        save_thread_artifact(envelope.model_copy(update={"payload": legacy_payload}, deep=True))


def _fixture_payload(manifest: ArtifactManifest, legacy_payload: dict[str, Any]):
    if manifest.artifact_type == "sql_artifact":
        sql = SqlStepArtifact.model_validate(legacy_payload["sql_artifact"])
        return SqlArtifactPayload(
            sql_artifact=sql,
            runtime_status_assessment=RuntimeStatusAssessment(
                device=(manifest.device_refs or ["J1"])[0],
                query_status="success" if sql.success else "failed",
                runtime_status="abnormal" if manifest.fault_code_refs else "unknown",
                data_basis=DataBasis(
                    resolution_mode="latest_available_fallback",
                    freshness="historical_latest" if manifest.freshness == "stale" else "recent",
                    usable_for_status=sql.success,
                    usable_for_diagnosis=sql.success,
                    usable_for_report=sql.success,
                    usable_for_workorder_draft=sql.success,
                ),
                sample_count=int(sql.row_count or 0),
                event_codes=list(manifest.fault_code_refs),
            ),
        )
    if manifest.artifact_type == "knowledge_artifact":
        return KnowledgeArtifactPayload(
            knowledge_artifact=KnowledgeStepArtifact.model_validate(legacy_payload["knowledge_artifact"])
        )
    if manifest.artifact_type == "analysis_artifact":
        analysis = AnalysisStepArtifact.model_validate(legacy_payload["analysis_artifact"])
        return AnalysisArtifactPayload(
            structured_analysis=StructuredAnalysisArtifact(
                assessment=DiagnosticAssessment(
                    success=analysis.success,
                    asset=(manifest.device_refs or ["J1"])[0],
                    source_table=manifest.source_table or "real_data_01",
                    conclusion=analysis.conclusion,
                    recommendations=list(analysis.recommendations),
                    confidence="medium" if analysis.confidence == "medium" else "low",
                ),
                analysis_artifact=analysis,
            )
        )
    if manifest.artifact_type == "report_artifact":
        raw_report = legacy_payload.get("report_artifact")
        if not isinstance(raw_report, dict):
            filename = manifest.report_filename or manifest.artifact_id
            raw_report = {
                "success": True,
                "report_filename": filename,
                "report_url": f"/reports/{filename}",
            }
        return ReportArtifactPayload(
            report_artifact=ReportStepArtifact.model_validate(raw_report)
        )
    raise ValueError(f"unsupported eval fixture artifact type: {manifest.artifact_type}")


def login_identity(client: TestClient, fixture_name: str | None, role: str | None) -> None:
    fixture = load_json_fixture("auth", fixture_name) or {}
    payload = {
        "role": fixture.get("role") or role or "guest",
        "user_id": fixture.get("user_id"),
        "asset_scope": fixture.get("asset_scope"),
        "allowed_tables": fixture.get("allowed_tables"),
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    response = client.post("/auth/dev-login", json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"dev login failed for {fixture_name or role}: {response.text}")


def last_turn(case: dict[str, Any]) -> str:
    turns = case.get("turns") or []
    if not turns:
        return ""
    last = turns[-1]
    if isinstance(last, dict):
        return str(last.get("message") or last.get("content") or "")
    return str(last)


def run_local_case(client: TestClient, case: dict[str, Any]) -> dict[str, Any]:
    clear_all_artifacts()
    thread_id = f"eval-plan-{case['id']}"
    install_artifact_fixture(thread_id, case.get("artifact_fixture"))
    login_identity(client, case.get("identity_fixture"), case.get("role"))
    response = client.get(
        "/chat/plan",
        params={
            "message": last_turn(case),
            "thread_id": thread_id,
            "user_identity": "管理员",
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"{case['id']} plan request failed: {response.status_code} {response.text}")
    return response.json()


def run_remote_case(base_url: str, case: dict[str, Any]) -> dict[str, Any]:
    response = httpx.get(
        f"{base_url.rstrip('/')}/chat/plan",
        params={"message": last_turn(case), "thread_id": f"eval-plan-{case['id']}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(CASE_FILE))
    parser.add_argument("--base-url", default=os.getenv("AGENT_EVAL_BASE_URL", ""))
    parser.add_argument("--tier", choices=["smoke", "core", "extended"], default="")
    args = parser.parse_args()

    case_path = Path(args.cases)
    if args.tier == "core" and case_path.resolve() == CASE_FILE.resolve():
        case_path = PHASE2_CASE_FILE
    cases = [
        case
        for case in load_cases(case_path)
        if "plan" in (case.get("eval_modes") or [])
        and (not args.tier or case.get("tier") == args.tier)
    ]
    client = None if args.base_url else build_test_client()
    results = []
    for case in cases:
        try:
            strength_failures = case_assertion_strength_failures(case)
            if strength_failures:
                result = evaluate_plan_case(case, {})
                result.passed = False
                result.failures.extend(strength_failures)
                results.append(result)
                status = "PASS" if result.passed else "FAIL"
                print(f"{status} {result.case_id} {case.get('name', '')}")
                for failure in result.failures:
                    print(f"  - {failure}")
                continue
            snapshot = run_remote_case(args.base_url, case) if args.base_url else run_local_case(client, case)  # type: ignore[arg-type]
            result = evaluate_plan_case(case, snapshot)
        except Exception as exc:  # noqa: BLE001
            result = evaluate_plan_case(case, {})
            result.passed = False
            result.failures.append(str(exc))
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.case_id} {case.get('name', '')}")
        for failure in result.failures:
            print(f"  - {failure}")

    summary = summarize_results(
        results,
        [
            "intent_accuracy",
            "context_binding_accuracy",
            "workflow_policy_accuracy",
            "tool_selection_precision",
            "evidence_gap_accuracy",
        ],
    )
    summary.setdefault("p95_latency_by_intent", {})
    gate_failures = hard_gate_failures(summary, mode="plan")
    if gate_failures:
        summary["hard_gate_failures"] = gate_failures
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] and not gate_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
