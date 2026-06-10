---
mode: quick
date: 2026-06-09
task: api-health-tts-router-split
---

# Health 与 TTS 路由拆分计划

## 目标

按后端重构路线图 Phase 1 的低风险入口，先将 `/health/*` 与 `/tts/synthesize` 从 `app.py` 拆到 `fault_diagnosis/api/`，保持所有 HTTP 路径、请求和响应不变。

## 范围

- 新增 `fault_diagnosis/api/health.py`。
- 新增 `fault_diagnosis/api/tts.py`。
- 在 `fault_diagnosis/app.py` 注册这些 router，并删除已迁移的路由函数和局部辅助函数。

## 不做

- 不改聊天流、历史、PDF、治理、鉴权路由。
- 不改 SSE 协议。
- 不改前端调用。
- 不读取 `.env` 内容。

## 验证

- 运行定向测试：`tests/test_health.py`、`tests/test_tts_synthesize_api.py`、`tests/test_smoke.py`。
- 运行 `git diff --check`。
