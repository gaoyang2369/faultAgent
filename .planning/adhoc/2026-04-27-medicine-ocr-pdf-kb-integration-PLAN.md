---
mode: debug
date: 2026-04-27
task: medicine-ocr-pdf-kb-integration
base_branch: Code-refactoring-version
working_branch: integration-medicine-ocr-pdf-kb
---

# medicineOCR 审计与 PDF->OCR->知识库底座集成计划

## 目标

先审计并运行评估 `medicineOCR`，在不破坏当前聊天主链路和现有上传能力的前提下，打通“管理员上传 PDF -> 后端保存 -> OCR/解析 -> 结构化结果持久化 -> 知识库底座归档”。

## 范围

- 审计 `medicineOCR` 的目录结构、入口、依赖、输入输出和外部环境要求。
- 明确它当前是否可直接运行，若不可用则做最小侵入修复与可降级封装。
- 扩展管理员 PDF 记录的处理状态、OCR 结果和知识库归档元信息。
- 将 OCR/解析结果持久化到受控目录，并接入可重建/可删除的上传文档知识库。
- 调整前端上传页，展示 OCR / 知识库处理状态与结果摘要。
- 补齐对应 smoke / 接口 / 删除链路测试。

## 非目标

- 不重写 `medicineOCR` 的整套算法实现。
- 不在本轮完成复杂实时推送或完整 Agent 推理优化。
- 不直接污染现有主知识库 PDF 语料目录。

## 风险控制

- 若 `medicineOCR` 缺依赖、缺模型或依赖 GPU，本轮必须显式降级，不能伪装为真实 OCR 成功。
- 删除上传记录时，同步处理文件、OCR 结果和上传知识库索引，避免孤儿记录。
- 现有聊天、SSE、历史恢复和报告链接必须回归验证。

## 验证

- `medicineOCR` 能给出明确可用性结论，并有最小 smoke test 覆盖。
- 上传一个 PDF 后，原文件、OCR/解析结果、结构化结果和知识库归档记录都可落盘。
- 前端历史列表能看到状态变化，刷新后仍保持。
- 删除记录后，文件和上传知识库内容按设计移除或重建。
- `query_knowledge_base` 至少能检索到上传文档知识来源，且原主链路不回退。
