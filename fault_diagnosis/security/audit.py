"""Append-only structured security audit records."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from ..common.paths import RUN_STATE_DIR
from .contracts import AuthContext, AuthorizationDecision

_LOCK = RLock()


class SecurityAuditLogger:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path or os.getenv("SECURITY_AUDIT_PATH") or Path(RUN_STATE_DIR) / "security-audit.jsonl")

    def record(
        self,
        *,
        event_type: str,
        auth: AuthContext,
        decision: AuthorizationDecision,
        trace_id: str = "",
        resource: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "trace_id": trace_id,
            "auth": auth.audit_summary(),
            "decision": decision.model_dump(),
            "resource": resource or {},
        }
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


_DEFAULT_AUDIT_LOGGER = SecurityAuditLogger()


def get_security_audit_logger() -> SecurityAuditLogger:
    return _DEFAULT_AUDIT_LOGGER
