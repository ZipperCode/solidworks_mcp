"""Close SolidWorks documents that belong to a completed run directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solidworks_mcp.adapters import create_adapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.executor import ModelPlanExecutor


SUCCESS_STATUSES = {"completed", "skipped_no_documents"}
SUCCESS_VERIFICATION_STATUSES = {"verified", "not_applicable"}


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for post-run document cleanup."""

    parser = argparse.ArgumentParser(
        description=(
            "Close open SolidWorks SLDPRT/SLDDRW documents declared by a completed "
            "solidworks-mcp run directory. Candidates are path-guarded against run_dir, "
            "and the real adapter attaches to an existing SolidWorks session by default."
        )
    )
    parser.add_argument("run_dir", help="Path to a completed run_<timestamp>_<id> directory.")
    return parser.parse_args()


def main() -> int:
    """Run the adapter cleanup tool and print structured JSON."""

    args = parse_args()
    config = SolidWorksMCPConfig.from_env()
    executor = ModelPlanExecutor(create_adapter(config), config)
    payload = executor.cleanup_run_documents(args.run_dir)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if payload.get("status") not in SUCCESS_STATUSES:
        return 1
    if payload.get("cleanup_verification_status") not in SUCCESS_VERIFICATION_STATUSES:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
