---
mode: quick
date: 2026-04-22
task: chat-history-actions
base_branch: feature/admin-upload-ui-transplant
---

# 咨询记录管理交互计划

## 目标

为左侧咨询记录列表增加更多操作菜单，支持重命名和删除，并移除右上角重复的独立声纹认证按钮。

## 范围

- 审计并复用现有 `ChatSidebar.vue`、`useChatStream.ts`、`chatSessionCache.ts` 和 `chatAPI`。
- 本地缓存记录：重命名/删除直接更新 `localStorage` 中的 `fd_service_chat_cache_v1`。
- 后端记录：删除通过新增最小后端接口调用 LangGraph checkpointer 的 `adelete_thread`；重命名因后端当前没有标题字段，使用本地标题元数据覆盖展示。
- 删除当前会话时关闭活跃流并切换到下一条可用记录，否则回到新咨询状态。
- 移除独立“认证”按钮，保留管理员身份入口点击打开声纹认证弹窗。

## 验证

- 前端构建通过。
- 后端语法/导入可用。
- dev server 页面真实渲染，咨询记录更多按钮与管理员入口可见。
- 通过浏览器 localStorage 验证本地重命名/删除可持久化。
- 通过后端接口验证删除 endpoint 返回成功。

## 追加修复

- 人工测试发现：旧浏览器缓存里可能保留其他会话签发的 `thread.*`，后端会按当前 `fd_session` 拒绝删除这些未授权 thread。
- 修复策略：如果 DELETE 返回 404 且该记录来自本地缓存，则按“旧服务端 thread 的本地残留”处理，只清理当前浏览器缓存和列表，不伪造服务端删除成功。
