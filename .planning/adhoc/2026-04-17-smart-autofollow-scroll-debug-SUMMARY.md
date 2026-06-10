---
mode: debug
date: 2026-04-17
owner: codex
status: completed
objective: 修复聊天页“用户上滑查看旧内容时仍被流式输出强制拉回底部”的问题，改为智能自动跟随，并完成真实回归验证。
---

# Smart Autofollow Scroll Debug 总结

## 已确认根因
1. 当前聊天页只有一个滚动入口：`CustomerService.vue` 的 `scrollToBottom()`，内部直接执行 `messagesRef.scrollTop = scrollHeight`。
2. `useChatStream.ts` 会在发送消息、历史恢复、SSE `start/ping/token/tool_start/tool_end/complete/error/interrupted` 等多条路径频繁调用这个入口。
3. 这些调用不区分“用户是否已主动离开底部”，因此用户手动上滑后，后续任意流式更新都会再次把视图强制拉回到底部。

## 修复内容
1. `CustomerService.vue` 新增统一滚动状态：是否接近底部、是否允许自动跟随、是否存在下方未读新内容、是否处于程序性滚动。
2. `scrollToBottom()` 改成智能策略：只有在底部附近或显式 `force` 时才自动滚动；用户离开底部后，新的 token/tool/ping 只会标记“有新内容”，不会再强制改写滚动位置。
3. 增加轻量“回到底部”按钮，用户点击后会平滑滚到底部并恢复自动跟随。
4. 增加纯函数滚动策略工具与测试，覆盖“接近底部判断”“暂停跟随”“强制恢复跟随”等关键决策。
5. `useChatStream.ts` 改为区分滚动意图：用户发送消息、切换/恢复会话、本地缓存恢复时使用 `force`；流式 token/tool 更新继续走普通自动跟随。

## 真实回归结果
1. 前端滚动策略测试 `chatScrollStrategy.test.mjs` 已通过。
2. 消息模型测试 `chatMessageModel.test.mjs` 仍通过，说明本轮没有破坏历史恢复层。
3. `npx tsc --noEmit` 与 `npm run build` 已通过。
4. 后端 `8000` 健康检查正常，前端 dev server 已重启并在 `9005` 正常提供页面。

## 残留说明
- 运行态、构建态和滚动策略测试都已确认；像素级最终交互观感仍建议你在浏览器里手拖一次长文本流式回复做人工验收，因为当前环境没有现成的浏览器自动化工具。
