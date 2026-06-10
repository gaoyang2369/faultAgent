---
mode: quick
date: 2026-06-10
task: backend-package-root-slimming-phase56
---

# 后端包根瘦身 Phase5.6 总结

## 已完成

- 新增 `auth/`、`common/`、`infrastructure/`、`knowledge/`、`integrations/`、`quality/` 子包，承接认证/session、通用工具、基础设施、知识库、外部 OCR 集成和质量门禁治理逻辑。
- 将 `streaming.py`、`stream_control.py`、`middleware.py`、`error_classification.py` 实现迁入 `agent_runtime/`，顶层同名文件保留为兼容 facade。
- 将 `health.py` 实现迁入 `services/health_service.py`，将 `admin_pdf_registry.py` 底层存储实现迁入 `repositories/admin_pdf_registry_storage.py`。
- 将 `evidence.py`、`governance.py`、`safe_actions.py` 迁入 `quality/`，避免创建和旧模块同名的包，同时保留旧导入入口。
- 调整内部高价值导入路径，让新代码优先依赖新分层。
- 将 `runtime/__init__.py` 改为惰性导出，避免 `quality.evidence` 与 runtime 聚合入口之间的循环导入。
- 更新 [backend-refactor-roadmap.md](../../docs/backend-refactor-roadmap.md)，新增 Phase5.6 记录。

## 验证

- 通过：`python -m compileall fault_diagnosis tests`。
- 通过：`git diff --check`。仅剩 Git 的 LF/CRLF 提示，无空白错误。
- 通过：轻量 import smoke，确认 `common`、`runtime.session_store`、`repositories.admin_pdf_registry_storage` 新入口与旧 facade 入口可用。
- 阻塞：`python -m pytest tests/test_repositories.py` 未执行，当前默认 Python 缺少 `pytest`。
- 阻塞：完整 import smoke 触发 `langchain_core` 依赖缺失；需在 `faultagent` Python 3.12 环境补跑。

## 当前结构口径

- `fault_diagnosis/app.py`、`config.py`、`paths.py` 是包根真实入口/配置。
- 包根其他小文件是兼容 facade，用于保护现有测试、脚本和外部导入。
- 后续新代码应直接引用 `auth/`、`common/`、`infrastructure/`、`knowledge/`、`integrations/`、`quality/`、`agent_runtime/`、`services/`、`repositories/`。
