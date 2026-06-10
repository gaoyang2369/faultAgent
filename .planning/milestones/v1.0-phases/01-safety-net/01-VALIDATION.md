---
phase: 1
slug: safety-net
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-26
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | pytest.ini (Wave 0 installs) |
| **Quick run command** | `pytest tests/ -x -q` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 1-01-01 | 01 | 1 | SAFE-01 | integration | `pytest tests/test_sse_stream.py -v` | ❌ W0 | ⬜ pending |
| 1-01-02 | 01 | 1 | SAFE-02 | integration | `pytest tests/test_tool_calls.py -v` | ❌ W0 | ⬜ pending |
| 1-01-03 | 01 | 1 | SAFE-03 | integration | `pytest tests/test_history_api.py -v` | ❌ W0 | ⬜ pending |
| 1-01-04 | 01 | 1 | SAFE-04 | smoke | `pytest tests/test_smoke.py -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/conftest.py` — shared fixtures (monkeypatch for module-level DB, FakeToolCallingModel, mock checkpointer)
- [ ] `pytest.ini` — pytest configuration
- [ ] `pip install pytest pytest-asyncio` — framework install

*If none: "Existing infrastructure covers all phase requirements."*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| API key rotation | SAFE-04 | Requires access to external API key management | Verify rotated keys work by testing LLM API call |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
