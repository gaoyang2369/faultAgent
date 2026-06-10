---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: v1.0 milestone complete
stopped_at: Completed 05-01-PLAN.md
last_updated: "2026-05-24T00:00:00.000Z"
progress:
  total_phases: 5
  completed_phases: 5
  total_plans: 11
  completed_plans: 11
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-26)

**Core value:** 模块清晰可替换 — 新用户 fork 后替换 tools/、prompts/、middleware.py、config.py 即可搭建自己的 Agent 服务
**Current focus:** ad-hoc voice gateway TTS state merge

## Current Position

Phase: ad-hoc
Plan: 2026-05-25-voice-gateway-tts-state-merge-quick

## Performance Metrics

**Velocity:**

| Phase 01-safety-net P01 | 4min | 2 tasks | 7 files |
| Phase 01-safety-net P02 | 3min | 2 tasks | 2 files |
| Phase 01-safety-net P03 | 11min | 2 tasks | 3 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Phase 02 discuss]: 架构方向从 agent_core/ + projects/ 双层改为单项目内模块拆分
- [Phase 02 discuss]: globals() 共享 — extract_data/fig_inter/python_inter 保持同文件
- [Phase 02 discuss]: 模块级 DB 连接改延迟初始化
- [Phase 02 discuss]: 子 Agent 移入 tools/subagent/
- [Phase 01-safety-net]: pytest_configure hook for pre-import patching
- [Phase 01-safety-net]: FakeToolCallingModel(BaseChatModel) with bind_tools for agent testing
- [Phase 01-safety-net]: LangChain 1.0 compat shims (sys.modules injection) in conftest
- [Phase 02-modular-restructure]: Module-level constants only in config.py (no class/dataclass/pydantic-settings)
- [Phase 02-modular-restructure]: Moved langchain_core.messages import to module-level in utils.py
- [Phase 02-modular-restructure]: Kept load_dotenv in app.py and tools.py since they still read non-config env vars (DB secrets, Phase 3+ scope)
- [Phase 02-modular-restructure]: Used db_save_path=None with FAISS_PATH fallback for backward compat in create_knowledge_base()
- [Phase 02-modular-restructure]: Removed re, ast, typing imports from tools.py after utility function extraction
- [Phase 03-tools-modularization]: fault_explanation_tool in __init__.py with lazy subagent import (avoids module-level DB init)
- [Phase 03-tools-modularization]: python_inter excluded from tools/ package (dead code not in tools list)
- [Phase 03-tools-modularization]: Lazy singleton pattern (_db=None + _get_db()) for SQL tools deferred initialization
- [Phase 03]: Subagent DB connection independent from tools/sql_tools.py (separate _get_db singleton)
- [Phase 03]: get_tools() deferred loading pattern for subagent sqltools instead of module-level list
- [Phase 03]: conftest.py patches unchanged -- library-level patching works with new tools/ structure
- [Phase 03]: Removed unused imports (matplotlib, seaborn, pandas, sqlalchemy, ast, re) from app.py alongside tool definitions
- [Phase 04-02]: KB build parameters (chunk_size, chunk_overlap, batch_size) added to config.py with env-var overrides
- [Phase 04-prompts-middleware-kb]: Dead code deletion: removed ~70 lines of commented-out identity prompt from old prompt_template.py
- [Phase 04-prompts-middleware-kb]: build_middleware accepts summary_model param to keep model creation in app.py lifespan
- [Phase 05-app-slim-integration]: Removed 11 unused imports from app.py; streaming.py is the final extracted module completing monolith breakup
- [2026-04-27 quick]: Added session-bound admin password auth (`/auth/admin/login`, `/auth/identity`, `/auth/logout`) and server-side PDF registry (`/admin/pdfs*`) so PDF upload can be tested without the unfinished voice auth path
- [2026-04-27 debug]: `medicineOCR` 当前并非完整 PDF OCR 流水线，而是分散的 OCR / 签名提取 / Markdown 转 PDF 脚本；接入时必须通过可探测、可降级的服务封装而不是直接内嵌脚本
- [2026-04-27 debug]: 上传 PDF 知识库与现有主 `faiss_db` 解耦；当 Ollama / 向量化不可用时，先落 `lexical_corpus` 语料底座，避免 OCR 结果只停留在临时文件
- [2026-05-12 quick]: 报告链接渲染清洗：`linkifyReportMentions` → `stripReportMentions`，消除正文中 `<a>` 标签/HTML 属性残片的双重渲染问题；新增 `isSafeReportUrl()` 安全校验；修复 `extractReportLinks` 模块级 `gi` 正则 `lastIndex` 状态泄漏
- [2026-05-24 quick]: PDF 上传接口允许管理员 cookie 或已识别非访客身份访问；上传窗口携带当前身份上下文；诊断摘要详情按钮固定尺寸；桌宠来源跳转后自动启动语音提问。
- [2026-05-25 quick]: Voice gateway TTS state merge keeps ASR text on the normal chat stream while restoring gateway playback state fallbacks for `speaking`, `tts_audio_chunk`, and `tts_playback_end`.

### Pending Todos

None yet.

### Blockers/Concerns

- **globals() 耦合**: extract_data/fig_inter/python_inter 通过 globals() 共享命名空间，拆分时必须保持在同一文件
- **模块级 DB 连接**: tools.py:32-48 在 import 时创建 MySQL 连接，需在 Phase 3 改为延迟初始化
- **测试 mock 路径**: Phase 3 移动文件后，conftest.py 中的 mock patch 路径需要同步更新
- **管理员认证仍是最小测试方案**: 当前为单一管理员账号 + session 绑定 cookie，适合内部联调；声纹认证与正式权限模型仍待后续接回
- **medicineOCR 运行条件不满足**: 当前环境缺少 `modelscope` / `opencv-python` / `ultralytics` / `PyMuPDF`，且仓库内没有 DeepSeek OCR 模型目录，`ocr_test.py` 还强依赖 CUDA；必须先做可用性探测和降级路径
- **报告链接双重渲染已消除**: 前端正文不再生成内联 `<a>` 报告链接，统一由按钮组件渲染；旧历史消息含 HTML 标签时由 `stripReportMentions` 清洗

## Session Continuity

Last session: 2026-03-27T01:28:36.409Z
Stopped at: Completed 05-01-PLAN.md
Resume file: None
