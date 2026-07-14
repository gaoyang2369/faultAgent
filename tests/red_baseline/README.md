# Phase 0 V2-native red baseline

These files are intentionally outside the default `pytest.ini` filename pattern.
They must be run explicitly while Phase 0 is red:

```bash
PYTHONPATH=. pytest -q tests/red_baseline/phase0_v2_native_red.py \
  tests/red_baseline/phase0_report_source_property_red.py \
  tests/red_baseline/phase0_architecture_invariants_red.py
```

The baseline may fail only on the named semantic and architecture assertions.
Collection errors, import errors, fixture errors, mock errors and environment
errors invalidate the baseline. Default `PYTHONPATH=. pytest -q` must remain
green until later phases intentionally move these files into normal discovery.
The exact Phase 0 node IDs and semantic failure reasons are frozen in
`expected_failures.json`.
