# 2026-05-06 PDF 上传产品化体验优化计划

## 背景
- 本轮跳过身份安全收口，聚焦 PDF 上传后的状态解释、归档可见性和 Agent 提问入口。
- 当前右侧识别结果可编辑但无保存 / 重新归档闭环，容易误导用户。
- 已有显式知识库归档接口和上传 PDF 知识库查询底座，需要补齐产品状态呈现。

## 目标
1. 将识别结果区域改为只读预览，避免无保存闭环的编辑误导。
2. 基于后端真实状态返回并展示 PDF 状态时间线。
3. 增加“用此 PDF 提问”入口，仅在已归档可查询时填入推荐问题。
4. 扩展现有 PDF 详情 / 列表响应，暴露 Agent 可查询判断所需字段。
5. 保持上传、归档、删除、聊天和 SSE 主链路不回退。

## 执行范围
- 前端：`agent_fronted/src/views/FileUpload.vue`、`agent_fronted/src/views/CustomerService.vue`、`agent_fronted/src/services/api.js`、`agent_fronted/src/services/api.d.ts`
- 后端：`fault_diagnosis/admin_pdf_registry.py`
- 验证：前端构建、相关 Python 测试或可运行的最小回归命令。
