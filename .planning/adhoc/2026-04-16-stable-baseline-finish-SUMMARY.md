---
mode: debug
date: 2026-04-16
owner: codex
status: completed
objective: 修复默认启动路径的 SESSION_SECRET 稳定性，明确 KB smoke/full 状态，并收口 reasoning-only 感知与空 assistant 历史，形成可继续开发的稳定基线。
---

# Stable Baseline Finish 总结

## 已完成
- 默认本地启动不再依赖手工注入 `SESSION_SECRET`；开发环境会稳定复用 `trash/run/session_secret.txt`，重启后同 cookie / thread 可以继续恢复历史。
- `/health/real?deep=true` 与启动日志已能明确反映 `SESSION_SECRET` 来源和 KB `build_mode`。
- 当前 `faiss_db` 已明确标注为 `smoke`，并补充元信息文件，避免误判为正式全量知识库。
- reasoning-only 阶段在 SSE `start/ping` 与前端状态文本中可见，长首 token 等待不再完全像“卡住”。
- history 返回层与前端缓存恢复层都已规避空 assistant 占位；真实回归中 `empty_assistant_count=0`。
- 默认启动方式下的真实回归通过：中英文简单问答、中文工具调用、assistant 历史恢复、重启后 follow-up，以及前端 `npm run build`。

## 当前结论
- 现在可以开始下一轮开发。
- 当前版本已经具备稳定的默认开发启动路径、清晰的 KB 状态表达和可接受的首 token 前用户感知。
- 剩余风险不再阻塞下一轮业务开发：当前 `faiss_db` 仍只是 2 chunk smoke index，后续做知识库质量或覆盖率相关开发前，应按 README 路径升级为 full rebuild。
