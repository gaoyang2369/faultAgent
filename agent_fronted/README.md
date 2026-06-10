# Agent Frontend

这是一个基于 Vue 3 的前端项目，使用 Vite + TypeScript 构建，用于承载智能客服的 Web 端体验。页面整合了流式对话、消息富展示、任务面板以及语音播报等能力，配合后端 Agent 实现完整的客服工作流。

## 功能特性

- 智能聊天：实时流式回答、Markdown 渲染、代码高亮、图表/图片展示
- 会话侧边栏：咨询历史列表、快速新建会话
- 任务面板：展示、统计、自动收起待办（`TaskPanel` 组件 + `useTodosPanel` 逻辑）
- 语音支持：语音输入、回答播报
- PDF / 报告预览：内嵌抽屉 + 新窗口打开
- 暗色模式：基于 `@vueuse/core` 的 `useDark`

## 技术栈

- Vue 3 (Composition API)
- TypeScript
- Vite
- Pinia (状态管理)
- Vue Router
- Sass/CSS

## 目录结构（节选）

```
src/
├── assets/                 # 全局样式、静态资源（如 CustomerService.css）
├── components/
│   ├── ChatMessage.vue     # 单条消息展示
│   ├── ChatSidebar.vue     # 咨询记录侧边栏（封装自 CustomerService.vue）
│   └── TaskPanel.vue       # 任务面板展示
├── composables/
│   ├── useChatStream.ts    # 处理聊天流、消息列表、历史加载
│   └── useTodosPanel.ts    # 任务面板状态 & 工具结果解析
├── hooks/                  # 语音等自定义 Hook
├── services/               # API 调用
├── stores/                 # Pinia 状态
├── utils/                  # 工具方法（用户身份等）
└── views/
    └── CustomerService.vue # 客服主页面（拼装组件、调用 composable）
```

## 安装与启动

1. 克隆仓库后进入 `agent_fronted`
2. 执行 `npm install`
3. 开发模式：`npm run dev`
4. 生产构建：`npm run build`
5. 预览构建产物：`npm run preview`

## 开发说明

- 样式统一从 `src/assets/CustomerService.css` / `ChatMessage.css` 引入，保持组件拆分后仍共用同一套视觉规格
- 聊天逻辑：`useChatStream` 负责
  - `sendMessage`：封装流式 API、工具调用消息、错误兜底
  - `loadChat` / `loadChatHistory` / `startNewChat`：会话与历史管理
  - 组合式 API 返回的 `currentMessages` / `isStreaming` 直接驱动 UI
- 任务逻辑：`useTodosPanel`
  - 解析工具输出、同步后端线程 todos
  - 控制 TaskPanel 展开/收起、统计数字、状态标签
- UI 组件：
  - `CustomerService.vue` 仅负责布局拼装、语音/WS 等页面级逻辑
  - `ChatSidebar` & `TaskPanel` 保持原有行为，事件/props 设计与之前页面内实现一致
- 若需要新增样式，可优先在 `assets` CSS 中扩展，再由组件复用已有 class，避免样式漂移

## 贡献指南

1. Fork 仓库 & 新建分支
2. 运行 `npm run dev` 进行本地验证
3. 优先复用现有组件/composable，保持 API 稳定
4. 提交 PR 前请确保通过格式化 / Lint（如：`npm run lint`，若有配置）

## 许可证

本项目采用MIT许可证。详情请查看项目根目录下的LICENSE文件。