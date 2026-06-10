---
mode: quick
date: 2026-04-22
task: admin-upload-ui-transplant
base_branch: Code-refactoring-version
source_branch: AdminUpload
---

# 管理员上传界面迁移计划

## 目标

将本地 `AdminUpload` 分支中的 PDF 上传前端界面，以最小闭包迁移到稳定基线 `Code-refactoring-version`，避免整分支 merge/rebase。

## 范围

- 新增上传弹窗视图组件。
- 在全局导航新增上传入口。
- 补回稳定聊天页根路由，确保原主链路可打开。
- 按用户追加要求迁移语音认证弹窗与自动管理员调试逻辑。
- 不迁移 `AdminUpload` 中的后端上传接口、聊天/SSE 改动和无关配置漂移。

## 不迁移内容修正

- 不迁移 `AdminUpload` 中的后端上传接口。
- 不迁移聊天/SSE 改动、报告渲染改动、构建配置漂移和新增依赖。
- 语音认证只迁移前端弹窗和入口；后端识别接口保持为现有服务能力，若接口未实现则前端给出错误提示。

## 验证

- 前端构建通过。
- 前端 dev server 能启动。
- 上传入口可见，上传弹窗可打开。
- 基本选择 PDF、清除文件、预览占位、关闭弹窗不报错。
- 聊天页可打开，SSE/消息发送等主链路不因本次前端迁移改动而回退。
