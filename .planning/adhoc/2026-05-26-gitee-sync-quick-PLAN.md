---
mode: quick
date: 2026-05-26
task: gitee-sync
---

# Gitee 分支同步计划

## 目标

拉取 `origin/Code-refactoring-version` 最新代码，保留当前工作区已有本地改动，整合后提交并推送回 Gitee 仓库。

## 范围

- 检查当前分支、远端地址和未提交改动。
- 暂存本地工作区改动，拉取远端最新提交。
- 恢复并整合本地改动，处理可能出现的冲突。
- 运行可承受范围内的验证命令。
- 提交并推送到 `origin/Code-refactoring-version`。

## 验证

- 确认 `git status` 无冲突路径。
- 优先运行前端构建和后端轻量测试；如环境或时间限制导致无法完整验证，记录原因。
