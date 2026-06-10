---
mode: debug
date: 2026-04-17
owner: codex
status: completed
objective: 修复聊天页“用户上滑查看旧内容时仍被流式输出强制拉回底部”的问题，改为智能自动跟随，并完成真实回归验证。
---

# Smart Autofollow Scroll Debug 计划

## 本轮范围
- 定位当前聊天页滚动控制入口和所有触发路径。
- 将“无条件滚到底部”改成“在底部附近时自动跟随，离开底部后暂停跟随”。
- 增加轻量“回到底部/继续跟随”提示，不打断现有聊天主流程。
- 保持 SSE、流式渲染、历史恢复、会话切换、工具调用不回退。

## 执行策略
1. 审查 `CustomerService.vue` 与 `useChatStream.ts` 的滚动调用链，确认哪些消息变化会触发强制滚动。
2. 在视图层建立统一滚动控制：维护“是否接近底部”“是否允许自动跟随”“是否有新内容未读”等状态。
3. 让 `scrollToBottom` 支持 `force` 选项：历史恢复/会话切换/用户点击按钮时强制滚动，流式 token/tool/ping 则仅在允许跟随时自动滚动。
4. 添加轻量“回到底部”按钮，用户滚离底部且有新内容时显示；点击后恢复自动跟随。
5. 重启前端并做短回复、长文本、工具调用、历史恢复、会话切换等回归。

## 当前已确认线索
1. 当前只有一个滚动入口：`CustomerService.vue` 中的 `scrollToBottom()`，内部直接执行 `messagesRef.scrollTop = scrollHeight`。
2. `useChatStream.ts` 会在发送消息、`start/ping/token/tool_start/tool_end/complete/error/interrupted`、本地缓存恢复等多个路径反复调用这个入口。
3. 因为这些调用都不区分“用户是否已主动离开底部”，所以用户一旦上滑，后续任意流式更新都会再次把视图拉回最底部。
