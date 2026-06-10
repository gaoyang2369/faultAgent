# 根目录收拢与兼容层退休说明

## 回退点

- 结构收拢基线分支：`backup/pre-structure-refactor-20260409`
- 结构收拢基线标签：`pre-refactor-stable-20260409`
- Phase 1 前 checkpoint：`pre-phase1-import-cleanup-20260410`
- 当前兼容层退休分支：`refactor/root-layout-phase1-import-cleanup`

## 已完成的三阶段退休

### Phase 1：内部 import 去 shim

- `fault_diagnosis/` 内部模块已全部改为包内导入或 `fault_diagnosis.*` 导入
- `fault_diagnosis/` 内部不再通过根目录 shim 或根兼容包互相引用
- 已新增 `tests/test_source_root_imports.py` 防止回退

### Phase 2：脚本、测试与开发入口迁移

- `scripts/run_backend.py`、`scripts/run_local_dev.py` 已改为直接导入 `fault_diagnosis.app`
- `rebuild_kb.py` 已改为直接导入 `fault_diagnosis.knowledge_base`
- 测试已全部切换到 `fault_diagnosis.*`
- `fault_diagnosis/app.py` 的模块入口已切换为 `fault_diagnosis.app:app`

### Phase 3：删除兼容层

- 已删除 11 个根目录 shim：
  - `app.py`
  - `config.py`
  - `db_pool.py`
  - `dev_mode.py`
  - `knowledge_base.py`
  - `logger.py`
  - `middleware.py`
  - `session_scope.py`
  - `session_store.py`
  - `streaming.py`
  - `utils.py`
- 已删除 3 个根目录兼容包：
  - `tools/`
  - `prompts/`
  - `robot_arm/`

## 当前结构

- 根目录现在只保留项目壳层职责：
  - 环境文件
  - 依赖清单
  - 启动 / 测试脚本
  - 文档
  - 前端工程
  - 知识库资源
- 后端真实源码只保留在 `fault_diagnosis/`

## 当前官方入口

- 开发启动：
  - `python -m fault_diagnosis.app`
- 生产启动：
  - `gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000`
- 知识库初始化：
  - `python -c "from fault_diagnosis.knowledge_base import init_knowledge_base; init_knowledge_base()"`
- 知识库重建：
  - `python rebuild_kb.py`

## 回退方式

- 回到 Phase 1 前 checkpoint：
  - `git checkout pre-phase1-import-cleanup-20260410`
- 回到结构收拢稳定基线：
  - `git checkout pre-refactor-stable-20260409`
- 回到兼容层仍保留的分支状态：
  - `git switch refactor/root-layout`

## 后续建议

1. 后续新增后端代码只写入 `fault_diagnosis/`
2. 若再做拆分，优先围绕 `fault_diagnosis/` 内部继续解耦，而不是重新引入根目录转发层
3. 若需要提供 CLI，可新增显式命令入口或 console script，不要重新恢复根目录 shim
