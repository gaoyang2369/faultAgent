#!/usr/bin/env python3
"""Migrate one explicitly selected legacy Analysis Artifact."""

from __future__ import annotations

import argparse
import json

from fault_diagnosis.agent.artifact_migration import migrate_analysis_report_snapshot
from fault_diagnosis.domain.security.permissions import build_auth_context


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--analysis-artifact-id", required=True)
    parser.add_argument("--sql-artifact-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--role", required=True, choices=("guest", "engineer", "admin"))
    parser.add_argument("--asset", action="append", default=[])
    parser.add_argument("--table", action="append", default=[])
    args = parser.parse_args()
    auth = build_auth_context(
        user_id=args.user_id,
        role=args.role,
        asset_scope=args.asset,
        table_scope=args.table,
    )
    result = migrate_analysis_report_snapshot(
        thread_id=args.thread_id,
        analysis_artifact_id=args.analysis_artifact_id,
        sql_artifact_id=args.sql_artifact_id,
        auth_context=auth,
        apply=args.apply,
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "write_performed": result.write_performed,
                "source_analysis_artifact_id": result.source_analysis_artifact_id,
                "source_sql_artifact_id": result.source_sql_artifact_id,
                "migrated_artifact_id": result.migrated_artifact_id,
                "migration_tool_version": result.envelope.manifest.migration_tool_version,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
