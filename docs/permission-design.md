# 故障诊断 Agent 权限设计与实现计划

本文用于指导后续代码改造。目标是在当前限制型单 Agent 架构上补齐身份权限管理，做到：

```text
入口控制：决定用户能不能进入某类 workflow
工具控制：决定用户能不能调用某个工具
数据/RAG 控制：决定用户最终能看哪些设备数据、哪些知识片段
输出控制：防止最终回答、报告、artifact 泄露未授权内容
```

当前项目不是开放式多 Agent，也不是 LangGraph 状态图，而是 `RestrictedSingleAgentRunner` 驱动的固定阶段流水线。权限改造应沿用现有分层，不要把所有逻辑塞进 `flow.py` 或 prompt。

---

## 一、现状判断

当前已有的能力：

- `auth/` 里有 session cookie、管理员 cookie、thread 归属校验。
- `/auth/admin/login` 能建立管理员态，`/auth/identity` 能返回当前身份。
- `ChatService.stream_chat` 已经忽略前端传入的伪造 `user_identity`，真实身份来自服务端 cookie。
- 管理员 PDF 接口已通过 `require_admin_identity()` 做 HTTP 级别保护。
- 单 Agent 已有工具硬白名单、`runtime_tools`、只读 SQL 校验和表白名单。
- `workflow/nodes.py` 有 `permission_check`，但它现在只服务 `action_request`，且默认不执行任何写操作。

当前缺口：

- 传入 Agent 的身份只是 `"游客"` / `"管理员"` 字符串，不足以表达设备范围、知识库范围、数据权限。
- workflow 入口没有真正的 RBAC/ABAC 授权。游客也可能进入实时状态查询类流程。
- 工具调用只检查工具名是否在白名单内，没有结合用户角色和资源范围。
- SQL 只做全局表白名单和只读校验，没有设备级、字段级、时间窗口级 ACL。
- RAG 检索没有按文档可见性、角色、设备/系统范围过滤。
- 证据链、报告、artifact 没有保存授权范围，也没有校验输出是否引用了未授权证据。

---

## 二、设计目标与非目标

### 目标

1. 服务端可信身份
   - 所有权限判断基于服务端签名 cookie 或服务端用户表。
   - 前端传来的 `user_identity` 只能作为兼容字段，不作为权限边界。

2. RBAC + ABAC
   - RBAC 决定角色基础能力。
   - ABAC 决定具体资源范围，例如设备、产线、系统、文档可见性、风险等级。

3. 分层权限边界
   - workflow 入口先做粗粒度允许、拒绝或降级。
   - 工具网关做工具级执行控制。
   - SQL/RAG 内部做最终数据级过滤。
   - 输出 guardrail 做最后一致性检查。

4. 可审计
   - 每次授权结果进入 trace、complete payload、artifact。
   - 拒绝、降级、工具拦截、数据过滤都要有结构化记录。

5. 小步落地
   - 第一版只做少量角色。
   - 不引入复杂组织架构、OAuth、审批流和真实设备控制。

### 非目标

- 第一版不实现直接设备控制、参数下发、告警关闭、工单派发。
- 第一版不做复杂多租户组织树。
- 第一版不把权限写进 prompt 后交给模型自行遵守。
- 第一版不重写整个 Agent 编排，只在现有固定阶段中插入授权节点。

---

## 三、第一版角色模型

建议先支持 3 个角色：

| 角色 | role | 能力范围 |
| --- | --- | --- |
| 游客 | `guest` | 只能问公开知识库、能力说明、普通故障码含义。不能查实时设备数据，不能生成包含设备数据的报告。 |
| 维修工程师 | `engineer` | 可查询自己负责设备/系统的数据，可做诊断、告警分诊、健康评估，可生成授权范围内报告和工单草稿。 |
| 管理员 | `admin` | 全局读权限、PDF/RAG 管理权限、审计查看权限。写操作仍不直接执行，只允许草稿或审批提示。 |

角色不要直接散落在业务代码里，应集中定义权限码。

推荐权限码：

```text
workflow.knowledge_qa
workflow.status_query
workflow.alarm_triage
workflow.fault_diagnosis
workflow.root_cause_analysis
workflow.health_assessment
workflow.report_generation
workflow.action_request

tool.sql.read
tool.kb.search
tool.report.write_draft
tool.workorder.write_draft

data.runtime.read
data.runtime.read_all
data.alarm.read
data.alarm.read_all
data.report.read
data.report.read_all

kb.public.read
kb.internal.read
kb.restricted.read

admin.pdf.manage
admin.audit.read
```

第一版角色权限建议：

| 权限 | guest | engineer | admin |
| --- | --- | --- | --- |
| `workflow.knowledge_qa` | 是 | 是 | 是 |
| `workflow.status_query` | 否 | 是，限授权设备 | 是 |
| `workflow.alarm_triage` | 降级为知识解释 | 是，限授权设备 | 是 |
| `workflow.fault_diagnosis` | 否 | 是，限授权设备 | 是 |
| `workflow.root_cause_analysis` | 否 | 是，限授权设备 | 是 |
| `workflow.health_assessment` | 否 | 是，限授权设备 | 是 |
| `workflow.report_generation` | 否 | 是，限授权设备 | 是 |
| `workflow.action_request` | 否 | 只生成草稿/审批提示 | 只生成草稿/审批提示 |
| `tool.sql.read` | 否 | 是，限授权设备 | 是 |
| `tool.kb.search` | 公开知识 | 公开 + 内部知识 | 全部知识 |
| `tool.report.write_draft` | 否 | 是，限授权设备 | 是 |
| `admin.pdf.manage` | 否 | 否 | 是 |

---

## 四、AuthContext 设计

新增目录：

```text
fault_diagnosis/security/
  __init__.py
  contracts.py
  permissions.py
  policy_engine.py
  tool_gateway.py
  sql_acl.py
  rag_acl.py
  audit.py
```

推荐核心模型放在 `security/contracts.py`：

```python
class AuthContext(BaseModel):
    user_id: str
    display_name: str = ""
    role: Literal["guest", "engineer", "admin"] = "guest"
    permissions: set[str] = Field(default_factory=set)
    asset_scope: list[str] = Field(default_factory=list)
    system_scope: list[str] = Field(default_factory=list)
    location_scope: list[str] = Field(default_factory=list)
    kb_scopes: list[str] = Field(default_factory=list)
    session_id: str = ""
    auth_method: str | None = None

    def is_admin(self) -> bool:
        return self.role == "admin"
```

授权结果模型：

```python
class AuthorizationDecision(BaseModel):
    allowed: bool
    mode: Literal["allow", "degrade", "deny", "clarify"] = "allow"
    reason: str = ""
    denied_reason_code: str = ""
    allowed_nodes: dict[str, bool] = Field(default_factory=dict)
    denied_nodes: dict[str, str] = Field(default_factory=dict)
    runtime_tools: list[str] = Field(default_factory=list)
    data_scope: dict[str, Any] = Field(default_factory=dict)
    kb_scope: dict[str, Any] = Field(default_factory=dict)
    user_message: str = ""
```

资源范围模型：

```python
class ResourceScope(BaseModel):
    asset_ids: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    max_rows: int = 50
    max_time_window_days: int = 7
    allowed_kb_visibility: list[str] = Field(default_factory=list)
```

---

## 五、身份来源与接口兼容

### 第一版登录方式

当前只有管理员登录。为了支持工程师角色，建议新增通用登录：

```text
POST /auth/login
POST /auth/logout
GET  /auth/identity
```

实现方式第一版可以用文件型用户仓储，不必上数据库：

```text
fault_diagnosis/repositories/user_repository.py
trash/run/users.json
```

`users.json` 示例：

```json
[
  {
    "user_id": "engineer_01",
    "username": "engineer_01",
    "password_hash": "...",
    "role": "engineer",
    "asset_scope": ["J1号机", "pump_001"],
    "system_scope": ["DCMA_LINE_1"],
    "display_name": "维修工程师01"
  }
]
```

安全要求：

- 不保存明文密码。
- cookie 必须服务端签名，不能让前端直接写 role。
- 继续兼容现有 `fd_admin_auth`，管理员老接口不破坏。
- `/auth/identity` 保持已有字段，同时增加新字段：

```json
{
  "user_id": "engineer_01",
  "user_role": "维修工程师",
  "role": "engineer",
  "is_admin": false,
  "permissions": ["workflow.status_query", "tool.sql.read"],
  "asset_scope": ["J1号机"],
  "system_scope": ["DCMA_LINE_1"],
  "auth_method": "password"
}
```

### 修改点

- `fault_diagnosis/auth/admin_auth.py`
  - 保留现有管理员 cookie。
  - 新增 `resolve_auth_context(request, session_id) -> AuthContext`。
  - 旧的 `resolve_identity_payload()` 可以基于 `AuthContext` 生成兼容响应。

- `fault_diagnosis/api/auth.py`
  - 新增普通登录接口。
  - `/auth/identity` 返回兼容字段 + 权限字段。

- `fault_diagnosis/services/chat_service.py`
  - `resolve_request_identity()` 后构造 `AuthContext`。
  - 调用 `token_stream_events` 时传 `auth_context`。
  - `user_identity` 参数只保留日志和兼容，不参与授权。

- `fault_diagnosis/agent_runtime/streaming.py`
  - `token_stream_events()` 增加 `auth_context` 可选参数。
  - 创建 `RestrictedSingleAgentRunner` 时传入 `auth_context`。

- `fault_diagnosis/single_agent/runner.py`
  - `__init__` 增加 `auth_context: AuthContext | None`。
  - `self.user_identity` 继续保留字符串兼容，但真实权限用 `self.auth_context`。

---

## 六、Workflow 入口控制

新增阶段：

```text
understand
  -> access_authorization
  -> select_workflow_policy
```

注意：当前 `understand_request()` 已经会生成 `SingleAgentDecision` 和 workflow policy。实现时可以先在 `understand` 后做授权，不必重写 intent。

### 授权策略

`security/policy_engine.py` 提供：

```python
def authorize_workflow(
    auth: AuthContext,
    decision: SingleAgentDecision,
) -> AuthorizationDecision:
    ...
```

基本规则：

- 没有 workflow 权限：`deny`。
- 可以回答部分内容但不能查实时数据：`degrade`。
- 缺少设备范围或请求设备超范围：`deny` 或 `clarify`。
- `action_request`：所有角色都禁止直接执行，只允许草稿/审批提示。工程师和管理员可以进入受限动作 workflow，游客拒绝。

### 降级示例

游客问：

```text
E102 是什么意思？现在 pump_001 还故障吗？
```

期望处理：

- `knowledge` 节点允许，仅查公开知识。
- `sql` 节点拒绝。
- 实时状态相关 subgoal 标记 blocked。
- 最终回答说明：可以解释故障码，但当前身份不能查询 pump_001 实时状态。

### 拒绝示例

游客问：

```text
J1号机当前运行状态怎么样？
```

期望处理：

- 不调用 SQL。
- 不泄露任何设备数据。
- 返回权限说明和登录提示。
- complete payload 中包含 `authorization.allowed=false`。

### 修改点

- `fault_diagnosis/single_agent/contracts.py`
  - `SingleAgentDecision` 增加：

```python
authorization: dict[str, Any] = Field(default_factory=dict)
access_scope: dict[str, Any] = Field(default_factory=dict)
denied_nodes: dict[str, str] = Field(default_factory=dict)
```

- `fault_diagnosis/single_agent/flow.py`
  - `understand` 后插入 `access_authorization` 阶段。
  - 授权结果写 trace artifact。
  - 若 `deny`，直接走权限拒绝 final answer，不进入 SQL/RAG/报告。
  - 若 `degrade`，根据 `AuthorizationDecision` 修改 `decision.enabled_nodes` 和 `decision.runtime_tools`。

- `fault_diagnosis/single_agent/workflow/todos.py`
  - 增加 `access_authorization` 阶段映射，仍归入 `理解与规划` 或 `收集证据` 分组。

- `fault_diagnosis/single_agent/contracts.py`
  - 新增阶段会增加轮次，检查 `SingleAgentLimits.max_rounds` 是否需要从 16 调整到 17 或 18。

---

## 七、工具网关

当前 `_start_tool_call()` 只检查工具名是否在本轮白名单内。需要增加用户权限判断。

新增 `security/tool_gateway.py`：

```python
class ToolGateway:
    async def invoke(
        self,
        *,
        tool_name: str,
        tool: Any,
        tool_input: Any,
        auth: AuthContext,
        decision: SingleAgentDecision,
        stage: str,
        invoke_raw: Callable[[Any, Any], Awaitable[Any]],
    ) -> ToolInvocationResult:
        ...
```

第一版也可以不做完整 class，先做函数：

```python
def authorize_tool_call(
    auth: AuthContext,
    tool_name: str,
    tool_input: Any,
    decision: SingleAgentDecision,
) -> AuthorizationDecision:
    ...
```

工具权限映射：

| 工具 | 权限 |
| --- | --- |
| `sql_db_query_checker` | `tool.sql.read` |
| `sql_db_query` | `tool.sql.read` |
| `query_knowledge_base` | `tool.kb.search` |
| `save_report` | `tool.report.write_draft` |

执行原则：

- 先检查工具名白名单。
- 再检查角色权限。
- 再检查工具输入是否符合数据范围。
- 拒绝时不要执行真实工具。
- 拒绝结果写入 trace、complete payload、artifact。

修改点：

- `fault_diagnosis/single_agent/runner.py`
  - `_start_tool_call()` 中加入 `authorize_tool_call()`。
  - 被拒绝时抛 `SingleAgentExecutionError` 不够友好，建议返回结构化 denied artifact，或在调用前由 stage 判断并跳过。

- `fault_diagnosis/single_agent/stages.py`
  - SQL、knowledge、report 阶段在调用工具前可以先检查 authorization，便于生成 skipped artifact。

---

## 八、SQL 数据级 ACL

这是最重要的安全边界。不能只依赖 prompt 或 workflow。

新增 `security/sql_acl.py`：

```python
def apply_sql_acl(
    sql_query: str,
    *,
    auth: AuthContext,
    request: DiagnosisRequest,
    decision: SingleAgentDecision,
) -> SqlAclResult:
    ...
```

推荐结果：

```python
class SqlAclResult(BaseModel):
    allowed: bool
    sql_query: str = ""
    reason: str = ""
    filters_applied: list[str] = Field(default_factory=list)
    blocked_reason_code: str = ""
```

第一版规则：

1. 管理员
   - 仍必须满足只读、表白名单、LIMIT。
   - 不强制设备过滤。

2. 工程师
   - 必须有 `asset_scope` 或 `system_scope`。
   - 查询设备必须在授权范围内。
   - 如果 SQL 没有设备条件，自动注入：

```sql
(device_name IN (...) OR inverter_name IN (...))
```

   - 禁止 `WHERE 1=1` 全局查询。
   - 限制最大 `LIMIT 50`。
   - 默认最大时间窗口 7 天。

3. 游客
   - 不允许执行运行数据 SQL。

4. 所有角色
   - 继续执行现有 `is_readonly_sql()` 和 `has_unknown_sql_table()`。
   - 禁止未知表、旧表 `real_data`、非只读语句。

实现建议：

- 第一版可以在当前受控 SQL 形态上做确定性注入。
- 后续如果 SQL 复杂度增加，改用 `sqlglot` 这类 SQL AST 工具做解析和重写。
- SQL ACL 必须在 `sql_db_query` 真正执行前运行。
- checker 返回修正 SQL 后，还要再次执行 SQL ACL。

修改点：

- `fault_diagnosis/single_agent/stages.py`
  - `stream_sql_step()` 在生成 SQL 后调用 `apply_sql_acl()`。
  - checker 后再次调用 `apply_sql_acl()`。
  - ACL 拒绝时生成 `SqlStepArtifact(success=False, error=...)`，不调用 SQL 工具。

- `fault_diagnosis/single_agent/sql_safety.py`
  - 保留表白名单、只读校验、fallback 查询。
  - 不建议把用户权限逻辑放进这里，可由 `security/sql_acl.py` 引用现有 helper。

---

## 九、RAG / 知识库 ACL

知识库权限也必须在检索结果进入模型上下文前执行。

### 文档 metadata

基础 PDF 和上传 PDF chunk 需要带 metadata：

```json
{
  "visibility": "public",
  "allowed_roles": ["guest", "engineer", "admin"],
  "allowed_systems": [],
  "allowed_asset_ids": [],
  "sensitivity": "normal",
  "source_type": "knowledge_base"
}
```

建议默认值：

| 来源 | 默认 visibility |
| --- | --- |
| 基础故障码/公开手册 | `public` |
| 管理员上传 PDF | `internal` |
| 敏感维修方案、内部复盘 | `restricted` |

角色可见性：

| role | 可读 visibility |
| --- | --- |
| guest | `public` |
| engineer | `public`, `internal` |
| admin | `public`, `internal`, `restricted` |

### ACL 过滤

新增 `security/rag_acl.py`：

```python
def filter_kb_documents(
    docs: list[Any],
    *,
    auth: AuthContext,
    decision: SingleAgentDecision,
) -> list[Any]:
    ...
```

过滤规则：

- 文档 `visibility` 不在角色可见范围内，过滤。
- 文档指定 `allowed_roles` 且当前 role 不在其中，过滤。
- 文档指定 `allowed_asset_ids`，必须和用户 `asset_scope` 有交集。
- 文档指定 `allowed_systems`，必须和用户 `system_scope` 有交集。
- 过滤后为空时返回“未检索到当前权限范围内可用知识片段”，不要返回原始未授权片段。

修改点：

- `fault_diagnosis/knowledge/uploaded_pdf_kb.py`
  - 写入 corpus 和 vector chunk 时补充 metadata。
  - 上传 PDF 默认 `visibility=internal`。
  - 管理端后续可扩展 visibility 设置。

- `fault_diagnosis/tools/kb_tools.py`
  - `query_knowledge_base()` 增加可选 `auth_context` 或通过工具网关注入。
  - 在格式化输出前调用 `filter_kb_documents()`。
  - 如果 LangChain retriever 支持 metadata filter，可以先 filter 检索；否则必须 post-filter。

第一版为了少改工具签名，也可以新增一个 Agent 专用函数：

```python
query_knowledge_base_with_acl(query: str, auth: AuthContext, decision: SingleAgentDecision) -> str
```

然后 `stages.py` 里调用这个受控入口。

---

## 十、报告、证据链和输出安全

权限不仅要拦工具，还要进入证据链和输出校验。

### EvidenceItem 增加访问元数据

在证据构造时补充：

```json
{
  "metadata": {
    "authorized": true,
    "visibility": "internal",
    "access_scope": {
      "role": "engineer",
      "asset_scope": ["J1号机"]
    }
  }
}
```

要求：

- 未授权数据不得生成 EvidenceItem。
- 被拒绝或降级的节点可以生成 `tool_error` / `permission_denied` 类型证据，但不能包含敏感原文。
- Claim 只能引用授权 evidence。

### output_guardrail 增加检查

在 `evidence/quality.py` 中增加：

```text
no_unauthorized_evidence_refs
no_denied_tool_content_in_answer
report_uses_authorized_evidence_only
permission_denial_disclosed
```

最终回答和报告要求：

- 不能出现未授权 SQL 原始结果。
- 不能出现未授权知识库片段。
- 不能把“权限不足”包装成“没有故障”。
- 对降级回答必须明示限制，例如“当前身份不能查询实时运行数据”。

### artifact 保存

`save_artifact` 中保存：

```json
{
  "auth": {
    "user_id": "engineer_01",
    "role": "engineer",
    "asset_scope": ["J1号机"]
  },
  "authorization": {
    "mode": "allow",
    "allowed_nodes": {},
    "denied_nodes": {}
  }
}
```

注意不要保存密码、cookie、完整 token。

---

## 十一、前端和 API 兼容

第一版不强制前端大改，但需要补充身份展示和权限提示。

接口兼容原则：

- `/auth/identity` 保留旧字段。
- `user_identity` query 参数继续接受，但后端继续忽略其权限含义。
- SSE 增加字段不能破坏旧字段。

建议新增 SSE/complete 字段：

```json
{
  "authorization": {
    "allowed": true,
    "mode": "degrade",
    "reason": "当前身份不能查询实时设备数据，已降级为知识库回答。",
    "denied_nodes": {
      "sql": "missing_tool_permission"
    }
  }
}
```

前端展示建议：

- 权限拒绝不显示为系统错误。
- 权限降级显示为普通提示。
- 工作流任务清单中，被权限拒绝的阶段标记 skipped/blocked。

---

## 十二、建议实施顺序

### 阶段 1：身份上下文

改动：

- 新增 `security/contracts.py`、`security/permissions.py`。
- 新增 `AuthContext`。
- `auth/admin_auth.py` 增加 `resolve_auth_context()`。
- `ChatService` 传递 `auth_context`。
- `RestrictedSingleAgentRunner` 保存 `self.auth_context`。

验收：

- `/auth/identity` 返回 role、permissions、asset_scope。
- 前端伪造 `user_identity=管理员` 不会获得管理员权限。
- 现有管理员 PDF 接口仍正常。

### 阶段 2：workflow 入口授权

改动：

- 新增 `security/policy_engine.py`。
- `flow.py` 加 `access_authorization` 阶段。
- `SingleAgentDecision` 增加 authorization 字段。
- guest 对实时查询被拒绝或降级。

验收：

- 游客问实时设备状态，不触发 SQL tool_start。
- 游客问公开故障码含义，可以走知识库。
- 工程师问授权设备状态，可以进入 SQL。
- 工程师问非授权设备，拒绝或要求切换授权范围。

### 阶段 3：工具网关

改动：

- 新增 `security/tool_gateway.py`。
- `_invoke_restricted_tool()` 前增加工具级授权。
- 拒绝结果进入 trace 和 complete payload。

验收：

- 即使误把 `sql` 节点打开，游客仍不能执行 `sql_db_query`。
- 未授权工具不会产生真实工具输出。

### 阶段 4：SQL ACL

改动：

- 新增 `security/sql_acl.py`。
- `stream_sql_step()` 在执行 SQL 前应用 ACL。
- 工程师 SQL 自动注入设备范围或拒绝。

验收：

- 工程师只能查 `asset_scope` 中设备。
- 无设备范围的工程师不能全表查最近 50 条。
- 管理员仍能查全局，但仍受只读、表白名单、LIMIT 限制。

### 阶段 5：RAG ACL

改动：

- uploaded PDF chunk/corpus 增加 visibility metadata。
- `query_knowledge_base` 输出前过滤未授权文档。
- guest 只能看到 public 文档。

验收：

- guest 检索不到 internal/restricted 上传 PDF 内容。
- engineer 可以检索 internal 文档。
- admin 可以检索 restricted 文档。

### 阶段 6：证据链、报告和审计

改动：

- EvidenceItem metadata 增加授权信息。
- output_guardrail 增加未授权引用检查。
- artifact 保存 auth 摘要和 authorization decision。

验收：

- final answer/report 不引用未授权 evidence。
- 权限拒绝/降级能在 trace 和 artifact 中追踪。
- 权限拒绝不会被算作“系统错误”。

---

## 十三、测试清单

建议新增测试文件：

```text
tests/test_auth_context.py
tests/test_policy_engine.py
tests/test_tool_gateway.py
tests/test_sql_acl.py
tests/test_rag_acl.py
tests/test_agent_authorization_flow.py
```

必须覆盖：

- `guest` 默认权限生成。
- `admin` 兼容旧管理员 cookie。
- `engineer` 带设备范围。
- workflow 授权 allow/degrade/deny。
- `action_request` 不直接执行写操作。
- 工具网关拒绝游客 SQL。
- SQL ACL 注入设备过滤。
- SQL ACL 拒绝越权设备。
- RAG ACL 过滤 internal/restricted 文档。
- complete payload 包含 authorization。
- 权限拒绝时没有 SQL/KB 未授权 tool output。

建议验证命令：

```bash
PYTHONPATH=. pytest -q tests/test_auth_context.py tests/test_policy_engine.py tests/test_sql_acl.py tests/test_rag_acl.py
PYTHONPATH=. pytest -q tests/test_agent_authorization_flow.py
python -m compileall fault_diagnosis
git diff --check
```

如果改动影响前端身份显示，再跑：

```bash
cd agent_fronted
npm run build
```

---

## 十四、实现注意事项

- 不要把权限判断写在 prompt 里作为唯一边界。
- 不要相信前端传来的身份、角色、设备范围。
- 不要在拒绝响应里带出 SQL 原文结果、RAG 原文片段或工具异常详情。
- 不要把 `permission_check` 继续扩成所有权限逻辑的大杂烩。建议保留它作为动作请求节点，新增 `access_authorization` 作为通用入口授权。
- 不要把 SQL ACL 写成只在 fallback SQL 生效，模型生成 SQL 和 checker 修正 SQL 都必须过 ACL。
- 不要只过滤最终回答。未授权数据不能进入 LLM 上下文。
- 新增阶段后要重新检查 `SingleAgentLimits.max_rounds`。
- artifact 中只保存身份摘要和授权结果，不保存 cookie、密码、token。

---

## 十五、推荐第一轮最小改动范围

如果只做一个可上线的 MVP，优先完成：

```text
1. AuthContext
2. workflow 入口授权
3. 工具网关拒绝游客 SQL/报告
4. SQL ACL 限制 engineer 设备范围
5. complete payload 暴露 authorization
6. 单测覆盖 guest/admin/engineer 三类身份
```

RAG 文档可见性和报告 evidence 授权可以作为第二轮，但 SQL 数据级 ACL 必须放在第一轮。
