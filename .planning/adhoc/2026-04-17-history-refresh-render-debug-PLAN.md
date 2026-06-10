---
mode: debug
date: 2026-04-17
owner: codex
status: completed
objective: 复现并修复聊天页“实时展示正常但刷新后历史恢复错乱”的问题，统一实时 SSE 与历史恢复的消息归一化/渲染模型，并完成真实回归验证。
---

# History Refresh Render Debug 计划

## 本轮范围
- 复现“刷新前正常、刷新后正文出现原始对象/工具输出/中间态消息直出”的前端问题。
- 对比实时 SSE 路径与 history/local cache 恢复路径的消息结构、归一化和渲染差异。
- 在不破坏真实 SSE、工具调用、assistant 落库与历史恢复接口的前提下，做最小侵入修复。
- 完成简单问答、工具调用、KB 长文本、刷新恢复、重启恢复的真实回归验证。

## 执行策略
1. 先抓取一轮真实工具调用线程，保留实时事件摘要、history API 返回和刷新后恢复所需的原始证据。
2. 审查 `useChatStream.ts`、`chatMessageModel.js`、`api.js`/`api.d.ts`、`ChatMessage.vue` 与后端 history 接口，确认实时消息与历史消息是否走同一归一化层。
3. 优先在前端做统一的 history hydration/normalize 兼容层，把原始 `ToolMessage`、中间态 `AIMessage`、todos 快照折叠进可控的消息读模型，而不是粗暴删消息。
4. 仅在后端返回结构明显不安全或不一致时，再做最小必要补充；保持现有 HTTP 接口与数据库历史兼容。
5. 修复后重启前后端并做刷新前后对照回归，确认正文、任务面板、工具链明细三者在实时与历史恢复中保持一致。

## 当前已确认线索
1. 实时路径会把工具执行过程聚合到 assistant 消息上的 `toolEvents` / `taskSnapshot`，不会把原始 `ToolMessage` 直接作为正文消息展示。
2. 历史恢复路径当前直接消费 `/ai/history/service/{thread}` 返回的 checkpoint 原始消息；其中 `ToolMessage` 会被前端归为 `tool`，并被主消息列表直接渲染。
3. 后端 `sanitize_chat_history_messages` 目前只做角色和内容清洗，不会把工具/中间态消息折叠成与实时路径一致的前端读模型。
4. 修复后已确认：history 原始接口仍保持兼容，但前端 hydrate 会把 `assistant + tool + assistant` 还原成单条 assistant 主消息，并把工具结果放入折叠明细；同浏览器缓存可进一步恢复 `tool_start`/`tool_end` 与任务快照。
