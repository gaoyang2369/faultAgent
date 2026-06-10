---
phase: 3
slug: tools-modularization
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-26
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (installed in conda faultagent env) |
| **Config file** | pytest.ini at project root |
| **Quick run command** | `pytest tests/ -x -q` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | TOOL-01 | integration | `pytest tests/ -x -q` | existing | pending |
| 03-01-02 | 01 | 1 | TOOL-02 | unit | `pytest tests/test_lazy_init.py -x` | Plan 03 Task 2 | pending |
| 03-01-03 | 01 | 1 | TOOL-03 | integration | `pytest tests/ -x -q` | existing | pending |
| 03-01-04 | 01 | 1 | TOOL-04 | integration | `pytest tests/ -x -q` | existing | pending |
| 03-01-05 | 01 | 1 | TOOL-05 | integration | `pytest tests/ -x -q` | existing | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [x] `tests/test_lazy_init.py` — Created by Plan 03 Task 2. Verifies `from tools import tools` works without live MySQL/Ollama, verifies get_sqltools() lazy singleton behavior. Plan 03 Task 2 `<automated>` block runs `pytest tests/test_lazy_init.py -x -v` as TOOL-02 proof.

*Existing 74 tests in tests/ cover integration verification for TOOL-01/03/04/05.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 10s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved
