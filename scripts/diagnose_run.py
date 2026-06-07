"""Summarize a SolidWorks MCP debug run directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solidworks_mcp.run_diagnostics import diagnose_run_directory


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for offline run diagnosis."""

    parser = argparse.ArgumentParser(description="Diagnose a solidworks-mcp run directory.")
    parser.add_argument("run_dir", help="Path to a run_<timestamp>_<id> directory.")
    parser.add_argument(
        "--tail",
        type=int,
        default=12,
        help="Number of final events to include in the summary.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only the production verdict and compact acceptance summary.",
    )
    return parser.parse_args()


def main() -> int:
    """Read debug artifacts and print a diagnosis summary."""

    args = parse_args()
    payload = diagnose_run_directory(
        args.run_dir,
        tail=args.tail,
        summary_only=args.summary_only,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
