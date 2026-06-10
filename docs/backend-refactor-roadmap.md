# 后端重构路线图

本文记录后端重构的当前状态与后续路线。当前目标不是推倒重写，而是在**不改变现有 HTTP API、SSE 契约、cookie/session 行为和前端依赖**的前提下，持续把包根收口、把装配层拆薄、把旧实现迁移到稳定分层，并为后续逻辑优化与测试边界迁移打基础。

## 当前重构原则

- 先保契约，再调结构。
- 先分层，再优化逻辑。
- 先迁移调用，再删除旧入口。
- 新代码优先依赖 `api/`、`services/`、`repositories/`、`agent_runtime/`、`workflows/` 等新分层。
- 顶层旧模块不再保留兼容导出；旧调用方必须迁移到新分层路径。

## 现状摘要

当前后端已完成以下基础收口：

- `api/` 已承接 HTTP 入口：聊天、历史、认证、管理员 PDF、治理、健康、TTS。
- `services/` 已承接用例级业务逻辑：聊天、历史、治理、管理员 PDF、TTS、健康。
- `repositories/` 已承接持久化访问：history index、治理台账、PDF registry 等。
- `agent_runtime/` 已承接 SSE / agent / stream control / 事件协议 / middleware。
- `auth/`、`common/`、`quality/`、`knowledge/`、`integrations/`、`infrastructure/` 等子包已建立。
- `fault_diagnosis/app.py` 已瘦身为最小入口，应用装配迁入 `fault_diagnosis/app_factory.py`。
- 顶层兼容 facade 已清除，包根只保留入口、配置和包初始化文件。

## 目标结构

建议将 `fault_diagnosis/` 收敛为以下职责边界：

```text
fault_diagnosis/
  app.py                 # 最小入口
  app_factory.py         # 应用装配层
  config.py              # 配置中心
  api/                   # HTTP 入口层
  services/              # 应用服务层
  repositories/          # 持久化层
  agent_runtime/         # agent / SSE / stream 协议层
  workflows/             # 领域场景层
  tools/                 # 工具层
  auth/                  # session / admin 身份
  common/                # 日志、编码、路径、通用工具
  quality/               # 质量门禁 / 安全动作
  knowledge/             # 知识库
  integrations/          # 外部集成
  infrastructure/        # DB / server / runtime 基础设施
```

### 边界定义

- `api/`：只做 FastAPI 参数解析、权限入口、HTTP 响应包装。
- `services/`：只做用例级业务编排，不直接依赖 HTTP 语义。
- `repositories/`：只做持久化读写，不承担业务流程。
- `agent_runtime/`：只做 agent 执行、SSE 协议、事件适配、流控制。
- `workflows/`：只做领域场景编排，如故障诊断、状态巡检、手册问答、报告生成等。
- `auth/`：只做 session、管理员身份、权限 scope。
- `common/`：只做通用编码、日志、工具函数。
- `quality/`：只做质量门禁、安全动作、治理辅助。
- `knowledge/`：只做知识库访问与索引逻辑。
- `integrations/`：只做外部 OCR、TTS 等系统接入。
- `infrastructure/`：只做数据库、服务启动、运行时基础设施。

## Phase 0：契约冻结

状态：已完成。

产出：

- [`docs/backend-api-contract.md`](./backend-api-contract.md)
- [`docs/sse-event-contract.md`](./sse-event-contract.md)
- 本路线图

验收：

- 能用文档反向检查路由、请求参数、响应外壳、SSE 事件字段。
- 后续重构不得在未更新契约或前端适配的情况下破坏现有行为。

## Phase 1：拆分 `app.py` 路由与入口

目标：让 `app.py` 只保留应用入口，不再承担复杂装配。

当前进度：

- 已将路由按职责拆到 `fault_diagnosis/api/`。
- `fault_diagnosis.app:app` 仍保持兼容。
- 当前 `app.py` 已进一步变薄，主装配逻辑迁入 `fault_diagnosis/app_factory.py`。

验收：

- 所有现有后端 API 路径、方法和状态码保持不变。
- `app.py` 不再包含路由注册、静态挂载、生命周期、模型初始化等重逻辑。

## Phase 2：提取应用服务层

目标：将路由中的业务逻辑抽离到服务层，形成稳定的用例边界。

当前进度：

- `ChatService`、`HistoryService`、`GovernanceService`、`AdminPdfService`、`TtsService`、`HealthService` 已形成。
- 路由层主要保留 HTTP 入参解析、权限入口和响应包装。

验收：

- router 文件中不再出现大段文件写入、checkpoint 遍历、SSE 拼装逻辑。
- service 可直接单测，不依赖 patch FastAPI route 函数。

## Phase 3：拆分 SSE 与事件模型

目标：将聊天流式输出的协议适配从业务执行中剥离出来。

建议组件：

- `agent_runtime/event_contracts.py`：定义内部事件模型。
- `agent_runtime/sse_adapter.py`：将内部事件编码为 SSE。
- `agent_runtime/legacy_react_engine.py`：封装 legacy ReAct 路径。
- `agent_runtime/workflow_engine.py`：封装 workflow 主链路。

验收：

- Workflow 和 legacy ReAct 最终都输出统一内部事件。
- SSE 层只做事件转换，不承载业务决策。

## Phase 4：确立 workflow 主链路

目标：让结构化 `workflows/` 成为主链路，legacy 仅作为回退。

建议：

1. 明确业务场景路由：
   - 故障诊断
   - 状态巡检
   - 手册问答
   - 报告生成
   - 澄清
   - 证据复核
2. 将证据门禁、治理快照、报告产物保存收敛到 workflow 结束阶段。
3. 保留 legacy ReAct 作为 fallback，而不是并行主入口。

验收：

- `ENABLE_WORKFLOW_V1=true` 时，主链路进入 workflow。
- complete 事件字段与 legacy 路径兼容。
- 各场景有专门测试覆盖。

## Phase 5：持久化与性能治理

目标：减少全量扫描和文件散写，让状态来源清晰可替换。

建议：

- 使用 history index 替代历史列表中的全量 checkpoint 扫描。
- 将 workflow artifact、治理台账、PDF registry 等统一成 repository 风格。
- 保留文件系统后端作为默认实现，必要时再引入其他存储后端。
- 为 `/health/dependencies` 增加对关键存储组件的检查。

当前进度：

- 已新增 `repositories/history_index.py`。
- 已新增文件型治理 repository 与 PDF repository。
- 已新增文件型 workflow artifact backend。
- 健康检查已加入对历史索引、workflow artifact store、治理 repository、PDF registry 的检查项。

验收：

- 历史分页不再依赖全量遍历所有 checkpoint。
- artifact、governance、PDF 记录的读写入口清晰可替换。

## Phase 5.5：模块归类与兼容层收口

目标：把仍散落在顶层的历史实现进一步归入清晰子包，同时保留必要兼容入口。

当前进度：

- `repositories/` 已承接治理、PDF、history index 等存储实现。
- `services/admin_pdf_pipeline.py` 已承接 PDF OCR、结构化解析、知识库归档、校正后重建、删除后重建等流程。
- 顶层老模块逐步退化为 facade。

验收：

- 新代码优先依赖 `repositories/*` 与 `services/*`，不再直接把实现写回顶层。
- 顶层旧入口仅保留兼容，不承载新逻辑。

## Phase 5.6：包根瘦身与企业分层收口

目标：把 `fault_diagnosis/` 根目录收敛为“应用入口 + 配置”，避免实现文件混排。

当前进度：

- 已新增 `auth/`、`common/`、`infrastructure/`、`knowledge/`、`integrations/`、`quality/` 子包。
- `streaming.py`、`stream_control.py`、`middleware.py`、`error_classification.py` 等实现已迁入 `agent_runtime/`。
- `health.py` 已迁入 `services/health_service.py`。
- `admin_pdf_registry.py` 的底层实现已迁入 repository 层。
- `paths.py` 已迁入 `common/paths.py`。
- `app_static.py` 已迁入 `infrastructure/app_static.py`。
- 顶层兼容入口已删除，代码和测试均引用新分层路径。

验收：

- `fault_diagnosis/` 顶层只保留 `app.py`、`app_factory.py`、`config.py` 和包必需的 `__init__.py`。
- HTTP API、SSE 事件、PDF record 字段、cookie/session 行为不变。
- 新代码不再直接把基础设施、认证、知识库、质量门禁和外部集成实现放在包根。

## Phase 5.7：应用装配继续拆分

目标：把 `app_factory.py` 从“应用工厂”继续拆薄为组合层。

建议新增模块：

- `fault_diagnosis/infrastructure/app_bootstrap.py`
- `fault_diagnosis/infrastructure/app_lifespan.py`
- `fault_diagnosis/api/app_routes.py`
- `fault_diagnosis/infrastructure/app_static.py`
- `fault_diagnosis/infrastructure/app_models.py`

建议职责划分：

- `app_bootstrap.py`：`load_dotenv`、UTF-8 标准输出、Windows 事件循环策略、运行前准备。
- `app_models.py`：主模型与摘要模型初始化。
- `app_lifespan.py`：session secret 检查、DEV 初始化、DB pool、checkpointer、agent 及资源释放。
- `app_routes.py`：统一 include routers。
- `app_static.py`：`/images`、`/reports` 和静态前端挂载。
- `app_factory.py`：只保留 `create_app()` 的装配动作。

验收：

- `app_factory.py` 不再承担过多初始化细节。
- 应用装配逻辑具备更清晰的可测试边界。

## Phase 5.8：顶层 facade 清理

目标：删除包根兼容层，强制内部代码和测试依赖新分层路径。

状态：已完成。

当前包根仅保留：

- `app.py`
- `app_factory.py`
- `config.py`
- `__init__.py`

已删除或迁移的旧入口：

- `admin_pdf_processing.py` -> `services/admin_pdf_pipeline.py`
- `admin_pdf_registry.py` -> `repositories/admin_pdf_registry_storage.py`
- `app_routes.py` -> `api/app_routes.py`
- `app_static.py` -> `infrastructure/app_static.py`
- `health.py` -> `services/health_service.py`
- `knowledge_base.py` -> `knowledge/base.py`
- `paths.py` -> `common/paths.py`
- `streaming.py` -> `agent_runtime/streaming.py`

后续新增代码不得恢复包根兼容入口。

## Phase 5.9：旧入口删除准备

目标：防止旧入口回流，避免重新形成双轨代码。

建议执行顺序：

1. 在导入边界测试中禁止新增包根 shim。
2. 新模块必须落入对应分层目录。
3. 若确需兼容外部调用，先在文档中登记兼容期限，再单独评审。

验收：

- 兼容层不再回流。
- 新代码只使用新分层路径。
- 顶层不再是“实现堆”，而是稳定的应用入口。

## Phase 6：测试边界迁移

目标：让测试保护契约和业务边界，而不是保护旧文件结构。

建议测试分层：

- API 契约测试：路径、方法、状态码、响应字段、cookie 行为。
- SSE 契约测试：事件名、payload 字段、complete 事件、取消行为、错误脱敏。
- Service 单测：`ChatService`、`HistoryService`、`GovernanceService`、`AdminPdfService`、`TtsService`、`HealthService`。
- Repository 单测：history index、governance repository、PDF repository。
- Workflow 场景测试：故障诊断、状态巡检、手册问答、报告生成、澄清、证据复核。

验收：

- 移动 route 或 service 文件不会导致大量测试因为 patch 路径变化而失败。
- 测试名称与断言描述的是行为，而不是旧文件名。

## 关键风险

- 不能只保 HTTP 路径，不保 SSE complete 事件字段；前端证据、报告门禁和 workflow 展示都依赖它。
- `/agent/chat` 不能变成第二套 agent 入口，应继续聚合统一聊天流。
- PDF record 字段较多，后端不应随意改名或删字段。
- 静态路径 `/reports`、`/images` 与报告/图表生成强耦合，拆模块时必须保留挂载顺序。
- `user_identity` 仍要由服务端身份派生，不能退回信任前端参数。
- 后续如果重新添加包根兼容入口，会再次制造“目录看似规范、实际双轨并存”的维护负担。

## 建议下一步

下一轮优先做两件事：

1. 把 `app_factory.py` 继续控制在纯装配层，避免重新堆业务逻辑。
2. 将领域规则和运行时状态继续从 workflow/service 中拆出，向 `domain/` 和 `runtime/` 收敛。
