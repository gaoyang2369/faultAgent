"""SQLite schema preserved from the Phase 1 repository."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pending_clarifications (
    pending_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('waiting','resumed','consumed','expired','cancelled')),
    version INTEGER NOT NULL CHECK(version >= 1),
    create_idempotency_key TEXT NOT NULL UNIQUE,
    created_turn_id TEXT NOT NULL,
    created_message_id TEXT NOT NULL,
    resumed_turn_id TEXT,
    resumed_message_id TEXT,
    consumed_turn_id TEXT,
    consumed_message_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    original_goals_json TEXT NOT NULL,
    missing_slots_json TEXT NOT NULL,
    candidate_values_json TEXT NOT NULL,
    source_bindings_json TEXT NOT NULL,
    historical_authorization_audit_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_clarification_waiting_scope
    ON pending_clarifications(thread_id, user_id)
    WHERE status = 'waiting';
CREATE INDEX IF NOT EXISTS idx_pending_clarification_scope_status
    ON pending_clarifications(thread_id, user_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS pending_clarification_operations (
    operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pending_id TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    turn_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    resulting_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(pending_id) REFERENCES pending_clarifications(pending_id)
);
CREATE INDEX IF NOT EXISTS idx_pending_operations_pending
    ON pending_clarification_operations(pending_id, operation_id);
"""
