# Retrospective

Living document — updated at each milestone completion.

## Milestone: v1.0 — AI Agent 后端模块化重组

**Shipped:** 2026-03-27
**Phases:** 5 | **Plans:** 11 | **Tasks:** 22

### What Was Built
- 22 个特征测试安全网（SSE、工具调用、History API、冒烟测试）
- config.py 集中管理 11 个常量 + utils.py 提取 7 个通用函数
- tools/ 目录（5 个领域模块 + subagent/），全部延迟初始化
- prompts/ 目录 + middleware.py 中间件组装
- streaming.py SSE 生成器提取，app.py 从 592 行瘦身到 256 行

### What Worked
- **Phase 1 安全网贯穿全程**：76 个测试从 Phase 1 到 Phase 5 始终全绿，每次重构后立即验证
- **延迟初始化策略**：懒加载单例模式让测试无需真实 DB 连接，大幅简化 mock
- **渐进式拆分**：每个 Phase 结束系统可运行，从不破坏现有功能
- **CONTEXT.md 上下文传递**：用户决策在 discuss-phase 阶段捕获，下游 agent 不重复提问

### What Was Inefficient
- **Phase 4 ROADMAP 状态未完全同步**：Phase 4 的 checkbox 和 status 在执行后未立即标记完成
- **conda 环境不可用**：验证 agent 无法运行 pytest，依赖执行 agent 的测试结果

### Patterns Established
- **提取模式**：从 app.py 提取 → 创建新文件 → app.py import → 删除旧代码 → 跑测试
- **config.py 风格**：模块级常量 + os.getenv() 带默认值，按功能分组
- **懒加载单例**：`_db = None` + `_get_db()` 函数，首次调用时初始化
- **工具定义模式**：Pydantic BaseModel schema + @tool(args_schema=...) + 中文 docstring

### Key Lessons
- 特征测试是重构的基础设施，值得在第一个 Phase 投入
- 简单的模块拆分（不做框架抽象）足以实现"fork 后替换"的目标
- globals() 共享命名空间是硬约束，需要在架构决策中明确标注

### Cost Observations
- Model mix: 主要使用 Opus 4.6
- Sessions: ~6 sessions (discuss + plan + execute per phase)
- Notable: Phase 5 最简洁 — 跳过研究，1 个 plan，2 个 task，4 分钟执行

---

## Cross-Milestone Trends

| Metric | v1.0 |
|--------|------|
| Phases | 5 |
| Plans | 11 |
| Tasks | 22 |
| Tests | 76 |
| Duration | 1 day |
