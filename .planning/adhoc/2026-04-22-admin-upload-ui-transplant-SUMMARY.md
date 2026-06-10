---
mode: quick
date: 2026-04-22
task: admin-upload-ui-transplant
base_branch: Code-refactoring-version
source_branch: AdminUpload
---

# 管理员上传界面迁移总结

## 完成内容

- 从本地 `AdminUpload` 分支中提取了 PDF 上传相关前端界面，未做整分支 merge / rebase。
- 新增上传弹窗视图，并接回稳定基线现有聊天页主路由。
- 保留了上传入口、基础选择文件、预览、历史记录与关闭交互。
- 按追加要求迁移了前端语音认证弹窗与自动管理员调试逻辑入口。

## 明确未迁移

- 未迁移 `AdminUpload` 中的后端上传接口。
- 未迁移聊天主链路、SSE、报告链接、历史恢复等无关改动。
- 未迁移新增依赖、构建配置漂移和实验逻辑。

## 验证

- 前端构建通过。
- 开发服务器可启动。
- 上传入口与上传弹窗可见。
- 基本 PDF 选择 / 清除 / 预览交互不报错。
- 聊天页主链路保持可打开。
