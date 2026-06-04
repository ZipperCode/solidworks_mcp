"""Runtime configuration for local SolidWorks automation.

The server is designed to be developed on macOS or Linux with a mock adapter and
to run against a real SolidWorks COM session on Windows.  Environment variables
are used instead of a committed local config file so private template paths and
output directories do not leak into the repository.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform


@dataclass(frozen=True)
class SolidWorksMCPConfig:
    """Configuration values shared by the MCP tools and CAD adapters."""

    adapter: str
    output_root: Path
    part_template: str | None
    drawing_template: str | None
    visible: bool
    macro_fallback_enabled: bool
    debug_level: str
    run_id: str | None

    @classmethod
    def from_env(cls) -> "SolidWorksMCPConfig":
        """Create configuration from environment variables.

        ``SOLIDWORKS_MCP_ADAPTER`` accepts ``auto``, ``mock`` or ``solidworks``.
        The default ``auto`` mode uses the real adapter on Windows and the mock
        adapter elsewhere, which keeps repository development possible on macOS.
        """

        requested_adapter = os.getenv("SOLIDWORKS_MCP_ADAPTER", "auto").strip().lower()
        if requested_adapter == "auto":
            adapter = "solidworks" if platform.system() == "Windows" else "mock"
        elif requested_adapter in {"mock", "solidworks"}:
            adapter = requested_adapter
        else:
            raise ValueError("SOLIDWORKS_MCP_ADAPTER must be one of: auto, mock, solidworks")

        output_root = Path(os.getenv("SOLIDWORKS_MCP_OUTPUT_DIR", "outputs")).expanduser()
        visible = os.getenv("SOLIDWORKS_MCP_VISIBLE", "1").strip() not in {"0", "false", "False"}
        debug_level = os.getenv("SOLIDWORKS_MCP_DEBUG_LEVEL", "basic").strip().lower()
        if debug_level not in {"basic", "verbose"}:
            raise ValueError("SOLIDWORKS_MCP_DEBUG_LEVEL must be basic or verbose")

        return cls(
            adapter=adapter,
            output_root=output_root,
            part_template=os.getenv("SOLIDWORKS_MCP_PART_TEMPLATE") or None,
            drawing_template=os.getenv("SOLIDWORKS_MCP_DRAWING_TEMPLATE") or None,
            visible=visible,
            macro_fallback_enabled=os.getenv("SOLIDWORKS_MCP_MACRO_FALLBACK", "0").strip()
            in {"1", "true", "True"},
            debug_level=debug_level,
            run_id=os.getenv("SOLIDWORKS_MCP_RUN_ID") or None,
        )
