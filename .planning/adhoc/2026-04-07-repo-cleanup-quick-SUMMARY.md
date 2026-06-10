# 仓库整理总结

## 已完成

- 新建 `scripts/`，收拢本地启动 / 测试脚本：
  - `run_local_dev.ps1`
  - `run_local_dev.py`
  - `run_frontend_dev.ps1`
  - `run_tests.ps1`
- 新建 `templates/`，收拢报告模板：
  - `html_template.html`
  - `md_template.md`
- 新建 `docs/`，收拢辅助分析文档：
  - `codebase_analysis_report.md`
- 更新 `tools/report_tools.py` 模板路径
- 更新 `.gitignore`
- 删除运行残留：
  - `.pytest_cache/`
  - `__pycache__/`
  - `*.log`
  - `run_state*.txt`
  - `agent_fronted/dist/`

## pytest 文件说明

- `pytest.ini`：测试配置文件，属于源码的一部分，必须保留。
- `.pytest_cache/`：pytest 本地缓存，可删除。
- `pytest-cache-files-*`：测试/沙箱残留目录，不是源码。

## 未完全完成项

- 根目录下 6 个 `pytest-cache-files-*` 目录尝试删除时遇到 ACL / Access denied。
- 已将其加入 `.gitignore`，避免继续干扰代码仓库；如果需要，我可以后续继续用更高权限方案单独处理。

## 验证

- `C:\miniconda3\envs\faultagent312\python.exe -c "from app import app; print('app import ok')"`
- `C:\miniconda3\envs\faultagent312\python.exe -m pytest -q -p no:cacheprovider`

结果：通过，`80 passed`
