# README / WORKLOG 同步总结

## 已完成

- `README.md` 已同步到当前仓库状态：
  - 增加“当前状态”小节
  - 测试基线更新为 `105 passed`
  - 补充 `run_backend.ps1` / `clean_garbage.ps1`
  - 增加历史恢复热修后的行为说明与剩余边界
- `WORKLOG.md` 已补录：
  - `2026-04-09` 根目录收拢与源码根迁移
  - `2026-04-10` 兼容层退休、垃圾站收拢、流式工具渲染热修、历史刷新热修

## 当前文档口径

- 后端唯一真实源码根：`fault_diagnosis/`
- 官方入口：`python -m fault_diagnosis.app`
- 当前验证基线：`105 passed` + 前端 build 通过 + 前后端探针 `200`
- 已知剩余风险：服务端旧历史的 user 文本退化问题尚未在存储层根治，但同浏览器刷新已有前端兼容修补
