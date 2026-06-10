# 兼容包 pycache 清理总结

## 已完成

- 删除 `prompts/__pycache__/`
- 删除 `tools/__pycache__/`
- 删除 `robot_arm/__pycache__/`

## 当前兼容包状态

- 根目录兼容包目前只剩：
  - `prompts/__init__.py`
  - `tools/__init__.py`
  - `robot_arm/__init__.py`

## 退休兼容层评估结论

- 脚本仍依赖根入口：
  - `scripts/run_backend.py`
  - `scripts/run_local_dev.py`
- 测试仍大量依赖根目录 shim 与兼容包：
  - `app`
  - `config`
  - `knowledge_base`
  - `session_scope`
  - `streaming`
  - `utils`
  - `tools`
  - `prompts`
- `fault_diagnosis/` 内部仍有一批模块通过兼容层互相引用，尚未完全自洽

## 本次未做

- 未删除 shim 文件
- 未删除兼容包
- 未修改 `WORKLOG.md`、`DEPLOY.md`、`CLAUDE.md`、`.claude/`
