---
mode: quick
date: 2026-04-27
task: admin-password-auth-upload
---

# 管理员密码登录与服务端 PDF 上传链路补齐总结

## 背景

- 上传入口此前只在前端按“声纹识别成功 -> 管理员”显示。
- 声纹识别后端尚未完成，导致当前无法真实拿到管理员身份。
- `FileUpload.vue` 之前是本地 `localStorage` 版，没有后端权限校验，无法作为真实管理员上传链路使用。

## 完成内容

- 新增最小管理员认证接口：
  - `POST /auth/admin/login`
  - `GET /auth/identity`
  - `POST /auth/logout`
- 认证凭据改为后端配置项：
  - `ADMIN_USERNAME`
  - `ADMIN_PASSWORD`
  - 默认值分别为 `DCMA` / `707707`
- 新增 `fd_admin_auth` `httpOnly` cookie，并绑定现有 `fd_session` 会话，刷新后保持管理员态。
- 新增服务端 PDF 管理接口：
  - `GET /admin/pdfs`
  - `POST /admin/pdfs`
  - `GET /admin/pdfs/{record_id}/file`
  - `DELETE /admin/pdfs/{record_id}`
- 服务端 PDF 元数据与文件统一落到 `trash/run/admin_uploads/`，避免污染仓库和业务库。
- 前端新增管理员登录弹窗 `AdminAuthDialog.vue`，右上角身份入口改为密码登录入口。
- 前端 `FileUpload.vue` 从本地登记版切到后端真实登记版，历史记录、原文件预览、删除都依赖后端管理员权限。
- 保留声纹认证扩展位：当前主入口不再依赖声纹，但认证能力结构已可在后续复用同一套管理员 cookie 签发逻辑。

## 安全边界

- 密码未写入前端 bundle，仅在后端配置中使用。
- 认证失败只返回通用错误，不输出用户名/密码明文。
- 当前方案仍是内部测试用最小闭环：
  - 单一管理员账号
  - 未做限流 / 多账号 / 审计 / CSRF 专项设计
  - 后续正式化时应替换为哈希密码、权限模型和可扩展认证源

## 验证

- 后端 `compileall` 通过。
- 前端 `npm run build` 通过。
- 真实环境已启动：
  - 前端 `http://127.0.0.1:9005/`
  - 后端 `http://127.0.0.1:8000/docs`
- 实际联调结果已确认：
  - 未登录访问 `/admin/pdfs` 返回 `403`
  - 错误密码登录返回 `401`
  - `DCMA / 707707` 登录成功后 `/auth/identity` 返回管理员态
  - 实际上传 `pdfs/S120_故障手册.pdf` 成功，后端返回服务端记录
  - 刷新会话后管理员态与 PDF 历史仍可访问
  - 删除记录成功
  - 退出登录后管理员权限恢复不可用
  - 普通聊天 SSE 仍能完成 `start -> token -> complete`
- 详细验证结果保存在：
  - `runs/admin-auth-validation.json`

## 影响文件

- 后端：
  - `fault_diagnosis/config.py`
  - `fault_diagnosis/app.py`
  - `fault_diagnosis/admin_auth.py`
  - `fault_diagnosis/admin_pdf_registry.py`
- 前端：
  - `agent_fronted/src/App.vue`
  - `agent_fronted/src/components/AdminAuthDialog.vue`
  - `agent_fronted/src/stores/userIdentity.ts`
  - `agent_fronted/src/services/api.js`
  - `agent_fronted/src/services/api.d.ts`
  - `agent_fronted/src/views/FileUpload.vue`

## 后续

- 声纹认证接回时，优先复用当前 `/auth/identity` 与管理员 cookie 机制。
- 若 PDF 上传后续需要入知识库或业务表，应将 `trash/run/admin_uploads/` 替换为正式存储与任务流水。
