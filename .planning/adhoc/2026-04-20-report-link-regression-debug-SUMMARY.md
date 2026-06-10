# 2026-04-20 报告链接点击回归排查总结

## 结论

- 报告文件生成与静态文件服务未失效。
- 根因在前端 `ChatMessage.vue` 的报告链接识别规则过旧，未覆盖新的消息文本格式。
- 当前消息格式如：`【报告文件】 运行状态诊断报告已生成： dcma_status_20260417_1628.html`
- 旧规则只识别以下格式：
  - `` `xxx.html` ``
  - 独占一行的 `xxx.html`
  - `报告文件：xxx.html`
- 因此新格式不会被渲染成链接，也不会出现在报告按钮区。

## 已确认事实

1. 报告文件真实存在
   - `agent_fronted/public/reports/` 中存在多个 `.html/.md` 报告文件
   - 例如：`dcma_status_20260420_1614.html`

2. 后端静态访问正常
   - `GET /reports/dcma_status_20260420_1614.html` 返回 `200`
   - `GET /reports/does_not_exist_20260420.html` 返回 `404`

3. 前端旧规则无法识别截图文本
   - 对样例 `【报告文件】 运行状态诊断报告已生成： dcma_status_20260417_1628.html`
   - 旧识别规则 `Backtick / Line / Labeled` 全部未命中

## 修复内容

- 新增 `agent_fronted/src/utils/reportLinks.js`
  - 统一实现报告链接提取与内联链接转换
  - 使用安全白名单文件名规则：仅允许 `[A-Za-z0-9._-]+.(html|md)`
  - 仅识别报告上下文，不开放任意 HTML 或任意路径

- 更新 `agent_fronted/src/components/ChatMessage.vue`
  - 正文中的报告文件名链接化与上方报告按钮区，统一共用 `extractReportLinks / linkifyReportMentions`
  - 覆盖新格式：
    - `【报告文件】 … 已生成：xxx.html`
    - `报告已保存至：xxx.html`
    - `HTML 报告已保存至：xxx.html`
    - 旧格式仍兼容

- 新增最小测试 `agent_fronted/src/utils/reportLinks.test.mjs`
  - 验证截图文本能提取出 `/reports/dcma_status_20260417_1628.html`
  - 验证能生成 `<a href="/reports/...">`

## 回归结果

- `node agent_fronted/src/utils/reportLinks.test.mjs`：通过
- `npx tsc --noEmit`：通过
- `npm run build`：通过
- 后端真实静态报告访问：通过

## 风险控制

- 未引入 `innerHTML` 宽松注入策略
- 未开放任意文件路径
- 仅把白名单文件名映射到 `/reports/filename`
- 实时消息与历史恢复共用同一组件、同一识别逻辑，避免再次分叉
