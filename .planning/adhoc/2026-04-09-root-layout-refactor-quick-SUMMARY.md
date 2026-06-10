# 根目录收拢与包结构整理总结

## 已完成

- 建立回退基线：
  - 分支 `backup/pre-structure-refactor-20260409`
  - 空提交 `checkpoint/pre-package-refactor-20260409`
  - 标签 `pre-refactor-stable-20260409`
- 创建重构分支：`refactor/root-layout`
- 新增 `fault_diagnosis/` 作为后端真实源码根
- 将根目录后端实现模块与 `tools/`、`prompts/`、`robot_arm/` 迁入 `fault_diagnosis/`
- 在根目录保留兼容层，维持旧入口和旧 import 路径
- 修正迁移后受 `__file__` 影响的路径解析：
  - 前端静态目录
  - 报告输出目录
  - 图表输出目录
  - 模板目录
  - 知识库 PDF / FAISS 路径
- 更新 `README.md`、`DEPLOY.md`、`docs/root-layout-refactor.md`

## 兼容策略

- 保留 `app.py`，继续支持 `python app.py` 与 `gunicorn ... app:app`
- 保留 `rebuild_kb.py`，继续支持原有知识库重建命令
- 保留根目录模块兼容层：`config.py`、`knowledge_base.py`、`streaming.py`、`utils.py` 等
- 保留根目录兼容包：`tools/`、`prompts/`、`robot_arm/`
- 包内导入改为“同包相对导入 + 跨包公共导入”混合模式，兼容新旧路径同时存在

## 验证

- 根入口启动探针：
  - `patch("app.LOCAL_DEV_MODE", True)` 后用 `TestClient` 访问 `/`
  - 结果：`200 OK`
- 导入兼容验证：
  - `import app`
  - `import tools.kb_tools`
  - `import prompts.dynamic_prompt`
  - `import robot_arm.data_tools`
  - `import fault_diagnosis.app`
  - 结果：通过
- 后端测试：
  - `C:\miniconda3\envs\faultagent312\python.exe -m pytest -q -p no:cacheprovider`
  - 结果：`102 passed`
- 前端构建：
  - `D:\nvm\nodejs\npm.cmd run build`
  - 结果：通过，Vite 产物生成成功

## 未完全解决

- 仓库根目录存在一个实验残留目录 `tmpnzlq3l7f/`，尝试删除时遇到 ACL / Access denied。
- 该目录不是本次重构结构的一部分；如需彻底清理，需要单独处理其权限。
