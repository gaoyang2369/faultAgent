# 2026-04-26 Frontend Error Debug Plan

## Goal

修复前端当前可见报错，优先处理会影响编译、运行或 IDE 诊断的最小问题。

## Scope

- 检查 `agent_fronted` TypeScript/Vue 配置与当前组件改动。
- 复现前端类型检查和构建错误。
- 只修改确认有问题的前端源码，不回滚已有用户改动。

## Findings

- `vue-tsc` 对 `tsconfig.app.json` 和 `tsconfig.node.json` 均通过。
- Vite 构建失败点为 Node 无法 spawn `esbuild.exe`，属于当前执行环境权限问题。
- `VoiceAuthDialog.vue` 存在损坏的中文字符串，影响可读性和模板文本正确性。

## Tasks

- [x] 复现并区分类型检查错误与构建环境错误。
- [x] 修复语音身份识别弹窗中的损坏中文字符串。
- [x] 重新运行可用的类型检查验证。
