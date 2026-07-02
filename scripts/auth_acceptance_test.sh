#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "缺少命令：$1" >&2
    exit 2
  }
}

require_command curl
require_command "$PYTHON_BIN"

login() {
  local role="$1"
  local jar="$TMP_DIR/${role}.cookies"
  local output="$TMP_DIR/${role}.identity.json"
  curl --fail --silent --show-error --noproxy '*' \
    --cookie "$jar" --cookie-jar "$jar" \
    --header 'Content-Type: application/json' \
    --data "{\"role\":\"${role}\"}" \
    "$BASE_URL/auth/dev-login" >"$output"
  "$PYTHON_BIN" - "$output" "$role" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
role = sys.argv[2]
assert payload["role"] == role, payload
assert payload["auth_method"] == "dev-login", payload
assert isinstance(payload["permissions"], list), payload
assert isinstance(payload["asset_scope"], list), payload
assert isinstance(payload["allowed_tables"], list), payload
PY
  echo "[PASS] dev-login $role"
}

stream_case() {
  local role="$1"
  local case_name="$2"
  local message="$3"
  local jar="$TMP_DIR/${role}.cookies"
  local output="$TMP_DIR/${role}-${case_name}.sse"
  curl --fail --silent --show-error --no-buffer --noproxy '*' \
    --get --cookie "$jar" \
    --data-urlencode "message=$message" \
    --data-urlencode 'user_identity=管理员' \
    "$BASE_URL/chat/stream" >"$output"
  "$PYTHON_BIN" - "$output" "$role" "$case_name" <<'PY'
import json, sys

path, role, case_name = sys.argv[1:]
events = []
with open(path, encoding="utf-8") as handle:
    for raw_line in handle:
        if raw_line.startswith("data:"):
            events.append(json.loads(raw_line.removeprefix("data:").strip()))

complete = next((item for item in reversed(events) if item.get("type") == "chat_complete"), None)
assert complete, "缺少 complete/chat_complete 事件"
authorization = complete.get("authorization")
assert isinstance(authorization, dict), complete
assert "allowed" in authorization and "mode" in authorization, authorization
tools = [item.get("tool") for item in events if item.get("type") == "tool_start"]
report_url = complete.get("report_url")

if case_name == "guest_knowledge":
    assert role == "guest" and authorization["allowed"] is True
    assert "query_knowledge_base" in tools, tools
    assert authorization["kb_scope"]["allowed_visibility"] == ["public"]
elif case_name == "guest_status":
    scope = authorization["data_scope"]
    assert scope["allowed_tables"] == ["real_data_01"], scope
    assert scope["max_lookback_hours"] == 1, scope
    assert "sql_db_query" in tools, tools
elif case_name == "guest_diagnosis":
    assert authorization["mode"] == "degrade", authorization
    assert "fault_diagnosis" in authorization["denied_nodes"], authorization
    assert "save_report" not in tools, tools
    assert report_url is None, report_url
elif case_name == "engineer_allowed":
    assert authorization["mode"] == "allow", authorization
    assert complete["decision"]["task_family"] == "diagnosis"
    assert "sql_db_query" in tools, tools
elif case_name == "engineer_denied":
    assert authorization["allowed"] is False, authorization
    assert authorization["denied_reason_code"] == "asset_out_of_scope", authorization
    assert not tools, tools
elif case_name == "admin_report":
    assert authorization["mode"] == "allow", authorization
    assert "save_report" in tools, tools
    assert isinstance(report_url, str) and report_url.startswith("/reports/"), report_url
elif case_name == "action":
    assert not any(tool and ("control" in tool or "write" in tool or "dispatch" in tool) for tool in tools), tools
    permission = complete.get("permission_check") or {}
    assert permission.get("allowed") is False, permission
else:
    raise AssertionError(f"未知验收场景：{case_name}")
PY
  echo "[PASS] $role/$case_name authorization tool_start report_url"
}

for role in guest engineer admin; do
  login "$role"
done

stream_case guest guest_knowledge '故障码 F01002 是什么意思'
stream_case guest guest_status '查询 J1号机当前状态'
stream_case guest guest_diagnosis '诊断 J1号机异常并生成报告'
stream_case engineer engineer_allowed '诊断 J1号机异常'
stream_case engineer engineer_denied '诊断 J2号机异常'
stream_case admin admin_report '生成 J99号机诊断报告'

for role in guest engineer admin; do
  stream_case "$role" action '重启 J1号机'
done

guest_pdf_status="$(curl --silent --noproxy '*' --output /dev/null --write-out '%{http_code}' \
  --cookie "$TMP_DIR/guest.cookies" "$BASE_URL/admin/pdfs")"
admin_pdf_status="$(curl --silent --noproxy '*' --output /dev/null --write-out '%{http_code}' \
  --cookie "$TMP_DIR/admin.cookies" "$BASE_URL/admin/pdfs")"
test "$guest_pdf_status" = "403"
test "$admin_pdf_status" = "200"
echo '[PASS] guest 禁止 PDF 管理，admin 允许 PDF 管理'

echo '权限验收全部通过。'
