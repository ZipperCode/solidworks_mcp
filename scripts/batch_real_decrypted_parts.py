# /// script
# requires-python = ">=3.11"
# ///

# --- How to run ---
# uv run --no-sync python scripts/batch_real_decrypted_parts.py --source-dir C:\Users\Zipper\Downloads\解密3D

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solidworks_mcp.decrypted_parts_batch import main


if __name__ == "__main__":
    raise SystemExit(main())
