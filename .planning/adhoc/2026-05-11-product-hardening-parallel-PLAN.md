---
mode: quick
date: 2026-05-11
task: product-hardening-parallel
---

# 产品化加固并行优化计划

## 背景

基于项目全身体检报告，优先处理高收益、可验证、低耦合的问题。当前项目已经具备聊天、SSE、工具调用、历史会话、管理员 PDF 上传、知识库归档、报告生成等闭环能力，但仍存在身份安全、报告安全、健康检查、历史加载体验和证据展示等产品化短板。

## 目标

- 服务端收口聊天身份来源，避免前端伪造 `user_identity=管理员` 影响 Agent prompt / workflow 路由。
- 加固报告文件名、保存路径和 HTML 报告内容。
- 增强健康检查，让报告目录、上传 PDF KB、管理员密码安全状态可观测。
- 做兼容式历史分页、搜索、加载更多，降低历史列表前端渲染压力。
- 增加轻量证据来源卡，让 assistant 回答默认暴露“依据与来源”。

## 范围

### 本轮纳入

- `fault_diagnosis/app.py`
- `fault_diagnosis/admin_auth.py`
- `fault_diagnosis/config.py`
- `fault_diagnosis/health.py`
- `fault_diagnosis/tools/report_tools.py`
- `agent_fronted/src/services/api.js`
- `agent_fronted/src/services/api.d.ts`
- `agent_fronted/src/composables/useChatStream.ts`
- `agent_fronted/src/views/CustomerService.vue`
- `agent_fronted/src/components/ChatSidebar.vue`
- `agent_fronted/src/components/ChatMessage.vue`
- 相关测试文件
- `.env.example`

### 本轮暂缓

- 聊天 POST 初始化 + 短 SSE 订阅 URL 改造。
- `ChatMessage.vue` / `FileUpload.vue` 大组件拆分。
- PDF/OCR/KB 完整持久任务队列。
- 历史消息全文搜索索引。

## 执行策略

- 先做安全小改，再做报告安全和健康检查。
- `app.py`、`api.js`、历史链路由总控串行整合，避免多个改动互相覆盖。
- 保留旧接口兼容：历史接口无分页参数时仍返回原来的 `thread_id[]`。
- 不展示模型私有推理链，只展示可审计的证据与来源。

## 验收标准

- 未登录用户伪造 `user_identity=管理员` 不生效。
- 管理员 cookie 登录后聊天上下文可派生为管理员。
- 管理员用户名+密码登录在本地和真实运行模式下均可使用（`ALLOW_DEFAULT_ADMIN_PASSWORD` 默认为 `True`）。
- 前端导航栏身份标识点击后打开管理员登录对话框（用户名+密码），不再仅限声纹认证。
- `.env.example` 中管理员凭据使用占位符，不暴露真实默认值。
- 报告路径穿越不写出报告目录。
- HTML 报告危险标签、事件属性和危险 URL 被清理。
- `/health/dependencies` 包含新增健康项且不泄露密码或 token。
- 历史分页、搜索、加载更多不破坏旧格式和历史恢复。
- 前端构建通过。
- 后端定向测试和全量脚本测试通过。
