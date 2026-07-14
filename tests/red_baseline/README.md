# Phase 0 V2-native red baseline

After the Phase 2 cutover these explicitly collected files retain only assigned
Phase 3/4 gaps:

```bash
PYTHONPATH=. pytest -q tests/red_baseline --tb=no
```

The baseline may fail only on the named semantic and architecture assertions.
Collection errors, import errors, fixture errors, mock errors and environment
errors invalidate the baseline. The normal regression command must use
`--ignore=tests/red_baseline` until the assigned later phases turn these cases green.
The shrinking list, target phase, and precise reasons are recorded in
`expected_failures.json`; fixed Phase 2 cases also live in normal regression tests.
