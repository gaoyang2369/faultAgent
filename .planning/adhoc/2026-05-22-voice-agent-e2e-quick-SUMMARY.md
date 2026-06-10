---
mode: quick
date: 2026-05-22
task: voice-agent-e2e
---

# 语音 Agent 端到端链路打通总结

## 修改内容

- Agent 后端新增 `POST /agent/chat` JSON 兼容接口，聚合现有 SSE 事件为 `reply_text` 与 `visual_actions`。
- `/agent/chat` 复用服务端会话身份判断，忽略不可信的 `metadata.user_identity`。
- 语音后端 `AgentClient` 优先调用 `/agent/chat`，保留旧 `/chat/stream` 兼容逻辑。
- 语音网关下行事件补充 `session_id`，新增兼容字段 `visual_actions` 与 `tts_audio_chunk`。
- 语音网关在单次话语进入 ASR 前追加声纹复核；不匹配时丢弃音频并返回 `voiceprint_rejected`。
- 前端语音问答改为消费语音后端返回的 Agent/TTS/visual actions，不再在收到 ASR 文本后重复触发普通文本聊天。

## 部署与验证

- Agent 后端分支：`codex/voice-agent-e2e`。
- live 前端分支：`codex/voice-agent-e2e`。
- 语音后端不是 Git 仓库，已备份：
  - `/media/lenovo/Desktop_pet/voice/gateway.py.bak-20260522-024843`
  - `/media/lenovo/Desktop_pet/voice/services/agent_client.py.bak-20260522-024843`
- 远程 Python 语法检查通过。
- live 前端 `npm run build` 通过。
- `/agent/chat` 非空请求返回结构化 JSON。
- 语音后端 `AgentClient.chat_stream()` 调用 Agent 成功。
- 语音 WebSocket 握手成功，缺失音频认证返回 `auth_rejected`。
- TTS 直连生成 PCM 音频块成功。

## 未覆盖

- 未在浏览器里用真实麦克风完成本人声纹认证、ASR、TTS 播放和视觉渲染全流程。
- 未提供非本人真实声音样本，声纹不匹配场景通过代码路径和缺失音频拒绝事件验证，未做真实声纹样本验证。
- ASR 空文本、Agent 异常和 TTS 失败未通过故障注入完整验证。
