from __future__ import annotations

import json

from tests.evals.canonical_output_regressions import assert_regressions, run_regressions


def main() -> int:
    results = run_regressions()
    assert_regressions(results)
    print(json.dumps({"status": "passed", "cases": results}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
