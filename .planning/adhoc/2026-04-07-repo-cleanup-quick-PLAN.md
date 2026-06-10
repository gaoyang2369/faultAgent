---
mode: quick
date: 2026-04-07
owner: codex
status: completed
objective: 在不破坏现有入口和依赖关系的前提下，清理 pytest 运行残留和本地启动残留，归拢脚本与模板文件，并同步 README / WORKLOG。
---

# 仓库整理计划

## 不动的根目录文件
- `app.py`
- `config.py`
- `middleware.py`
- `streaming.py`
- `knowledge_base.py`
- `rebuild_kb.py`
- `requirements.txt`
- `pytest.ini`
- `README.md`
- `WORKLOG.md`
- `AGENTS.md`
- `CLAUDE.md`
- `DEPLOY.md`
- `.env.example`

## 计划整理项
- 删除：`.pytest_cache/`、`pytest-cache-files-*`、`__pycache__/`、本次启动产生的 `*.log` / `run_state*.txt`
- 迁移到 `scripts/`：`run_local_dev.ps1`、`run_frontend_dev.ps1`、`run_tests.ps1`、`run_local_dev.py`
- 迁移到 `templates/`：`html_template.html`、`md_template.md`
- 迁移到 `docs/`：`codebase_analysis_report.md`

## 风险控制
- 只整理非核心入口和辅助资源
- 对移动的脚本和模板同步更新引用路径
- 运行最小导入验证，确保主入口和脚本路径仍成立
