# 2026-04-17 MySQL USER 环境变量冲突修复总结

## 结论

- 问题已确认：项目把 MySQL 用户名绑定在通用环境变量 `USER` 上，容易与系统环境变量冲突。
- 已完成修复：代码统一改为优先读取 `MYSQL_USER`，并兼容项目 `.env` 中旧的 `USER=` 写法。
- 已同步更新文档与测试，避免 README / `.env.example` 继续误导用户。

## 本次修改

1. `fault_diagnosis/config.py`
   - 新增 `MYSQL_USER` 统一解析入口。
   - 解析顺序为：`MYSQL_USER` → 项目 `.env` 中旧 `USER` → 默认 `root`。
   - 明确不再把进程里的通用 `USER` 当作 MySQL 用户名。

2. 运行期调用点
   - `fault_diagnosis/db_pool.py`
   - `fault_diagnosis/health.py`
   - `fault_diagnosis/tools/sql_tools.py`
   - `fault_diagnosis/robot_arm/data_tools.py`
   - `fault_diagnosis/robot_arm/subagent/api_tool.py`
   - 全部改为使用 `config.MYSQL_USER`。

3. 文档与示例
   - `README.md`
   - `.env.example`
   - 改为使用 `MYSQL_USER=root`，并补充冲突说明。

4. 测试
   - `tests/conftest.py` 改为注入 `MYSQL_USER`
   - `tests/test_config.py` 新增回归测试：
     - `MYSQL_USER` 优先于系统 `USER`
     - 项目 `.env` 中旧 `USER` 仍可兼容

## 回归结果

- `C:\\miniconda3\\envs\\faultagent312\\python.exe -m pytest tests\\test_config.py -q`
  - 结果：`31 passed`
  - 附带 1 条 `PytestCacheWarning`，属于测试缓存目录写权限提示，不影响本次修复结论。

## 风险与兼容性

- 已有项目 `.env` 若仍写 `USER=root`，当前版本仍能跑，不会被立即破坏。
- 新配置和 README 已切换到 `MYSQL_USER`，后续推荐统一迁移过去。
- 本次未改动接口与业务语义，只修正配置解析入口与文档示例。
