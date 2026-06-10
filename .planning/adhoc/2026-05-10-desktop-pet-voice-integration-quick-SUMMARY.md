---
mode: quick
date: 2026-05-10
task: desktop-pet-voice-integration
---

# 桌宠与语音网关联调整合总结

## 整合来源

本记录整合了以下零散 quick 记录的有效内容：

- `2026-05-08-desktop-pet-user-info-dialog-quick-*`
- `2026-05-08-desktop-pet-voice-auth-quick-*`
- `2026-05-10-desktop-pet-flow-align-quick-*`
- `2026-05-10-desktop-pet-message-submit-quick-*`
- `2026-05-10-desktop-pet-permission-voice-quick-*`
- `2026-05-10-desktop-pet-ws-port-quick-*`
- `2026-05-10-identity-display-copy-quick-*`
- `2026-05-10-identity-role-label-quick-*`
- `2026-05-10-voice-gateway-compatible-quick-*`
- `2026-05-10-voice-gateway-url-role-quick-*`

## 完成内容

- 新增桌宠桥接能力：`useDesktopPetBridge` 连接桌宠 WebSocket，接收 `user_info`、失败类消息、`questions` 和 `type: "message"` 文本消息。
- 桌宠桥接默认地址优先尝试 `ws://{host}:3000` 和 `ws://{host}:3000/ws`，并保留旧的 `8765` / `8765/ws` 候选地址；仍支持 `VITE_DESKTOP_PET_WS_URL`、`desktopPetWs`、`wsUrl` 覆盖。
- 新增 `DesktopPetIdentityDialog`，展示“正在识别 / 识别成功 / 识别失败”、用户编号、当前身份和权限提示。
- 桌宠来源跳转只承接身份结果或等待桥接消息，不再默认打开网页内录音弹窗；显式 `voiceAuth=1` 仍可打开网页声纹认证。
- 桌宠语音文本通过全局事件和 30 秒 pending 缓存转发给聊天页，聊天页填入输入框并调用现有 `sendMessage()`；启动问候语会被过滤，避免误提交。
- 新增语音网关前端接入：`voiceGateway.ts` 管理 WebSocket，`useVoiceGatewaySession.ts` 管理认证、ASR、Agent、TTS 事件，`pcmAudio.ts` / `pcmPlayer.ts` 处理 16kHz / 16bit / 单声道 PCM 采集、编码和播放。
- 语音网关本地开发默认地址为 `ws://10.108.13.254:8100/ws/voice`，仍可通过 `VITE_VOICE_WS_URL` 覆盖。
- 声纹认证通过 `auth_request` 完成；语音提问通过 `audio_chunk` / `speech_end` 发送，并兼容后续 `agent_token`、`agent_complete`、`thread_id` 等事件。
- 身份角色兼容 `user_role`、`role`、`clean_role`、`permission_hint`，并按 L1-L5 映射到 `数据录入员`、`数据分析师`、`系统工程师`、`技术专家`、`总监`。
- 身份展示主文案统一为“某身份识别已完成”，有身份结果时状态固定显示“识别已完成”，样式保持完成态。
- 权限提示不会再被当作身份名称；例如 `用户权限：全局管理` / `全局管理` 会映射为 `总监`，避免显示“全局管理身份识别已完成”。
- 上传入口权限改为“已识别且非访客”，`总监`、`系统工程师`、`数据分析师` 等非访客身份都可看到上传按钮。
- 聊天页麦克风入口不再强制要求先完成网页内声纹认证，点击后可直接连接语音网关并发送音频。

## 保留记录

- `2026-05-08-quick-question-collapse-quick-*` 属于独立的快速提问区域体验优化，未合并。
- `2026-04-27-admin-upload-test-default-quick-*` 是临时测试默认权限记录，和当前桌宠/语音身份链路重复且容易误导，已删除。

## 验证

- 相关功能改动均曾通过 `npm.cmd run build`。
- 普通沙箱构建多次因 esbuild 子进程 `spawn EPERM` 失败，提权后构建成功。
- Vite 仍提示部分 chunk 超过 500 kB，和这些联调改动无关。
- 本次仅整理 `.planning/adhoc` 记录，未改动业务代码。
