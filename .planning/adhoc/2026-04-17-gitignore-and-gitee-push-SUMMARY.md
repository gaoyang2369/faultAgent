# Git 忽略与 Gitee 推送准备总结（2026-04-17）

## 已完成

- 审计了仓库现有 `.gitignore` 和 `agent_fronted/.gitignore`
- 补充了本轮本地产生但原先未忽略的目录与文件类型：
  - `agent_fronted/.npm-cache/`
  - `agent_fronted/.tmp-taskstate-check/`
  - `agent_fronted/.vite/`
  - `.pytest-local-temp/`
  - `*.sqlite3`
  - `*.db-shm`
  - `*.db-wal`
- 通过 `git check-ignore -v` 复核，确认 `.env`、`trash/run/*`、`faiss_db/*`、前端依赖和构建目录都已在忽略范围内

## 当前判断

- 仓库当前看起来仍处于“首次入库/大部分文件未跟踪”的状态，因此现在补好 `.gitignore` 再执行 `git add`，最安全
- `.gitignore` 只能阻止未跟踪文件进入暂存区；如果后续某些文件已经被你手工 `git add` 过，需要先 `git restore --staged <path>` 再重新检查

## 推送前建议

1. 先执行 `git status --short --ignored`
2. 再执行 `git add .`
3. 提交前执行 `git status` 和 `git diff --cached --stat`
4. 只在确认没有 `.env`、日志、缓存、数据库文件后再提交推送
