---
mode: quick
date: 2026-05-12
task: report-link-render-cleanup
---

# 报告链接渲染清洗总结

## 问题复现

| 场景 | HTML 残片 | 说明 |
|------|-----------|------|
| 实时 SSE 消息 | 已确认 | `linkifyReportMentions` 生成 `<a>` + 按钮双重渲染；LLM 输出 HTML 时 href 被二次匹配导致嵌套破损 |
| 历史消息 | 已确认 | 同一 `processContent` 路径复现 |
| 刷新/切换会话 | 已确认 | 同上 |
| 报告按钮重复 | 已确认 | 内联 `<a>` + el-button 同时出现 |

## 根因

1. **前端双重渲染（主因）**：`linkifyReportMentions()` 在正文生成内联 `<a class="report-link">` 标签，同时 `reportLinks` computed 提取相同 URL 渲染按钮 → 同一链接出现两次
2. **HTML 嵌套破损**：LLM 直接输出 `<a href="/reports/...">` 时，`linkifyReportMentions` 的 `DIRECT_REPORT_URL_PATTERN` 匹配到 `href` 属性内 URL，生成嵌套/截断 HTML
3. **属性文本暴露**：破损 HTML 经 `marked.parse()` + DOMPurify 后，`target="_blank"`、`rel="noopener noreferrer"`、`class="report-link"` 作为可见文本出现
4. **正则状态泄漏**：模块级 `gi` 正则在 `extractReportLinks` 中 `exec()` 后 `lastIndex` 不重置，间歇性提取失败
5. **DOMPurify 属性遗漏**：`ADD_ATTR` 未包含 `href`/`target`/`rel`

## 修复方案

**核心策略**：`linkifyReportMentions()` → `stripReportMentions()`，从正文中完全剥离报告相关内容，仅由按钮组件渲染。

| 环节 | 方案 |
|------|------|
| 链接提取 | `extractReportLinks()` 从原始 `message.content` 提取（不受 strip 影响） |
| 正文清洗 | `stripReportMentions()` 清除：完整/残缺 `<a>` 标签、Markdown 链接、裸 URL、反引号文件名、`【报告文件】` 行、`报告已保存至` 行、HTML 属性残片 |
| 按钮渲染 | 现有 el-button 不变，新增 "📄 诊断报告" 标题 + "报告文件暂不可用" 降级 |
| 非法链接 | `isSafeReportUrl()` 仅允许 `/reports/[A-Za-z0-9._-]+.(html|md)` |
| 一致性 | `processContent()` 统一处理实时/完成/历史/刷新所有路径 |
| 正则修复 | 模块级常量正则改为工厂函数（`makeDirectUrlRegex()` 等），每次调用产生新实例 |

## 修改清单

| 文件 | 改动 | 原因 |
|------|------|------|
| `agent_fronted/src/utils/reportLinks.js` | 新增 `stripReportMentions()`、`isSafeReportUrl()`；正则工厂函数修复 `lastIndex` 泄漏 | 核心清洗与安全逻辑 |
| `agent_fronted/src/utils/reportLinks.d.ts` | 添加 `stripReportMentions`、`normalizeReportFilename`、`toReportUrl`、`isSafeReportUrl` 类型声明 | 类型完整性 |
| `agent_fronted/src/components/ChatMessage.vue` | `processContent`: `linkifyReportMentions` → `stripReportMentions`；`reportLinks` 加 `.filter(isSafeReportUrl)`；新增 `hasReportMentionButNoLinks` computed；模板新增报告标题和降级提示；`openReport`/`openReportInNewTab` 加 `isSafeReportUrl` 校验；DOMPurify `ADD_ATTR` 补 `href`/`target`/`rel` | 消除双重渲染、HTML 残片、安全加固 |
| `agent_fronted/src/assets/ChatMessage.css` | 新增 `.report-section-header`、`.report-unavailable` 及暗色模式适配 | 视觉样式 |

## 测试结果

| 项目 | 结果 |
|------|------|
| 16 条 JS 逻辑测试 | 全部通过 |
| 真实场景（工作流最终回答/工具返回/LLM 生成 HTML/混合） | 全部通过 |
| `npm run build` | 通过 |
| 后端 pytest | 3F 为预存失败，非本次引入 |

## 最终效果

**用户看到：**
- "📄 诊断报告" 区域标题
- "查看报告" 主色按钮 + "新窗口打开" 按钮
- 报告不可用时显示 "报告文件暂不可用"

**用户不再看到：**
- `<a href=...>` / `</a>` 标签
- `target="_blank"` / `rel="noopener noreferrer"` / `class="report-link"` 属性文本
- 重复文件名残片
- 裸 URL 原文
- 内联链接与按钮的双重渲染

## 安全加固

- `isSafeReportUrl()`: 仅匹配 `/reports/[A-Za-z0-9._-]+.(html|md)`，拒绝 `..`、`\`、外部 URL
- `openReport` / `openReportInNewTab` 入口校验，非法 URL 提示并阻止
- `reportLinks` computed 过滤非安全 URL

## 遗留风险评估

- **旧历史消息兼容**：含原始 `<a>` 标签的旧消息通过 `stripReportMentions` 可正确清洗；已验证
- **`linkifyReportMentions` 保留**：函数仍 export 且逻辑修复，但 `ChatMessage.vue` 不再使用；如其他组件引用需评估
- **后端未改**：后端报告工具仍返回 `"报告已保存至：/reports/xxx.html"` 纯文本，由前端 strip 清洗；若后续希望结构化，可在 `complete` 事件的 `report_url` 字段中获取
