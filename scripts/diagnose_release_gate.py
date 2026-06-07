"""Verify an archived SolidWorks MCP release-gate report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solidworks_mcp.release_diagnostics import diagnose_release_gate_report


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for release-gate diagnosis."""

    parser = argparse.ArgumentParser(description="Diagnose a release_gate_report.json without touching SolidWorks.")
    parser.add_argument("report_file", help="Path to release_gate_report.json.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Include the archived report and fresh batch diagnosis payloads.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print the compact release-gate diagnosis. This is the default and is accepted for script parity.",
    )
    return parser.parse_args()


def main() -> int:
    """Print the release-gate diagnosis and return a CI-friendly status code."""

    args = parse_args()
    payload = diagnose_release_gate_report(args.report_file, summary_only=not args.full)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
