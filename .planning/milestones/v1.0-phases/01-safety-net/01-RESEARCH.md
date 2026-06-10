# Phase 1: Safety Net - Research

**Researched:** 2026-03-26
**Domain:** Python testing (pytest), FastAPI SSE streaming, LangChain/LangGraph agent mocking, secret cleanup
**Confidence:** HIGH

## Summary

Phase 1 establishes a characterization test suite and safety preconditions before any code is moved in the 7-phase refactoring. The primary technical challenges are: (1) mocking a deeply-coupled import chain where `tools.py`, `knowledge_base.py`, and `app.py` all execute side-effects at module import time (MySQL connections, Ollama connections, Tavily API client creation), (2) constructing a fake LLM that supports `bind_tools` so `create_agent()` can build a working agent in test without any real LLM, and (3) consuming FastAPI `StreamingResponse` SSE events in pytest assertions.

The codebase has three distinct hardcoded API keys (two `sk-` keys and one `ms-` key) in commented-out code blocks across `app.py`, `app_copy.py`, and `subagent/fault_explanation_agent.py`. Additionally, `app_copy.py` is a 22KB duplicate file that must be deleted. The `subagent/fault_explanation_agent.py` has a missing `import os` that would cause `NameError` at runtime.

**Primary recommendation:** Use pytest + pytest-asyncio with aggressive monkeypatching of module-level side-effects before any production code is imported. Build a custom `FakeToolCallingModel(BaseChatModel)` that implements `bind_tools` and returns pre-configured `AIMessage` objects with `tool_calls`. Test SSE by reading `response.text` from synchronous `TestClient` and parsing the `data:` lines.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **测试隔离策略**: 全 Mock -- Mock 掉 LLM、MySQL、PostgreSQL、Ollama 所有外部服务
- `tools.py` 模块级 DB 连接用 pytest monkeypatch 在 import 前 mock 掉 `pymysql.connect` 和 `SQLDatabase`
- 不修改生产代码来适配测试（延迟初始化属于 Phase 4 TOOL-02 的工作）
- 测试框架：pytest + pytest-asyncio，测试文件放在项目根目录 `tests/` 下，配 `conftest.py` 和 `pytest.ini`
- httpx 已在 requirements.txt 中，用 FastAPI TestClient 进行端点测试
- **SSE 特征测试**: Mock LLM 响应用 fake model 返回固定的 tool_call 消息，让 agent 确定性地调用工具
- **断言粒度：事件类型 + 结构** -- 断言事件类型序列（start -> token -> tool_start -> tool_end -> complete）和每种事件的 JSON 结构（必有字段），不断言具体文本内容
- 至少测试 get_time 工具调用产生的完整 SSE 事件序列
- **密钥清理**: 删除 `app_copy.py`，清理 `subagent/fault_explanation_agent.py` 中注释掉的 API key 和旧配置块，修复缺失的 `import os`
- 不重写 git 历史，而是轮换已泄露的密钥
- 验证：`git log --all -S "sk-"` 在当前文件中不返回结果（历史中的记录通过密钥轮换来消除风险）
- **冒烟测试**: pytest 测试形式，验证 `from app import app` 不报错 + GET `/chat/stream` 返回 200 并产生 SSE 事件

### Claude's Discretion
- pytest fixture 的具体实现方式（如何 mock LangChain model）
- conftest.py 中 monkeypatch 的作用域和顺序
- 测试文件的命名和组织方式

### Deferred Ideas (OUT OF SCOPE)
None -- 讨论全程保持在 Phase 1 范围内
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SAFE-01 | Characterization test 覆盖 SSE 流式响应（验证 start/token/tool_start/tool_end/complete 事件序列） | FakeToolCallingModel + TestClient SSE parsing pattern; monkeypatch chain for import-safe app loading |
| SAFE-02 | Characterization test 覆盖工具调用（至少验证 get_time 和 sql_inter 工具能被正确触发和返回） | FakeToolCallingModel with pre-configured tool_calls returning get_time; sql_inter mock via monkeypatch of pymysql.connect |
| SAFE-03 | Characterization test 覆盖历史 API（/ai/history/{type} 和 /api/todos/{thread_id} 端点返回正确格式） | TestClient GET with mock checkpointer on app.state; assert response JSON structure |
| SAFE-04 | Smoke test 脚本验证 agent 启动成功并能响应简单查询 | Same FakeToolCallingModel + TestClient pattern; verify from app import app succeeds + SSE stream produces events |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Tech Stack Lock**: LangChain 1.0.3 + LangGraph 1.0.5 + FastAPI 0.121.0 -- do not upgrade
- **API Contract**: Do not change existing HTTP endpoints -- frontend depends on them unchanged
- **No new heavy dependencies** -- keep the project lightweight (pytest + pytest-asyncio are lightweight test deps)
- **Language**: All comments, docstrings, user-facing strings in Chinese (Simplified)
- **Style**: Python snake_case, 4-space indent, double quotes preferred
- **No production code changes** for test accommodation (locked in CONTEXT.md)

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | 9.0.x | Test framework | De facto Python testing standard; already decided in CONTEXT.md |
| pytest-asyncio | 1.3.x | Async test support | Required for testing async FastAPI lifespan and agent streaming |
| httpx | 0.28.1 | HTTP test client | Already in requirements.txt; powers FastAPI TestClient |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| FastAPI TestClient | (bundled) | Synchronous endpoint testing | SSE endpoint tests, history API tests, smoke tests |
| unittest.mock / monkeypatch | (stdlib/pytest) | Module-level mocking | Mock DB connections, LLM, Ollama before import |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| TestClient (sync) | httpx.AsyncClient | AsyncClient hangs on SSE in same event loop; TestClient simpler and works |
| Custom FakeToolCallingModel | GenericFakeChatModel | GenericFakeChatModel lacks bind_tools; create_agent will fail |
| async-asgi-testclient | TestClient | Extra dependency; TestClient sufficient when SSE generator terminates |

**Installation:**
```bash
pip install pytest pytest-asyncio
```

**Note:** httpx is already in requirements.txt (0.28.1). No other new dependencies needed.

## Architecture Patterns

### Recommended Project Structure
```
tests/
├── conftest.py           # Session/module-scoped monkeypatch fixtures
├── pytest.ini            # (or pyproject.toml [tool.pytest.ini_options])
├── test_sse_stream.py    # SAFE-01: SSE event sequence tests
├── test_tool_calls.py    # SAFE-02: Tool invocation tests
├── test_history_api.py   # SAFE-03: History + todos endpoint tests
└── test_smoke.py         # SAFE-04: Import + basic query smoke test
```

### Pattern 1: Module-Level Mock Chain (conftest.py)

**What:** Before `app.py` or `tools.py` can be imported, their module-level side-effects must be neutralized. This requires monkeypatching external dependencies BEFORE the first `import app`.

**When to use:** Every test file in this project.

**Critical import chain that triggers side-effects:**
```
app.py imports:
  ├── tools.py (line 33) which at import time:
  │   ├── TavilySearch() → needs TAVILY_API_KEY
  │   ├── SQLDatabase.from_uri() → connects to MySQL
  │   ├── ChatOpenAI() → needs env vars
  │   ├── SQLDatabaseToolkit() → needs live DB
  │   └── imports subagent.fault_explanation_agent → missing import os
  ├── knowledge_base.py (indirectly via tools.py query_knowledge_base)
  │   └── init_knowledge_base() at module level → connects to Ollama
  ├── ChatOpenAI() (lines 220-233) → needs env vars
  └── lifespan() → creates PostgreSQL pool (only at app startup, not import)
```

**Example conftest.py approach:**
```python
import sys
import types
from unittest.mock import MagicMock, AsyncMock, patch
import pytest


@pytest.fixture(scope="session", autouse=True)
def mock_external_services(tmp_path_factory):
    """在所有 import 之前 mock 掉外部服务连接。

    必须在 conftest.py 中以 session scope 运行，
    确保 tools.py / knowledge_base.py 的模块级代码不会真正连接外部服务。
    """
    import os
    # 设置必要的环境变量（假值，不会真正连接）
    env_defaults = {
        "HOST": "localhost", "USER": "test", "MYSQL_PW": "test",
        "DB_NAME": "test", "PORT": "3306",
        "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "test", "POSTGRES_USER": "test",
        "POSTGRES_PASSWORD": "test",
        "MODEL_NAME": "fake-model", "OPENAI_BASE_URL": "http://fake",
        "OPENAI_API_KEY": "fake-key",
    }
    for k, v in env_defaults.items():
        os.environ.setdefault(k, v)

    # Mock 掉 tools.py 模块级 DB 连接
    with patch("langchain_community.utilities.SQLDatabase.from_uri",
               return_value=MagicMock()) as mock_db, \
         patch("langchain_community.agent_toolkits.SQLDatabaseToolkit",
               return_value=MagicMock(get_tools=lambda: [])) as mock_toolkit, \
         patch("langchain_tavily.TavilySearch",
               return_value=MagicMock()) as mock_tavily, \
         patch("knowledge_base.init_knowledge_base") as mock_kb, \
         patch("knowledge_base.create_knowledge_base",
               return_value=None) as mock_create_kb:

        # 修复 subagent 缺失的 import os（在 import 前注入）
        # 注意：这里不修改生产代码，只在测试环境中处理

        yield
```

**Key insight:** The `patch` context managers must be active BEFORE `import app` / `import tools` happens. Using `session` scope ensures they're set up once and stay active for the entire test session.

### Pattern 2: FakeToolCallingModel for Agent Testing

**What:** A custom `BaseChatModel` subclass that supports `bind_tools()` and returns predetermined `AIMessage` objects (optionally with `tool_calls`). Required because `create_agent()` calls `model.bind_tools(tools)` internally, and neither `FakeListChatModel` nor `GenericFakeChatModel` implement `bind_tools`.

**When to use:** Any test that needs a working agent (SSE tests, tool call tests, smoke tests).

**Example:**
```python
from typing import Any, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeToolCallingModel(BaseChatModel):
    """支持 bind_tools 的假 LLM，用于确定性测试。"""

    responses: list  # List[AIMessage] -- 按序返回
    _call_count: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any
    ) -> ChatResult:
        response = self.responses[self._call_count % len(self.responses)]
        self._call_count += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    def bind_tools(self, tools: list, **kwargs: Any):
        """返回 self -- 工具绑定是 no-op，因为响应是预设的。"""
        return self
```

**Usage in test (get_time tool call):**
```python
from langchain_core.messages import AIMessage

# 第一次调用：LLM 决定调用 get_time 工具
tool_call_msg = AIMessage(
    content="",
    tool_calls=[{
        "id": "call_1",
        "name": "get_time",
        "args": {},
    }]
)
# 第二次调用：LLM 收到工具结果后，生成最终回复
final_msg = AIMessage(content="现在时间是 2026-03-26 12:00:00")

fake_model = FakeToolCallingModel(responses=[tool_call_msg, final_msg])
```

### Pattern 3: SSE Event Parsing from TestClient

**What:** FastAPI's `TestClient` (based on httpx) returns a synchronous `Response` for streaming endpoints. For SSE endpoints backed by `StreamingResponse`, `response.text` contains the full SSE output (because TestClient buffers the entire response). Parse the `data:` lines to extract events.

**When to use:** SAFE-01 SSE tests, SAFE-04 smoke tests.

**Important nuance:** The SSE generator in `app.py` (`token_stream_events`) is finite -- it terminates after `complete` or `server_error` event. This means `TestClient` will NOT hang (it only hangs for infinite SSE generators). The generator runs to completion and TestClient returns the buffered response.

**Example:**
```python
import json
from fastapi.testclient import TestClient


def parse_sse_events(response_text: str) -> list:
    """从 SSE 响应文本中解析事件列表。"""
    events = []
    for line in response_text.strip().split("\n"):
        if line.startswith("data: "):
            data_str = line[len("data: "):]
            try:
                events.append(json.loads(data_str))
            except json.JSONDecodeError:
                events.append({"raw": data_str})
    return events


def test_sse_event_sequence(test_client):
    """SAFE-01: 断言 SSE 事件类型序列。"""
    response = test_client.get(
        "/chat/stream",
        params={"message": "现在几点？", "thread_id": "test-1"}
    )
    assert response.status_code == 200

    events = parse_sse_events(response.text)
    event_types = [e.get("type") for e in events]

    # 断言事件序列包含必要的类型
    assert event_types[0] == "chat_start"           # start event
    assert "token" in event_types                     # at least one token
    assert "tool_start" in event_types                # tool was called
    assert "tool_end" in event_types                  # tool completed
    assert event_types[-1] == "chat_complete"         # ends with complete
```

### Pattern 4: Mock Checkpointer for History API Tests

**What:** The `/ai/history/{type}` and `/api/todos/{thread_id}` endpoints read from `app.state.checkpointer`. In tests, replace the checkpointer with a mock that returns predefined data.

**When to use:** SAFE-03 history API tests.

**Example:**
```python
@pytest.fixture
def test_client_with_mock_history(app_with_mocks):
    """创建带 mock checkpointer 的测试客户端。"""
    from fastapi.testclient import TestClient

    mock_checkpointer = AsyncMock()
    # mock alist() 返回 checkpoint tuples
    mock_checkpointer.alist = AsyncMock(return_value=iter([
        MagicMock(config={"configurable": {"thread_id": "thread-1"}}),
        MagicMock(config={"configurable": {"thread_id": "thread-2"}}),
    ]))
    # mock aget() 返回 checkpoint data
    mock_checkpointer.aget = AsyncMock(return_value={
        "channel_values": {
            "messages": [],
            "todos": [{"id": "1", "content": "测试任务", "status": "pending"}]
        }
    })

    app_with_mocks.state.checkpointer = mock_checkpointer

    with TestClient(app_with_mocks) as client:
        yield client
```

### Anti-Patterns to Avoid
- **Import app at module top level in test files:** Will trigger all module-level side-effects before monkeypatch is active. Always import inside fixtures or test functions, after patches are applied.
- **Using `GenericFakeChatModel` directly with `create_agent`:** Will fail because it lacks `bind_tools()`. Must use custom subclass.
- **Asserting exact LLM text content in SSE tests:** Defeats the purpose of characterization tests. Assert event types and JSON structure, not text content.
- **Using `httpx.AsyncClient` for SSE tests:** Can hang in same event loop. Use synchronous `TestClient` instead.
- **Modifying production code for testability:** Locked decision -- no production code changes. All mocking via monkeypatch/patch.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SSE event parsing | Custom streaming client | `response.text` + line parsing | TestClient buffers entire response for finite generators |
| Fake LLM with tools | Patching internal agent methods | Custom `FakeToolCallingModel(BaseChatModel)` | Clean, reusable, works with `create_agent` |
| Async test infrastructure | Custom event loop management | pytest-asyncio `auto` mode | Handles event loop lifecycle correctly |
| JSON structure assertions | Nested key-by-key checks | Dict subset assertions or schema validation | More readable, better error messages |

**Key insight:** The testing challenge here is 90% about mocking the import chain and 10% about writing assertions. Get the `conftest.py` mock chain right and the actual test functions are straightforward.

## Common Pitfalls

### Pitfall 1: Import Order Causes Real DB Connection
**What goes wrong:** Importing `app` or `tools` before mocks are active triggers `SQLDatabase.from_uri()` which attempts a real MySQL connection and fails.
**Why it happens:** Python executes module-level code at import time. `tools.py` lines 32-48 run immediately on `import tools`.
**How to avoid:** Use `session`-scoped fixtures in `conftest.py` that set up `patch()` context managers BEFORE any test file imports `app`. Alternatively, use `conftest.py` at the `tests/` level to ensure it runs first.
**Warning signs:** `OperationalError: (2003, "Can't connect to MySQL server")` in test output.

### Pitfall 2: knowledge_base.py Module-Level init_knowledge_base()
**What goes wrong:** `knowledge_base.py` line 134 calls `init_knowledge_base()` at import time, which tries to connect to Ollama at `http://10.108.13.254:11434`.
**Why it happens:** `tools.py` imports `from knowledge_base import db_retriever` indirectly through `query_knowledge_base` tool (lazy import inside the function). However, `knowledge_base.py` runs `init_knowledge_base()` unconditionally at the bottom of the file.
**How to avoid:** Mock `knowledge_base.init_knowledge_base` and `knowledge_base.create_knowledge_base` before `import knowledge_base` happens.
**Warning signs:** Connection timeout errors to Ollama server in test output.

### Pitfall 3: FakeToolCallingModel Missing bind_tools
**What goes wrong:** `create_agent()` calls `model.bind_tools(tools)`. If the fake model doesn't implement this, you get `NotImplementedError`.
**Why it happens:** `GenericFakeChatModel` and `FakeListChatModel` don't implement `bind_tools()`. This is a known gap in LangChain's testing utilities as of 2026.
**How to avoid:** Use a custom `FakeToolCallingModel` subclass of `BaseChatModel` that implements `bind_tools` (returns `self`).
**Warning signs:** `NotImplementedError: bind_tools` during test setup.

### Pitfall 4: TestClient Hanging on Infinite SSE Streams
**What goes wrong:** TestClient blocks forever waiting for the response to complete.
**Why it happens:** If the SSE generator never terminates (e.g., if agent enters infinite loop), TestClient waits indefinitely.
**How to avoid:** The app's `token_stream_events` generator is finite (terminates after `complete` or `server_error` event). Using a `FakeToolCallingModel` with a fixed response list ensures deterministic termination. As a safety net, set `timeout` parameter on TestClient requests.
**Warning signs:** Tests hang indefinitely with no output.

### Pitfall 5: subagent/fault_explanation_agent.py Missing import os
**What goes wrong:** When `create_fault_explanation_agent()` is called, `os.getenv()` on line 35 raises `NameError: name 'os' is not defined`.
**Why it happens:** The file imports `from dotenv import load_dotenv` but never imports `os`, yet uses `os.getenv()`.
**How to avoid:** Fix by adding `import os` as part of the security cleanup task (CONTEXT.md already includes this).
**Warning signs:** `NameError` when running fault_explanation_tool in tests.

### Pitfall 6: TavilySearch Requires API Key at Import Time
**What goes wrong:** `tools.py` line 27 creates `TavilySearch(max_results=5, topic="general")` at module level. Without `TAVILY_API_KEY` env var, this may fail or create a non-functional instance.
**Why it happens:** Module-level instantiation.
**How to avoid:** Mock `langchain_tavily.TavilySearch` before `import tools`.
**Warning signs:** Pydantic validation error or API key missing error.

### Pitfall 7: `alist()` is an Async Iterator Not a List
**What goes wrong:** `/ai/history/{type}` endpoint uses `async for checkpoint_tuple in request.app.state.checkpointer.alist()`. Mocking this requires returning an async iterable, not a regular list.
**Why it happens:** `AsyncPostgresSaver.alist()` returns an async generator.
**How to avoid:** Mock with an async generator function or use `AsyncMock` configured to return an async iterable.
**Warning signs:** `TypeError: 'async for' requires an object with __aiter__ method`.

## Code Examples

### Complete conftest.py Mock Setup
```python
"""tests/conftest.py - 测试环境初始化和全局 fixture。

在任何测试模块 import app 之前，mock 掉所有外部服务连接。
"""
import os
import sys
import json
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Any, List, Optional

import pytest


# ===== 环境变量设置（在任何 import 之前） =====
_TEST_ENV = {
    "HOST": "localhost", "USER": "test", "MYSQL_PW": "test",
    "DB_NAME": "test", "PORT": "3306",
    "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "test", "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "MODEL_NAME": "fake-model",
    "OPENAI_BASE_URL": "http://localhost:1/fake",
    "OPENAI_API_KEY": "fake-key-for-testing",
    "TAVILY_API_KEY": "fake-tavily-key",
}
for k, v in _TEST_ENV.items():
    os.environ.setdefault(k, v)


# ===== Session-scoped patches =====

@pytest.fixture(scope="session", autouse=True)
def _patch_external_services():
    """Mock 掉所有模块级外部服务连接。"""
    mock_db = MagicMock()
    mock_toolkit = MagicMock()
    mock_toolkit.get_tools.return_value = []

    patches = [
        patch("langchain_community.utilities.SQLDatabase.from_uri",
              return_value=mock_db),
        patch("langchain_community.agent_toolkits.SQLDatabaseToolkit",
              return_value=mock_toolkit),
        patch("langchain_tavily.TavilySearch", return_value=MagicMock()),
        patch("knowledge_base.init_knowledge_base"),
        patch("knowledge_base.create_knowledge_base", return_value=None),
    ]

    for p in patches:
        p.start()

    yield

    for p in patches:
        p.stop()
```

### FakeToolCallingModel Implementation
```python
"""tests/fake_model.py - 支持 bind_tools 的假 LLM。"""
from typing import Any, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class FakeToolCallingModel(BaseChatModel):
    """确定性假 LLM，按序返回预设的 AIMessage 列表。

    支持 bind_tools() 以兼容 create_agent()。
    """
    responses: List[AIMessage] = Field(default_factory=list)
    call_count: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        if not self.responses:
            msg = AIMessage(content="没有预设响应")
        else:
            msg = self.responses[self.call_count % len(self.responses)]
            self.call_count += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools: list, **kwargs: Any) -> "FakeToolCallingModel":
        """No-op: 工具绑定不影响预设响应。"""
        return self
```

### SSE Event Parser Utility
```python
"""tests/helpers.py - 测试辅助函数。"""
import json
from typing import List, Dict, Any


def parse_sse_events(response_text: str) -> List[Dict[str, Any]]:
    """从 SSE 响应文本中解析所有 data: 行为 JSON 事件列表。"""
    events = []
    for line in response_text.strip().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            data_str = line[len("data: "):]
            try:
                events.append(json.loads(data_str))
            except json.JSONDecodeError:
                events.append({"_raw": data_str})
    return events


def get_event_type_sequence(events: List[Dict[str, Any]]) -> List[str]:
    """提取事件类型序列。"""
    return [e.get("type", "unknown") for e in events]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `FakeListChatModel` for agent tests | Custom `FakeToolCallingModel` subclass | LangChain 1.0 (2025) | `create_agent` requires `bind_tools` support |
| `httpx.AsyncClient` for SSE tests | Synchronous `TestClient` for finite generators | Ongoing | AsyncClient can hang in same event loop |
| `initialize_agent` + `AgentExecutor` | `create_agent` returns `CompiledStateGraph` | LangChain 1.0 | New agent factory, new streaming API (`astream_events`) |

**Deprecated/outdated:**
- `langchain.agents.initialize_agent`: Replaced by `create_agent` in LangChain 1.0
- `langchain_community.chat_models.fake.FakeListChatModel`: Exists but lacks `bind_tools`, insufficient for agent tests

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.x + pytest-asyncio 1.3.x |
| Config file | `tests/pytest.ini` (Wave 0 -- to be created) |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SAFE-01 | SSE 流式事件序列 start/token/tool_start/tool_end/complete | integration | `pytest tests/test_sse_stream.py -x` | Wave 0 |
| SAFE-02 | get_time 和 sql_inter 工具调用触发和返回格式 | integration | `pytest tests/test_tool_calls.py -x` | Wave 0 |
| SAFE-03 | /ai/history/{type} 和 /api/todos/{thread_id} 返回结构 | integration | `pytest tests/test_history_api.py -x` | Wave 0 |
| SAFE-04 | from app import app 成功 + 简单查询响应 | smoke | `pytest tests/test_smoke.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x -q`
- **Per wave merge:** `pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/conftest.py` -- 全局 mock fixture（模块级外部服务 mock）
- [ ] `tests/fake_model.py` -- FakeToolCallingModel 实现
- [ ] `tests/helpers.py` -- SSE 事件解析辅助函数
- [ ] `tests/pytest.ini` -- pytest 配置（asyncio_mode, testpaths）
- [ ] `tests/test_sse_stream.py` -- SAFE-01
- [ ] `tests/test_tool_calls.py` -- SAFE-02
- [ ] `tests/test_history_api.py` -- SAFE-03
- [ ] `tests/test_smoke.py` -- SAFE-04
- [ ] Framework install: `pip install pytest pytest-asyncio`

## Open Questions

1. **`alist()` async iterator mocking precision**
   - What we know: `checkpointer.alist()` is async. The mock needs to return an async iterable for `async for` in the endpoint.
   - What's unclear: Whether `AsyncMock` auto-handles async iteration or we need an explicit async generator fixture.
   - Recommendation: Implement a small async generator helper and test it works before writing the full SAFE-03 test.

2. **`create_agent` internal call to `bind_tools` verified?**
   - What we know: Based on LangChain 1.0 docs, `create_agent` calls `model.bind_tools(tools)`. This is HIGH confidence from official docs.
   - What's unclear: Whether LangChain 1.0.3 specifically might have a different code path.
   - Recommendation: The custom `FakeToolCallingModel.bind_tools` returning `self` is safe -- even if `create_agent` doesn't call it, having it doesn't hurt. If it does call it (expected), the test works.

3. **SSE event format with `event:` prefix lines**
   - What we know: The app uses `f"event: start\ndata: {json}\n\n"` format. TestClient returns the full text including `event:` lines.
   - What's unclear: Whether TestClient strips `event:` lines or returns them as-is.
   - Recommendation: Parse both `event:` and `data:` lines. The SSE parser should handle both formats.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | All tests | Unknown (conda env) | faultagent env | System Python 3.9.6 insufficient; must use conda env |
| pytest | Test framework | Not installed | -- | `pip install pytest` in faultagent env |
| pytest-asyncio | Async test support | Not installed | -- | `pip install pytest-asyncio` in faultagent env |
| httpx | TestClient | Installed (in requirements.txt) | 0.28.1 | -- |
| MySQL | NOT needed (mocked) | N/A | -- | -- |
| PostgreSQL | NOT needed (mocked) | N/A | -- | -- |
| Ollama | NOT needed (mocked) | N/A | -- | -- |

**Missing dependencies with no fallback:**
- pytest and pytest-asyncio must be installed in the `faultagent` conda environment

**Missing dependencies with fallback:**
- None -- all external services are mocked

## Security Cleanup Inventory

### Files Containing Hardcoded Secrets

| File | Line(s) | Secret Type | Action |
|------|---------|-------------|--------|
| `app_copy.py` | 222, 229 | `sk-` API key, `sk-` API key | Delete entire file |
| `app.py` | 207-208 | `ms-` ModelScope key (commented) | Remove commented block (lines 204-218) |
| `app.py` | 214-215 | `sk-` API key (commented) | Remove commented block (lines 204-218) |
| `subagent/fault_explanation_agent.py` | 18-19 | `sk-` API key (commented) | Remove commented block (lines 16-33) |
| `subagent/fault_explanation_agent.py` | 24-25 | `ms-` ModelScope key (commented) | Remove commented block (lines 16-33) |

### Git History Check
`git log --all -S "sk-"` currently returns ~20 commits. After removing the commented code from current files, re-running should show 0 matches in the current working tree. Historical commits are mitigated by key rotation (per CONTEXT.md decision).

### Bug Fix
- `subagent/fault_explanation_agent.py`: Add `import os` at top of file (line 1 area) -- currently missing, causes `NameError` at runtime.

## Sources

### Primary (HIGH confidence)
- FastAPI TestClient docs: https://fastapi.tiangolo.com/tutorial/testing/ -- TestClient usage patterns
- FastAPI reference: https://fastapi.tiangolo.com/reference/testclient/ -- TestClient API
- LangChain create_agent reference: https://reference.langchain.com/python/langchain/agents/factory/create_agent -- function signature, middleware, context_schema parameters
- LangChain GenericFakeChatModel: https://reference.langchain.com/python/langchain-core/language_models/fake_chat_models/GenericFakeChatModel -- confirmed lacks bind_tools
- LangChain AIMessage reference: https://reference.langchain.com/python/langchain-core/messages/ai/AIMessage -- tool_calls attribute

### Secondary (MEDIUM confidence)
- LangGraph test_agent.py: https://github.com/langchain-ai/langchain/blob/master/libs/langchain/tests/unit_tests/agents/test_agent.py -- FakeListLLM patterns
- LangChain GitHub discussion #31761: FakeChatModel with Tools limitation confirmed
- LangChain GitHub discussion #29893: GenericFakeChatModel bind_tools not implemented
- httpx discussion #2629: AsyncClient SSE testing challenges
- FastAPI issue #2006: Streaming response testing recommendations

### Tertiary (LOW confidence)
- Exact FakeToolCallingModel implementation from LangGraph tests -- could not fetch source directly; custom implementation based on BaseChatModel API is well-grounded

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- pytest + httpx are standard, directly confirmed in requirements.txt
- Architecture (mock chain): HIGH -- verified import chain by reading all source files; side-effects documented with line numbers
- Architecture (FakeToolCallingModel): MEDIUM -- based on BaseChatModel API docs + community confirmation that GenericFakeChatModel lacks bind_tools; exact create_agent internal behavior at v1.0.3 inferred
- Pitfalls: HIGH -- identified by tracing actual import chains and module-level code in source files
- Security cleanup: HIGH -- verified all secret locations by grep search of actual files

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (stable -- pinned library versions)
