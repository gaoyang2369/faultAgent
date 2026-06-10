---
mode: quick
date: 2026-05-11
task: product-hardening-parallel
---

# 产品化加固并行优化总结

## 总控预检

- 当前分支：`Code-refactoring-version`
- 起始 HEAD：`f0d4dbf`
- 起始状态：工作区干净，本地分支领先 `origin/Code-refactoring-version` 2 个提交。
- 保护项：已有 stash 未恢复，本轮未触碰：
  - `stash@{0}: codex-before-switch-to-Code-refactoring-version`
  - `stash@{1}: codex-before-gitee-pull-2026-05-05`

## 子任务执行

| 子任务 | 处理内容 | 状态 |
| --- | --- | --- |
| A 权限与身份安全 | 服务端派生聊天身份、身份回归测试 | 已完成 |
| C 报告安全 | 报告文件名净化、路径越界保护、HTML 主动内容清理、报告安全测试 | 已完成 |
| I 可观测性 | 健康检查新增报告目录、上传 PDF KB、管理员密码状态 | 已完成 |
| E 历史分页 | 兼容式分页对象、搜索、加载更多、空/错态 | 已完成保守版 |
| F 证据来源卡 | assistant 默认展示轻量“依据与来源”摘要 | 已完成轻量版 |
| B/G PDF 校正与状态 | 本轮未大改，复用已合入能力并通过回归验证 | 已验证 |
| D POST+SSE 改造 | 聊天主链路风险较高 | 暂缓 |
| H 大组件拆分 | 与本轮前端改动冲突风险较高 | 暂缓 |

## 完成内容

### 身份与管理员安全

- `/chat/stream` 不再信任前端传入的 `user_identity`。
- 聊天上下文身份改为从服务端 admin cookie 派生：
  - 已登录管理员：`管理员`
  - 未登录或伪造参数：`游客`
- `.env.example` 增加默认密码风险说明。
- `ALLOW_DEFAULT_ADMIN_PASSWORD` 默认值从 `IS_LOCAL_RUNTIME` 改为 `True`，使管理员用户名+密码登录在真实运行模式下同样可用。
- `.env.example` 中 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 改为占位符格式（`your_admin_username`/`your_admin_password`），避免暴露真实默认凭据；实际默认值 `DCMA`/`707707` 仅保留在 `config.py` 硬编码回退中。
- `.env` 新增 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD`，优先于硬编码默认值，方便运行时覆盖。
- 前端导航栏身份标识点击行为从仅打开声纹认证对话框改为打开管理员登录对话框（`AdminAuthDialog`，支持用户名+密码），声纹认证入口保留。

### 报告路径与 HTML 安全

- `report_filename` 统一净化为安全 basename。
- 支持兼容输入：
  - `/reports/name.md`
  - `reports/name.md`
  - `name.md`
  - `name`
- 写入报告前使用受控 reports 目录和 `commonpath` 校验，阻止路径穿越。
- HTML 报告清理：
  - 危险标签：`script` / `iframe` / `object` / `embed` 等。
  - 事件属性：`onclick` / `onmouseover` 等。
  - 危险 URL：`javascript:` / `data:` / `vbscript:`。
- 动态 HTML 文本做转义。

### 健康检查与可观测性

- `/health/dependencies` 新增：
  - `reports_directory`
  - `uploaded_pdf_kb`
  - `admin_password`
- 健康状态统一为：
  - `available`
  - `degraded`
  - `not_configured`
  - `failed`
- 上传 PDF KB 健康项区分：
  - FAISS 向量索引
  - lexical corpus 兜底
  - 向量索引开关
  - vector error 是否存在
- 健康检查不返回管理员密码、token、API key 或向量错误原文。

### 历史分页与搜索

- `/ai/history/{type}` 保留旧格式：
  - 无分页参数时仍返回 `thread_id[]`。
- 新增兼容分页参数：
  - `limit`
  - `cursor`
  - `q`
- 带分页参数时返回：
  - `items`
  - `has_more`
  - `next_cursor`
  - `limit`
  - `cursor`
  - `keyword`
- 前端历史侧边栏新增：
  - 搜索框
  - 加载更多
  - 空历史状态
  - 搜索无结果状态
  - 加载失败状态
- 搜索为保守实现：只匹配 thread id / 默认标题 / 本地缓存标题，不做消息全文索引。

### 证据来源展示

- assistant 消息默认显示轻量“依据与来源”卡片。
- 展示后端已有 `normalizedEvidences` 前 3 条：
  - 证据标题
  - 证据类型
  - 来源
  - 文件名或来源文件
  - 摘要
- 完整证据清单仍保留在折叠详情里。
- 不展示模型私有 chain-of-thought。

## 变更文件

### 后端

- `.env.example`
- `fault_diagnosis/admin_auth.py`
- `fault_diagnosis/app.py`
- `fault_diagnosis/config.py`
- `fault_diagnosis/health.py`
- `fault_diagnosis/tools/report_tools.py`

### 前端

- `agent_fronted/src/App.vue`
- `agent_fronted/src/components/AdminAuthDialog.vue`
- `agent_fronted/src/components/ChatMessage.vue`
- `agent_fronted/src/components/ChatSidebar.vue`
- `agent_fronted/src/composables/useChatStream.ts`
- `agent_fronted/src/services/api.d.ts`
- `agent_fronted/src/services/api.js`
- `agent_fronted/src/views/CustomerService.vue`

### 测试

- `tests/test_admin_auth.py`
- `tests/test_health.py`
- `tests/test_history_api.py`
- `tests/test_report_security.py`
- `tests/test_sse_stream.py`

## 验证结果

### 定向后端测试

命令：

```powershell
python -m pytest -q tests/test_admin_pdf_pipeline.py tests/test_kb_tools.py tests/test_admin_auth.py tests/test_health.py tests/test_report_tools.py tests/test_report_security.py tests/test_history_api.py tests/test_sse_stream.py
```

结果：

- `64 passed`
- 仅有既有依赖告警。

### 前端构建

命令：

```powershell
npm run build
```

结果：

- 构建通过。
- 仅有既有 Vite chunk size 警告。

### 全量脚本回归

命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1
```

结果：

- `312 passed`
- 仅有既有依赖告警。

### 静态检查

命令：

```powershell
git diff --check
```

结果：

- 通过。

## 暂缓原因

- 聊天 POST 初始化 + SSE 订阅改造会影响聊天主链路、停止生成、历史落库和 EventSource 连接方式，建议单独开一轮灰度兼容。
- 大组件拆分容易与当前前端功能改动冲突，建议在本轮稳定后再拆。
- 历史全文搜索需要索引或独立 metadata 表，当前只做轻量搜索，避免在 checkpoint 上做高成本扫描。
- 完整 PDF/OCR/KB 任务队列需要持久 job store，本轮不引入 Celery/RQ。

## 当前风险

- 历史分页仍基于 checkpoint 全量扫描后切页，主要减少响应体和前端渲染压力，尚未根治数据库扫描成本。
- offset cursor 在新会话插入时可能轻微漂移。
- HTML 清理是项目内轻量 sanitizer，不等同于完整安全库。
- 证据来源卡仅展示已有证据摘要，不保证每个关键结论都有逐条来源绑定。

## 后续建议

- 单独推进 POST 初始化 + SSE 短 URL 订阅。
- 为历史记录建立持久 metadata 索引。
- 为 PDF/OCR/KB 增加持久任务状态表。
- 拆分 `ChatMessage.vue` 和 `FileUpload.vue`。
- 继续增强 claim-level 证据绑定。
