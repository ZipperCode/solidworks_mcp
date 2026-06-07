"""Summarize SolidWorks MCP debug run directories below a root."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solidworks_mcp.run_diagnostics import diagnose_run_collection


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for offline batch diagnosis."""

    parser = argparse.ArgumentParser(description="Diagnose solidworks-mcp run directories below a root.")
    parser.add_argument("root_dir", help="Directory containing one or more run directories.")
    parser.add_argument(
        "--tail",
        type=int,
        default=12,
        help="Number of final events to include per run when full diagnosis is requested.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only compact per-run verdicts.",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=0,
        help="Maximum newest run directories to diagnose. Use 0 for the production default: a complete unbounded scan.",
    )
    return parser.parse_args()


def main() -> int:
    """Read debug artifacts and print a batch diagnosis summary."""

    args = parse_args()
    payload = diagnose_run_collection(
        args.root_dir,
        tail=args.tail,
        summary_only=args.summary_only,
        max_runs=args.max_runs,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
