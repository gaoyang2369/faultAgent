---
phase: 2
slug: modular-restructure
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-26
---

# Phase 2 — Validation Strategy

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
| 02-01-01 | 01 | 1 | CONF-01 | unit | `pytest tests/test_config.py -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | CONF-02 | unit | `pytest tests/test_utils.py -x` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | CONF-03 | integration | `pytest tests/ -x -q` | ✅ existing | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_config.py` — import config.py, verify all 8 constants have expected defaults, verify env var override works
- [ ] `tests/test_utils.py` — import utils.py, verify sanitize_for_json/safe_json_dumps/parse_todos_from_tool_output with representative inputs

*Existing 22 tests in tests/ cover CONF-03 integration verification.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
