---
mode: debug
date: 2026-05-31
task: streaming-tts-merge
---

# 流式播报合并调试总结

## 完成内容

- 通过 stash 备份本地未提交修改，快进同步远端 `Code-refactoring-version`，并恢复本地代码。
- 手动整合 `CustomerService.vue` 的语音播放冲突，保留消息编辑、文字发送时中止语音、网关音频直接播放和卸载清理逻辑。
- 新增普通文字回复的流式播报开关，默认关闭，并在浏览器本地存储中保存选择。
- 新增串行 TTS 合成队列：按句末标点或长度切段，清理 Markdown，只在用户停止时取消当前任务。
- 新增 `/tts/synthesize` 后端代理接口，以及可配置的 TTS 服务地址、超时和长度限制。
- 将文字流式 TTS 服务地址改为显式配置，避免误用仅提供 WebSocket 的语音网关端口。
- 增加文本切段测试和 TTS 代理接口测试。

## 验证结果

- `node src/utils/streamingTtsText.test.mjs` 通过。
- `node src/components/ChatMessage.layout.test.mjs` 通过。
- `npm.cmd exec vue-tsc -- -b --pretty false` 通过。
- `npm.cmd run build` 通过。
- `git diff --check` 通过。
- 未发现残留 Git 冲突标记。

## 环境限制

- Python 虚拟环境和系统 Python 3.12 启动器当前均无法创建 Python 进程，因此未能运行后端 pytest。
- 本地浏览器连接当前不可用，因此未执行页面点击验证。
- `10.108.13.254:8100` 当前未稳定提供 HTTP `/tts/synthesize`，文字回复流式播报需要在 `.env` 中显式设置真实 `TTS_SYNTHESIZE_URL`。

## 回退点

- 保留 `stash@{0}`：`codex-before-remote-sync-streaming-tts-2026-05-31`。
