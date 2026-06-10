---
mode: quick
date: 2026-06-09
task: api-health-tts-router-split
status: completed-with-test-env-blocker
---

# Health 与 TTS 路由拆分总结

## 已完成

- 新增 `fault_diagnosis/api/health.py`，承接 `/health/dependencies`、`/health/ocr`、`/health/real`。
- 新增 `fault_diagnosis/api/tts.py`，承接 `/tts/synthesize` 和 TTS 请求辅助逻辑。
- 新增 `fault_diagnosis/api/__init__.py`。
- `fault_diagnosis/app.py` 改为注册 `tts_router` 和 `health_router`，删除已迁移的内联 route/helper 和无用 import。
- `tests/test_tts_synthesize_api.py` 的 monkeypatch 目标从 `fault_diagnosis.app` 改为 `fault_diagnosis.api.tts`。

## 契约影响

- HTTP 路径未变。
- 请求体和响应体处理逻辑未变。
- SSE、聊天、PDF、治理、鉴权相关接口未改动。

## 验证

- `git diff --check`：通过。
- `python -c "... ast.parse(..., encoding='utf-8-sig') ..."`：通过。
- `python -m compileall -q fault_diagnosis/app.py fault_diagnosis/api tests/test_tts_synthesize_api.py`：通过。
- `python -m pytest -q tests/test_health.py tests/test_tts_synthesize_api.py tests/test_smoke.py`：未能执行，当前默认 `Python314` 没有安装 `pytest`。
- `powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1`：未能执行，脚本要求的 `.venv`、`.conda312` 或 `C:\miniconda3` Python 3.12 解释器当前不存在。

## 后续

- 在可用 Python 3.12 测试环境恢复后，优先补跑 `tests/test_health.py tests/test_tts_synthesize_api.py tests/test_smoke.py`。
- 下一步可按路线图继续拆 `/auth/*` 和 `/admin/pdfs*`，但应保持 endpoint path 与前端调用不变。

