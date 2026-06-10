---
mode: debug
date: 2026-06-08
task: text-send-duplicate-reply
---

# 文本输入重复回复调试总结

## 发现

- 语音输入走语音网关事件合并路径，并通过 `hasActiveVoiceTurn`、`hasAppendedVoiceQuestion` 防止同一轮语音重复追加。
- 文字输入、模板提问、桌宠文本消息、编辑后重发都复用 `useChatStream.sendMessage`。
- `sendMessage` 原先只用 `isStreaming` 防重入，但 `isStreaming` 在 `await scrollToBottom()` 之后才置为 `true`。如果 Enter、点击或外部事件在同一帧重复触发，第二次调用会绕过防护并创建第二条 SSE 回复。

## 修复

- 在 `sendMessage` 增加同步提交锁 `isSubmittingMessage`。
- 进入发送流程时立即上锁，SSE 句柄创建完成或异常后释放。
- 保留原有 `isStreaming` 作为流式生成期间的状态控制，不改变后端接口和 SSE 协议。

## 验证

- `cmd /c npm run build` 通过。
- `git diff --check` 通过。
