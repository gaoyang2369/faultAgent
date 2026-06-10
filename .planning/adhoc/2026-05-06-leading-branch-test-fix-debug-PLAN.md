# Leading Branch Test Fix Debug Plan

## Context

The leading local branch is `integration-medicine-ocr-pdf-kb`. Full regression did not complete within 120 seconds and showed failures/errors. The first isolated failure is in `tests/test_data_tools.py`, where `fig_inter` succeeds functionally but returns an English success string without the expected Chinese/status marker.

## Scope

- Inspect `fig_inter` return contracts and affected tests.
- Restore status-prefixed Chinese user-facing strings while preserving machine-readable fields such as `frontend=/images/...`.
- Run focused tests first, then broader regression as time allows.

## Non-goals

- No branch deletion until tests are healthy.
- No backend protocol or tool artifact schema changes.
