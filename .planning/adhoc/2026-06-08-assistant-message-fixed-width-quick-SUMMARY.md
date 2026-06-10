---
mode: quick
date: 2026-06-08
task: assistant-message-fixed-width
---

# 助手消息固定宽度 quick 总结

## 问题

助手消息主容器只有 `max-width: 80%`，没有显式 `width`。执行阶段先出现短状态条和较少任务内容时，容器会按内容收缩；后续任务面板、摘要或最终回答变长后，容器再被撑宽，导致视觉上越来越宽。

## 变更

- `agent_fronted/src/assets/ChatMessage.css`
  - 为非用户消息 `.message:not(.message-user) .content` 增加固定 `width: 80%`。
  - 为助手消息的直接子元素设置 `width: 100%` 和 `box-sizing: border-box`，让状态条、任务面板、摘要面板和最终回答使用同一稳定宽度。
  - 保持用户消息气泡继续按内容自适应。
- `agent_fronted/src/components/ChatMessage.layout.test.mjs`
  - 增加样式断言，防止助手消息固定宽度约束被误删。

## 验证

- `node src\components\ChatMessage.layout.test.mjs` 通过。
- `npm.cmd run build` 通过；Vite 仍有既有 chunk 体积警告。
- `git diff --check` 通过；仅输出 Windows 换行提示。
- 本地 Vite 开发服务已启动：`http://127.0.0.1:9006/`。
