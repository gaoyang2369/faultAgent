#!/usr/bin/env python3
"""Render Agent trace JSONL entries into developer-friendly views."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fault_diagnosis.platform.observability.trace_rendering import (
    DEFAULT_OUT_DIR,
    DEFAULT_TRACE_PATH,
    render_compact_summary,
    select_trace_record,
    write_markdown,
    write_pretty_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render an Agent trace.")
    parser.add_argument("path", nargs="?", default="", help="Trace JSONL or JSON path.")
    parser.add_argument("--latest", action="store_true", help="Select the latest entry from the default JSONL trace file.")
    parser.add_argument("--format", choices=("summary", "markdown", "pretty-json"), default="summary")
    parser.add_argument("--trace-id", default="", help="Trace ID to select from JSONL.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory for markdown/pretty-json.")
    args = parser.parse_args()

    trace_path = DEFAULT_TRACE_PATH if args.latest or not args.path else Path(args.path)
    record = select_trace_record(trace_path, trace_id=args.trace_id, latest=True)
    if args.format == "summary":
        print(render_compact_summary(record, trace_path=str(trace_path)))
        return 0

    out_dir = Path(args.out_dir)
    if args.format == "pretty-json":
        print(write_pretty_json(record, out_dir=out_dir))
        return 0

    print(write_markdown(record, out_dir=out_dir, trace_path=str(trace_path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
