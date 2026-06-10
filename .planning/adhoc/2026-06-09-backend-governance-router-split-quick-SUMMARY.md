---
mode: quick
date: 2026-06-09
task: backend-governance-router-split
status: completed-with-test-env-blocker
---

# Governance 路由拆分总结

## 已完成

- 新增 `fault_diagnosis/api/governance.py`，承接 `/api/governance/save`、`/api/governance/list`、`/api/governance/ledger`、`/api/governance/ledger/update`。
- 迁移治理快照、治理台账 payload、文件落盘 helper、列表聚合 helper 和台账过滤/更新 helper。
- `fault_diagnosis/app.py` 注册 `governance_router`，删除已迁移的内联 route/helper/model/import。
- `tests/test_governance_api.py` 的 `REPORTS_DIR` monkeypatch 目标从 `fault_diagnosis.app` 更新为 `fault_diagnosis.api.governance`。
- `docs/backend-refactor-roadmap.md` 已更新 Phase 1 进度，下一步建议迁移历史/Todo 路由。

## 契约影响

- `/api/governance/*` HTTP 路径、方法、请求体和响应外壳未变。
- 治理文件仍落在 `/reports/governance/*` URL 命名空间下。
- session scope cookie 写入继续通过共享 API helper 完成。
- 聊天、历史、Todo、PDF、TTS、健康检查、SSE 和静态资源挂载未迁移。

## 代码质量调整

- 新模块中的 Pydantic 列表字段使用 `Field(default_factory=list)`，避免可变默认值。
- 新 router 使用 `api.governance` 独立 logger，减少 `app.py` 继续承载业务日志上下文。

## 验证

- `python -m compileall -q fault_diagnosis/app.py fault_diagnosis/api tests/test_governance_api.py`：通过。
- AST 解析 `fault_diagnosis/app.py`、`fault_diagnosis/api/governance.py`、`tests/test_governance_api.py`：通过。
- `git diff --check`：通过，仅提示工作区 LF/CRLF 规范化 warning。
- 残留检查：`fault_diagnosis/app.py` 中不再包含 `/api/governance` route、治理 payload/helper 或 `REPORTS_DIR` 依赖。

## 未执行

- 未运行 pytest。当前项目没有可用的仓库要求 Python 3.12/pytest 测试环境，本轮按用户要求只做语法验证。

## 后续

- 恢复 Python 3.12 环境后优先补跑 `tests/test_governance_api.py` 以及本轮前后的 API 定向测试。
- 下一轮可继续拆 `fault_diagnosis/api/history.py`，迁移 `/ai/history/*` 和 `/api/todos/{thread_id}`。
