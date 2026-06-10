---
mode: quick
date: 2026-04-26
task: file-upload-ui-final
---

# FileUpload 上传界面整理总结

## 完成内容

- `FileUpload.vue` 默认隐藏历史记录，上传区和识别结果区为 5:5 双栏布局。
- 左上角增加轻量历史记录展开按钮，展开后保留三栏布局，并可从历史面板标题栏收起。
- 历史按钮视觉收敛为小型边缘入口，避免遮挡上传和识别主体区域。
- 上传 PDF 后在上传面板内展示原始 PDF 内嵌预览。
- 保留登记、清除、识别结果、结果预览和浏览器打印导出逻辑。

## 验证

- `npm.cmd exec vite build` 通过。
- `npm.cmd run build` 未通过，原因是现有 `tsconfig.app.json` 中 `ignoreDeprecations: "6.0"` 被当前 `vue-tsc` 判定为无效配置值。
