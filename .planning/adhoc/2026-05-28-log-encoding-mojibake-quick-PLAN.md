---
mode: quick
date: 2026-05-28
task: log-encoding-mojibake
---

# 后端日志乱码修复计划

## 目标

修复本地后端日志中的中文乱码问题，区分运行时输出编码不一致和源码字符串已损坏两类根因。

## 范围

- 统一后端 Python stdout/stderr、文件日志和 Windows PowerShell 启动脚本的 UTF-8 默认行为。
- 恢复已确认损坏的后端中文日志、注释和用户可见字符串。
- 保持现有日志系统、HTTP API 和业务逻辑不变。
- 更新本地开发与部署文档中的 UTF-8 验证建议。

## 验证

- 新增日志编码回归测试，确认控制台捕获和 JSON 文件日志均保留中文可读性。
- 新增源码 mojibake 静态扫描测试，覆盖已发现的损坏字符串特征。
- 运行聚焦测试：`tests/test_logging_encoding.py` 与 `tests/test_server_runner.py`。
