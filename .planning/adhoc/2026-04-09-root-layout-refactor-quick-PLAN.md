---
mode: quick
date: 2026-04-09
owner: codex
status: in_progress
objective: 在不破坏现有入口、构建、测试和部署方式的前提下，先建立可回退基线，再对仓库做一次最小侵入式的根目录收敛和包结构整理。
---

# 根目录收拢与包结构整理计划

## 已完成前置保护
- 检查 Git 工作区、当前分支、未跟踪文件和未完成 merge/rebase 状态
- 建立基线分支 `backup/pre-structure-refactor-20260409`
- 创建 checkpoint 提交 `checkpoint/pre-package-refactor-20260409`
- 创建 tag `pre-refactor-stable-20260409`
- 从基线切出重构分支 `refactor/root-layout`

## 本次整理目标
- 让根目录更像项目壳层，减少散落的后端业务代码
- 为 Python 后端提供更明确的源码根和模块边界
- 保持 `app.py`、现有 HTTP 路由、前端路径、脚本入口和部署方式兼容
- 只做高确定性的小步迁移，不做无依据的大规模重命名

## 当前假设
- 前端目录 `agent_fronted/` 保持原位，避免额外影响 Vite 与静态资源引用
- 后端主入口仍以根目录 `app.py` 作为兼容入口保留
- 现有模块可优先收敛到单独源码目录，并通过转发层维持原始导入路径

## 执行步骤
1. 识别主入口、源码主目录、关键配置、构建/运行脚本、测试目录和部署文件
2. 标注运行时、构建时、测试时的强依赖路径与高风险迁移点
3. 设计最小侵入的目标结构，优先收敛后端源码并保留兼容入口
4. 分批迁移目录与模块，必要时增加 wrapper / shim / forwarding module
5. 逐步验证导入、启动、构建与测试
6. 更新 README / 文档 / 过渡说明，明确回退方式与兼容层状态

## 风险控制
- 不直接移动 `agent_fronted/`、`app.py`、`.env.example`、`requirements.txt`、`pytest.ini`
- 避免一次性修改所有 import，优先通过兼容入口降低波及面
- 若某路径迁移会影响外部调用，则保留旧路径转发层
- 任何目录调整后立即做最小验证，失败时优先修复兼容层而非继续搬迁
