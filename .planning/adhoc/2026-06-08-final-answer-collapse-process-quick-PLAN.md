---
mode: quick
date: 2026-06-08
task: final-answer-collapse-process
---

# 最终回答突出展示 quick 计划

## 目标

执行过程中保留摘要、任务进度、证据和结构化流程展示；一旦最终回答生成，自动收起这些过程信息，突出最终结果，并保留“查看详情”入口供用户展开。

## 范围

- 调整 `ChatMessage.vue` 中助手消息的展示条件。
- 复用现有 `assistantDetailsExpanded` 状态和“查看详情”按钮。
- 不改 SSE 协议、消息模型字段和后端接口。

## 验证

- 更新并运行 `ChatMessage.layout.test.mjs`。
- 运行前端构建。
- 运行 `git diff --check`。
