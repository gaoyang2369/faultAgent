---
mode: debug
date: 2026-04-17
owner: codex
status: completed
objective: 复现并修复聊天页“实时展示正常但刷新后历史恢复错乱”的问题，统一实时 SSE 与历史恢复的消息归一化/渲染模型，并完成真实回归验证。
---

# History Refresh Render Debug 总结

## 已确认根因
1. 实时 SSE 路径只维护一个 assistant 主消息，并把工具过程聚合到 `toolEvents` / `taskSnapshot`。
2. 刷新恢复路径直接消费 `/ai/history/service/{thread}` 的 checkpoint 原始消息，返回里包含 `ToolMessage` 与中间态 `AIMessage`。
3. 前端原先对 history 仅做基础 normalize，没有把 `assistant + tool + assistant` 回合重新折叠成与实时路径一致的读模型，导致 raw `ToolMessage` 和中间态 assistant 被直接渲染进正文。

## 修复内容
1. 前端 `chatMessageModel.js` 增加 history hydration：按用户回合折叠原始 history，把工具消息改挂到 assistant 的 `toolEvents`，并优先复用同浏览器缓存中的更丰富事件信息。
2. 主消息可渲染规则收紧：raw `tool` 不再进入主正文区，只作为折叠明细存在。
3. `ChatMessage.vue` 的工具明细支持显示 `details`，历史工具原始反馈会保留在折叠区，不再污染正文。
4. 后端 `sanitize_for_json` 为 history 增补 `ToolMessage.name` / `tool_call_id`，帮助前端恢复真实工具名，保持接口向后兼容。

## 真实回归结果
1. 中文简单问答、英文简单问答的 history 恢复都稳定为 `user + assistant` 两条消息。
2. 中文工具 + KB 请求的原始 history 仍是 `user + assistant + tool + tool + assistant`，但前端 hydrate 后恢复为 `user + assistant`，且 assistant 保留工具折叠明细。
3. 使用缓存增强时，可恢复更完整的 `tool_start` / `tool_end` 数量；带任务清单的线程在刷新恢复后仍能挂回 `taskSnapshot(total=3)`。
4. 前端 `chatMessageModel.test.mjs`、`npx tsc --noEmit`、`npm run build` 已通过；后端健康检查与真实 `/chat/stream` 回归通过。

## 残留说明
- 后端 history 接口本身仍返回 checkpoint 原始消息列表，这是兼容性保留；这次修复点放在前端 hydrate/render 层，不需要迁移或删改已有历史数据。
- 终端里用 PowerShell 内联脚本发中文 prompt 时，命令行回显可能出现 `????`，但这不影响浏览器/前端 UTF-8 正常链路，也不是本次刷新错乱的根因。
