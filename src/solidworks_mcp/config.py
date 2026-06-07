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
    macro_execution_disabled: bool
    force_holewizard_failure: bool
    force_drawing_callout_failure: bool
    force_drawing_dimension_failure: bool
    force_cad_content_failure: bool
    force_cleanup_failure: bool
    force_material_failure: bool
    force_preflight_failure: bool
    enforce_trusted_workflow: bool
    require_direct_hole_callout: bool
    close_documents_after_run: bool
    cleanup_attach_only: bool
    debug_level: str
    run_id: str | None
    force_export_failure: bool = False
    force_model_geometry_failure: bool = False

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

        output_root = Path(os.getenv("SOLIDWORKS_MCP_OUTPUT_DIR", "outputs")).expanduser().resolve()
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
            macro_fallback_enabled=os.getenv("SOLIDWORKS_MCP_MACRO_FALLBACK", "1").strip()
            in {"1", "true", "True"},
            macro_execution_disabled=os.getenv("SOLIDWORKS_MCP_DISABLE_MACRO_EXECUTION", "0").strip()
            in {"1", "true", "True"},
            force_holewizard_failure=os.getenv("SOLIDWORKS_MCP_FORCE_HOLEWIZARD_FAILURE", "0").strip()
            in {"1", "true", "True"},
            force_drawing_callout_failure=os.getenv("SOLIDWORKS_MCP_FORCE_DRAWING_CALLOUT_FAILURE", "0").strip()
            in {"1", "true", "True"},
            force_drawing_dimension_failure=os.getenv("SOLIDWORKS_MCP_FORCE_DRAWING_DIMENSION_FAILURE", "0").strip()
            in {"1", "true", "True"},
            force_cad_content_failure=os.getenv("SOLIDWORKS_MCP_FORCE_CAD_CONTENT_FAILURE", "0").strip()
            in {"1", "true", "True"},
            force_cleanup_failure=os.getenv("SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE", "0").strip()
            in {"1", "true", "True"},
            force_material_failure=os.getenv("SOLIDWORKS_MCP_FORCE_MATERIAL_FAILURE", "0").strip()
            in {"1", "true", "True"},
            force_preflight_failure=os.getenv("SOLIDWORKS_MCP_FORCE_PREFLIGHT_FAILURE", "0").strip()
            in {"1", "true", "True"},
            enforce_trusted_workflow=os.getenv("SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW", "1").strip()
            not in {"0", "false", "False"},
            require_direct_hole_callout=os.getenv("SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT", "1").strip()
            in {"1", "true", "True"},
            close_documents_after_run=os.getenv("SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN", "1").strip()
            not in {"0", "false", "False"},
            cleanup_attach_only=os.getenv("SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY", "1").strip()
            not in {"0", "false", "False"},
            debug_level=debug_level,
            run_id=os.getenv("SOLIDWORKS_MCP_RUN_ID") or None,
            force_export_failure=os.getenv("SOLIDWORKS_MCP_FORCE_EXPORT_FAILURE", "0").strip()
            in {"1", "true", "True"},
            force_model_geometry_failure=os.getenv("SOLIDWORKS_MCP_FORCE_MODEL_GEOMETRY_FAILURE", "0").strip()
            in {"1", "true", "True"},
        )
