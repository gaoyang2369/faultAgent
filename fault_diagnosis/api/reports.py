"""Authenticated report delivery; report files are not mounted as public static assets."""

from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..common.paths import REPORTS_DIR
from ..security.policy_engine import asset_is_in_scope
from ._shared import resolve_request_auth_context

router = APIRouter()
_SAFE_REPORT_NAME = re.compile(r"^[A-Za-z0-9._-]{1,160}\.html$")


def _report_path(filename: str) -> str:
    if not _SAFE_REPORT_NAME.fullmatch(filename or ""):
        raise HTTPException(status_code=404, detail="report not found")
    root = os.path.abspath(REPORTS_DIR)
    path = os.path.abspath(os.path.join(root, filename))
    if os.path.commonpath([root, path]) != root:
        raise HTTPException(status_code=404, detail="report not found")
    return path


@router.get("/reports/{filename}")
async def get_report(request: Request, filename: str):
    _, _, _, auth_context = resolve_request_auth_context(request)
    if not auth_context.has_permission("data.report.read") and not auth_context.has_permission("data.report.read_all"):
        raise HTTPException(status_code=403, detail="当前身份无权查看诊断报告。")

    report_path = _report_path(filename)
    if not os.path.isfile(report_path):
        raise HTTPException(status_code=404, detail="report not found")
    if not auth_context.is_admin():
        try:
            with open(f"{report_path}.access.json", "r", encoding="utf-8") as handle:
                access = json.load(handle)
        except (OSError, json.JSONDecodeError):
            raise HTTPException(status_code=403, detail="该报告缺少可验证的访问范围。")
        diagnosis_object = str(access.get("diagnosis_object") or "").strip()
        report_tables = {str(value).strip() for value in access.get("authorized_table_scope", []) if str(value).strip()}
        current_tables = set(auth_context.table_scope)
        asset_allowed = bool(diagnosis_object) and asset_is_in_scope(diagnosis_object, auth_context.asset_scope)
        tables_allowed = not report_tables or report_tables.issubset(current_tables)
        if not asset_allowed or not tables_allowed:
            raise HTTPException(status_code=403, detail="当前账号无权查看该报告。")
    return FileResponse(report_path, media_type="text/html; charset=utf-8")
