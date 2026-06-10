---
mode: quick
date: 2026-06-09
task: backend-auth-admin-pdfs-router-split
---

# Auth 和 Admin PDF 路由拆分计划

## 目标

继续后端重构路线图 Phase 1，把 `/auth/*` 与 `/admin/pdfs*` 从 `fault_diagnosis/app.py` 迁移到 `fault_diagnosis/api/`，保持现有 HTTP 契约、状态码、响应字段和 cookie 行为不变。

## 范围

- 新增共享 API helper，承接 session scope cookie 绑定、管理员身份解析和管理员权限入口。
- 新增 `fault_diagnosis/api/auth.py`，迁移 `/auth/identity`、`/auth/admin/login`、`/auth/logout`。
- 新增 `fault_diagnosis/api/admin_pdfs.py`，迁移管理员 PDF 列表、上传、详情、文件读取、归档、校正和删除接口。
- 在 `fault_diagnosis/app.py` 注册新 router，并删除已迁移的内联 route 和未使用 import。
- 更新后端重构路线图与本轮 summary。

## 不做

- 不修改已有 HTTP endpoint 路径、方法、请求参数或响应外壳。
- 不改 SSE、聊天、历史、Todo、治理接口。
- 不升级依赖，不读取 `.env`。

## 验证

- 运行 Python 语法解析和 `compileall` 定向验证。
- 运行 `git diff --check`。
- 当前项目没有可用 Python 3.12 测试环境时，不强行跑完整 pytest，只记录阻塞。
