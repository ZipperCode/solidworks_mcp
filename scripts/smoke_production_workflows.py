"""Production smoke entrypoint for all trusted SolidWorks MCP workflows.

This module keeps the original smoke_mounting_plate CLI stable while exposing a
workflow-neutral command name for production and CI usage.
"""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from smoke_mounting_plate import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
