# 2026-04-17 MySQL USER 环境变量冲突修复计划

## 背景

- 代码中多处使用 `os.getenv("USER", "root")` 或 `os.getenv("USER")` 读取 MySQL 用户名。
- `USER` 是通用系统环境变量；在 `load_dotenv(override=False)` 条件下，进程里已有的系统 `USER` 会优先于项目 `.env` 生效。
- 这会导致 README 按 `USER=root` 配置时，运行期可能实际读到系统登录名而不是 MySQL 用户名。

## 目标

1. 统一 MySQL 用户名读取方式，避免误用系统 `USER`。
2. 保持向后兼容：项目 `.env` 中旧的 `USER=` 写法仍可用。
3. 同步修正文档与示例配置，避免新用户继续踩坑。

## 执行步骤

1. 在 `fault_diagnosis.config` 中新增 MySQL 用户名统一解析入口。
2. 替换连接池、健康检查、SQL 工具、机械臂子模块中的 `USER` 读取。
3. 同步更新 `README.md`、`.env.example`、`tests/conftest.py`。
4. 回归检查所有相关引用，确认没有遗漏。

## 风险控制

- 不读取或打印真实 `.env` 内容。
- 不改动现有接口与业务逻辑，只修正配置解析入口。
- 保留对项目 `.env` 中旧 `USER=` 的兼容，避免已有本地环境立即失效。
