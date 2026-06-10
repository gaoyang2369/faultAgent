---
mode: debug
date: 2026-04-16
owner: codex
status: completed
objective: 修复默认启动路径的 SESSION_SECRET 稳定性，明确 KB smoke/full 状态，并收口 reasoning-only 感知与空 assistant 历史，形成可继续开发的稳定基线。
---

# Stable Baseline Finish 计划

## 本轮范围
- 修复最常用默认本地启动路径下的 SESSION_SECRET 稳定加载问题，确保无需手工向进程注入固定 secret 也能通过重启恢复验证。
- 为 FAISS 索引补充 smoke / full / missing 的工程化状态表达，并补充默认 `faiss_db` 的说明与升级路径。
- 用最小侵入方式改善 reasoning-only 阶段的用户感知，并避免 history 渲染空 assistant 气泡。

## 执行策略
1. 审查启动脚本、dotenv 加载路径、SessionScopeManager 来源判定，先修默认启动路径稳定 secret。
2. 补充知识库元信息与健康检查输出，明确默认 `faiss_db` 只是 smoke index。
3. 在后端 history 返回层和前端流式状态层做最小收口，解决空 assistant 与“像卡住”的感知问题。
4. 用真实默认启动路径完成健康检查、SSE、工具、落库、历史恢复和重启恢复回归。

## 风险控制
- 继续保持进程环境变量优先于 `.env`。
- 生产环境仍要求显式配置 SESSION_SECRET，不允许退化为隐式临时密钥。
- 不把当前 2 chunk FAISS smoke index 误报为正式全量知识库。

## 实际执行结果
1. 根因确认有两层：`load_dotenv()` 之前依赖启动时 cwd，默认启动脚本也没有显式切换到项目根；同时开发态在 env / `.env` 缺失 `SESSION_SECRET` 时会直接退化成进程级临时密钥。
2. 已修复默认启动路径：`.env` 现在固定从项目根加载，`scripts/run_backend.py` 也会切回项目根；开发态若未显式配置 secret，会自动复用 `trash/run/session_secret.txt`，生产态仍要求显式配置。
3. 已补充 SESSION_SECRET 可观测性：启动日志与 `/health/real` 会输出 `configured / explicit_configured / source / stable_after_restart / fingerprint`，且保持脱敏。
4. 已给知识库补充 `kb_meta.json` 和状态表达，健康检查现在明确区分 `smoke` / `full` / `missing`；当前默认 `faiss_db` 已回填为 `2 chunk` smoke index。
5. 已在 SSE `start/ping` 事件与前端状态文案中显式表达 reasoning-only 阶段，避免用户把首 token 前等待误判成卡死。
6. 已在后端 history 返回层和前端缓存恢复层过滤空 assistant 占位，真实历史读取中 `empty_assistant_count=0`。
7. 已用默认启动方式（无手工注入 SESSION_SECRET）完成真实回归：健康检查、中英文问答、中文工具调用、assistant 历史恢复、重启后同 cookie/thread 连续追问均通过；前端 `npm run build` 也已通过。
