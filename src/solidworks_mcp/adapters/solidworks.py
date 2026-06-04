"""Windows SolidWorks COM adapter.

The implementation keeps direct COM calls narrow and guarded.  Complex feature
creation varies across SolidWorks versions, so the MVP records exact failure
context and leaves room for a future VBA macro fallback for methods whose COM
signatures are unstable from Python.
"""

from __future__ import annotations

from pathlib import Path
import platform
from time import perf_counter
from typing import Any

from solidworks_mcp.adapters.base import CADAdapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.schemas import DrawingProfile, ModelOperation, ModelPlan, StepResult, path_to_string, safe_output_name


SW_DOC_PART = 1
SW_DOC_DRAWING = 3
SW_SAVE_AS_CURRENT_VERSION = 0
SW_SAVE_AS_OPTIONS_SILENT = 1
SW_END_COND_BLIND = 0
SW_END_COND_THROUGH_ALL = 1

ISO_METRIC_COARSE_THREADS = {
    "M3": {"tap_drill_diameter": 2.5, "pitch": 0.5},
    "M4": {"tap_drill_diameter": 3.3, "pitch": 0.7},
    "M5": {"tap_drill_diameter": 4.2, "pitch": 0.8},
    "M6": {"tap_drill_diameter": 5.0, "pitch": 1.0},
    "M8": {"tap_drill_diameter": 6.8, "pitch": 1.25},
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
        self._drawing_view_status = "not_requested"
        self._drawing_annotation_status = "not_requested"
        self._active_part_path: Path | None = None

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
        revision = getattr(self._sw, "RevisionNumber", lambda: "unknown")()
        return {
            "adapter": self.name,
            "connected": True,
            "revision": revision,
            "visible": self._config.visible,
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
        self._drawing_view_status = "not_requested"
        self._drawing_annotation_status = "not_requested"
        self._active_part_path = None

        if self._config.part_template:
            self._model = sw.NewDocument(self._config.part_template, 0, 0, 0)
        else:
            self._model = sw.NewPart()

        if self._model is None:
            raise RuntimeError("SolidWorks did not create a part document.")

        return {
            "workspace": path_to_string(self._workspace),
            "document": self._active_title(),
        }

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

        template = profile.template_path or self._config.drawing_template
        if template:
            self._drawing = sw.NewDocument(template, 0, 0, 0)
        else:
            self._drawing = sw.NewDrawing()

        if self._drawing is None:
            raise RuntimeError("SolidWorks did not create a drawing document.")

        part_path = self._ensure_part_saved(plan)
        view_status = self._create_standard_drawing_views(part_path)
        callout_status = self._try_insert_thread_callouts()
        self._drawing_view_status = view_status
        self._drawing_annotation_status = callout_status
        if view_status != "created":
            self._warnings.append(f"drawing_views:{view_status}")
        if callout_status != "created":
            self._warnings.append(f"drawing_thread_callouts:{callout_status}")

        drawing_path = workspace / f"{safe_output_name(plan.name)}.slddrw"
        self._save_as(self._drawing, drawing_path)
        return {"slddrw": path_to_string(drawing_path)}

    def export_outputs(self, plan: ModelPlan, formats: tuple[str, ...]) -> dict[str, str]:
        """Export part and drawing documents to the requested formats."""

        workspace = self._require_workspace() / "exports"
        workspace.mkdir(parents=True, exist_ok=True)
        model = self._require_model()
        outputs: dict[str, str] = {}
        base_name = safe_output_name(plan.name)
        for file_format in formats:
            suffix = _solidworks_suffix(file_format)
            target_path = workspace / f"{base_name}.{suffix}"
            document = self._drawing if file_format in {"pdf", "dwg", "dxf", "slddrw"} and self._drawing else model
            self._save_as(document, target_path)
            outputs[file_format] = path_to_string(target_path)
        return outputs

    def inspect_active_model(self) -> dict[str, Any]:
        """Return a compact feature summary without reading the whole COM tree."""

        return {
            "adapter": self.name,
            "active_document": self._active_title(),
            "feature_count": len(self._features),
            "features": list(self._features),
            "thread_model_status": self._thread_model_status,
            "drawing_view_status": self._drawing_view_status,
            "drawing_annotation_status": self._drawing_annotation_status,
            "fallbacks": list(self._fallbacks),
            "warnings": list(self._warnings),
        }

    def capture_previews(self, plan: ModelPlan) -> dict[str, str]:
        """Save standard-view preview images when SolidWorks exposes SaveAs support."""

        workspace = self._require_workspace() / "previews"
        workspace.mkdir(parents=True, exist_ok=True)
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

    def _op_create_sketch(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a sketch on a named plane and draw MVP-supported entities."""

        model = self._require_model()
        params = operation.parameters
        plane_name = _plane_name(params["plane"])
        model.Extension.SelectByID2(plane_name, "PLANE", 0, 0, 0, False, 0, None, 0)
        model.SketchManager.InsertSketch(True)

        for entity in params["entities"]:
            self._draw_entity(entity, plan)

        model.SketchManager.InsertSketch(True)
        return {"plane": plane_name, "entity_count": len(params["entities"])}

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

    def _op_extrude(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a boss extrude from the currently selected or latest sketch."""

        depth_m = _to_meters(operation.parameters["depth"], plan.units)
        feature = self._require_model().FeatureManager.FeatureExtrusion2(
            True, False, False, 0, 0, depth_m, 0, False, False, False, False,
            0, 0, False, False, False, False, True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("FeatureExtrusion2 returned no feature.")
        return {"depth_m": depth_m}

    def _op_cut(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create a cut extrude from the currently selected or latest sketch."""

        depth_m = _to_meters(operation.parameters["depth"], plan.units)
        feature = self._require_model().FeatureManager.FeatureCut4(
            True, False, False, 0, 0, depth_m, 0, False, False, False, False,
            0, 0, False, False, False, False, False, True, True, True, True,
            False, 0, 0, False, False
        )
        if feature is None:
            raise RuntimeError("FeatureCut4 returned no feature.")
        return {"depth_m": depth_m}

    def _op_hole(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Create one or more threaded holes, degrading to sketch cuts if needed."""

        params = operation.parameters
        positions = params.get("positions") or [params["position"]]
        thread_spec = str(params.get("thread_spec", "M6")).upper()
        depth = float(params.get("depth", 0))
        result = self._create_threaded_holes_or_fallback(positions, thread_spec, depth, plan)
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

    def _op_linear_pattern(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Record a linear pattern request until reference selection is formalized."""

        raise NotImplementedError("linear_pattern needs explicit seed feature and direction reference selection")

    def _op_circular_pattern(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Record a circular pattern request until axis selection is formalized."""

        raise NotImplementedError("circular_pattern needs explicit seed feature and axis reference selection")

    def _op_assign_material(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Set material metadata when a material database name is provided."""

        material = str(operation.parameters["material"])
        self._require_model().SetMaterialPropertyName2("", "", material)
        return {"material": material}

    def _op_make_drawing(self, operation: ModelOperation, plan: ModelPlan) -> dict[str, Any]:
        """Defer drawing creation to the dedicated drawing stage."""

        return {"deferred_to": "generate_drawing"}

    def _create_rounded_plate_body(
        self,
        length: float,
        width: float,
        thickness: float,
        corner_radius: float,
        plan: ModelPlan,
    ) -> None:
        """Sketch a centered rounded rectangle and extrude it to plate thickness."""

        model = self._require_model()
        model.Extension.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0, None, 0)
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        self._draw_rounded_rectangle(length, width, corner_radius, plan)
        sketch.InsertSketch(True)

        depth_m = _to_meters(thickness, plan.units)
        feature = model.FeatureManager.FeatureExtrusion2(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            True, True, True, 0, 0, False
        )
        if feature is None:
            raise RuntimeError("Mounting plate base extrusion failed.")

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
        holewizard_result = self._try_holewizard_threaded_holes(hole_points, thread_spec, depth, plan)
        if holewizard_result["ok"]:
            self._thread_model_status = "holewizard_threaded_hole"
            return holewizard_result

        self._fallbacks.append({"from": "HoleWizard5", "to": "macro_or_geometry", "reason": holewizard_result["message"]})
        if self._config.macro_fallback_enabled:
            macro_result = self._try_holewizard_macro_fallback(hole_points, thread_spec, depth, plan)
            if macro_result["ok"]:
                self._thread_model_status = "macro_threaded_hole"
                return macro_result
            self._fallbacks.append({"from": "HoleWizard macro", "to": "geometry_cut", "reason": macro_result["message"]})

        cut_result = self._create_geometry_cut_holes(hole_points, thread_spec, depth, plan)
        self._thread_model_status = "degraded_geometry_only"
        return {
            "ok": True,
            "method": "geometry_cut_fallback",
            "thread_model_status": self._thread_model_status,
            "holewizard_error": holewizard_result["message"],
            "details": cut_result,
        }

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
            model = self._require_model()
            self._select_top_face_for_points(hole_points, depth, plan)
            thread_info = ISO_METRIC_COARSE_THREADS[thread_spec]
            diameter_m = _to_meters(thread_info["tap_drill_diameter"], plan.units)
            depth_m = _to_meters(depth + 1, plan.units)
            started_at = perf_counter()
            try:
                feature = model.FeatureManager.HoleWizard5(
                    1, 0, 0, thread_spec, SW_END_COND_THROUGH_ALL,
                    diameter_m, depth_m, 0, 0, 0, 0, 0, 0, 0, False, True, True, True,
                    False, False, False, False, "", False, False, False
                )
                self.record_com_call(
                    "FeatureManager.HoleWizard5",
                    {"thread_spec": thread_spec, "diameter_m": diameter_m, "depth_m": depth_m},
                    result=feature,
                    started_at=started_at,
                )
            except Exception as exc:
                self.record_com_call(
                    "FeatureManager.HoleWizard5",
                    {"thread_spec": thread_spec, "diameter_m": diameter_m, "depth_m": depth_m},
                    error=exc,
                    started_at=started_at,
                )
                raise
            if feature is None:
                return {"ok": False, "method": "holewizard5", "message": "HoleWizard5 returned no feature"}
            return {
                "ok": True,
                "method": "holewizard5",
                "thread_spec": thread_spec,
                "hole_count": len(hole_points),
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
        """Create a macro artifact and try running it when macro fallback is enabled."""

        workspace = self._require_workspace() / "macros"
        workspace.mkdir(parents=True, exist_ok=True)
        macro_path = workspace / "holewizard_fallback.bas"
        macro_path.write_text(
            "' HoleWizard fallback stub generated by solidworks-mcp.\n"
            "' The direct COM call failed; refine this macro on the Windows smoke machine.\n"
            f"' thread_spec={thread_spec}, depth={depth}, points={hole_points}, units={plan.units}\n",
            encoding="utf-8",
        )
        return {
            "ok": False,
            "method": "macro_fallback_stub",
            "message": f"Macro fallback stub written to {macro_path}; automatic macro execution is not yet enabled.",
            "macro_path": path_to_string(macro_path),
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
        model.Extension.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0, None, 0)
        sketch = model.SketchManager
        sketch.InsertSketch(True)
        for point in hole_points:
            sketch.CreateCircleByRadius(
                _to_meters(point[0], plan.units),
                _to_meters(point[1], plan.units),
                0,
                _to_meters(radius, plan.units),
            )
        sketch.InsertSketch(True)
        depth_m = _to_meters(depth + 1, plan.units)
        feature = model.FeatureManager.FeatureCut4(
            True, False, False, SW_END_COND_BLIND, 0, depth_m, 0,
            False, False, False, False, 0, 0, False, False, False, False,
            False, True, True, True, True, False, 0, 0, False, False
        )
        if feature is None:
            raise RuntimeError("Geometry fallback cut holes failed.")
        return {
            "thread_spec": thread_spec,
            "tap_drill_diameter": thread_info["tap_drill_diameter"],
            "hole_count": len(hole_points),
        }

    def _select_top_face_for_points(
        self,
        hole_points: list[list[float]] | list[tuple[float, float]],
        depth: float,
        plan: ModelPlan,
    ) -> None:
        """Best-effort semantic selection for the plate face that receives holes."""

        model = self._require_model()
        model.ClearSelection2(True)
        if not hole_points:
            raise RuntimeError("No hole points supplied for top_face selection.")
        x, y = hole_points[0]
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

    def _draw_entity(self, entity: dict[str, Any], plan: ModelPlan) -> None:
        """Draw one supported sketch entity in model units."""

        sketch = self._require_model().SketchManager
        entity_type = entity.get("type")
        if entity_type == "circle":
            center = entity["center"]
            radius = _to_meters(entity["radius"], plan.units)
            sketch.CreateCircleByRadius(
                _to_meters(center[0], plan.units),
                _to_meters(center[1], plan.units),
                0,
                radius,
            )
        elif entity_type == "rectangle":
            corner1 = entity["corner1"]
            corner2 = entity["corner2"]
            sketch.CreateCornerRectangle(
                _to_meters(corner1[0], plan.units),
                _to_meters(corner1[1], plan.units),
                0,
                _to_meters(corner2[0], plan.units),
                _to_meters(corner2[1], plan.units),
                0,
            )
        elif entity_type == "line":
            start = entity["start"]
            end = entity["end"]
            sketch.CreateLine(
                _to_meters(start[0], plan.units),
                _to_meters(start[1], plan.units),
                0,
                _to_meters(end[0], plan.units),
                _to_meters(end[1], plan.units),
                0,
            )
        else:
            raise RuntimeError(f"Unsupported sketch entity type: {entity_type}")

    def _save_as(self, document: Any, path: Path) -> None:
        """Call SaveAs3 and normalize failed saves into Python exceptions."""

        path.parent.mkdir(parents=True, exist_ok=True)
        started_at = perf_counter()
        try:
            result = document.Extension.SaveAs(
                str(path),
                SW_SAVE_AS_CURRENT_VERSION,
                SW_SAVE_AS_OPTIONS_SILENT,
                None,
                0,
                0,
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

    def _ensure_part_saved(self, plan: ModelPlan) -> Path:
        """Save the active part so drawing views can reference a stable file path."""

        if self._active_part_path is None:
            workspace = self._require_workspace() / "exports"
            workspace.mkdir(parents=True, exist_ok=True)
            self._active_part_path = workspace / f"{safe_output_name(plan.name)}.sldprt"
        self._save_as(self._require_model(), self._active_part_path)
        return self._active_part_path

    def _create_standard_drawing_views(self, part_path: Path) -> str:
        """Create front, top, right and isometric views from the saved part."""

        drawing = self._drawing
        if drawing is None:
            return "no_drawing_document"

        view_specs = (
            ("*Front", 0.18, 0.16),
            ("*Top", 0.18, 0.28),
            ("*Right", 0.34, 0.16),
            ("*Isometric", 0.34, 0.28),
        )
        created = 0
        errors: list[str] = []
        for view_name, x_position, y_position in view_specs:
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
                else:
                    errors.append(f"{view_name}:no_view")
            except Exception as exc:
                self.record_com_call(
                    "DrawingDoc.CreateDrawViewFromModelView3",
                    {"part_path": part_path, "view_name": view_name, "x": x_position, "y": y_position},
                    error=exc,
                    started_at=started_at,
                )
                errors.append(f"{view_name}:{exc}")

        if created == len(view_specs):
            return "created"
        if created > 0:
            return f"partial:{created}/{len(view_specs)}:{'; '.join(errors)}"
        return f"failed:{'; '.join(errors)}"

    def _try_insert_thread_callouts(self) -> str:
        """Attempt hole/thread callouts without blocking drawing export."""

        drawing = self._drawing
        if drawing is None:
            return "no_drawing_document"

        candidate_names = ("InsertHoleCallout", "InsertHoleTable")
        for method_name in candidate_names:
            method = getattr(drawing, method_name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if result is not False:
                    return "created"
            except Exception as exc:
                self._warnings.append(f"{method_name}:{exc}")
        return "failed_or_not_supported"

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
        try:
            return self._model.GetTitle()
        except Exception:
            return None


def _plane_name(value: str) -> str:
    """Map stable plan plane names to common SolidWorks plane labels."""

    mapping = {
        "front": "Front Plane",
        "top": "Top Plane",
        "right": "Right Plane",
    }
    return mapping.get(str(value).lower(), str(value))


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


def _solidworks_suffix(file_format: str) -> str:
    """Normalize requested formats to SolidWorks-friendly file suffixes."""

    mapping = {
        "sldprt": "sldprt",
        "slddrw": "slddrw",
        "pdf": "pdf",
        "dwg": "dwg",
        "dxf": "dxf",
        "step": "step",
        "stl": "stl",
    }
    return mapping[file_format]
