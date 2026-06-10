---
mode: quick
date: 2026-04-10
owner: codex
status: in_progress
objective: 删除根目录兼容包中的 __pycache__，并评估下一步如何退休根目录 shim 与兼容包。
---

# 兼容包 pycache 清理计划

## 本次范围
- 删除 `prompts/__pycache__/`
- 删除 `tools/__pycache__/`
- 删除 `robot_arm/__pycache__/`
- 不修改 `WORKLOG.md`、`DEPLOY.md`、`CLAUDE.md`、`.claude/`

## 同步评估内容
- 识别仓库内部仍依赖根目录 shim 的脚本与测试
- 识别 `fault_diagnosis/` 内部仍通过兼容层互相引用的模块
- 给出退休兼容层的分阶段推进路线

## 风险控制
- 只删除缓存目录，不删除源码
- 不在本次直接删 shim 文件或兼容包
- 不调整运行入口与测试入口，先给出迁移建议
