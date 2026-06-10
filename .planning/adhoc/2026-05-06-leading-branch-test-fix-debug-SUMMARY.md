# Leading Branch Test Fix Debug Summary

## 时间

2026-05-06

## 背景

用户要求确认本地最领先分支，并在没有测试问题时清理其他已合并本地分支。审计后确认 `integration-medicine-ocr-pdf-kb` 是本地最领先分支，其他本地分支均已合入。

## 分支处理

- 最领先本地分支：`integration-medicine-ocr-pdf-kb`
- 已删除的本地已合并分支：
  - `Code-refactoring-version`
  - `codex/test-remote-code-refactoring-version`
  - `integration-zzh-reasoning-improvement`
- 最新提交已推送到 Gitee `Code-refactoring-version` 分支：
  - `origin/Code-refactoring-version`: `7d2296f -> 4273309`

## 修复内容

- `fault_diagnosis/robot_arm/data_tools.py`
  - 恢复 `fig_inter` 的中文状态前缀返回。
  - 保留 `frontend=/images/...`、`report=...`、`figure=...` 等机器可解析字段。

- `fault_diagnosis/tools/kb_tools.py`
  - 上传 PDF corpus 仅在基础知识库和上传向量库均无命中时作为兜底，避免本地上传语料污染基础知识库测试证据数。

- `fault_diagnosis/tools/__init__.py`
  - 默认 `tools.tools` 恢复为 5 个懒加载共享工具。
  - `create_work_order` 改为在 `get_runtime_tools()` 中运行时追加，避免导入阶段工具数量和加载边界回归。

- `fault_diagnosis/streaming.py`
  - 恢复 Workflow complete SSE 的第四阶段结构化字段富化。
  - 修复 `server_error` payload，兼容 `type: "error"` 并补齐结构化 `error` 字段。

- `scripts/run_tests.ps1`
  - 每次测试使用唯一 pytest 临时目录。
  - 设置 `PYTHONUTF8=1`，避免 Windows 默认 GBK 读取 UTF-8 源码失败。

- `tests/conftest.py`
  - Windows 测试进程中忽略 `os.mkdir(..., mode=0o700)` 的限制性 mode，避免 pytest `tmp_path` 在当前沙箱下创建不可访问目录。

## 验证

- 聚焦测试：
  - `tests/test_data_tools.py`：9 passed
  - 图表工具事件测试：1 passed
  - 修复后的失败断言集合：5 passed
  - `tests/test_utils.py::TestUtilsModuleStructure`：7 passed
  - 报告/治理/安全门禁/工单组：18 passed

- 全量测试：
  - 命令：`powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1`
  - 结果：`298 passed, 3 warnings in 144.51s`

## 当前状态

- 本地当前分支：`integration-medicine-ocr-pdf-kb`
- 远端 `origin/Code-refactoring-version` 已指向最新提交 `4273309`
- 工作区在推送后保持干净
