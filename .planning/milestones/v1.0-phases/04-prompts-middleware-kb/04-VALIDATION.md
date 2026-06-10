---
phase: 4
slug: prompts-middleware-kb
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-26
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio |
| **Config file** | pytest.ini |
| **Quick run command** | `pytest tests/ -x -q` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | PROM-01 | integration | `pytest tests/ -x -q` | ✅ | ⬜ pending |
| 04-01-02 | 01 | 1 | PROM-02 | integration | `pytest tests/ -x -q` | ✅ | ⬜ pending |
| 04-01-03 | 01 | 1 | PROM-03 | integration | `pytest tests/ -x -q` | ✅ | ⬜ pending |
| 04-02-01 | 02 | 1 | KBAS-01 | integration | `pytest tests/ -x -q` | ✅ | ⬜ pending |
| 04-02-02 | 02 | 1 | KBAS-02 | integration | `pytest tests/ -x -q` | ✅ | ⬜ pending |
| 04-02-03 | 02 | 1 | KBAS-03 | integration | `pytest tests/ -x -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. pytest + conftest.py mock infrastructure already in place from Phase 1.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| rebuild_kb.py works with new structure | KBAS-03 | Requires Ollama connection | Run `python rebuild_kb.py` in environment with Ollama access |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
