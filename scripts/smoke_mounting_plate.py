"""Manual Windows smoke test for the mounting plate SolidWorks workflow.

This script is intentionally not a unit test.  It gives a Windows operator one
repeatable command that validates the MCP execution stack, runs the confirmed
mounting plate plan and prints the generated artifact paths.
"""

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


def parse_args() -> argparse.Namespace:
    """Parse smoke-test command line arguments."""

    parser = argparse.ArgumentParser(description="Run the SolidWorks mounting plate smoke workflow.")
    parser.add_argument(
        "--plan",
        default=str(ROOT / "examples" / "mounting_plate_plan.json"),
        help="Path to the mounting plate model plan JSON file.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Force the mock adapter for local dry-runs outside Windows.",
    )
    return parser.parse_args()


def main() -> int:
    """Run connection, validation and confirmed execution for the smoke plan."""

    args = parse_args()
    plan_path = Path(args.plan).expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    config = SolidWorksMCPConfig.from_env()
    if args.mock:
        config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=config.output_root,
            part_template=config.part_template,
            drawing_template=config.drawing_template,
            visible=config.visible,
            macro_fallback_enabled=config.macro_fallback_enabled,
            debug_level=config.debug_level,
            run_id=config.run_id,
        )

    executor = ModelPlanExecutor(create_adapter(config), config)
    connection = executor.connect()
    validation = executor.validate_plan(plan).to_dict()
    if not validation["ok"]:
        print(json.dumps({"connection": connection, "validation": validation}, indent=2))
        return 2

    execution = executor.execute_plan(plan, confirmed=True).to_dict()
    print(json.dumps({
        "connection": connection,
        "validation": validation,
        "execution": {
            "ok": execution["ok"],
            "message": execution["message"],
            "report_file": execution["report_file"],
            "run_id": execution["run_id"],
            "run_dir": execution["run_dir"],
            "diagnostics": execution["diagnostics"],
            "output_files": execution["output_files"],
            "preview_files": execution["preview_files"],
            "diagnose_command": f"python scripts/diagnose_run.py {execution['run_dir']}",
        },
    }, indent=2))
    return 0 if execution["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
