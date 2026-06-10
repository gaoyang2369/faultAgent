---
mode: quick
date: 2026-04-10
owner: codex
status: completed
objective: 先恢复前后端运行状态，再定位并修复“刷新网页后历史聊天乱码或说话人混乱”的问题，并补充最小必要验证。
---

# Restart And History Refresh Hotfix 计划

## 当前前提
- 已从 `refactor/root-layout-phase1-import-cleanup` 冻结 checkpoint 并切出 `hotfix/restart-and-chat-history-refresh-bug`
- 前后端已经恢复到可联调状态，问题已在真实历史接口与前端恢复链路中复现
- 本轮修复保持最小侵入，不改接口路径、不改 SSE 主流程

## 本轮目标
- 建立可回退 checkpoint 和 hotfix 子分支
- 恢复后端与前端运行状态
- 稳定复现刷新后历史聊天乱码 / 说话人混乱问题
- 精准定位根因并做最小侵入修复
- 运行测试、构建与刷新恢复验证

## 实际执行结果
1. 已冻结 dirty worktree，生成 checkpoint `55d04cd`：`checkpoint/pre-history-refresh-hotfix-20260410`
2. 已创建工作分支 `hotfix/restart-and-chat-history-refresh-bug`
3. 已恢复前后端服务并完成健康检查，`/` 与前端首页均返回 `200`
4. 已确认两个独立问题：
   - 历史 API 返回 `human/ai` 角色别名，前端刷新后未统一归一化，导致说话人混乱
   - 服务端持久化链路中的 `HumanMessage.content` 在真实环境下存在退化，前端刷新后直接信任服务端历史会覆盖本地正确文本
5. 已实施热修：
   - 前端新增统一消息模型归一化与本地缓存回填
   - 后端历史 API 序列化统一角色别名并展平结构化 content
6. 已完成验证：
   - `powershell -ExecutionPolicy Bypass -File .\\scripts\\run_tests.ps1`
   - `node agent_fronted/src/utils/chatMessageModel.test.mjs`
   - `npm run build`
   - 前后端健康检查 `200`

## 风险控制
- 修复限定在历史消息归一化、缓存恢复和历史序列化层，不改聊天接口和流式协议
- 本轮已修复用户可见的刷新异常，但服务端持久化链路仍可能产出退化的 user 文本；当前通过同浏览器本地缓存优先修补
- 若后续需要彻底根治 user 文本退化，需单独深挖 LangGraph/PostgreSQL checkpoint 持久化链路
