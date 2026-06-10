---
mode: quick
date: 2026-05-12
task: report-link-render-cleanup
---

# 报告链接渲染清洗 — 消除 HTML 残片与双重渲染

## 背景

Agent 生成报告后，聊天消息中出现严重渲染污染：

1. 正文内联 `<a href="/reports/..." target="_blank" rel="noopener noreferrer" class="report-link">` 标签 + 下方按钮同时渲染 → 双重显示
2. LLM 直接输出 HTML 链接时，`linkifyReportMentions` 的 `DIRECT_REPORT_URL_PATTERN` 匹配到 `href` 属性内 URL，产生嵌套破损 HTML
3. 破损 HTML 经 `marked.parse()` + DOMPurify 处理后，`target="_blank"`/`rel="noopener noreferrer"`/`class="report-link"` 暴露为可见文本
4. `extractReportLinks` 使用模块级带 `gi` 标志正则 + `exec()`，`lastIndex` 跨次调用泄漏导致间歇性提取失败
5. DOMPurify `ADD_ATTR` 遗漏 `href`/`target`/`rel`

## 根因

- **前端双重渲染（主因）**：`linkifyReportMentions()` 在正文中生成内联 `<a>` 标签，同时 `reportLinks` computed 提取相同 URL 渲染按钮
- **HTML 冲突**：LLM 输出 `<a href=...>` → `linkifyReportMentions` 二次匹配 → 嵌套破损
- **正则状态泄漏**：模块级 `DIRECT_REPORT_URL_PATTERN`（带 `gi`）在 `extractReportLinks` 中 `exec()` 后 `lastIndex` 不重置

## 目标

- 正文只保留语义内容，不渲染任何报告链接/HTML/属性残片
- 报告入口统一由按钮组件渲染："📄 诊断报告" → ["查看报告"] ["新窗口打开"]
- 清洗覆盖：完整/残缺 `<a>` 标签、Markdown 链接、裸 URL、反引号文件名、`【报告文件】` 行、`报告已保存至` 行、HTML 属性残片
- 仅允许安全的 `/reports/[A-Za-z0-9._-]+.(html|md)` URL
- 实时/完成/历史/刷新/切换会话路径一致

## 范围

### 修改文件

| 文件 | 改动 |
|------|------|
| `agent_fronted/src/utils/reportLinks.js` | 新增 `stripReportMentions()`、`isSafeReportUrl()`；修复正则状态泄漏（工厂函数） |
| `agent_fronted/src/utils/reportLinks.d.ts` | 补充类型声明 |
| `agent_fronted/src/components/ChatMessage.vue` | `linkifyReportMentions` → `stripReportMentions`；`reportLinks` 加 `isSafeReportUrl` 过滤；新增 `hasReportMentionButNoLinks`；报告区标题+降级提示；`openReport`/`openReportInNewTab` 加安全校验；DOMPurify 补 `href`/`target`/`rel` |
| `agent_fronted/src/assets/ChatMessage.css` | 新增 `.report-section-header`、`.report-unavailable` 样式 |

### 不修改

- 后端：报告生成逻辑不变，仍返回 `/reports/xxx.html` 纯文本（工具返回值和 `【报告文件】` 标记）
- `extractReportLinks` 的提取逻辑仍从原始 `message.content` 工作（不受 strip 影响）
- 按钮点击行为（drawer/new-tab）逻辑不变

## 验证

- `npm run build` 通过
- 16 条 JS 单元测试全通过（HTML 清洗、Markdown 链接、裸 URL、去重、路径穿越、外部 URL、属性残片）
- 4 组真实场景验证通过（工作流最终回答、工具返回、LLM 生成 HTML、混合场景）
