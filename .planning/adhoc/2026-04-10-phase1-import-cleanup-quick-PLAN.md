---
mode: quick
date: 2026-04-10
owner: codex
status: completed
objective: 先冻结垃圾站相关未提交改动，再完成兼容层退休的三阶段执行：内部 import 去 shim、脚本与测试迁移、最终删除根目录 shim 与兼容包，并通过回归验证确认项目仍可运行、可回退。
---

# 兼容层退休执行计划

## 当前前提
- 当前分支为 `refactor/root-layout`
- 工作区存在未提交的“垃圾站/清理规则”改动，需要先冻结为 checkpoint
- 用户在执行中将范围扩大为完整三阶段退休

## 本轮目标
- 冻结当前脏工作区，建立 checkpoint 提交和 tag
- 切出 `refactor/root-layout-phase1-import-cleanup`
- Phase 1：清理 `fault_diagnosis/` 内部模块对根目录 shim 和兼容包的 import 依赖
- Phase 2：迁移运行脚本、测试和开发文档到 `fault_diagnosis.*`
- Phase 3：删除根目录 shim 与兼容包，统一官方入口
- 验证后端入口、关键导入、pytest 和前端 build

## 执行步骤
1. 检查并记录当前 Git 状态
2. 提交当前垃圾站相关未提交改动，创建 checkpoint tag
3. 基于 checkpoint 创建 Phase 1 子分支
4. 逐个修改 `fault_diagnosis/prompts/`、`fault_diagnosis/tools/`、`fault_diagnosis/robot_arm/` 内部 import
5. 新增最小 smoke/structure test
6. 迁移 `scripts/`、`tests/`、`rebuild_kb.py` 与官方文档中的入口路径
7. 删除根目录 shim 与 `tools/` / `prompts/` / `robot_arm/` 兼容包
8. 运行导入验证、模块入口启动验证、`pytest`、前端 `npm run build`
9. 输出本轮结果与后续演进建议

## 风险控制
- 任何删除动作都必须在脚本和测试迁移完成之后执行
- 不顺手改无关业务逻辑
- 对无法安全迁移的引用先保留并记录原因，不做无依据的大规模替换
- 删除兼容层前必须完成导入、启动、测试和前端构建验证
