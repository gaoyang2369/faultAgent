---
phase: 02-modular-restructure
verified: 2026-03-26T08:15:00Z
status: passed
score: 5/5 success criteria verified
gaps: []
---

# Phase 2: Config & Utils Verification Report

**Phase Goal:** 配置集中管理，通用工具函数独立，为后续模块拆分做基础准备
**Verified:** 2026-03-26T08:15:00Z
**Status:** PASSED
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | config.py 存在，包含所有 8 个当前硬编码值 | VERIFIED | config.py has OLLAMA_BASE_URL, EMBEDDING_MODEL, FAISS_PATH, MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP, RECURSION_LIMIT, DCMA_DB_NAME, FAULT_API_URL -- all 8 with os.getenv and correct defaults |
| 2 | utils.py 存在，包含 sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output 及辅助函数 | VERIFIED | utils.py has 3 public + 4 private functions (205 lines), all 7 functions present |
| 3 | app.py 和 tools.py 中对应的硬编码值和工具函数改为从 config.py / utils.py 导入 | VERIFIED | app.py:33 `from tools import tools`, app.py:34 `from utils import ...`, app.py:35 `from config import ...`; tools.py:17 `from config import DCMA_DB_NAME`; grep for hardcoded values in app.py (64000) and tools.py ("dcma") returns zero matches; utility functions removed from tools.py |
| 4 | knowledge_base.py 从 config.py 读取 Ollama URL, embedding model, FAISS path | VERIFIED | knowledge_base.py:8 `from config import OLLAMA_BASE_URL, EMBEDDING_MODEL, FAISS_PATH`; both OllamaEmbeddings calls use config constants (lines 28-29, 77-78); grep for hardcoded "qwen3-embedding:8b" and "10.108.13.254:11434" returns zero matches |
| 5 | Phase 1 所有 22 个测试仍然通过 | VERIFIED | Full suite: 74 passed (22 Phase 1 + 52 Phase 2), 0 failed, exit code 0 |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `config.py` | Centralized configuration with 8 env-var constants | VERIFIED | 27 lines, all 8 constants with os.getenv defaults, load_dotenv(override=True), no class definitions |
| `utils.py` | Generic utilities: sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output + 4 private helpers | VERIFIED | 205 lines, 7 functions, module-level langchain_core.messages import, no imports from app/tools/config |
| `tests/test_config.py` | Config module unit tests | VERIFIED | 18 tests: 8 default values, 3 type checks, 4 env overrides, 3 structure constraints |
| `tests/test_utils.py` | Utils module unit tests | VERIFIED | 34 tests: 12 sanitize_for_json, 5 safe_json_dumps, 10 parse_todos, 7 module structure |
| `app.py` (modified) | Imports from config and utils, no hardcoded config values | VERIFIED | Uses MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP, RECURSION_LIMIT from config; sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output from utils |
| `tools.py` (modified) | Uses DCMA_DB_NAME from config, utility functions removed | VERIFIED | `from config import DCMA_DB_NAME` at line 17; DCMA_DB_NAME used in SQLDatabase.from_uri at line 33; zero utility function definitions remain (grep confirmed) |
| `knowledge_base.py` (modified) | Uses OLLAMA_BASE_URL, EMBEDDING_MODEL, FAISS_PATH from config | VERIFIED | Config import at line 8; both OllamaEmbeddings instances use config constants; db_save_path=None with FAISS_PATH fallback |
| `subagent/call_api_tool.py` (modified) | Uses FAULT_API_URL from config | VERIFIED | `from config import FAULT_API_URL` at line 18; `api_url = FAULT_API_URL` at line 92; grep for hardcoded "10.108.13.250:8001" returns zero |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| app.py | utils.py | `from utils import sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output` | WIRED | Line 34, functions used throughout SSE streaming |
| app.py | config.py | `from config import MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP, RECURSION_LIMIT` | WIRED | Line 35; used at lines 247, 248, 323 |
| tools.py | config.py | `from config import DCMA_DB_NAME` | WIRED | Line 17; used in SQLDatabase.from_uri at line 33 |
| knowledge_base.py | config.py | `from config import OLLAMA_BASE_URL, EMBEDDING_MODEL, FAISS_PATH` | WIRED | Line 8; EMBEDDING_MODEL used at lines 28, 77; OLLAMA_BASE_URL at lines 29, 78; FAISS_PATH at line 21 |
| subagent/call_api_tool.py | config.py | `from config import FAULT_API_URL` | WIRED | Line 18; used at line 92 |
| config.py | .env | `load_dotenv + os.getenv` | WIRED | load_dotenv(override=True) at line 10; 8 os.getenv calls |
| utils.py | langchain_core.messages | `from langchain_core.messages import HumanMessage, AIMessage, ToolMessage` | WIRED | Line 8; used in sanitize_for_json isinstance check at line 21 |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| CONF-01 | 02-01-PLAN.md | 创建 config.py 集中管理所有硬编码配置值 | SATISFIED | config.py exists with all 8 constants, tests pass (18/18) |
| CONF-02 | 02-01-PLAN.md | 创建 utils.py 包含通用工具函数 | SATISFIED | utils.py exists with 7 functions, tests pass (34/34) |
| CONF-03 | 02-02-PLAN.md | 现有文件中的硬编码值和通用函数改为从 config.py / utils.py 导入 | SATISFIED | All 4 files rewired: app.py, tools.py, knowledge_base.py, subagent/call_api_tool.py; grep confirms zero hardcoded values remain |

No orphaned requirements. REQUIREMENTS.md maps CONF-01, CONF-02, CONF-03 to Phase 2; all three are claimed and satisfied.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| knowledge_base.py | 122 | `rebuild_knowledge_base(pdf_dir="pdfs", db_save_path="faiss_db")` still has hardcoded `"faiss_db"` default | Info | Not a blocker -- matches config.py default value `FAISS_PATH="faiss_db"`. Function passes value to create_knowledge_base which handles it. Could be improved to use FAISS_PATH directly in a future phase. |

No TODO/FIXME/PLACEHOLDER patterns found in config.py or utils.py.
No empty implementations found.

### Human Verification Required

None. All Phase 2 goals are verifiable programmatically:
- Config constants and defaults verified by unit tests
- Import rewiring verified by grep
- Regression safety verified by full test suite (74/74 pass)
- No UI or runtime behavior changes in this phase

### Verification Commands and Output

**Test Suite (full):**
```bash
python3 -m pytest tests/ -v
```
```
74 passed, 14 warnings in 1.48s
```

**Config/Utils Tests:**
```bash
python3 -m pytest tests/test_config.py tests/test_utils.py -v
```
```
52 passed in 0.08s
```

**Import Chain:**
```bash
python3 -c "from config import OLLAMA_BASE_URL, ..., FAULT_API_URL; print('config imports OK')"
python3 -c "from utils import sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output; print('utils imports OK')"
```
Both print OK with exit code 0.

**Hardcoded Value Grep (all return zero matches):**
- `grep "qwen3-embedding:8b" knowledge_base.py` -- no matches
- `grep "10.108.13.254:11434" knowledge_base.py` -- no matches
- `grep "64000" app.py` -- no matches
- `grep '"dcma"' tools.py` -- no matches
- `grep "10.108.13.250:8001" subagent/call_api_tool.py` -- no matches

**Utility Function Removal (all return zero matches):**
- `grep "def sanitize_for_json" tools.py` -- no matches
- `grep "def safe_json_dumps" tools.py` -- no matches
- `grep "def parse_todos_from_tool_output" tools.py` -- no matches

**Commits Verified:**
- `429fee3` feat(02-01): create config.py with 8 centralized configuration constants
- `70f1712` feat(02-01): create utils.py with 7 utility functions extracted from tools.py
- `a66e5ae` refactor(02-02): rewire app.py and knowledge_base.py to import from config and utils
- `bdfabd6` refactor(02-02): rewire tools.py and subagent/call_api_tool.py, remove utility functions

### Gaps Summary

No gaps found. All 5 success criteria from ROADMAP.md are verified. All 3 requirement IDs (CONF-01, CONF-02, CONF-03) are satisfied. All artifacts exist, are substantive, and are wired. Full test suite passes with zero regressions.

---

_Verified: 2026-03-26T08:15:00Z_
_Verifier: Claude (gsd-verifier)_
