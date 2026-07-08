"""Work-order draft and pending-action helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from fault_diagnosis.domain.context.contracts import PendingAction
from fault_diagnosis.platform.persistence.diagnosis_artifacts.store import list_thread_artifacts
from ..contracts import DiagnosisArtifactEnvelope, WorkOrderDraftArtifact, WorkOrderSuggestion
from fault_diagnosis.domain.security.assets import asset_is_in_scope
from fault_diagnosis.domain.security.contracts import AuthContext


def build_workorder_source_hash(
    *,
    thread_id: str,
    action_type: str,
    source_diagnosis_artifact_id: str | None,
    source_report_artifact_id: str | None,
    device: str | None,
    fault_code: str | None,
) -> str:
    payload = {
        "thread_id": thread_id,
        "action_type": action_type,
        "source_diagnosis_artifact_id": source_diagnosis_artifact_id or "",
        "source_report_artifact_id": source_report_artifact_id or "",
        "device": device or "",
        "fault_code": fault_code or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_pending_workorder_draft_action(
    *,
    thread_id: str,
    suggestion: WorkOrderSuggestion,
    source_diagnosis_artifact_id: str | None,
    source_report_artifact_id: str | None,
    recommendation_artifact_id: str | None,
    stale_refresh_required: bool = False,
) -> PendingAction:
    source_hash = build_workorder_source_hash(
        thread_id=thread_id,
        action_type="workorder_draft",
        source_diagnosis_artifact_id=source_diagnosis_artifact_id,
        source_report_artifact_id=source_report_artifact_id,
        device=suggestion.equipment_object,
        fault_code=suggestion.fault_code,
    )
    return PendingAction(
        action_type="workorder_draft",
        status="pending",
        artifact_id=recommendation_artifact_id,
        reason=suggestion.reason,
        required_evidence=["diagnosis_summary", "severity_or_status_level", "recommended_action_policy"],
        source_diagnosis_artifact_id=source_diagnosis_artifact_id,
        recommendation_artifact_id=recommendation_artifact_id,
        source_report_artifact_id=source_report_artifact_id,
        required_role="engineer",
        stale_refresh_required=stale_refresh_required,
        expires_at=(datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds"),
        source_hash=source_hash,
    )


def find_pending_workorder_draft_action(resolved_context: dict[str, Any]) -> dict[str, Any] | None:
    for item in resolved_context.get("pending_actions") or []:
        if not isinstance(item, dict):
            continue
        if item.get("action_type") == "workorder_draft" and item.get("status", "pending") == "pending":
            return item
    return None


def validate_pending_workorder_draft_action(
    *,
    pending_action: dict[str, Any] | None,
    auth_context: AuthContext,
    device: str | None,
) -> tuple[bool, str]:
    if not pending_action:
        return False, "当前线程没有待确认的工单草稿动作，请先判断是否建议生成工单。"
    required_role = str(pending_action.get("required_role") or "engineer")
    if required_role == "engineer" and auth_context.role not in {"engineer", "admin"}:
        return False, "当前身份无权创建工单草稿。"
    if required_role == "admin" and not auth_context.is_admin():
        return False, "当前身份无权执行管理员级工单动作。"
    if device and auth_context.role == "engineer" and not asset_is_in_scope(device, auth_context.asset_scope):
        return False, "当前账号只能为授权范围内设备创建工单草稿。"
    expires_at = str(pending_action.get("expires_at") or "").strip()
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) < datetime.now():
                return False, "待确认工单动作已过期，请重新评估是否需要生成工单。"
        except ValueError:
            pass
    return True, ""


def find_existing_workorder_draft(thread_id: str, source_hash: str | None) -> WorkOrderDraftArtifact | None:
    if not source_hash:
        return None
    for envelope in list_thread_artifacts(thread_id, limit=20):
        payload = envelope.payload or {}
        raw = payload.get("workorder_draft")
        if isinstance(raw, dict) and raw.get("source_hash") == source_hash:
            try:
                return WorkOrderDraftArtifact.model_validate(raw)
            except Exception:
                continue
    return None


def build_workorder_draft_artifact(
    *,
    thread_id: str,
    suggestion: WorkOrderSuggestion,
    pending_action: dict[str, Any],
    source_diagnosis_artifact_id: str,
    source_report_artifact_id: str | None,
    stale: bool,
) -> WorkOrderDraftArtifact:
    source_hash = str(pending_action.get("source_hash") or "") or build_workorder_source_hash(
        thread_id=thread_id,
        action_type="workorder_draft",
        source_diagnosis_artifact_id=source_diagnosis_artifact_id,
        source_report_artifact_id=source_report_artifact_id,
        device=suggestion.equipment_object,
        fault_code=suggestion.fault_code,
    )
    draft_id = f"WOD-{source_hash[:10].upper()}"
    stale_warning = "上一轮数据已滞后，正式提交或派发前必须刷新当前状态并经人工审批。" if stale else None
    return WorkOrderDraftArtifact(
        draft_id=draft_id,
        source_diagnosis_artifact_id=source_diagnosis_artifact_id,
        source_report_artifact_id=source_report_artifact_id,
        device=suggestion.equipment_object or "DCMA 系统",
        fault_code=suggestion.fault_code,
        priority=suggestion.priority or "P2",
        workorder_type=suggestion.workorder_type or "运行异常确认工单",
        recommended_assignee_role=suggestion.assignee_role or "电气维护人员",
        acceptance_criteria=suggestion.acceptance_criteria or ["异常状态已复核", "派发前已刷新当前状态"],
        stale_warning=stale_warning,
        status="pending_verification" if stale else "draft",
        source_hash=source_hash,
        title=suggestion.title or f"{suggestion.equipment_object or 'DCMA 系统'} 待确认工单草稿",
        created_from_recommendation_artifact_id=str(pending_action.get("recommendation_artifact_id") or "") or None,
    )


def source_ids_from_envelope(envelope: DiagnosisArtifactEnvelope) -> tuple[str, str | None]:
    payload = envelope.payload or {}
    draft = payload.get("workorder_draft") if isinstance(payload.get("workorder_draft"), dict) else {}
    if draft.get("source_diagnosis_artifact_id"):
        return str(draft.get("source_diagnosis_artifact_id")), (
            str(draft.get("source_report_artifact_id") or "").strip() or None
        )
    workorder = payload.get("workorder_decision") if isinstance(payload.get("workorder_decision"), dict) else {}
    if workorder.get("source_diagnosis_artifact_id"):
        return str(workorder.get("source_diagnosis_artifact_id")), (
            str(workorder.get("source_report_artifact_id") or "").strip() or None
        )
    evidence_bundle = payload.get("evidence_bundle") if isinstance(payload.get("evidence_bundle"), dict) else {}
    report_artifact = payload.get("report_artifact") if isinstance(payload.get("report_artifact"), dict) else {}
    source_diagnosis_artifact_id = (
        str(evidence_bundle.get("bundle_id") or "").strip()
        or str((payload.get("trace") or {}).get("trace_id") if isinstance(payload.get("trace"), dict) else "").strip()
        or envelope.created_at
    )
    source_report_artifact_id = (
        str(report_artifact.get("report_url") or "").strip()
        or str(report_artifact.get("report_filename") or "").strip()
        or envelope.report_filename
    )
    return source_diagnosis_artifact_id, source_report_artifact_id or None
