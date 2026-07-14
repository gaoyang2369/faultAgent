# Phase 0 V2-native red baseline

Canonical cutover completed. This directory is retained as a historical regression set.

The Canonical Turn Phase 4 cutover closed every originally assigned gap. These
files remain as the historical Phase 0 acceptance set:

```bash
PYTHONPATH=. pytest -q tests/red_baseline --tb=no
```

The expected failure list is empty and all 23 cases must pass. Stable Phase 4
output behavior is also collected by default in
`tests/test_canonical_output_phase4.py`; this directory is no longer the only
coverage for the original Case A-D and dependency/composite defects.
