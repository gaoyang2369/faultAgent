# Streaming Tool Render Bugfix 总结

## 已完成

- 后端在 [`fault_diagnosis/streaming.py`](D:/code/fault-diagnosis-master/fault_diagnosis/streaming.py) 增加了流式 token 过滤：
  - 抑制工具调用阶段泄露出来的 SQL / JSON 参数草稿
  - 仅在检测到用户可见自然语言后再推送首段 token
  - 修复 `StopAsyncIteration` 被误判为流式错误的问题
- 前端在 [`agent_fronted/src/composables/useChatStream.ts`](D:/code/fault-diagnosis-master/agent_fronted/src/composables/useChatStream.ts) 接入新的工具结果摘要 helper：
  - 不再把对象直接渲染成 `[object Object]`
- 新增工具结果摘要 helper：
  - [`agent_fronted/src/utils/toolEventSummary.js`](D:/code/fault-diagnosis-master/agent_fronted/src/utils/toolEventSummary.js)
  - [`agent_fronted/src/utils/toolEventSummary.d.ts`](D:/code/fault-diagnosis-master/agent_fronted/src/utils/toolEventSummary.d.ts)
  - [`agent_fronted/src/utils/toolEventSummary.test.mjs`](D:/code/fault-diagnosis-master/agent_fronted/src/utils/toolEventSummary.test.mjs)
- 新增 / 更新回归测试：
  - [`tests/test_tool_calls.py`](D:/code/fault-diagnosis-master/tests/test_tool_calls.py)

## 验证结果

- 定向 Python 回归：
  - `pytest tests/test_tool_calls.py -q -p no:cacheprovider`
  - 结果：`5 passed`
- 前端摘要检查：
  - `node src/utils/toolEventSummary.test.mjs`
  - 结果：`toolEventSummary checks passed`
- 全量 pytest：
  - `powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1`
  - 结果：`105 passed`
- 前端 build：
  - `npm run build`
  - 结果：通过
  - 首次在沙箱内因 `esbuild spawn EPERM` 失败，已在提权后复验通过
- 当前代码版本前后端启动探针：
  - `http://127.0.0.1:8000/` 返回 `200`
  - `http://127.0.0.1:9005/` 返回 `200`

## 当前可人工复测

- 前端：`http://127.0.0.1:9005/`
- 后端：`http://127.0.0.1:8000/`
