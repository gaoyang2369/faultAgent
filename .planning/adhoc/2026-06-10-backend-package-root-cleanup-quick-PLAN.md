---
mode: quick
date: 2026-06-10
task: backend-package-root-cleanup
---

# 后端包根清理 quick 计划

## 目标

按当前企业分层目标清理 `fault_diagnosis/` 包根残留文件。除 `app.py`、`app_factory.py`、`config.py` 和 Python 包必需的 `__init__.py` 外，顶层不再保留实现文件或兼容 facade。

## 范围

- 将 `paths.py` 迁入 `fault_diagnosis/common/paths.py`。
- 将 `app_static.py` 迁入 `fault_diagnosis/infrastructure/app_static.py`。
- 删除顶层兼容入口：`admin_pdf_processing.py`、`admin_pdf_registry.py`、`app_routes.py`、`app_static.py`、`health.py`、`knowledge_base.py`、`paths.py`、`streaming.py`。
- 更新内部代码、测试和脚本导入路径，统一依赖新分层。
- 更新后端重构路线图与本轮 summary。

## 不做

- 不处理 `robot_arm/` 旧场景的结构拆分。
- 不改变 HTTP API 路径、SSE 事件字段、cookie/session 行为。
- 不升级依赖，不读取 `.env`。
- 不回滚当前工作区已有重构改动。

## 验证

- 运行 `python -m compileall fault_diagnosis tests`。
- 运行 `git diff --check`。
- 运行与包根清理相关的关键测试；若环境依赖不完整，记录阻塞。
