# 故障诊断 Agent 权限设计说明

本文描述 `fault_diagnosis` 当前代码中的身份、权限、资源范围、workflow 授权、Tool Gateway、数据 ACL、工单接口、报告接口和 PDF 管理接口。

本文以代码现状为准，不把尚未实现的能力写成系统能力。文中的“权限码”均来自 `fault_diagnosis/security/permissions.py`；没有独立权限码的接口，会明确写出它当前实际复用的角色或权限条件。

## 一、系统能力边界

### 1.1 能力分类

当前项目中的能力分为五类：

| 类型 | 数量或入口 | 是否属于 Agent workflow |
| --- | --- | --- |
| 顶层 Agent workflow | 8 类 `TaskType` / `WorkflowPolicy` | 是 |
| workflow 内部节点 | SQL、知识检索、分析、工单判断、报告等 | 否，节点属于选中的 workflow |
| Agent Tool | `sql_db_query_checker`、`sql_db_query`、`query_knowledge_base`、`save_report` | 否，工具由 workflow 节点调用 |
| 业务 HTTP 能力 | 工单、报告读取、身份接口 | 否 |
| 管理 HTTP 能力 | `/admin/pdfs/*` 与后台解析/归档管线 | 否 |

系统调用边界如下：

```text
签名 Cookie / 服务端用户记录
  -> AuthContext
  -> 8 类 workflow 入口授权
  -> workflow policy 解析节点与 runtime_tools
  -> Tool Gateway 检查工具权限
  -> SQL ACL / RAG ACL 检查资源范围
  -> evidence / report / artifact 保存授权信息
  -> 输出 guardrail
  -> security audit
```

工单和 PDF 不进入顶层 workflow 路由：

- `workorder_decision` 是 8 类 workflow 中按 policy 开启或关闭的内部节点。它生成 `WorkOrderSuggestion`，不直接创建或派发工单。
- 工单落库由 `POST /api/workorders` 调用 `WorkOrderService` 完成。
- PDF 上传、解析、校正、归档和删除由 `/admin/pdfs/*` 与固定后台任务完成，不经过 Agent intent router、workflow policy 或 Agent runner。

### 1.2 代码事实来源

| 领域 | 代码位置 |
| --- | --- |
| 身份合同与授权结果 | `fault_diagnosis/security/contracts.py` |
| 角色权限与资源范围 | `fault_diagnosis/security/permissions.py` |
| workflow 入口授权 | `fault_diagnosis/security/policy_engine.py` |
| Tool Gateway | `fault_diagnosis/security/tool_gateway.py` |
| SQL ACL | `fault_diagnosis/security/sql_acl.py` |
| RAG ACL | `fault_diagnosis/security/rag_acl.py` |
| 安全审计 | `fault_diagnosis/security/audit.py` |
| 8 类 workflow policy | `fault_diagnosis/single_agent/workflow/policies.py` |
| workflow 类型 | `fault_diagnosis/single_agent/workflow/contracts.py` |
| 固定阶段编排 | `fault_diagnosis/single_agent/flow.py` |
| 工单节点 | `fault_diagnosis/single_agent/workorder_suggestions.py` |
| 工单 HTTP 能力 | `fault_diagnosis/api/workorders.py`、`services/workorder_service.py` |
| PDF 管理能力 | `fault_diagnosis/api/admin_pdfs.py`、`services/admin_pdf_service.py` |
| 报告读取 | `fault_diagnosis/api/reports.py` |

---

## 二、统一权限资源模型

项目采用 RBAC 与资源范围校验组合：

- RBAC：`role -> permissions` 决定基础能力。
- 资源范围：`asset_scope`、`table_scope`、`system_scope`、`location_scope`、`kb_scopes` 限制实际对象。
- 运行条件：workflow 节点、时间窗口、SQL 表、文档 metadata、报告访问文件和工单状态进一步收窄权限。
- Obligations：授权通过后仍注入 SQL 时间/设备过滤、限制行数、过滤知识片段、保存访问范围和写审计。

当前代码没有通用 `AuthorizationRequest` 类，也没有通用 condition 表达式解释器。下面的 `subject + action + resource + context + condition` 是对当前各层授权入参的统一文档表示；实际 condition 由确定性 Python 代码执行。

### 2.1 Subject：谁

Subject 对应 `AuthContext`：

```json
{
  "subject": {
    "user_id": "engineer_01",
    "display_name": "维修工程师01",
    "role": "engineer",
    "permissions": [
      "workflow.fault_diagnosis",
      "tool.sql.read",
      "tool.kb.search",
      "tool.report.write_draft",
      "tool.workorder.create"
    ],
    "asset_scope": ["J1号机", "pump_001"],
    "table_scope": ["real_data_01", "device_alarm", "device_metric"],
    "system_scope": ["DCMA_LINE_1"],
    "location_scope": ["一号车间"],
    "kb_scopes": ["public", "internal"],
    "session_id": "session_xxx",
    "auth_method": "password"
  }
}
```

字段定义：

| 字段 | 当前含义 | 可信来源 |
| --- | --- | --- |
| `user_id` | 用户唯一标识；未登录为 `guest` | 签名 cookie + 用户仓储 |
| `display_name` | 前端显示名称 | 用户仓储或管理员身份 |
| `role` | `guest`、`engineer`、`admin` | 服务端解析，不接受前端角色 |
| `permissions` | 角色的有效权限集合 | `ROLE_PERMISSIONS` 服务端生成 |
| `asset_scope` | 工程师负责设备范围 | `users.json` |
| `table_scope` | 工程师可查询表范围 | `users.json`；guest 固定为 `real_data_01` |
| `system_scope` | 负责系统范围 | `users.json` |
| `location_scope` | 负责位置范围 | `users.json` |
| `kb_scopes` | `public/internal/restricted` 可见级别 | 角色上限与用户配置的交集 |
| `session_id` | cookie 与会话绑定 | Session Scope |
| `auth_method` | 当前为 `password`，游客为空 | 签名 cookie |

当前系统没有 `tenant_id`、独立 `site_ids`、多角色集合、MFA 状态和动态部门属性。这些字段不属于当前授权合同。

### 2.2 Action：做什么

Action 分为两层：

1. 权限 action：当前代码真正参与 `has_permission()` 判断的权限码。
2. 操作 action：HTTP 路由或资源操作的细粒度名称；部分操作当前复用较粗的权限码。

例如：

```json
{
  "action": {
    "permission": "tool.workorder.create",
    "operation": "workorder.draft.create"
  }
}
```

`permission` 不能单独决定结果。SQL 工具调用需要同时满足 workflow、Tool Gateway 和 SQL ACL；工单创建还需要设备在工程师范围内。

### 2.3 Resource：对什么对象

当前系统实际参与授权的资源类型如下：

| Resource type | 标识或字段 | 授权属性 |
| --- | --- | --- |
| `workflow` | `task_type`、`workflow_id` | workflow 权限、请求设备 |
| `tool` | `tool_name` | 硬白名单、`runtime_tools`、tool 权限 |
| `sql_table` | 表名 | 全局表白名单、角色表范围 |
| `device` | `device_id`、`equipment_hint` | `asset_scope` |
| `timeseries` | 表、设备、时间列 | 表范围、设备范围、时间窗口、行数 |
| `kb_document` | document metadata | visibility、allowed_roles、allowed_asset_ids、allowed_systems |
| `report` | HTML 文件与 `.access.json` | 文件名、设备、授权表范围 |
| `work_order` | work_order_id、equipment_object | 创建人、设备范围、状态 |
| `pdf_record` | record_id、源文件 | 管理员身份 |
| `audit_record` | JSONL event | 当前只有写入，没有读取接口 |

统一表示示例：

```json
{
  "resource": {
    "type": "timeseries",
    "table": "real_data_01",
    "device_ids": ["J1号机"],
    "time_column": "create_time",
    "requested_time_range": "last_24h"
  }
}
```

### 2.4 Context：本次执行上下文

Context 由 HTTP 会话、intent router、workflow plan 和 runner 共同产生：

```json
{
  "context": {
    "session_id": "session_xxx",
    "trace_id": "trace_xxx",
    "task_type": "fault_diagnosis",
    "workflow_id": "wf_fault_diagnosis_v1",
    "requested_output": "answer",
    "risk_level": "read_only",
    "runtime_tools": [
      "sql_db_query_checker",
      "sql_db_query",
      "query_knowledge_base"
    ],
    "route_objects": {
      "device_ids": ["J1号机"],
      "alarm_codes": ["F01002"],
      "system": "DCMA_LINE_1"
    },
    "flags": {
      "need_sql": true,
      "need_knowledge": true,
      "need_workorder_decision": true
    }
  }
}
```

前端提交的 `user_identity`、role、permissions 或授权范围不参与权限判断。身份由服务端 cookie 重新解析。

### 2.5 Condition：实际判断条件

当前 condition 不是调用方传入的规则，而是以下代码条件：

```yaml
conditions:
  workflow_permission:
    check: required_workflow_permission in subject.permissions
    code: security/policy_engine.py

  engineer_asset_scope:
    check: every requested device matches subject.asset_scope
    code: security/policy_engine.py

  workflow_tool_scope:
    check: tool_name in context.runtime_tools
    code: security/tool_gateway.py

  sql_table_scope:
    check: table is globally allowed and allowed for current role
    code: security/sql_acl.py

  sql_time_and_row_scope:
    check: server injects role time predicate and LIMIT <= 50
    code: security/sql_acl.py

  rag_visibility:
    check: document metadata matches kb_scopes, role, asset and system
    code: security/rag_acl.py

  report_access:
    check: report access metadata is within current asset/table scope
    code: api/reports.py

  workorder_access:
    check: create/read/update permission, equipment scope and draft status
    code: services/workorder_service.py

  pdf_management:
    check: subject.is_admin == true
    code: api/_shared.py + api/admin_pdfs.py
```

### 2.6 完整授权请求示例

下面的例子对应工程师诊断自己负责的设备：

```json
{
  "subject": {
    "user_id": "engineer_01",
    "role": "engineer",
    "permissions": [
      "workflow.fault_diagnosis",
      "tool.sql.read",
      "tool.kb.search"
    ],
    "asset_scope": ["J1号机"],
    "table_scope": ["real_data_01", "device_alarm"],
    "system_scope": ["DCMA_LINE_1"],
    "kb_scopes": ["public", "internal"]
  },
  "action": {
    "permission": "workflow.fault_diagnosis",
    "operation": "workflow.run"
  },
  "resource": {
    "type": "device",
    "device_ids": ["J1号机"],
    "system": "DCMA_LINE_1"
  },
  "context": {
    "task_type": "fault_diagnosis",
    "workflow_id": "wf_fault_diagnosis_v1",
    "requested_output": "answer",
    "session_id": "session_xxx",
    "trace_id": "trace_xxx"
  },
  "condition": {
    "workflow_permission_present": true,
    "requested_assets_in_scope": true
  }
}
```

### 2.7 AuthorizationDecision

当前授权结果结构为：

```json
{
  "allowed": true,
  "mode": "allow",
  "reason": "身份与资源范围校验通过。",
  "denied_reason_code": "",
  "allowed_nodes": {
    "sql": true,
    "knowledge": true,
    "analysis": true,
    "workorder_decision": true,
    "report": false
  },
  "denied_nodes": {},
  "runtime_tools": [
    "sql_db_query_checker",
    "sql_db_query",
    "query_knowledge_base"
  ],
  "data_scope": {
    "asset_ids": ["J1号机"],
    "allowed_tables": ["real_data_01", "device_alarm"],
    "max_rows": 50,
    "max_time_window_days": 7,
    "allowed_kb_visibility": ["public", "internal"],
    "authorized_purpose": "diagnosis"
  },
  "kb_scope": {
    "allowed_visibility": ["public", "internal"]
  },
  "user_message": ""
}
```

`mode` 支持：

| mode | 当前语义 |
| --- | --- |
| `allow` | workflow 与资源入口检查通过 |
| `degrade` | guest 的诊断、RCA、健康评估或报告请求降级为最近一小时状态与公开知识 |
| `deny` | 缺少 workflow 权限或请求设备越权 |
| `clarify` | engineer 未配置设备或系统范围 |

---

## 三、当前完整权限码目录

本节只列 `permissions.py` 中已经存在的权限码。

### 3.1 Workflow 权限

| 权限码 | 对应 TaskType | guest | engineer | admin |
| --- | --- | ---: | ---: | ---: |
| `workflow.status_query` | `status_query` | 是 | 是 | 是 |
| `workflow.alarm_triage` | `alarm_triage` | 是 | 是 | 是 |
| `workflow.fault_diagnosis` | `fault_diagnosis` | 否 | 是 | 是 |
| `workflow.root_cause_analysis` | `root_cause_analysis` | 否 | 是 | 是 |
| `workflow.health_assessment` | `health_assessment` | 否 | 是 | 是 |
| `workflow.knowledge_qa` | `knowledge_qa` | 是 | 是 | 是 |
| `workflow.report_generation` | `report_generation` | 否 | 是 | 是 |
| `workflow.action_request` | `action_request` | 否 | 是 | 是 |

没有 `workflow.work_order_draft` 和 `workflow.rag_admin`。工单是节点与 HTTP 能力，PDF 是管理员接口。

### 3.2 Tool 权限

| 权限码 | 当前用途 | guest | engineer | admin | 实际检查位置 |
| --- | --- | ---: | ---: | ---: | --- |
| `tool.sql.read` | SQL checker 与 SQL query | 是 | 是 | 是 | Tool Gateway |
| `tool.kb.search` | 知识库检索 | 是 | 是 | 是 | Tool Gateway |
| `tool.report.write_draft` | `save_report` | 否 | 是 | 是 | Tool Gateway |
| `tool.workorder.create` | 工单创建、非管理员工单读写的粗粒度权限 | 否 | 是 | 是 | WorkOrderService；Gateway 也有映射 |
| `tool.workorder.dispatch` | 工单派发预留拒绝项 | 否 | 否 | 否 | Gateway 有映射，但当前无可调用工具、无角色持有 |

### 3.3 输出、数据、知识库与管理权限

| 类别 | 权限码 | guest | engineer | admin | 当前直接执行点 |
| --- | --- | ---: | ---: | ---: | --- |
| 输出 | `output.chart.generate` | 是 | 是 | 是 | 角色清单；图表必须来自已授权 SQL 结果 |
| 数据 | `data.runtime.read` | 是 | 是 | 是 | 角色清单；实际边界由 SQL ACL 执行 |
| 数据 | `data.runtime.read_all` | 否 | 否 | 是 | 角色清单；admin SQL 范围 |
| 数据 | `data.alarm.read` | 否 | 是 | 是 | 角色清单；实际边界由 SQL ACL 执行 |
| 数据 | `data.alarm.read_all` | 否 | 否 | 是 | 角色清单；admin SQL 范围 |
| 报告 | `data.report.read` | 否 | 是 | 是 | `GET /reports/{filename}` |
| 报告 | `data.report.read_all` | 否 | 否 | 是 | `GET /reports/{filename}` |
| 知识库 | `kb.public.read` | 是 | 是 | 是 | 构造 `kb_scopes`，RAG ACL 执行 |
| 知识库 | `kb.internal.read` | 否 | 是 | 是 | 构造 `kb_scopes`，RAG ACL 执行 |
| 知识库 | `kb.restricted.read` | 否 | 否 | 是 | 构造 `kb_scopes`，RAG ACL 执行 |
| 管理 | `admin.pdf.manage` | 否 | 否 | 是 | 角色清单；路由当前直接检查 admin 身份 |
| 审计 | `admin.audit.read` | 否 | 否 | 是 | 权限已定义，当前没有审计读取 API |

`data.runtime.*`、`data.alarm.*` 和 `output.chart.generate` 当前主要承担角色能力声明；SQL 执行路径没有逐项调用 `has_permission()`，真正的数据边界由 workflow 权限、`tool.sql.read` 和 SQL ACL 共同完成。这是当前实现事实，不等同于这些权限码已经形成独立数据网关。

### 3.4 细粒度操作与实际复用权限

以下 operation 是系统中的真实操作，但没有独立 RBAC 权限码：

| Operation | 当前实际授权条件 |
| --- | --- |
| `workorder.read` | admin，或持有 `tool.workorder.create` 且记录由本人创建/设备在范围内 |
| `workorder.update_draft` | 先满足 `workorder.read`，且目标状态只能是 `待派单/draft/pending` |
| `pdf.list/read/upload/correct/ingest/delete` | `require_admin_identity()` |
| `evidence.create/read` | workflow 内部执行，随 thread、artifact 和授权快照流转，没有独立权限码 |
| `report.create_draft` | `tool.report.write_draft` |
| `report.read_scoped` | `data.report.read` + access metadata 范围校验 |
| `report.read_all` | admin + `data.report.read_all` |
| `audit.write` | workflow 与 tool 拒绝路径内部写入，没有独立权限码 |

---

## 四、角色与资源范围

### 4.1 Guest

```yaml
role: guest
permissions:
  - workflow.knowledge_qa
  - workflow.status_query
  - workflow.alarm_triage
  - tool.sql.read
  - tool.kb.search
  - output.chart.generate
  - data.runtime.read
  - kb.public.read
resource_scope:
  allowed_tables: [real_data_01]
  max_rows: 50
  max_time_window_days: 1
  max_lookback_hours: 1
  allowed_kb_visibility: [public]
  authorized_purpose: status_or_visualization_only
```

执行约束：

- SQL 只能访问 `real_data_01`。
- SQL 强制注入 `create_time >= NOW() - INTERVAL 1 HOUR`。
- 报告和工单节点被关闭，`save_report` 从 `runtime_tools` 移除。
- `fault_diagnosis`、`root_cause_analysis`、`health_assessment`、`report_generation` 请求进入 `degrade`。
- `action_request` 直接拒绝。
- 知识库只保留 `public` 文档。

### 4.2 Engineer

```yaml
role: engineer
permissions:
  - workflow.knowledge_qa
  - workflow.status_query
  - workflow.alarm_triage
  - workflow.fault_diagnosis
  - workflow.root_cause_analysis
  - workflow.health_assessment
  - workflow.report_generation
  - workflow.action_request
  - tool.sql.read
  - tool.kb.search
  - tool.report.write_draft
  - tool.workorder.create
  - output.chart.generate
  - data.runtime.read
  - data.alarm.read
  - data.report.read
  - kb.public.read
  - kb.internal.read
resource_scope:
  asset_ids: AuthContext.asset_scope
  allowed_tables: AuthContext.table_scope
  systems: AuthContext.system_scope
  locations: AuthContext.location_scope
  max_rows: 50
  max_time_window_days: 7
  allowed_kb_visibility: AuthContext.kb_scopes
  authorized_purpose: diagnosis
```

执行约束：

- workflow 入口要求账号至少配置 `asset_scope` 或 `system_scope`。
- 请求中明确出现的设备必须匹配 `asset_scope`。
- SQL 表必须属于 `table_scope` 与全局白名单的交集。
- 设备型 SQL 表会注入全部 `asset_scope` 过滤条件。
- 非 `fault_records` 表无法安全注入设备条件时拒绝。
- 报告读取要求报告对象在设备范围内，且报告表范围是当前 `table_scope` 的子集。
- 工单可读取本人创建或设备在负责范围内的记录。

### 4.3 Admin

```yaml
role: admin
permissions:
  - all workflow permissions
  - tool.sql.read
  - tool.kb.search
  - tool.report.write_draft
  - tool.workorder.create
  - output.chart.generate
  - data.runtime.read
  - data.runtime.read_all
  - data.alarm.read
  - data.alarm.read_all
  - data.report.read
  - data.report.read_all
  - kb.public.read
  - kb.internal.read
  - kb.restricted.read
  - admin.pdf.manage
  - admin.audit.read
resource_scope:
  allowed_tables:
    - real_data_01
    - real_data_02
    - real_data_03
    - device_alarm
    - device_metric
    - device_fault_data
    - fault_records
  max_rows: 50
  max_time_window_days: 7
  allowed_kb_visibility: [public, internal, restricted]
  authorized_purpose: diagnosis
```

Admin 仍不具备 `tool.workorder.dispatch`，系统也没有设备控制、配置写入、告警关闭或工单派发工具。

### 4.4 身份建立与信任边界

| 接口/机制 | 当前行为 |
| --- | --- |
| `POST /auth/dev-login` | 仅开发开关启用时，签发与 session 绑定的 `fd_dev_auth`，模拟服务端预设的 guest/engineer/admin |
| `POST /auth/login` | 校验文件用户仓储中的 PBKDF2 密码，签发 `fd_user_auth` |
| `POST /auth/admin/login` | 校验管理员配置，签发 `fd_admin_auth` |
| `POST /auth/logout` | 清理用户与管理员 cookie |
| `GET /auth/identity` | 返回服务端解析后的 `AuthContext.identity_payload()`，含 role、permissions、asset_scope、allowed_tables、auth_method |
| 用户 cookie | HMAC 签名、绑定 session id、检查有效期 |
| 管理员 cookie | HMAC 签名、绑定 session id、用户名和有效期 |
| `users.json` | 保存 role 与资源范围；持久化 permissions 不被信任 |

---

## 五、8 类 Workflow 权限声明

### 5.1 公共声明结构

下面的声明是 `WorkflowPolicy`、`policy_engine.py` 和 runtime tool 解析规则的等价文档表示：

```yaml
workflow_definition:
  task_type: string
  workflow_id: string
  policy_id: string
  required_permission: string
  required_slots: []
  conditional_required_slots: {}
  enabled_nodes: {}
  runtime_tools: []
  evidence_requirements: {}
  output_schema: string
  on_missing_evidence: string
  guardrails: []
```

所有 workflow 共用以下领域能力白名单与禁止项：

```yaml
allowed_capabilities:
  - knowledge_base.search
  - asset_db.read
  - timeseries_db.read
  - alarm_db.read
  - event_log.read
  - workorder_db.read
  - report_store.write_draft
forbidden_capabilities:
  - device_control.write
  - config.write
  - workorder.dispatch
  - alarm.acknowledge
  - alarm.close
```

领域能力名用于 policy 表达；runner 真正可调用的物理工具只来自 `runtime_tools`。

### 5.2 Status Query

```yaml
status_query:
  task_type: status_query
  workflow_id: wf_status_query_v1
  policy_id: status_query_v1
  required_permission: workflow.status_query
  required_slots: [asset_context]
  conditional_required_slots:
    workorder_decision: [current_abnormal_status]
  enabled_nodes:
    sql: true
    knowledge: false
    analysis: true
    resolution_recommendation: false
    workorder_decision: conditional
    report: false
  runtime_tools:
    always: [sql_db_query_checker, sql_db_query]
  evidence_requirements:
    need_asset_identity: true
    need_current_status: true
    need_metric_timestamp: true
  output_schema: status_query_answer_v1
  on_missing_evidence: answer_available_status_and_mark_unknowns
  guardrails:
    - no_current_status_claim_without_runtime_data
    - show_data_freshness
    - no_workorder_dispatch_without_human_confirmation
```

### 5.3 Alarm Triage

```yaml
alarm_triage:
  task_type: alarm_triage
  workflow_id: wf_alarm_triage_v1
  policy_id: alarm_triage_v1
  required_permission: workflow.alarm_triage
  required_slots: [alarm_code_or_name]
  conditional_required_slots:
    check_current_fault_status: [device_id]
    workorder_decision: [device_id, current_alarm_status]
  enabled_nodes:
    sql: conditional
    knowledge: true
    analysis: true
    resolution_recommendation: true
    workorder_decision: conditional
    report: false
  runtime_tools:
    always: [query_knowledge_base]
    when_sql_enabled: [sql_db_query_checker, sql_db_query]
  evidence_requirements:
    need_alarm_definition: true
    need_alarm_severity: true
    need_recommended_actions: true
    need_current_alarm_status_if_device_provided: true
  output_schema: alarm_triage_answer_v1
  on_missing_evidence: answer_available_subgoals_and_mark_blocked_subgoals
  guardrails:
    - no_current_fault_claim_without_realtime_data
    - no_workorder_dispatch_without_human_confirmation
    - show_uncertainty
    - cite_evidence_ids
```

### 5.4 Fault Diagnosis

```yaml
fault_diagnosis:
  task_type: fault_diagnosis
  workflow_id: wf_fault_diagnosis_v1
  policy_id: fault_diagnosis_v1
  required_permission: workflow.fault_diagnosis
  required_slots: [asset_context, symptom_or_alarm]
  enabled_nodes:
    collect_asset_context: conditional
    sql: true
    knowledge: true
    analysis: true
    resolution_recommendation: true
    workorder_decision: true
    report: conditional
  runtime_tools:
    always: [sql_db_query_checker, sql_db_query, query_knowledge_base]
    when_report_enabled: [save_report]
  evidence_requirements:
    need_runtime_data: true
    need_supporting_evidence_for_each_cause: true
    need_missing_evidence_disclosure: true
  output_schema: fault_diagnosis_answer_v1
  on_missing_evidence: lower_confidence_and_disclose_missing_evidence
  guardrails:
    - do_not_confirm_root_cause_without_causal_evidence
    - separate_symptom_cause_and_root_cause
    - no_control_action_without_approval
```

### 5.5 Root Cause Analysis

```yaml
root_cause_analysis:
  task_type: root_cause_analysis
  workflow_id: wf_root_cause_analysis_v1
  policy_id: root_cause_analysis_v1
  required_permission: workflow.root_cause_analysis
  required_slots: [event_or_asset_context, time_window]
  conditional_required_slots:
    workorder_decision: [open_risk]
  enabled_nodes:
    sql: true
    knowledge: true
    analysis: true
    resolution_recommendation: true
    workorder_decision: conditional
    report: true
  runtime_tools:
    always: [sql_db_query_checker, sql_db_query, query_knowledge_base, save_report]
  evidence_requirements:
    need_event_timeline: true
    need_causal_support: true
    need_impact_scope: true
    need_unknowns: true
  output_schema: rca_answer_v1
  on_missing_evidence: avoid_root_cause_claim_and_mark_hypothesis
  guardrails:
    - do_not_turn_correlation_into_causality
    - root_cause_requires_temporal_and_mechanism_support
    - show_unknowns
```

### 5.6 Health Assessment

```yaml
health_assessment:
  task_type: health_assessment
  workflow_id: wf_health_assessment_v1
  policy_id: health_assessment_v1
  required_permission: workflow.health_assessment
  required_slots: [asset_or_group_context]
  conditional_required_slots:
    workorder_decision: [high_risk_or_degradation]
  enabled_nodes:
    sql: true
    knowledge: conditional
    analysis: true
    resolution_recommendation: true
    workorder_decision: conditional
    report: conditional
  runtime_tools:
    always: [sql_db_query_checker, sql_db_query]
    when_knowledge_enabled: [query_knowledge_base]
    when_report_enabled: [save_report]
  evidence_requirements:
    need_trend_window: true
    need_enough_data_points: true
    need_risk_rule_reference_if_scored: true
  output_schema: health_assessment_answer_v1
  on_missing_evidence: answer_observed_health_and_disclose_prediction_limits
  guardrails:
    - do_not_present_prediction_as_fact
    - show_assessment_window
    - show_data_sufficiency
```

### 5.7 Knowledge QA

```yaml
knowledge_qa:
  task_type: knowledge_qa
  workflow_id: wf_knowledge_qa_v1
  policy_id: knowledge_qa_v1
  required_permission: workflow.knowledge_qa
  required_slots: [topic_or_alarm_or_operation]
  conditional_required_slots:
    sql: [device_id_when_device_specific]
  enabled_nodes:
    sql: conditional
    knowledge: true
    analysis: true
    resolution_recommendation: conditional
    workorder_decision: false
    report: false
  runtime_tools:
    always: [query_knowledge_base]
    when_sql_enabled: [sql_db_query_checker, sql_db_query]
  evidence_requirements:
    need_knowledge_source: true
    need_applicability_scope: true
    need_safety_notes_for_risky_operation: true
  output_schema: knowledge_qa_answer_v1
  on_missing_evidence: answer_from_sources_and_mark_applicability_limits
  guardrails:
    - no_manual_claim_without_source
    - show_model_or_version_limits
    - no_workorder_dispatch_without_human_confirmation
```

### 5.8 Report Generation

```yaml
report_generation:
  task_type: report_generation
  workflow_id: wf_report_generation_v1
  policy_id: report_generation_v1
  required_permission: workflow.report_generation
  required_slots: [report_type_or_existing_evidence]
  enabled_nodes:
    sql: conditional
    knowledge: conditional
    analysis: true
    resolution_recommendation: conditional
    workorder_decision: false
    report: true
  runtime_tools:
    always: [save_report]
    when_sql_enabled: [sql_db_query_checker, sql_db_query]
    when_knowledge_enabled: [query_knowledge_base]
  evidence_requirements:
    need_existing_or_fresh_evidence_bundle: true
    need_report_time_window: true
    need_claim_evidence_links: true
  output_schema: report_answer_v1
  on_missing_evidence: generate_report_with_limitations
  guardrails:
    - report_only_uses_evidence_bundle
    - no_unverified_claims_in_report
    - show_report_window
```

### 5.9 Action Request

```yaml
action_request:
  task_type: action_request
  workflow_id: wf_action_request_v1
  policy_id: action_request_v1
  required_permission: workflow.action_request
  required_slots: [action_type]
  conditional_required_slots:
    execute_if_allowed: [permission, approval, safe_state]
  enabled_nodes:
    permission_check: true
    risk_check: true
    sql: true
    knowledge: true
    analysis: true
    resolution_recommendation: true
    workorder_decision: conditional
    report: false
    audit_log: true
  runtime_tools:
    always: [sql_db_query_checker, sql_db_query, query_knowledge_base]
  evidence_requirements:
    need_permission_result: true
    need_risk_result: true
    need_precondition_evidence: true
    need_human_confirmation_for_write: true
  output_schema: action_request_answer_v1
  on_missing_evidence: deny_or_request_confirmation
  guardrails:
    - no_device_control_execution
    - no_config_write_execution
    - no_workorder_dispatch_without_human_confirmation
    - audit_write_intent
```

### 5.10 Workflow 入口授权矩阵

| TaskType | guest | engineer | admin |
| --- | --- | --- | --- |
| `status_query` | allow；1h/real_data_01 | allow；设备与表范围 | allow |
| `alarm_triage` | allow；公开知识与受限状态数据 | allow；设备与表范围 | allow |
| `fault_diagnosis` | degrade | allow；设备与表范围 | allow |
| `root_cause_analysis` | degrade | allow；设备与表范围 | allow |
| `health_assessment` | degrade | allow；设备与表范围 | allow |
| `knowledge_qa` | allow；仅 public | allow；public/internal | allow；含 restricted |
| `report_generation` | degrade，不生成报告 | allow；设备与表范围 | allow |
| `action_request` | deny | allow，但写动作仍不执行 | allow，但写动作仍不执行 |

---

## 六、Workflow 节点权限说明

节点属于已选 workflow，不是新的 workflow：

| 节点 | 是否调用 Agent Tool | 权限与资源条件 |
| --- | --- | --- |
| `permission_check` | 否 | 仅 action_request 开启；判断动作类型与权限边界 |
| `risk_check` | 否 | 仅 action_request 开启；写动作要求人工确认语义，但没有执行器 |
| `sql` | 是 | `tool.sql.read` + runtime tool + SQL ACL |
| `knowledge` | 是 | `tool.kb.search` + runtime tool + RAG ACL |
| `analysis` | 否 | 只使用已经授权的 SQL/知识/evidence 产物 |
| `resolution_recommendation` | 否 | 输出处置建议，不执行设备动作 |
| `workorder_decision` | 否 | 生成是否需要工单的建议；guest 被授权层关闭 |
| `report` | 是 | `tool.report.write_draft` + `save_report` 在 runtime_tools 中 |
| `evidence_validation` | 否 | 检查 claim-evidence 引用与缺失证据披露 |
| `output_guardrail` | 否 | 检查最终输出与 evidence bundle 一致性 |
| `audit_log` | 否 | action_request 内部 artifact；与 security JSONL 审计是两类记录 |

### 6.1 工单节点与工单接口的边界

```text
8 类 workflow 中的 workorder_decision
  -> 生成 WorkOrderSuggestion
  -> 诊断 payload / artifact 返回工单草稿内容
  -> 用户或前端调用 POST /api/workorders
  -> WorkOrderService 再做身份、权限和设备范围校验
  -> 强制保存为“待派单”
```

`workorder_decision` 不执行以下动作：

- 不写工单仓储。
- 不指派人员。
- 不派发工单。
- 不进入处理中、待复核或已关闭状态。
- 不调用 `dispatch_workorder`。

---

## 七、Tool Gateway 当前注册声明

### 7.1 当前网关执行规则

`tool_gateway.py` 当前使用 `TOOL_PERMISSION_MAP`，不是外部 YAML 注册表。其等价声明为：

```yaml
tool_permission_map:
  sql_db_query_checker: tool.sql.read
  sql_db_query: tool.sql.read
  query_knowledge_base: tool.kb.search
  save_report: tool.report.write_draft
  create_workorder: tool.workorder.create
  dispatch_workorder: tool.workorder.dispatch
```

一次 Agent Tool 调用必须同时通过：

```text
tool_name in current allowed set
  （workflow 已激活时使用 runtime_tools，否则回退到 SingleAgentLimits.allowed_tools）
AND TOOL_PERMISSION_MAP contains tool_name
AND required permission in AuthContext.permissions
AND tool internal resource ACL succeeds
```

Gateway 本身只检查工具名、工具权限和 `runtime_tools`。`tool_input` 当前没有在 Gateway 内解析；SQL、RAG、报告和工单的资源条件由各自实现层检查。

### 7.2 `sql_db_query_checker`

```yaml
tool: sql_db_query_checker
entry_type: agent_tool
permission: tool.sql.read
resource_type: sql_query
workflow_requirement: sql node enabled
risk_level: read_only
input:
  query: string
gateway_conditions:
  - tool is in runtime_tools
  - subject has tool.sql.read
resource_conditions:
  - SQL ACL has already accepted and rewritten the query
  - checker result is passed through SQL ACL again
audit:
  denied_event: tool_denied
  trace_events: [tool_call, tool_result]
```

Checker 不被当作授权器。checker 返回的 SQL 必须再次经过 `apply_sql_acl()`。

### 7.3 `sql_db_query`

```yaml
tool: sql_db_query
entry_type: agent_tool
permission: tool.sql.read
resource_type: sql_table
workflow_requirement: sql node enabled
risk_level: read_only
input:
  query: string
gateway_conditions:
  - tool is in runtime_tools
  - subject has tool.sql.read
resource_conditions:
  - readonly single SELECT
  - exactly one recognized table
  - table is in global and role scope
  - device is in engineer scope
  - role time predicate is injected
  - LIMIT is clamped to 50
audit:
  denied_event: tool_denied
  trace_events: [tool_call, tool_result]
```

### 7.4 `query_knowledge_base`

```yaml
tool: query_knowledge_base
entry_type: agent_tool
permission: tool.kb.search
resource_type: kb_document
workflow_requirement: knowledge node enabled
risk_level: read_only
input:
  query: string
gateway_conditions:
  - tool is in runtime_tools
  - subject has tool.kb.search
resource_conditions:
  - document visibility is in subject.kb_scopes
  - allowed_roles is empty or contains subject.role
  - allowed_asset_ids is empty or intersects subject.asset_scope
  - allowed_systems is empty or intersects subject.system_scope
obligation:
  - filter documents before rendering them into model context
  - return no unauthorized document text
```

### 7.5 `save_report`

```yaml
tool: save_report
entry_type: agent_tool
permission: tool.report.write_draft
resource_type: report
workflow_requirement: report node enabled
risk_level: scoped_write
input_schema: SaveReportSchema
gateway_conditions:
  - tool is in runtime_tools
  - subject has tool.report.write_draft
resource_conditions:
  - report filename is sanitized
  - output path remains under REPORTS_DIR
obligations:
  - write HTML to private report directory
  - write sidecar .access.json when AuthContext exists
  - sidecar stores created_by, role, asset scope, table scope and diagnosis object
```

### 7.6 `create_workorder` 与 `dispatch_workorder`

```yaml
create_workorder:
  gateway_mapping_exists: true
  permission: tool.workorder.create
  registered_in_current_runner: false
  present_in_workflow_runtime_tools: false
  actual_execution_entry: POST /api/workorders

dispatch_workorder:
  gateway_mapping_exists: true
  permission: tool.workorder.dispatch
  registered_in_current_runner: false
  present_in_workflow_runtime_tools: false
  granted_roles: []
  actual_execution_entry: none
```

这两个映射不能被理解为当前 Agent 可以调用的工具。工单创建实际走 HTTP Service，派发没有实现执行入口。

### 7.7 不属于 Tool Gateway 的能力

以下能力不注册为 Agent Tool：

| 能力 | 实际入口 |
| --- | --- |
| PDF 上传、查看、校正、归档、删除 | `/admin/pdfs/*` |
| PDF 文本提取 | `BackgroundTasks -> process_admin_pdf_record` |
| PDF 知识库归档 | `BackgroundTasks -> ingest_admin_pdf_record` |
| 工单创建、读取、更新 | `/api/workorders*` |
| 报告读取 | `GET /reports/{filename}` |
| 诊断分析 | `analysis` 内部 stage |
| evidence 读取与校验 | artifact/evidence 内部模块 |

---

## 八、HTTP 能力权限声明

### 8.1 工单接口

```yaml
workorder_capabilities:
  create:
    route: POST /api/workorders
    permission: tool.workorder.create
    resource: equipment_object
    conditions:
      - engineer equipment must match asset_scope
      - thread_id is required
      - trace_id is required
    obligations:
      - force status to 待派单
      - persist created_by and created_by_role
      - persist authorized_asset_scope

  list:
    route: GET /api/workorders
    permission_for_non_admin: tool.workorder.create
    conditions:
      - admin sees all matching records
      - non-admin sees own records or records in asset_scope

  detail:
    route: GET /api/workorders/{work_order_id}
    permission_for_non_admin: tool.workorder.create
    conditions:
      - admin can read
      - creator can read
      - engineer can read when equipment matches asset_scope

  update_draft:
    route: POST /api/workorders/update
    permission_for_non_admin: tool.workorder.create
    conditions:
      - subject can read the record
      - requested status is one of [待派单, draft, pending]
    forbidden:
      - dispatched status
      - execution status
      - review status
      - closed status
```

当前 `update_work_order()` 只限制请求体中新提交的 `status`，没有检查记录更新前是否已经处于草稿状态。因此“不允许更新已派发/执行中记录的其他字段”尚未形成代码边界；本文不把它记为已实现能力。

### 8.2 报告接口

```yaml
report_read:
  route: GET /reports/{filename}
  required_any_permission:
    - data.report.read
    - data.report.read_all
  conditions:
    - filename matches safe HTML basename pattern
    - file resolves under REPORTS_DIR
    - admin can read all existing reports
    - non-admin report must have .access.json
    - diagnosis_object matches current asset_scope
    - report table scope is a subset of current table_scope
```

报告目录不作为公共静态目录挂载。

### 8.3 PDF 管理接口与固定流程

所有 PDF 路由首先执行 `require_admin_identity()`：

```yaml
pdf_admin_capabilities:
  list:
    route: GET /admin/pdfs
    condition: subject.role == admin

  upload:
    route: POST /admin/pdfs
    condition: subject.role == admin
    flow:
      - read uploaded file
      - save registry record and source file
      - if not duplicate, schedule process_admin_pdf_record

  detail:
    route: GET /admin/pdfs/{record_id}
    condition: subject.role == admin

  source_file:
    route: GET /admin/pdfs/{record_id}/file
    condition: subject.role == admin

  correction:
    route: PATCH /admin/pdfs/{record_id}/correction
    condition: subject.role == admin
    flow:
      - validate corrected_text
      - save correction
      - mark record for re-ingest

  ingest:
    route: POST /admin/pdfs/{record_id}/ingest
    condition: subject.role == admin
    flow:
      - inspect OCR state
      - schedule ingest_admin_pdf_record

  delete:
    route: DELETE /admin/pdfs/{record_id}
    condition: subject.role == admin
    flow:
      - delete registry record and related artifacts
```

`admin.pdf.manage` 存在于 admin 权限集合中，但这些路由当前检查的是 `is_admin`，没有逐路由调用 `has_permission("admin.pdf.manage")`。因此 PDF 管理是角色门禁，不是独立可委派权限。

---

## 九、数据与输出权限边界

### 9.1 SQL ACL

全局允许表：

```yaml
allowed_tables:
  - real_data_01
  - real_data_02
  - real_data_03
  - device_alarm
  - device_metric
  - device_fault_data
  - fault_records
```

拒绝条件：

- 非单条只读 `SELECT`。
- `WITH`、多语句、注释、`UNION`、`INTERSECT`、`EXCEPT`、`FOR UPDATE`、`INTO OUTFILE`。
- 未识别表、未知表、多表查询。
- guest 查询 `real_data_01` 之外的表。
- engineer 查询不在 `table_scope` 的表。
- engineer 请求设备不在 `asset_scope`。
- 设备型表缺少可安全注入的设备过滤能力。

强制注入：

| 角色 | 时间条件 | 设备条件 | 行数 |
| --- | --- | --- | --- |
| guest | `real_data_01.create_time >= NOW() - INTERVAL 1 HOUR` | 不额外注入账号设备范围 | `LIMIT <= 50` |
| engineer | 对有时间列的表注入最近 7 天 | 按表字段注入全部 `asset_scope` | `LIMIT <= 50` |
| admin | 对有时间列的表注入最近 7 天 | 不注入设备范围 | `LIMIT <= 50` |

设备字段映射：

```yaml
real_data_*: [device_name, inverter_name]
device_alarm: [device_name, device_id]
device_fault_data: [device_name, device_id]
device_metric: [device_id]
fault_records: no injected asset predicate
```

### 9.2 RAG ACL

知识文档检索后、进入模型上下文前执行过滤：

```yaml
document_acl:
  default_visibility:
    uploaded_pdf: internal
    other_source: public
  checks:
    - visibility in AuthContext.kb_scopes
    - allowed_roles is empty or contains AuthContext.role
    - admin bypasses asset and system checks after visibility/role check
    - allowed_asset_ids is empty or matches AuthContext.asset_scope
    - allowed_systems is empty or intersects AuthContext.system_scope
```

全部结果被过滤时，工具返回“未检索到当前权限范围内可用知识片段”，不会把被过滤文档内容交给模型。

### 9.3 Evidence 与 Artifact

workflow 授权结果会进入：

- security audit JSONL。
- `authorization` artifact。
- decision 的 `authorization`、`access_scope`、`denied_nodes`。
- complete payload 和保存后的诊断 artifact envelope。

SQL artifact 保存 `access_scope` 与 `filters_applied`。报告工具从 request-local `AuthContext` 写入访问 sidecar。证据、报告和最终答案只能基于已经通过 SQL/RAG 边界的内容。

### 9.4 输出 Guardrail

输出边界负责检查证据引用与缺失证据披露，不替代入口和资源授权。权限拒绝时，流程直接生成权限提示，不继续执行真实工具。

---

## 十、审计模型

`SecurityAuditLogger` 将记录追加到：

```text
${SECURITY_AUDIT_PATH}
或
${RUN_STATE_DIR}/security-audit.jsonl
```

当前安全审计事件：

| event_type | 触发位置 | 内容 |
| --- | --- | --- |
| `workflow_authorization` | workflow 入口授权后 | auth 摘要、decision、task_type、trace_id |
| `tool_denied` | Tool Gateway 拒绝后 | auth 摘要、decision、tool、stage、trace_id |

记录结构：

```json
{
  "timestamp": "2026-06-21T10:00:00+00:00",
  "event_type": "workflow_authorization",
  "trace_id": "trace_xxx",
  "auth": {
    "user_id": "engineer_01",
    "display_name": "维修工程师01",
    "role": "engineer",
    "asset_scope": ["J1号机"],
    "table_scope": ["real_data_01", "device_alarm"],
    "system_scope": ["DCMA_LINE_1"],
    "location_scope": [],
    "kb_scopes": ["public", "internal"],
    "auth_method": "password"
  },
  "decision": {
    "allowed": true,
    "mode": "allow",
    "runtime_tools": ["sql_db_query_checker", "sql_db_query", "query_knowledge_base"]
  },
  "resource": {
    "task_type": "fault_diagnosis"
  }
}
```

审计摘要不包含 cookie、密码和 token。`admin.audit.read` 已定义，但当前没有读取或导出安全审计的 HTTP API。

---

## 十一、当前未提供的能力

以下能力不属于当前系统权限面：

- 多租户 `tenant_id` 隔离。
- 独立 site 资源模型。
- MFA 与一次性审批凭证。
- 动态角色管理、权限管理和用户管理接口。
- 独立 `knowledge_admin`、`auditor`、`work_order_approver` 角色。
- 通用 condition DSL 或策略解释器。
- 工单派发、指派、开始、复核、关闭状态机。
- 设备启动、停止、重启和参数下发。
- 告警确认与关闭。
- 审计日志读取和导出 API。
- PDF 能力的细粒度委派；当前只有 admin 角色门禁。
- `data.runtime.read`、`data.alarm.read` 的独立数据权限网关；当前由角色清单与 SQL ACL 组合实现。

这些项目没有出现在前述 workflow、Tool Gateway 或接口声明中，不能从现有权限码推导为已授权能力。
