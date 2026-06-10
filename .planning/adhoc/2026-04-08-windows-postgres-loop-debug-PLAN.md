# Windows PostgreSQL 事件循环兼容修复计划

日期：2026-04-08

## 背景

- 真实环境启动后端时，MySQL 已能初始化。
- PostgreSQL 在 Windows 下通过 `psycopg_pool` 建立异步连接时失败。
- 错误信息明确指出当前使用的是 `ProactorEventLoop`，需要切换为兼容的 `SelectorEventLoop`。

## 目标

- 在不改变业务语义的前提下，修复 Windows 本机运行时的 PostgreSQL 异步连接兼容问题。
- 完成后重新验证后端启动链路。

## 执行项

1. 在应用入口增加 Windows 平台事件循环策略修正。
2. 保持 Linux / macOS 路径不变。
3. 重新启动后端，验证 MySQL、PostgreSQL 和 HTTP 服务是否完成初始化。
