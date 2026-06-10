# Restart And History Refresh Hotfix 总结

## Git 与回退点
- 基线 checkpoint：`55d04cd` `checkpoint/pre-history-refresh-hotfix-20260410`
- 基线 tag：`pre-history-refresh-hotfix-20260410`
- 热修分支：`hotfix/restart-and-chat-history-refresh-bug`

## 恢复结果
- 后端已通过 `scripts/run_backend.ps1` 恢复，`http://127.0.0.1:8000/` 返回 `200`
- 前端已通过 `scripts/run_frontend_dev.ps1` 保持运行，`http://127.0.0.1:9005/` 返回 `200`
- 当前环境可稳定复现刷新后历史异常

## 根因结论
- 说话人混乱：
  - 历史 API 返回 LangChain 风格角色 `human/ai`
  - 前端刷新恢复链路直接用 `message.role` 渲染，未将历史角色统一为 `user/assistant`
- 刷新后乱码：
  - 在真实 PostgreSQL checkpoint 中，`HumanMessage.content` 已出现 `????` 退化
  - 前端刷新时无条件信任服务端历史，导致服务端退化文本覆盖了本地缓存中的正确 user 文本
- 两者不是同一个根因

## 实际修改
- 前端新增 `agent_fronted/src/utils/chatMessageModel.js`
  - 统一角色映射
  - 展平结构化 content
  - 识别退化文本
  - 用本地缓存修补服务端退化历史
- 前端接入：
  - `agent_fronted/src/composables/useChatStream.ts`
  - `agent_fronted/src/utils/chatSessionCache.ts`
- 后端对齐：
  - `fault_diagnosis/utils.py` 中 `sanitize_for_json()` 统一历史角色为 `user/assistant/tool/system`
  - 同时展平 LangChain 结构化消息 content
- 测试：
  - `agent_fronted/src/utils/chatMessageModel.test.mjs`
  - `tests/test_utils.py`
  - `tests/test_history_api.py`

## 验证
- `node agent_fronted/src/utils/chatMessageModel.test.mjs` 通过
- `powershell -ExecutionPolicy Bypass -File .\\scripts\\run_tests.ps1` 通过，`105 passed`
- `npm run build` 通过
- 热修后历史接口样本已返回 `role: "user"` / `role: "assistant"`

## 剩余风险
- 服务端持久化链路中 user 文本退化问题尚未在存储层根治
- 当前热修已保证“同浏览器刷新恢复”优先显示本地缓存中的正确文本，但跨浏览器 / 无本地缓存场景仍可能看到服务端已退化的旧 user 文本
