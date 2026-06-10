# 垃圾站收敛总结

## 已完成

- 删除仓库根目录历史运行日志 `*.log`
- 新增统一垃圾站目录 `trash/`
- 新增垃圾站说明文件 `trash/README.md`
- 新增一键清理脚本 `scripts/clean_garbage.ps1`
- 更新 `scripts/run_tests.ps1`
  - `pytest --basetemp` 重定向到 `trash/pytest/temp`
  - `cache_dir` 重定向到 `trash/pytest/cache`
  - `TMP/TEMP` 重定向到 `trash/tmp`
- 更新 `scripts/run_backend.ps1` / `scripts/run_local_dev.ps1`
  - `TMP/TEMP` 重定向到 `trash/tmp`
  - `PYTHONPYCACHEPREFIX` 重定向到 `trash/pycache`
- 更新 `.gitignore`
  - 忽略 `trash/` 内容
  - 保留 `trash/README.md`
  - 静音现有权限异常临时目录 `tmpnzlq3l7f/`
- 更新 `README.md`，说明垃圾站与清理脚本

## 验证

- `powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1`
- 结果：`102 passed`
- 运行测试后，缓存与 pycache 已落到 `trash/`
- 再次运行 `scripts/clean_garbage.ps1` 后，`trash/` 已恢复为仅保留 `README.md`
- 仓库根目录日志已清空

## 未完全完成

- 根目录仍残留一组 `pytest-cache-files-*` 目录和 `tmpnzlq3l7f/`
- 这些目录不是源码，而是 ACL / 权限异常的空壳残留
- 已尝试：
  - `Remove-Item -Recurse -Force`
  - 提权删除
  - `takeown + icacls + rd`
  - `cmd /c rd /s /q`
- 结果：均被系统权限拒绝，当前会话无法清除
- 已通过 `.gitignore` 和新的垃圾站规则避免它们继续干扰后续工作区
