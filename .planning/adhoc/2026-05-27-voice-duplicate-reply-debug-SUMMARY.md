---
mode: debug
date: 2026-05-27
task: voice-duplicate-reply
---

# 语音提问重复回复排查总结

## 结论

重复回复由前端语音事件合并回归引起：`asr_result` 到达后，前端会把识别文本提交给普通聊天流；同时语音网关后续返回的 Agent/TTS 事件仍被前端消费，导致同一轮语音提问出现两条助手回复。

远端语音网关当前在发送 `asr_result` 后仍会执行 `_run_agent_and_tts(session, transcript)`。因此当前架构仍存在两路 Agent 请求的可能；本次修复恢复前端的接管屏蔽逻辑，先消除重复展示和重复播放。

## 修复

- 在 `asr_result` 分支恢复 `isVoiceQuestionHandledByChatStream = true`。
- 语音文本交给普通聊天流后关闭语音网关 TTS 播放，避免同一轮产生第二路音频/回复。
- 文字输入链路未改动。

## 后续收敛

- 若要彻底消除冗余 Agent 调用，应让语音网关提供 ASR-only 模式，或改回由语音网关独占 Agent 调用的单路模式。

## 验证

- `npm run build` 通过。
