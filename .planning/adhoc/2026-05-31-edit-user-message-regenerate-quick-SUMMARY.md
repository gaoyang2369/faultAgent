mode: quick
task: edit-user-message-regenerate
date: 2026-05-31

# Summary

实现用户消息编辑后重新生成，并根据反馈调整交互：

- 新增 `/chat/stream/edit`，按用户轮次截断历史并通过 SSE 重新生成。
- 编辑重生成改为在本次 LangGraph 输入中携带 `RemoveMessage(REMOVE_ALL_MESSAGES)` 和保留历史，避免单独 `aupdate_state` 造成连接中断。
- 前端用户消息支持编辑、保存并重新生成。
- 用户消息移除复制入口，改为编辑入口和历史版本入口。
- 每次编辑会记录上一版内容，气泡下方可查看历史版本。

# Verification

- `git diff --check` 通过。
- `npm.cmd run build` 未通过，当前失败点在既有 `FileUpload.vue` 类型错误（多处函数参数数量不匹配），与本次编辑消息改动无直接关系。
- 后端测试未能运行：当前 shell 中 `conda` 不在 PATH，`python` 不可用，`py.exe` 指向的 Python 安装无法启动。
