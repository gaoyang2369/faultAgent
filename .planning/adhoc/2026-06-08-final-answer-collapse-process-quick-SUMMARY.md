---
mode: quick
date: 2026-06-08
task: final-answer-collapse-process
---

# 最终回答突出展示 quick 总结

## 问题

完整诊断报告生成后，助手消息仍然把摘要、证据、结构化诊断结果和执行明细全部展开在最终回答上方，导致最终结果被挤到很靠下的位置，阅读重点不清晰。

## 变更

- `agent_fronted/src/components/ChatMessage.vue`
  - 新增最终回答后的过程折叠逻辑。
  - 最终回答出现前，任务进度、摘要、证据、结构化流程仍按原样展示。
  - 最终回答出现后，自动收起任务进度、摘要、证据、结构化流程和明细，只保留紧凑的“查看详情”入口。
  - 点击“查看详情”后展开完整过程信息，再次点击可收起。
- `agent_fronted/src/assets/ChatMessage.css`
  - 增加紧凑过程详情入口的浅色/暗色样式。
- `agent_fronted/src/components/ChatMessage.layout.test.mjs`
  - 增加布局断言，覆盖最终回答后过程信息自动折叠的约束。

## 验证

- `node src\components\ChatMessage.layout.test.mjs` 通过。
- `npm.cmd run build` 通过；Vite 仍有既有 chunk 体积警告。
- `git diff --check` 通过；仅输出 Windows 换行提示。
- 本地 Vite 开发服务可访问：`http://127.0.0.1:9006/`。
