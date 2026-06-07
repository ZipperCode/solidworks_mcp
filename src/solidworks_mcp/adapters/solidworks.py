"""Windows SolidWorks COM adapter.

The implementation keeps direct COM calls narrow and guarded.  Complex feature
creation varies across SolidWorks versions, so the MVP records exact failure
context and leaves room for a future VBA macro fallback for methods whose COM
signatures are unstable from Python.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import platform
import struct
from time import perf_counter
from typing import Any

from solidworks_mcp.adapters.base import CADAdapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.feature_graph import atomic_dimension_ids_from_metadata
from solidworks_mcp.schemas import (
    bom_assembly_parameters_from_plan,
    bracket_basic_dimension_ids,
    bracket_basic_dimension_ids_from_plan,
    bracket_parameters_from_plan,
    center_hole_flange_basic_dimension_ids,
    center_hole_flange_basic_dimension_ids_from_plan,
    center_hole_flange_parameters_from_plan,
    center_hole_plate_basic_dimension_ids,
    center_hole_plate_basic_dimension_ids_from_plan,
    center_hole_plate_parameters_from_plan,
    DrawingProfile,
    end_cap_basic_dimension_ids,
    end_cap_basic_dimension_ids_from_plan,
    end_cap_parameters_from_plan,
    ModelOperation,
    ModelPlan,
    StepResult,
    mounting_block_basic_dimension_ids,
    mounting_block_basic_dimension_ids_from_plan,
    mounting_block_parameters_from_plan,
    mounting_plate_basic_dimension_ids,
    mounting_plate_basic_dimension_ids_from_plan,
    mounting_plate_parameters_from_plan,
    path_to_string,
    safe_output_name,
    shaft_basic_dimension_ids,
    shaft_basic_dimension_ids_from_plan,
    shaft_parameters_from_plan,
    sheet_metal_base_flange_basic_dimension_ids,
    sheet_metal_base_flange_basic_dimension_ids_from_plan,
    sheet_metal_base_flange_parameters_from_plan,
    sleeve_basic_dimension_ids,
    sleeve_basic_dimension_ids_from_plan,
    sleeve_parameters_from_plan,
    slotted_array_plate_basic_dimension_ids,
    slotted_array_plate_basic_dimension_ids_from_plan,
    slotted_array_plate_parameters_from_plan,
    static_simulation_basic_dimension_ids,
    static_simulation_basic_dimension_ids_from_plan,
    static_simulation_parameters_from_plan,
    washer_basic_dimension_ids,
    washer_basic_dimension_ids_from_plan,
    washer_parameters_from_plan,
    weldment_frame_basic_dimension_ids,
    weldment_frame_basic_dimension_ids_from_plan,
    weldment_frame_parameters_from_plan,
)


SW_DOC_PART = 1
SW_DOC_ASSEMBLY = 2
SW_DOC_DRAWING = 3
SW_SAVE_AS_CURRENT_VERSION = 0
SW_SAVE_AS_OPTIONS_SILENT = 1
SW_END_COND_BLIND = 0
SW_END_COND_THROUGH_ALL = 1
SW_WZD_TAP = 4
SW_STANDARD_ISO = 8
SW_STANDARD_ISO_TAPPED_HOLE = 147
SW_RUN_MACRO_UNLOAD_AFTER_RUN = 1
SW_SEL_EDGES = 1
SW_VIEW_ENTITY_EDGE = 1
SW_SOLID_BODY = 0
SW_CONST_RADIUS_FILLET = 0
SW_FILLET_OVERFLOW_DEFAULT = 0
SW_FILLET_OPTIONS_MVP = 195
SW_INSERT_HOLE_CALLOUT = 1048576
SW_INSERT_DIMENSIONS = 8
SW_RADIAL_DIMENSION = 5
SW_CURVE_TYPE_CIRCLE = 3002
SW_CURVE_TYPE_TRIMMED = 3009
SW_REF_PLANE_DISTANCE = 8
SW_FM_BASE_FLANGE = 34
SW_EXPORT_TO_DWG_EXPORT_SHEET_METAL = 1
SW_SHEET_METAL_EXPORT_FLAT_PATTERN_GEOMETRY = 1
SW_SHEET_METAL_EXPORT_BEND_LINES = 4
SW_SHEET_METAL_EXPORT_SKETCHES = 8
SW_SHEET_METAL_EXPORT_OPTIONS = (
    SW_SHEET_METAL_EXPORT_FLAT_PATTERN_GEOMETRY
    | SW_SHEET_METAL_EXPORT_BEND_LINES
    | SW_SHEET_METAL_EXPORT_SKETCHES
)

ISO_METRIC_COARSE_THREADS = {
    "M3": {"tap_drill_diameter": 2.5, "pitch": 0.5},
    "M4": {"tap_drill_diameter": 3.3, "pitch": 0.7},
    "M5": {"tap_drill_diameter": 4.2, "pitch": 0.8},
    "M6": {"tap_drill_diameter": 5.0, "pitch": 1.0},
    "M8": {"tap_drill_diameter": 6.8, "pitch": 1.25},
}

MATERIAL_ALIASES = {
    "plain carbon steel": ["普通碳钢"],
}


class SolidWorksCOMAdapter(CADAdapter):
    """Adapter that drives a local Windows SolidWorks session through COM."""

    name = "solidworks"

    def __init__(self, config: SolidWorksMCPConfig) -> None:
        self._config = config
        self._sw: Any | None = None
        self._model: Any | None = None
        self._drawing: Any | None = None
        self._workspace: Path | None = None
        self._features: list[dict[str, Any]] = []
        self._fallbacks: list[dict[str, Any]] = []
        self._warnings: list[str] = []
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_view_status = "not_requested"
        self._drawing_view_result: dict[str, Any] = {"status": "not_requested", "views": [], "errors": []}
        self._drawing_annotation_status = "not_requested"
        self._drawing_annotation_result: dict[str, Any] = {"status": "not_requested"}
        self._drawing_dimension_status = "not_requested"
        self._drawing_dimension_result: dict[str, Any] = {"status": "not_requested"}
        self._drawing_metadata_note_result: dict[str, Any] = {"status": "not_requested"}
        self._material_status = "not_requested"
        self._material_result: dict[str, Any] = {"status": "not_requested"}
        self._custom_property_status = "not_requested"
        self._custom_property_result: dict[str, Any] = {"status": "not_requested"}
        self._model_geometry_status = "not_requested"
        self._model_geometry_result: dict[str, Any] = {"status": "not_requested"}
        self._mass_property_status = "not_requested"
        self._mass_property_result: dict[str, Any] = {"status": "not_requested"}
        self._export_result: dict[str, Any] = {"status": "not_requested", "formats": [], "exported": [], "failed": []}
        self._assembly_result: dict[str, Any] = {"status": "not_requested"}
        self._bom_result: dict[str, Any] = {"status": "not_requested"}
        self._sheet_metal_result: dict[str, Any] = {"status": "not_requested"}
        self._weldment_result: dict[str, Any] = {"status": "not_requested"}
        self._cut_list_result: dict[str, Any] = {"status": "not_requested"}
        self._simulation_result: dict[str, Any] = {"status": "not_requested"}
        self._active_plan: ModelPlan | None = None
        self._active_part_path: Path | None = None
        self._active_drawing_path: Path | None = None
        self._active_part_title: str | None = None
        self._active_drawing_title: str | None = None
        self._last_hole_result: dict[str, Any] | None = None
        self._last_hole_points: list[list[float]] = []
        self._last_hole_features: list[Any] = []
        self._drawing_view_handles: dict[str, Any] = {}
        self._atomic_references: dict[str, dict[str, Any]] = {}
        self._atomic_reference_objects: dict[str, list[Any]] = {}
        self._atomic_sketch_count = 0
        self._atomic_axis_count = 0
        self._solidworks_rpc_unavailable: str | None = None

    def record_com_call(
        self,
        method: str,
        parameters: dict[str, Any] | None,
        *,
        result: Any = None,
        error: Exception | str | None = None,
        started_at: float | None = None,
    ) -> None:
        """Record COM calls and remember fatal SolidWorks RPC failures."""

        super().record_com_call(method, parameters, result=result, error=error, started_at=started_at)
        if error is not None and _is_solidworks_rpc_failure(error):
            self._solidworks_rpc_unavailable = str(error)

    def connect(self) -> dict[str, Any]:
        """Connect to SolidWorks or raise a clear platform/setup error."""

        if platform.system() != "Windows":
            raise RuntimeError("The SolidWorks COM adapter can only run on Windows.")

        try:
            import win32com.client
        except ImportError as exc:
            raise RuntimeError("Install the windows extra: pip install 'solidworks-mcp[windows]'") from exc

        self._sw = win32com.client.Dispatch("SldWorks.Application")
        self._sw.Visible = self._config.visible
        revision_value = getattr(self._sw, "RevisionNumber", None)
        revision = revision_value() if callable(revision_value) else revision_value
        revision = revision or "unknown"
        return {
            "adapter": self.name,
            "connected": True,
            "revision": revision,
            "visible": self._config.visible,
        }

    def _connect_for_post_run_cleanup(self) -> dict[str, Any]:
        """Attach to SolidWorks for completed-run cleanup without starting it by default."""

        if self._sw is not None:
            return {
                "adapter": self.name,
                "connected": True,
                "attach_mode": "existing_adapter_session",
                "attach_only": self._config.cleanup_attach_only,
            }
        if not self._config.cleanup_attach_only:
            connection = self.connect()
            connection["attach_mode"] = "dispatch_may_start_solidworks"
            connection["attach_only"] = False
            return connection
        return self._attach_existing_solidworks()

    def _attach_existing_solidworks(self) -> dict[str, Any]:
        """Attach to an already-running SolidWorks application object."""

        if platform.system() != "Windows":
            raise RuntimeError("The SolidWorks COM adapter can only run on Windows.")

        try:
            import win32com.client
        except ImportError as exc:
            raise RuntimeError("Install the windows extra: pip install 'solidworks-mcp[windows]'") from exc

        started_at = perf_counter()
        try:
            self._sw = win32com.client.GetActiveObject("SldWorks.Application")
            self.record_com_call(
                "win32com.client.GetActiveObject",
                {"progid": "SldWorks.Application", "purpose": "post_run_cleanup_attach_only"},
                result=self._sw,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "win32com.client.GetActiveObject",
                {"progid": "SldWorks.Application", "purpose": "post_run_cleanup_attach_only"},
                error=exc,
                started_at=started_at,
            )
            raise RuntimeError(
                "No running SolidWorks application was found. Post-run cleanup is attach-only by default "
                "and will not start SolidWorks; open SolidWorks with the run documents still loaded or set "
                "SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY=0 to allow Dispatch for this remediation."
            ) from exc

        revision_value = getattr(self._sw, "RevisionNumber", None)
        revision = revision_value() if callable(revision_value) else revision_value
        return {
            "adapter": self.name,
            "connected": True,
            "revision": revision or "unknown",
            "visible": getattr(self._sw, "Visible", None),
            "attach_mode": "get_active_object",
            "attach_only": True,
        }

    def preflight_environment(self, plan: ModelPlan | None = None) -> dict[str, Any]:
        """Check SolidWorks runtime prerequisites without creating documents."""

        checks: list[dict[str, Any]] = []
        connection: dict[str, Any] | None = None
        sw: Any | None = None
        if self._config.force_preflight_failure:
            checks.append(
                {
                    "id": "forced_preflight_failure",
                    "ok": False,
                    "message": "SOLIDWORKS_MCP_FORCE_PREFLIGHT_FAILURE is enabled.",
                    "remediation": "Unset SOLIDWORKS_MCP_FORCE_PREFLIGHT_FAILURE.",
                }
            )

        try:
            connection = self.connect()
            sw = self._sw
            checks.append({"id": "solidworks_com", "ok": True, "message": "SolidWorks COM connected.", **connection})
        except Exception as exc:
            checks.append(
                {
                    "id": "solidworks_com",
                    "ok": False,
                    "message": str(exc),
                    "remediation": "Start SolidWorks and install the Windows extra with pywin32.",
                }
            )

        checks.append(_template_preflight_check("part_template", self._config.part_template, ".prtdot", sw))
        if plan is not None and _is_bom_assembly_plan(plan):
            checks.append(_template_preflight_check("assembly_template", None, ".asmdot", sw))
        if plan is not None and _is_weldment_frame_plan(plan):
            checks.append(_weldment_profile_preflight_check(plan))
        if plan is not None and _is_static_simulation_plan(plan):
            checks.append(_simulation_api_preflight_check(sw))
        drawing_template = self._config.drawing_template
        if plan is not None and plan.drawing_profile.template_path:
            drawing_template = plan.drawing_profile.template_path
        checks.append(_template_preflight_check("drawing_template", drawing_template, ".drwdot", sw))
        checks.append(_output_dir_preflight_check(self._config.output_root))
        checks.append(
            {
                "id": "cleanup_policy",
                "ok": self._config.close_documents_after_run,
                "message": "Run-created SolidWorks document cleanup is enabled."
                if self._config.close_documents_after_run
                else "SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN is disabled.",
                "remediation": None
                if self._config.close_documents_after_run
                else "Set SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN=1 before confirmed execution.",
            }
        )
        checks.append(
            {
                "id": "direct_hole_callout_policy",
                "ok": self._config.require_direct_hole_callout,
                "message": "Direct selected-edge Hole Callout enforcement is enabled."
                if self._config.require_direct_hole_callout
                else "SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT is disabled.",
                "remediation": None
                if self._config.require_direct_hole_callout
                else "Set SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT=1 before confirmed SolidWorks execution.",
            }
        )

        failed = [check["id"] for check in checks if not check.get("ok")]
        return {
            "ok": not failed,
            "status": "failed" if failed else "ready",
            "adapter": self.name,
            "plan_name": plan.name if plan else None,
            "checks": checks,
            "failures": failed,
            "templates": {
                "part": _selected_template_path(checks, "part_template"),
                "assembly": _selected_template_path(checks, "assembly_template"),
                "drawing": _selected_template_path(checks, "drawing_template"),
            },
        }

    def begin_transaction(self, plan: ModelPlan) -> dict[str, Any]:
        """Create a new part document for isolated execution."""

        sw = self._require_sw()
        self._workspace = getattr(self, "_run_workspace", None) or (
            self._config.output_root / safe_output_name(plan.name)
        )
        self._workspace.mkdir(parents=True, exist_ok=True)
        self.record_event("adapter.transaction", "started", {"workspace": self._workspace})
        self._features = []
        self._fallbacks = []
        self._warnings = []
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_view_status = "not_requested"
        self._drawing_view_result = {"status": "not_requested", "views": [], "errors": []}
        self._drawing_annotation_status = "not_requested"
        self._drawing_annotation_result = {"status": "not_requested"}
        self._drawing_dimension_status = "not_requested"
        self._drawing_dimension_result = {"status": "not_requested"}
        self._drawing_metadata_note_result = {"status": "not_requested"}
        self._material_status = "not_requested"
        self._material_result = {"status": "not_requested"}
        self._custom_property_status = "not_requested"
        self._custom_property_result = {"status": "not_requested"}
        self._model_geometry_status = "not_requested"
        self._model_geometry_result = {"status": "not_requested"}
        self._mass_property_status = "not_requested"
        self._mass_property_result = {"status": "not_requested"}
        self._export_result = {"status": "not_requested", "formats": [], "exported": [], "failed": []}
        self._assembly_result = {"status": "not_requested"}
        self._bom_result = {"status": "not_requested"}
        self._sheet_metal_result = {"status": "not_requested"}
        self._weldment_result = {"status": "not_requested"}
        self._cut_list_result = {"status": "not_requested"}
        self._simulation_result = {"status": "not_requested"}
        self._active_plan = plan
        self._active_part_path = None
        self._active_drawing_path = None
        self._active_part_title = None
        self._active_drawing_title = None
        self._last_hole_result = None
        self._last_hole_points = []
        self._last_hole_features = []
        self._drawing_view_handles = {}
        self._atomic_references = {}
        self._atomic_reference_objects = {}
        self._atomic_sketch_count = 0
        self._atomic_axis_count = 0
        self._solidworks_rpc_unavailable = None

        self._model = self._new_assembly_document(sw) if _is_bom_assembly_plan(plan) else self._new_part_document(sw)

        if self._model is None:
            raise RuntimeError("SolidWorks did not create a model document.")
        self._active_part_title = self._document_title(self._model)

        return {
            "workspace": path_to_string(self._workspace),
            "document": self._active_part_title,
        }

    def _new_part_document(self, sw: Any) -> Any:
        """Create a part document using explicit, shortcut, then default-template paths."""

        if self._config.part_template:
            return self._new_document_from_template(sw, self._config.part_template, "part")

        document = self._call_sldworks_document_factory(sw, "NewPart", {})
        if document is not None:
            return document

        template = self._find_default_template(sw, ".prtdot")
        if template:
            return self._new_document_from_template(sw, template, "part")
        template = _first_existing_common_template(".prtdot")
        if template:
            return self._new_document_from_template(sw, template, "part")
        raise RuntimeError("SolidWorks could not create a part document and no default .prtdot template was found.")

    def _new_drawing_document(self, sw: Any, profile: DrawingProfile) -> Any:
        """Create a drawing document using explicit, shortcut, then default-template paths."""

        template = profile.template_path or self._config.drawing_template
        if template:
            return self._new_document_from_template(sw, template, "drawing")

        document = self._call_sldworks_document_factory(sw, "NewDrawing", {})
        if document is not None:
            return document

        template = self._find_default_template(sw, ".drwdot")
        if template:
            return self._new_document_from_template(sw, template, "drawing")
        template = _first_existing_common_template(".drwdot")
        if template:
            return self._new_document_from_template(sw, template, "drawing")
        raise RuntimeError("SolidWorks could not create a drawing document and no default .drwdot template was found.")

    def _new_assembly_document(self, sw: Any) -> Any:
        """Create an assembly document using shortcut, default-template, then common-template paths."""

        document = self._call_sldworks_document_factory(sw, "NewAssembly", {})
        if document is not None:
            return document

        template = self._find_default_template(sw, ".asmdot")
        if template:
            return self._new_document_from_template(sw, template, "assembly")
        template = _first_existing_common_template(".asmdot")
        if template:
            return self._new_document_from_template(sw, template, "assembly")
        raise RuntimeError("SolidWorks could not create an assembly document and no default .asmdot template was found.")

    def _new_document_from_template(self, sw: Any, template: str, document_kind: str) -> Any:
        """Create a SolidWorks document from a template path with COM logging."""

        started_at = perf_counter()
        try:
            document = sw.NewDocument(str(template), 0, 0, 0)
            self.record_com_call(
                "SldWorks.NewDocument",
                {"template": str(template), "document_kind": document_kind},
                result=document,
                started_at=started_at,
            )
            return document
        except Exception as exc:
            self.record_com_call(
                "SldWorks.NewDocument",
                {"template": str(template), "document_kind": document_kind},
                error=exc,
                started_at=started_at,
            )
            raise

    def _call_sldworks_document_factory(self, sw: Any, method_name: str, parameters: dict[str, Any]) -> Any | None:
        """Call a SolidWorks document factory method and return None when unavailable."""

        started_at = perf_counter()
        try:
            method = getattr(sw, method_name)
            document = method()
            self.record_com_call(f"SldWorks.{method_name}", parameters, result=document, started_at=started_at)
            return document
        except Exception as exc:
            self.record_com_call(f"SldWorks.{method_name}", parameters, error=exc, started_at=started_at)
            self._warnings.append(f"SldWorks.{method_name}:{exc}")
            return None

    def _find_default_template(self, sw: Any, suffix: str) -> str | None:
        """Find a configured SolidWorks default template by extension."""

        method = getattr(sw, "GetUserPreferenceStringValue", None)
        if not callable(method):
            return None
        suffix = suffix.lower()
        for preference_id in range(1, 51):
            started_at = perf_counter()
            try:
                value = method(preference_id)
                self.record_com_call(
                    "SldWorks.GetUserPreferenceStringValue",
                    {"preference_id": preference_id, "suffix": suffix},
                    result=value,
                    started_at=started_at,
                )
            except Exception as exc:
                self.record_com_call(
                    "SldWorks.GetUserPreferenceStringValue",
                    {"preference_id": preference_id, "suffix": suffix},
                    error=exc,
                    started_at=started_at,
                )
                continue
            if isinstance(value, str) and value.lower().endswith(suffix) and Path(value).exists():
                return value
        return None

    def execute_operation(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Dispatch one operation to a small, version-tolerant handler."""

        handler_name = f"_op_{operation.op}"
        handler = getattr(self, handler_name, None)
        if handler is None:
            return StepResult(index, operation.op, False, f"No handler for {operation.op}", operation.id)

        try:
            self.record_event("adapter.operation", "started", operation.to_dict())
            details = handler(operation, plan)
            feature = {
                "index": index,
                "id": operation.id or f"{operation.op}_{index}",
                "op": operation.op,
                "description": operation.description,
                "details": details,
            }
            self._features.append(feature)
            self.record_event("adapter.operation", "completed", feature)
            return StepResult(index, operation.op, True, f"Executed {operation.op}", operation.id, feature)
        except Exception as exc:
            self.record_event(
                "adapter.operation",
                "failed",
                {"operation": operation.to_dict(), "error": str(exc)},
            )
            return StepResult(
                index=index,
                id=operation.id,
                op=operation.op,
                ok=False,
                message=str(exc),
                details={"operation": operation.to_dict()},
            )

    def generate_drawing(self, plan: ModelPlan, profile: DrawingProfile) -> dict[str, str]:
        """Create a drawing document, insert standard views and try hole callouts."""

        sw = self._require_sw()
        workspace = self._require_workspace() / "exports"
        workspace.mkdir(parents=True, exist_ok=True)
        if not profile.enabled:
            return {}

        self._drawing = self._new_drawing_document(sw, profile)

        if self._drawing is None:
            raise RuntimeError("SolidWorks did not create a drawing document.")
        self._active_drawing_title = self._document_title(self._drawing)

        part_path = self._ensure_part_saved(plan)
        view_result = self._create_standard_drawing_views(part_path)
        dimension_result = self._try_insert_basic_dimensions(plan, view_result, profile)
        if _is_bom_assembly_plan(plan):
            callout_result = {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_assembly_bom_has_no_part_hole_callout_gate",
            }
            self.record_event("drawing.hole_callout", "skipped", callout_result)
        elif _is_sheet_metal_base_flange_plan(plan):
            callout_result = {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_sheet_metal_base_flange_has_no_holes",
            }
            self.record_event("drawing.hole_callout", "skipped", callout_result)
        elif _is_weldment_frame_plan(plan):
            callout_result = {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_weldment_frame_has_no_holes",
            }
            self.record_event("drawing.hole_callout", "skipped", callout_result)
        elif _is_static_simulation_plan(plan):
            callout_result = {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_static_simulation_has_no_holes",
            }
            self.record_event("drawing.hole_callout", "skipped", callout_result)
        elif _is_atomic_model_without_holes(plan):
            callout_result = {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_atomic_model_has_no_hole_operation",
            }
            self.record_event("drawing.hole_callout", "skipped", callout_result)
        else:
            callout_result = self._try_insert_thread_callouts(plan, view_result)
        metadata_note_result = self._try_insert_metadata_note(plan)
        view_status = str(view_result.get("status", "failed"))
        dimension_status = str(dimension_result.get("status", "not_requested"))
        callout_status = str(callout_result.get("status", "hole_callout_failed"))
        self._drawing_view_status = view_status
        self._drawing_view_result = view_result
        self._drawing_dimension_status = dimension_status
        self._drawing_dimension_result = dimension_result
        self._drawing_metadata_note_result = metadata_note_result
        self._drawing_annotation_status = callout_status
        self._drawing_annotation_result = callout_result
        if view_status != "created":
            self._warnings.append(f"drawing_views:{view_status}")
        if dimension_status not in {"not_requested", "basic_dimensions_created"}:
            self._warnings.append(f"drawing_basic_dimensions:{dimension_status}")
        if callout_status not in {"hole_callout_created", "not_requested"}:
            self._warnings.append(f"drawing_thread_callouts:{callout_status}")

        drawing_path = workspace / f"{safe_output_name(plan.name)}.slddrw"
        self._save_as(self._drawing, drawing_path)
        self._active_drawing_path = drawing_path
        self._active_drawing_title = self._document_title(self._drawing) or drawing_path.name
        return {"slddrw": path_to_string(drawing_path)}

    def export_outputs(self, plan: ModelPlan, formats: tuple[str, ...]) -> dict[str, str]:
        """Export part and drawing documents to the requested formats."""

        workspace = self._require_workspace() / "exports"
        workspace.mkdir(parents=True, exist_ok=True)
        model = self._require_model()
        outputs: dict[str, str] = {}
        failed: list[dict[str, Any]] = []
        base_name = safe_output_name(plan.name)
        for file_format in formats:
            suffix = _solidworks_suffix(file_format)
            target_path = workspace / f"{base_name}.{suffix}"
            try:
                if file_format == "csv" and self._bom_result.get("status") == "bom_verified":
                    self._write_bom_csv(target_path)
                    outputs[file_format] = path_to_string(target_path)
                    continue
                if file_format == "csv" and self._cut_list_result.get("status") == "cut_list_verified":
                    self._write_cut_list_csv(target_path)
                    outputs[file_format] = path_to_string(target_path)
                    continue
                if file_format == "csv" and self._simulation_result.get("status") == "simulation_verified":
                    self._write_simulation_csv(target_path)
                    outputs[file_format] = path_to_string(target_path)
                    continue
                if file_format == "dxf" and _is_sheet_metal_base_flange_plan(plan):
                    flat_pattern_result = self._export_sheet_metal_flat_pattern(target_path, plan)
                    if flat_pattern_result.get("ok") is not True:
                        raise RuntimeError(
                            str(flat_pattern_result.get("failure_reason") or "Flat-pattern DXF export failed.")
                        )
                    outputs[file_format] = path_to_string(target_path)
                    continue
                if file_format in {"pdf", "dwg", "dxf", "slddrw"} and self._drawing:
                    self._activate_drawing_document()
                    self._drawing.ClearSelection2(True)
                    document = self._drawing
                else:
                    self._activate_part_document()
                    model.ClearSelection2(True)
                    document = model
                self._save_as(document, target_path)
                if file_format in {"sldprt", "sldasm"}:
                    self._active_part_path = target_path
                    self._active_part_title = self._document_title(model) or target_path.name
                elif file_format == "slddrw" and self._drawing:
                    self._active_drawing_path = target_path
                    self._active_drawing_title = self._document_title(self._drawing) or target_path.name
                outputs[file_format] = path_to_string(target_path)
            except Exception as exc:
                failure = {
                    "format": file_format,
                    "path": path_to_string(target_path),
                    "error": str(exc),
                }
                failed.append(failure)
                self._warnings.append(f"export.{file_format}:{exc}")
                self.record_event("outputs.export_format", "failed", failure)
                continue
        self._export_result = {
            "status": "exports_completed" if not failed else "partial_export_failure",
            "formats": list(formats),
            "exported": sorted(outputs),
            "failed": failed,
            "failed_count": len(failed),
        }
        return outputs

    def _export_sheet_metal_flat_pattern(self, target_path: Path, plan: ModelPlan) -> dict[str, Any]:
        """Export the active sheet-metal part's flat pattern as DXF."""

        attempts: list[dict[str, Any]] = []
        self._activate_part_document()
        model = self._active_model_doc()
        model.ClearSelection2(True)
        part_path = self._ensure_part_saved(plan)

        method = getattr(model, "ExportFlatPatternView", None)
        if callable(method):
            started_at = perf_counter()
            try:
                result = method(str(target_path), SW_SHEET_METAL_EXPORT_FLAT_PATTERN_GEOMETRY)
                self.record_com_call(
                    "PartDoc.ExportFlatPatternView",
                    {
                        "target_path": path_to_string(target_path),
                        "options": SW_SHEET_METAL_EXPORT_FLAT_PATTERN_GEOMETRY,
                    },
                    result=result,
                    started_at=started_at,
                )
                attempt = {
                    "method": "ExportFlatPatternView",
                    "ok": bool(result) and target_path.exists(),
                    "return_value": result,
                    "path_exists": target_path.exists(),
                    "size_bytes": target_path.stat().st_size if target_path.exists() else 0,
                }
                attempts.append(attempt)
                if attempt["ok"]:
                    return self._record_sheet_metal_flat_pattern_result(target_path, attempts, "ExportFlatPatternView")
            except Exception as exc:
                self.record_com_call(
                    "PartDoc.ExportFlatPatternView",
                    {"target_path": path_to_string(target_path)},
                    error=exc,
                    started_at=started_at,
                )
                attempts.append({"method": "ExportFlatPatternView", "ok": False, "error": str(exc)})

        method = getattr(model, "ExportToDWG2", None)
        if callable(method):
            for alignment in _flat_pattern_alignment_variants():
                started_at = perf_counter()
                try:
                    result = method(
                        str(target_path),
                        str(part_path),
                        SW_EXPORT_TO_DWG_EXPORT_SHEET_METAL,
                        True,
                        alignment,
                        False,
                        False,
                        SW_SHEET_METAL_EXPORT_OPTIONS,
                        None,
                    )
                    self.record_com_call(
                        "PartDoc.ExportToDWG2",
                        {
                            "target_path": path_to_string(target_path),
                            "part_path": path_to_string(part_path),
                            "action": SW_EXPORT_TO_DWG_EXPORT_SHEET_METAL,
                            "sheet_metal_options": SW_SHEET_METAL_EXPORT_OPTIONS,
                            "alignment_variant": type(alignment).__name__,
                        },
                        result=result,
                        started_at=started_at,
                    )
                    attempt = {
                        "method": "ExportToDWG2",
                        "ok": bool(result) and target_path.exists(),
                        "return_value": result,
                        "path_exists": target_path.exists(),
                        "size_bytes": target_path.stat().st_size if target_path.exists() else 0,
                        "alignment_variant": type(alignment).__name__,
                    }
                    attempts.append(attempt)
                    if attempt["ok"]:
                        return self._record_sheet_metal_flat_pattern_result(target_path, attempts, "ExportToDWG2")
                except Exception as exc:
                    self.record_com_call(
                        "PartDoc.ExportToDWG2",
                        {
                            "target_path": path_to_string(target_path),
                            "part_path": path_to_string(part_path),
                            "action": SW_EXPORT_TO_DWG_EXPORT_SHEET_METAL,
                        },
                        error=exc,
                        started_at=started_at,
                    )
                    attempts.append({"method": "ExportToDWG2", "ok": False, "error": str(exc)})

        result = {
            "status": "flat_pattern_export_failed",
            "ok": False,
            "format": "dxf",
            "path": path_to_string(target_path),
            "attempts": attempts,
            "failure_reason": "SolidWorks did not export a sheet-metal flat-pattern DXF.",
        }
        self._sheet_metal_result["flat_pattern_result"] = result
        if self._sheet_metal_result.get("status") == "sheet_metal_verified":
            self._sheet_metal_result["status"] = "sheet_metal_flat_pattern_failed"
        self.record_event("sheet_metal.flat_pattern_export", "failed", result)
        return result

    def _record_sheet_metal_flat_pattern_result(
        self,
        target_path: Path,
        attempts: list[dict[str, Any]],
        method: str,
    ) -> dict[str, Any]:
        """Record a successful flat-pattern export and publish it in diagnostics."""

        result = {
            "status": "flat_pattern_exported",
            "ok": True,
            "format": "dxf",
            "path": path_to_string(target_path),
            "method": method,
            "attempts": attempts,
            "size_bytes": target_path.stat().st_size if target_path.exists() else 0,
        }
        self._sheet_metal_result["flat_pattern_result"] = result
        if self._sheet_metal_result.get("base_flange_created") is True:
            self._sheet_metal_result["status"] = "sheet_metal_verified"
        self.record_event("sheet_metal.flat_pattern_export", "completed", result)
        return result

    def inspect_active_model(self) -> dict[str, Any]:
        """Return a compact feature summary without reading the whole COM tree."""

        if self._active_plan is not None and _is_bom_assembly_plan(self._active_plan):
            self._model_geometry_result = {"status": "not_requested", "reason": "assembly workflow uses assembly_result"}
            self._model_geometry_status = "not_requested"
            self._mass_property_result = {"status": "not_requested", "reason": "assembly workflow uses BOM/component evidence"}
            self._mass_property_status = "not_requested"
        else:
            self._model_geometry_result = self._inspect_controlled_model_geometry()
            self._model_geometry_status = str(self._model_geometry_result.get("status", "geometry_readback_failed"))
            self._mass_property_result = self._inspect_mass_properties()
            self._mass_property_status = str(self._mass_property_result.get("status", "mass_property_failed"))
        return {
            "adapter": self.name,
            "active_document": self._active_title(),
            "feature_count": len(self._features),
            "features": list(self._features),
            "thread_model_status": self._thread_model_status,
            "corner_radius_status": self._corner_radius_status,
            "drawing_view_status": self._drawing_view_status,
            "drawing_view_result": self._drawing_view_result,
            "drawing_annotation_status": self._drawing_annotation_status,
            "drawing_annotation_result": self._drawing_annotation_result,
            "drawing_dimension_status": self._drawing_dimension_status,
            "drawing_dimension_result": self._drawing_dimension_result,
            "drawing_metadata_note_result": self._drawing_metadata_note_result,
            "material_status": self._material_status,
            "material_result": self._material_result,
            "custom_property_status": self._custom_property_status,
            "custom_property_result": self._custom_property_result,
            "model_geometry_status": self._model_geometry_status,
            "model_geometry_result": self._model_geometry_result,
            "mass_property_status": self._mass_property_status,
            "mass_property_result": self._mass_property_result,
            "export_result": self._export_result,
            "assembly_result": self._assembly_result,
            "bom_result": self._bom_result,
            "sheet_metal_status": self._sheet_metal_result.get("status"),
            "sheet_metal_result": self._sheet_metal_result,
            "weldment_status": self._weldment_result.get("status"),
            "weldment_result": self._weldment_result,
            "cut_list_status": self._cut_list_result.get("status"),
            "cut_list_result": self._cut_list_result,
            "simulation_status": self._simulation_result.get("status"),
            "simulation_result": self._simulation_result,
            "fallbacks": list(self._fallbacks),
            "warnings": list(self._warnings),
            "hole_result": self._last_hole_result,
        }

    def document_state_snapshot(self, phase: str) -> dict[str, Any]:
        """Return a best-effort snapshot of open SolidWorks documents for cleanup auditing."""

        result: dict[str, Any] = {
            "status": "not_started",
            "adapter": self.name,
            "phase": phase,
            "workspace": path_to_string(self._workspace) if self._workspace else None,
            "open_document_count": None,
            "run_created_open_count": None,
            "run_created_documents": [],
            "open_documents": [],
            "tracked_documents": [],
            "warnings": [],
        }
        if self._sw is None:
            result["status"] = "skipped_no_connection"
            result["run_created_open_count"] = 0
            result["message"] = "SolidWorks application object is not available for document state audit."
            return result
        if self._solidworks_rpc_unavailable:
            result["status"] = "failed"
            result["failure_reason"] = "solidworks_rpc_unavailable"
            result["rpc_failure"] = self._solidworks_rpc_unavailable
            return result

        open_documents = self._open_document_summaries()
        result["enumeration"] = open_documents
        if open_documents.get("ok"):
            documents = [
                item for item in open_documents.get("documents", [])
                if isinstance(item, dict)
            ]
            result["open_documents"] = documents
            result["open_document_count"] = len(documents)
            run_created_documents = [
                item for item in documents
                if item.get("is_run_created")
            ]
            result["run_created_documents"] = run_created_documents
            result["run_created_open_count"] = len(run_created_documents)
            result["status"] = "verified"
        else:
            result["warnings"].append("open_document_enumeration_failed")
            result["status"] = "partial"

        tracked = self._tracked_document_state_checks()
        result["tracked_documents"] = tracked
        if result["run_created_open_count"] is None:
            tracked_open = [
                item for item in tracked
                if isinstance(item, dict) and item.get("is_run_created")
            ]
            result["run_created_documents"] = tracked_open
            result["run_created_open_count"] = len(tracked_open)
            if tracked:
                result["status"] = "verified_by_tracked_candidates"
        result["message"] = (
            "No run-created SolidWorks documents are open."
            if result.get("run_created_open_count") == 0
            else "One or more run-created SolidWorks documents are still open."
        )
        return result

    def capture_previews(self, plan: ModelPlan) -> dict[str, str]:
        """Save standard-view preview images when SolidWorks exposes SaveAs support."""

        workspace = self._require_workspace() / "previews"
        workspace.mkdir(parents=True, exist_ok=True)
        self._activate_part_document()
        model = self._require_model()
        previews: dict[str, str] = {}
        view_commands = {
            "front": "*Front",
            "top": "*Top",
            "right": "*Right",
            "isometric": "*Isometric",
        }
        for view_name, solidworks_view in view_commands.items():
            try:
                model.ShowNamedView2(solidworks_view, -1)
                model.ViewZoomtofit2()
                preview_path = workspace / f"{safe_output_name(plan.name)}_{view_name}.png"
                self._save_as(model, preview_path)
                previews[view_name] = path_to_string(preview_path)
            except Exception as exc:
                previews[f"{view_name}_error"] = str(exc)
        return previews

    def cleanup_after_run(self, plan: ModelPlan | None = None) -> dict[str, Any]:
        """Close SolidWorks documents created by this adapter run."""

        result: dict[str, Any] = {
            "status": "not_started",
            "enabled": self._config.close_documents_after_run,
            "adapter": self.name,
            "closed_documents": [],
            "attempts": [],
            "cleanup_verification_status": "not_started",
            "failure_reason": None,
        }
        if not self._config.close_documents_after_run:
            result["status"] = "disabled"
            result["cleanup_verification_status"] = "not_attempted"
            result["message"] = "SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN is disabled."
            return result

        if self._config.force_cleanup_failure:
            result["status"] = "forced_failure"
            result["cleanup_verification_status"] = "failed"
            result["failure_reason"] = "SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE is enabled"
            result["message"] = "Forced cleanup failure for regression testing."
            self._clear_document_handles()
            return result

        if self._sw is None:
            result["status"] = "skipped_no_connection"
            result["cleanup_verification_status"] = "not_attempted"
            result["message"] = "SolidWorks application object is not available."
            self._clear_document_handles()
            return result

        if self._solidworks_rpc_unavailable:
            result["status"] = "failed"
            result["cleanup_verification_status"] = "not_attempted"
            result["failure_reason"] = "solidworks_rpc_unavailable"
            result["message"] = (
                "SolidWorks COM/RPC became unavailable before cleanup; "
                "run-created documents could not be closed through COM."
            )
            result["rpc_failure"] = self._solidworks_rpc_unavailable
            self._clear_document_handles()
            return result

        close_targets = [
            ("drawing", self._drawing, self._active_drawing_title, self._active_drawing_path),
            ("part", self._model, self._active_part_title, self._active_part_path),
        ]
        targets_with_candidates = 0
        failed_targets: list[str] = []
        for kind, document, title, path in close_targets:
            candidates = self._cleanup_candidates(document, title, path)
            if not candidates:
                continue
            targets_with_candidates += 1
            closed_any = False
            failed_attempts = 0
            required_attempts = 0
            for candidate in candidates:
                resolved_document = None
                attempt = {
                    "kind": kind,
                    "title": candidate["name"],
                    "source": candidate["source"],
                    "requires_path_match": candidate["requires_path_match"],
                    "ok": False,
                    "close_called": False,
                    "verified_closed": None,
                }
                if candidate["requires_path_match"]:
                    resolution = self._resolve_run_created_document(candidate["name"])
                    resolved_document = resolution.pop("document", None)
                    attempt["resolution"] = resolution
                    if not resolution.get("is_run_created"):
                        attempt["failure_reason"] = (
                            resolution.get("failure_reason")
                            or "Candidate did not resolve to a document in the current run workspace."
                        )
                        attempt["skipped"] = True
                        result["attempts"].append(attempt)
                        continue
                required_attempts += 1
                started_at = perf_counter()
                try:
                    close_name = candidate["name"]
                    if resolved_document is not None:
                        resolved_title = self._document_title(resolved_document)
                        if resolved_title:
                            close_name = resolved_title
                    attempt["close_name"] = close_name
                    close_result = self._sw.CloseDoc(close_name)
                    self.record_com_call(
                        "SldWorks.CloseDoc",
                        {
                            "title": close_name,
                            "kind": kind,
                            "candidate": candidate["name"],
                            "candidate_source": candidate["source"],
                        },
                        result=close_result,
                        started_at=started_at,
                    )
                    attempt["close_called"] = True
                    attempt["result"] = close_result
                    verification = self._verify_document_closed(close_name)
                    attempt["verification"] = verification
                    attempt["verified_closed"] = verification.get("verified_closed")
                    close_succeeded = close_result is not False
                    if verification.get("verified_closed") is True:
                        attempt["ok"] = True
                        attempt["verification_status"] = "verified_closed"
                    elif verification.get("verified_closed") is False:
                        attempt["failure_reason"] = "Document was still open after CloseDoc."
                        attempt["verification_status"] = "still_open"
                    elif close_succeeded:
                        attempt["ok"] = True
                        attempt["verification_status"] = "unverified"
                    else:
                        attempt["failure_reason"] = "CloseDoc returned false and closure could not be verified."
                        attempt["verification_status"] = "unverified_close_failed"
                    if attempt["ok"]:
                        result["closed_documents"].append(
                            {
                                "kind": kind,
                                "title": close_name,
                                "candidate": candidate["name"],
                                "source": candidate["source"],
                                "ok": True,
                                "verified_closed": attempt["verified_closed"],
                                "verification_status": attempt["verification_status"],
                            }
                        )
                        closed_any = True
                    else:
                        failed_attempts += 1
                except Exception as exc:
                    attempt["error"] = str(exc)
                    self.record_com_call(
                        "SldWorks.CloseDoc",
                        {
                            "title": candidate["name"],
                            "kind": kind,
                            "candidate_source": candidate["source"],
                        },
                        error=exc,
                        started_at=started_at,
                    )
                    failed_attempts += 1
                finally:
                    result["attempts"].append(attempt)
            if not closed_any and required_attempts > 0:
                failed_targets.append(kind)

        self._clear_document_handles()
        if targets_with_candidates == 0:
            result["status"] = "skipped_no_documents"
            result["cleanup_verification_status"] = "not_applicable"
            result["message"] = "No run-created SolidWorks documents were tracked for cleanup."
        elif failed_targets:
            result["status"] = "partial" if result["closed_documents"] else "failed"
            result["cleanup_verification_status"] = "failed"
            result["failure_reason"] = f"Failed to close run-created documents: {', '.join(failed_targets)}"
            result["message"] = result["failure_reason"]
        else:
            result["status"] = "completed"
            verification_statuses = {
                str(item.get("verification_status"))
                for item in result["closed_documents"]
                if isinstance(item, dict) and item.get("verification_status")
            }
            result["cleanup_verification_status"] = (
                "verified"
                if verification_statuses == {"verified_closed"}
                else "unverified"
                if "unverified" in verification_statuses
                else "mixed"
            )
            result["message"] = "Closed run-created SolidWorks documents."
        return result

    def cleanup_run_documents(self, run_dir: str | Path) -> dict[str, Any]:
        """Close open SolidWorks documents that resolve to a completed run directory."""

        run_path = Path(run_dir).expanduser().resolve()
        result: dict[str, Any] = {
            "status": "not_started",
            "enabled": True,
            "adapter": self.name,
            "run_dir": path_to_string(run_path),
            "attach_only": self._config.cleanup_attach_only,
            "closed_documents": [],
            "attempts": [],
            "candidate_documents": [],
            "cleanup_verification_status": "not_started",
            "failure_reason": None,
        }
        if not run_path.exists() or not run_path.is_dir():
            result["status"] = "failed"
            result["cleanup_verification_status"] = "not_attempted"
            result["failure_reason"] = "run_dir_missing"
            result["message"] = "Run directory does not exist."
            return result
        if self._config.force_cleanup_failure:
            result["status"] = "forced_failure"
            result["cleanup_verification_status"] = "failed"
            result["failure_reason"] = "SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE is enabled"
            result["message"] = "Forced post-run cleanup failure for regression testing."
            return result

        candidates = _run_native_document_candidates(run_path)
        result["candidate_documents"] = candidates
        if not candidates:
            result["status"] = "skipped_no_documents"
            result["cleanup_verification_status"] = "not_applicable"
            result["message"] = "No SLDPRT or SLDDRW output paths were found in the run artifacts."
            return result

        self.set_run_workspace(run_path)
        if self._sw is None:
            try:
                result["connection"] = self._connect_for_post_run_cleanup()
            except Exception as exc:
                result["status"] = "failed"
                result["cleanup_verification_status"] = "not_attempted"
                result["failure_reason"] = (
                    "solidworks_not_running_attach_only"
                    if self._config.cleanup_attach_only
                    else str(exc)
                )
                result["connection_error"] = str(exc)
                result["message"] = (
                    "No running SolidWorks session was available for attach-only post-run cleanup."
                    if self._config.cleanup_attach_only
                    else "SolidWorks connection failed before post-run cleanup."
                )
                return result

        closed_any = False
        failed_close = False
        for candidate in candidates:
            lookup_names = _cleanup_lookup_names(candidate)
            candidate_closed = False
            for lookup_name in lookup_names:
                attempt: dict[str, Any] = {
                    "kind": candidate["kind"],
                    "lookup_name": lookup_name,
                    "path": candidate["path"],
                    "ok": False,
                    "close_called": False,
                    "verified_closed": None,
                }
                resolution = self._resolve_run_created_document(lookup_name)
                resolved_document = resolution.pop("document", None)
                attempt["resolution"] = resolution
                if not resolution.get("is_run_created"):
                    attempt["skipped"] = True
                    attempt["failure_reason"] = resolution.get("failure_reason")
                    result["attempts"].append(attempt)
                    continue

                close_name = resolution.get("document_title") or lookup_name
                attempt["close_name"] = close_name
                started_at = perf_counter()
                try:
                    close_result = self._sw.CloseDoc(close_name)
                    self.record_com_call(
                        "SldWorks.CloseDoc",
                        {
                            "title": close_name,
                            "kind": candidate["kind"],
                            "candidate_path": candidate["path"],
                            "purpose": "post_run_cleanup",
                        },
                        result=close_result,
                        started_at=started_at,
                    )
                    attempt["close_called"] = True
                    attempt["result"] = close_result
                    verification = self._verify_document_closed(close_name)
                    attempt["verification"] = verification
                    attempt["verified_closed"] = verification.get("verified_closed")
                    if verification.get("verified_closed") is True:
                        attempt["ok"] = True
                        attempt["verification_status"] = "verified_closed"
                    elif close_result is not False and verification.get("verified_closed") is None:
                        attempt["ok"] = True
                        attempt["verification_status"] = "unverified"
                    else:
                        attempt["failure_reason"] = "Document was still open or CloseDoc failed."
                        attempt["verification_status"] = "failed"
                    if attempt["ok"]:
                        result["closed_documents"].append(
                            {
                                "kind": candidate["kind"],
                                "path": candidate["path"],
                                "title": close_name,
                                "ok": True,
                                "verified_closed": attempt["verified_closed"],
                                "verification_status": attempt["verification_status"],
                            }
                        )
                        closed_any = True
                        candidate_closed = True
                        result["attempts"].append(attempt)
                        break
                    failed_close = True
                except Exception as exc:
                    attempt["error"] = str(exc)
                    attempt["failure_reason"] = str(exc)
                    failed_close = True
                    self.record_com_call(
                        "SldWorks.CloseDoc",
                        {
                            "title": close_name,
                            "kind": candidate["kind"],
                            "candidate_path": candidate["path"],
                            "purpose": "post_run_cleanup",
                        },
                        error=exc,
                        started_at=started_at,
                    )
                finally:
                    if attempt not in result["attempts"]:
                        result["attempts"].append(attempt)
            if not candidate_closed:
                continue

        if failed_close:
            result["status"] = "partial" if closed_any else "failed"
            result["cleanup_verification_status"] = "failed"
            result["failure_reason"] = "One or more run-created documents could not be closed."
            result["message"] = result["failure_reason"]
        elif closed_any:
            result["status"] = "completed"
            verification_statuses = {
                str(item.get("verification_status"))
                for item in result["closed_documents"]
                if isinstance(item, dict) and item.get("verification_status")
            }
            result["cleanup_verification_status"] = (
                "verified"
                if verification_statuses == {"verified_closed"}
                else "unverified"
                if "unverified" in verification_statuses
                else "mixed"
            )
            result["message"] = "Closed open SolidWorks documents from the completed run."
        else:
            result["status"] = "skipped_no_documents"
            result["cleanup_verification_status"] = "not_applicable"
            result["message"] = "No candidate run-created SolidWorks documents were open."
        return result

    def _op_create_plane(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create an offset reference plane and register it under a stable atomic id."""

        model = self._require_model()
        params = operation.parameters
        plane_id = str(operation.id or params.get("plane_id") or f"plane_{len(self._atomic_references) + 1}")
        base_plane = str(params["base_plane"])
        distance_m = _to_meters(float(params["distance"]), plan.units)
        base_selected = _select_by_id(model, _plane_name_candidates(base_plane), "PLANE")
        base_selection: dict[str, Any] = {
            "reference_id": base_plane,
            "object_type": "PLANE",
            "expected_type": "plane",
            "selected": base_selected,
            "method": "SelectByID2",
            "mark": 0,
        }
        if not base_selected:
            base_selection = self._select_atomic_reference_using_registered_type(base_plane, "PLANE", "plane")
        if not base_selection.get("selected"):
            raise RuntimeError(f"Could not select base_plane for create_plane: {base_plane}")

        attempts: list[dict[str, Any]] = []
        method = getattr(model.FeatureManager, "InsertRefPlane", None)
        if not callable(method):
            raise RuntimeError("SolidWorks FeatureManager.InsertRefPlane is unavailable.")
        started_at = perf_counter()
        try:
            feature = method(SW_REF_PLANE_DISTANCE, distance_m, 0, 0, 0, 0)
            self.record_com_call(
                "FeatureManager.InsertRefPlane",
                {"base_plane": base_plane, "distance_m": distance_m},
                result=feature,
                started_at=started_at,
            )
            attempts.append({"method": "InsertRefPlane", "available": True, "created": feature is not None})
        except Exception as exc:
            self.record_com_call(
                "FeatureManager.InsertRefPlane",
                {"base_plane": base_plane, "distance_m": distance_m},
                error=exc,
                started_at=started_at,
            )
            attempts.append({"method": "InsertRefPlane", "available": True, "error": str(exc)})
            raise RuntimeError(f"SolidWorks create_plane failed: {attempts}") from exc
        if feature is None:
            raise RuntimeError(f"SolidWorks create_plane failed: {attempts}")
        registration = self._register_atomic_reference(
            plane_id,
            "plane",
            object_type="PLANE",
            com_objects=[feature],
        )
        self.record_event(
            "adapter.atomic_reference",
            "completed" if registration.get("registered") else "warning",
            registration,
        )
        return {
            "base_plane": base_plane,
            "distance_m": distance_m,
            "selection": base_selection,
            "method": "InsertRefPlane",
            "attempts": attempts,
            "created_ids": registration.get("created_ids", []),
            "atomic_reference": registration,
        }

    def _op_create_sketch(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a sketch on a named plane and draw MVP-supported entities."""

        model = self._require_model()
        params = operation.parameters
        sketch_id = str(operation.id or params.get("sketch_id") or f"sketch_{len(self._atomic_references) + 1}")
        plane_id = str(params["plane"])
        plane_names = _plane_name_candidates(plane_id)
        plane_selected = _select_by_id(model, plane_names, "PLANE")
        plane_selection: dict[str, Any] = {
            "reference_id": plane_id,
            "object_type": "PLANE",
            "expected_type": "plane",
            "selected": plane_selected,
            "method": "SelectByID2",
            "names": list(plane_names),
        }
        if not plane_selected:
            plane_selection = self._select_atomic_reference_using_registered_type(plane_id, "PLANE", "plane")
            plane_selected = bool(plane_selection.get("selected"))
        if not plane_selected:
            raise RuntimeError(f"Could not select sketch plane from candidates: {plane_names}")
        model.SketchManager.InsertSketch(True)

        entity_references: list[dict[str, Any]] = []
        created_ids = [sketch_id]
        for entity in params["entities"]:
            entity_objects = self._draw_entity(entity, plan)
            entity_id = entity.get("id") if isinstance(entity, dict) else None
            if entity_id:
                entity_registration = self._register_atomic_reference(
                    str(entity_id),
                    "entity",
                    object_type="SKETCHSEGMENT",
                    com_objects=_as_sequence(entity_objects),
                )
                entity_references.append(
                    {
                        **entity_registration,
                        "entity_type": entity.get("type"),
                        "construction": bool(entity.get("construction") or entity.get("for_construction")),
                    }
                )
                created_ids.extend(entity_registration.get("created_ids", []))

        registration = self._register_active_atomic_sketch(sketch_id)
        model.SketchManager.InsertSketch(True)
        return {
            "plane": plane_names[0] if plane_selection.get("method") == "SelectByID2" else plane_id,
            "plane_selection": plane_selection,
            "entity_count": len(params["entities"]),
            "created_ids": _unique_strings(created_ids),
            "atomic_reference": registration,
            "entity_references": entity_references,
        }

    def _register_active_atomic_sketch(self, sketch_id: str) -> dict[str, Any]:
        """Name and remember the active sketch so later atomic operations can select it by id."""

        self._atomic_sketch_count += 1
        model = self._require_model()
        sketch_manager = getattr(model, "SketchManager", None)
        active_sketch = _call_or_get(sketch_manager, "ActiveSketch")
        if active_sketch is None:
            active_sketch = _call_com_noargs(model, "GetActiveSketch2")
        feature = _call_com_noargs(active_sketch, "GetFeature") if active_sketch is not None else None
        registration = self._register_atomic_reference(
            sketch_id,
            "sketch",
            object_type="SKETCH",
            com_objects=[feature, active_sketch],
            native_name_candidates=_sketch_name_candidates(self._atomic_sketch_count),
        )
        self.record_event(
            "adapter.atomic_reference",
            "completed" if registration.get("registered") else "warning",
            registration,
        )
        return registration

    def _register_atomic_feature(self, operation: ModelOperation, feature: Any) -> dict[str, Any]:
        """Name and remember a created feature for later pattern/feature references."""

        feature_id = str(operation.id or f"{operation.op}_{len(self._atomic_references) + 1}")
        registration = self._register_atomic_reference(
            feature_id,
            "feature",
            object_type="BODYFEATURE",
            com_objects=[feature],
        )
        self.record_event(
            "adapter.atomic_reference",
            "completed" if registration.get("registered") else "failed",
            registration,
        )
        return registration

    def _register_atomic_reference(
        self,
        reference_id: str,
        reference_type: str,
        *,
        object_type: str,
        com_objects: list[Any],
        native_name_candidates: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Assign a stable SolidWorks name for an atomic feature-graph id."""

        clean_id = str(reference_id).strip()
        solidworks_name = _safe_solidworks_atomic_name(clean_id)
        selection_names = _unique_strings([solidworks_name, clean_id, *native_name_candidates])
        self._atomic_reference_objects[clean_id] = [item for item in com_objects if item is not None]
        attempts: list[dict[str, Any]] = []
        for index, com_object in enumerate(com_objects):
            if com_object is None:
                attempts.append({"index": index, "available": False})
                continue
            attempt: dict[str, Any] = {
                "index": index,
                "available": True,
                "target_type": type(com_object).__name__,
                "name": solidworks_name,
            }
            try:
                before = _call_or_get(com_object, "Name") or _call_or_get(com_object, "GetName")
                setattr(com_object, "Name", solidworks_name)
                after = _call_or_get(com_object, "Name") or _call_or_get(com_object, "GetName")
                attempt.update({"before": before, "after": after, "renamed": after == solidworks_name})
            except Exception as exc:
                attempt.update({"renamed": False, "error": str(exc)})
            attempts.append(attempt)
            if attempt.get("renamed"):
                self._atomic_references[clean_id] = {
                    "id": clean_id,
                    "type": reference_type,
                    "object_type": object_type,
                    "solidworks_name": solidworks_name,
                    "selection_names": selection_names,
                }
                return {
                    "registered": True,
                    "id": clean_id,
                    "type": reference_type,
                    "object_type": object_type,
                    "solidworks_name": solidworks_name,
                    "selection_names": selection_names,
                    "created_ids": [clean_id],
                    "attempts": attempts,
                }
        self._atomic_references[clean_id] = {
            "id": clean_id,
            "type": reference_type,
            "object_type": object_type,
            "solidworks_name": solidworks_name,
            "selection_names": selection_names,
            "unverified_name": True,
        }
        return {
            "registered": False,
            "id": clean_id,
            "type": reference_type,
            "object_type": object_type,
            "solidworks_name": solidworks_name,
            "selection_names": selection_names,
            "created_ids": [clean_id],
            "attempts": attempts,
            "fallback_reason": "Could not verify SolidWorks COM object renaming; native SolidWorks names will be tried during selection.",
        }

    def _select_atomic_reference(
        self,
        reference_id: Any,
        object_type: str,
        expected_type: str,
        *,
        append: bool = False,
        mark: int = 0,
        clear_selection: bool = True,
    ) -> dict[str, Any]:
        """Select a named atomic reference, recording evidence for later diagnosis."""

        model = self._require_model()
        clean_id = str(reference_id).strip()
        reference = self._atomic_references.get(clean_id)
        names = _unique_strings(
            [
                *(reference.get("selection_names", []) if isinstance(reference, dict) else []),
                reference.get("solidworks_name") if isinstance(reference, dict) else None,
                clean_id,
                _safe_solidworks_atomic_name(clean_id),
            ]
        )
        object_types = (object_type,)
        if expected_type == "feature" and object_type == "BODYFEATURE":
            object_types = ("BODYFEATURE", "FEATURE")
        if expected_type == "axis" and object_type == "AXIS":
            object_types = ("AXIS", "DATUMAXIS")
        if expected_type == "entity" and object_type == "SKETCHSEGMENT":
            object_types = ("SKETCHSEGMENT",)
        attempts: list[dict[str, Any]] = []
        if clear_selection:
            try:
                model.ClearSelection2(True)
            except Exception:
                pass
        for candidate_type in object_types:
            selected = _select_by_id(model, tuple(names), candidate_type, append=append, mark=mark)
            attempt = {
                "reference_id": clean_id,
                "expected_type": expected_type,
                "object_type": candidate_type,
                "names": names,
                "registered_reference": reference,
                "append": append,
                "mark": mark,
                "method": "SelectByID2",
                "selected": selected,
            }
            attempts.append(attempt)
            if selected:
                result = {**attempt, "attempts": attempts}
                self.record_event("adapter.atomic_selection", "completed", result)
                return result
        direct_selection = self._select_atomic_reference_object(clean_id, append=append, mark=mark)
        attempts.extend(direct_selection.get("attempts", []))
        if direct_selection.get("selected"):
            result = {
                "reference_id": clean_id,
                "expected_type": expected_type,
                "object_type": object_type,
                "names": names,
                "registered_reference": reference,
                "append": append,
                "mark": mark,
                "method": direct_selection.get("method"),
                "selected": True,
                "attempts": attempts,
            }
            self.record_event("adapter.atomic_selection", "completed", result)
            return result
        result = {
            "reference_id": clean_id,
            "expected_type": expected_type,
            "object_type": object_type,
            "names": names,
            "registered_reference": reference,
            "append": append,
            "mark": mark,
            "selected": False,
            "attempts": attempts,
            "failure_reason": "No SolidWorks object matched the named atomic reference.",
        }
        self.record_event("adapter.atomic_selection", "failed", result)
        return result

    def _select_atomic_reference_object(self, reference_id: str, *, append: bool, mark: int) -> dict[str, Any]:
        """Select a remembered COM object for references that are not name-selectable."""

        objects = self._atomic_reference_objects.get(reference_id, [])
        attempts: list[dict[str, Any]] = []
        if not objects:
            return {"selected": False, "attempts": attempts, "failure_reason": "No COM objects recorded for reference."}
        select_data = _create_select_data(self._require_model(), mark)
        for index, com_object in enumerate(objects):
            if com_object is None:
                attempts.append({"method": "direct_object_select", "index": index, "available": False})
                continue
            for method_name, args in (
                ("Select4", (append, select_data)),
                ("Select2", (append, mark)),
                ("Select", (append,)),
            ):
                method = getattr(com_object, method_name, None)
                if not callable(method):
                    attempts.append({"method": method_name, "index": index, "available": False})
                    continue
                started_at = perf_counter()
                try:
                    selected = bool(method(*args))
                    self.record_com_call(
                        f"{type(com_object).__name__}.{method_name}",
                        {"reference_id": reference_id, "append": append, "mark": mark},
                        result=selected,
                        started_at=started_at,
                    )
                    attempts.append(
                        {
                            "method": method_name,
                            "index": index,
                            "available": True,
                            "selected": selected,
                        }
                    )
                    if selected:
                        return {"selected": True, "method": method_name, "attempts": attempts}
                except Exception as exc:
                    self.record_com_call(
                        f"{type(com_object).__name__}.{method_name}",
                        {"reference_id": reference_id, "append": append, "mark": mark},
                        error=exc,
                        started_at=started_at,
                    )
                    attempts.append(
                        {
                            "method": method_name,
                            "index": index,
                            "available": True,
                            "error": str(exc),
                        }
                    )
        return {
            "selected": False,
            "attempts": attempts,
            "failure_reason": "Recorded COM objects could not be selected.",
        }

    def _select_revolve_axis_reference(self, axis_id: Any) -> dict[str, Any]:
        """Select a named revolve axis using the SolidWorks revolve-axis selection mark."""

        clean_id = str(axis_id).strip()
        reference = self._atomic_references.get(clean_id)
        if reference is None and clean_id.lower() in {"x_axis", "y_axis", "z_axis"}:
            self._ensure_builtin_axis_reference(clean_id)
            reference = self._atomic_references.get(clean_id)
        object_type = str(reference.get("object_type") or "AXIS") if isinstance(reference, dict) else "AXIS"
        expected_type = str(reference.get("type") or "axis") if isinstance(reference, dict) else "axis"
        if expected_type == "entity":
            object_type = "SKETCHSEGMENT"
        return self._select_atomic_reference(
            clean_id,
            object_type,
            expected_type,
            append=True,
            mark=16,
            clear_selection=False,
        )

    def _select_atomic_reference_using_registered_type(
        self,
        reference_id: Any,
        default_object_type: str,
        default_expected_type: str,
        *,
        append: bool = False,
        mark: int = 0,
        clear_selection: bool = True,
    ) -> dict[str, Any]:
        """Select an atomic reference while honoring its recorded graph type."""

        clean_id = str(reference_id).strip()
        reference = self._atomic_references.get(clean_id)
        object_type = default_object_type
        expected_type = default_expected_type
        if isinstance(reference, dict):
            object_type = str(reference.get("object_type") or object_type)
            expected_type = str(reference.get("type") or expected_type)
        return self._select_atomic_reference(
            clean_id,
            object_type,
            expected_type,
            append=append,
            mark=mark,
            clear_selection=clear_selection,
        )

    def _ensure_builtin_axis_reference(self, axis_id: str) -> dict[str, Any]:
        """Create and register a SolidWorks reference axis for a built-in graph axis id."""

        clean_id = axis_id.strip().lower()
        existing = self._atomic_references.get(clean_id)
        if existing is not None:
            return existing
        plane_pair = _axis_plane_pair(clean_id)
        if plane_pair is None:
            return {}
        model = self._require_model()
        attempts: list[dict[str, Any]] = []
        try:
            model.ClearSelection2(True)
        except Exception:
            pass
        first_selected = _select_by_id(model, _plane_name_candidates(plane_pair[0]), "PLANE")
        second_selected = _select_by_id(model, _plane_name_candidates(plane_pair[1]), "PLANE", append=True)
        attempts.append(
            {
                "method": "SelectByID2",
                "planes": list(plane_pair),
                "first_selected": first_selected,
                "second_selected": second_selected,
            }
        )
        axis_object = None
        result = None
        method = getattr(model, "InsertAxis2", None)
        if callable(method) and first_selected and second_selected:
            started_at = perf_counter()
            try:
                result = method(True)
                axis_object = (
                    result
                    if result is not None and result is not True and result is not False
                    else _selected_object(model, 1, -1)
                )
                self.record_com_call(
                    "ModelDoc2.InsertAxis2",
                    {"axis_id": clean_id, "planes": list(plane_pair)},
                    result=result,
                    started_at=started_at,
                )
                attempts.append(
                    {
                        "method": "InsertAxis2",
                        "available": True,
                        "created": bool(result) or axis_object is not None,
                        "selected_object_type": type(axis_object).__name__ if axis_object is not None else None,
                    }
                )
            except Exception as exc:
                self.record_com_call(
                    "ModelDoc2.InsertAxis2",
                    {"axis_id": clean_id, "planes": list(plane_pair)},
                    error=exc,
                    started_at=started_at,
                )
                attempts.append({"method": "InsertAxis2", "available": True, "error": str(exc)})
        else:
            attempts.append({"method": "InsertAxis2", "available": callable(method), "created": False})
        registration = self._register_atomic_reference(
            clean_id,
            "axis",
            object_type="AXIS",
            com_objects=[axis_object],
            native_name_candidates=_axis_name_candidates(clean_id, self._atomic_axis_count + 1),
        )
        self._atomic_axis_count += 1
        registration["axis_creation_attempts"] = attempts
        self.record_event(
            "adapter.atomic_reference",
            "completed" if registration.get("registered") else "warning",
            registration,
        )
        return self._atomic_references.get(clean_id, {})

    def _op_create_bom_assembly(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled assembly from generated component parts and record BOM evidence."""

        sw = self._require_sw()
        assembly = self._require_model()
        params = bom_assembly_parameters_from_plan(plan)
        if params is None:
            raise RuntimeError("create_bom_assembly parameters could not be extracted")
        workspace = self._require_workspace()
        components_dir = workspace / "components"
        components_dir.mkdir(parents=True, exist_ok=True)
        component_records: list[dict[str, Any]] = []
        bom_rows: list[dict[str, Any]] = []
        instance_count = 0

        for component_index, component in enumerate(params["components"]):
            part_path = components_dir / f"{safe_output_name(component['part_number'])}.sldprt"
            part_doc = self._new_part_document(sw)
            self._create_controlled_component_geometry(part_doc, component, plan.units)
            self._save_as(part_doc, part_path)
            part_title = self._document_title(part_doc)
            if part_title:
                try:
                    started_at = perf_counter()
                    close_result = sw.CloseDoc(part_title)
                    self.record_com_call(
                        "SldWorks.CloseDoc",
                        {"document_name": part_title, "purpose": "close_generated_component"},
                        result=close_result,
                        started_at=started_at,
                    )
                except Exception as exc:
                    self.record_com_call(
                        "SldWorks.CloseDoc",
                        {"document_name": part_title, "purpose": "close_generated_component"},
                        error=exc,
                    )
                    self._warnings.append(f"close_generated_component:{part_title}:{exc}")

            quantity = int(component["quantity"])
            inserted_instances = []
            for quantity_index in range(quantity):
                x_position = 0.04 * component_index
                y_position = 0.025 * quantity_index
                inserted = self._insert_assembly_component(assembly, part_path, x_position, y_position, 0.0)
                inserted_instances.append(
                    {
                        "component_path": path_to_string(part_path),
                        "inserted": inserted is not None,
                        "x": x_position,
                        "y": y_position,
                        "z": 0.0,
                    }
                )
                if inserted is not None:
                    instance_count += 1
            component_records.append(
                {
                    **component,
                    "path": path_to_string(part_path),
                    "inserted_instances": inserted_instances,
                }
            )
            bom_rows.append(
                {
                    "item": component_index + 1,
                    "component_id": component["id"],
                    "part_number": component["part_number"],
                    "description": component["description"],
                    "quantity": quantity,
                    "material": component["material"],
                }
            )

        self._active_part_path = self._require_workspace() / "exports" / f"{safe_output_name(plan.name)}.sldasm"
        self._save_as(assembly, self._active_part_path)
        self._active_part_title = self._document_title(assembly) or self._active_part_path.name
        self._assembly_result = {
            "status": "assembly_verified" if instance_count >= 2 else "assembly_incomplete",
            "method": "solidworks_generated_components",
            "component_definition_count": len(component_records),
            "component_instance_count": instance_count,
            "component_definitions": component_records,
            "mates": operation.parameters.get("mates", []),
            "checks": {
                "component_count_positive": instance_count >= 2,
                "all_components_have_paths": all(Path(record["path"]).exists() for record in component_records),
            },
        }
        self._bom_result = {
            "status": "bom_verified" if len(bom_rows) >= 2 else "bom_incomplete",
            "method": "solidworks_mcp_generated_bom_csv",
            "columns": params["bom"]["columns"],
            "row_count": len(bom_rows),
            "rows": bom_rows,
            "export_formats": params["bom"]["export_formats"],
        }
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "not_requested"
        self._drawing_annotation_result = {
            "status": "not_requested",
            "created_callout_count": 0,
            "direct_hole_callout_created": None,
            "reason": "controlled_assembly_bom_has_no_part_hole_callout_gate",
        }
        self._model_geometry_status = "not_requested"
        self._model_geometry_result = {"status": "not_requested", "reason": "assembly workflow uses assembly_result"}
        self._mass_property_status = "not_requested"
        self._mass_property_result = {"status": "not_requested", "reason": "assembly workflow uses BOM/component evidence"}
        self.record_event("assembly.create", "completed", self._assembly_result)
        self.record_event("bom.create", "completed", self._bom_result)
        return {
            "assembly_result": self._assembly_result,
            "bom_result": self._bom_result,
        }

    def _create_controlled_component_geometry(self, part_doc: Any, component: dict[str, Any], units: str) -> None:
        """Create simple real component geometry for controlled assembly fixtures."""

        dimensions = component["dimensions"]
        kind = component["kind"]
        sketch = part_doc.SketchManager
        part_doc.ClearSelection2(True)
        sketch.InsertSketch(True)
        if kind == "spacer":
            sketch.CreateCircleByRadius(0, 0, 0, _to_meters(float(dimensions["outer_diameter"]) / 2, units))
            sketch.CreateCircleByRadius(0, 0, 0, _to_meters(float(dimensions["inner_diameter"]) / 2, units))
            depth = float(dimensions["length"])
        else:
            length = float(dimensions.get("length") or dimensions.get("base_length"))
            width = float(dimensions.get("width") or dimensions.get("base_width"))
            sketch.CreateCenterRectangle(
                0,
                0,
                0,
                _to_meters(length / 2, units),
                _to_meters(width / 2, units),
                0,
            )
            depth = float(dimensions.get("thickness") or dimensions.get("base_thickness"))
        sketch.InsertSketch(True)
        feature = part_doc.FeatureManager.FeatureExtrusion2(
            True,
            False,
            False,
            SW_END_COND_BLIND,
            0,
            _to_meters(depth, units),
            0,
            False,
            False,
            False,
            False,
            0,
            0,
            False,
            False,
            False,
            False,
            True,
            True,
            True,
            0,
            0,
            False,
        )
        if feature is None:
            raise RuntimeError(f"Failed to create component geometry for {component['id']}")

    def _insert_assembly_component(self, assembly: Any, path: Path, x: float, y: float, z: float) -> Any | None:
        """Insert one component into an assembly using available SolidWorks COM signatures."""

        for method_name, args in (
            ("AddComponent5", (str(path), 0, "", False, "", x, y, z)),
            ("AddComponent4", (str(path), "", x, y, z)),
            ("AddComponent", (str(path), x, y, z)),
        ):
            method = getattr(assembly, method_name, None)
            if not callable(method):
                continue
            started_at = perf_counter()
            try:
                component = method(*args)
                self.record_com_call(
                    f"AssemblyDoc.{method_name}",
                    {"path": path, "x": x, "y": y, "z": z},
                    result=component,
                    started_at=started_at,
                )
                if component is not None:
                    return component
            except Exception as exc:
                self.record_com_call(
                    f"AssemblyDoc.{method_name}",
                    {"path": path, "x": x, "y": y, "z": z},
                    error=exc,
                    started_at=started_at,
                )
                continue
        return None

    def _op_create_mounting_plate(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create the MVP mounting plate template as a reproducible smoke part.

        The operation expands one high-level AI-friendly command into explicit
        SolidWorks steps.  Rounded corners are created directly in the base
        sketch so the first smoke test does not depend on fragile edge selection.
        Threaded holes try HoleWizard first, optionally try a macro fallback,
        then degrade to sketch-cut geometry while recording the loss of thread
        semantics in the execution report.
        """

        params = operation.parameters
        length = float(params["length"])
        width = float(params["width"])
        thickness = float(params["thickness"])
        corner_radius = float(params["corner_radius"])
        edge_offset = float(params["edge_offset"])
        thread_spec = str(params["thread_spec"]).upper()

        self._create_rounded_plate_body(length, width, thickness, corner_radius, plan)
        hole_points = _four_corner_hole_points(length, width, edge_offset)
        hole_result = self._create_threaded_holes_or_fallback(hole_points, thread_spec, thickness, plan)
        return {
            "template": "mounting_plate",
            "length": length,
            "width": width,
            "thickness": thickness,
            "corner_radius": corner_radius,
            "hole_points": hole_points,
            "thread_spec": thread_spec,
            "semantic_selectors": ["top_face", "outer_edges"],
            "hole_result": hole_result,
        }

    def _op_create_center_hole_flange(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled cylindrical flange with a concentric through hole."""

        params = operation.parameters
        outer_diameter = float(params["outer_diameter"])
        thickness = float(params["thickness"])
        hole_diameter = float(params["hole_diameter"])
        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for center-hole flange sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(outer_diameter / 2, plan.units))
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(hole_diameter / 2, plan.units))
        sketch.InsertSketch(True)

        depth_m = _to_meters(thickness, plan.units)
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("Center-hole flange base extrusion failed.")
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        return {
            "template": "center_hole_flange",
            "outer_diameter": outer_diameter,
            "hole_diameter": hole_diameter,
            "thickness": thickness,
            "semantic_selectors": ["front_face", "center_hole"],
        }

    def _op_create_center_hole_plate(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled rectangular plate with a concentric through hole."""

        params = operation.parameters
        length = float(params["length"])
        width = float(params["width"])
        thickness = float(params["thickness"])
        hole_diameter = float(params["hole_diameter"])
        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for center-hole plate sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        sketch.CreateCenterRectangle(
            0,
            0,
            0,
            _to_meters(length / 2, plan.units),
            _to_meters(width / 2, plan.units),
            0,
        )
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(hole_diameter / 2, plan.units))
        sketch.InsertSketch(True)

        depth_m = _to_meters(thickness, plan.units)
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("Center-hole plate base extrusion failed.")
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        return {
            "template": "center_hole_plate",
            "length": length,
            "width": width,
            "hole_diameter": hole_diameter,
            "thickness": thickness,
            "semantic_selectors": ["front_face", "center_hole"],
        }

    def _op_create_bracket(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled L bracket with one base hole and one upright hole."""

        params = operation.parameters
        base_length = float(params["base_length"])
        base_width = float(params["base_width"])
        base_thickness = float(params["base_thickness"])
        upright_height = float(params["upright_height"])
        upright_thickness = float(params["upright_thickness"])
        hole_diameter = float(params["hole_diameter"])
        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for bracket sketch.")
        sketch = model.SketchManager
        left = -base_length / 2
        right = base_length / 2
        bottom = 0.0
        base_top = base_thickness
        upright_right = left + upright_thickness
        top = upright_height
        points = [
            (left, bottom),
            (right, bottom),
            (right, base_top),
            (upright_right, base_top),
            (upright_right, top),
            (left, top),
            (left, bottom),
        ]
        sketch.InsertSketch(True)
        for start, end in zip(points, points[1:]):
            sketch.CreateLine(
                _to_meters(start[0], plan.units),
                _to_meters(start[1], plan.units),
                0,
                _to_meters(end[0], plan.units),
                _to_meters(end[1], plan.units),
                0,
            )
        sketch.InsertSketch(True)

        depth_m = _to_meters(base_width, plan.units)
        started_at = perf_counter()
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        self.record_com_call(
            "FeatureManager.FeatureExtrusion2",
            {"purpose": "controlled_bracket_body", "depth_m": depth_m},
            result=feature,
            started_at=started_at,
        )
        if feature is None:
            raise RuntimeError("Bracket base extrusion failed.")
        hole_points = [
            (0.0, base_thickness / 2),
            (left + upright_thickness / 2, (base_thickness + upright_height) / 2),
        ]
        hole_cut_result = self._cut_circular_profiles_through_depth(
            hole_points,
            hole_diameter,
            base_width,
            plan,
            purpose="controlled_bracket_holes",
        )
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        return {
            "template": "bracket",
            "base_length": base_length,
            "base_width": base_width,
            "base_thickness": base_thickness,
            "upright_height": upright_height,
            "upright_thickness": upright_thickness,
            "hole_diameter": hole_diameter,
            "hole_points": hole_points,
            "hole_cut_result": hole_cut_result,
            "semantic_selectors": ["front_face", "base_hole", "upright_hole", "outer_edges"],
        }

    def _op_create_slotted_array_plate(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled rectangular plate with a center slot and hole array."""

        params = operation.parameters
        length = float(params["length"])
        width = float(params["width"])
        thickness = float(params["thickness"])
        slot_length = float(params["slot_length"])
        slot_width = float(params["slot_width"])
        hole_diameter = float(params["hole_diameter"])
        rows = int(params["hole_rows"])
        columns = int(params["hole_columns"])
        spacing_x = float(params["hole_spacing_x"])
        spacing_y = float(params["hole_spacing_y"])
        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for slotted-array plate sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        sketch.CreateCenterRectangle(
            0,
            0,
            0,
            _to_meters(length / 2, plan.units),
            _to_meters(width / 2, plan.units),
            0,
        )
        sketch.InsertSketch(True)

        depth_m = _to_meters(thickness, plan.units)
        started_at = perf_counter()
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        self.record_com_call(
            "FeatureManager.FeatureExtrusion2",
            {"purpose": "controlled_slotted_array_plate_body", "depth_m": depth_m},
            result=feature,
            started_at=started_at,
        )
        if feature is None:
            raise RuntimeError("Slotted-array plate base extrusion failed.")
        slot_cut_result = self._cut_straight_slot_through_depth(
            slot_length,
            slot_width,
            thickness,
            plan,
            purpose="controlled_slotted_array_plate_center_slot",
        )
        hole_points = [
            (
                (column - (columns - 1) / 2) * spacing_x,
                (row - (rows - 1) / 2) * spacing_y,
            )
            for row in range(rows)
            for column in range(columns)
        ]
        hole_cut_result = self._cut_circular_profiles_through_depth(
            hole_points,
            hole_diameter,
            thickness,
            plan,
            purpose="controlled_slotted_array_plate_hole_array",
        )
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        return {
            "template": "slotted_array_plate",
            "length": length,
            "width": width,
            "thickness": thickness,
            "slot_length": slot_length,
            "slot_width": slot_width,
            "hole_diameter": hole_diameter,
            "hole_rows": rows,
            "hole_columns": columns,
            "hole_spacing_x": spacing_x,
            "hole_spacing_y": spacing_y,
            "hole_points": hole_points,
            "slot_cut_result": slot_cut_result,
            "hole_cut_result": hole_cut_result,
            "semantic_selectors": ["front_face", "center_slot", "hole_array"],
        }

    def _op_create_end_cap(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled circular end cap with a center bore and bolt-hole pattern."""

        params = operation.parameters
        outer_diameter = float(params["outer_diameter"])
        thickness = float(params["thickness"])
        center_hole_diameter = float(params["center_hole_diameter"])
        bolt_circle_diameter = float(params["bolt_circle_diameter"])
        bolt_hole_diameter = float(params["bolt_hole_diameter"])
        bolt_hole_count = int(params["bolt_hole_count"])
        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for end-cap sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(outer_diameter / 2, plan.units))
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(center_hole_diameter / 2, plan.units))
        bolt_radius = bolt_circle_diameter / 2
        for index in range(bolt_hole_count):
            angle = 2 * math.pi * index / bolt_hole_count
            x = _to_meters(math.cos(angle) * bolt_radius, plan.units)
            y = _to_meters(math.sin(angle) * bolt_radius, plan.units)
            sketch.CreateCircleByRadius(x, y, 0, _to_meters(bolt_hole_diameter / 2, plan.units))
        sketch.InsertSketch(True)

        depth_m = _to_meters(thickness, plan.units)
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("End-cap base extrusion failed.")
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        return {
            "template": "end_cap",
            "outer_diameter": outer_diameter,
            "thickness": thickness,
            "center_hole_diameter": center_hole_diameter,
            "bolt_circle_diameter": bolt_circle_diameter,
            "bolt_hole_diameter": bolt_hole_diameter,
            "bolt_hole_count": bolt_hole_count,
            "semantic_selectors": ["front_face", "center_hole", "bolt_hole_pattern"],
        }

    def _op_create_mounting_block(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled rectangular mounting block with a concentric through hole."""

        params = operation.parameters
        length = float(params["length"])
        width = float(params["width"])
        height = float(params["height"])
        hole_diameter = float(params["hole_diameter"])
        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for mounting block sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        sketch.CreateCenterRectangle(
            0,
            0,
            0,
            _to_meters(length / 2, plan.units),
            _to_meters(width / 2, plan.units),
            0,
        )
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(hole_diameter / 2, plan.units))
        sketch.InsertSketch(True)

        depth_m = _to_meters(height, plan.units)
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("Mounting block base extrusion failed.")
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        return {
            "template": "mounting_block",
            "length": length,
            "width": width,
            "height": height,
            "hole_diameter": hole_diameter,
            "semantic_selectors": ["front_face", "center_hole"],
        }

    def _op_create_shaft(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled plain shaft as a cylindrical solid."""

        params = operation.parameters
        diameter = float(params["diameter"])
        length = float(params["length"])
        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for shaft sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(diameter / 2, plan.units))
        sketch.InsertSketch(True)

        depth_m = _to_meters(length, plan.units)
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("Shaft base extrusion failed.")
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        return {
            "template": "shaft",
            "diameter": diameter,
            "length": length,
            "semantic_selectors": ["front_face", "outer_cylinder"],
        }

    def _op_create_sheet_metal_base_flange(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled sheet-metal base flange and require real sheet-metal evidence."""

        params = sheet_metal_base_flange_parameters_from_plan(plan)
        if params is None:
            raise RuntimeError("create_sheet_metal_base_flange parameters could not be extracted")
        length = float(params["length"])
        width = float(params["width"])
        thickness = float(params["thickness"])
        bend_radius = float(params["bend_radius"])
        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for sheet-metal base-flange sketch.")
        sketch = model.SketchManager
        model.ClearSelection2(True)
        _select_by_id(model, _plane_name_candidates("front"), "PLANE")
        sketch.InsertSketch(True)
        sketch.CreateCenterRectangle(
            0,
            0,
            0,
            _to_meters(length / 2, plan.units),
            _to_meters(width / 2, plan.units),
            0,
        )
        sketch.InsertSketch(True)

        feature_result = self._insert_sheet_metal_base_flange(params, plan)
        if feature_result.get("ok") is not True:
            self._sheet_metal_result = {
                "status": "sheet_metal_failed",
                "method": "solidworks_insert_sheet_metal_base_flange2",
                "base_flange_created": False,
                "attempts": feature_result.get("attempts", []),
                "failure_reason": feature_result.get("failure_reason"),
            }
            self.record_event("sheet_metal.base_flange", "failed", self._sheet_metal_result)
            raise RuntimeError(str(feature_result.get("failure_reason") or "Sheet-metal base flange failed."))

        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "not_requested"
        self._drawing_annotation_result = {
            "status": "not_requested",
            "created_callout_count": 0,
            "direct_hole_callout_created": None,
            "callout_creation_method": None,
            "reason": "controlled_sheet_metal_base_flange_has_no_holes",
        }
        self._sheet_metal_result = {
            "status": "sheet_metal_verified",
            "method": "solidworks_insert_sheet_metal_base_flange2",
            "base_flange_created": True,
            "feature_name": feature_result.get("feature_name"),
            "feature_type": feature_result.get("feature_type"),
            "thickness_mm": thickness,
            "bend_radius_mm": bend_radius,
            "flat_pattern_result": {"status": "pending_export", "ok": False, "format": "dxf"},
            "attempts": feature_result.get("attempts", []),
        }
        self.record_event("sheet_metal.base_flange", "completed", self._sheet_metal_result)
        return {
            "template": "sheet_metal_base_flange",
            "length": length,
            "width": width,
            "thickness": thickness,
            "bend_radius": bend_radius,
            "semantic_selectors": ["front_face", "flat_pattern"],
            "sheet_metal_result": self._sheet_metal_result,
        }

    def _op_create_weldment_frame(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled rectangular structural-member weldment frame."""

        import pythoncom
        import win32com.client

        params = weldment_frame_parameters_from_plan(plan)
        if params is None:
            raise RuntimeError("create_weldment_frame parameters could not be extracted")
        model = self._require_model()
        profile_result = _resolve_weldment_profile(params)
        if not profile_result.get("path"):
            raise RuntimeError(str(profile_result.get("failure_reason") or "No weldment profile path was found."))

        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for weldment frame sketch.")
        sketch = model.SketchManager
        centerline_length = float(params["centerline_length"])
        centerline_width = float(params["centerline_width"])
        half_length_m = _to_meters(centerline_length / 2, plan.units)
        half_width_m = _to_meters(centerline_width / 2, plan.units)
        points = [
            (-half_length_m, -half_width_m),
            (half_length_m, -half_width_m),
            (half_length_m, half_width_m),
            (-half_length_m, half_width_m),
            (-half_length_m, -half_width_m),
        ]
        sketch.InsertSketch(True)
        segments = []
        for start, end in zip(points, points[1:]):
            segments.append(sketch.CreateLine(start[0], start[1], 0, end[0], end[1], 0))
        sketch.InsertSketch(True)

        null_dispatch = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        selected_segments = 0
        model.ClearSelection2(True)
        for segment in segments:
            if segment is None:
                continue
            started_at = perf_counter()
            try:
                selected = segment.Select4(True, null_dispatch)
                self.record_com_call(
                    "SketchSegment.Select4",
                    {"purpose": "controlled_weldment_frame", "append": True},
                    result=selected,
                    started_at=started_at,
                )
            except Exception as exc:
                self.record_com_call(
                    "SketchSegment.Select4",
                    {"purpose": "controlled_weldment_frame", "append": True},
                    error=exc,
                    started_at=started_at,
                )
                selected = False
            if selected:
                selected_segments += 1
        if selected_segments != 4:
            raise RuntimeError(f"Expected 4 weldment path sketch segments, selected {selected_segments}.")

        started_at = perf_counter()
        try:
            feature = model.FeatureManager.InsertStructuralWeldment2(
                str(profile_result["path"]),
                1,
                0,
                False,
            )
            self.record_com_call(
                "FeatureManager.InsertStructuralWeldment2",
                {
                    "purpose": "controlled_weldment_frame",
                    "profile_path": profile_result["path"],
                    "selected_segments": selected_segments,
                },
                result=feature,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "FeatureManager.InsertStructuralWeldment2",
                {
                    "purpose": "controlled_weldment_frame",
                    "profile_path": profile_result["path"],
                    "selected_segments": selected_segments,
                },
                error=exc,
                started_at=started_at,
            )
            raise
        finally:
            model.ClearSelection2(True)
        if feature is None:
            raise RuntimeError("InsertStructuralWeldment2 returned no weldment feature.")

        try:
            model.ForceRebuild3(False)
        except Exception as exc:
            self._warnings.append(f"weldment_rebuild:{exc}")
        feature_type = _call_or_get(feature, "GetTypeName2") or _call_or_get(feature, "GetTypeName")
        body_count = _solid_body_count(model)
        rows = _weldment_cut_list_rows(params, plan)
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "not_requested"
        self._drawing_annotation_result = {
            "status": "not_requested",
            "created_callout_count": 0,
            "direct_hole_callout_created": None,
            "callout_creation_method": None,
            "reason": "controlled_weldment_frame_has_no_holes",
        }
        self._weldment_result = {
            "status": "weldment_verified"
            if str(feature_type) == "WeldMemberFeat" and body_count >= 4
            else "weldment_incomplete",
            "method": "solidworks_insert_structural_weldment2",
            "structural_member_created": str(feature_type) == "WeldMemberFeat",
            "feature_type": feature_type,
            "body_count": body_count,
            "member_count": 4,
            "selected_segment_count": selected_segments,
            "profile": params["profile"],
            "profile_result": profile_result,
        }
        self._cut_list_result = {
            "status": "cut_list_verified" if len(rows) >= 2 and body_count >= 4 else "cut_list_incomplete",
            "method": "solidworks_mcp_weldment_cut_list_csv",
            "row_count": len(rows),
            "columns": params["cut_list"]["columns"],
            "rows": rows,
            "export_formats": params["cut_list"]["export_formats"],
            "source_evidence": "WeldMemberFeat body readback plus controlled frame parameters",
        }
        self.record_event("weldment.create", "completed", self._weldment_result)
        self.record_event("weldment.cut_list", "completed", self._cut_list_result)
        return {
            "template": "weldment_frame",
            "length": params["length"],
            "width": params["width"],
            "centerline_length": params["centerline_length"],
            "centerline_width": params["centerline_width"],
            "profile": params["profile"],
            "semantic_selectors": ["front_face", "weldment_members", "cut_list"],
            "weldment_result": self._weldment_result,
            "cut_list_result": self._cut_list_result,
        }

    def _op_run_static_simulation(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create the controlled cantilever beam and require real Simulation API evidence."""

        params = static_simulation_parameters_from_plan(plan)
        if params is None:
            raise RuntimeError("run_static_simulation parameters could not be extracted")
        self._create_static_simulation_beam_body(params, plan)
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "not_requested"
        self._drawing_annotation_result = {
            "status": "not_requested",
            "created_callout_count": 0,
            "direct_hole_callout_created": None,
            "callout_creation_method": None,
            "reason": "controlled_static_simulation_has_no_holes",
        }
        api_check = _simulation_api_preflight_check(self._sw)
        if api_check.get("ok") is not True:
            self._simulation_result = {
                "status": "simulation_api_unavailable",
                "method": "solidworks_simulation_api_probe",
                "study_type": "static",
                "study_name": "cantilever_static_baseline",
                "api_check": api_check,
                "failure_reason": api_check.get("message"),
            }
            self.record_event("simulation.static_study", "failed", self._simulation_result)
            raise RuntimeError(str(api_check.get("message") or "SolidWorks Simulation API is not available."))

        self._simulation_result = {
            "status": "simulation_api_probe_only",
            "method": "solidworks_simulation_api_probe",
            "study_type": "static",
            "study_name": "cantilever_static_baseline",
            "api_check": api_check,
            "failure_reason": (
                "SolidWorks Simulation API was detected, but controlled static study creation/result readback "
                "is not implemented yet."
            ),
        }
        self.record_event("simulation.static_study", "failed", self._simulation_result)
        raise RuntimeError(str(self._simulation_result["failure_reason"]))

    def _create_static_simulation_beam_body(self, params: dict[str, Any], plan: ModelPlan) -> None:
        """Sketch a rectangular cantilever beam section and extrude it to length."""

        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for static simulation beam sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        sketch.CreateCenterRectangle(
            0,
            0,
            0,
            _to_meters(float(params["width"]) / 2, plan.units),
            _to_meters(float(params["height"]) / 2, plan.units),
            0,
        )
        sketch.InsertSketch(True)
        depth_m = _to_meters(float(params["length"]), plan.units)
        started_at = perf_counter()
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        self.record_com_call(
            "FeatureManager.FeatureExtrusion2",
            {
                "purpose": "controlled_static_simulation_beam",
                "length_m": depth_m,
                "width_m": _to_meters(float(params["width"]), plan.units),
                "height_m": _to_meters(float(params["height"]), plan.units),
            },
            result=feature,
            started_at=started_at,
        )
        if feature is None:
            raise RuntimeError("Static simulation beam extrusion failed.")

    def _insert_sheet_metal_base_flange(self, params: dict[str, float], plan: ModelPlan) -> dict[str, Any]:
        """Call the SolidWorks sheet-metal base-flange API with audited evidence."""

        model = self._require_model()
        feature_manager = model.FeatureManager
        method = getattr(feature_manager, "InsertSheetMetalBaseFlange2", None)
        attempts: list[dict[str, Any]] = []
        if not callable(method):
            return {
                "ok": False,
                "attempts": attempts,
                "failure_reason": "FeatureManager.InsertSheetMetalBaseFlange2 is unavailable.",
            }
        thickness_m = _to_meters(params["thickness"], plan.units)
        radius_m = _to_meters(params["bend_radius"], plan.units)
        relief_width_m = _to_meters(params.get("relief_width", params["thickness"]), plan.units)
        relief_depth_m = _to_meters(params.get("relief_depth", params["thickness"]), plan.units)
        k_factor = float(params.get("k_factor", 0.5))
        definition_result = self._insert_sheet_metal_base_flange_definition(
            feature_manager,
            thickness_m,
            radius_m,
            relief_width_m,
            relief_depth_m,
            k_factor,
            attempts,
        )
        if definition_result.get("ok") is True:
            return definition_result

        bend_allowance = self._create_sheet_metal_bend_allowance(feature_manager, k_factor, attempts)
        pcba_candidates: list[tuple[str, Any]] = []
        if bend_allowance is not None:
            pcba_candidates.append(("custom_bend_allowance", bend_allowance))
            dispatch_variant = _dispatch_variant_or_none(bend_allowance)
            if dispatch_variant is not None:
                pcba_candidates.append(("custom_bend_allowance_variant", dispatch_variant))
        else:
            pcba_candidates.append(("missing_custom_bend_allowance", None))

        feature = None
        feature_variant: str | None = None
        last_error: str | None = None
        for variant_name, pcba in pcba_candidates:
            call_args = (
                thickness_m,
                False,
                radius_m,
                0.0,
                0.0,
                False,
                SW_END_COND_BLIND,
                SW_END_COND_BLIND,
                0,
                pcba,
                True,
                1,
                relief_width_m,
                relief_depth_m,
                0.5,
                False,
                True,
                False,
                True,
            )
            started_at = perf_counter()
            try:
                feature = method(*call_args)
                feature_variant = variant_name
                self.record_com_call(
                    "FeatureManager.InsertSheetMetalBaseFlange2",
                    {
                        "purpose": "controlled_sheet_metal_base_flange",
                        "variant": variant_name,
                        "thickness_m": thickness_m,
                        "bend_radius_m": radius_m,
                        "relief_width_m": relief_width_m,
                        "relief_depth_m": relief_depth_m,
                        "k_factor": k_factor,
                    },
                    result=feature,
                    started_at=started_at,
                )
            except Exception as exc:
                last_error = str(exc)
                self.record_com_call(
                    "FeatureManager.InsertSheetMetalBaseFlange2",
                    {
                        "purpose": "controlled_sheet_metal_base_flange",
                        "variant": variant_name,
                        "thickness_m": thickness_m,
                        "bend_radius_m": radius_m,
                        "k_factor": k_factor,
                    },
                    error=exc,
                    started_at=started_at,
                )
                attempts.append(
                    {
                        "method": "InsertSheetMetalBaseFlange2",
                        "variant": variant_name,
                        "ok": False,
                        "error": str(exc),
                    }
                )
                continue
            if feature is not None:
                break
            attempts.append(
                {
                    "method": "InsertSheetMetalBaseFlange2",
                    "variant": variant_name,
                    "ok": False,
                    "error": "InsertSheetMetalBaseFlange2 returned no feature.",
                }
            )

        if feature is None:
            return {
                "ok": False,
                "attempts": attempts,
                "failure_reason": last_error or "InsertSheetMetalBaseFlange2 returned no feature.",
            }
        feature_name = _call_or_get(feature, "Name")
        feature_type = _call_or_get(feature, "GetTypeName2") or _call_or_get(feature, "GetTypeName")
        attempts.append(
            {
                "method": "InsertSheetMetalBaseFlange2",
                "variant": feature_variant,
                "ok": True,
                "feature_name": feature_name,
                "feature_type": feature_type,
            }
        )
        return {
            "ok": True,
            "attempts": attempts,
            "feature_name": str(feature_name or "Base-Flange1"),
            "feature_type": str(feature_type or ""),
        }

    def _insert_sheet_metal_base_flange_definition(
        self,
        feature_manager: Any,
        thickness_m: float,
        radius_m: float,
        relief_width_m: float,
        relief_depth_m: float,
        k_factor: float,
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create BaseFlange through modern feature-data CreateDefinition/CreateFeature."""

        started_at = perf_counter()
        try:
            feature_data = feature_manager.CreateDefinition(SW_FM_BASE_FLANGE)
            self.record_com_call(
                "FeatureManager.CreateDefinition",
                {
                    "purpose": "controlled_sheet_metal_base_flange",
                    "feature_name_id": SW_FM_BASE_FLANGE,
                },
                result=feature_data,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "FeatureManager.CreateDefinition",
                {
                    "purpose": "controlled_sheet_metal_base_flange",
                    "feature_name_id": SW_FM_BASE_FLANGE,
                },
                error=exc,
                started_at=started_at,
            )
            attempts.append({"method": "CreateDefinition(swFmBaseFlange)", "ok": False, "error": str(exc)})
            return {"ok": False, "failure_reason": str(exc), "attempts": attempts}

        if feature_data is None:
            attempts.append(
                {
                    "method": "CreateDefinition(swFmBaseFlange)",
                    "ok": False,
                    "error": "CreateDefinition returned no feature-data object.",
                }
            )
            return {"ok": False, "failure_reason": "CreateDefinition returned no feature-data object.", "attempts": attempts}

        property_attempts: dict[str, Any] = {}
        for property_name, value in (
            ("Thickness", thickness_m),
            ("BendRadius", radius_m),
            ("KFactor", k_factor),
            ("UseGaugeTable", False),
            ("ReverseDirection", False),
        ):
            try:
                setattr(feature_data, property_name, value)
                property_attempts[property_name] = {"ok": True, "value": value}
            except Exception as exc:
                property_attempts[property_name] = {"ok": False, "error": str(exc), "value": value}

        null_dispatch = _dispatch_variant_or_none(None)
        init_args = (
            False,
            False,
            null_dispatch,
            True,
            1,
            False,
            0.5,
            relief_width_m,
            relief_depth_m,
        )
        started_at = perf_counter()
        try:
            init_result = feature_data.Initialize(*init_args)
            self.record_com_call(
                "IBaseFlangeFeatureData.Initialize",
                {
                    "purpose": "controlled_sheet_metal_base_flange",
                    "use_material_sheet_metal_parameters": False,
                    "override_default_bend_allowance": False,
                    "override_default_bend_relief": True,
                    "relief_type": 1,
                    "use_relief_ratio": False,
                    "relief_ratio": 0.5,
                    "relief_width_m": relief_width_m,
                    "relief_depth_m": relief_depth_m,
                },
                result=init_result,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "IBaseFlangeFeatureData.Initialize",
                {
                    "purpose": "controlled_sheet_metal_base_flange",
                    "override_default_bend_allowance": False,
                    "relief_width_m": relief_width_m,
                    "relief_depth_m": relief_depth_m,
                },
                error=exc,
                started_at=started_at,
            )
            attempts.append(
                {
                    "method": "CreateDefinition(swFmBaseFlange)",
                    "stage": "Initialize",
                    "ok": False,
                    "error": str(exc),
                    "property_attempts": property_attempts,
                }
            )
            return {"ok": False, "failure_reason": str(exc), "attempts": attempts}

        started_at = perf_counter()
        try:
            feature = feature_manager.CreateFeature(feature_data)
            self.record_com_call(
                "FeatureManager.CreateFeature",
                {"purpose": "controlled_sheet_metal_base_flange", "feature_name_id": SW_FM_BASE_FLANGE},
                result=feature,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "FeatureManager.CreateFeature",
                {"purpose": "controlled_sheet_metal_base_flange", "feature_name_id": SW_FM_BASE_FLANGE},
                error=exc,
                started_at=started_at,
            )
            attempts.append(
                {
                    "method": "CreateDefinition(swFmBaseFlange)",
                    "stage": "CreateFeature",
                    "ok": False,
                    "error": str(exc),
                    "property_attempts": property_attempts,
                }
            )
            return {"ok": False, "failure_reason": str(exc), "attempts": attempts}

        feature_name = _call_or_get(feature, "Name")
        feature_type = _call_or_get(feature, "GetTypeName2") or _call_or_get(feature, "GetTypeName")
        attempts.append(
            {
                "method": "CreateDefinition(swFmBaseFlange)",
                "ok": feature is not None,
                "feature_name": feature_name,
                "feature_type": feature_type,
                "property_attempts": property_attempts,
            }
        )
        if feature is None:
            return {
                "ok": False,
                "failure_reason": "CreateFeature returned no BaseFlange feature.",
                "attempts": attempts,
            }
        return {
            "ok": True,
            "attempts": attempts,
            "feature_name": str(feature_name or "Base-Flange1"),
            "feature_type": str(feature_type or ""),
        }

    def _create_sheet_metal_bend_allowance(
        self,
        feature_manager: Any,
        k_factor: float,
        attempts: list[dict[str, Any]],
    ) -> Any:
        """Create the CustomBendAllowance COM object required by BaseFlange2."""

        method = getattr(feature_manager, "CreateCustomBendAllowance", None)
        if not callable(method):
            attempts.append(
                {
                    "method": "CreateCustomBendAllowance",
                    "ok": False,
                    "error": "FeatureManager.CreateCustomBendAllowance is unavailable.",
                }
            )
            return None
        started_at = perf_counter()
        try:
            bend_allowance = method()
            self.record_com_call(
                "FeatureManager.CreateCustomBendAllowance",
                {
                    "purpose": "controlled_sheet_metal_base_flange",
                    "k_factor": k_factor,
                },
                result=bend_allowance,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "FeatureManager.CreateCustomBendAllowance",
                {
                    "purpose": "controlled_sheet_metal_base_flange",
                    "k_factor": k_factor,
                },
                error=exc,
                started_at=started_at,
            )
            attempts.append({"method": "CreateCustomBendAllowance", "ok": False, "error": str(exc)})
            return None
        if bend_allowance is None:
            attempts.append(
                {
                    "method": "CreateCustomBendAllowance",
                    "ok": False,
                    "error": "CreateCustomBendAllowance returned no object.",
                }
            )
            return None
        try:
            bend_allowance.KFactor = k_factor
            k_factor_readback = _call_or_get(bend_allowance, "KFactor")
        except Exception as exc:
            attempts.append(
                {
                    "method": "CustomBendAllowance.KFactor",
                    "ok": False,
                    "error": str(exc),
                }
            )
            return None
        attempts.append(
            {
                "method": "CreateCustomBendAllowance",
                "ok": True,
                "k_factor": k_factor,
                "k_factor_readback": k_factor_readback,
            }
        )
        return bend_allowance

    def _op_create_washer(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled washer as a thin ring with a concentric through hole."""

        params = operation.parameters
        outer_diameter = float(params["outer_diameter"])
        inner_diameter = float(params["inner_diameter"])
        thickness = float(params["thickness"])
        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for washer sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(outer_diameter / 2, plan.units))
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(inner_diameter / 2, plan.units))
        sketch.InsertSketch(True)

        depth_m = _to_meters(thickness, plan.units)
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("Washer base extrusion failed.")
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        return {
            "template": "washer",
            "outer_diameter": outer_diameter,
            "inner_diameter": inner_diameter,
            "thickness": thickness,
            "semantic_selectors": ["front_face", "inner_hole"],
        }

    def _op_create_sleeve(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a controlled sleeve as a cylindrical tube with a concentric bore."""

        params = operation.parameters
        outer_diameter = float(params["outer_diameter"])
        inner_diameter = float(params["inner_diameter"])
        length = float(params["length"])
        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for sleeve sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(outer_diameter / 2, plan.units))
        sketch.CreateCircleByRadius(0, 0, 0, _to_meters(inner_diameter / 2, plan.units))
        sketch.InsertSketch(True)

        depth_m = _to_meters(length, plan.units)
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("Sleeve base extrusion failed.")
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        return {
            "template": "sleeve",
            "outer_diameter": outer_diameter,
            "inner_diameter": inner_diameter,
            "length": length,
            "semantic_selectors": ["front_face", "inner_bore"],
        }

    def _inspect_controlled_model_geometry(self) -> dict[str, Any]:
        """Read the active part bounding box and compare it to the controlled plan."""

        plan = self._active_plan
        if plan is None:
            return {"status": "not_requested", "failure_reason": "No controlled geometry operation was executed."}
        try:
            if self._config.force_model_geometry_failure:
                result = _controlled_model_geometry_result(
                    plan,
                    {
                        "status": "geometry_mismatch",
                        "body_count": 0,
                        "failure_reason": "SOLIDWORKS_MCP_FORCE_MODEL_GEOMETRY_FAILURE is enabled",
                    },
                )
                self._warnings.append(f"model_geometry:{result['status']}")
                self.record_event("diagnostics.model_geometry", "failed", result)
                return result
            self._activate_part_document()
            model = self._require_model()
            measured = _read_model_bounding_box(model)
            result = _controlled_model_geometry_result(plan, measured)
            event_status = "completed" if result.get("status") == "geometry_verified" else "failed"
            if event_status == "failed":
                self._warnings.append(f"model_geometry:{result['status']}")
            self.record_event("diagnostics.model_geometry", event_status, result)
            return result
        except Exception as exc:
            result = _controlled_model_geometry_result(
                plan,
                {"status": "geometry_readback_failed", "failure_reason": str(exc)},
            )
            self._warnings.append(f"model_geometry:{result['status']}")
            self.record_event("diagnostics.model_geometry", "failed", result)
            return result

    def _inspect_mass_properties(self) -> dict[str, Any]:
        """Read positive mass, volume, and area signals from the active SolidWorks part."""

        plan = self._active_plan
        if plan is None or not (_has_controlled_geometry_operation(plan) or _is_atomic_model_plan(plan)):
            return {"status": "not_requested", "failure_reason": "No controlled geometry operation was executed."}
        if self._config.force_model_geometry_failure:
            result = {
                "status": "mass_property_invalid",
                "mass_kg": 0,
                "volume_m3": 0,
                "failure_reason": "SOLIDWORKS_MCP_FORCE_MODEL_GEOMETRY_FAILURE is enabled",
            }
            self._warnings.append("mass_properties:mass_property_invalid")
            self.record_event("diagnostics.mass_properties", "failed", result)
            return result
        attempts: list[dict[str, Any]] = []
        try:
            self._activate_part_document()
            model = self._active_model_doc()
            result = self._mass_properties_from_extension(model, attempts)
            if result is None:
                result = self._mass_properties_from_model_doc(model, attempts)
            if result is None:
                result = self._mass_properties_from_bodies(model, attempts)
            if result is None:
                result = {
                    "status": "mass_property_failed",
                    "attempts": attempts,
                    "failure_reason": "SolidWorks returned no readable mass properties.",
                }
            checks = {
                "positive_mass": float(result.get("mass_kg") or 0) > 0,
                "positive_volume": float(result.get("volume_m3") or 0) > 0,
            }
            result["checks"] = checks
            if all(checks.values()):
                result["status"] = "mass_properties_verified"
                result["failure_reason"] = None
                event_status = "completed"
            else:
                result["status"] = "mass_property_invalid"
                result["failure_reason"] = "Mass or volume was not positive."
                event_status = "failed"
                self._warnings.append("mass_properties:mass_property_invalid")
            self.record_event("diagnostics.mass_properties", event_status, result)
            return result
        except Exception as exc:
            result = {
                "status": "mass_property_failed",
                "attempts": attempts,
                "failure_reason": str(exc),
            }
            self._warnings.append("mass_properties:mass_property_failed")
            self.record_event("diagnostics.mass_properties", "failed", result)
            return result

    def _mass_properties_from_extension(self, model: Any, attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Read mass properties through ModelDocExtension.CreateMassProperty."""

        extension = _model_doc_extension_dispatch(model)
        if extension is None:
            attempts.append({"method": "ModelDocExtension.CreateMassProperty", "ok": False, "error": "Extension unavailable"})
            return None
        method = getattr(extension, "CreateMassProperty", None)
        if not callable(method):
            attempts.append({"method": "ModelDocExtension.CreateMassProperty", "ok": False, "error": "Method unavailable"})
            return None
        started_at = perf_counter()
        try:
            mass_property = method()
            self.record_com_call(
                "ModelDocExtension.CreateMassProperty",
                {"purpose": "mass_property_readback"},
                result=mass_property,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "ModelDocExtension.CreateMassProperty",
                {"purpose": "mass_property_readback"},
                error=exc,
                started_at=started_at,
            )
            attempts.append({"method": "ModelDocExtension.CreateMassProperty", "ok": False, "error": str(exc)})
            return None
        if mass_property is None:
            attempts.append({"method": "ModelDocExtension.CreateMassProperty", "ok": False, "error": "returned None"})
            return None
        values = {
            "mass_kg": _call_or_get(mass_property, "Mass"),
            "volume_m3": _call_or_get(mass_property, "Volume"),
            "surface_area_m2": _call_or_get(mass_property, "SurfaceArea"),
            "density_kg_per_m3": _call_or_get(mass_property, "Density"),
        }
        attempts.append({"method": "ModelDocExtension.CreateMassProperty", "ok": True, **values})
        return {
            "method": "ModelDocExtension.CreateMassProperty",
            "attempts": attempts,
            **{key: float(value) for key, value in values.items() if _is_number(value)},
        }

    def _mass_properties_from_model_doc(self, model: Any, attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Read mass properties from legacy ModelDoc2.GetMassProperties output."""

        method = getattr(model, "GetMassProperties", None)
        if not callable(method):
            attempts.append({"method": "ModelDoc2.GetMassProperties", "ok": False, "error": "Method unavailable"})
            return None
        started_at = perf_counter()
        try:
            raw = method()
            values = _numeric_sequence(raw)
            self.record_com_call(
                "ModelDoc2.GetMassProperties",
                {"purpose": "mass_property_readback"},
                result=values,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "ModelDoc2.GetMassProperties",
                {"purpose": "mass_property_readback"},
                error=exc,
                started_at=started_at,
            )
            attempts.append({"method": "ModelDoc2.GetMassProperties", "ok": False, "error": str(exc)})
            return None
        attempts.append({"method": "ModelDoc2.GetMassProperties", "ok": bool(values), "value_count": len(values)})
        if len(values) < 4:
            return None
        return {
            "method": "ModelDoc2.GetMassProperties",
            "attempts": attempts,
            "mass_kg": float(values[0]),
            "volume_m3": float(values[3]),
        }

    def _mass_properties_from_bodies(self, model: Any, attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Read mass properties by summing Body2.GetMassProperties(density) results."""

        try:
            bodies = model.GetBodies2(SW_SOLID_BODY, False)
            if bodies is None:
                bodies = []
            if not isinstance(bodies, (list, tuple)):
                bodies = [bodies]
        except Exception as exc:
            attempts.append({"method": "Body2.GetMassProperties", "ok": False, "error": f"GetBodies2 failed: {exc}"})
            return None

        body_results: list[dict[str, Any]] = []
        mass_values: list[float] = []
        volume_values: list[float] = []
        area_values: list[float] = []
        density = 7850.0
        for index, body in enumerate(bodies):
            if body is None:
                continue
            method = getattr(body, "GetMassProperties", None)
            if not callable(method):
                body_results.append({"index": index, "ok": False, "error": "GetMassProperties unavailable"})
                continue
            try:
                raw = method(density)
                values = _numeric_sequence(raw)
                parsed = _parse_body_mass_properties(values, density)
                body_results.append({"index": index, "ok": parsed is not None, "values": values, "parsed": parsed})
                if parsed:
                    mass_values.append(parsed["mass_kg"])
                    volume_values.append(parsed["volume_m3"])
                    if parsed.get("surface_area_m2") is not None:
                        area_values.append(parsed["surface_area_m2"])
            except Exception as exc:
                body_results.append({"index": index, "ok": False, "error": str(exc)})

        attempts.append(
            {
                "method": "Body2.GetMassProperties",
                "ok": bool(mass_values and volume_values),
                "body_count": len(bodies),
                "body_results": body_results,
            }
        )
        if not mass_values or not volume_values:
            return None
        return {
            "method": "Body2.GetMassProperties",
            "attempts": attempts,
            "mass_kg": sum(mass_values),
            "volume_m3": sum(volume_values),
            "surface_area_m2": sum(area_values) if area_values else None,
            "density_kg_per_m3": density,
        }

    def _op_extrude(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a boss extrude from the currently selected or latest sketch."""

        selection = self._select_atomic_reference(operation.parameters["sketch_id"], "SKETCH", "sketch")
        if not selection["selected"]:
            raise RuntimeError(f"Could not select atomic sketch_id for extrude: {operation.parameters['sketch_id']}")
        depth_m = _to_meters(operation.parameters["depth"], plan.units)
        feature = self._require_model().FeatureManager.FeatureExtrusion2(
            True, False, False, 0, 0, depth_m, 0, False, False, False, False,
            0, 0, False, False, False, False, True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("FeatureExtrusion2 returned no feature.")
        registration = self._register_atomic_feature(operation, feature)
        return {"depth_m": depth_m, "selection": selection, "created_ids": registration.get("created_ids", []), "atomic_reference": registration}

    def _op_cut(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a cut extrude from the currently selected or latest sketch."""

        sketch_id = operation.parameters["sketch_id"]
        selection = self._select_atomic_reference(sketch_id, "SKETCH", "sketch")
        if not selection["selected"]:
            raise RuntimeError(f"Could not select atomic sketch_id for cut: {sketch_id}")
        depth_m = _to_meters(operation.parameters["depth"], plan.units)
        feature_manager = self._require_model().FeatureManager
        attempts: list[dict[str, Any]] = []
        cut_attempts = (
            (
                "FeatureCut4",
                "blind_default_direction",
                (True, False, False, 0, 0, depth_m, 0, False, False, False, False,
                 0, 0, False, False, False, False, False, True, True, True, True,
                 False, 0, 0, False, False),
            ),
            (
                "FeatureCut4",
                "blind_reversed_direction",
                (True, False, True, 0, 0, depth_m, 0, False, False, False, False,
                 0, 0, False, False, False, False, False, True, True, True, True,
                 False, 0, 0, False, False),
            ),
            (
                "FeatureCut4",
                "through_all_default_direction",
                (True, False, False, 1, 0, depth_m, 0, False, False, False, False,
                 0, 0, False, False, False, False, False, True, True, True, True,
                 False, 0, 0, False, False),
            ),
            (
                "FeatureCut4",
                "through_all_reversed_direction",
                (True, False, True, 1, 0, depth_m, 0, False, False, False, False,
                 0, 0, False, False, False, False, False, True, True, True, True,
                 False, 0, 0, False, False),
            ),
        )
        for attempt_index, (method_name, mode, args) in enumerate(cut_attempts):
            if attempt_index > 0:
                selection = self._select_atomic_reference(sketch_id, "SKETCH", "sketch")
                if not selection["selected"]:
                    attempts.append({"method": method_name, "mode": mode, "selected": False})
                    continue
            method = getattr(feature_manager, method_name, None)
            if not callable(method):
                attempts.append({"method": method_name, "mode": mode, "available": False})
                continue
            started_at = perf_counter()
            try:
                feature = method(*args)
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {"sketch_id": sketch_id, "depth_m": depth_m, "mode": mode},
                    result=feature,
                    started_at=started_at,
                )
                attempts.append({"method": method_name, "mode": mode, "available": True, "created": feature is not None})
                if feature is not None:
                    registration = self._register_atomic_feature(operation, feature)
                    return {
                        "depth_m": depth_m,
                        "selection": selection,
                        "method": method_name,
                        "mode": mode,
                        "attempts": attempts,
                        "created_ids": registration.get("created_ids", []),
                        "atomic_reference": registration,
                    }
            except Exception as exc:
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {"sketch_id": sketch_id, "depth_m": depth_m, "mode": mode},
                    error=exc,
                    started_at=started_at,
                )
                attempts.append({"method": method_name, "mode": mode, "available": True, "error": str(exc)})
        raise RuntimeError(f"SolidWorks cut failed: {attempts}")

    def _op_hole(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create one or more threaded holes, degrading to sketch cuts if needed."""

        params = operation.parameters
        positions = params.get("positions") or [params["position"]]
        thread_spec = str(params.get("thread_spec", "M6")).upper()
        depth = float(params.get("depth", 0))
        result = self._create_threaded_holes_or_fallback(positions, thread_spec, depth, plan)
        feature_id = str(operation.id or f"hole_{len(self._atomic_references) + 1}")
        registration = self._register_atomic_reference(
            feature_id,
            "feature",
            object_type="BODYFEATURE",
            com_objects=list(self._last_hole_features),
        )
        self.record_event(
            "adapter.atomic_reference",
            "completed" if registration.get("registered") else "warning",
            registration,
        )
        result = dict(result)
        result["created_ids"] = registration.get("created_ids", [])
        result["atomic_reference"] = registration
        reference = self._atomic_references.get(feature_id)
        if isinstance(reference, dict):
            reference["operation_parameters"] = dict(params)
            reference["operation_result"] = {
                key: value
                for key, value in result.items()
                if key not in {"atomic_reference"}
            }
        self._last_hole_result = result
        return result

    def _op_fillet(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Apply a constant-radius fillet to the current selection."""

        radius_m = _to_meters(operation.parameters["radius"], plan.units)
        feature = self._require_model().FeatureManager.FeatureFillet3(195, radius_m, 0, 0, None, None, None)
        if feature is None:
            raise RuntimeError("FeatureFillet3 returned no feature. Select target edges before fillet.")
        return {"radius_m": radius_m}

    def _op_chamfer(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Apply a simple equal-distance chamfer to the current selection."""

        distance_m = _to_meters(operation.parameters["distance"], plan.units)
        feature = self._require_model().FeatureManager.InsertFeatureChamfer(4, 1, distance_m, 0, 0, 0, 0, 0)
        if feature is None:
            raise RuntimeError("InsertFeatureChamfer returned no feature. Select target edges before chamfer.")
        return {"distance_m": distance_m}

    def _fallback_linear_pattern_from_hole_seed(
        self,
        operation: ModelOperation,
        plan: ModelPlan,
        seed_selection: dict[str, Any],
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Create equivalent patterned hole instances when SolidWorks pattern COM calls fail."""

        params = operation.parameters
        seed_id = str(params["seed_id"])
        seed_reference = self._atomic_references.get(seed_id)
        seed_parameters = seed_reference.get("operation_parameters") if isinstance(seed_reference, dict) else None
        if not isinstance(seed_parameters, dict):
            attempts.append(
                {
                    "method": "explicit_hole_instances_fallback",
                    "available": False,
                    "reason": "seed feature does not have recorded hole parameters",
                }
            )
            return None
        seed_position = seed_parameters.get("position")
        if seed_position is None and isinstance(seed_parameters.get("positions"), list) and seed_parameters["positions"]:
            seed_position = seed_parameters["positions"][0]
        direction = _atomic_pattern_direction_vector(params.get("direction_id") or params.get("direction"))
        if seed_position is None or direction is None:
            attempts.append(
                {
                    "method": "explicit_hole_instances_fallback",
                    "available": False,
                    "reason": "seed position or supported axis direction is missing",
                }
            )
            return None
        spacing = float(params["spacing"])
        count = int(params["count"])
        positions = [
            [
                float(seed_position[0]) + direction[0] * spacing * index,
                float(seed_position[1]) + direction[1] * spacing * index,
            ]
            for index in range(1, count)
        ]
        if not positions:
            attempts.append(
                {
                    "method": "explicit_hole_instances_fallback",
                    "available": False,
                    "reason": "pattern count does not create additional instances",
                }
            )
            return None

        started_at = perf_counter()
        result = self._create_threaded_holes_or_fallback(
            positions,
            str(seed_parameters.get("thread_spec", "M6")).upper(),
            float(seed_parameters.get("depth", params.get("depth", 0))),
            plan,
        )
        fallback_attempt = {
            "method": "explicit_hole_instances_fallback",
            "available": True,
            "created": bool(result.get("ok")),
            "positions": positions,
            "source": "recorded_seed_hole_parameters",
        }
        attempts.append(fallback_attempt)
        self._fallbacks.append(
            {
                "from": "FeatureLinearPattern",
                "to": "explicit_hole_instances_fallback",
                "reason": "SolidWorks linear pattern COM calls failed for the selected seed/direction.",
                "seed_id": seed_id,
                "created_instance_count": len(positions),
            }
        )
        registration = self._register_atomic_reference(
            str(operation.id or f"linear_pattern_{len(self._atomic_references) + 1}"),
            "feature",
            object_type="BODYFEATURE",
            com_objects=list(self._last_hole_features),
        )
        self.record_event(
            "adapter.atomic_reference",
            "completed" if registration.get("registered") else "warning",
            registration,
        )
        self.record_com_call(
            "FeatureManager.FeatureLinearPatternFallback",
            {"seed_id": seed_id, "positions": positions, "count": count, "spacing": spacing},
            result=result,
            started_at=started_at,
        )
        return {
            "count": count,
            "spacing_m": _to_meters(spacing, plan.units),
            "seed_id": seed_id,
            "direction": params.get("direction"),
            "selection": seed_selection,
            "method": "explicit_hole_instances_fallback",
            "fallback_result": result,
            "attempts": attempts,
            "created_ids": registration.get("created_ids", []),
            "atomic_reference": registration,
        }

    def _op_linear_pattern(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a linear pattern from the current selected seed and direction reference."""

        model = self._require_model()
        params = operation.parameters
        count = int(params["count"])
        spacing_m = _to_meters(params["spacing"], plan.units)
        seed_selection = self._select_atomic_reference(params["seed_id"], "BODYFEATURE", "feature")
        if not seed_selection["selected"]:
            raise RuntimeError(f"Could not select atomic seed_id for linear_pattern: {params['seed_id']}")
        self.record_event(
            "adapter.atomic_reference",
            "warning",
            {
                "operation": "linear_pattern",
                "seed_id": params.get("seed_id"),
                "direction": params.get("direction"),
                "message": "Named feature graph references are validated by the MCP session layer; "
                "SolidWorks execution uses the current selected seed/direction until COM reference replay is expanded.",
            },
        )
        feature_manager = model.FeatureManager
        attempts: list[dict[str, Any]] = []
        for method_name, args in (
            ("FeatureLinearPattern5", (count, spacing_m, 1, 0, False, False, "", "", False, False, False, False, False, False, False)),
            ("FeatureLinearPattern4", (count, spacing_m, 1, 0, False, False, "", "", False, False, False)),
            ("FeatureLinearPattern3", (count, spacing_m, 1, 0, False, False, "", "", False, False)),
        ):
            method = getattr(feature_manager, method_name, None)
            if not callable(method):
                attempts.append({"method": method_name, "available": False})
                continue
            started_at = perf_counter()
            try:
                feature = method(*args)
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {"count": count, "spacing_m": spacing_m, "seed_id": params.get("seed_id")},
                    result=feature,
                    started_at=started_at,
                )
                attempts.append({"method": method_name, "available": True, "created": feature is not None})
                if feature is not None:
                    registration = self._register_atomic_feature(operation, feature)
                    return {
                        "count": count,
                        "spacing_m": spacing_m,
                        "seed_id": params.get("seed_id"),
                        "direction": params.get("direction"),
                        "selection": seed_selection,
                        "method": method_name,
                        "attempts": attempts,
                        "created_ids": registration.get("created_ids", []),
                        "atomic_reference": registration,
                    }
            except Exception as exc:
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {"count": count, "spacing_m": spacing_m, "seed_id": params.get("seed_id")},
                    error=exc,
                    started_at=started_at,
                )
                attempts.append({"method": method_name, "available": True, "error": str(exc)})
        fallback = self._fallback_linear_pattern_from_hole_seed(operation, plan, seed_selection, attempts)
        if fallback is not None:
            return fallback
        raise RuntimeError(f"SolidWorks linear pattern failed: {attempts}")

    def _op_circular_pattern(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Record a circular pattern request until axis selection is formalized."""

        model = self._require_model()
        params = operation.parameters
        count = int(params["count"])
        total_angle = float(params.get("angle", 360))
        spacing_angle_rad = _to_radians(total_angle / count)
        seed_selection = self._select_atomic_reference(params["seed_id"], "BODYFEATURE", "feature")
        if not seed_selection["selected"]:
            raise RuntimeError(f"Could not select atomic seed_id for circular_pattern: {params['seed_id']}")
        self.record_event(
            "adapter.atomic_reference",
            "warning",
            {
                "operation": "circular_pattern",
                "seed_id": params.get("seed_id"),
                "axis": params.get("axis"),
                "message": "Named feature graph references are validated by the MCP session layer; "
                "SolidWorks execution uses the current selected seed/axis until COM reference replay is expanded.",
            },
        )
        feature_manager = model.FeatureManager
        attempts: list[dict[str, Any]] = []
        for method_name, args in (
            ("FeatureCircularPattern5", (count, spacing_angle_rad, False, "", False, True, False)),
            ("FeatureCircularPattern4", (count, spacing_angle_rad, False, "", False, True)),
            ("FeatureCircularPattern3", (count, spacing_angle_rad, False, "", False)),
        ):
            method = getattr(feature_manager, method_name, None)
            if not callable(method):
                attempts.append({"method": method_name, "available": False})
                continue
            started_at = perf_counter()
            try:
                feature = method(*args)
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {"count": count, "spacing_angle_rad": spacing_angle_rad, "seed_id": params.get("seed_id")},
                    result=feature,
                    started_at=started_at,
                )
                attempts.append({"method": method_name, "available": True, "created": feature is not None})
                if feature is not None:
                    registration = self._register_atomic_feature(operation, feature)
                    return {
                        "count": count,
                        "angle": total_angle,
                        "spacing_angle_rad": spacing_angle_rad,
                        "seed_id": params.get("seed_id"),
                        "axis": params.get("axis"),
                        "selection": seed_selection,
                        "method": method_name,
                        "attempts": attempts,
                        "created_ids": registration.get("created_ids", []),
                        "atomic_reference": registration,
                    }
            except Exception as exc:
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {"count": count, "spacing_angle_rad": spacing_angle_rad, "seed_id": params.get("seed_id")},
                    error=exc,
                    started_at=started_at,
                )
                attempts.append({"method": method_name, "available": True, "error": str(exc)})
        raise RuntimeError(f"SolidWorks circular pattern failed: {attempts}")

    def _op_revolve(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a revolved boss from the current selected sketch and axis."""

        model = self._require_model()
        params = operation.parameters
        angle_rad = _to_radians(float(params["angle"]))
        axis_selection: dict[str, Any] = {"selected": False, "status": "not_attempted"}

        def select_revolve_inputs() -> dict[str, Any]:
            sketch = self._select_atomic_reference(params["sketch_id"], "SKETCH", "sketch")
            if not sketch["selected"]:
                raise RuntimeError(f"Could not select atomic sketch_id for revolve: {params['sketch_id']}")
            axis = self._select_revolve_axis_reference(params["axis"])
            if not axis.get("selected"):
                self.record_event(
                    "adapter.atomic_selection",
                    "warning",
                    {
                        "operation": "revolve",
                        "axis": params.get("axis"),
                        "selection": axis,
                        "message": "Revolve axis was not selected by stable id; SolidWorks may still use a construction centerline in the selected sketch.",
                    },
                )
            return {"sketch": sketch, "axis": axis}

        selection = select_revolve_inputs()
        sketch_selection = selection["sketch"]
        axis_selection = selection["axis"]
        attempts: list[dict[str, Any]] = []
        for attempt_index, (method_name, args) in enumerate(
            (
            ("FeatureRevolve2", (True, True, False, False, False, False, 0, 0, angle_rad, 0, False, False, 0, 0, 0, 0, 0, True, True, True)),
            ("FeatureRevolve", (angle_rad, False)),
            )
        ):
            if attempt_index > 0:
                selection = select_revolve_inputs()
                sketch_selection = selection["sketch"]
                axis_selection = selection["axis"]
            method = getattr(model.FeatureManager, method_name, None)
            if not callable(method):
                attempts.append({"method": method_name, "available": False})
                continue
            started_at = perf_counter()
            try:
                feature = method(*args)
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {
                        "sketch_id": params.get("sketch_id"),
                        "axis": params.get("axis"),
                        "angle_rad": angle_rad,
                        "axis_selected": bool(axis_selection.get("selected")),
                    },
                    result=feature,
                    started_at=started_at,
                )
                attempts.append(
                    {
                        "method": method_name,
                        "available": True,
                        "created": feature is not None,
                        "axis_selected": bool(axis_selection.get("selected")),
                    }
                )
                if feature is not None:
                    registration = self._register_atomic_feature(operation, feature)
                    return {
                        "sketch_id": params["sketch_id"],
                        "axis": params["axis"],
                        "angle_rad": angle_rad,
                        "selection": sketch_selection,
                        "axis_selection": axis_selection,
                        "method": method_name,
                        "attempts": attempts,
                        "created_ids": registration.get("created_ids", []),
                        "atomic_reference": registration,
                    }
            except Exception as exc:
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {
                        "sketch_id": params.get("sketch_id"),
                        "axis": params.get("axis"),
                        "angle_rad": angle_rad,
                        "axis_selected": bool(axis_selection.get("selected")),
                    },
                    error=exc,
                    started_at=started_at,
                )
                attempts.append(
                    {
                        "method": method_name,
                        "available": True,
                        "axis_selected": bool(axis_selection.get("selected")),
                        "error": str(exc),
                    }
                )
        raise RuntimeError(f"SolidWorks revolve failed: {attempts}")

    def _op_sweep(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a sweep feature from selected profile and path references."""

        model = self._require_model()
        params = operation.parameters
        profile_id = params.get("profile_id") or params["profile_sketch_id"]
        path_id = params.get("path_sketch_id") or params.get("path_id")

        def select_sweep_inputs() -> dict[str, Any]:
            profile = self._select_atomic_reference_using_registered_type(
                profile_id,
                "SKETCH",
                "sketch",
                mark=1,
            )
            if not profile["selected"]:
                raise RuntimeError(f"Could not select atomic profile reference for sweep: {profile_id}")
            path = self._select_atomic_reference_using_registered_type(
                path_id,
                "SKETCH",
                "sketch",
                append=True,
                mark=4,
                clear_selection=False,
            )
            if not path["selected"]:
                raise RuntimeError(f"Could not select atomic path reference for sweep: {path_id}")
            return {"profile": profile, "path": path}

        selection = select_sweep_inputs()
        profile_selection = selection["profile"]
        path_selection = selection["path"]
        attempts: list[dict[str, Any]] = []
        for attempt_index, (method_name, args) in enumerate(
            (
                (
                    "InsertProtrusionSwept4",
                    (
                        False,
                        True,
                        0,
                        False,
                        False,
                        0,
                        0,
                        False,
                        0,
                        0,
                        0,
                        0,
                        True,
                        True,
                        True,
                        0,
                        True,
                        False,
                        0,
                        0,
                    ),
                ),
                (
                    "InsertProtrusionSwept3",
                    (
                        False,
                        True,
                        0,
                        False,
                        False,
                        0,
                        0,
                        False,
                        0,
                        0,
                        0,
                        0,
                        True,
                        True,
                        True,
                        0,
                        True,
                    ),
                ),
            )
        ):
            if attempt_index > 0:
                selection = select_sweep_inputs()
                profile_selection = selection["profile"]
                path_selection = selection["path"]
            method = getattr(model.FeatureManager, method_name, None)
            if not callable(method):
                attempts.append({"method": method_name, "available": False})
                continue
            started_at = perf_counter()
            try:
                feature = method(*args)
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {
                        "profile_sketch_id": params.get("profile_sketch_id"),
                        "profile_id": profile_id,
                        "path_sketch_id": path_id,
                        "profile_selected": bool(profile_selection.get("selected")),
                        "path_selected": bool(path_selection.get("selected")),
                    },
                    result=feature,
                    started_at=started_at,
                )
                attempts.append(
                    {
                        "method": method_name,
                        "available": True,
                        "created": feature is not None,
                        "profile_selected": bool(profile_selection.get("selected")),
                        "path_selected": bool(path_selection.get("selected")),
                    }
                )
                if feature is not None:
                    registration = self._register_atomic_feature(operation, feature)
                    return {
                        "profile_sketch_id": params["profile_sketch_id"],
                        "profile_id": profile_id,
                        "path_sketch_id": path_id,
                        "selection": profile_selection,
                        "path_selection": path_selection,
                        "method": method_name,
                        "attempts": attempts,
                        "created_ids": registration.get("created_ids", []),
                        "atomic_reference": registration,
                    }
            except Exception as exc:
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {
                        "profile_sketch_id": params.get("profile_sketch_id"),
                        "profile_id": profile_id,
                        "path_sketch_id": path_id,
                        "profile_selected": bool(profile_selection.get("selected")),
                        "path_selected": bool(path_selection.get("selected")),
                    },
                    error=exc,
                    started_at=started_at,
                )
                attempts.append(
                    {
                        "method": method_name,
                        "available": True,
                        "profile_selected": bool(profile_selection.get("selected")),
                        "path_selected": bool(path_selection.get("selected")),
                        "error": str(exc),
                    }
                )
        circular_profile_diameter = params.get("profile_diameter") or params.get("circular_profile_diameter")
        if circular_profile_diameter is not None:
            diameter_m = _to_meters(float(circular_profile_diameter), plan.units)
            path_selection = self._select_atomic_reference_using_registered_type(
                path_id,
                "SKETCH",
                "sketch",
                mark=4,
            )
            method_name = "InsertProtrusionSwept4"
            method = getattr(model.FeatureManager, method_name, None)
            if not callable(method):
                attempts.append(
                    {
                        "method": f"{method_name}:circular_profile",
                        "available": False,
                        "path_selected": bool(path_selection.get("selected")),
                    }
                )
            elif path_selection.get("selected"):
                args = (
                    False,
                    True,
                    0,
                    False,
                    False,
                    0,
                    0,
                    False,
                    0,
                    0,
                    0,
                    0,
                    True,
                    True,
                    True,
                    0,
                    True,
                    True,
                    diameter_m,
                    0,
                )
                started_at = perf_counter()
                try:
                    feature = method(*args)
                    self.record_com_call(
                        f"FeatureManager.{method_name}",
                        {
                            "profile_sketch_id": params.get("profile_sketch_id"),
                            "profile_id": profile_id,
                            "path_sketch_id": path_id,
                            "path_selected": bool(path_selection.get("selected")),
                            "circular_profile": True,
                            "profile_diameter_m": diameter_m,
                        },
                        result=feature,
                        started_at=started_at,
                    )
                    attempts.append(
                        {
                            "method": f"{method_name}:circular_profile",
                            "available": True,
                            "created": feature is not None,
                            "path_selected": bool(path_selection.get("selected")),
                            "profile_diameter_m": diameter_m,
                        }
                    )
                    if feature is not None:
                        registration = self._register_atomic_feature(operation, feature)
                        return {
                            "profile_sketch_id": params["profile_sketch_id"],
                            "profile_id": profile_id,
                            "path_sketch_id": path_id,
                            "path_selection": path_selection,
                            "method": f"{method_name}:circular_profile",
                            "attempts": attempts,
                            "created_ids": registration.get("created_ids", []),
                            "atomic_reference": registration,
                        }
                except Exception as exc:
                    self.record_com_call(
                        f"FeatureManager.{method_name}",
                        {
                            "profile_sketch_id": params.get("profile_sketch_id"),
                            "profile_id": profile_id,
                            "path_sketch_id": path_id,
                            "path_selected": bool(path_selection.get("selected")),
                            "circular_profile": True,
                            "profile_diameter_m": diameter_m,
                        },
                        error=exc,
                        started_at=started_at,
                    )
                    attempts.append(
                        {
                            "method": f"{method_name}:circular_profile",
                            "available": True,
                            "path_selected": bool(path_selection.get("selected")),
                            "profile_diameter_m": diameter_m,
                            "error": str(exc),
                        }
                    )
            else:
                attempts.append(
                    {
                        "method": f"{method_name}:circular_profile",
                        "available": True,
                        "path_selected": False,
                        "failure_reason": "Could not select path reference for circular profile sweep fallback.",
                    }
                )
        raise RuntimeError(f"SolidWorks sweep failed: {attempts}")

    def _op_loft(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a loft feature from selected profile references."""

        model = self._require_model()
        params = operation.parameters

        def select_loft_inputs() -> list[dict[str, Any]]:
            selections: list[dict[str, Any]] = []
            for profile_index, profile_id in enumerate(params["profile_sketch_ids"]):
                selection = self._select_atomic_reference(
                    profile_id,
                    "SKETCH",
                    "sketch",
                    append=profile_index > 0,
                    mark=1,
                    clear_selection=profile_index == 0,
                )
                selections.append(selection)
                if not selection["selected"]:
                    raise RuntimeError(f"Could not select atomic profile_sketch_ids[{profile_index}] for loft: {profile_id}")
            return selections

        profile_selections = select_loft_inputs()
        attempts: list[dict[str, Any]] = []
        for attempt_index, (method_name, args) in enumerate(
            (
                (
                    "InsertProtrusionBlend2",
                    (
                        False,
                        True,
                        False,
                        1,
                        0,
                        0,
                        1,
                        1,
                        True,
                        True,
                        False,
                        0,
                        0,
                        0,
                        True,
                        True,
                        True,
                        0,
                    ),
                ),
                (
                    "InsertProtrusionBlend",
                    (
                        False,
                        True,
                        False,
                        1,
                        0,
                        0,
                        1,
                        1,
                        True,
                        True,
                        False,
                        0,
                        0,
                        0,
                        True,
                        True,
                        True,
                    ),
                ),
            )
        ):
            if attempt_index > 0:
                profile_selections = select_loft_inputs()
            method = getattr(model.FeatureManager, method_name, None)
            if not callable(method):
                attempts.append({"method": method_name, "available": False})
                continue
            started_at = perf_counter()
            try:
                feature = method(*args)
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {
                        "profile_sketch_ids": params.get("profile_sketch_ids"),
                        "profile_selected_count": sum(1 for item in profile_selections if item.get("selected")),
                    },
                    result=feature,
                    started_at=started_at,
                )
                attempts.append(
                    {
                        "method": method_name,
                        "available": True,
                        "created": feature is not None,
                        "profile_selected_count": sum(1 for item in profile_selections if item.get("selected")),
                    }
                )
                if feature is not None:
                    registration = self._register_atomic_feature(operation, feature)
                    return {
                        "profile_sketch_ids": list(params["profile_sketch_ids"]),
                        "profile_selections": profile_selections,
                        "method": method_name,
                        "attempts": attempts,
                        "created_ids": registration.get("created_ids", []),
                        "atomic_reference": registration,
                    }
            except Exception as exc:
                self.record_com_call(
                    f"FeatureManager.{method_name}",
                    {
                        "profile_sketch_ids": params.get("profile_sketch_ids"),
                        "profile_selected_count": sum(1 for item in profile_selections if item.get("selected")),
                    },
                    error=exc,
                    started_at=started_at,
                )
                attempts.append(
                    {
                        "method": method_name,
                        "available": True,
                        "profile_selected_count": sum(1 for item in profile_selections if item.get("selected")),
                        "error": str(exc),
                    }
                )
        raise RuntimeError(f"SolidWorks loft failed: {attempts}")

    def _op_assign_material(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Set and verify material metadata when a material database name is provided."""

        material = str(operation.parameters["material"])
        configured_database = operation.parameters.get("database")
        model = self._require_model()
        config_candidates = _configuration_name_candidates(model)
        config_name = config_candidates[0] if config_candidates else ""
        if self._config.force_material_failure:
            result = {
                "status": "forced_failure",
                "requested_material": material,
                "configuration": config_name,
                "database": configured_database,
                "current_material": None,
                "set_result": False,
                "verified": False,
                "attempts": [],
                "failure_reason": "SOLIDWORKS_MCP_FORCE_MATERIAL_FAILURE is enabled",
            }
            self._material_status = "forced_failure"
            self._material_result = result
            self._warnings.append("material_assignment:forced_failure")
            self.record_event("properties.material", "failed", result)
            return result
        material_candidates = _material_name_candidates(material)
        result: dict[str, Any] = {
            "status": "material_assignment_failed",
            "requested_material": material,
            "configuration": config_name,
            "database": None,
            "material_candidates": material_candidates,
            "attempts": [],
        }

        for configuration in config_candidates:
            if result["status"] == "material_verified":
                break
            for database in _material_database_candidates(configured_database):
                if result["status"] == "material_verified":
                    break
                for effective_material in material_candidates:
                    attempt: dict[str, Any] = {
                        "configuration": configuration,
                        "database": database,
                        "effective_material": effective_material,
                    }
                    set_result = self._set_material_property(model, configuration, database, effective_material)
                    attempt.update(set_result)
                    if set_result.get("error"):
                        result["attempts"].append(attempt)
                        continue

                    attempt["rebuild"] = self._rebuild_after_material_assignment(model)
                    material_info = self._read_material_info(model, configuration, database)
                    current_material = material_info.get("material")
                    attempt["current_material"] = current_material
                    attempt["readback_database"] = material_info.get("database")
                    attempt["verified"] = _material_names_match(current_material, effective_material)
                    result["attempts"].append(attempt)
                    if attempt["verified"]:
                        result.update(
                            {
                                "status": "material_verified",
                                "configuration": configuration,
                                "database": database,
                                "effective_material": effective_material,
                                "set_result": set_result.get("set_result"),
                                "current_material": current_material,
                                "readback_database": material_info.get("database"),
                                "verified": True,
                            }
                        )
                        break

        if result["status"] != "material_verified":
            successful_attempts = [
                attempt for attempt in result["attempts"] if attempt.get("set_result") not in {False, None}
            ]
            last_attempt = result["attempts"][-1] if result["attempts"] else {}
            if successful_attempts:
                result["status"] = "material_set_unverified"
                result["database"] = successful_attempts[-1].get("database")
                result["set_result"] = successful_attempts[-1].get("set_result")
                result["current_material"] = successful_attempts[-1].get("current_material")
                result["readback_database"] = successful_attempts[-1].get("readback_database")
                result["failure_reason"] = (
                    "SolidWorks accepted at least one material call but readback did not match the requested material."
                )
            else:
                result["status"] = "material_assignment_failed"
                result["database"] = last_attempt.get("database")
                result["set_result"] = last_attempt.get("set_result")
                result["current_material"] = last_attempt.get("current_material")
                result["readback_database"] = last_attempt.get("readback_database")
                result["failure_reason"] = (
                    last_attempt.get("failure_reason")
                    or "SolidWorks did not report a successful material assignment."
                )
            result["verified"] = False

        self._material_status = str(result["status"])
        self._material_result = result
        if result["status"] != "material_verified":
            self._warnings.append(f"material_assignment:{result['status']}")
        self.record_event(
            "properties.material",
            "completed" if result["status"] == "material_verified" else "failed",
            result,
        )
        return result

    def _set_material_property(
        self,
        model: Any,
        configuration: str,
        database: str,
        material: str,
    ) -> dict[str, Any]:
        """Set material using the configuration API, then legacy part API when useful."""

        part_doc = _part_doc_dispatch(model)
        method = getattr(part_doc, "SetMaterialPropertyName2", None)
        if callable(method):
            started_at = perf_counter()
            try:
                value = method(configuration, database, material)
                self.record_com_call(
                    "ModelDoc2.SetMaterialPropertyName2",
                    {"configuration": configuration, "database": database, "material": material},
                    result=value,
                    started_at=started_at,
                )
                return {"set_method": "SetMaterialPropertyName2", "set_result": value}
            except Exception as exc:
                self.record_com_call(
                    "ModelDoc2.SetMaterialPropertyName2",
                    {"configuration": configuration, "database": database, "material": material},
                    error=exc,
                    started_at=started_at,
                )
                return {"set_method": "SetMaterialPropertyName2", "error": str(exc)}

        legacy_method = getattr(part_doc, "SetMaterialPropertyName", None)
        if callable(legacy_method):
            started_at = perf_counter()
            try:
                value = legacy_method(database, material)
                self.record_com_call(
                    "ModelDoc2.SetMaterialPropertyName",
                    {"database": database, "material": material},
                    result=value,
                    started_at=started_at,
                )
                return {"set_method": "SetMaterialPropertyName", "set_result": value}
            except Exception as exc:
                self.record_com_call(
                    "ModelDoc2.SetMaterialPropertyName",
                    {"database": database, "material": material},
                    error=exc,
                    started_at=started_at,
                )
                return {"set_method": "SetMaterialPropertyName", "error": str(exc)}

        return {"set_method": None, "error": "No SolidWorks material assignment method is available."}

    def _rebuild_after_material_assignment(self, model: Any) -> dict[str, Any]:
        """Refresh model state so material readback sees recent assignment."""

        for method_name, args in (("ForceRebuild3", (False,)), ("EditRebuild3", ())):
            method = getattr(model, method_name, None)
            if not callable(method):
                continue
            started_at = perf_counter()
            try:
                value = method(*args)
                self.record_com_call(
                    f"ModelDoc2.{method_name}",
                    {"purpose": "material_readback_refresh"},
                    result=value,
                    started_at=started_at,
                )
                return {"method": method_name, "result": value}
            except Exception as exc:
                self.record_com_call(
                    f"ModelDoc2.{method_name}",
                    {"purpose": "material_readback_refresh"},
                    error=exc,
                    started_at=started_at,
                )
        return {"method": None, "result": None}

    def _read_material_info(self, model: Any, configuration: str, database: str) -> dict[str, str | None]:
        """Read the active material name with COM logging."""

        part_doc = _part_doc_dispatch(model)
        method = getattr(part_doc, "GetMaterialPropertyName2", None)
        if not callable(method):
            return {"material": None, "database": None}

        import pythoncom
        import win32com.client

        database_out = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
        started_at = perf_counter()
        try:
            value = method(configuration, database_out)
            self.record_com_call(
                "ModelDoc2.GetMaterialPropertyName2",
                {"configuration": configuration, "database_probe": database},
                result={"material": value, "database": database_out.value},
                started_at=started_at,
            )
            return {
                "material": str(value) if value not in {None, False} else None,
                "database": str(database_out.value) if database_out.value not in {None, False} else None,
            }
        except Exception as exc:
            self.record_com_call(
                "ModelDoc2.GetMaterialPropertyName2",
                {"configuration": configuration, "database_probe": database},
                error=exc,
                started_at=started_at,
            )
            self._warnings.append(f"ModelDoc2.GetMaterialPropertyName2:{exc}")
            return {"material": None, "database": None}

    def _op_set_custom_properties(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Write and verify document or configuration custom properties."""

        properties = {
            str(key).strip(): str(value)
            for key, value in operation.parameters["properties"].items()
        }
        scope = str(operation.parameters.get("scope", "document"))
        configuration = str(operation.parameters.get("configuration") or _active_configuration_name(self._require_model()))
        manager_result = self._custom_property_manager_result(scope, configuration)
        manager = manager_result.get("manager")
        result: dict[str, Any] = {
            "status": "custom_property_failed",
            "scope": scope,
            "configuration": configuration if scope == "configuration" else None,
            "property_api": "custom_property_manager" if manager is not None else "custom_info_legacy",
            "manager_error": manager_result.get("error"),
            "requested_properties": properties,
            "current_properties": {},
            "attempts": [],
        }
        for name, value in properties.items():
            attempt = self._set_and_verify_custom_property(name, value, scope, manager)
            if attempt.get("readback") is not None:
                result["current_properties"][name] = attempt.get("readback")
            result["attempts"].append(attempt)
            if attempt.get("property_api") == "custom_info_legacy":
                result["property_api"] = "custom_info_legacy"

        missing_or_mismatched = [
            attempt["name"]
            for attempt in result["attempts"]
            if not attempt.get("verified")
        ]
        if missing_or_mismatched:
            result.update(
                {
                    "status": "custom_property_unverified",
                    "verified": False,
                    "missing_or_mismatched": missing_or_mismatched,
                    "failure_reason": "One or more custom properties did not read back with the requested value.",
                }
            )
            self._warnings.append("custom_properties:custom_property_unverified")
            self.record_event("properties.custom", "failed", result)
        else:
            result.update({"status": "custom_properties_verified", "verified": True})
            self.record_event("properties.custom", "completed", result)

        self._custom_property_status = str(result["status"])
        self._custom_property_result = result
        return result

    def _set_and_verify_custom_property(
        self,
        name: str,
        value: str,
        scope: str,
        manager: Any | None,
    ) -> dict[str, Any]:
        """Set one custom property and use legacy fallback when manager readback fails."""

        attempt: dict[str, Any] = {"name": name, "value": value}
        readback: dict[str, Any]
        if manager is not None:
            write_result = self._set_custom_property(manager, name, value)
            readback = self._read_custom_property(manager, name)
            attempt["manager_attempt"] = {**write_result, "readback_result": readback}
            if str(readback.get("value") or "") == value:
                attempt.update(write_result)
                attempt.update({"readback": readback.get("value"), "readback_result": readback})
                attempt["verified"] = True
                attempt["property_api"] = "custom_property_manager"
                return attempt

        if scope == "document":
            write_result = self._set_legacy_custom_info(name, value)
            readback = self._read_legacy_custom_info(name)
            attempt["legacy_attempt"] = {**write_result, "readback_result": readback}
            attempt.update(write_result)
            attempt.update({"readback": readback.get("value"), "readback_result": readback})
            attempt["verified"] = str(readback.get("value") or "") == value
            attempt["property_api"] = "custom_info_legacy"
            return attempt

        readback = {"read_method": None, "value": None}
        write_result = {
            "write_method": None,
            "write_error": "Configuration-scoped custom properties require CustomPropertyManager.",
        }
        attempt.update(write_result)
        attempt.update({"readback": None, "readback_result": readback, "verified": False})
        return attempt

    def _custom_property_manager_result(self, scope: str, configuration: str) -> dict[str, Any]:
        """Return CustomPropertyManager diagnostics without preventing legacy fallback."""

        try:
            return {"manager": self._custom_property_manager(scope, configuration)}
        except Exception as exc:
            self.record_event(
                "properties.custom.manager",
                "failed",
                {"scope": scope, "configuration": configuration, "error": str(exc)},
            )
            return {"manager": None, "error": str(exc)}

    def _custom_property_manager(self, scope: str, configuration: str) -> Any:
        """Return the SolidWorks CustomPropertyManager for the requested scope."""

        self._activate_part_document()
        model = self._active_model_doc()
        extension = _model_doc_extension_dispatch(model)
        if extension is None:
            raise RuntimeError("ModelDoc2.Extension is unavailable for custom properties.")
        manager_name = configuration if scope == "configuration" else ""
        started_at = perf_counter()
        try:
            manager = extension.CustomPropertyManager(manager_name)
            self.record_com_call(
                "ModelDocExtension.CustomPropertyManager",
                {"scope": scope, "configuration": manager_name},
                result=manager,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "ModelDocExtension.CustomPropertyManager",
                {"scope": scope, "configuration": manager_name},
                error=exc,
                started_at=started_at,
            )
            raise
        if manager is None:
            raise RuntimeError("SolidWorks returned no CustomPropertyManager.")
        return manager

    def _active_model_doc(self) -> Any:
        """Return the freshest active model document available from SolidWorks."""

        model = self._model
        sw = self._sw
        if sw is not None:
            try:
                active_doc = sw.ActiveDoc
                if active_doc is not None:
                    self._model = active_doc
                    model = active_doc
            except Exception as exc:
                self.record_com_call("SldWorks.ActiveDoc", {"purpose": "custom_properties"}, error=exc)
        if model is None:
            raise RuntimeError("No active part document. Call begin_transaction first.")
        return model

    def _set_custom_property(self, manager: Any, name: str, value: str) -> dict[str, Any]:
        """Set one custom property using the most capable available COM method."""

        method = getattr(manager, "Add3", None)
        if callable(method):
            started_at = perf_counter()
            try:
                result = method(name, 30, value, 2)
                self.record_com_call(
                    "CustomPropertyManager.Add3",
                    {"name": name, "value": value, "type": "text", "overwrite": True},
                    result=result,
                    started_at=started_at,
                )
                return {"write_method": "Add3", "write_result": result}
            except Exception as exc:
                self.record_com_call(
                    "CustomPropertyManager.Add3",
                    {"name": name, "value": value, "type": "text", "overwrite": True},
                    error=exc,
                    started_at=started_at,
                )

        method = getattr(manager, "Set2", None)
        if callable(method):
            started_at = perf_counter()
            try:
                result = method(name, value)
                self.record_com_call(
                    "CustomPropertyManager.Set2",
                    {"name": name, "value": value},
                    result=result,
                    started_at=started_at,
                )
                return {"write_method": "Set2", "write_result": result}
            except Exception as exc:
                self.record_com_call(
                    "CustomPropertyManager.Set2",
                    {"name": name, "value": value},
                    error=exc,
                    started_at=started_at,
                )
                return {"write_method": "Set2", "write_error": str(exc)}

        return {"write_method": None, "write_error": "No CustomPropertyManager write method is available."}

    def _read_custom_property(self, manager: Any, name: str) -> dict[str, Any]:
        """Read one custom property with pywin32 byref variants when needed."""

        import pythoncom
        import win32com.client

        for method_name, variant_count in (("Get6", 5), ("Get5", 4), ("Get4", 3)):
            method = getattr(manager, method_name, None)
            if not callable(method):
                continue
            values = [win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "") for _ in range(variant_count)]
            args = [name, False, *values] if method_name in {"Get6", "Get5"} else [name, *values]
            started_at = perf_counter()
            try:
                result = method(*args)
                self.record_com_call(
                    f"CustomPropertyManager.{method_name}",
                    {"name": name},
                    result={"return": result, "values": [item.value for item in values]},
                    started_at=started_at,
                )
                value = _first_nonempty_string([item.value for item in values])
                return {"read_method": method_name, "result": result, "value": value}
            except Exception as exc:
                self.record_com_call(
                    f"CustomPropertyManager.{method_name}",
                    {"name": name},
                    error=exc,
                    started_at=started_at,
                )

        method = getattr(manager, "Get2", None)
        if callable(method):
            started_at = perf_counter()
            try:
                value = method(name)
                self.record_com_call(
                    "CustomPropertyManager.Get2",
                    {"name": name},
                    result=value,
                    started_at=started_at,
                )
                return {"read_method": "Get2", "value": str(value) if value not in {None, False} else None}
            except Exception as exc:
                self.record_com_call(
                    "CustomPropertyManager.Get2",
                    {"name": name},
                    error=exc,
                    started_at=started_at,
                )
                return {"read_method": "Get2", "error": str(exc), "value": None}

        return {"read_method": None, "error": "No CustomPropertyManager read method is available.", "value": None}

    def _set_legacy_custom_info(self, name: str, value: str) -> dict[str, Any]:
        """Set one document-level custom property through legacy ModelDoc2 APIs."""

        model = self._active_model_doc()
        attempts: list[dict[str, Any]] = []
        method = getattr(model, "DeleteCustomInfo2", None)
        if callable(method):
            started_at = perf_counter()
            try:
                delete_result = method("", name)
                self.record_com_call(
                    "ModelDoc2.DeleteCustomInfo2",
                    {"configuration": "", "name": name},
                    result=delete_result,
                    started_at=started_at,
                )
                attempts.append({"method": "DeleteCustomInfo2", "result": delete_result})
            except Exception as exc:
                self.record_com_call(
                    "ModelDoc2.DeleteCustomInfo2",
                    {"configuration": "", "name": name},
                    error=exc,
                    started_at=started_at,
                )
                attempts.append({"method": "DeleteCustomInfo2", "error": str(exc)})

        for method_name, args in (
            ("AddCustomInfo3", ("", name, 30, value)),
            ("AddCustomInfo2", (name, 30, value)),
            ("AddCustomInfo", (name, value)),
        ):
            method = getattr(model, method_name, None)
            if not callable(method):
                continue
            started_at = perf_counter()
            try:
                write_result = method(*args)
                self.record_com_call(
                    f"ModelDoc2.{method_name}",
                    {"name": name, "value": value},
                    result=write_result,
                    started_at=started_at,
                )
                return {"write_method": method_name, "write_result": write_result, "legacy_attempts": attempts}
            except Exception as exc:
                self.record_com_call(
                    f"ModelDoc2.{method_name}",
                    {"name": name, "value": value},
                    error=exc,
                    started_at=started_at,
                )
                attempts.append({"method": method_name, "error": str(exc)})

        for method_name, args in (
            ("CustomInfo2", ("", name, value)),
            ("CustomInfo", (name, value)),
        ):
            try:
                setattr(model, method_name, args)
                return {"write_method": f"{method_name}.property", "write_result": True, "legacy_attempts": attempts}
            except Exception as exc:
                attempts.append({"method": f"{method_name}.property", "error": str(exc)})

        return {
            "write_method": None,
            "write_error": "No legacy ModelDoc2 custom property write method succeeded.",
            "legacy_attempts": attempts,
        }

    def _read_legacy_custom_info(self, name: str) -> dict[str, Any]:
        """Read one document-level custom property through legacy ModelDoc2 APIs."""

        model = self._active_model_doc()
        for method_name, args in (
            ("GetCustomInfoValue", ("", name)),
            ("CustomInfo2", ("", name)),
            ("CustomInfo", (name,)),
        ):
            member = getattr(model, method_name, None)
            if member is None:
                continue
            started_at = perf_counter()
            try:
                value = member(*args) if callable(member) else member
                self.record_com_call(
                    f"ModelDoc2.{method_name}",
                    {"name": name},
                    result=value,
                    started_at=started_at,
                )
                return {
                    "read_method": method_name,
                    "value": str(value) if value not in {None, False} else None,
                }
            except Exception as exc:
                self.record_com_call(
                    f"ModelDoc2.{method_name}",
                    {"name": name},
                    error=exc,
                    started_at=started_at,
                )

        return {"read_method": None, "value": None, "error": "No legacy ModelDoc2 custom property read method succeeded."}

    def _op_make_drawing(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Defer drawing creation to the dedicated drawing stage."""

        return {"deferred_to": "generate_drawing"}

    def _try_insert_metadata_note(self, plan: ModelPlan) -> dict[str, Any]:
        """Insert visible drawing metadata derived from requested custom properties."""

        properties = _custom_properties_from_plan(plan)
        if not properties:
            return {"status": "not_requested", "properties": {}}
        drawing = self._drawing
        if drawing is None:
            return {"status": "no_drawing", "properties": properties, "failure_reason": "No active drawing document."}
        text = _metadata_note_text(properties)
        result: dict[str, Any] = {
            "status": "metadata_note_failed",
            "properties": properties,
            "text": text,
            "attempts": [],
        }
        for method_name, args in (
            ("InsertNote", (text,)),
            ("CreateText", (text, 0.02, 0.02, 0.0, 0.003, 0.0)),
        ):
            method = getattr(drawing, method_name, None)
            if not callable(method):
                result["attempts"].append({"method": method_name, "available": False})
                continue
            started_at = perf_counter()
            try:
                note = method(*args)
                self.record_com_call(
                    f"DrawingDoc.{method_name}",
                    {"purpose": "metadata_note", "text": text},
                    result=note,
                    started_at=started_at,
                )
                created = note is not None
                result["attempts"].append({"method": method_name, "available": True, "created": created})
                if created:
                    result.update({"status": "metadata_note_created", "method": method_name})
                    self.record_event("drawing.metadata_note", "completed", result)
                    return result
            except Exception as exc:
                self.record_com_call(
                    f"DrawingDoc.{method_name}",
                    {"purpose": "metadata_note", "text": text},
                    error=exc,
                    started_at=started_at,
                )
                result["attempts"].append({"method": method_name, "available": True, "error": str(exc)})
        result["failure_reason"] = "SolidWorks did not create a metadata note."
        self._warnings.append("drawing_metadata_note:metadata_note_failed")
        self.record_event("drawing.metadata_note", "failed", result)
        return result

    def _create_rounded_plate_body(
        self,
        length: float,
        width: float,
        thickness: float,
        corner_radius: float,
        plan: ModelPlan,
    ) -> None:
        """Sketch a centered plate profile and extrude it to plate thickness."""

        model = self._require_model()
        if not _select_by_id(model, _plane_name_candidates("front"), "PLANE"):
            raise RuntimeError("Could not select the front plane for mounting plate sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        sketch.CreateCenterRectangle(
            0,
            0,
            0,
            _to_meters(length / 2, plan.units),
            _to_meters(width / 2, plan.units),
            0,
        )
        sketch.InsertSketch(True)

        depth_m = _to_meters(thickness, plan.units)
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("Mounting plate base extrusion failed.")
        if corner_radius > 0:
            self._apply_mounting_plate_corner_fillets(length, width, thickness, corner_radius, plan)

    def _apply_mounting_plate_corner_fillets(
        self,
        length: float,
        width: float,
        thickness: float,
        corner_radius: float,
        plan: ModelPlan,
    ) -> None:
        """Apply real constant-radius fillets to the four vertical outer corner edges."""

        if corner_radius * 2 >= min(length, width):
            raise RuntimeError("corner_radius must be less than half of the shorter plate side")
        model = self._require_model()
        selected_edges = self._select_mounting_plate_corner_edges(length, width, thickness, plan)
        if selected_edges != 4:
            raise RuntimeError(f"Expected 4 corner edges for mounting plate fillets, selected {selected_edges}.")

        radius_m = _to_meters(corner_radius, plan.units)
        started_at = perf_counter()
        try:
            feature = model.FeatureManager.FeatureFillet3(
                SW_FILLET_OPTIONS_MVP,
                radius_m,
                0,
                0,
                SW_CONST_RADIUS_FILLET,
                SW_FILLET_OVERFLOW_DEFAULT,
                0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
            self.record_com_call(
                "FeatureManager.FeatureFillet3",
                {"radius_m": radius_m, "selected_edges": selected_edges, "options": SW_FILLET_OPTIONS_MVP},
                result=feature,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "FeatureManager.FeatureFillet3",
                {"radius_m": radius_m, "selected_edges": selected_edges, "options": SW_FILLET_OPTIONS_MVP},
                error=exc,
                started_at=started_at,
            )
            raise
        finally:
            model.ClearSelection2(True)
        if feature is None:
            raise RuntimeError("FeatureFillet3 returned no feature for mounting plate corner fillets.")
        self._corner_radius_status = "fillet_feature"

    def _select_mounting_plate_corner_edges(
        self,
        length: float,
        width: float,
        thickness: float,
        plan: ModelPlan,
    ) -> int:
        """Select the four vertical outside edges of the extruded plate body."""

        model = self._require_model()
        model.ClearSelection2(True)
        selected = 0
        half_length = _to_meters(length / 2, plan.units)
        half_width = _to_meters(width / 2, plan.units)
        z_mid = _to_meters(thickness / 2, plan.units)
        outside = _to_meters(max(min(length, width) * 0.02, 1), plan.units)
        ray_radius = _to_meters(0.5, plan.units)
        for x_sign, y_sign in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x = x_sign * half_length
            y = y_sign * half_width
            append = selected > 0
            if self._select_corner_edge_by_ray(x, y, z_mid, x_sign, y_sign, outside, ray_radius, append=append, mark=0):
                selected += 1
                continue
            if self._select_corner_edge_by_id(x, y, z_mid, append=append, mark=0):
                selected += 1
        return selected

    def _select_corner_edge_by_ray(
        self,
        x: float,
        y: float,
        z: float,
        x_sign: int,
        y_sign: int,
        outside: float,
        ray_radius: float,
        append: bool,
        mark: int,
    ) -> bool:
        """Select one vertical corner edge by shooting a ray from outside the body."""

        model = self._require_model()
        parameters = {
            "x": x + x_sign * outside,
            "y": y + y_sign * outside,
            "z": z,
            "direction": [-x_sign, -y_sign, 0],
            "radius": ray_radius,
            "append": append,
            "mark": mark,
        }
        started_at = perf_counter()
        selected = model.Extension.SelectByRay(
            parameters["x"],
            parameters["y"],
            parameters["z"],
            -x_sign,
            -y_sign,
            0,
            parameters["radius"],
            SW_SEL_EDGES,
            append,
            mark,
            0,
        )
        self.record_com_call("ModelDocExtension.SelectByRay", parameters, result=selected, started_at=started_at)
        return bool(selected)

    def _select_corner_edge_by_id(
        self,
        x: float,
        y: float,
        z: float,
        append: bool,
        mark: int,
    ) -> bool:
        """Fallback edge pick using SelectByID2 at the corner edge midpoint."""

        import pythoncom
        import win32com.client

        model = self._require_model()
        callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        started_at = perf_counter()
        try:
            selected = model.Extension.SelectByID2("", "EDGE", x, y, z, append, mark, callout, 0)
            self.record_com_call(
                "ModelDocExtension.SelectByID2",
                {"name": "", "type": "EDGE", "x": x, "y": y, "z": z, "append": append, "mark": mark},
                result=selected,
                started_at=started_at,
            )
            return bool(selected)
        except Exception as exc:
            self.record_com_call(
                "ModelDocExtension.SelectByID2",
                {"name": "", "type": "EDGE", "x": x, "y": y, "z": z, "append": append, "mark": mark},
                error=exc,
                started_at=started_at,
            )
            return False

    def _draw_rounded_rectangle(self, length: float, width: float, radius: float, plan: ModelPlan) -> None:
        """Draw a closed rounded rectangle from line and arc sketch segments."""

        if radius * 2 >= min(length, width):
            raise RuntimeError("corner_radius must be less than half of the shorter plate side")

        sketch = self._require_model().SketchManager
        half_length = length / 2
        half_width = width / 2
        left = -half_length
        right = half_length
        bottom = -half_width
        top = half_width
        r = radius

        sketch.CreateLine(_to_meters(left + r, plan.units), _to_meters(top, plan.units), 0,
                          _to_meters(right - r, plan.units), _to_meters(top, plan.units), 0)
        sketch.CreateArc(_to_meters(right - r, plan.units), _to_meters(top - r, plan.units), 0,
                         _to_meters(right - r, plan.units), _to_meters(top, plan.units), 0,
                         _to_meters(right, plan.units), _to_meters(top - r, plan.units), 0, -1)
        sketch.CreateLine(_to_meters(right, plan.units), _to_meters(top - r, plan.units), 0,
                          _to_meters(right, plan.units), _to_meters(bottom + r, plan.units), 0)
        sketch.CreateArc(_to_meters(right - r, plan.units), _to_meters(bottom + r, plan.units), 0,
                         _to_meters(right, plan.units), _to_meters(bottom + r, plan.units), 0,
                         _to_meters(right - r, plan.units), _to_meters(bottom, plan.units), 0, -1)
        sketch.CreateLine(_to_meters(right - r, plan.units), _to_meters(bottom, plan.units), 0,
                          _to_meters(left + r, plan.units), _to_meters(bottom, plan.units), 0)
        sketch.CreateArc(_to_meters(left + r, plan.units), _to_meters(bottom + r, plan.units), 0,
                         _to_meters(left + r, plan.units), _to_meters(bottom, plan.units), 0,
                         _to_meters(left, plan.units), _to_meters(bottom + r, plan.units), 0, -1)
        sketch.CreateLine(_to_meters(left, plan.units), _to_meters(bottom + r, plan.units), 0,
                          _to_meters(left, plan.units), _to_meters(top - r, plan.units), 0)
        sketch.CreateArc(_to_meters(left + r, plan.units), _to_meters(top - r, plan.units), 0,
                         _to_meters(left, plan.units), _to_meters(top - r, plan.units), 0,
                         _to_meters(left + r, plan.units), _to_meters(top, plan.units), 0, -1)

    def _create_threaded_holes_or_fallback(
        self,
        hole_points: list[list[float]] | list[tuple[float, float]],
        thread_spec: str,
        depth: float,
        plan: ModelPlan,
    ) -> dict[str, Any]:
        """Create threaded holes with HoleWizard, macro fallback or sketch cuts."""

        self._thread_model_status = "requested"
        self._last_hole_points = [[float(point[0]), float(point[1])] for point in hole_points]
        self._last_hole_features = []
        holewizard_result = self._try_holewizard_threaded_holes(hole_points, thread_spec, depth, plan)
        if holewizard_result["ok"]:
            self._thread_model_status = "holewizard_threaded_hole"
            self._last_hole_result = holewizard_result
            return holewizard_result

        self._fallbacks.append({"from": "HoleWizard5", "to": "macro_or_geometry", "reason": holewizard_result["message"]})
        macro_result: dict[str, Any] | None = None
        if self._config.macro_fallback_enabled:
            macro_result = self._try_holewizard_macro_fallback(hole_points, thread_spec, depth, plan)
            if macro_result["ok"]:
                self._thread_model_status = "macro_threaded_hole"
                self._last_hole_result = macro_result
                return macro_result
            self._fallbacks.append({"from": "HoleWizard macro", "to": "geometry_cut", "reason": macro_result["message"]})
        else:
            self._fallbacks.append(
                {
                    "from": "HoleWizard macro",
                    "to": "geometry_cut",
                    "reason": "SOLIDWORKS_MCP_MACRO_FALLBACK is disabled",
                }
            )

        cut_result = self._create_geometry_cut_holes(hole_points, thread_spec, depth, plan)
        self._thread_model_status = "degraded_geometry_only"
        result = {
            "ok": True,
            "method": "geometry_cut_fallback",
            "thread_model_status": self._thread_model_status,
            "holewizard_error": holewizard_result["message"],
            "macro_error": macro_result["message"] if macro_result else "macro fallback disabled",
            "macro_path": macro_result.get("macro_path") if macro_result else None,
            "details": cut_result,
        }
        self._last_hole_result = result
        return result

    def _try_holewizard_threaded_holes(
        self,
        hole_points: list[list[float]] | list[tuple[float, float]],
        thread_spec: str,
        depth: float,
        plan: ModelPlan,
    ) -> dict[str, Any]:
        """Try a narrow HoleWizard5 call for ISO metric coarse threaded holes.

        SolidWorks versions expose a long HoleWizard5 signature.  This method is
        intentionally isolated so Windows smoke feedback can refine the exact
        positional constants without destabilizing the geometry fallback path.
        """

        try:
            if self._config.force_holewizard_failure:
                return {
                    "ok": False,
                    "method": "holewizard5",
                    "message": "SOLIDWORKS_MCP_FORCE_HOLEWIZARD_FAILURE is enabled",
                }
            model = self._require_model()
            thread_info = ISO_METRIC_COARSE_THREADS[thread_spec]
            diameter_m = _to_meters(thread_info["tap_drill_diameter"], plan.units)
            depth_m = _to_meters(depth, plan.units)
            created = 0
            features: list[Any] = []
            for point in hole_points:
                if not self._select_top_face_at_point(point, depth, plan):
                    raise RuntimeError(f"Could not select top face for HoleWizard point {point}.")
                parameters = _holewizard_tapped_hole_parameters(thread_spec, diameter_m, depth_m)
                started_at = perf_counter()
                try:
                    feature = model.FeatureManager.HoleWizard5(*parameters)
                    self.record_com_call(
                        "FeatureManager.HoleWizard5",
                        {
                            "thread_spec": thread_spec,
                            "point": point,
                            "diameter_m": diameter_m,
                            "depth_m": depth_m,
                            "parameter_count": len(parameters),
                        },
                        result=feature,
                        started_at=started_at,
                    )
                except Exception as exc:
                    self.record_com_call(
                        "FeatureManager.HoleWizard5",
                        {
                            "thread_spec": thread_spec,
                            "point": point,
                            "diameter_m": diameter_m,
                            "depth_m": depth_m,
                            "parameter_count": len(parameters),
                        },
                        error=exc,
                        started_at=started_at,
                    )
                    raise
                if feature is None:
                    return {"ok": False, "method": "holewizard5", "message": "HoleWizard5 returned no feature"}
                features.append(feature)
                created += 1
            self._last_hole_features = features
            return {
                "ok": True,
                "method": "holewizard5",
                "thread_model_status": "holewizard_threaded_hole",
                "thread_spec": thread_spec,
                "thread_size": _holewizard_thread_size(thread_spec),
                "hole_count": created,
            }
        except Exception as exc:
            return {"ok": False, "method": "holewizard5", "message": str(exc)}

    def _try_holewizard_macro_fallback(
        self,
        hole_points: list[list[float]] | list[tuple[float, float]],
        thread_spec: str,
        depth: float,
        plan: ModelPlan,
    ) -> dict[str, Any]:
        """Run a controlled VBA macro that creates MVP ISO metric tapped holes."""

        workspace = self._require_workspace() / "macros"
        workspace.mkdir(parents=True, exist_ok=True)
        macro_path = workspace / "holewizard_fallback.swb"
        try:
            result_path = workspace / "holewizard_fallback_result.json"
            macro_source = _render_holewizard_macro(hole_points, thread_spec, depth, plan, result_path)
            macro_path.write_text(macro_source, encoding="utf-8")
        except Exception as exc:
            return {
                "ok": False,
                "method": "macro_fallback",
                "message": f"Controlled macro generation failed: {exc}",
                "macro_path": path_to_string(macro_path),
            }

        details = {
            "macro_path": macro_path,
            "result_path": result_path,
            "thread_spec": thread_spec,
            "thread_size": _holewizard_thread_size(thread_spec),
            "hole_count": len(hole_points),
            "units": plan.units,
        }
        self.record_event("holewizard.macro", "written", details)
        if self._config.macro_execution_disabled:
            message = "SOLIDWORKS_MCP_DISABLE_MACRO_EXECUTION is enabled; macro was written but not executed."
            self.record_event("holewizard.macro", "skipped", {**details, "reason": message})
            return {
                "ok": False,
                "method": "macro_fallback",
                "message": message,
                "macro_path": path_to_string(macro_path),
                "result_path": path_to_string(result_path),
            }

        sw = self._require_sw()
        import pythoncom
        import win32com.client

        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        started_at = perf_counter()
        try:
            run_result = sw.RunMacro2(
                str(macro_path),
                "HoleWizardFallback",
                "main",
                SW_RUN_MACRO_UNLOAD_AFTER_RUN,
                errors,
            )
            self.record_com_call(
                "SldWorks.RunMacro2",
                {"macro_path": macro_path, "module": "HoleWizardFallback", "procedure": "main"},
                result={"run_result": run_result, "errors": errors.value},
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "SldWorks.RunMacro2",
                {"macro_path": macro_path, "module": "HoleWizardFallback", "procedure": "main"},
                error=exc,
                started_at=started_at,
            )
            self.record_event("holewizard.macro", "failed", {**details, "error": str(exc)})
            return {
                "ok": False,
                "method": "macro_fallback",
                "message": f"RunMacro2 raised: {exc}",
                "macro_path": path_to_string(macro_path),
                "result_path": path_to_string(result_path),
            }

        macro_error = int(errors.value or 0)
        if run_result is False or macro_error:
            if run_result is False and macro_error == 0 and macro_path.suffix.lower() == ".swb":
                message = (
                    "RunMacro2 rejected the generated .swb text macro without an error code; "
                    "this SolidWorks install appears to require a runnable .swp macro project."
                )
            else:
                message = f"RunMacro2 failed with result={run_result}, error_code={macro_error}."
            self.record_event("holewizard.macro", "failed", {**details, "run_result": run_result, "error_code": macro_error})
            return {
                "ok": False,
                "method": "macro_fallback",
                "message": message,
                "macro_path": path_to_string(macro_path),
                "result_path": path_to_string(result_path),
                "run_result": bool(run_result),
                "error_code": macro_error,
            }

        if not result_path.exists():
            message = "RunMacro2 completed but the controlled macro did not write its result file."
            self.record_event("holewizard.macro", "failed", {**details, "run_result": run_result, "reason": message})
            return {
                "ok": False,
                "method": "macro_fallback",
                "message": message,
                "macro_path": path_to_string(macro_path),
                "result_path": path_to_string(result_path),
                "run_result": bool(run_result),
                "error_code": macro_error,
            }

        result_text = result_path.read_text(encoding="utf-8", errors="replace").strip()
        if '"ok": true' not in result_text.lower():
            message = f"Controlled macro reported failure: {result_text[:500]}"
            self.record_event("holewizard.macro", "failed", {**details, "result": result_text[:500]})
            return {
                "ok": False,
                "method": "macro_fallback",
                "message": message,
                "macro_path": path_to_string(macro_path),
                "result_path": path_to_string(result_path),
                "run_result": bool(run_result),
                "error_code": macro_error,
            }

        self.record_event("holewizard.macro", "completed", {**details, "run_result": run_result, "error_code": macro_error})
        return {
            "ok": True,
            "method": "macro_fallback",
            "thread_model_status": "macro_threaded_hole",
            "thread_spec": thread_spec,
            "thread_size": _holewizard_thread_size(thread_spec),
            "hole_count": len(hole_points),
            "macro_path": path_to_string(macro_path),
            "result_path": path_to_string(result_path),
            "run_result": bool(run_result),
            "error_code": macro_error,
        }

    def _create_geometry_cut_holes(
        self,
        hole_points: list[list[float]] | list[tuple[float, float]],
        thread_spec: str,
        depth: float,
        plan: ModelPlan,
    ) -> dict[str, Any]:
        """Cut tap-drill circles through the plate when thread metadata cannot be modeled."""

        model = self._require_model()
        thread_info = ISO_METRIC_COARSE_THREADS[thread_spec]
        radius = thread_info["tap_drill_diameter"] / 2
        if not self._select_top_face_for_points(hole_points, depth, plan):
            raise RuntimeError("Could not select the top face for fallback hole sketch.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        for point in hole_points:
            sketch.CreateCircleByRadius(
                _to_meters(point[0], plan.units),
                _to_meters(point[1], plan.units),
                _to_meters(depth, plan.units),
                _to_meters(radius, plan.units),
            )
        sketch.InsertSketch(True)
        depth_m = _to_meters(depth + 1, plan.units)
        feature = model.FeatureManager.FeatureCut4(
            True, False, False, SW_END_COND_THROUGH_ALL, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            False, True, True, True, True, False, 0, 0, False, False
        )
        if feature is None:
            raise RuntimeError("Geometry fallback cut holes failed.")
        self._last_hole_features = [feature]
        return {
            "thread_spec": thread_spec,
            "tap_drill_diameter": thread_info["tap_drill_diameter"],
            "hole_count": len(hole_points),
        }

    def _cut_circular_profiles_through_depth(
        self,
        hole_points: list[tuple[float, float]],
        diameter: float,
        depth: float,
        plan: ModelPlan,
        *,
        purpose: str,
    ) -> dict[str, Any]:
        """Cut one or more plain circular profiles through an extruded body."""

        if not hole_points:
            raise RuntimeError(f"{purpose} requires at least one cut point.")
        model = self._require_model()
        if not self._select_top_face_for_points(hole_points, depth, plan):
            raise RuntimeError(f"Could not select the cut face for {purpose}.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        z_m = _to_meters(depth, plan.units)
        radius_m = _to_meters(diameter / 2, plan.units)
        for point in hole_points:
            sketch.CreateCircleByRadius(
                _to_meters(point[0], plan.units),
                _to_meters(point[1], plan.units),
                z_m,
                radius_m,
            )
        sketch.InsertSketch(True)
        depth_m = _to_meters(depth + 1, plan.units)
        started_at = perf_counter()
        feature = model.FeatureManager.FeatureCut4(
            True, False, False, SW_END_COND_THROUGH_ALL, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            False, True, True, True, True, False, 0, 0, False, False
        )
        self.record_com_call(
            "FeatureManager.FeatureCut4",
            {
                "purpose": purpose,
                "profile": "circles",
                "diameter_m": _to_meters(diameter, plan.units),
                "point_count": len(hole_points),
                "end_condition": SW_END_COND_THROUGH_ALL,
            },
            result=feature,
            started_at=started_at,
        )
        model.ClearSelection2(True)
        if feature is None:
            raise RuntimeError(f"{purpose} cut failed.")
        return {
            "status": "cut_created",
            "method": "FeatureCut4",
            "profile": "circles",
            "diameter": diameter,
            "depth": depth,
            "hole_count": len(hole_points),
        }

    def _cut_straight_slot_through_depth(
        self,
        slot_length: float,
        slot_width: float,
        depth: float,
        plan: ModelPlan,
        *,
        purpose: str,
    ) -> dict[str, Any]:
        """Cut a centered straight slot through an extruded body."""

        if slot_length <= slot_width:
            raise RuntimeError(f"{purpose} requires slot_length greater than slot_width.")
        model = self._require_model()
        slot_radius = slot_width / 2
        left_center = -(slot_length - slot_width) / 2
        right_center = (slot_length - slot_width) / 2
        cap_cut = self._cut_circular_profiles_through_depth(
            [(left_center, 0.0), (right_center, 0.0)],
            slot_width,
            depth,
            plan,
            purpose=f"{purpose}_caps",
        )
        if not self._select_top_face_at_point((0.0, 0.0), depth, plan):
            raise RuntimeError(f"Could not select the cut face for {purpose}.")
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        bridge_overlap = min(slot_width * 0.1, 1.0)
        bridge_half_length = (slot_length - slot_width) / 2 + bridge_overlap
        sketch.CreateCenterRectangle(
            0,
            0,
            _to_meters(depth, plan.units),
            _to_meters(bridge_half_length, plan.units),
            _to_meters(slot_radius, plan.units),
            _to_meters(depth, plan.units),
        )
        sketch.InsertSketch(True)
        depth_m = _to_meters(depth + 1, plan.units)
        started_at = perf_counter()
        feature = model.FeatureManager.FeatureCut4(
            True, False, False, SW_END_COND_THROUGH_ALL, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            False, True, True, True, True, False, 0, 0, False, False
        )
        self.record_com_call(
            "FeatureManager.FeatureCut4",
            {
                "purpose": purpose,
                "profile": "slot_bridge_rectangle",
                "slot_length_m": _to_meters(slot_length, plan.units),
                "slot_width_m": _to_meters(slot_width, plan.units),
                "bridge_half_length_m": _to_meters(bridge_half_length, plan.units),
                "end_condition": SW_END_COND_THROUGH_ALL,
            },
            result=feature,
            started_at=started_at,
        )
        model.ClearSelection2(True)
        if feature is None:
            raise RuntimeError(f"{purpose} cut failed.")
        return {
            "status": "cut_created",
            "method": "FeatureCut4",
            "profile": "two_caps_and_bridge",
            "slot_length": slot_length,
            "slot_width": slot_width,
            "depth": depth,
            "cap_cut_result": cap_cut,
            "bridge_cut_result": {
                "status": "cut_created",
                "method": "FeatureCut4",
                "profile": "slot_bridge_rectangle",
                "bridge_half_length": bridge_half_length,
            },
        }

    def _select_top_face_for_points(
        self,
        hole_points: list[list[float]] | list[tuple[float, float]],
        depth: float,
        plan: ModelPlan,
    ) -> bool:
        """Best-effort semantic selection for the plate face that receives holes."""

        if not hole_points:
            raise RuntimeError("No hole points supplied for top_face selection.")
        return self._select_top_face_at_point(hole_points[0], depth, plan)

    def _select_top_face_at_point(
        self,
        point: list[float] | tuple[float, float],
        depth: float,
        plan: ModelPlan,
    ) -> bool:
        """Select the top face at one XY point using a downward ray."""

        model = self._require_model()
        model.ClearSelection2(True)
        x, y = point
        z = depth + 0.1
        parameters = {
            "x": _to_meters(x, plan.units),
            "y": _to_meters(y, plan.units),
            "z": _to_meters(z, plan.units),
            "direction": [0, 0, -1],
            "radius": _to_meters(max(depth, 1), plan.units),
        }
        started_at = perf_counter()
        selected = model.Extension.SelectByRay(
            parameters["x"],
            parameters["y"],
            parameters["z"],
            0,
            0,
            -1,
            parameters["radius"],
            2,
            False,
            0,
            0,
        )
        self.record_com_call("ModelDocExtension.SelectByRay", parameters, result=selected, started_at=started_at)
        if not selected:
            self._warnings.append("semantic_selector.top_face:SelectByRay did not select a face")
        return bool(selected)

    def _draw_entity(self, entity: dict[str, Any], plan: ModelPlan) -> list[Any]:
        """Draw one supported sketch entity in model units."""

        sketch = self._require_model().SketchManager
        entity_type = entity.get("type")
        created: list[Any] = []
        if entity_type == "circle":
            center = entity["center"]
            radius_value = entity.get("radius")
            if radius_value is None:
                radius_value = float(entity["diameter"]) / 2
            radius = _to_meters(radius_value, plan.units)
            created = _as_sequence(
                sketch.CreateCircleByRadius(
                    _to_meters(center[0], plan.units),
                    _to_meters(center[1], plan.units),
                    0,
                    radius,
                )
            )
        elif entity_type == "center_rectangle":
            center = entity["center"]
            half_width = float(entity["width"]) / 2
            half_height = float(entity["height"]) / 2
            created = _as_sequence(
                sketch.CreateCornerRectangle(
                    _to_meters(float(center[0]) - half_width, plan.units),
                    _to_meters(float(center[1]) - half_height, plan.units),
                    0,
                    _to_meters(float(center[0]) + half_width, plan.units),
                    _to_meters(float(center[1]) + half_height, plan.units),
                    0,
                )
            )
        elif entity_type == "rectangle":
            corner1 = entity["corner1"]
            corner2 = entity["corner2"]
            created = _as_sequence(
                sketch.CreateCornerRectangle(
                    _to_meters(corner1[0], plan.units),
                    _to_meters(corner1[1], plan.units),
                    0,
                    _to_meters(corner2[0], plan.units),
                    _to_meters(corner2[1], plan.units),
                    0,
                )
            )
        elif entity_type == "line":
            start = entity["start"]
            end = entity["end"]
            created = _as_sequence(
                sketch.CreateLine(
                    _to_meters(start[0], plan.units),
                    _to_meters(start[1], plan.units),
                    0,
                    _to_meters(end[0], plan.units),
                    _to_meters(end[1], plan.units),
                    0,
                )
            )
            if entity.get("construction") or entity.get("for_construction"):
                for segment in created:
                    _set_sketch_segment_construction(segment)
        else:
            raise RuntimeError(f"Unsupported sketch entity type: {entity_type}")
        return created

    def _save_as(self, document: Any, path: Path) -> None:
        """Call SaveAs3 and normalize failed saves into Python exceptions."""

        import pythoncom
        import win32com.client

        path.parent.mkdir(parents=True, exist_ok=True)
        export_data = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        started_at = perf_counter()
        try:
            result = document.Extension.SaveAs(
                str(path),
                SW_SAVE_AS_CURRENT_VERSION,
                SW_SAVE_AS_OPTIONS_SILENT,
                export_data,
                errors,
                warnings,
            )
            self.record_com_call(
                "ModelDocExtension.SaveAs",
                {"path": path, "version": SW_SAVE_AS_CURRENT_VERSION, "options": SW_SAVE_AS_OPTIONS_SILENT},
                result=result,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "ModelDocExtension.SaveAs",
                {"path": path, "version": SW_SAVE_AS_CURRENT_VERSION, "options": SW_SAVE_AS_OPTIONS_SILENT},
                error=exc,
                started_at=started_at,
            )
            raise
        if result is False:
            raise RuntimeError(f"SolidWorks failed to save {path}")

    def _write_bom_csv(self, path: Path) -> None:
        """Write the verified BOM rows to CSV."""

        columns = [str(item) for item in self._bom_result.get("columns", [])]
        rows = self._bom_result.get("rows", [])
        if not columns or not isinstance(rows, list):
            raise RuntimeError("BOM CSV was requested before BOM evidence was generated.")
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [",".join(columns)]
        for row in rows:
            if isinstance(row, dict):
                lines.append(",".join(str(row.get(column, "")) for column in columns))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_cut_list_csv(self, path: Path) -> None:
        """Write verified weldment cut-list rows to CSV."""

        columns = [str(item) for item in self._cut_list_result.get("columns", [])]
        rows = self._cut_list_result.get("rows", [])
        if not columns or not isinstance(rows, list):
            raise RuntimeError("Cut-list CSV was requested before weldment evidence was generated.")
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [",".join(columns)]
        for row in rows:
            if isinstance(row, dict):
                lines.append(",".join(str(row.get(column, "")) for column in columns))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_simulation_csv(self, path: Path) -> None:
        """Write verified controlled simulation result rows to CSV."""

        columns = [str(item) for item in self._simulation_result.get("columns", [])]
        rows = self._simulation_result.get("rows", [])
        if not columns or not isinstance(rows, list):
            raise RuntimeError("Simulation CSV was requested before simulation evidence was generated.")
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [",".join(columns)]
        for row in rows:
            if isinstance(row, dict):
                lines.append(",".join(str(row.get(column, "")) for column in columns))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _ensure_part_saved(self, plan: ModelPlan) -> Path:
        """Save the active model so drawing views can reference a stable file path."""

        if self._active_part_path is None:
            workspace = self._require_workspace() / "exports"
            workspace.mkdir(parents=True, exist_ok=True)
            suffix = "sldasm" if _is_bom_assembly_plan(plan) else "sldprt"
            self._active_part_path = workspace / f"{safe_output_name(plan.name)}.{suffix}"
        self._save_as(self._require_model(), self._active_part_path)
        return self._active_part_path

    def _create_standard_drawing_views(self, part_path: Path) -> dict[str, Any]:
        """Create front, top, right and isometric views from the saved part."""

        drawing = self._drawing
        if drawing is None:
            return {"status": "no_drawing_document", "views": [], "errors": ["no_drawing_document"]}

        view_specs = (
            ("front", ("*Front", "*前视"), 0.18, 0.16),
            ("top", ("*Top", "*上视"), 0.18, 0.28),
            ("right", ("*Right", "*右视"), 0.34, 0.16),
            ("isometric", ("*Isometric", "*等轴测"), 0.34, 0.28),
        )
        self._drawing_view_handles = {}
        created = 0
        errors: list[str] = []
        views: list[dict[str, Any]] = []
        for role, view_names, x_position, y_position in view_specs:
            view_created = False
            view_errors: list[str] = []
            for view_name in view_names:
                try:
                    started_at = perf_counter()
                    view = drawing.CreateDrawViewFromModelView3(str(part_path), view_name, x_position, y_position, 0)
                    self.record_com_call(
                        "DrawingDoc.CreateDrawViewFromModelView3",
                        {"part_path": part_path, "view_name": view_name, "x": x_position, "y": y_position},
                        result=view,
                        started_at=started_at,
                    )
                    if view is not None:
                        created += 1
                        view_created = True
                        self._drawing_view_handles[role] = view
                        views.append({"role": role, "name": view_name, "x": x_position, "y": y_position})
                        break
                    view_errors.append(f"{view_name}:no_view")
                except Exception as exc:
                    self.record_com_call(
                        "DrawingDoc.CreateDrawViewFromModelView3",
                        {"part_path": part_path, "view_name": view_name, "x": x_position, "y": y_position},
                        error=exc,
                        started_at=started_at,
                    )
                    view_errors.append(f"{view_name}:{exc}")
            if not view_created:
                errors.extend(view_errors)

        if created == len(view_specs):
            status = "created"
        elif created > 0:
            status = f"partial:{created}/{len(view_specs)}"
        else:
            status = "failed"
        required_roles = [str(spec[0]) for spec in view_specs]
        created_roles = {str(view.get("role")) for view in views if view.get("role")}
        missing_roles = [role for role in required_roles if role not in created_roles]
        return {
            "status": status,
            "views": views,
            "created_count": created,
            "required_roles": required_roles,
            "missing_roles": missing_roles,
            "errors": errors,
        }

    def _try_insert_basic_dimensions(
        self,
        plan: ModelPlan,
        view_result: dict[str, Any],
        profile: DrawingProfile,
    ) -> dict[str, Any]:
        """Create real display dimensions for the MVP mounting plate drawing."""

        required_dimensions = _trusted_basic_dimension_ids_from_plan(plan)
        result: dict[str, Any] = {
            "status": "dimension_creation_failed",
            "required_dimensions": required_dimensions,
            "created_dimensions": [],
            "created_dimension_count": 0,
            "missing_dimensions": list(required_dimensions),
            "dimension_layout_status": "not_created",
            "attempts": [],
        }
        if not profile.include_basic_dimensions:
            result.update({"status": "not_requested", "missing_dimensions": []})
            self.record_event("drawing.basic_dimensions", "skipped", result)
            return result

        drawing = self._drawing
        if drawing is None:
            result.update({"status": "no_drawing_document", "failure_reason": "No drawing document is active."})
            self.record_event("drawing.basic_dimensions", "failed", result)
            return result

        if self._config.force_drawing_dimension_failure:
            result.update(
                {
                    "status": "forced_failure",
                    "failure_reason": "SOLIDWORKS_MCP_FORCE_DRAWING_DIMENSION_FAILURE is enabled",
                }
            )
            self.record_event("drawing.basic_dimensions", "failed", result)
            return result

        missing_views = [role for role in ("top", "front", "right") if role not in self._drawing_view_handles]
        if missing_views:
            result.update(
                {
                    "status": "no_required_views",
                    "failure_reason": f"Missing required drawing views: {missing_views}",
                    "missing_views": missing_views,
                }
            )
            self.record_event("drawing.basic_dimensions", "failed", result)
            return result

        flange_params = center_hole_flange_parameters_from_plan(plan)
        if flange_params is not None:
            return self._try_insert_center_hole_flange_dimensions(
                drawing,
                plan,
                flange_params,
                required_dimensions,
                result,
            )

        center_hole_plate_params = center_hole_plate_parameters_from_plan(plan)
        if center_hole_plate_params is not None:
            return self._try_insert_center_hole_plate_dimensions(
                drawing,
                plan,
                center_hole_plate_params,
                required_dimensions,
                result,
            )

        bracket_params = bracket_parameters_from_plan(plan)
        if bracket_params is not None:
            return self._try_insert_bracket_dimensions(
                drawing,
                plan,
                bracket_params,
                required_dimensions,
                result,
            )

        slotted_array_plate_params = slotted_array_plate_parameters_from_plan(plan)
        if slotted_array_plate_params is not None:
            return self._try_insert_slotted_array_plate_dimensions(
                drawing,
                plan,
                slotted_array_plate_params,
                required_dimensions,
                result,
            )

        end_cap_params = end_cap_parameters_from_plan(plan)
        if end_cap_params is not None:
            return self._try_insert_end_cap_dimensions(
                drawing,
                plan,
                end_cap_params,
                required_dimensions,
                result,
            )

        mounting_block_params = mounting_block_parameters_from_plan(plan)
        if mounting_block_params is not None:
            return self._try_insert_mounting_block_dimensions(
                drawing,
                plan,
                mounting_block_params,
                required_dimensions,
                result,
            )

        shaft_params = shaft_parameters_from_plan(plan)
        if shaft_params is not None:
            return self._try_insert_shaft_dimensions(
                drawing,
                plan,
                shaft_params,
                required_dimensions,
                result,
            )

        sheet_metal_params = sheet_metal_base_flange_parameters_from_plan(plan)
        if sheet_metal_params is not None:
            return self._try_insert_sheet_metal_base_flange_dimensions(
                drawing,
                plan,
                sheet_metal_params,
                required_dimensions,
                result,
            )

        weldment_params = weldment_frame_parameters_from_plan(plan)
        if weldment_params is not None:
            return self._try_insert_weldment_frame_dimensions(
                drawing,
                plan,
                weldment_params,
                required_dimensions,
                result,
            )

        simulation_params = static_simulation_parameters_from_plan(plan)
        if simulation_params is not None:
            return self._try_insert_static_simulation_dimensions(
                drawing,
                plan,
                simulation_params,
                required_dimensions,
                result,
            )

        washer_params = washer_parameters_from_plan(plan)
        if washer_params is not None:
            return self._try_insert_washer_dimensions(
                drawing,
                plan,
                washer_params,
                required_dimensions,
                result,
            )

        sleeve_params = sleeve_parameters_from_plan(plan)
        if sleeve_params is not None:
            return self._try_insert_sleeve_dimensions(
                drawing,
                plan,
                sleeve_params,
                required_dimensions,
                result,
            )

        atomic_dimensions = atomic_dimension_ids_from_metadata(plan.metadata)
        if atomic_dimensions:
            return self._try_insert_atomic_dimensions(
                drawing,
                plan,
                atomic_dimensions,
                result,
            )

        params = _mounting_plate_parameters(plan)
        if params is None:
            result.update(
                {
                    "status": "dimension_creation_failed",
                    "failure_reason": "No create_mounting_plate operation was found for basic dimension inference.",
                }
            )
            self.record_event("drawing.basic_dimensions", "failed", result)
            return result

        imported = self._try_import_model_dimensions(drawing, view_result, required_dimensions)
        result["import_model_dimensions_result"] = imported
        if imported["created_dimension_count"] >= len(required_dimensions):
            result["created_dimensions"] = [
                {"id": dimension_id, "method": "InsertModelAnnotations3", "is_display_dimension": True}
                for dimension_id in required_dimensions
            ]
            result["created_dimension_count"] = len(required_dimensions)
            result["missing_dimensions"] = []
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _basic_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [created_by_id[dimension_id] for dimension_id in required_dimensions if dimension_id in created_by_id]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [dimension_id for dimension_id in required_dimensions if dimension_id not in created_by_id]
        if any(item.get("proxy_dimension") for item in result["created_dimensions"]):
            result["dimension_layout_status"] = "radius_proxy_used"
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            if result["dimension_layout_status"] != "radius_proxy_used":
                result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        if not result["created_dimensions"]:
            result["status"] = "edge_selection_failed"
            result["failure_reason"] = "No required drawing entities could be selected for basic dimensions."
        else:
            result["status"] = "dimension_creation_failed"
            result["failure_reason"] = f"Missing required dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_atomic_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for a staged atomic model session."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _atomic_dimension_specs(plan, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if any(item.get("proxy_dimension") for item in result["created_dimensions"]):
            result["dimension_layout_status"] = "radius_proxy_used"
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            if result["dimension_layout_status"] != "radius_proxy_used":
                result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "atomic_dimensions_incomplete"
        result["failure_reason"] = f"Missing required atomic dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_center_hole_flange_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, float],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled center-hole flange."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _center_hole_flange_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "flange_dimensions_incomplete"
        result["failure_reason"] = f"Missing required flange dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_center_hole_plate_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, float],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled center-hole plate."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _center_hole_plate_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "center_hole_plate_dimensions_incomplete"
        result["failure_reason"] = f"Missing required center-hole plate dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_bracket_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, float],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled bracket."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _bracket_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "bracket_dimensions_incomplete"
        result["failure_reason"] = f"Missing required bracket dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_slotted_array_plate_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, float],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled slotted-array plate."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _slotted_array_plate_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "slotted_array_plate_dimensions_incomplete"
        result["failure_reason"] = f"Missing required slotted-array plate dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_end_cap_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, float],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled end cap."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _end_cap_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "end_cap_dimensions_incomplete"
        result["failure_reason"] = f"Missing required end-cap dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_washer_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, float],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled washer."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _washer_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "washer_dimensions_incomplete"
        result["failure_reason"] = f"Missing required washer dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_mounting_block_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, float],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled mounting block."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _mounting_block_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "mounting_block_dimensions_incomplete"
        result["failure_reason"] = f"Missing required mounting block dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_sleeve_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, float],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled sleeve."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _sleeve_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "sleeve_dimensions_incomplete"
        result["failure_reason"] = f"Missing required sleeve dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_shaft_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, float],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled shaft."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _shaft_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "shaft_dimensions_incomplete"
        result["failure_reason"] = f"Missing required shaft dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_sheet_metal_base_flange_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, float],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled sheet-metal base flange."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _sheet_metal_base_flange_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "sheet_metal_dimensions_incomplete"
        result["failure_reason"] = f"Missing required sheet-metal dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_weldment_frame_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, Any],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled weldment frame."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _weldment_frame_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "weldment_dimensions_incomplete"
        result["failure_reason"] = f"Missing required weldment dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_insert_static_simulation_dimensions(
        self,
        drawing: Any,
        plan: ModelPlan,
        params: dict[str, Any],
        required_dimensions: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create trusted display dimensions for the controlled simulation beam."""

        created_by_id: dict[str, dict[str, Any]] = {}
        for spec in _static_simulation_dimension_specs(params, plan.units, self._drawing_view_handles):
            attempt = self._try_create_basic_dimension_from_spec(drawing, spec)
            result["attempts"].append(attempt)
            if attempt.get("created"):
                created_by_id[str(spec["id"])] = {
                    "id": str(spec["id"]),
                    "method": str(attempt.get("method")),
                    "is_display_dimension": attempt.get("is_display_dimension") is not False,
                    "proxy_dimension": attempt.get("proxy_dimension") is True,
                }

        result["created_dimensions"] = [
            created_by_id[dimension_id]
            for dimension_id in required_dimensions
            if dimension_id in created_by_id
        ]
        result["created_dimension_count"] = len(result["created_dimensions"])
        result["missing_dimensions"] = [
            dimension_id
            for dimension_id in required_dimensions
            if dimension_id not in created_by_id
        ]
        if not result["missing_dimensions"]:
            result["status"] = "basic_dimensions_created"
            result["dimension_layout_status"] = "trusted_dimensions_created"
            self.record_event("drawing.basic_dimensions", "completed", result)
            return result

        result["status"] = "dimension_creation_failed"
        result["dimension_layout_status"] = "simulation_dimensions_incomplete"
        result["failure_reason"] = f"Missing required simulation dimensions: {result['missing_dimensions']}"
        self.record_event("drawing.basic_dimensions", "failed", result)
        return result

    def _try_import_model_dimensions(
        self,
        drawing: Any,
        view_result: dict[str, Any],
        required_dimensions: list[str],
    ) -> dict[str, Any]:
        """Try importing model dimensions as real drawing display dimensions."""

        result: dict[str, Any] = {"method": "InsertModelAnnotations3", "created_dimension_count": 0, "attempts": []}
        method = _get_com_member(drawing, "InsertModelAnnotations3")
        if not callable(method):
            result["failure_reason"] = "DrawingDoc.InsertModelAnnotations3 is not available"
            return result

        view_names = [view.get("name") for view in view_result.get("views", []) if view.get("name")]
        for view_name in view_names + [None]:
            if view_name:
                activate_view = _get_com_member(drawing, "ActivateView")
                if callable(activate_view):
                    started_at = perf_counter()
                    try:
                        activated = activate_view(str(view_name))
                        self.record_com_call(
                            "DrawingDoc.ActivateView",
                            {"view_name": str(view_name), "purpose": "basic_dimensions"},
                            result=activated,
                            started_at=started_at,
                        )
                    except Exception as exc:
                        self.record_com_call(
                            "DrawingDoc.ActivateView",
                            {"view_name": str(view_name), "purpose": "basic_dimensions"},
                            error=exc,
                            started_at=started_at,
                        )
            for all_views in (False, True):
                attempt = {"view_name": view_name, "all_views": all_views}
                before_count = self._annotation_count(self._drawing_view_handles.get("top"))
                started_at = perf_counter()
                try:
                    annotations = method(0, SW_INSERT_DIMENSIONS, all_views, False, False, True)
                    self.record_com_call(
                        "DrawingDoc.InsertModelAnnotations3",
                        {"types": SW_INSERT_DIMENSIONS, "all_views": all_views, "purpose": "basic_dimensions"},
                        result=annotations,
                        started_at=started_at,
                    )
                except Exception as exc:
                    self.record_com_call(
                        "DrawingDoc.InsertModelAnnotations3",
                        {"types": SW_INSERT_DIMENSIONS, "all_views": all_views, "purpose": "basic_dimensions"},
                        error=exc,
                        started_at=started_at,
                    )
                    attempt["failure_reason"] = str(exc)
                    result["attempts"].append(attempt)
                    continue

                after_count = self._annotation_count(self._drawing_view_handles.get("top"))
                returned_count = len(_as_sequence(annotations))
                count_changed = before_count is not None and after_count is not None and after_count > before_count
                created = returned_count if returned_count else (1 if count_changed else 0)
                attempt.update(
                    {
                        "returned_annotation_count": returned_count,
                        "annotation_count_before": before_count,
                        "annotation_count_after": after_count,
                        "created": created,
                    }
                )
                result["attempts"].append(attempt)
                if created > result["created_dimension_count"]:
                    result["created_dimension_count"] = created
                if result["created_dimension_count"] >= len(required_dimensions):
                    return result
        result["failure_reason"] = "InsertModelAnnotations3 did not import enough model dimensions for MVP acceptance."
        return result

    def _try_create_basic_dimension_from_spec(self, drawing: Any, spec: dict[str, Any]) -> dict[str, Any]:
        """Select drawing entities for one MVP dimension and create a DisplayDimension."""

        attempt: dict[str, Any] = {
            "id": spec["id"],
            "view_role": spec["view_role"],
            "method": spec["method"],
            "selected_count": 0,
            "created": False,
            "points": spec["points"],
            "point_set_attempts": [],
        }
        point_sets = spec.get("point_sets") or [spec["points"]]
        if spec.get("edge_selector"):
            edge_attempt: dict[str, Any] = {
                "point_set_index": -1,
                "selection_method": "drawing_view_edge",
                "selected_count": 0,
                "points": [],
            }
            self._clear_drawing_selection()
            selected_edge = self._select_dimension_edge_from_spec(spec, edge_attempt)
            if selected_edge:
                selected_count = int(edge_attempt.get("selected_count") or 1)
                edge_attempt["selected_count"] = selected_count
                dimension = self._add_basic_dimension(drawing, spec, edge_attempt)
                is_display_dimension = _is_display_dimension(dimension)
                created = dimension is not None and dimension is not False and is_display_dimension is not False
                edge_attempt["is_display_dimension"] = is_display_dimension
                edge_attempt["created"] = created
                attempt["point_set_attempts"].append(edge_attempt)
                attempt["selected_count"] = selected_count
                attempt["method"] = str(edge_attempt.get("method") or spec["method"])
                attempt["is_display_dimension"] = is_display_dimension
                attempt["created"] = created
                if created:
                    attempt["points"] = []
                    self._clear_drawing_selection()
                    return attempt
                edge_attempt["failure_reason"] = "Dimension API did not return a verified display dimension for selected drawing view edge."
            else:
                edge_attempt["failure_reason"] = "No matching drawing view edge could be selected for this dimension."
                attempt["point_set_attempts"].append(edge_attempt)

        for point_set_index, points in enumerate(point_sets):
            point_set_attempt: dict[str, Any] = {
                "point_set_index": point_set_index,
                "selected_count": 0,
                "points": points,
            }
            self._clear_drawing_selection()
            for index, point in enumerate(points):
                selected = self._select_drawing_entity_by_sheet_point(
                    point["x"],
                    point["y"],
                    0,
                    index,
                    tuple(point.get("selection_types", ("EDGE", "SKETCHSEGMENT"))),
                    append=index > 0,
                    mark=0,
                )
                if selected:
                    point_set_attempt["selected_count"] += 1
                else:
                    point_set_attempt.setdefault("selection_failures", []).append({"index": index, "point": point})

            minimum_selections = int(spec.get("minimum_selections", len(points)))
            if point_set_attempt["selected_count"] < minimum_selections:
                point_set_attempt["failure_reason"] = "Not enough drawing entities were selected for this dimension."
                attempt["point_set_attempts"].append(point_set_attempt)
                continue

            dimension = self._add_basic_dimension(drawing, spec, point_set_attempt)
            is_display_dimension = _is_display_dimension(dimension)
            created = dimension is not None and dimension is not False and is_display_dimension is not False
            point_set_attempt["is_display_dimension"] = is_display_dimension
            point_set_attempt["created"] = created
            attempt["point_set_attempts"].append(point_set_attempt)
            attempt["selected_count"] = int(point_set_attempt["selected_count"])
            attempt["method"] = str(point_set_attempt.get("method") or spec["method"])
            attempt["is_display_dimension"] = is_display_dimension
            attempt["created"] = created
            if created:
                attempt["points"] = points
                self._clear_drawing_selection()
                return attempt
            point_set_attempt["failure_reason"] = "Dimension API did not return a verified display dimension."

        if not attempt["created"]:
            for proxy_spec in spec.get("proxy_specs", []):
                proxy_attempt = self._try_create_basic_dimension_from_spec(drawing, proxy_spec)
                attempt.setdefault("proxy_attempts", []).append(proxy_attempt)
                if proxy_attempt.get("created"):
                    attempt.update(
                        {
                            "selected_count": proxy_attempt.get("selected_count", 0),
                            "method": f"{proxy_attempt.get('method')}_proxy",
                            "is_display_dimension": proxy_attempt.get("is_display_dimension"),
                            "created": True,
                            "points": proxy_attempt.get("points", []),
                            "proxy_dimension": True,
                            "proxy_reason": spec.get("proxy_reason", "Primary dimension API did not create a display dimension."),
                        }
                    )
                    self._warnings.append(f"drawing_basic_dimensions:{spec['id']}:proxy_dimension_used")
                    self._clear_drawing_selection()
                    return attempt
            attempt["failure_reason"] = (
                "No point set returned a verified display dimension."
                if len(point_sets) > 1
                else "Dimension API did not return a verified display dimension."
            )
        self._clear_drawing_selection()
        return attempt

    def _select_drawing_entity_by_sheet_point(
        self,
        x: float,
        y: float,
        z: float,
        index: int,
        selection_types: tuple[str, ...],
        append: bool,
        mark: int,
    ) -> bool:
        """Select a drawing entity near a sheet coordinate with type fallbacks."""

        drawing = self._drawing
        extension = getattr(drawing, "Extension", None) if drawing is not None else None
        method = getattr(extension, "SelectByID2", None) if extension is not None else None
        if not callable(method):
            return False

        import pythoncom
        import win32com.client

        callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        for selection_type in selection_types:
            started_at = perf_counter()
            try:
                selected = method("", selection_type, x, y, z, append, mark, callout, 0)
                self.record_com_call(
                    "ModelDocExtension.SelectByID2",
                    {
                        "index": index,
                        "selection_type": selection_type,
                        "x": x,
                        "y": y,
                        "z": z,
                        "append": append,
                        "purpose": "basic_dimension",
                    },
                    result=selected,
                    started_at=started_at,
                )
                if selected:
                    return True
            except Exception as exc:
                self.record_com_call(
                    "ModelDocExtension.SelectByID2",
                    {
                        "index": index,
                        "selection_type": selection_type,
                        "x": x,
                        "y": y,
                        "z": z,
                        "append": append,
                        "purpose": "basic_dimension",
                    },
                    error=exc,
                    started_at=started_at,
                )
        return False

    def _add_basic_dimension(self, drawing: Any, spec: dict[str, Any], attempt: dict[str, Any]) -> Any:
        """Call the requested drawing dimension API at the spec text position."""

        position = spec["position"]
        method_names = [str(spec["method"]), *[str(name) for name in spec.get("fallback_methods", [])]]
        seen_methods: set[str] = set()
        for method_name in method_names:
            if method_name in seen_methods:
                continue
            seen_methods.add(method_name)
            if method_name == "Extension.AddSpecificDimension":
                dimension = self._add_specific_dimension(drawing, spec, attempt, position)
                if dimension is not None:
                    return dimension
                continue
            method = getattr(drawing, method_name, None)
            if not callable(method):
                attempt.setdefault("dimension_method_attempts", []).append(
                    {"method": method_name, "available": False}
                )
                continue

            started_at = perf_counter()
            try:
                dimension = method(position["x"], position["y"], 0)
                self.record_com_call(
                    f"DrawingDoc.{method_name}",
                    {"id": spec["id"], "x": position["x"], "y": position["y"], "purpose": "basic_dimension"},
                    result=dimension,
                    started_at=started_at,
                )
                is_display_dimension = _is_display_dimension(dimension)
                attempt.setdefault("dimension_method_attempts", []).append(
                    {
                        "method": method_name,
                        "available": True,
                        "returned": dimension is not None and dimension is not False,
                        "is_display_dimension": is_display_dimension,
                    }
                )
                if dimension is not None and dimension is not False and is_display_dimension is not False:
                    attempt["method"] = method_name
                    return dimension
            except Exception as exc:
                self.record_com_call(
                    f"DrawingDoc.{method_name}",
                    {"id": spec["id"], "x": position["x"], "y": position["y"], "purpose": "basic_dimension"},
                    error=exc,
                    started_at=started_at,
                )
                attempt.setdefault("dimension_method_attempts", []).append(
                    {"method": method_name, "available": True, "error": str(exc)}
                )
                self._warnings.append(f"DrawingDoc.{method_name}:{spec['id']}:{exc}")
        return None

    def _select_dimension_edge_from_spec(self, spec: dict[str, Any], attempt: dict[str, Any]) -> bool:
        """Select a model edge exposed in a drawing view for a dimension spec."""

        selector = str(spec.get("edge_selector", ""))
        view = self._drawing_view_handles.get(str(spec.get("view_role", "")))
        if view is None:
            attempt["failure_reason"] = f"Drawing view is not available for role {spec.get('view_role')}."
            return False
        if selector == "mounting_plate_hole_edge_offset":
            edge_result = self._visible_dimension_edges_for_view(view)
            attempt["visible_edge_count"] = edge_result["visible_edge_count"]
            attempt["edge_samples"] = edge_result["edge_samples"]
            candidates = _mounting_plate_hole_edge_offset_edges(edge_result["edges"], spec)
            if candidates is None:
                return False

            selected_edges = []
            self._clear_drawing_selection()
            for edge_index, candidate in enumerate(candidates):
                selected = self._select_drawing_view_entity(view, candidate["edge"], append=edge_index > 0)
                selected_edges.append({**candidate["summary"], "selected": selected})
                if not selected:
                    attempt["selected_edges"] = selected_edges
                    return False
            attempt["selected_edges"] = selected_edges
            attempt["selected_count"] = len(selected_edges)
            return True

        if selector == "center_hole_flange_diameter":
            edge_result = self._visible_dimension_edges_for_view(view)
            attempt["visible_edge_count"] = edge_result["visible_edge_count"]
            attempt["edge_samples"] = edge_result["edge_samples"]
            candidate = _best_center_hole_flange_circle_edge(edge_result["edges"], spec)
            if candidate is None:
                attempt["failure_reason"] = "No matching center-hole flange circular edge could be selected."
                return False

            attempt["selected_edge"] = candidate["summary"]
            selected = self._select_drawing_view_entity(view, candidate["edge"])
            attempt["selected_count"] = 1 if selected else 0
            return selected

        if selector == "line_edge_length":
            edge_result = self._visible_dimension_edges_for_view(view)
            attempt["visible_edge_count"] = edge_result["visible_edge_count"]
            attempt["edge_samples"] = edge_result["edge_samples"]
            candidate = _best_line_edge_for_length(edge_result["edges"], spec)
            if candidate is None:
                attempt["failure_reason"] = "No matching line edge could be selected."
                return False

            attempt["selected_edge"] = candidate["summary"]
            selected = self._select_drawing_view_entity(view, candidate["edge"])
            attempt["selected_count"] = 1 if selected else 0
            return selected

        if selector != "mounting_plate_corner_radius":
            attempt["failure_reason"] = f"Unsupported edge selector: {selector}"
            return False

        edge_result = self._visible_dimension_edges_for_view(view)
        attempt["visible_edge_count"] = edge_result["visible_edge_count"]
        attempt["edge_samples"] = edge_result["edge_samples"]
        candidate = _best_mounting_plate_radius_edge(edge_result["edges"], spec)
        if candidate is None:
            return False

        attempt["selected_edge"] = candidate["summary"]
        return self._select_drawing_view_entity(view, candidate["edge"])

    def _visible_dimension_edges_for_view(self, view: Any) -> dict[str, Any]:
        """Return visible drawing-view edges with compact curve samples."""

        components = self._get_visible_components(view)
        edges: list[Any] = []
        samples: list[dict[str, Any]] = []
        visible_edge_count = 0
        for component in components or [None]:
            component_edges = self._get_visible_entities(view, component, SW_VIEW_ENTITY_EDGE)
            visible_edge_count += len(component_edges)
            for edge in component_edges:
                edges.append(edge)
                if len(samples) < 12:
                    samples.append(_edge_curve_probe(edge))
        return {"visible_edge_count": visible_edge_count, "edges": edges, "edge_samples": samples}

    def _add_specific_dimension(
        self,
        drawing: Any,
        spec: dict[str, Any],
        attempt: dict[str, Any],
        position: dict[str, float],
    ) -> Any:
        """Call ModelDocExtension.AddSpecificDimension for dimension types that need it."""

        extension = getattr(drawing, "Extension", None)
        method = getattr(extension, "AddSpecificDimension", None) if extension is not None else None
        if not callable(method):
            attempt.setdefault("dimension_method_attempts", []).append(
                {"method": "Extension.AddSpecificDimension", "available": False}
            )
            return None

        import pythoncom
        import win32com.client

        error = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        dimension_type = int(spec.get("specific_dimension_type", SW_RADIAL_DIMENSION))
        started_at = perf_counter()
        try:
            dimension = method(position["x"], position["y"], 0, dimension_type, error)
            self.record_com_call(
                "ModelDocExtension.AddSpecificDimension",
                {
                    "id": spec["id"],
                    "x": position["x"],
                    "y": position["y"],
                    "dimension_type": dimension_type,
                    "purpose": "basic_dimension",
                },
                result=dimension,
                started_at=started_at,
            )
            is_display_dimension = _is_display_dimension(dimension)
            attempt.setdefault("dimension_method_attempts", []).append(
                {
                    "method": "Extension.AddSpecificDimension",
                    "available": True,
                    "returned": dimension is not None and dimension is not False,
                    "is_display_dimension": is_display_dimension,
                    "error_code": int(error.value),
                }
            )
            if dimension is not None and dimension is not False and is_display_dimension is not False:
                attempt["method"] = "Extension.AddSpecificDimension"
                return dimension
        except Exception as exc:
            self.record_com_call(
                "ModelDocExtension.AddSpecificDimension",
                {
                    "id": spec["id"],
                    "x": position["x"],
                    "y": position["y"],
                    "dimension_type": dimension_type,
                    "purpose": "basic_dimension",
                },
                error=exc,
                started_at=started_at,
            )
            attempt.setdefault("dimension_method_attempts", []).append(
                {"method": "Extension.AddSpecificDimension", "available": True, "error": str(exc)}
            )
            self._warnings.append(f"ModelDocExtension.AddSpecificDimension:{spec['id']}:{exc}")
        return None

    def _try_insert_thread_callouts(self, plan: ModelPlan, view_result: dict[str, Any]) -> dict[str, Any]:
        """Create real drawing hole callouts from selected hole-face view edges."""

        result: dict[str, Any] = {
            "status": "hole_callout_failed",
            "view_name": None,
            "selected_edge_count": 0,
            "created_callout_count": 0,
            "attempts": [],
        }
        if shaft_parameters_from_plan(plan) is not None:
            result.update(
                {
                    "status": "not_requested",
                    "failure_reason": None,
                    "reason": "controlled_shaft_has_no_holes",
                    "direct_hole_callout_created": None,
                    "callout_creation_method": None,
                }
            )
            self.record_event("drawing.hole_callout", "skipped", result)
            return result
        drawing = self._drawing
        if drawing is None:
            result.update({"status": "no_drawing_document", "failure_reason": "No drawing document is active."})
            self.record_event("drawing.hole_callout", "failed", result)
            return result

        if self._config.force_drawing_callout_failure:
            result.update(
                {
                    "status": "forced_failure",
                    "failure_reason": "SOLIDWORKS_MCP_FORCE_DRAWING_CALLOUT_FAILURE is enabled",
                }
            )
            self.record_event("drawing.hole_callout", "failed", result)
            return result

        top_view = self._hole_callout_view_handle()
        if top_view is None:
            result.update({"status": "no_top_view", "failure_reason": "No hole-face drawing view handle was created."})
            self.record_event("drawing.hole_callout", "failed", result)
            return result

        result["view_name"] = result["view_name"] or _drawing_view_name(top_view)
        result["view_role"] = self._hole_callout_view_role()
        edge_result = self._visible_hole_edges_for_view(top_view)
        candidate_edges = edge_result["edges"]
        result["view_probe"] = self._probe_drawing_view_for_hole_edges(top_view)
        result.update(
            {
                "component_count": edge_result["component_count"],
                "visible_edge_count": edge_result["visible_edge_count"],
                "circular_edge_count": len(candidate_edges),
                "polyline_edge_count": edge_result.get("polyline_edge_count", 0),
                "polyline_numeric_count": edge_result.get("polyline_numeric_count", 0),
                "polyline_circular_edge_count": edge_result.get("polyline_circular_edge_count", 0),
                "polyline_error": edge_result.get("polyline_error"),
                "visible_edge_samples": edge_result.get("edge_samples", []),
            }
        )
        if not candidate_edges:
            point_result = self._try_insert_callouts_by_sheet_points(drawing, top_view, plan)
            result["attempts"] = point_result["attempts"]
            result["selected_edge_count"] = point_result["selected_edge_count"]
            result["created_callout_count"] = point_result["created_callout_count"]
            if point_result["created_callout_count"] > 0:
                result["status"] = "hole_callout_created"
                result["callout_creation_method"] = "add_hole_callout2"
                result["direct_hole_callout_created"] = True
                result["selection_fallback"] = "drawing_extension_select_by_id"
                self.record_event("drawing.hole_callout", "completed", result)
                return result
            model_annotation_result = self._try_insert_model_hole_callouts(drawing, top_view, result.get("view_name"))
            if model_annotation_result["created_callout_count"] > 0:
                result["status"] = "hole_callout_created"
                result["callout_creation_method"] = "insert_model_annotations3"
                result["direct_hole_callout_created"] = False
                result["direct_callout_failure_reason"] = (
                    "No hole-face drawing edge could be selected for AddHoleCallout2; "
                    "used InsertModelAnnotations3 fallback."
                )
                result["selection_fallback"] = "insert_model_annotations3"
                result["model_annotation_result"] = model_annotation_result
                result["created_callout_count"] = model_annotation_result["created_callout_count"]
                self.record_event("drawing.hole_callout", "completed", result)
                return result
            result["model_annotation_result"] = model_annotation_result
            result.update(
                {
                    "status": "edge_selection_failed",
                    "failure_reason": "Top view exposed no visible circular edges and sheet-point edge selection failed.",
                }
            )
            self.record_event("drawing.hole_callout", "failed", result)
            return result

        selected_edges = 0
        created_callouts = 0
        before_count = self._annotation_count(top_view)
        max_attempts = min(4, len(candidate_edges))
        for index, edge in enumerate(candidate_edges[:max_attempts]):
            attempt: dict[str, Any] = {"index": index, "selected": False, "callout_created": False}
            self._clear_drawing_selection()
            selected = self._select_drawing_view_entity(top_view, edge)
            attempt["selected"] = selected
            if not selected:
                attempt["failure_reason"] = "IView.SelectEntity returned false"
                result["attempts"].append(attempt)
                continue

            selected_edges += 1
            x_position, y_position, z_position = self._hole_callout_position(top_view, plan, index)
            attempt["position"] = {"x": x_position, "y": y_position, "z": z_position}
            callout = self._add_hole_callout(drawing, x_position, y_position, z_position, index)
            after_count = self._annotation_count(top_view)
            is_hole_callout = _is_hole_callout(callout)
            count_changed = before_count is not None and after_count is not None and after_count > before_count
            valid_callout = (
                callout is not None
                and callout is not False
                and is_hole_callout is not False
                and (count_changed or before_count is None or after_count is None or is_hole_callout is True)
            )
            attempt.update(
                {
                    "callout_created": valid_callout,
                    "annotation_count_before": before_count,
                    "annotation_count_after": after_count,
                    "is_hole_callout": is_hole_callout,
                }
            )
            if valid_callout:
                created_callouts += 1
                before_count = after_count
            else:
                attempt["failure_reason"] = "AddHoleCallout2 did not return a verified hole callout"
            result["attempts"].append(attempt)

        result["selected_edge_count"] = selected_edges
        result["created_callout_count"] = created_callouts
        if created_callouts > 0:
            result["status"] = "hole_callout_created"
            result["callout_creation_method"] = "add_hole_callout2"
            result["direct_hole_callout_created"] = True
            self.record_event("drawing.hole_callout", "completed", result)
            return result
        if selected_edges == 0:
            result.update({"status": "edge_selection_failed", "failure_reason": "No candidate hole edges could be selected."})
        else:
            result.update({"status": "hole_callout_failed", "failure_reason": "Selected hole edges did not produce callouts."})
        self.record_event("drawing.hole_callout", "failed", result)
        return result

    def _hole_callout_view_role(self) -> str:
        """Return the drawing view role that faces the MVP through-hole openings."""

        return "front"

    def _hole_callout_view_handle(self) -> Any:
        """Return the drawing view where MVP mounting-plate holes are visible as circles."""

        role = self._hole_callout_view_role()
        return self._drawing_view_handles.get(role) or self._drawing_view_handles.get("top")

    def _probe_drawing_view_for_hole_edges(self, view: Any) -> dict[str, Any]:
        """Collect safe COM-shape diagnostics for drawing-view edge discovery."""

        probe: dict[str, Any] = {
            "view_name": _drawing_view_name(view),
            "view_type": type(view).__name__,
            "members": {},
            "calls": [],
        }
        member_names = (
            "GetVisibleComponents",
            "GetVisibleComponents2",
            "GetVisibleEntities",
            "GetVisibleEntities2",
            "GetPolyLineCount5",
            "GetPolylines6",
            "GetDisplayMode2",
            "SetDisplayMode3",
            "RootDrawingComponent",
            "GetRootDrawingComponent",
            "GetFirstVisibleComponent",
            "GetNextVisibleComponent",
            "GetOutline",
            "Position",
            "ScaleDecimal",
            "ScaleRatio",
            "SelectEntity",
        )
        for name in member_names:
            member = _get_com_member(view, name)
            probe["members"][name] = {
                "available": member is not None,
                "callable": callable(member),
                "type": type(member).__name__ if member is not None else None,
            }

        for name in ("GetVisibleComponents", "GetVisibleComponents2", "GetRootDrawingComponent", "GetOutline"):
            probe["calls"].append(self._probe_com_call(view, name))

        root_component = _get_com_member(view, "RootDrawingComponent")
        if callable(root_component):
            root_component = _call_com_noargs(view, "RootDrawingComponent")
        if root_component is None:
            root_component = _call_com_noargs(view, "GetRootDrawingComponent")
        probe["root_drawing_component"] = _safe_com_summary(root_component)
        probe["root_component_children_count"] = len(_as_sequence(_call_or_get(root_component, "GetChildren")))
        component = _call_or_get(root_component, "Component")
        probe["root_component2"] = _safe_com_summary(component)
        for method_name in ("GetVisibleEntities2", "GetVisibleEntities"):
            for component_value, source in ((component, "root_component2"), (None, "none")):
                call = self._probe_visible_entities_call(view, method_name, component_value, source)
                probe["calls"].append(call)
        probe["polyline_edges"] = self._probe_polyline_edges(view)
        return probe

    def _probe_com_call(self, target: Any, method_name: str) -> dict[str, Any]:
        """Call a no-argument COM method and summarize the result without retaining COM objects."""

        call: dict[str, Any] = {"method": method_name, "available": False}
        method = _get_com_member(target, method_name)
        if method is not None and not callable(method):
            call["available"] = True
            call["member_kind"] = "property"
            call.update(_safe_com_summary(method))
            return call
        if not callable(method):
            return call
        call["available"] = True
        started_at = perf_counter()
        try:
            value = method()
            self.record_com_call(f"IView.{method_name}", {"purpose": "hole_edge_probe"}, result=value, started_at=started_at)
            call.update(_safe_com_summary(value))
        except Exception as exc:
            self.record_com_call(f"IView.{method_name}", {"purpose": "hole_edge_probe"}, error=exc, started_at=started_at)
            call["error"] = str(exc)
        return call

    def _probe_visible_entities_call(self, view: Any, method_name: str, component: Any, source: str) -> dict[str, Any]:
        """Call a visible-entities method and summarize result/error for diagnostics."""

        call: dict[str, Any] = {
            "method": method_name,
            "component_source": source,
            "entity_type": SW_VIEW_ENTITY_EDGE,
            "available": False,
        }
        method = _get_com_member(view, method_name)
        if not callable(method):
            return call
        call["available"] = True
        started_at = perf_counter()
        try:
            value = method(component, SW_VIEW_ENTITY_EDGE)
            self.record_com_call(
                f"IView.{method_name}",
                {"purpose": "hole_edge_probe", "component_source": source, "entity_type": SW_VIEW_ENTITY_EDGE},
                result=value,
                started_at=started_at,
            )
            call.update(_safe_com_summary(value))
            edges = _as_sequence(value)
            call["circular_edge_count"] = sum(1 for item in edges if _edge_looks_circular(item))
            call["edge_samples"] = [_edge_curve_probe(item) for item in edges[:8]]
        except Exception as exc:
            self.record_com_call(
                f"IView.{method_name}",
                {"purpose": "hole_edge_probe", "component_source": source, "entity_type": SW_VIEW_ENTITY_EDGE},
                error=exc,
                started_at=started_at,
            )
            call["error"] = str(exc)
        return call

    def _visible_hole_edges_for_view(self, view: Any) -> dict[str, Any]:
        """Return circular visible edges from the drawing view."""

        components = self._get_visible_components(view)
        visible_edge_count = 0
        circular_edges: list[Any] = []
        edge_samples: list[dict[str, Any]] = []
        for component in components or [None]:
            edges = self._get_visible_entities(view, component, SW_VIEW_ENTITY_EDGE)
            visible_edge_count += len(edges)
            for edge in edges:
                if len(edge_samples) < 12:
                    edge_samples.append(_edge_curve_probe(edge))
                if _edge_looks_circular(edge):
                    circular_edges.append(edge)
        polyline_result = self._get_polyline_edges(view) if not circular_edges else {"edges": [], "edge_count": 0}
        polyline_circular_count = 0
        for edge in polyline_result.get("edges", []):
            if len(edge_samples) < 12:
                edge_samples.append(_edge_curve_probe(edge))
            if _edge_looks_circular(edge):
                circular_edges.append(edge)
                polyline_circular_count += 1

        return {
            "component_count": len(components),
            "visible_edge_count": visible_edge_count,
            "polyline_edge_count": polyline_result.get("edge_count", 0),
            "polyline_numeric_count": polyline_result.get("polyline_numeric_count", 0),
            "polyline_circular_edge_count": polyline_circular_count,
            "polyline_error": polyline_result.get("error"),
            "edge_samples": edge_samples,
            "edges": circular_edges,
        }

    def _get_visible_components(self, view: Any) -> list[Any]:
        """Call IView.GetVisibleComponents with COM logging."""

        for method_name in ("GetVisibleComponents", "GetVisibleComponents2"):
            method = _get_com_member(view, method_name)
            if method is not None and not callable(method):
                components = _as_sequence(method)
                if components:
                    self.record_event(
                        "drawing.view_components",
                        "completed",
                        {"source": f"IView.{method_name}", "member_kind": "property", "count": len(components)},
                        level="verbose",
                    )
                    return components
                continue
            started_at = perf_counter()
            try:
                components = method()
                self.record_com_call(f"IView.{method_name}", {}, result=components, started_at=started_at)
                return _as_sequence(components)
            except Exception as exc:
                self.record_com_call(f"IView.{method_name}", {}, error=exc, started_at=started_at)
                self._warnings.append(f"IView.{method_name}:{exc}")
        root_component = _get_com_member(view, "RootDrawingComponent")
        if callable(root_component):
            root_component = _call_com_noargs(view, "RootDrawingComponent")
        root_components = _components_from_drawing_component(root_component)
        if root_components:
            self.record_event(
                "drawing.view_components",
                "completed",
                {"source": "RootDrawingComponent", "count": len(root_components)},
                level="verbose",
            )
            return root_components

        self._warnings.append("IView.GetVisibleComponents:not_available")
        return []

    def _get_visible_entities(self, view: Any, component: Any, entity_type: int) -> list[Any]:
        """Call IView.GetVisibleEntities2 for one visible component."""

        for method_name in ("GetVisibleEntities2", "GetVisibleEntities"):
            method = _get_com_member(view, method_name)
            if not callable(method):
                continue
            started_at = perf_counter()
            try:
                entities = method(component, entity_type)
                self.record_com_call(
                    f"IView.{method_name}",
                    {"entity_type": entity_type, "component": component is not None},
                    result=entities,
                    started_at=started_at,
                )
                return _as_sequence(entities)
            except Exception as exc:
                self.record_com_call(
                    f"IView.{method_name}",
                    {"entity_type": entity_type, "component": component is not None},
                    error=exc,
                    started_at=started_at,
                )
                self._warnings.append(f"IView.{method_name}:{exc}")
        self._warnings.append("IView.GetVisibleEntities:not_available")
        return []

    def _probe_polyline_edges(self, view: Any) -> dict[str, Any]:
        """Summarize projected drawing polylines and their backing model edges."""

        result = self._get_polyline_edges(view)
        edges = result.get("edges", [])
        return {
            "available": result.get("available", False),
            "polyline_count": result.get("polyline_count"),
            "polyline_numeric_count": result.get("polyline_numeric_count", 0),
            "edge_count": result.get("edge_count", 0),
            "circular_edge_count": sum(1 for edge in edges if _edge_looks_circular(edge)),
            "edge_samples": [_edge_curve_probe(edge) for edge in edges[:8]],
            "error": result.get("error"),
        }

    def _get_polyline_edges(self, view: Any) -> dict[str, Any]:
        """Return model edges paired with IView.GetPolylines6 projected data."""

        result: dict[str, Any] = {
            "available": False,
            "polyline_count": None,
            "polyline_numeric_count": 0,
            "edge_count": 0,
            "edges": [],
            "error": None,
        }

        count_member = _get_com_member(view, "GetPolyLineCount5")
        if callable(count_member):
            import pythoncom
            import win32com.client

            point_count = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)
            started_at = perf_counter()
            try:
                count = count_member(1, point_count)
                self.record_com_call(
                    "IView.GetPolyLineCount5",
                    {"purpose": "hole_edge_polyline_probe", "cross_hatch_option": 1},
                    result={"polyline_count": count, "point_count": point_count.value},
                    started_at=started_at,
                )
                result["polyline_count"] = _safe_int(count)
                result["polyline_point_count"] = _safe_int(point_count.value)
            except Exception as exc:
                self.record_com_call(
                    "IView.GetPolyLineCount5",
                    {"purpose": "hole_edge_polyline_probe", "cross_hatch_option": 1},
                    error=exc,
                    started_at=started_at,
                )
                result["error"] = str(exc)

        method = _get_com_member(view, "GetPolylines6")
        if not callable(method):
            result["error"] = result.get("error") or "IView.GetPolylines6 is not available"
            return result

        result["available"] = True
        import pythoncom
        import win32com.client

        polylines = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)
        started_at = perf_counter()
        try:
            raw_value = method(1, polylines)
            self.record_com_call(
                "IView.GetPolylines6",
                {"purpose": "hole_edge_polyline_probe", "cross_hatch_option": 1},
                result={"edges": raw_value, "polylines": polylines.value},
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "IView.GetPolylines6",
                {"purpose": "hole_edge_polyline_probe", "cross_hatch_option": 1},
                error=exc,
                started_at=started_at,
            )
            result["error"] = str(exc)
            return result

        edges = _extract_com_edges_from_polyline_result(raw_value)
        result["edges"] = edges
        result["edge_count"] = len(edges)
        result["polyline_numeric_count"] = _polyline_numeric_count(polylines.value)
        return result

    def _select_drawing_view_entity(self, view: Any, entity: Any, append: bool = False) -> bool:
        """Select one model entity inside a drawing view."""

        method = getattr(view, "SelectEntity", None)
        if not callable(method):
            return False
        started_at = perf_counter()
        try:
            selected = method(entity, append)
            self.record_com_call("IView.SelectEntity", {"append": append}, result=selected, started_at=started_at)
            return bool(selected)
        except Exception as exc:
            self.record_com_call("IView.SelectEntity", {"append": append}, error=exc, started_at=started_at)
            self._warnings.append(f"IView.SelectEntity:{exc}")
            return False

    def _add_hole_callout(self, drawing: Any, x: float, y: float, z: float, index: int) -> Any:
        """Call IDrawingDoc.AddHoleCallout2 at one sheet position."""

        method = getattr(drawing, "AddHoleCallout2", None)
        if not callable(method):
            return None
        started_at = perf_counter()
        try:
            callout = method(x, y, z)
            self.record_com_call(
                "DrawingDoc.AddHoleCallout2",
                {"index": index, "x": x, "y": y, "z": z},
                result=callout,
                started_at=started_at,
            )
            return callout
        except Exception as exc:
            self.record_com_call(
                "DrawingDoc.AddHoleCallout2",
                {"index": index, "x": x, "y": y, "z": z},
                error=exc,
                started_at=started_at,
            )
            self._warnings.append(f"DrawingDoc.AddHoleCallout2:{exc}")
            return None

    def _annotation_count(self, view: Any) -> int | None:
        """Return the view annotation count when SolidWorks exposes it."""

        method = getattr(view, "GetAnnotationCount", None)
        if not callable(method):
            return None
        try:
            return int(method())
        except Exception:
            return None

    def _clear_drawing_selection(self) -> None:
        """Clear active drawing selections before selecting the next hole edge."""

        drawing = self._drawing
        if drawing is None:
            return
        method = getattr(drawing, "ClearSelection2", None)
        if callable(method):
            try:
                method(True)
            except Exception:
                return

    def _hole_callout_position(self, view: Any, plan: ModelPlan, index: int) -> tuple[float, float, float]:
        """Place callout text near the selected hole in sheet coordinates."""

        view_x, view_y = _drawing_view_position(view)
        scale = _drawing_view_scale(view)
        offsets = ((-0.024, 0.018), (0.024, 0.018), (0.024, -0.018), (-0.024, -0.018))
        if index < len(self._last_hole_points):
            point = self._last_hole_points[index]
            x_position = view_x + (_to_meters(point[0], plan.units) * scale) + offsets[index % len(offsets)][0]
            y_position = view_y + (_to_meters(point[1], plan.units) * scale) + offsets[index % len(offsets)][1]
            return x_position, y_position, 0
        offset_x, offset_y = offsets[index % len(offsets)]
        return view_x + offset_x, view_y + offset_y, 0

    def _try_insert_callouts_by_sheet_points(self, drawing: Any, view: Any, plan: ModelPlan) -> dict[str, Any]:
        """Fallback: select drawing hole edges by sheet coordinates, then add callouts."""

        attempts: list[dict[str, Any]] = []
        selected_edges = 0
        created_callouts = 0
        before_count = self._annotation_count(view)
        point_count = min(4, len(self._last_hole_points))
        for index in range(point_count):
            x_pick, y_pick, z_pick = self._hole_edge_pick_position(view, plan, index)
            attempt: dict[str, Any] = {
                "index": index,
                "selected": False,
                "callout_created": False,
                "selection_method": "drawing_extension_select_by_id",
                "pick_position": {"x": x_pick, "y": y_pick, "z": z_pick},
            }
            self._clear_drawing_selection()
            selected = self._select_drawing_edge_by_sheet_point(x_pick, y_pick, z_pick, index)
            attempt["selected"] = selected
            if not selected:
                attempt["failure_reason"] = "ModelDocExtension.SelectByID2 did not select a drawing edge"
                attempts.append(attempt)
                continue

            selected_edges += 1
            x_position, y_position, z_position = self._hole_callout_position(view, plan, index)
            attempt["position"] = {"x": x_position, "y": y_position, "z": z_position}
            callout = self._add_hole_callout(drawing, x_position, y_position, z_position, index)
            after_count = self._annotation_count(view)
            is_hole_callout = _is_hole_callout(callout)
            count_changed = before_count is not None and after_count is not None and after_count > before_count
            valid_callout = (
                callout is not None
                and callout is not False
                and is_hole_callout is not False
                and (count_changed or before_count is None or after_count is None or is_hole_callout is True)
            )
            attempt.update(
                {
                    "callout_created": valid_callout,
                    "annotation_count_before": before_count,
                    "annotation_count_after": after_count,
                    "is_hole_callout": is_hole_callout,
                }
            )
            if valid_callout:
                created_callouts += 1
                before_count = after_count
            else:
                attempt["failure_reason"] = "AddHoleCallout2 did not return a verified hole callout"
            attempts.append(attempt)
        return {
            "attempts": attempts,
            "selected_edge_count": selected_edges,
            "created_callout_count": created_callouts,
        }

    def _select_drawing_edge_by_sheet_point(self, x: float, y: float, z: float, index: int) -> bool:
        """Select a drawing edge near a sheet coordinate."""

        drawing = self._drawing
        extension = getattr(drawing, "Extension", None) if drawing is not None else None
        method = getattr(extension, "SelectByID2", None) if extension is not None else None
        if not callable(method):
            return False

        import pythoncom
        import win32com.client

        callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        for selection_type in ("EDGE", "SKETCHSEGMENT"):
            started_at = perf_counter()
            try:
                selected = method("", selection_type, x, y, z, False, 0, callout, 0)
                self.record_com_call(
                    "ModelDocExtension.SelectByID2",
                    {"index": index, "selection_type": selection_type, "x": x, "y": y, "z": z},
                    result=selected,
                    started_at=started_at,
                )
                if selected:
                    return True
            except Exception as exc:
                self.record_com_call(
                    "ModelDocExtension.SelectByID2",
                    {"index": index, "selection_type": selection_type, "x": x, "y": y, "z": z},
                    error=exc,
                    started_at=started_at,
                )
        return False

    def _hole_edge_pick_position(self, view: Any, plan: ModelPlan, index: int) -> tuple[float, float, float]:
        """Return the sheet coordinate for a modeled hole center."""

        view_x, view_y = _drawing_view_position(view)
        scale = _drawing_view_scale(view)
        if index < len(self._last_hole_points):
            point = self._last_hole_points[index]
            return (
                view_x + (_to_meters(point[0], plan.units) * scale),
                view_y + (_to_meters(point[1], plan.units) * scale),
                0,
            )
        return view_x, view_y, 0

    def _try_insert_model_hole_callouts(self, drawing: Any, view: Any, view_name: Any) -> dict[str, Any]:
        """Fallback: ask SolidWorks to import model hole callout annotations."""

        result: dict[str, Any] = {
            "method": "InsertModelAnnotations3",
            "created_callout_count": 0,
            "attempts": [],
        }
        method = _get_com_member(drawing, "InsertModelAnnotations3")
        if not callable(method):
            result["failure_reason"] = "DrawingDoc.InsertModelAnnotations3 is not available"
            return result

        if view_name:
            activate_view = _get_com_member(drawing, "ActivateView")
            if callable(activate_view):
                started_at = perf_counter()
                try:
                    activated = activate_view(str(view_name))
                    self.record_com_call(
                        "DrawingDoc.ActivateView",
                        {"view_name": str(view_name)},
                        result=activated,
                        started_at=started_at,
                    )
                except Exception as exc:
                    self.record_com_call(
                        "DrawingDoc.ActivateView",
                        {"view_name": str(view_name)},
                        error=exc,
                        started_at=started_at,
                    )

        before_count = self._annotation_count(view)
        for all_views in (False, True):
            attempt = {"all_views": all_views}
            started_at = perf_counter()
            try:
                annotations = method(0, SW_INSERT_HOLE_CALLOUT, all_views, False, False, True)
                self.record_com_call(
                    "DrawingDoc.InsertModelAnnotations3",
                    {"types": SW_INSERT_HOLE_CALLOUT, "all_views": all_views},
                    result=annotations,
                    started_at=started_at,
                )
            except Exception as exc:
                self.record_com_call(
                    "DrawingDoc.InsertModelAnnotations3",
                    {"types": SW_INSERT_HOLE_CALLOUT, "all_views": all_views},
                    error=exc,
                    started_at=started_at,
                )
                attempt["failure_reason"] = str(exc)
                result["attempts"].append(attempt)
                continue

            after_count = self._annotation_count(view)
            annotation_count = len(_as_sequence(annotations))
            count_changed = before_count is not None and after_count is not None and after_count > before_count
            created = annotation_count if annotation_count else (1 if count_changed else 0)
            attempt.update(
                {
                    "returned_annotation_count": annotation_count,
                    "annotation_count_before": before_count,
                    "annotation_count_after": after_count,
                    "callout_created": created > 0,
                }
            )
            result["attempts"].append(attempt)
            if created > 0:
                result["created_callout_count"] = created
                return result
        result["failure_reason"] = "InsertModelAnnotations3 did not return or add hole callout annotations"
        return result

    def _require_sw(self) -> Any:
        """Return the connected SolidWorks application object."""

        if self._sw is None:
            self.connect()
        return self._sw

    def _require_model(self) -> Any:
        """Return the active part document."""

        if self._model is None:
            raise RuntimeError("No active part document. Call begin_transaction first.")
        return self._model

    def _require_workspace(self) -> Path:
        """Return the active transaction output directory."""

        if self._workspace is None:
            raise RuntimeError("No active workspace. Call begin_transaction first.")
        return self._workspace

    def _active_title(self) -> str | None:
        """Return the current model title when a model is active."""

        if self._model is None:
            return None
        return self._document_title(self._model)

    def _document_title(self, document: Any | None) -> str | None:
        """Read a document title from a tracked SolidWorks document object."""

        if document is None:
            return None
        for attribute in ("GetTitle", "Title"):
            try:
                value = getattr(document, attribute, None)
                value = value() if callable(value) else value
                if value:
                    return str(value)
            except Exception:
                continue
        return None

    def _document_path(self, document: Any | None) -> str | None:
        """Read a document path from a tracked SolidWorks document object."""

        if document is None:
            return None
        for attribute in ("GetPathName", "PathName"):
            try:
                value = getattr(document, attribute, None)
                value = value() if callable(value) else value
                if value:
                    return str(value)
            except Exception:
                continue
        return None

    def _open_document_summaries(self) -> dict[str, Any]:
        """Enumerate open SolidWorks documents with conservative fallbacks."""

        attempts: list[dict[str, Any]] = []
        documents = self._documents_from_get_documents(attempts)
        method = "SldWorks.GetDocuments"
        if documents is None:
            documents = self._documents_from_first_document(attempts)
            method = "SldWorks.GetFirstDocument"
        if documents is None:
            return {
                "ok": False,
                "status": "unavailable",
                "attempts": attempts,
                "documents": [],
                "failure_reason": "Could not enumerate open SolidWorks documents.",
            }
        summaries = [self._document_summary(document, index) for index, document in enumerate(documents)]
        return {
            "ok": True,
            "status": "enumerated",
            "method": method,
            "attempts": attempts,
            "documents": summaries,
            "document_count": len(summaries),
        }

    def _documents_from_get_documents(self, attempts: list[dict[str, Any]]) -> list[Any] | None:
        """Try SldWorks.GetDocuments when available."""

        if self._sw is None:
            attempts.append({"method": "SldWorks.GetDocuments", "ok": False, "error": "No SolidWorks application"})
            return None
        method = getattr(self._sw, "GetDocuments", None)
        if not callable(method):
            attempts.append({"method": "SldWorks.GetDocuments", "ok": False, "error": "Method unavailable"})
            return None
        started_at = perf_counter()
        try:
            raw_documents = method()
            self.record_com_call(
                "SldWorks.GetDocuments",
                {"purpose": "document_state_audit"},
                result=raw_documents,
                started_at=started_at,
            )
            documents = [] if raw_documents is None or raw_documents is False else _as_sequence(raw_documents)
            attempts.append({"method": "SldWorks.GetDocuments", "ok": True, "count": len(documents)})
            return documents
        except Exception as exc:
            self.record_com_call(
                "SldWorks.GetDocuments",
                {"purpose": "document_state_audit"},
                error=exc,
                started_at=started_at,
            )
            attempts.append({"method": "SldWorks.GetDocuments", "ok": False, "error": str(exc)})
            return None

    def _documents_from_first_document(self, attempts: list[dict[str, Any]]) -> list[Any] | None:
        """Try walking the ModelDoc2 linked list from SldWorks.GetFirstDocument."""

        if self._sw is None:
            attempts.append({"method": "SldWorks.GetFirstDocument", "ok": False, "error": "No SolidWorks application"})
            return None
        method = getattr(self._sw, "GetFirstDocument", None)
        if not callable(method):
            attempts.append({"method": "SldWorks.GetFirstDocument", "ok": False, "error": "Method unavailable"})
            return None
        started_at = perf_counter()
        try:
            document = method()
            self.record_com_call(
                "SldWorks.GetFirstDocument",
                {"purpose": "document_state_audit"},
                result=document,
                started_at=started_at,
            )
        except Exception as exc:
            self.record_com_call(
                "SldWorks.GetFirstDocument",
                {"purpose": "document_state_audit"},
                error=exc,
                started_at=started_at,
            )
            attempts.append({"method": "SldWorks.GetFirstDocument", "ok": False, "error": str(exc)})
            return None

        documents: list[Any] = []
        seen: set[int] = set()
        while document is not None and document is not False and len(documents) < 200:
            identity = id(document)
            if identity in seen:
                attempts.append({"method": "ModelDoc2.GetNext", "ok": False, "error": "Cycle detected"})
                break
            seen.add(identity)
            documents.append(document)
            next_document = None
            next_error = None
            for method_name in ("GetNext", "IGetNext"):
                next_method = getattr(document, method_name, None)
                if not callable(next_method):
                    continue
                try:
                    next_document = next_method()
                    next_error = None
                    break
                except Exception as exc:
                    next_error = str(exc)
            if next_error:
                attempts.append({"method": "ModelDoc2.GetNext", "ok": False, "error": next_error})
                break
            if next_document is None or next_document is False:
                break
            document = next_document
        attempts.append({"method": "SldWorks.GetFirstDocument", "ok": True, "count": len(documents)})
        return documents

    def _document_summary(self, document: Any, index: int) -> dict[str, Any]:
        """Return a sanitized summary for one open document."""

        path = self._document_path(document)
        return {
            "index": index,
            "title": self._document_title(document),
            "path": path,
            "document_type": self._document_type(document),
            "is_run_created": self._is_path_in_run_workspace(path),
        }

    def _document_type(self, document: Any | None) -> int | None:
        """Read a SolidWorks document type when available."""

        if document is None:
            return None
        for attribute in ("GetType", "Type"):
            try:
                value = getattr(document, attribute, None)
                value = value() if callable(value) else value
                if value is not None:
                    return int(value)
            except Exception:
                continue
        return None

    def _tracked_document_state_checks(self) -> list[dict[str, Any]]:
        """Check tracked run-created document names without closing them."""

        checks: list[dict[str, Any]] = []
        for kind, document, title, path in (
            ("drawing", self._drawing, self._active_drawing_title, self._active_drawing_path),
            ("part", self._model, self._active_part_title, self._active_part_path),
        ):
            for candidate in self._cleanup_candidates(document, title, path):
                resolution = self._resolve_run_created_document(candidate["name"])
                resolution.pop("document", None)
                checks.append(
                    {
                        "kind": kind,
                        "candidate": candidate["name"],
                        "source": candidate["source"],
                        "requires_path_match": candidate["requires_path_match"],
                        "status": resolution.get("status"),
                        "is_run_created": resolution.get("is_run_created"),
                        "document_title": resolution.get("document_title"),
                        "document_path": resolution.get("document_path"),
                        "failure_reason": resolution.get("failure_reason"),
                    }
                )
        return checks

    def _is_path_in_run_workspace(self, path: str | None) -> bool:
        """Return whether a document path belongs to the active run workspace."""

        if not path or self._workspace is None:
            return False
        try:
            resolved_path = Path(path).resolve()
            workspace = self._workspace.resolve()
            return resolved_path == workspace or workspace in resolved_path.parents
        except Exception:
            return False

    def _cleanup_candidates(
        self,
        document: Any | None,
        title: str | None,
        path: Path | None,
    ) -> list[dict[str, Any]]:
        """Return close candidates, marking file-name fallbacks as path-guarded."""

        candidates: list[dict[str, Any]] = []
        for source, name, requires_path_match in (
            ("tracked_title", title, False),
            ("document_title", self._document_title(document), False),
            ("path_name", path.name if path else None, True),
            ("path_stem", path.stem if path else None, True),
        ):
            if not name:
                continue
            text = str(name).strip()
            if not text:
                continue
            if any(candidate["name"] == text for candidate in candidates):
                continue
            candidates.append(
                {
                    "name": text,
                    "source": source,
                    "requires_path_match": requires_path_match,
                }
            )
        return candidates

    def _resolve_run_created_document(self, document_name: str) -> dict[str, Any]:
        """Resolve a document name and verify it belongs to this run workspace."""

        result: dict[str, Any] = {
            "document_name": document_name,
            "status": "not_checked",
            "is_run_created": False,
        }
        if self._sw is None:
            result["status"] = "skipped_no_connection"
            return result
        if self._workspace is None:
            result["status"] = "skipped_no_workspace"
            result["failure_reason"] = "No active run workspace is available for path-guarded cleanup."
            return result
        if self._solidworks_rpc_unavailable:
            result["status"] = "solidworks_rpc_unavailable"
            result["failure_reason"] = self._solidworks_rpc_unavailable
            return result

        method = getattr(self._sw, "GetOpenDocumentByName", None)
        if not callable(method):
            result["status"] = "unavailable"
            result["failure_reason"] = "SldWorks.GetOpenDocumentByName is not available."
            return result

        started_at = perf_counter()
        try:
            document = method(document_name)
            self.record_com_call(
                "SldWorks.GetOpenDocumentByName",
                {"document_name": document_name, "purpose": "cleanup_path_guard"},
                result=document,
                started_at=started_at,
            )
        except Exception as exc:
            result["status"] = "error"
            result["failure_reason"] = str(exc)
            self.record_com_call(
                "SldWorks.GetOpenDocumentByName",
                {"document_name": document_name, "purpose": "cleanup_path_guard"},
                error=exc,
                started_at=started_at,
            )
            return result

        if document is None or document is False:
            result["status"] = "not_open"
            result["failure_reason"] = "No open document matched this cleanup candidate."
            return result

        document_path = self._document_path(document)
        result["document_path"] = document_path
        result["document_title"] = self._document_title(document)
        if not document_path:
            result["status"] = "path_unavailable"
            result["failure_reason"] = "Open document has no path, so cleanup will not close it by file-name fallback."
            return result

        try:
            resolved_path = Path(document_path).resolve()
            workspace = self._workspace.resolve()
            result["is_run_created"] = resolved_path == workspace or workspace in resolved_path.parents
            result["status"] = "run_created" if result["is_run_created"] else "outside_workspace"
        except Exception as exc:
            result["status"] = "path_error"
            result["failure_reason"] = str(exc)
        if result["is_run_created"]:
            result["document"] = document
        elif "failure_reason" not in result:
            result["failure_reason"] = "Open document path is outside the current run workspace."
        return result

    def _verify_document_closed(self, document_name: str) -> dict[str, Any]:
        """Check whether a document name is still open after a cleanup attempt."""

        result: dict[str, Any] = {
            "document_name": document_name,
            "status": "not_checked",
            "verified_closed": None,
        }
        if self._sw is None:
            result["status"] = "skipped_no_connection"
            return result
        if self._solidworks_rpc_unavailable:
            result["status"] = "solidworks_rpc_unavailable"
            result["failure_reason"] = self._solidworks_rpc_unavailable
            return result

        method = getattr(self._sw, "GetOpenDocumentByName", None)
        if not callable(method):
            result["status"] = "unavailable"
            result["failure_reason"] = "SldWorks.GetOpenDocumentByName is not available."
            return result

        started_at = perf_counter()
        try:
            open_document = method(document_name)
            self.record_com_call(
                "SldWorks.GetOpenDocumentByName",
                {"document_name": document_name, "purpose": "cleanup_verification"},
                result=open_document,
                started_at=started_at,
            )
        except Exception as exc:
            result["status"] = "error"
            result["failure_reason"] = str(exc)
            self.record_com_call(
                "SldWorks.GetOpenDocumentByName",
                {"document_name": document_name, "purpose": "cleanup_verification"},
                error=exc,
                started_at=started_at,
            )
            return result

        is_open = open_document is not None and open_document is not False
        result["verified_closed"] = not is_open
        result["status"] = "still_open" if is_open else "closed"
        if is_open:
            result["open_document_title"] = self._document_title(open_document)
        return result

    def _clear_document_handles(self) -> None:
        """Clear COM document handles after cleanup attempts."""

        self._drawing = None
        self._model = None
        self._drawing_view_handles = {}

    def _activate_part_document(self) -> None:
        """Best-effort activation of the saved part before model preview capture."""

        if self._sw is None or self._active_part_path is None:
            return

        import pythoncom
        import win32com.client

        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        for document_name in (self._active_part_path.name, self._active_part_path.stem):
            started_at = perf_counter()
            try:
                result = self._sw.ActivateDoc3(document_name, False, 0, errors)
                self.record_com_call(
                    "SldWorks.ActivateDoc3",
                    {"document_name": document_name},
                    result=result,
                    started_at=started_at,
                )
                if result is not None:
                    return
            except Exception as exc:
                self.record_com_call(
                    "SldWorks.ActivateDoc3",
                    {"document_name": document_name},
                    error=exc,
                    started_at=started_at,
                )
        self._warnings.append(f"activate_part_document_failed:{self._active_part_path.name}")

    def _activate_drawing_document(self) -> None:
        """Best-effort activation of the saved drawing before drawing export."""

        if self._sw is None or self._active_drawing_path is None:
            return

        import pythoncom
        import win32com.client

        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        for document_name in (self._active_drawing_path.name, self._active_drawing_path.stem):
            started_at = perf_counter()
            try:
                result = self._sw.ActivateDoc3(document_name, False, 0, errors)
                self.record_com_call(
                    "SldWorks.ActivateDoc3",
                    {"document_name": document_name},
                    result=result,
                    started_at=started_at,
                )
                if result is not None:
                    return
            except Exception as exc:
                self.record_com_call(
                    "SldWorks.ActivateDoc3",
                    {"document_name": document_name},
                    error=exc,
                    started_at=started_at,
                )
        self._warnings.append(f"activate_drawing_document_failed:{self._active_drawing_path.name}")


def _view_name_for_role(view_result: dict[str, Any], role: str) -> str | None:
    """Return the recorded drawing view name for a semantic role."""

    for view in view_result.get("views", []):
        if view.get("role") == role:
            return str(view.get("name"))
    return None


def _drawing_view_name(view: Any) -> str | None:
    """Read a drawing view name when exposed by COM."""

    for attribute in ("Name", "GetName2", "GetName"):
        try:
            value = getattr(view, attribute, None)
            value = value() if callable(value) else value
            if value:
                return str(value)
        except Exception:
            continue
    return None


def _unique_strings(values: list[Any]) -> list[str]:
    """Return non-empty unique strings while preserving order."""

    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        unique.append(text)
        seen.add(text)
    return unique


def _safe_solidworks_atomic_name(value: str) -> str:
    """Return a stable SolidWorks feature/sketch name for an atomic graph id."""

    clean = "".join(character if character.isalnum() or character in {"_", "-"} else "_" for character in str(value))
    clean = clean.strip("_-") or "atomic_ref"
    return f"swmcp_{clean}"[:80]


def _sketch_name_candidates(index: int) -> tuple[str, ...]:
    """Return localized SolidWorks default sketch names for a creation index."""

    return (f"Sketch{index}", f"草图{index}")


def _first_nonempty_string(values: list[Any]) -> str | None:
    """Return the first non-empty string from COM byref output values."""

    for value in values:
        if value in {None, False}:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _as_sequence(value: Any) -> list[Any]:
    """Normalize COM SAFEARRAY-ish values into a Python list."""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return [value]


def _edge_looks_circular(edge: Any) -> bool:
    """Best-effort check that an edge is backed by a circular curve."""

    for attribute in ("GetCurveParams3", "GetCurveParams2"):
        params = _call_or_get(edge, attribute)
        if _curve_params_look_circular(params):
            return True

    curve = _edge_curve(edge)
    if curve is None:
        return False

    if _curve_looks_circular(curve):
        return True

    return False


def _edge_curve(edge: Any) -> Any:
    """Return the curve object behind an edge when SolidWorks exposes one."""

    try:
        curve_getter = getattr(edge, "GetCurve", None)
        return curve_getter() if callable(curve_getter) else None
    except Exception:
        return None


def _curve_looks_circular(curve: Any) -> bool:
    """Return whether a SolidWorks curve object reports circle-like geometry."""

    for attribute in ("IsCircle", "IsCircular"):
        try:
            value = getattr(curve, attribute, None)
            value = value() if callable(value) else value
            if _com_bool(value):
                return True
        except Exception:
            continue

    for attribute in ("CircleParams", "GetCircleParams", "GetCircleParams2"):
        try:
            value = getattr(curve, attribute, None)
            value = value() if callable(value) else value
            if len(_numeric_sequence(value)) >= 6:
                return True
        except Exception:
            continue

    identity = _call_or_get(curve, "Identity")
    if _safe_int(identity) == SW_CURVE_TYPE_CIRCLE:
        return True
    if _safe_int(identity) == SW_CURVE_TYPE_TRIMMED:
        base_curve = _call_or_get(curve, "GetBaseCurve") or _call_or_get(curve, "BaseCurve")
        if base_curve is not None and _curve_looks_circular(base_curve):
            return True
    return False


def _curve_params_look_circular(params: Any) -> bool:
    """Detect circle/arc edge parameters from SolidWorks edge parameter arrays."""

    values = _numeric_sequence(params)
    if len(values) < 8:
        return False

    curve_type = _safe_int(values[0])
    if curve_type == SW_CURVE_TYPE_CIRCLE:
        return True

    start = values[:3]
    end = values[3:6]
    if _points_close(start, end) and _angle_span_is_circle(values[6], values[7]):
        return True

    bool_flags = [_safe_int(value) for value in values[-4:]]
    return bool_flags[0] == SW_CURVE_TYPE_CIRCLE or SW_CURVE_TYPE_CIRCLE in bool_flags


def _edge_curve_probe(edge: Any) -> dict[str, Any]:
    """Summarize one visible drawing edge's curve shape for failure reports."""

    probe: dict[str, Any] = {
        "edge_type": type(edge).__name__,
        "looks_circular": False,
        "curve": None,
        "edge_params": {},
    }
    curve = _edge_curve(edge)
    if curve is not None:
        curve_probe: dict[str, Any] = {
            "type": type(curve).__name__,
            "identity": _safe_int(_call_or_get(curve, "Identity")),
            "is_circle": _com_bool(_call_or_get(curve, "IsCircle")),
            "is_circular": _com_bool(_call_or_get(curve, "IsCircular")),
            "circle_param_count": len(_numeric_sequence(_call_or_get(curve, "CircleParams"))),
            "get_circle_param_count": len(_numeric_sequence(_call_or_get(curve, "GetCircleParams"))),
        }
        base_curve = _call_or_get(curve, "GetBaseCurve") or _call_or_get(curve, "BaseCurve")
        if base_curve is not None:
            curve_probe["base_curve"] = {
                "type": type(base_curve).__name__,
                "identity": _safe_int(_call_or_get(base_curve, "Identity")),
                "is_circle": _com_bool(_call_or_get(base_curve, "IsCircle")),
                "circle_param_count": len(_numeric_sequence(_call_or_get(base_curve, "CircleParams"))),
            }
        probe["curve"] = curve_probe

    for attribute in ("GetCurveParams3", "GetCurveParams2"):
        raw_params = _call_or_get(edge, attribute)
        values = _numeric_sequence(raw_params)
        probe["edge_params"][attribute] = {
            "available": raw_params is not None,
            "value_type": type(raw_params).__name__ if raw_params is not None else None,
            "numeric_count": len(values),
            "first_values": values[:10],
            "looks_circular": _curve_params_look_circular(raw_params),
        }

    probe["looks_circular"] = _edge_looks_circular(edge)
    return probe


def _best_mounting_plate_radius_edge(edges: list[Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    """Find the MVP mounting-plate corner radius edge from drawing-view edges."""

    selector_data = spec.get("edge_selector_data", {})
    center = selector_data.get("center", {}) if isinstance(selector_data, dict) else {}
    try:
        expected_center = (float(center["x"]), float(center["y"]), float(center["z"]))
        expected_radius = float(selector_data["radius_m"])
    except (KeyError, TypeError, ValueError):
        return None

    best: dict[str, Any] | None = None
    best_score = float("inf")
    for edge in edges:
        params = _edge_curve_params(edge)
        if not _curve_params_look_circular(params):
            arc = _arc_summary_from_curve_params(params)
            if arc is None:
                continue
        else:
            arc = _arc_summary_from_curve_params(params)
            if arc is None:
                continue
        if not arc.get("is_arc"):
            continue
        center_error = _point_distance(arc["center"], expected_center)
        radius_error = abs(float(arc["radius"]) - expected_radius)
        score = center_error + radius_error * 4
        if score < best_score:
            best_score = score
            best = {
                "edge": edge,
                "summary": {
                    "center": list(arc["center"]),
                    "radius": arc["radius"],
                    "start_angle": arc["start_angle"],
                    "end_angle": arc["end_angle"],
                    "center_error": center_error,
                    "radius_error": radius_error,
                    "score": score,
                },
            }

    tolerance = max(expected_radius * 0.35, 0.001)
    if best is not None and best_score <= tolerance:
        return best
    return None


def _mounting_plate_hole_edge_offset_edges(edges: list[Any], spec: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Find the left outside edge and first hole edge for the MVP offset dimension."""

    selector_data = spec.get("edge_selector_data", {})
    try:
        plate_left_x = float(selector_data["plate_left_x"])
        hole_center_data = selector_data["hole_center"]
        hole_center = (
            float(hole_center_data["x"]),
            float(hole_center_data["y"]),
            float(hole_center_data["z"]),
        )
        hole_radius = float(selector_data["hole_radius_m"])
    except (KeyError, TypeError, ValueError):
        return None

    outer_edge = _best_mounting_plate_vertical_edge(edges, plate_left_x)
    hole_edge = _best_mounting_plate_hole_edge(edges, hole_center, hole_radius)
    if outer_edge is None or hole_edge is None:
        return None
    return [outer_edge, hole_edge]


def _best_mounting_plate_vertical_edge(edges: list[Any], expected_x: float) -> dict[str, Any] | None:
    """Select the vertical outside edge nearest the expected x coordinate."""

    best: dict[str, Any] | None = None
    best_score = float("inf")
    for edge in edges:
        line = _line_summary_from_curve_params(_edge_curve_params(edge))
        if line is None:
            continue
        dx = abs(line["end"][0] - line["start"][0])
        dy = abs(line["end"][1] - line["start"][1])
        if dy <= dx:
            continue
        x_mid = (line["start"][0] + line["end"][0]) / 2
        score = abs(x_mid - expected_x)
        if score < best_score:
            best_score = score
            best = {
                "edge": edge,
                "summary": {
                    "role": "outer_edge",
                    "start": list(line["start"]),
                    "end": list(line["end"]),
                    "x_mid": x_mid,
                    "x_error": score,
                },
            }
    if best is not None and best_score <= 0.002:
        return best
    return None


def _best_mounting_plate_hole_edge(
    edges: list[Any],
    expected_center: tuple[float, float, float],
    expected_radius: float,
) -> dict[str, Any] | None:
    """Select the circular hole edge nearest the expected center and radius."""

    best: dict[str, Any] | None = None
    best_score = float("inf")
    for edge in edges:
        arc = _arc_summary_from_curve_params(_edge_curve_params(edge))
        if arc is None or arc.get("is_arc"):
            continue
        center_error = _point_distance(arc["center"], expected_center)
        radius_error = abs(float(arc["radius"]) - expected_radius) if arc["radius"] else 0.0
        score = center_error + radius_error * 4
        if score < best_score:
            best_score = score
            best = {
                "edge": edge,
                "summary": {
                    "role": "hole_edge",
                    "center": list(arc["center"]),
                    "radius": arc["radius"],
                    "center_error": center_error,
                    "radius_error": radius_error,
                    "score": score,
                },
            }
    tolerance = max(expected_radius * 1.6, 0.003)
    if best is not None and best_score <= tolerance:
        return best
    return None


def _best_center_hole_flange_circle_edge(edges: list[Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    """Select the center-hole flange circle edge nearest the requested radius."""

    selector_data = spec.get("edge_selector_data", {})
    try:
        expected_radius = float(selector_data["expected_radius_m"])
    except (KeyError, TypeError, ValueError):
        return None
    allow_unknown_radius = bool(selector_data.get("allow_unknown_radius")) if isinstance(selector_data, dict) else False
    expected_point: tuple[float, float, float] | None = None
    raw_expected_point = selector_data.get("expected_point_m") if isinstance(selector_data, dict) else None
    if isinstance(raw_expected_point, (list, tuple)) and len(raw_expected_point) >= 3:
        try:
            expected_point = (
                float(raw_expected_point[0]),
                float(raw_expected_point[1]),
                float(raw_expected_point[2]),
            )
        except (TypeError, ValueError):
            expected_point = None

    best: dict[str, Any] | None = None
    best_score = float("inf")
    for edge in edges:
        arc = _arc_summary_from_curve_params(_edge_curve_params(edge))
        if arc is None or arc.get("is_arc"):
            continue
        radius = float(arc.get("radius") or 0.0)
        point_error = _point_distance(arc["center"], expected_point) if expected_point is not None else None
        if radius > 0:
            score = abs(radius - expected_radius)
        elif point_error is not None:
            score = point_error
        elif allow_unknown_radius:
            score = 0.0
        else:
            continue
        if score < best_score:
            best_score = score
            best = {
                "edge": edge,
                "summary": {
                    "role": str(selector_data.get("role", "diameter")),
                    "center": list(arc["center"]),
                    "radius": radius,
                    "expected_radius": expected_radius,
                    "radius_error": score,
                    "expected_point": list(expected_point) if expected_point is not None else None,
                    "point_error": point_error,
                },
            }
    tolerance = (
        float("inf")
        if allow_unknown_radius and best is not None and float(best["summary"].get("radius") or 0.0) <= 0
        else max(expected_radius * 0.08, 0.001)
        if expected_point is None
        else max(expected_radius * 1.2, 0.003)
    )
    if best is not None and best_score <= tolerance:
        return best
    return None


def _best_line_edge_for_length(edges: list[Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    """Select the visible straight edge that best matches a requested line length."""

    selector_data = spec.get("edge_selector_data", {})
    try:
        expected_length = float(selector_data["expected_length_m"])
    except (KeyError, TypeError, ValueError):
        return None
    orientation = str(selector_data.get("orientation", "")).lower()
    if orientation not in {"horizontal", "vertical"}:
        return None

    best: dict[str, Any] | None = None
    best_score = float("inf")
    for edge in edges:
        line = _line_summary_from_curve_params(_edge_curve_params(edge))
        if line is None:
            continue
        start = line["start"]
        end = line["end"]
        dx = abs(end[0] - start[0])
        dy = abs(end[1] - start[1])
        dz = abs(end[2] - start[2])
        if orientation == "horizontal":
            axis_length = dx
            off_axis = dy + dz
        else:
            axis_length = dy
            off_axis = dx + dz
        if axis_length <= 0 or off_axis > max(axis_length * 0.08, 0.001):
            continue
        length_error = abs(axis_length - expected_length)
        score = length_error + off_axis
        if score < best_score:
            best_score = score
            best = {
                "edge": edge,
                "summary": {
                    "role": str(selector_data.get("role", "line_length")),
                    "orientation": orientation,
                    "start": list(start),
                    "end": list(end),
                    "axis_length": axis_length,
                    "expected_length": expected_length,
                    "length_error": length_error,
                    "off_axis": off_axis,
                    "score": score,
                },
            }
    tolerance = max(expected_length * 0.08, 0.001)
    if best is not None and best_score <= tolerance:
        return best
    return None


def _edge_curve_params(edge: Any) -> Any:
    """Return the first available SolidWorks edge curve-parameter tuple."""

    for attribute in ("GetCurveParams3", "GetCurveParams2"):
        params = _call_or_get(edge, attribute)
        if params is not None:
            return params
    return None


def _arc_summary_from_curve_params(params: Any) -> dict[str, Any] | None:
    """Infer a compact arc/circle summary from SolidWorks edge params."""

    values = _numeric_sequence(params)
    if len(values) < 8:
        return None
    start = tuple(values[:3])
    end = tuple(values[3:6])
    start_angle = float(values[6])
    end_angle = float(values[7])
    is_full_circle = _points_close(list(start), list(end)) and _angle_span_is_circle(start_angle, end_angle)
    is_arc = not is_full_circle
    if is_arc:
        # In SW edge params for planar circular arcs, values 8..9 are not stable
        # through pywin32.  Reconstruct the center from endpoint tangency for
        # quarter arcs when the interval spans 90 degrees.
        span = abs(end_angle - start_angle)
        radius = _point_distance(start, end) / 2**0.5 if abs(span - 1.5707963267948966) <= 1e-6 else 0.0
        center = _quarter_arc_center_from_endpoints(start, end, start_angle, end_angle, radius)
    else:
        radius = _full_circle_radius_from_curve_params(values)
        center = start
    if center is None:
        return None
    return {
        "center": center,
        "radius": radius,
        "start_angle": start_angle,
        "end_angle": end_angle,
        "is_arc": is_arc,
        "is_full_circle": is_full_circle,
    }


def _line_summary_from_curve_params(params: Any) -> dict[str, tuple[float, float, float]] | None:
    """Infer a compact straight-line summary from SolidWorks edge params."""

    values = _numeric_sequence(params)
    if len(values) < 6:
        return None
    start = tuple(values[:3])
    end = tuple(values[3:6])
    if _points_close(list(start), list(end)):
        return None
    if len(values) >= 8 and _curve_params_look_circular(values):
        return None
    return {"start": start, "end": end}


def _full_circle_radius_from_curve_params(values: list[float]) -> float:
    """Best-effort radius readback from SolidWorks full-circle edge params."""

    if len(values) >= 9:
        candidate = abs(float(values[8]))
        if 1e-6 <= candidate <= 1.0:
            return candidate
    return 0.0


def _quarter_arc_center_from_endpoints(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    start_angle: float,
    end_angle: float,
    radius: float,
) -> tuple[float, float, float] | None:
    """Reconstruct the center for a planar quarter-circle arc."""

    if radius <= 0:
        return None
    candidates = (
        (start[0], end[1], start[2]),
        (end[0], start[1], start[2]),
    )
    return min(candidates, key=lambda item: abs(_point_distance(item, start) - radius) + abs(_point_distance(item, end) - radius))


def _point_distance(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    """Return Euclidean distance between two points."""

    length = min(len(first), len(second))
    return sum((float(first[index]) - float(second[index])) ** 2 for index in range(length)) ** 0.5


def _active_configuration_name(model: Any) -> str:
    """Return the active configuration name for material/property calls."""

    candidates = _configuration_name_candidates(model)
    return candidates[0] if candidates else ""


def _configuration_name_candidates(model: Any) -> list[str]:
    """Return SolidWorks configuration names worth trying for material APIs."""

    candidates: list[Any] = []
    try:
        configuration = _call_or_get(model, "ConfigurationManager")
        active_configuration = _call_or_get(configuration, "ActiveConfiguration") if configuration is not None else None
        name = _call_or_get(active_configuration, "Name") if active_configuration is not None else None
    except Exception:
        name = None
    candidates.append(name)

    for method_name in ("GetActiveConfiguration",):
        active_configuration = _call_or_get(model, method_name)
        candidates.append(_call_or_get(active_configuration, "Name") if active_configuration is not None else None)

    for method_name in ("GetConfigurationNames",):
        names = _call_or_get(model, method_name)
        candidates.extend(_as_sequence(names))

    return _unique_strings(candidates) + [""]


def _part_doc_dispatch(model: Any) -> Any:
    """Return a pywin32 PartDoc dispatch when material APIs need the part interface."""

    try:
        import win32com.client

        return win32com.client.CastTo(model, "PartDoc")
    except Exception:
        return model


def _model_doc_extension_dispatch(model: Any) -> Any:
    """Return the ModelDocExtension dispatch for custom-property APIs."""

    try:
        extension = getattr(model, "Extension", None)
        if extension is not None:
            return extension
    except Exception:
        pass
    try:
        import win32com.client

        cast_model = win32com.client.CastTo(model, "ModelDoc2")
        extension = getattr(cast_model, "Extension", None)
        return extension
    except Exception:
        return None


def _dispatch_variant_or_none(value: Any) -> Any:
    """Wrap a COM object as VT_DISPATCH when pywin32 needs explicit marshaling."""

    try:
        import pythoncom
        import win32com.client

        return win32com.client.VARIANT(pythoncom.VT_DISPATCH, value)
    except Exception:
        return None


def _material_names_match(current: str | None, requested: str) -> bool:
    """Return whether a SolidWorks material readback matches the requested name."""

    if not current:
        return False
    current_name = current.strip().lower()
    requested_name = requested.strip().lower()
    return (
        current_name == requested_name
        or current_name.endswith(f"\\{requested_name}")
        or current_name.endswith(f"/{requested_name}")
    )


def _material_name_candidates(material: str) -> list[str]:
    """Return controlled material-name aliases worth trying in local SW installs."""

    material_name = material.strip()
    candidates: list[Any] = [material_name]
    candidates.extend(MATERIAL_ALIASES.get(material_name.lower(), []))
    return _unique_strings(candidates)


def _custom_properties_from_plan(plan: ModelPlan) -> dict[str, str]:
    """Return the final custom-property map requested by a plan."""

    properties: dict[str, str] = {}
    for operation in plan.operations:
        if operation.op == "set_custom_properties":
            properties = {
                str(key).strip(): str(value)
                for key, value in operation.parameters.get("properties", {}).items()
            }
    return properties


def _metadata_note_text(properties: dict[str, str]) -> str:
    """Return a compact drawing note that exports reliably to PDF text."""

    preferred = ["PartNo", "Revision", "Description", "Material"]
    ordered_keys = [key for key in preferred if key in properties]
    ordered_keys.extend(sorted(key for key in properties if key not in ordered_keys))
    return "\n".join(f"{key}: {properties[key]}" for key in ordered_keys)


def _material_database_candidates(configured_database: Any) -> list[str]:
    """Return material database candidates for SolidWorks material assignment."""

    candidates: list[Any] = [
        configured_database,
        "solidworks materials.sldmat",
        "solidworks materials",
        "SolidWorks Materials",
        "SOLIDWORKS Materials",
        "",
    ]
    candidates.extend(
        [
            "SolidWorks DIN Materials",
            "SolidWorks DIN Materials.sldmat",
        ]
    )
    candidates.extend(_existing_sldmat_candidates())
    return _unique_strings(candidates)


def _is_solidworks_rpc_failure(error: Exception | str) -> bool:
    """Return whether a COM error means the SolidWorks RPC server is gone."""

    text = str(error)
    return (
        "-2147023170" in text
        or "-2147023174" in text
        or "RPC server is unavailable" in text
        or "RPC 服务器不可用" in text
        or "远程过程调用失败" in text
    )


def _existing_sldmat_candidates() -> list[str]:
    """Return common installed SolidWorks material database files."""

    roots = [
        Path("D:/Program Files/SOLIDWORKS Corp/SOLIDWORKS/lang/english/sldmaterials"),
        Path("D:/Program Files/SOLIDWORKS Corp/SOLIDWORKS/lang/chinese-simplified/sldmaterials"),
        Path("C:/Program Files/SOLIDWORKS Corp/SOLIDWORKS/lang/english/sldmaterials"),
        Path("C:/Program Files/SOLIDWORKS Corp/SOLIDWORKS/lang/chinese-simplified/sldmaterials"),
    ]
    names = [
        "solidworks materials.sldmat",
        "SolidWorks DIN Materials.sldmat",
        "sustainability extras.sldmat",
    ]
    return [path_to_string(root / name) for root in roots for name in names if (root / name).exists()]


def _extract_com_edges_from_polyline_result(value: Any) -> list[Any]:
    """Extract SolidWorks edge COM objects from GetPolylines6 return values."""

    edges: list[Any] = []
    seen: set[int] = set()
    _collect_com_edges(value, edges, seen, depth=0)
    return edges


def _collect_com_edges(value: Any, edges: list[Any], seen: set[int], depth: int) -> None:
    """Recursively collect edge-like COM dispatches from shallow containers."""

    if value is None or depth > 4 or len(edges) >= 256:
        return
    if isinstance(value, (str, bytes, bytearray, memoryview)):
        return

    value_id = id(value)
    if value_id in seen:
        return
    seen.add(value_id)

    if _looks_like_edge_dispatch(value):
        edges.append(value)
        return

    if isinstance(value, dict):
        for item in value.values():
            _collect_com_edges(item, edges, seen, depth + 1)
        return

    for item in _as_sequence(value):
        if item is value:
            return
        _collect_com_edges(item, edges, seen, depth + 1)


def _looks_like_edge_dispatch(value: Any) -> bool:
    """Return whether a COM object exposes enough IEdge shape to select it."""

    return _get_com_member(value, "GetCurve") is not None or _get_com_member(value, "GetCurveParams2") is not None


def _polyline_numeric_count(value: Any) -> int:
    """Count numeric drawing polyline values without retaining the raw payload."""

    if value is None:
        return 0
    if isinstance(value, (str, bytes, bytearray, memoryview)):
        return len(_numeric_sequence(value))
    if isinstance(value, (int, float)):
        return 1
    if isinstance(value, dict):
        return sum(_polyline_numeric_count(item) for item in value.values())
    total = 0
    for item in _as_sequence(value):
        if item is value:
            break
        total += _polyline_numeric_count(item)
    return total


def _numeric_sequence(value: Any) -> list[float]:
    """Normalize numeric COM arrays, including binary double SAFEARRAY payloads."""

    if value is None:
        return []
    if isinstance(value, (bytes, bytearray)):
        return _double_sequence_from_bytes(bytes(value))
    if isinstance(value, memoryview):
        return _double_sequence_from_bytes(value.tobytes())

    values: list[float] = []
    for item in _as_sequence(value):
        if isinstance(item, (bytes, bytearray)):
            values.extend(_double_sequence_from_bytes(bytes(item)))
            continue
        if isinstance(item, memoryview):
            values.extend(_double_sequence_from_bytes(item.tobytes()))
            continue
        try:
            values.append(float(item))
        except (TypeError, ValueError):
            continue
    return values


def _is_number(value: Any) -> bool:
    """Return whether a COM value can be safely converted to float."""

    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _double_sequence_from_bytes(value: bytes) -> list[float]:
    """Decode pywin32 binary double arrays returned by some SolidWorks APIs."""

    if not value or len(value) % 8 != 0:
        return []
    try:
        return list(struct.unpack(f"{len(value) // 8}d", value))
    except struct.error:
        return []


def _com_bool(value: Any) -> bool | None:
    """Interpret COM VARIANT_BOOL-ish values without treating None as false evidence."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"true", "yes", "-1", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return None


def _safe_int(value: Any) -> int | None:
    """Return an int for numeric COM values, otherwise None."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _points_close(first: list[float], second: list[float], tolerance: float = 1e-8) -> bool:
    """Return whether two 3D points are effectively identical."""

    if len(first) < 3 or len(second) < 3:
        return False
    return all(abs(a - b) <= tolerance for a, b in zip(first[:3], second[:3]))


def _angle_span_is_circle(start: float, end: float, tolerance: float = 1e-6) -> bool:
    """Return whether a curve parameter interval represents a full turn."""

    span = abs(float(end) - float(start))
    return abs(span - 6.283185307179586) <= tolerance


def _components_from_drawing_component(drawing_component: Any) -> list[Any]:
    """Extract Component2 objects from a drawing-component tree."""

    if drawing_component is None:
        return []
    components: list[Any] = []
    component = _call_or_get(drawing_component, "Component")
    if component is not None:
        components.append(component)
    children = _call_or_get(drawing_component, "GetChildren")
    for child in _as_sequence(children):
        components.extend(_components_from_drawing_component(child))
    return components


def _safe_com_summary(value: Any) -> dict[str, Any]:
    """Summarize a COM return value without preserving the COM object itself."""

    summary: dict[str, Any] = {
        "value_type": type(value).__name__,
        "is_none": value is None,
        "is_false": value is False,
    }
    sequence = _as_sequence(value)
    if sequence:
        summary["sequence_length"] = len(sequence)
        summary["item_types"] = _unique_strings([type(item).__name__ for item in sequence[:8]])
        return summary
    if isinstance(value, (str, int, float, bool)):
        summary["value"] = value
    elif value is not None:
        summary["repr"] = repr(value)[:160]
    return summary


def _is_hole_callout(callout: Any) -> bool | None:
    """Return whether the display dimension reports itself as a hole callout."""

    if callout is None or callout is False:
        return False
    for target in (callout, _call_com_noargs(callout, "GetAnnotation")):
        if target is None:
            continue
        for attribute in ("IsHoleCallout", "GetHoleCallout"):
            try:
                value = getattr(target, attribute, None)
                if value is None:
                    continue
                value = value() if callable(value) else value
                if isinstance(value, bool):
                    return value
                if value is not None:
                    return True
            except Exception:
                continue
    return None


def _is_display_dimension(value: Any) -> bool | None:
    """Return whether a COM return value looks like a SolidWorks DisplayDimension."""

    if value is None or value is False:
        return False
    if _call_com_noargs(value, "GetAnnotation") is not None:
        return True
    for attribute in ("GetDimension", "GetType", "GetNameForSelection"):
        try:
            member = getattr(value, attribute, None)
            if member is None:
                continue
            result = member() if callable(member) else member
            if result is not None:
                return True
        except Exception:
            continue
    return None


def _mounting_plate_parameters(plan: ModelPlan) -> dict[str, float] | None:
    """Extract the first create_mounting_plate operation parameters."""

    return mounting_plate_parameters_from_plan(plan)


def _atomic_dimension_specs(plan: ModelPlan, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Return drawing dimension specs for staged atomic sketch dimensions."""

    front_view = views.get("front")
    if front_view is None:
        return []
    front_x, front_y = _drawing_view_position(front_view)
    front_scale = _drawing_view_scale(front_view)
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE")
    specs: list[dict[str, Any]] = []
    required_dimensions = set(atomic_dimension_ids_from_metadata(plan.metadata))
    if not required_dimensions:
        return specs

    for operation in plan.operations:
        if operation.op != "create_sketch":
            continue
        entities = {
            str(entity.get("id")): entity
            for entity in operation.parameters.get("entities", [])
            if isinstance(entity, dict) and entity.get("id")
        }
        for dimension in operation.parameters.get("dimensions", []):
            if not isinstance(dimension, dict):
                continue
            dimension_id = str(dimension.get("id") or "")
            if dimension_id not in required_dimensions:
                continue
            entity = entities.get(str(dimension.get("entity_id") or dimension.get("target_id") or ""))
            if not isinstance(entity, dict):
                continue
            dimension_type = str(dimension.get("type") or "").lower()
            if dimension_type in {"outer_diameter", "diameter", "revolve_outer_diameter"}:
                diameter_value = dimension.get("value")
                if diameter_value is None:
                    center = entity.get("center") if isinstance(entity.get("center"), list) else [0, 0]
                    width_value = entity.get("width")
                    try:
                        diameter_value = 2 * (abs(float(center[0])) + float(width_value) / 2)
                    except (TypeError, ValueError):
                        diameter_value = None
                if diameter_value is None:
                    continue
                radius_m = _to_meters(float(diameter_value) / 2, plan.units)
                sheet_radius = radius_m * front_scale
                specs.append(
                    {
                        "id": dimension_id,
                        "view_role": "front",
                        "method": "AddDiameterDimension2",
                        "edge_selector": "center_hole_flange_diameter",
                        "edge_selector_data": {
                            "expected_radius_m": radius_m,
                            "role": "atomic_revolve_outer_diameter",
                            "allow_unknown_radius": True,
                        },
                        "points": [
                            {
                                "x": front_x + sheet_radius,
                                "y": front_y,
                                "selection_types": edge_types,
                            }
                        ],
                        "position": {
                            "x": front_x + sheet_radius + 0.035,
                            "y": front_y + sheet_radius + 0.02,
                        },
                        "minimum_selections": 1,
                    }
                )
                continue
            if dimension_type != "width":
                continue
            width_value = entity.get("width") or dimension.get("value")
            if width_value is None:
                continue
            width_m = _to_meters(width_value, plan.units)
            center = entity.get("center") if isinstance(entity.get("center"), list) else [0, 0]
            center_x = front_x + _to_meters(center[0], plan.units) * front_scale
            center_y = front_y + _to_meters(center[1], plan.units) * front_scale
            half_width = width_m * front_scale / 2
            specs.append(
                {
                    "id": dimension_id,
                    "view_role": "front",
                    "method": "AddHorizontalDimension2",
                    "fallback_methods": ["AddDimension2"],
                    "edge_selector": "line_edge_length",
                    "edge_selector_data": {
                        "expected_length_m": width_m,
                        "orientation": "horizontal",
                        "role": "atomic_width",
                    },
                    "points": [
                        {"x": center_x - half_width, "y": center_y, "selection_types": edge_types},
                        {"x": center_x + half_width, "y": center_y, "selection_types": edge_types},
                    ],
                    "position": {"x": center_x, "y": center_y + 0.03},
                    "minimum_selections": 1,
                }
            )
    return specs


def _basic_dimension_specs(params: dict[str, float], units: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for MVP basic dimensions."""

    length_m = _to_meters(params["length"], units)
    width_m = _to_meters(params["width"], units)
    thickness_m = _to_meters(params["thickness"], units)
    radius_m = _to_meters(params["corner_radius"], units)
    edge_offset_m = _to_meters(params["edge_offset"], units)
    hole_points = _four_corner_hole_points(params["length"], params["width"], params["edge_offset"])
    first_hole = hole_points[0]
    hole_x_m = _to_meters(first_hole[0], units)
    hole_y_m = _to_meters(first_hole[1], units)

    top_view = views.get("top")
    front_view = views["front"]
    thickness_view = top_view or views.get("right") or front_view
    top_x, top_y = _drawing_view_position(top_view)
    front_x, front_y = _drawing_view_position(front_view)
    thickness_x, thickness_y = _drawing_view_position(thickness_view)
    top_scale = _drawing_view_scale(top_view)
    front_scale = _drawing_view_scale(front_view)
    thickness_scale = _drawing_view_scale(thickness_view)

    front_half_length = length_m * front_scale / 2
    front_half_width = width_m * front_scale / 2
    thickness_half_length = length_m * thickness_scale / 2
    thickness_half = thickness_m * thickness_scale / 2
    hole_x = front_x + hole_x_m * front_scale
    hole_y = front_y + hole_y_m * front_scale
    corner_x = front_x - front_half_length + radius_m * front_scale * (1 - 0.7071067811865476)
    corner_y = front_y - front_half_width + radius_m * front_scale * (1 - 0.7071067811865476)
    corner_arc_candidates = [
        {"x": corner_x, "y": corner_y},
        {
            "x": front_x - front_half_length + radius_m * front_scale * 0.22,
            "y": front_y - front_half_width + radius_m * front_scale * 0.78,
        },
        {
            "x": front_x - front_half_length + radius_m * front_scale * 0.78,
            "y": front_y - front_half_width + radius_m * front_scale * 0.22,
        },
        {
            "x": front_x - front_half_length + radius_m * front_scale * 0.50,
            "y": front_y - front_half_width + radius_m * front_scale * 0.50,
        },
    ]

    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    arc_types = ("ARC", "EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT")
    radius_proxy_types = ("VERTEX", "EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "ARC")
    hole_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT")
    dimension_ids = mounting_plate_basic_dimension_ids(params)
    radius_tangent_x = front_x - front_half_length + radius_m * front_scale
    radius_tangent_y = front_y - front_half_width + radius_m * front_scale
    radius_proxy_spec = {
        "id": dimension_ids[3],
        "view_role": "front",
        "method": "AddHorizontalDimension2",
        "points": [
            {"x": front_x - front_half_length, "y": radius_tangent_y + 0.0015, "selection_types": edge_types},
            {"x": radius_tangent_x, "y": front_y - front_half_width, "selection_types": radius_proxy_types},
        ],
        "position": {
            "x": (front_x - front_half_length + radius_tangent_x) / 2,
            "y": front_y - front_half_width - 0.022,
        },
    }
    return [
        {
            "id": dimension_ids[0],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "points": [
                {"x": front_x - front_half_length, "y": front_y, "selection_types": edge_types},
                {"x": front_x + front_half_length, "y": front_y, "selection_types": edge_types},
            ],
            "position": {"x": front_x, "y": front_y + front_half_width + 0.028},
        },
        {
            "id": dimension_ids[1],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "points": [
                {"x": front_x, "y": front_y - front_half_width, "selection_types": edge_types},
                {"x": front_x, "y": front_y + front_half_width, "selection_types": edge_types},
            ],
            "position": {"x": front_x + front_half_length + 0.03, "y": front_y},
        },
        {
            "id": dimension_ids[2],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "points": [
                {"x": thickness_x, "y": thickness_y - thickness_half, "selection_types": edge_types},
                {"x": thickness_x, "y": thickness_y + thickness_half, "selection_types": edge_types},
            ],
            "position": {"x": thickness_x + thickness_half_length + 0.028, "y": thickness_y},
        },
        {
            "id": dimension_ids[3],
            "view_role": "front",
            "method": "AddRadialDimension2",
            "fallback_methods": ["Extension.AddSpecificDimension"],
            "specific_dimension_type": SW_RADIAL_DIMENSION,
            "edge_selector": "mounting_plate_corner_radius",
            "edge_selector_data": {
                "center": {
                    "x": _to_meters(-params["length"] / 2 + params["corner_radius"], units),
                    "y": _to_meters(-params["width"] / 2 + params["corner_radius"], units),
                    "z": _to_meters(params["thickness"], units),
                },
                "radius_m": radius_m,
            },
            "points": [
                {"x": corner_x, "y": corner_y, "selection_types": arc_types},
            ],
            "point_sets": [
                [{"x": point["x"], "y": point["y"], "selection_types": arc_types}]
                for point in corner_arc_candidates
            ],
            "proxy_specs": [radius_proxy_spec],
            "proxy_reason": "Radial dimension APIs did not create a display dimension for this drawing arc.",
            "position": {"x": front_x - front_half_length - 0.025, "y": front_y - front_half_width - 0.02},
            "minimum_selections": 1,
        },
        {
            "id": dimension_ids[4],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "edge_selector": "mounting_plate_hole_edge_offset",
            "edge_selector_data": {
                "plate_left_x": _to_meters(-params["length"] / 2, units),
                "hole_center": {
                    "x": _to_meters(first_hole[0], units),
                    "y": _to_meters(first_hole[1], units),
                    "z": _to_meters(params["thickness"], units),
                },
                "hole_radius_m": _to_meters(ISO_METRIC_COARSE_THREADS[str(params.get("thread_spec", "M6")).upper()]["tap_drill_diameter"] / 2, units)
                if str(params.get("thread_spec", "M6")).upper() in ISO_METRIC_COARSE_THREADS
                else 0.0,
            },
            "points": [
                {"x": front_x - front_half_length, "y": hole_y, "selection_types": edge_types},
                {"x": hole_x, "y": hole_y, "selection_types": hole_types},
            ],
            "position": {"x": (front_x - front_half_length + hole_x) / 2, "y": hole_y - 0.028},
            "expected_value_m": edge_offset_m,
        },
    ]


def _center_hole_flange_dimension_specs(params: dict[str, float], units: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled center-hole flange dimensions."""

    outer_radius_m = _to_meters(params["outer_diameter"] / 2, units)
    hole_radius_m = _to_meters(params["hole_diameter"] / 2, units)
    thickness_m = _to_meters(params["thickness"], units)
    ids = center_hole_flange_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    outer_sheet_radius = outer_radius_m * front_scale
    thickness_half = thickness_m * top_scale / 2
    thickness_half_length = outer_radius_m * top_scale
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": outer_radius_m, "role": "outer_diameter"},
            "points": [{"x": front_x + outer_sheet_radius, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + outer_sheet_radius + 0.03, "y": front_y + outer_sheet_radius + 0.02},
            "minimum_selections": 1,
        },
        {
            "id": ids[1],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": hole_radius_m, "role": "hole_diameter"},
            "points": [{"x": front_x + hole_radius_m * front_scale, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + hole_radius_m * front_scale + 0.035, "y": front_y - hole_radius_m * front_scale - 0.025},
            "minimum_selections": 1,
        },
        {
            "id": ids[2],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - thickness_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + thickness_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + thickness_half_length + 0.03, "y": top_y},
        },
    ]


def _center_hole_plate_dimension_specs(params: dict[str, float], units: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled center-hole plate dimensions."""

    length_m = _to_meters(params["length"], units)
    width_m = _to_meters(params["width"], units)
    thickness_m = _to_meters(params["thickness"], units)
    hole_radius_m = _to_meters(params["hole_diameter"] / 2, units)
    ids = center_hole_plate_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    front_half_length = length_m * front_scale / 2
    front_half_width = width_m * front_scale / 2
    thickness_half = thickness_m * top_scale / 2
    thickness_half_length = length_m * top_scale / 2
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "points": [
                {"x": front_x - front_half_length, "y": front_y, "selection_types": edge_types},
                {"x": front_x + front_half_length, "y": front_y, "selection_types": edge_types},
            ],
            "position": {"x": front_x, "y": front_y + front_half_width + 0.028},
        },
        {
            "id": ids[1],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "points": [
                {"x": front_x, "y": front_y - front_half_width, "selection_types": edge_types},
                {"x": front_x, "y": front_y + front_half_width, "selection_types": edge_types},
            ],
            "position": {"x": front_x + front_half_length + 0.03, "y": front_y},
        },
        {
            "id": ids[2],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - thickness_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + thickness_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + thickness_half_length + 0.03, "y": top_y},
        },
        {
            "id": ids[3],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": hole_radius_m, "role": "center_hole_plate_hole_diameter"},
            "points": [{"x": front_x + hole_radius_m * front_scale, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + hole_radius_m * front_scale + 0.035, "y": front_y - hole_radius_m * front_scale - 0.025},
            "minimum_selections": 1,
        },
    ]


def _slotted_array_plate_dimension_specs(params: dict[str, float], units: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled slotted-array plate dimensions."""

    length_m = _to_meters(params["length"], units)
    width_m = _to_meters(params["width"], units)
    thickness_m = _to_meters(params["thickness"], units)
    slot_length_m = _to_meters(params["slot_length"], units)
    slot_width_m = _to_meters(params["slot_width"], units)
    hole_radius_m = _to_meters(params["hole_diameter"] / 2, units)
    spacing_x_m = _to_meters(params["hole_spacing_x"], units)
    spacing_y_m = _to_meters(params["hole_spacing_y"], units)
    ids = slotted_array_plate_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    front_half_length = length_m * front_scale / 2
    front_half_width = width_m * front_scale / 2
    thickness_half = thickness_m * top_scale / 2
    thickness_half_length = length_m * top_scale / 2
    slot_half_length = slot_length_m * front_scale / 2
    slot_half_width = slot_width_m * front_scale / 2
    hole_sheet_radius = hole_radius_m * front_scale
    spacing_x_sheet = spacing_x_m * front_scale
    spacing_y_sheet = spacing_y_m * front_scale
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    first_hole_x = front_x - spacing_x_sheet / 2
    second_hole_x = front_x + spacing_x_sheet / 2
    first_hole_y = front_y - spacing_y_sheet / 2
    second_hole_y = front_y + spacing_y_sheet / 2
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "points": [
                {"x": front_x - front_half_length, "y": front_y, "selection_types": edge_types},
                {"x": front_x + front_half_length, "y": front_y, "selection_types": edge_types},
            ],
            "position": {"x": front_x, "y": front_y + front_half_width + 0.03},
        },
        {
            "id": ids[1],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "points": [
                {"x": front_x, "y": front_y - front_half_width, "selection_types": edge_types},
                {"x": front_x, "y": front_y + front_half_width, "selection_types": edge_types},
            ],
            "position": {"x": front_x + front_half_length + 0.03, "y": front_y},
        },
        {
            "id": ids[2],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - thickness_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + thickness_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + thickness_half_length + 0.03, "y": top_y},
        },
        {
            "id": ids[3],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": front_x - slot_half_length, "y": front_y, "selection_types": edge_types},
                {"x": front_x + slot_half_length, "y": front_y, "selection_types": edge_types},
            ],
            "position": {"x": front_x, "y": front_y - slot_half_width - 0.025},
        },
        {
            "id": ids[4],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": front_x, "y": front_y - slot_half_width, "selection_types": edge_types},
                {"x": front_x, "y": front_y + slot_half_width, "selection_types": edge_types},
            ],
            "position": {"x": front_x + slot_half_length + 0.025, "y": front_y},
        },
        {
            "id": ids[5],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": hole_radius_m, "role": "slotted_array_plate_hole_diameter"},
            "points": [{"x": first_hole_x + hole_sheet_radius, "y": first_hole_y, "selection_types": edge_types}],
            "position": {"x": first_hole_x + hole_sheet_radius + 0.035, "y": first_hole_y - hole_sheet_radius - 0.025},
            "minimum_selections": 1,
        },
        {
            "id": ids[6],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": first_hole_x, "y": second_hole_y, "selection_types": edge_types},
                {"x": second_hole_x, "y": second_hole_y, "selection_types": edge_types},
            ],
            "position": {"x": front_x, "y": second_hole_y + hole_sheet_radius + 0.025},
        },
        {
            "id": ids[7],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": second_hole_x, "y": first_hole_y, "selection_types": edge_types},
                {"x": second_hole_x, "y": second_hole_y, "selection_types": edge_types},
            ],
            "position": {"x": second_hole_x + hole_sheet_radius + 0.025, "y": front_y},
        },
    ]


def _mounting_block_dimension_specs(params: dict[str, float], units: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled mounting-block dimensions."""

    length_m = _to_meters(params["length"], units)
    width_m = _to_meters(params["width"], units)
    height_m = _to_meters(params["height"], units)
    hole_radius_m = _to_meters(params["hole_diameter"] / 2, units)
    ids = mounting_block_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    front_half_length = length_m * front_scale / 2
    front_half_width = width_m * front_scale / 2
    height_half = height_m * top_scale / 2
    height_half_length = length_m * top_scale / 2
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "points": [
                {"x": front_x - front_half_length, "y": front_y, "selection_types": edge_types},
                {"x": front_x + front_half_length, "y": front_y, "selection_types": edge_types},
            ],
            "position": {"x": front_x, "y": front_y + front_half_width + 0.028},
        },
        {
            "id": ids[1],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "points": [
                {"x": front_x, "y": front_y - front_half_width, "selection_types": edge_types},
                {"x": front_x, "y": front_y + front_half_width, "selection_types": edge_types},
            ],
            "position": {"x": front_x + front_half_length + 0.03, "y": front_y},
        },
        {
            "id": ids[2],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - height_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + height_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + height_half_length + 0.03, "y": top_y},
        },
        {
            "id": ids[3],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": hole_radius_m, "role": "mounting_block_hole_diameter"},
            "points": [{"x": front_x + hole_radius_m * front_scale, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + hole_radius_m * front_scale + 0.035, "y": front_y - hole_radius_m * front_scale - 0.025},
            "minimum_selections": 1,
        },
    ]


def _bracket_dimension_specs(params: dict[str, float], units: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled bracket dimensions."""

    base_length_m = _to_meters(params["base_length"], units)
    base_width_m = _to_meters(params["base_width"], units)
    base_thickness_m = _to_meters(params["base_thickness"], units)
    upright_height_m = _to_meters(params["upright_height"], units)
    upright_thickness_m = _to_meters(params["upright_thickness"], units)
    hole_radius_m = _to_meters(params["hole_diameter"] / 2, units)
    ids = bracket_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    length_half = base_length_m * front_scale / 2
    width_half = base_width_m * top_scale / 2
    base_thickness_sheet = base_thickness_m * front_scale
    upright_height_sheet = upright_height_m * front_scale
    upright_thickness_sheet = upright_thickness_m * front_scale
    hole_sheet_radius = hole_radius_m * front_scale
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "fallback_methods": ["AddDimension2"],
            "edge_selector": "line_edge_length",
            "edge_selector_data": {
                "expected_length_m": base_length_m,
                "orientation": "horizontal",
                "role": "bracket_base_length",
            },
            "points": [
                {"x": front_x - length_half, "y": front_y, "selection_types": edge_types},
                {"x": front_x + length_half, "y": front_y, "selection_types": edge_types},
            ],
            "position": {"x": front_x, "y": front_y - 0.04},
        },
        {
            "id": ids[1],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - width_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + width_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + length_half + 0.03, "y": top_y},
        },
        {
            "id": ids[2],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddDimension2"],
            "edge_selector": "line_edge_length",
            "edge_selector_data": {
                "expected_length_m": base_thickness_m,
                "orientation": "vertical",
                "role": "bracket_base_thickness",
            },
            "points": [
                {"x": front_x + length_half, "y": front_y, "selection_types": edge_types},
                {"x": front_x + length_half, "y": front_y + base_thickness_sheet, "selection_types": edge_types},
            ],
            "position": {"x": front_x + length_half + 0.025, "y": front_y + base_thickness_sheet / 2},
        },
        {
            "id": ids[3],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddDimension2"],
            "edge_selector": "line_edge_length",
            "edge_selector_data": {
                "expected_length_m": upright_height_m,
                "orientation": "vertical",
                "role": "bracket_upright_height",
            },
            "points": [
                {"x": front_x - length_half, "y": front_y, "selection_types": edge_types},
                {"x": front_x - length_half, "y": front_y + upright_height_sheet, "selection_types": edge_types},
            ],
            "position": {"x": front_x - length_half - 0.035, "y": front_y + upright_height_sheet / 2},
        },
        {
            "id": ids[4],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "fallback_methods": ["AddDimension2"],
            "edge_selector": "line_edge_length",
            "edge_selector_data": {
                "expected_length_m": upright_thickness_m,
                "orientation": "horizontal",
                "role": "bracket_upright_thickness",
            },
            "points": [
                {"x": front_x - length_half, "y": front_y + upright_height_sheet, "selection_types": edge_types},
                {"x": front_x - length_half + upright_thickness_sheet, "y": front_y + upright_height_sheet, "selection_types": edge_types},
            ],
            "position": {"x": front_x - length_half + upright_thickness_sheet / 2, "y": front_y + upright_height_sheet + 0.03},
        },
        {
            "id": ids[5],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {
                "expected_radius_m": hole_radius_m,
                "expected_point_m": [hole_radius_m, base_thickness_m / 2, base_width_m],
                "role": "bracket_hole_diameter",
            },
            "points": [{"x": front_x + hole_sheet_radius, "y": front_y + base_thickness_sheet / 2, "selection_types": edge_types}],
            "position": {"x": front_x + hole_sheet_radius + 0.035, "y": front_y + base_thickness_sheet + 0.02},
            "minimum_selections": 1,
        },
    ]


def _end_cap_dimension_specs(params: dict[str, float], units: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled end-cap dimensions."""

    outer_radius_m = _to_meters(params["outer_diameter"] / 2, units)
    center_radius_m = _to_meters(params["center_hole_diameter"] / 2, units)
    bolt_circle_radius_m = _to_meters(params["bolt_circle_diameter"] / 2, units)
    bolt_hole_radius_m = _to_meters(params["bolt_hole_diameter"] / 2, units)
    thickness_m = _to_meters(params["thickness"], units)
    ids = end_cap_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    outer_sheet_radius = outer_radius_m * front_scale
    center_sheet_radius = center_radius_m * front_scale
    bolt_sheet_radius = bolt_hole_radius_m * front_scale
    bolt_hole_x = front_x + bolt_circle_radius_m * front_scale + bolt_sheet_radius
    thickness_half = thickness_m * top_scale / 2
    thickness_half_width = outer_radius_m * top_scale
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": outer_radius_m, "role": "end_cap_outer_diameter"},
            "points": [{"x": front_x + outer_sheet_radius, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + outer_sheet_radius + 0.03, "y": front_y + outer_sheet_radius + 0.02},
            "minimum_selections": 1,
        },
        {
            "id": ids[1],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": center_radius_m, "role": "end_cap_center_hole"},
            "points": [{"x": front_x + center_sheet_radius, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + center_sheet_radius + 0.035, "y": front_y - center_sheet_radius - 0.025},
            "minimum_selections": 1,
        },
        {
            "id": ids[2],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": bolt_hole_radius_m, "role": "end_cap_bolt_hole"},
            "points": [{"x": bolt_hole_x, "y": front_y, "selection_types": edge_types}],
            "position": {"x": bolt_hole_x + 0.035, "y": front_y + bolt_sheet_radius + 0.025},
            "minimum_selections": 1,
        },
        {
            "id": ids[3],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - thickness_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + thickness_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + thickness_half_width + 0.03, "y": top_y},
        },
    ]


def _washer_dimension_specs(params: dict[str, float], units: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled washer dimensions."""

    outer_radius_m = _to_meters(params["outer_diameter"] / 2, units)
    inner_radius_m = _to_meters(params["inner_diameter"] / 2, units)
    thickness_m = _to_meters(params["thickness"], units)
    ids = washer_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    outer_sheet_radius = outer_radius_m * front_scale
    thickness_half = thickness_m * top_scale / 2
    thickness_half_length = outer_radius_m * top_scale
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": outer_radius_m, "role": "washer_outer_diameter"},
            "points": [{"x": front_x + outer_sheet_radius, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + outer_sheet_radius + 0.03, "y": front_y + outer_sheet_radius + 0.02},
            "minimum_selections": 1,
        },
        {
            "id": ids[1],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": inner_radius_m, "role": "washer_inner_diameter"},
            "points": [{"x": front_x + inner_radius_m * front_scale, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + inner_radius_m * front_scale + 0.035, "y": front_y - inner_radius_m * front_scale - 0.025},
            "minimum_selections": 1,
        },
        {
            "id": ids[2],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - thickness_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + thickness_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + thickness_half_length + 0.03, "y": top_y},
        },
    ]


def _sleeve_dimension_specs(params: dict[str, float], units: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled sleeve dimensions."""

    outer_radius_m = _to_meters(params["outer_diameter"] / 2, units)
    inner_radius_m = _to_meters(params["inner_diameter"] / 2, units)
    length_m = _to_meters(params["length"], units)
    ids = sleeve_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    outer_sheet_radius = outer_radius_m * front_scale
    length_half = length_m * top_scale / 2
    length_half_width = outer_radius_m * top_scale
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": outer_radius_m, "role": "sleeve_outer_diameter"},
            "points": [{"x": front_x + outer_sheet_radius, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + outer_sheet_radius + 0.03, "y": front_y + outer_sheet_radius + 0.02},
            "minimum_selections": 1,
        },
        {
            "id": ids[1],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": inner_radius_m, "role": "sleeve_inner_diameter"},
            "points": [{"x": front_x + inner_radius_m * front_scale, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + inner_radius_m * front_scale + 0.035, "y": front_y - inner_radius_m * front_scale - 0.025},
            "minimum_selections": 1,
        },
        {
            "id": ids[2],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - length_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + length_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + length_half_width + 0.03, "y": top_y},
        },
    ]


def _shaft_dimension_specs(params: dict[str, float], units: str, views: dict[str, Any]) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled shaft dimensions."""

    radius_m = _to_meters(params["diameter"] / 2, units)
    length_m = _to_meters(params["length"], units)
    ids = shaft_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    sheet_radius = radius_m * front_scale
    length_half = length_m * top_scale / 2
    length_half_width = radius_m * top_scale
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddDiameterDimension2",
            "edge_selector": "center_hole_flange_diameter",
            "edge_selector_data": {"expected_radius_m": radius_m, "role": "shaft_diameter"},
            "points": [{"x": front_x + sheet_radius, "y": front_y, "selection_types": edge_types}],
            "position": {"x": front_x + sheet_radius + 0.03, "y": front_y + sheet_radius + 0.02},
            "minimum_selections": 1,
        },
        {
            "id": ids[1],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - length_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + length_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + length_half_width + 0.03, "y": top_y},
        },
    ]


def _sheet_metal_base_flange_dimension_specs(
    params: dict[str, float],
    units: str,
    views: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled sheet-metal base flanges."""

    length_m = _to_meters(params["length"], units)
    width_m = _to_meters(params["width"], units)
    thickness_m = _to_meters(params["thickness"], units)
    ids = sheet_metal_base_flange_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    front_half_length = length_m * front_scale / 2
    front_half_width = width_m * front_scale / 2
    thickness_half = thickness_m * top_scale / 2
    thickness_half_length = length_m * top_scale / 2
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": front_x - front_half_length, "y": front_y, "selection_types": edge_types},
                {"x": front_x + front_half_length, "y": front_y, "selection_types": edge_types},
            ],
            "position": {"x": front_x, "y": front_y + front_half_width + 0.03},
        },
        {
            "id": ids[1],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": front_x, "y": front_y - front_half_width, "selection_types": edge_types},
                {"x": front_x, "y": front_y + front_half_width, "selection_types": edge_types},
            ],
            "position": {"x": front_x + front_half_length + 0.03, "y": front_y},
        },
        {
            "id": ids[2],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - thickness_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + thickness_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + thickness_half_length + 0.03, "y": top_y},
        },
    ]


def _weldment_frame_dimension_specs(
    params: dict[str, Any],
    units: str,
    views: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled weldment frames."""

    length_m = _to_meters(float(params["length"]), units)
    width_m = _to_meters(float(params["width"]), units)
    profile_m = _to_meters(float(params["profile_outer_width"]), units)
    ids = weldment_frame_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    front_half_length = length_m * front_scale / 2
    front_half_width = width_m * front_scale / 2
    profile_half = profile_m * top_scale / 2
    profile_half_length = length_m * top_scale / 2
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": front_x - front_half_length, "y": front_y, "selection_types": edge_types},
                {"x": front_x + front_half_length, "y": front_y, "selection_types": edge_types},
            ],
            "position": {"x": front_x, "y": front_y + front_half_width + 0.035},
        },
        {
            "id": ids[1],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": front_x, "y": front_y - front_half_width, "selection_types": edge_types},
                {"x": front_x, "y": front_y + front_half_width, "selection_types": edge_types},
            ],
            "position": {"x": front_x + front_half_length + 0.035, "y": front_y},
        },
        {
            "id": ids[2],
            "view_role": "top",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddHorizontalDimension2", "AddDimension2"],
            "points": [
                {"x": top_x, "y": top_y - profile_half, "selection_types": edge_types},
                {"x": top_x, "y": top_y + profile_half, "selection_types": edge_types},
            ],
            "position": {"x": top_x + profile_half_length + 0.035, "y": top_y},
        },
    ]


def _static_simulation_dimension_specs(
    params: dict[str, Any],
    units: str,
    views: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build drawing-sheet selection specs for controlled static simulation beams."""

    length_m = _to_meters(float(params["length"]), units)
    width_m = _to_meters(float(params["width"]), units)
    height_m = _to_meters(float(params["height"]), units)
    ids = static_simulation_basic_dimension_ids(params)

    front_view = views["front"]
    top_view = views.get("top") or views.get("right") or front_view
    front_x, front_y = _drawing_view_position(front_view)
    top_x, top_y = _drawing_view_position(top_view)
    front_scale = _drawing_view_scale(front_view)
    top_scale = _drawing_view_scale(top_view)
    front_half_width = width_m * front_scale / 2
    front_half_height = height_m * front_scale / 2
    top_half_length = length_m * top_scale / 2
    top_half_width = width_m * top_scale / 2
    edge_types = ("EDGE", "SKETCHSEGMENT", "EXTSKETCHSEGMENT", "LINE", "ARC")
    return [
        {
            "id": ids[0],
            "view_role": "top",
            "method": "AddHorizontalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": top_x - top_half_length, "y": top_y, "selection_types": edge_types},
                {"x": top_x + top_half_length, "y": top_y, "selection_types": edge_types},
            ],
            "position": {"x": top_x, "y": top_y + top_half_width + 0.03},
        },
        {
            "id": ids[1],
            "view_role": "front",
            "method": "AddHorizontalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": front_x - front_half_width, "y": front_y, "selection_types": edge_types},
                {"x": front_x + front_half_width, "y": front_y, "selection_types": edge_types},
            ],
            "position": {"x": front_x, "y": front_y + front_half_height + 0.03},
        },
        {
            "id": ids[2],
            "view_role": "front",
            "method": "AddVerticalDimension2",
            "fallback_methods": ["AddDimension2"],
            "points": [
                {"x": front_x, "y": front_y - front_half_height, "selection_types": edge_types},
                {"x": front_x, "y": front_y + front_half_height, "selection_types": edge_types},
            ],
            "position": {"x": front_x + front_half_width + 0.03, "y": front_y},
        },
    ]


def _trusted_basic_dimension_ids_from_plan(plan: ModelPlan) -> list[str]:
    """Return required drawing dimensions for the current controlled workflow."""

    mounting_plate_dimensions = mounting_plate_basic_dimension_ids_from_plan(plan)
    if mounting_plate_dimensions:
        return mounting_plate_dimensions
    bracket_dimensions = bracket_basic_dimension_ids_from_plan(plan)
    if bracket_dimensions:
        return bracket_dimensions
    flange_dimensions = center_hole_flange_basic_dimension_ids_from_plan(plan)
    if flange_dimensions:
        return flange_dimensions
    center_hole_plate_dimensions = center_hole_plate_basic_dimension_ids_from_plan(plan)
    if center_hole_plate_dimensions:
        return center_hole_plate_dimensions
    end_cap_dimensions = end_cap_basic_dimension_ids_from_plan(plan)
    if end_cap_dimensions:
        return end_cap_dimensions
    mounting_block_dimensions = mounting_block_basic_dimension_ids_from_plan(plan)
    if mounting_block_dimensions:
        return mounting_block_dimensions
    shaft_dimensions = shaft_basic_dimension_ids_from_plan(plan)
    if shaft_dimensions:
        return shaft_dimensions
    sheet_metal_dimensions = sheet_metal_base_flange_basic_dimension_ids_from_plan(plan)
    if sheet_metal_dimensions:
        return sheet_metal_dimensions
    weldment_dimensions = weldment_frame_basic_dimension_ids_from_plan(plan)
    if weldment_dimensions:
        return weldment_dimensions
    simulation_dimensions = static_simulation_basic_dimension_ids_from_plan(plan)
    if simulation_dimensions:
        return simulation_dimensions
    washer_dimensions = washer_basic_dimension_ids_from_plan(plan)
    if washer_dimensions:
        return washer_dimensions
    sleeve_dimensions = sleeve_basic_dimension_ids_from_plan(plan)
    if sleeve_dimensions:
        return sleeve_dimensions
    atomic_dimensions = atomic_dimension_ids_from_metadata(plan.metadata)
    if atomic_dimensions:
        return atomic_dimensions
    return slotted_array_plate_basic_dimension_ids_from_plan(plan)


def _drawing_view_position(view: Any) -> tuple[float, float]:
    """Return a drawing view sheet position, falling back to the MVP top view slot."""

    value = _call_or_get(view, "Position")
    sequence = _as_sequence(value)
    if len(sequence) >= 2:
        try:
            return float(sequence[0]), float(sequence[1])
        except (TypeError, ValueError):
            pass
    return 0.18, 0.28


def _drawing_view_scale(view: Any) -> float:
    """Return the drawing view decimal scale when available."""

    for attribute in ("ScaleDecimal", "ScaleRatio"):
        value = _call_or_get(view, attribute)
        if value is None:
            continue
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                denominator = float(value[1])
                return float(value[0]) / denominator if denominator else 1.0
            except (TypeError, ValueError):
                continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 1.0


def _call_or_get(value: Any, attribute: str) -> Any:
    """Read a COM property or zero-argument method."""

    try:
        member = _get_com_member(value, attribute)
        return member() if callable(member) else member
    except Exception:
        return None


def _call_com_noargs(value: Any, attribute: str) -> Any:
    """Call a COM zero-argument method and return None on failure."""

    try:
        member = getattr(value, attribute, None)
        if callable(member):
            return member()
    except Exception:
        return None
    return None


def _template_preflight_check(check_id: str, configured_path: str | None, suffix: str, sw: Any | None) -> dict[str, Any]:
    """Validate or discover one SolidWorks template path."""

    suffix = suffix.lower()
    candidates: list[dict[str, Any]] = []
    if configured_path:
        path = Path(configured_path).expanduser()
        candidates.append({"source": "configured", "path": path_to_string(path), "exists": path.exists()})
        if path.exists() and path.suffix.lower() == suffix:
            return {
                "id": check_id,
                "ok": True,
                "message": f"Configured {suffix} template exists.",
                "path": path_to_string(path),
                "source": "configured",
                "candidates": candidates,
            }
        return {
            "id": check_id,
            "ok": False,
            "message": f"Configured template is missing or not a {suffix} file.",
            "path": path_to_string(path),
            "source": "configured",
            "candidates": candidates,
            "remediation": f"Set SOLIDWORKS_MCP_{'PART' if suffix == '.prtdot' else 'DRAWING'}_TEMPLATE to an existing {suffix} path.",
        }

    default_template = _find_default_template_from_sw(sw, suffix) if sw is not None else None
    if default_template:
        candidates.append({"source": "solidworks_default", "path": default_template, "exists": True})
        return {
            "id": check_id,
            "ok": True,
            "message": f"SolidWorks default {suffix} template was found.",
            "path": default_template,
            "source": "solidworks_default",
            "candidates": candidates,
        }

    for path in _common_template_candidates(suffix):
        candidates.append({"source": "common_path", "path": path_to_string(path), "exists": path.exists()})
        if path.exists():
            return {
                "id": check_id,
                "ok": True,
                "message": f"Common {suffix} template path exists.",
                "path": path_to_string(path),
                "source": "common_path",
                "candidates": candidates,
            }

    return {
        "id": check_id,
        "ok": False,
        "message": f"No usable {suffix} template was found.",
        "candidates": candidates,
        "remediation": f"Configure SOLIDWORKS_MCP_{'PART' if suffix == '.prtdot' else 'DRAWING'}_TEMPLATE.",
    }


def _weldment_profile_preflight_check(plan: ModelPlan) -> dict[str, Any]:
    """Validate or discover the weldment profile required by a controlled frame plan."""

    params = weldment_frame_parameters_from_plan(plan)
    if params is None:
        return {
            "id": "weldment_profile",
            "ok": False,
            "message": "No create_weldment_frame parameters were available for profile preflight.",
            "remediation": "Provide a valid create_weldment_frame operation.",
        }
    result = _resolve_weldment_profile(params)
    ok = bool(result.get("path"))
    return {
        "id": "weldment_profile",
        "ok": ok,
        "message": "Weldment profile path is ready."
        if ok
        else str(result.get("failure_reason") or "Weldment profile path is not ready."),
        "path": result.get("path"),
        "source": result.get("source"),
        "candidates": result.get("candidates", []),
        "remediation": None
        if ok
        else "Set operations[].parameters.profile.profile_path to an existing .sldlfp square-tube profile.",
    }


def _simulation_api_preflight_check(sw: Any | None) -> dict[str, Any]:
    """Probe whether the SolidWorks Simulation API is reachable for controlled studies."""

    attempts: list[dict[str, Any]] = []
    if sw is None:
        return {
            "id": "simulation_api",
            "ok": False,
            "message": "SolidWorks COM is not connected, so Simulation API readiness cannot be checked.",
            "attempts": attempts,
            "remediation": "Start SolidWorks and ensure the Simulation add-in is installed and licensed.",
        }

    addin_names = [
        "SldWorks.Simulation",
        "SldWorks.Simulation.15",
        "CosmosWorks.CosmosWorks",
        "CosmosWorks.CosmosWorks.15",
        "CosmosWorks",
    ]
    for name in addin_names:
        try:
            addin = sw.GetAddInObject(name)
            ok = addin is not None
            attempts.append({"method": "GetAddInObject", "name": name, "ok": ok, "type": str(type(addin)) if ok else None})
            if ok:
                return {
                    "id": "simulation_api",
                    "ok": True,
                    "message": "SolidWorks Simulation add-in object is available.",
                    "method": "GetAddInObject",
                    "name": name,
                    "attempts": attempts,
                    "remediation": None,
                }
        except Exception as exc:
            attempts.append({"method": "GetAddInObject", "name": name, "ok": False, "error": str(exc)})

    try:
        import win32com.client
    except Exception as exc:
        attempts.append({"method": "win32com_import", "ok": False, "error": str(exc)})
    else:
        for progid in ["SldWorks.Simulation", "SldWorks.Simulation.15", "CosmosWorks.CosmosWorks", "CosmosWorks.CosmosWorks.15"]:
            try:
                obj = win32com.client.Dispatch(progid)
                attempts.append({"method": "Dispatch", "progid": progid, "ok": True, "type": str(type(obj))})
                return {
                    "id": "simulation_api",
                    "ok": True,
                    "message": "SolidWorks Simulation COM object is available.",
                    "method": "Dispatch",
                    "progid": progid,
                    "attempts": attempts,
                    "remediation": None,
                }
            except Exception as exc:
                attempts.append({"method": "Dispatch", "progid": progid, "ok": False, "error": str(exc)})

    return {
        "id": "simulation_api",
        "ok": False,
        "message": "SolidWorks Simulation API was not reachable through add-in or COM ProgID probes.",
        "attempts": attempts,
        "remediation": (
            "Install/register and license SolidWorks Simulation, then verify GetAddInObject('SldWorks.Simulation') "
            "or a CosmosWorks COM ProgID returns an object."
        ),
    }


def _find_default_template_from_sw(sw: Any, suffix: str) -> str | None:
    """Find a default template path from SolidWorks user preferences."""

    method = _get_com_member(sw, "GetUserPreferenceStringValue")
    if not callable(method):
        return None
    suffix = suffix.lower()
    for preference_id in range(1, 51):
        try:
            value = method(preference_id)
        except Exception:
            continue
        if isinstance(value, str) and value.lower().endswith(suffix) and Path(value).exists():
            return path_to_string(Path(value))
    return None


def _common_template_candidates(suffix: str) -> list[Path]:
    """Return common SolidWorks template paths seen on Windows installs."""

    suffix = suffix.lower()
    roots = [
        Path("C:/ProgramData/SOLIDWORKS/SOLIDWORKS 2022/templates"),
        Path("D:/ProgramData/SOLIDWORKS/SOLIDWORKS 2022/templates"),
        Path("D:/Program Files/SOLIDWORKS Corp/SOLIDWORKS/data/templates"),
        Path("C:/Program Files/SOLIDWORKS Corp/SOLIDWORKS/data/templates"),
    ]
    if suffix == ".prtdot":
        names = [
            "gb_part.prtdot",
            "Part.prtdot",
            "part.prtdot",
            "MBD/part 0051mm to 0250mm.prtdot",
            "MBD/part 0011mm to 0050mm.prtdot",
        ]
    elif suffix == ".asmdot":
        names = ["gb_assembly.asmdot", "Assembly.asmdot", "assembly.asmdot", "assem.asmdot"]
    else:
        names = ["gb_a3.drwdot", "Drawing.drwdot", "drawing.drwdot", "iso.drwdot", "gb.drwdot"]
    return [root / name for root in roots for name in names]


def _first_existing_common_template(suffix: str) -> str | None:
    """Return the first existing common template path for execution fallback."""

    for path in _common_template_candidates(suffix):
        if path.exists():
            return path_to_string(path)
    return None


def _output_dir_preflight_check(output_root: Path) -> dict[str, Any]:
    """Check that the output root can be created and written."""

    try:
        output_root.mkdir(parents=True, exist_ok=True)
        probe = output_root / ".solidworks_mcp_preflight.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {
            "id": "output_dir",
            "ok": True,
            "message": "Output directory is writable.",
            "path": path_to_string(output_root),
        }
    except Exception as exc:
        return {
            "id": "output_dir",
            "ok": False,
            "message": str(exc),
            "path": path_to_string(output_root),
            "remediation": "Set SOLIDWORKS_MCP_OUTPUT_DIR to a writable directory.",
        }


def _run_native_document_candidates(run_path: Path) -> list[dict[str, str]]:
    """Return SLDPRT/SLDDRW artifact paths declared by a completed run."""

    candidates: list[dict[str, str]] = []
    for file_name in ("execution_report.json", "artifacts.json", "delivery_manifest.json"):
        payload = _read_run_json(run_path / file_name)
        output_files = payload.get("output_files") if isinstance(payload, dict) else None
        if not isinstance(output_files, dict):
            continue
        for key, value in output_files.items():
            artifact_id = str(key).lower()
            if artifact_id not in {"sldprt", "slddrw"}:
                continue
            raw_path = value.get("path") if isinstance(value, dict) else value
            if not raw_path:
                continue
            candidate_path = _resolve_run_artifact_path(str(raw_path), run_path)
            try:
                resolved = candidate_path.resolve()
                if not (resolved == run_path or run_path in resolved.parents):
                    continue
            except Exception:
                continue
            item = {
                "kind": "part" if artifact_id == "sldprt" else "drawing",
                "id": artifact_id,
                "path": path_to_string(candidate_path),
                "source": file_name,
            }
            if not any(existing["path"] == item["path"] for existing in candidates):
                candidates.append(item)
    return candidates


def _cleanup_lookup_names(candidate: dict[str, str]) -> list[str]:
    """Return lookup names to resolve an already-open SolidWorks document."""

    path = Path(candidate["path"])
    names = [candidate["path"], path.name, path.stem]
    unique: list[str] = []
    for name in names:
        text = str(name).strip()
        if text and text not in unique:
            unique.append(text)
    return unique


def _read_run_json(path: Path) -> dict[str, Any]:
    """Read a run JSON file, returning an empty object if unavailable."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_run_artifact_path(raw_path: str, run_path: Path) -> Path:
    """Resolve absolute or run-relative artifact paths."""

    path = Path(raw_path)
    if path.is_absolute():
        return path
    return run_path / path


def _selected_template_path(checks: list[dict[str, Any]], check_id: str) -> str | None:
    """Return the chosen template path from preflight checks."""

    for check in checks:
        if check.get("id") == check_id:
            path = check.get("path")
            return str(path) if path else None
    return None


def _get_com_member(value: Any, attribute: str) -> Any:
    """Return a COM attribute without letting missing members raise."""

    try:
        return getattr(value, attribute, None)
    except Exception:
        return None


def _plane_name_candidates(value: str) -> tuple[str, ...]:
    """Map stable plan plane names to common localized SolidWorks plane labels."""

    mapping = {
        "front": ("Front Plane", "前视基准面"),
        "top": ("Top Plane", "上视基准面"),
        "right": ("Right Plane", "右视基准面"),
    }
    return mapping.get(str(value).lower(), (str(value),))


def _axis_plane_pair(value: str) -> tuple[str, str] | None:
    """Return the default SolidWorks plane intersection used to create an axis."""

    mapping = {
        "x_axis": ("front", "top"),
        "y_axis": ("front", "right"),
        "z_axis": ("top", "right"),
    }
    return mapping.get(str(value).strip().lower())


def _axis_name_candidates(value: str, index: int) -> tuple[str, ...]:
    """Return likely localized SolidWorks reference-axis names."""

    clean = str(value).strip().lower()
    prefix = clean[:1].upper() if clean else ""
    return tuple(
        _unique_strings(
            [
                _safe_solidworks_atomic_name(clean),
                clean,
                f"{prefix} Axis" if prefix else None,
                f"{prefix}-Axis" if prefix else None,
                f"{prefix}轴" if prefix else None,
                f"{prefix} 轴" if prefix else None,
                f"Axis{index}",
                f"轴{index}",
                f"基准轴{index}",
            ]
        )
    )


def _create_select_data(model: Any, mark: int) -> Any | None:
    """Create SolidWorks SelectData with a mark when available."""

    try:
        selection_manager = getattr(model, "SelectionManager", None)
        create = getattr(selection_manager, "CreateSelectData", None) if selection_manager is not None else None
        select_data = create() if callable(create) else None
        if select_data is not None:
            try:
                setattr(select_data, "Mark", int(mark))
            except Exception:
                pass
        return select_data
    except Exception:
        return None


def _selected_object(model: Any, index: int, mark: int) -> Any | None:
    """Return the currently selected SolidWorks object when SelectionMgr supports it."""

    try:
        selection_manager = getattr(model, "SelectionManager", None)
        getter = getattr(selection_manager, "GetSelectedObject6", None) if selection_manager is not None else None
        if callable(getter):
            return getter(index, mark)
    except Exception:
        return None
    return None


def _set_sketch_segment_construction(segment: Any) -> bool:
    """Mark a sketch segment as construction geometry when SolidWorks exposes it."""

    if segment is None:
        return False
    for attribute in ("ConstructionGeometry", "ForConstruction"):
        try:
            setattr(segment, attribute, True)
            return True
        except Exception:
            continue
    for method_name in ("SetConstructionGeometry", "SetForConstruction"):
        method = getattr(segment, method_name, None)
        if not callable(method):
            continue
        try:
            method(True)
            return True
        except Exception:
            continue
    return False


def _atomic_pattern_direction_vector(value: Any) -> tuple[float, float] | None:
    """Return a normalized 2D direction vector for controlled atomic pattern fallbacks."""

    if isinstance(value, str):
        mapping = {
            "x_axis": (1.0, 0.0),
            "+x_axis": (1.0, 0.0),
            "-x_axis": (-1.0, 0.0),
            "y_axis": (0.0, 1.0),
            "+y_axis": (0.0, 1.0),
            "-y_axis": (0.0, -1.0),
        }
        return mapping.get(value.strip().lower())
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            x = float(value[0])
            y = float(value[1])
        except (TypeError, ValueError):
            return None
        length = math.hypot(x, y)
        if length <= 0:
            return None
        return (x / length, y / length)
    return None


def _select_by_id(
    model: Any,
    names: tuple[str, ...],
    object_type: str,
    x: float = 0,
    y: float = 0,
    z: float = 0,
    append: bool = False,
    mark: int = 0,
    select_option: int = 0,
) -> bool:
    """Select an entity by trying localized names with a pywin32 COM null callout."""

    import pythoncom
    import win32com.client

    callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
    for name in names:
        try:
            if model.Extension.SelectByID2(
                name,
                object_type,
                x,
                y,
                z,
                append,
                mark,
                callout,
                select_option,
            ):
                return True
        except Exception:
            continue
    return False


def _flat_pattern_alignment_variants() -> list[Any]:
    """Return alignment argument variants tolerated by different pywin32 COM bindings."""

    alignment = [0.0] * 12
    variants: list[Any] = [tuple(alignment), alignment]
    try:
        import pythoncom
        import win32com.client

        variants.insert(0, win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, alignment))
    except Exception:
        pass
    return variants


def _holewizard_thread_size(thread_spec: str) -> str:
    """Return the SolidWorks HoleWizard metric size string for the supported thread."""

    normalized = str(thread_spec).upper()
    if normalized not in ISO_METRIC_COARSE_THREADS:
        raise ValueError(f"Unsupported ISO metric coarse thread for MVP macro fallback: {thread_spec}")
    return normalized


def _holewizard_tapped_hole_parameters(thread_spec: str, diameter_m: float, depth_m: float) -> tuple[Any, ...]:
    """Return the fixed HoleWizard5 argument list for an ISO metric through tapped hole."""

    return (
        SW_WZD_TAP,
        SW_STANDARD_ISO,
        SW_STANDARD_ISO_TAPPED_HOLE,
        _holewizard_thread_size(thread_spec),
        SW_END_COND_THROUGH_ALL,
        diameter_m,
        depth_m,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        "",
        False,
        False,
        False,
        False,
        False,
        False,
    )


def _render_holewizard_macro(
    hole_points: list[list[float]] | list[tuple[float, float]],
    thread_spec: str,
    depth: float,
    plan: ModelPlan,
    result_path: Path,
) -> str:
    """Render a locked-down VBA macro for the current mounting-plate MVP only."""

    if plan.units != "mm":
        raise ValueError(f"Controlled macro fallback only supports mm plans, got {plan.units!r}.")
    thread_info = ISO_METRIC_COARSE_THREADS[_holewizard_thread_size(thread_spec)]
    if len(hole_points) != 4:
        raise ValueError(f"Controlled macro fallback only supports four-corner holes, got {len(hole_points)} points.")

    points_vba = ", ".join(
        f"Array({_vba_float(_to_meters(point[0], plan.units))}, {_vba_float(_to_meters(point[1], plan.units))})"
        for point in hole_points
    )
    diameter_m = _to_meters(thread_info["tap_drill_diameter"], plan.units)
    depth_m = _to_meters(depth, plan.units)
    top_z_m = _to_meters(depth + 0.1, plan.units)
    ray_radius_m = _to_meters(max(depth, 1), plan.units)
    size = _vba_string(_holewizard_thread_size(thread_spec))
    result_literal = _vba_string(str(result_path))
    thread_literal = _holewizard_thread_size(thread_spec)
    return f'''Attribute VB_Name = "HoleWizardFallback"
Option Explicit

Const swWzdTap As Long = {SW_WZD_TAP}
Const swStandardISO As Long = {SW_STANDARD_ISO}
Const swStandardISOTappedHole As Long = {SW_STANDARD_ISO_TAPPED_HOLE}
Const swEndCondThroughAll As Long = {SW_END_COND_THROUGH_ALL}

Sub main()
    Dim swApp As Object
    Dim swModel As Object
    Dim swFeatMgr As Object
    Dim swFeature As Object
    Dim points As Variant
    Dim point As Variant
    Dim created As Long
    Dim status As Boolean
    Dim resultFile As String

    resultFile = {result_literal}
    On Error GoTo Fail
    Set swApp = Application.SldWorks
    Set swModel = swApp.ActiveDoc
    If swModel Is Nothing Then Err.Raise vbObjectError + 1000, , "No active SolidWorks part document."
    Set swFeatMgr = swModel.FeatureManager
    points = Array({points_vba})

    For Each point In points
        swModel.ClearSelection2 True
        status = swModel.Extension.SelectByRay(CDbl(point(0)), CDbl(point(1)), {_vba_float(top_z_m)}, 0#, 0#, -1#, {_vba_float(ray_radius_m)}, 2, False, 0, 0)
        If Not status Then Err.Raise vbObjectError + 1001, , "Could not select top face at " & CStr(point(0)) & "," & CStr(point(1))
        Set swFeature = swFeatMgr.HoleWizard5(swWzdTap, swStandardISO, swStandardISOTappedHole, {size}, swEndCondThroughAll, _
            {_vba_float(diameter_m)}, {_vba_float(depth_m)}, -1#, -1#, -1#, -1#, -1#, -1#, -1#, -1#, -1#, -1#, -1#, -1#, -1#, _
            "", False, False, False, False, False, False)
        If swFeature Is Nothing Then Err.Raise vbObjectError + 1002, , "HoleWizard5 returned no feature."
        created = created + 1
    Next

    swModel.ClearSelection2 True
    WriteResult resultFile, "{{""ok"": true, ""method"": ""macro_fallback"", ""thread_spec"": ""{thread_literal}"", ""hole_count"": " & CStr(created) & "}}"
    Exit Sub

Fail:
    WriteResult resultFile, "{{""ok"": false, ""error"": """ & JsonEscape(Err.Description) & """, ""number"": " & CStr(Err.Number) & "}}"
End Sub

Sub WriteResult(ByVal path As String, ByVal text As String)
    Dim handle As Integer
    handle = FreeFile
    Open path For Output As #handle
    Print #handle, text
    Close #handle
End Sub

Function JsonEscape(ByVal text As String) As String
    text = Replace(text, "\\", "\\\\")
    text = Replace(text, Chr(34), "\\" & Chr(34))
    JsonEscape = text
End Function
'''


def _vba_float(value: float) -> str:
    """Render a Python float as a VBA decimal literal."""

    return f"{float(value):.12g}"


def _vba_string(value: str) -> str:
    """Render a safe VBA string literal."""

    return '"' + value.replace('"', '""') + '"'


def _four_corner_hole_points(length: float, width: float, edge_offset: float) -> list[list[float]]:
    """Return four centered-coordinate hole points for a rectangular plate."""

    x_value = (length / 2) - edge_offset
    y_value = (width / 2) - edge_offset
    if x_value <= 0 or y_value <= 0:
        raise RuntimeError("edge_offset leaves no valid space for four-corner holes")
    return [
        [-x_value, -y_value],
        [x_value, -y_value],
        [x_value, y_value],
        [-x_value, y_value],
    ]


def _read_model_bounding_box(model: Any) -> dict[str, Any]:
    """Read body count and bounding box from the active SolidWorks part."""

    attempts: list[dict[str, Any]] = []
    body_count = 0
    body_boxes: list[list[float]] = []
    try:
        bodies = model.GetBodies2(SW_SOLID_BODY, False)
        if bodies is None:
            bodies = []
        if not isinstance(bodies, (list, tuple)):
            bodies = [bodies]
        for body in bodies:
            if body is None:
                continue
            body_count += 1
            try:
                box = _coerce_bounding_box(body.GetBodyBox())
                if box is not None:
                    body_boxes.append(box)
            except Exception as exc:
                attempts.append({"method": "Body2.GetBodyBox", "ok": False, "error": str(exc)})
        attempts.append(
            {
                "method": "PartDoc.GetBodies2",
                "ok": True,
                "body_count": body_count,
                "body_box_count": len(body_boxes),
            }
        )
    except Exception as exc:
        attempts.append({"method": "PartDoc.GetBodies2", "ok": False, "error": str(exc)})

    if body_boxes:
        return {
            "status": "read",
            "method": "GetBodies2.GetBodyBox",
            "body_count": body_count,
            "bbox_m": _combine_bounding_boxes(body_boxes),
            "attempts": attempts,
        }

    for method_name in ("GetPartBox", "GetBox"):
        try:
            method = getattr(model, method_name)
            box = _coerce_bounding_box(method(True) if method_name == "GetPartBox" else method())
            attempts.append({"method": f"ModelDoc2.{method_name}", "ok": box is not None})
            if box is not None:
                return {
                    "status": "read",
                    "method": method_name,
                    "body_count": body_count,
                    "bbox_m": box,
                    "attempts": attempts,
                }
        except Exception as exc:
            attempts.append({"method": f"ModelDoc2.{method_name}", "ok": False, "error": str(exc)})

    return {
        "status": "geometry_readback_failed",
        "body_count": body_count,
        "failure_reason": "Could not read a SolidWorks part bounding box.",
        "attempts": attempts,
    }


def _solid_body_count(model: Any) -> int:
    """Return the number of solid bodies in the active part document."""

    try:
        bodies = model.GetBodies2(SW_SOLID_BODY, False)
    except Exception:
        return 0
    if bodies is None:
        return 0
    if isinstance(bodies, (list, tuple)):
        return len([body for body in bodies if body is not None])
    return 1


def _resolve_weldment_profile(params: dict[str, Any]) -> dict[str, Any]:
    """Resolve the local SolidWorks weldment profile path for a controlled frame."""

    profile = params["profile"]
    requested = str(profile.get("profile_path") or "").strip()
    candidates: list[dict[str, Any]] = []
    if requested:
        path = Path(requested).expanduser()
        candidates.append({"source": "plan.profile_path", "path": path_to_string(path), "exists": path.exists()})
        if path.exists() and path.suffix.lower() == ".sldlfp":
            return {"path": path_to_string(path), "source": "plan.profile_path", "candidates": candidates}

    profile_file = "square tube.sldlfp"
    for base in _common_weldment_profile_roots():
        for relative in (
            Path("ansi inch") / profile_file,
            Path("ansi") / "tube square.sldlfp",
            Path("ansi") / "square hss.sldlfp",
        ):
            path = base / relative
            candidates.append({"source": "common_weldment_profile_root", "path": path_to_string(path), "exists": path.exists()})
            if path.exists():
                return {"path": path_to_string(path), "source": "common_weldment_profile_root", "candidates": candidates}
    return {
        "path": None,
        "source": None,
        "candidates": candidates,
        "failure_reason": "No square-tube SolidWorks weldment profile .sldlfp was found.",
    }


def _common_weldment_profile_roots() -> list[Path]:
    """Return common SolidWorks weldment profile roots for local production runs."""

    return [
        Path("D:/Program Files/SOLIDWORKS Corp/SOLIDWORKS/data/weldment profiles"),
        Path("C:/Program Files/SOLIDWORKS Corp/SOLIDWORKS/data/weldment profiles"),
        Path("C:/ProgramData/SOLIDWORKS/SOLIDWORKS 2022/weldment profiles"),
        Path("C:/ProgramData/SOLIDWORKS/SOLIDWORKS 2023/weldment profiles"),
        Path("C:/ProgramData/SOLIDWORKS/SOLIDWORKS 2024/weldment profiles"),
        Path("C:/ProgramData/SOLIDWORKS/SOLIDWORKS 2025/weldment profiles"),
        Path("C:/ProgramData/SOLIDWORKS/SOLIDWORKS 2026/weldment profiles"),
    ]


def _weldment_cut_list_rows(params: dict[str, Any], plan: ModelPlan) -> list[dict[str, Any]]:
    """Build controlled cut-list rows from verified weldment parameters."""

    profile = params["profile"]
    material = _material_from_plan(plan)
    return [
        {
            "item": 1,
            "member_id": "long_members",
            "description": "Horizontal square-tube frame members",
            "quantity": 2,
            "length_mm": round(float(params["centerline_length"]), 6),
            "profile": profile["size"],
            "material": material,
        },
        {
            "item": 2,
            "member_id": "short_members",
            "description": "Vertical square-tube frame members",
            "quantity": 2,
            "length_mm": round(float(params["centerline_width"]), 6),
            "profile": profile["size"],
            "material": material,
        },
    ]


def _material_from_plan(plan: ModelPlan) -> str:
    """Return the final requested material for reporting rows."""

    material = "Plain Carbon Steel"
    for operation in plan.operations:
        if operation.op == "assign_material":
            material = str(operation.parameters.get("material") or material)
    return material


def _controlled_model_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the active controlled workflow."""

    if center_hole_flange_parameters_from_plan(plan) is not None:
        return _center_hole_flange_geometry_result(plan, measured)
    if center_hole_plate_parameters_from_plan(plan) is not None:
        return _center_hole_plate_geometry_result(plan, measured)
    if bracket_parameters_from_plan(plan) is not None:
        return _bracket_geometry_result(plan, measured)
    if slotted_array_plate_parameters_from_plan(plan) is not None:
        return _slotted_array_plate_geometry_result(plan, measured)
    if end_cap_parameters_from_plan(plan) is not None:
        return _end_cap_geometry_result(plan, measured)
    if mounting_block_parameters_from_plan(plan) is not None:
        return _mounting_block_geometry_result(plan, measured)
    if shaft_parameters_from_plan(plan) is not None:
        return _shaft_geometry_result(plan, measured)
    if sheet_metal_base_flange_parameters_from_plan(plan) is not None:
        return _sheet_metal_base_flange_geometry_result(plan, measured)
    if weldment_frame_parameters_from_plan(plan) is not None:
        return _weldment_frame_geometry_result(plan, measured)
    if static_simulation_parameters_from_plan(plan) is not None:
        return _static_simulation_geometry_result(plan, measured)
    if washer_parameters_from_plan(plan) is not None:
        return _washer_geometry_result(plan, measured)
    if sleeve_parameters_from_plan(plan) is not None:
        return _sleeve_geometry_result(plan, measured)
    if _is_atomic_model_plan(plan):
        return _atomic_model_geometry_result(measured)
    return _mounting_plate_geometry_result(plan, measured)


def _atomic_model_geometry_result(measured: dict[str, Any]) -> dict[str, Any]:
    """Verify minimal real geometry evidence for a staged atomic model."""

    bbox = measured.get("bbox_m")
    dimensions_m: list[float] = []
    if isinstance(bbox, list) and len(bbox) == 6:
        try:
            dimensions_m = [
                abs(float(bbox[3]) - float(bbox[0])),
                abs(float(bbox[4]) - float(bbox[1])),
                abs(float(bbox[5]) - float(bbox[2])),
            ]
        except (TypeError, ValueError):
            dimensions_m = []
    body_count = int(measured.get("body_count") or 0)
    checks = {
        "body_count_positive": body_count >= 1,
        "bbox_dimensions_positive": bool(dimensions_m) and all(value > 0 for value in dimensions_m),
    }
    status = "geometry_verified" if measured.get("status") == "read" and all(checks.values()) else "geometry_mismatch"
    failure_reason = None
    if measured.get("status") != "read":
        status = "geometry_readback_failed"
        failure_reason = measured.get("failure_reason") or "Could not read atomic model bounding box."
    elif not checks["body_count_positive"]:
        failure_reason = "Atomic model has no SolidWorks solid bodies."
    elif not checks["bbox_dimensions_positive"]:
        failure_reason = "Atomic model bounding box dimensions are not all positive."
    return {
        "status": status,
        "workflow": "atomic_model",
        "method": measured.get("method"),
        "body_count": body_count,
        "bbox_m": bbox,
        "measured_dimensions_mm": sorted(round(value * 1000, 6) for value in dimensions_m),
        "checks": checks,
        "attempts": measured.get("attempts", []),
        "failure_reason": failure_reason,
    }


def _mounting_plate_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled mounting-plate parameters."""

    params = mounting_plate_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_mounting_plate operation was found."}
    expected_mm = sorted([float(params["length"]), float(params["width"]), float(params["thickness"])])
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the mounting-plate plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _center_hole_flange_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled center-hole flange parameters."""

    params = center_hole_flange_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_center_hole_flange operation was found."}
    expected_mm = sorted(
        [
            float(params["outer_diameter"]),
            float(params["outer_diameter"]),
            float(params["thickness"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the center-hole flange plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _bracket_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled bracket parameters."""

    params = bracket_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_bracket operation was found."}
    expected_mm = sorted(
        [
            float(params["base_length"]),
            float(params["base_width"]),
            float(params["upright_height"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "expected_hole_diameter_mm": float(params["hole_diameter"]),
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the bracket plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _slotted_array_plate_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled slotted-array plate parameters."""

    params = slotted_array_plate_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_slotted_array_plate operation was found."}
    expected_mm = sorted(
        [
            float(params["length"]),
            float(params["width"]),
            float(params["thickness"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "expected_slot_length_mm": float(params["slot_length"]),
        "expected_slot_width_mm": float(params["slot_width"]),
        "expected_hole_diameter_mm": float(params["hole_diameter"]),
        "expected_hole_count": int(params["hole_rows"]) * int(params["hole_columns"]),
        "expected_hole_rows": int(params["hole_rows"]),
        "expected_hole_columns": int(params["hole_columns"]),
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the slotted-array plate plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _end_cap_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled end-cap parameters."""

    params = end_cap_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_end_cap operation was found."}
    expected_mm = sorted(
        [
            float(params["outer_diameter"]),
            float(params["outer_diameter"]),
            float(params["thickness"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "expected_center_hole_diameter_mm": float(params["center_hole_diameter"]),
        "expected_bolt_circle_diameter_mm": float(params["bolt_circle_diameter"]),
        "expected_bolt_hole_diameter_mm": float(params["bolt_hole_diameter"]),
        "expected_bolt_hole_count": int(params["bolt_hole_count"]),
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the end-cap plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _center_hole_plate_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled center-hole plate parameters."""

    params = center_hole_plate_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_center_hole_plate operation was found."}
    expected_mm = sorted(
        [
            float(params["length"]),
            float(params["width"]),
            float(params["thickness"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the center-hole plate plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _washer_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled washer parameters."""

    params = washer_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_washer operation was found."}
    expected_mm = sorted(
        [
            float(params["outer_diameter"]),
            float(params["outer_diameter"]),
            float(params["thickness"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the washer plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _sheet_metal_base_flange_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to controlled sheet-metal parameters."""

    params = sheet_metal_base_flange_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_sheet_metal_base_flange operation was found."}
    expected_mm = sorted(
        [
            float(params["length"]),
            float(params["width"]),
            float(params["thickness"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "sheet_metal_thickness_mm": float(params["thickness"]),
        "bend_radius_mm": float(params["bend_radius"]),
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the sheet-metal base-flange plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _weldment_frame_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to controlled weldment frame parameters."""

    params = weldment_frame_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_weldment_frame operation was found."}
    expected_mm = sorted(
        [
            float(params["length"]),
            float(params["width"]),
            float(params["profile_outer_width"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "profile_outer_width_mm": float(params["profile_outer_width"]),
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.75, expected * 0.006)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 4 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 4:
        failure_reason = "SolidWorks reported fewer than four weldment member bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the weldment frame plan."
    return {
        **base_result,
        "status": status,
        "workflow": "weldment_frame",
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _static_simulation_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled simulation beam."""

    params = static_simulation_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No run_static_simulation operation was found."}
    expected_mm = sorted([float(params["length"]), float(params["width"]), float(params["height"])])
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "workflow": "static_simulation",
        "expected_dimensions_mm": expected_mm,
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the static simulation beam plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _mounting_block_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled mounting-block parameters."""

    params = mounting_block_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_mounting_block operation was found."}
    expected_mm = sorted(
        [
            float(params["length"]),
            float(params["width"]),
            float(params["height"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the mounting block plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _shaft_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled shaft parameters."""

    params = shaft_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_shaft operation was found."}
    expected_mm = sorted(
        [
            float(params["diameter"]),
            float(params["diameter"]),
            float(params["length"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the shaft plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _sleeve_geometry_result(plan: ModelPlan, measured: dict[str, Any]) -> dict[str, Any]:
    """Compare SolidWorks bounding-box dimensions to the controlled sleeve parameters."""

    params = sleeve_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_sleeve operation was found."}
    expected_mm = sorted(
        [
            float(params["outer_diameter"]),
            float(params["outer_diameter"]),
            float(params["length"]),
        ]
    )
    base_result: dict[str, Any] = {
        "method": measured.get("method"),
        "body_count": measured.get("body_count", 0),
        "expected_dimensions_mm": expected_mm,
        "attempts": measured.get("attempts", []),
    }
    bbox = _coerce_bounding_box(measured.get("bbox_m"))
    if measured.get("status") != "read" or bbox is None:
        return {
            **base_result,
            "status": "geometry_readback_failed",
            "failure_reason": measured.get("failure_reason") or "SolidWorks returned no readable bounding box.",
        }

    measured_mm = sorted([(bbox[index + 3] - bbox[index]) * 1000 for index in range(3)])
    checks = []
    for index, (expected, actual) in enumerate(zip(expected_mm, measured_mm, strict=True)):
        error = abs(actual - expected)
        tolerance = max(0.5, expected * 0.005)
        checks.append(
            {
                "axis_index": index,
                "expected_mm": expected,
                "measured_mm": actual,
                "error_mm": error,
                "tolerance_mm": tolerance,
                "ok": error <= tolerance,
            }
        )
    body_count = int(measured.get("body_count") or 0)
    failed_checks = [check for check in checks if not check["ok"]]
    status = "geometry_verified" if body_count >= 1 and not failed_checks else "geometry_mismatch"
    failure_reason = None
    if body_count < 1:
        failure_reason = "SolidWorks reported no solid bodies."
    elif failed_checks:
        failure_reason = "Bounding-box dimensions differ from the sleeve plan."
    return {
        **base_result,
        "status": status,
        "bbox_min_m": bbox[:3],
        "bbox_max_m": bbox[3:],
        "measured_dimensions_mm": measured_mm,
        "dimension_checks": checks,
        "max_error_mm": max((check["error_mm"] for check in checks), default=None),
        "failure_reason": failure_reason,
    }


def _has_controlled_geometry_operation(plan: ModelPlan) -> bool:
    """Return whether the plan contains a controlled high-level geometry operation."""

    return any(
        operation.op in {
            "create_mounting_plate",
            "create_center_hole_flange",
            "create_center_hole_plate",
            "create_bracket",
            "create_end_cap",
            "create_mounting_block",
            "create_shaft",
            "create_sheet_metal_base_flange",
            "create_weldment_frame",
            "run_static_simulation",
            "create_washer",
            "create_sleeve",
            "create_slotted_array_plate",
        }
        for operation in plan.operations
    )


def _is_atomic_model_plan(plan: ModelPlan) -> bool:
    """Return whether a plan came from the staged atomic session protocol."""

    return str(plan.metadata.get("solidworks_mcp_workflow") or "") == "atomic_model_session"


def _is_atomic_model_without_holes(plan: ModelPlan) -> bool:
    """Return whether an atomic session has no hole operation requiring callouts."""

    return _is_atomic_model_plan(plan) and not any(operation.op == "hole" for operation in plan.operations)


def _parse_body_mass_properties(values: list[float], density: float) -> dict[str, float] | None:
    """Parse Body2.GetMassProperties values using density consistency."""

    positives = [float(value) for value in values if float(value) > 0]
    if not positives or density <= 0:
        return None
    candidates: list[dict[str, float]] = []
    for volume in positives:
        expected_mass = volume * density
        for mass in positives:
            if expected_mass <= 0:
                continue
            relative_error = abs(mass - expected_mass) / expected_mass
            if relative_error <= 0.15:
                candidates.append({"volume_m3": volume, "mass_kg": mass, "score": relative_error})
    if candidates:
        best = min(candidates, key=lambda item: item["score"])
        surface_area = _surface_area_candidate(values, best["volume_m3"], best["mass_kg"])
        if surface_area is not None:
            best["surface_area_m2"] = surface_area
        best.pop("score", None)
        return best

    if len(values) >= 4 and values[3] > 0:
        volume = float(values[3])
        return {"volume_m3": volume, "mass_kg": volume * density}
    return None


def _surface_area_candidate(values: list[float], volume: float, mass: float) -> float | None:
    """Return a plausible surface area from mass-property values."""

    for value in values:
        if value <= 0:
            continue
        if abs(value - volume) <= max(volume * 0.01, 1e-12):
            continue
        if abs(value - mass) <= max(mass * 0.01, 1e-9):
            continue
        if 1e-6 <= value <= 10:
            return float(value)
    return None


def _coerce_bounding_box(raw_box: Any) -> list[float] | None:
    """Convert a COM bounding-box value to six floats."""

    if raw_box is None:
        return None
    values = _numeric_sequence(raw_box)
    if len(values) < 6:
        return None
    return [float(value) for value in values[:6]]


def _combine_bounding_boxes(boxes: list[list[float]]) -> list[float]:
    """Return the extents that contain all six-value bounding boxes."""

    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        min(box[2] for box in boxes),
        max(box[3] for box in boxes),
        max(box[4] for box in boxes),
        max(box[5] for box in boxes),
    ]


def _to_meters(value: Any, units: str) -> float:
    """Convert a numeric plan value to SolidWorks internal meters."""

    scale = {
        "mm": 0.001,
        "cm": 0.01,
        "m": 1.0,
        "inch": 0.0254,
        "ft": 0.3048,
    }[units]
    return float(value) * scale


def _to_radians(degrees: Any) -> float:
    """Convert degrees to radians for SolidWorks feature APIs."""

    return float(degrees) * 3.141592653589793 / 180.0


def _solidworks_suffix(file_format: str) -> str:
    """Normalize requested formats to SolidWorks-friendly file suffixes."""

    mapping = {
        "sldprt": "sldprt",
        "sldasm": "sldasm",
        "slddrw": "slddrw",
        "csv": "csv",
        "pdf": "pdf",
        "dwg": "dwg",
        "dxf": "dxf",
        "step": "step",
        "stl": "stl",
        "iges": "igs",
        "x_t": "x_t",
        "x_b": "x_b",
    }
    return mapping[file_format]


def _is_bom_assembly_plan(plan: ModelPlan) -> bool:
    """Return whether the plan creates a controlled assembly."""

    return any(operation.op == "create_bom_assembly" for operation in plan.operations)


def _is_sheet_metal_base_flange_plan(plan: ModelPlan) -> bool:
    """Return whether the plan creates a controlled sheet-metal base flange."""

    return any(operation.op == "create_sheet_metal_base_flange" for operation in plan.operations)


def _is_weldment_frame_plan(plan: ModelPlan) -> bool:
    """Return whether the plan creates a controlled weldment frame."""

    return any(operation.op == "create_weldment_frame" for operation in plan.operations)


def _is_static_simulation_plan(plan: ModelPlan) -> bool:
    """Return whether the plan creates a controlled static simulation study."""

    return any(operation.op == "run_static_simulation" for operation in plan.operations)
