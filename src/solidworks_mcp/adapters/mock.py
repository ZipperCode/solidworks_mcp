"""Mock adapter for development without a Windows SolidWorks installation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters.base import CADAdapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.feature_graph import atomic_dimension_ids_from_metadata
from solidworks_mcp.schemas import (
    bom_assembly_parameters_from_plan,
    bracket_basic_dimension_ids_from_plan,
    bracket_parameters_from_plan,
    center_hole_flange_basic_dimension_ids_from_plan,
    center_hole_flange_parameters_from_plan,
    center_hole_plate_basic_dimension_ids_from_plan,
    center_hole_plate_parameters_from_plan,
    DrawingProfile,
    end_cap_basic_dimension_ids_from_plan,
    end_cap_parameters_from_plan,
    ModelOperation,
    ModelPlan,
    StepResult,
    mounting_block_basic_dimension_ids_from_plan,
    mounting_block_parameters_from_plan,
    mounting_plate_basic_dimension_ids_from_plan,
    mounting_plate_parameters_from_plan,
    path_to_string,
    safe_output_name,
    shaft_basic_dimension_ids_from_plan,
    shaft_parameters_from_plan,
    sheet_metal_base_flange_basic_dimension_ids_from_plan,
    sheet_metal_base_flange_parameters_from_plan,
    sleeve_basic_dimension_ids_from_plan,
    sleeve_parameters_from_plan,
    slotted_array_plate_basic_dimension_ids_from_plan,
    slotted_array_plate_parameters_from_plan,
    static_simulation_basic_dimension_ids_from_plan,
    static_simulation_parameters_from_plan,
    washer_basic_dimension_ids_from_plan,
    washer_parameters_from_plan,
    weldment_frame_basic_dimension_ids_from_plan,
    weldment_frame_parameters_from_plan,
)


class MockCADAdapter(CADAdapter):
    """Deterministic adapter that records intended CAD actions as artifacts."""

    name = "mock"

    def __init__(self, config: SolidWorksMCPConfig) -> None:
        self._config = config
        self._active_plan: ModelPlan | None = None
        self._workspace: Path | None = None
        self._features: list[dict[str, Any]] = []
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
        self._fallbacks: list[dict[str, Any]] = []
        self._warnings: list[str] = []
        self._last_hole_result: dict[str, Any] | None = None

    def connect(self) -> dict[str, Any]:
        """Return mock runtime details used by clients during local development."""

        return {
            "adapter": self.name,
            "connected": True,
            "message": "Mock adapter active. Real SolidWorks COM execution requires Windows.",
            "output_root": path_to_string(self._config.output_root),
        }

    def preflight_environment(self, plan: ModelPlan | None = None) -> dict[str, Any]:
        """Return deterministic mock preflight diagnostics."""

        checks = [
            {
                "id": "mock_adapter",
                "ok": not self._config.force_preflight_failure,
                "message": "Mock adapter preflight is forced to fail."
                if self._config.force_preflight_failure
                else "Mock adapter is ready.",
                "remediation": "Unset SOLIDWORKS_MCP_FORCE_PREFLIGHT_FAILURE."
                if self._config.force_preflight_failure
                else None,
            },
            {
                "id": "output_dir",
                "ok": True,
                "message": f"Output root is {self._config.output_root}",
                "path": path_to_string(self._config.output_root),
            },
            {
                "id": "cleanup_policy",
                "ok": self._config.close_documents_after_run,
                "message": "Run-created document cleanup is enabled."
                if self._config.close_documents_after_run
                else "SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN is disabled.",
                "remediation": None
                if self._config.close_documents_after_run
                else "Set SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN=1 before confirmed execution.",
            },
        ]
        failed = [check["id"] for check in checks if not check["ok"]]
        return {
            "ok": not failed,
            "status": "failed" if failed else "ready",
            "adapter": self.name,
            "plan_name": plan.name if plan else None,
            "checks": checks,
            "failures": failed,
        }

    def begin_transaction(self, plan: ModelPlan) -> dict[str, Any]:
        """Create an isolated artifact directory for the current plan."""

        self._active_plan = plan
        self._features = []
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
        self._fallbacks = []
        self._warnings = []
        self._last_hole_result = None
        self._workspace = getattr(self, "_run_workspace", None) or (
            self._config.output_root / safe_output_name(plan.name)
        )
        self._workspace.mkdir(parents=True, exist_ok=True)
        self.record_event("adapter.transaction", "started", {"workspace": self._workspace})
        return {
            "workspace": path_to_string(self._workspace),
            "document": f"{plan.name}.SLDPRT",
        }

    def execute_operation(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record a simulated feature for traceable dry-runs."""

        if operation.op == "create_bracket":
            return self._execute_bracket(operation, index, plan)
        if operation.op == "create_bom_assembly":
            return self._execute_bom_assembly(operation, index, plan)
        if operation.op == "create_mounting_plate":
            return self._execute_mounting_plate(operation, index, plan)
        if operation.op == "create_center_hole_flange":
            return self._execute_center_hole_flange(operation, index, plan)
        if operation.op == "create_center_hole_plate":
            return self._execute_center_hole_plate(operation, index, plan)
        if operation.op == "create_end_cap":
            return self._execute_end_cap(operation, index, plan)
        if operation.op == "create_mounting_block":
            return self._execute_mounting_block(operation, index, plan)
        if operation.op == "create_shaft":
            return self._execute_shaft(operation, index, plan)
        if operation.op == "create_sheet_metal_base_flange":
            return self._execute_sheet_metal_base_flange(operation, index, plan)
        if operation.op == "create_weldment_frame":
            return self._execute_weldment_frame(operation, index, plan)
        if operation.op == "run_static_simulation":
            return self._execute_static_simulation(operation, index, plan)
        if operation.op == "create_washer":
            return self._execute_washer(operation, index, plan)
        if operation.op == "create_sleeve":
            return self._execute_sleeve(operation, index, plan)
        if operation.op == "create_slotted_array_plate":
            return self._execute_slotted_array_plate(operation, index, plan)
        if operation.op == "assign_material":
            return self._execute_assign_material(operation, index)
        if operation.op == "set_custom_properties":
            return self._execute_set_custom_properties(operation, index)
        if operation.op == "hole":
            return self._execute_atomic_hole(operation, index)

        feature = {
            "index": index,
            "id": operation.id or f"{operation.op}_{index}",
            "op": operation.op,
            "description": operation.description,
            "parameters": operation.parameters,
        }
        self._features.append(feature)
        self._refresh_atomic_geometry_evidence(operation)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message=f"Mock executed {operation.op}",
            details=feature,
        )

    def _execute_atomic_hole(self, operation: ModelOperation, index: int) -> StepResult:
        """Record a generic atomic hole with direct callout evidence."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or f"hole_{index}",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "hole_result": {
                "ok": True,
                "method": "mock_direct_hole",
                "hole_count": 1,
                "diameter": params.get("diameter"),
                "depth": params.get("depth"),
            },
            "drawing_annotation_status": "hole_callout_created",
        }
        self._last_hole_result = feature["hole_result"]
        self._drawing_annotation_status = "hole_callout_created"
        self._drawing_annotation_result = {
            "status": "hole_callout_created",
            "view_name": "*Top",
            "selected_edge_count": 1,
            "created_callout_count": 1,
            "callout_creation_method": "add_hole_callout2",
            "direct_hole_callout_created": True,
            "attempts": [{"index": index, "selected": True, "callout_created": True}],
        }
        self._features.append(feature)
        self._refresh_atomic_geometry_evidence(operation)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed atomic hole with direct callout evidence.",
            details=feature,
        )

    def _execute_bom_assembly(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record a controlled assembly with verified BOM evidence."""

        params = bom_assembly_parameters_from_plan(plan)
        if params is None:
            raise RuntimeError("create_bom_assembly parameters could not be extracted")
        components = params["components"]
        rows = []
        item_number = 1
        for component in components:
            rows.append(
                {
                    "item": item_number,
                    "component_id": component["id"],
                    "part_number": component["part_number"],
                    "description": component["description"],
                    "quantity": component["quantity"],
                    "material": component["material"],
                }
            )
            item_number += 1
        self._assembly_result = {
            "status": "assembly_verified",
            "method": "mock_controlled_bom_assembly",
            "component_definition_count": len(components),
            "component_instance_count": sum(int(component["quantity"]) for component in components),
            "component_definitions": components,
            "mates": operation.parameters.get("mates", []),
            "checks": {
                "component_count_positive": True,
                "all_components_have_part_numbers": all(bool(component["part_number"]) for component in components),
            },
        }
        self._bom_result = {
            "status": "bom_verified",
            "method": "mock_bom_table",
            "columns": params["bom"]["columns"],
            "row_count": len(rows),
            "rows": rows,
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
        self._drawing_view_status = "created"
        self._drawing_view_result = _mock_standard_drawing_view_result()
        self._model_geometry_status = "not_requested"
        self._model_geometry_result = {"status": "not_requested", "reason": "assembly workflow uses assembly_result"}
        self._mass_property_status = "not_requested"
        self._mass_property_result = {"status": "not_requested", "reason": "assembly workflow uses BOM/component evidence"}
        feature = {
            "index": index,
            "id": operation.id or "bom_assembly",
            "op": operation.op,
            "description": operation.description,
            "parameters": operation.parameters,
            "assembly_result": self._assembly_result,
            "bom_result": self._bom_result,
        }
        self._features.append(feature)
        self.record_event("assembly.create", "completed", self._assembly_result)
        self.record_event("bom.create", "completed", self._bom_result)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_bom_assembly with BOM evidence.",
            details=feature,
        )

    def _refresh_atomic_geometry_evidence(self, operation: ModelOperation) -> None:
        """Publish minimal body and mass evidence for staged atomic geometry."""

        geometry_ops = {
            "extrude",
            "cut",
            "hole",
            "fillet",
            "chamfer",
            "linear_pattern",
            "circular_pattern",
            "revolve",
            "sweep",
            "loft",
        }
        if operation.op not in geometry_ops:
            return
        if self._config.force_model_geometry_failure:
            self._model_geometry_result = {
                "status": "geometry_mismatch",
                "workflow": "atomic_model",
                "body_count": 0,
                "failure_reason": "SOLIDWORKS_MCP_FORCE_MODEL_GEOMETRY_FAILURE is enabled",
                "checks": {"body_count_positive": False, "bbox_dimensions_positive": False},
            }
            self._model_geometry_status = "geometry_mismatch"
            self._mass_property_result = {
                "status": "mass_property_invalid",
                "mass_kg": 0,
                "volume_m3": 0,
                "failure_reason": "SOLIDWORKS_MCP_FORCE_MODEL_GEOMETRY_FAILURE is enabled",
            }
            self._mass_property_status = "mass_property_invalid"
            return
        self._model_geometry_result = {
            "status": "geometry_verified",
            "workflow": "atomic_model",
            "method": "mock_atomic_feature_graph",
            "body_count": 1,
            "bbox_m": [0.0, 0.0, 0.0, 0.08, 0.04, 0.008],
            "measured_dimensions_mm": [8.0, 40.0, 80.0],
            "checks": {"body_count_positive": True, "bbox_dimensions_positive": True},
        }
        self._model_geometry_status = "geometry_verified"
        self._mass_property_result = {
            "status": "mass_properties_verified",
            "method": "mock_atomic_mass_properties",
            "mass_kg": 0.19,
            "volume_m3": 0.0000256,
            "surface_area_m2": 0.008,
            "checks": {"positive_mass": True, "positive_volume": True},
        }
        self._mass_property_status = "mass_properties_verified"

    def _execute_mounting_plate(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the high-level mounting plate template and expected outputs."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or "mounting_plate",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["top_face", "outer_edges"],
            "thread_model_status": "macro_threaded_hole",
            "corner_radius_status": "fillet_feature",
            "hole_result": {
                "ok": True,
                "method": "mock_macro_fallback",
                "thread_model_status": "macro_threaded_hole",
                "thread_spec": str(params.get("thread_spec", "M6")).upper(),
                "thread_size": str(params.get("thread_spec", "M6")).upper(),
                "hole_count": 4,
                "macro_path": path_to_string(self._require_workspace() / "macros" / "holewizard_fallback.swb"),
                "result_path": path_to_string(self._require_workspace() / "macros" / "holewizard_fallback_result.json"),
                "run_result": True,
                "error_code": 0,
            },
            "drawing_annotation_status": "hole_callout_created",
        }
        self._last_hole_result = feature["hole_result"]
        self._thread_model_status = "macro_threaded_hole"
        self._corner_radius_status = "fillet_feature"
        self._drawing_view_status = "created"
        self._drawing_view_result = _mock_standard_drawing_view_result()
        self._drawing_annotation_status = "hole_callout_created"
        self._drawing_annotation_result = {
            "status": "hole_callout_created",
            "view_name": "*Top",
            "selected_edge_count": 4,
            "created_callout_count": 4,
            "callout_creation_method": "add_hole_callout2",
            "direct_hole_callout_created": True,
            "attempts": [
                {"index": index, "selected": True, "callout_created": True}
                for index in range(4)
            ],
        }
        self._model_geometry_result = _mock_mounting_plate_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_mounting_plate_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        self._fallbacks.append(
            {"from": "HoleWizard5", "to": "mock_macro_fallback", "reason": "mock dry-run simulates macro fallback"}
        )
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_mounting_plate with semantic selectors.",
            details=feature,
        )

    def _execute_center_hole_flange(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the controlled center-hole flange template and expected outputs."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or "center_hole_flange",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["front_face", "center_hole"],
            "controlled_workflow": "center_hole_flange",
        }
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "hole_callout_created"
        self._drawing_annotation_result = {
            "status": "hole_callout_created",
            "view_name": "*Front",
            "selected_edge_count": 1,
            "created_callout_count": 1,
            "callout_creation_method": "add_hole_callout2",
            "direct_hole_callout_created": True,
            "attempts": [
                {"index": 0, "selected": True, "callout_created": True}
            ],
        }
        self._model_geometry_result = _mock_center_hole_flange_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_center_hole_flange_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_center_hole_flange with controlled geometry.",
            details=feature,
        )

    def _execute_center_hole_plate(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the controlled center-hole plate template and expected outputs."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or "center_hole_plate",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["front_face", "center_hole"],
            "controlled_workflow": "center_hole_plate",
        }
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "hole_callout_created"
        self._drawing_annotation_result = {
            "status": "hole_callout_created",
            "view_name": "*Front",
            "selected_edge_count": 1,
            "created_callout_count": 1,
            "callout_creation_method": "add_hole_callout2",
            "direct_hole_callout_created": True,
            "attempts": [
                {"index": 0, "selected": True, "callout_created": True}
            ],
        }
        self._model_geometry_result = _mock_center_hole_plate_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_center_hole_plate_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_center_hole_plate with controlled geometry.",
            details=feature,
        )

    def _execute_bracket(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the controlled L-bracket template and expected outputs."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or "bracket",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["front_face", "base_hole", "upright_hole", "outer_edges"],
            "controlled_workflow": "bracket",
        }
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "hole_callout_created"
        self._drawing_annotation_result = {
            "status": "hole_callout_created",
            "view_name": "*Front",
            "selected_edge_count": 2,
            "created_callout_count": 2,
            "callout_creation_method": "add_hole_callout2",
            "direct_hole_callout_created": True,
            "attempts": [
                {"index": 0, "role": "base_hole", "selected": True, "callout_created": True},
                {"index": 1, "role": "upright_hole", "selected": True, "callout_created": True},
            ],
        }
        self._model_geometry_result = _mock_bracket_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_bracket_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_bracket with controlled geometry.",
            details=feature,
        )

    def _execute_washer(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the controlled washer template and expected outputs."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or "washer",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["front_face", "inner_hole"],
            "controlled_workflow": "washer",
        }
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "hole_callout_created"
        self._drawing_annotation_result = {
            "status": "hole_callout_created",
            "view_name": "*Front",
            "selected_edge_count": 1,
            "created_callout_count": 1,
            "callout_creation_method": "add_hole_callout2",
            "direct_hole_callout_created": True,
            "attempts": [
                {"index": 0, "selected": True, "callout_created": True}
            ],
        }
        self._model_geometry_result = _mock_washer_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_washer_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_washer with controlled geometry.",
            details=feature,
        )

    def _execute_end_cap(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the controlled end-cap template and expected outputs."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or "end_cap",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["front_face", "center_hole", "bolt_hole_pattern"],
            "controlled_workflow": "end_cap",
        }
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "hole_callout_created"
        self._drawing_annotation_result = {
            "status": "hole_callout_created",
            "view_name": "*Front",
            "selected_edge_count": 2,
            "created_callout_count": 2,
            "callout_creation_method": "add_hole_callout2",
            "direct_hole_callout_created": True,
            "attempts": [
                {"index": 0, "role": "center_hole", "selected": True, "callout_created": True},
                {"index": 1, "role": "bolt_hole_pattern", "selected": True, "callout_created": True},
            ],
        }
        self._model_geometry_result = _mock_end_cap_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_end_cap_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_end_cap with controlled geometry.",
            details=feature,
        )

    def _execute_mounting_block(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the controlled mounting-block template and expected outputs."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or "mounting_block",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["front_face", "center_hole"],
            "controlled_workflow": "mounting_block",
        }
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "hole_callout_created"
        self._drawing_annotation_result = {
            "status": "hole_callout_created",
            "view_name": "*Front",
            "selected_edge_count": 1,
            "created_callout_count": 1,
            "callout_creation_method": "add_hole_callout2",
            "direct_hole_callout_created": True,
            "attempts": [
                {"index": 0, "selected": True, "callout_created": True}
            ],
        }
        self._model_geometry_result = _mock_mounting_block_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_mounting_block_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_mounting_block with controlled geometry.",
            details=feature,
        )

    def _execute_shaft(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the controlled plain-shaft template and expected outputs."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or "shaft",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["front_face", "outer_cylinder"],
            "controlled_workflow": "shaft",
        }
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "not_requested"
        self._drawing_annotation_result = {
            "status": "not_requested",
            "created_callout_count": 0,
            "direct_hole_callout_created": None,
            "callout_creation_method": None,
            "reason": "controlled_shaft_has_no_holes",
        }
        self._model_geometry_result = _mock_shaft_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_shaft_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_shaft with controlled geometry.",
            details=feature,
        )

    def _execute_sheet_metal_base_flange(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record a controlled sheet-metal base flange with flat-pattern evidence."""

        params = sheet_metal_base_flange_parameters_from_plan(plan)
        if params is None:
            raise RuntimeError("create_sheet_metal_base_flange parameters could not be extracted")
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
            "method": "mock_sheet_metal_base_flange",
            "base_flange_created": True,
            "feature_name": "Base-Flange1",
            "thickness_mm": params["thickness"],
            "bend_radius_mm": params["bend_radius"],
            "flat_pattern_result": {
                "status": "pending_export",
                "ok": False,
                "format": "dxf",
            },
        }
        self._model_geometry_result = _mock_sheet_metal_base_flange_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_sheet_metal_base_flange_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        feature = {
            "index": index,
            "id": operation.id or "sheet_metal_base_flange",
            "op": operation.op,
            "description": operation.description,
            "parameters": operation.parameters,
            "semantic_selectors": ["front_face", "flat_pattern"],
            "controlled_workflow": "sheet_metal_base_flange",
            "sheet_metal_result": self._sheet_metal_result,
        }
        self._features.append(feature)
        self.record_event("sheet_metal.base_flange", "completed", self._sheet_metal_result)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_sheet_metal_base_flange with flat-pattern evidence.",
            details=feature,
        )

    def _execute_weldment_frame(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record a controlled structural-member weldment with cut-list evidence."""

        params = weldment_frame_parameters_from_plan(plan)
        if params is None:
            raise RuntimeError("create_weldment_frame parameters could not be extracted")
        profile = params["profile"]
        rows = _mock_weldment_cut_list_rows(params, plan)
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
            "status": "weldment_verified",
            "method": "mock_structural_member_weldment",
            "structural_member_created": True,
            "feature_type": "WeldMemberFeat",
            "body_count": 4,
            "profile": profile,
            "member_count": 4,
        }
        self._cut_list_result = {
            "status": "cut_list_verified",
            "method": "mock_weldment_cut_list",
            "row_count": len(rows),
            "columns": params["cut_list"]["columns"],
            "rows": rows,
            "export_formats": params["cut_list"]["export_formats"],
        }
        self._model_geometry_result = _mock_weldment_frame_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_weldment_frame_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        feature = {
            "index": index,
            "id": operation.id or "weldment_frame",
            "op": operation.op,
            "description": operation.description,
            "parameters": operation.parameters,
            "semantic_selectors": ["front_face", "weldment_members", "cut_list"],
            "controlled_workflow": "weldment_frame",
            "weldment_result": self._weldment_result,
            "cut_list_result": self._cut_list_result,
        }
        self._features.append(feature)
        self.record_event("weldment.create", "completed", self._weldment_result)
        self.record_event("weldment.cut_list", "completed", self._cut_list_result)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_weldment_frame with cut-list evidence.",
            details=feature,
        )

    def _execute_static_simulation(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record a controlled cantilever static-study fixture with report evidence."""

        params = static_simulation_parameters_from_plan(plan)
        if params is None:
            raise RuntimeError("run_static_simulation parameters could not be extracted")
        result = _mock_static_simulation_result(params)
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
        self._simulation_result = result
        self._model_geometry_result = _mock_static_simulation_geometry_result(params)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_static_simulation_mass_property_result(params)
        self._mass_property_status = str(self._mass_property_result["status"])
        feature = {
            "index": index,
            "id": operation.id or "static_simulation",
            "op": operation.op,
            "description": operation.description,
            "parameters": operation.parameters,
            "semantic_selectors": ["fixed_left_face", "loaded_right_face", "beam_body"],
            "controlled_workflow": "static_simulation",
            "simulation_result": self._simulation_result,
        }
        self._features.append(feature)
        self.record_event("simulation.static_study", "completed", self._simulation_result)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed run_static_simulation with static-study evidence.",
            details=feature,
        )

    def _execute_sleeve(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the controlled sleeve template and expected outputs."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or "sleeve",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["front_face", "inner_bore"],
            "controlled_workflow": "sleeve",
        }
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "hole_callout_created"
        self._drawing_annotation_result = {
            "status": "hole_callout_created",
            "view_name": "*Front",
            "selected_edge_count": 1,
            "created_callout_count": 1,
            "callout_creation_method": "add_hole_callout2",
            "direct_hole_callout_created": True,
            "attempts": [
                {"index": 0, "selected": True, "callout_created": True}
            ],
        }
        self._model_geometry_result = _mock_sleeve_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_sleeve_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_sleeve with controlled geometry.",
            details=feature,
        )

    def _execute_slotted_array_plate(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the controlled slotted hole-array plate template and expected outputs."""

        params = operation.parameters
        hole_count = int(params["hole_rows"]) * int(params["hole_columns"])
        feature = {
            "index": index,
            "id": operation.id or "slotted_array_plate",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["front_face", "center_slot", "hole_array"],
            "controlled_workflow": "slotted_array_plate",
        }
        self._thread_model_status = "not_requested"
        self._corner_radius_status = "not_requested"
        self._drawing_annotation_status = "hole_callout_created"
        self._drawing_annotation_result = {
            "status": "hole_callout_created",
            "view_name": "*Front",
            "selected_edge_count": hole_count,
            "created_callout_count": hole_count,
            "callout_creation_method": "add_hole_callout2",
            "direct_hole_callout_created": True,
            "attempts": [
                {"index": item_index, "role": "array_hole", "selected": True, "callout_created": True}
                for item_index in range(hole_count)
            ],
        }
        self._model_geometry_result = _mock_slotted_array_plate_geometry_result(plan)
        self._model_geometry_status = str(self._model_geometry_result["status"])
        self._mass_property_result = _mock_slotted_array_plate_mass_property_result(plan)
        self._mass_property_status = str(self._mass_property_result["status"])
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_slotted_array_plate with controlled geometry.",
            details=feature,
        )

    def _execute_assign_material(self, operation: ModelOperation, index: int) -> StepResult:
        """Record a verified mock material assignment."""

        material = str(operation.parameters["material"])
        if self._config.force_material_failure:
            result = {
                "status": "forced_failure",
                "requested_material": material,
                "current_material": None,
                "configuration": "Default",
                "database": "mock",
                "set_result": False,
                "verified": False,
                "failure_reason": "SOLIDWORKS_MCP_FORCE_MATERIAL_FAILURE is enabled",
            }
            self._material_status = "forced_failure"
            self._material_result = result
            feature = {
                "index": index,
                "id": operation.id or "material",
                "op": operation.op,
                "description": operation.description,
                "details": result,
            }
            self._features.append(feature)
            self.record_event("properties.material", "failed", result)
            self.record_event("adapter.operation", "completed", feature)
            return StepResult(
                index=index,
                id=operation.id,
                op=operation.op,
                ok=True,
                message="Mock forced assign_material verification failure.",
                details=feature,
            )
        result = {
            "status": "material_verified",
            "requested_material": material,
            "current_material": material,
            "configuration": "Default",
            "database": "mock",
            "set_result": True,
            "verified": True,
        }
        self._material_status = "material_verified"
        self._material_result = result
        feature = {
            "index": index,
            "id": operation.id or "material",
            "op": operation.op,
            "description": operation.description,
            "details": result,
        }
        self._features.append(feature)
        self.record_event("properties.material", "completed", result)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock verified assign_material.",
            details=feature,
        )

    def _execute_set_custom_properties(self, operation: ModelOperation, index: int) -> StepResult:
        """Record a verified mock custom-property write."""

        properties = {
            str(key).strip(): str(value)
            for key, value in operation.parameters["properties"].items()
        }
        scope = str(operation.parameters.get("scope", "document"))
        result = {
            "status": "custom_properties_verified",
            "scope": scope,
            "requested_properties": properties,
            "current_properties": dict(properties),
            "verified": True,
            "attempts": [
                {"name": key, "value": value, "readback": value, "verified": True}
                for key, value in properties.items()
            ],
        }
        self._custom_property_status = "custom_properties_verified"
        self._custom_property_result = result
        feature = {
            "index": index,
            "id": operation.id or "custom_properties",
            "op": operation.op,
            "description": operation.description,
            "details": result,
        }
        self._features.append(feature)
        self.record_event("properties.custom", "completed", result)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock verified set_custom_properties.",
            details=feature,
        )

    def generate_drawing(self, plan: ModelPlan, profile: DrawingProfile) -> dict[str, str]:
        """Write a drawing manifest that mirrors the requested drawing profile."""

        workspace = self._require_workspace()
        self._drawing_view_status = "created"
        self._drawing_view_result = _mock_standard_drawing_view_result()
        self.record_event("drawing.standard_views", "completed", self._drawing_view_result)
        if self._config.force_drawing_callout_failure:
            self._drawing_annotation_status = "forced_failure"
            self._drawing_annotation_result = {
                "status": "forced_failure",
                "view_name": "*Top",
                "selected_edge_count": 0,
                "created_callout_count": 0,
                "callout_creation_method": None,
                "direct_hole_callout_created": False,
                "attempts": [],
                "failure_reason": "SOLIDWORKS_MCP_FORCE_DRAWING_CALLOUT_FAILURE is enabled",
            }
            self._warnings.append("drawing_thread_callouts:forced_failure")
            self.record_event("drawing.hole_callout", "failed", self._drawing_annotation_result)
        else:
            self.record_event("drawing.hole_callout", "completed", self._drawing_annotation_result)
        if profile.include_basic_dimensions:
            required_basic_dimensions = _mock_required_basic_dimension_ids(plan)
            self._drawing_dimension_result = _mock_basic_dimension_result(
                self._config.force_drawing_dimension_failure,
                required_basic_dimensions,
            )
            self._drawing_dimension_status = str(self._drawing_dimension_result["status"])
            event_status = "failed" if self._drawing_dimension_status != "basic_dimensions_created" else "completed"
            if event_status == "failed":
                self._warnings.append(f"drawing_basic_dimensions:{self._drawing_dimension_status}")
            self.record_event("drawing.basic_dimensions", event_status, self._drawing_dimension_result)
        else:
            self._drawing_dimension_status = "not_requested"
            self._drawing_dimension_result = {"status": "not_requested"}
        self._drawing_metadata_note_result = _mock_metadata_note_result(plan)
        if self._drawing_metadata_note_result["status"] == "metadata_note_created":
            self.record_event("drawing.metadata_note", "completed", self._drawing_metadata_note_result)
        drawing_path = workspace / "exports" / f"{safe_output_name(plan.name)}.drawing.json"
        drawing_path.parent.mkdir(parents=True, exist_ok=True)
        slddrw_path = workspace / "exports" / f"{safe_output_name(plan.name)}.slddrw"
        drawing_path.write_text(
            json.dumps(
                {
                    "plan": plan.name,
                    "units": plan.units,
                    "profile": profile.to_dict(),
                    "views": self._drawing_view_result["views"],
                    "view_status": self._drawing_view_status,
                    "view_result": self._drawing_view_result,
                    "annotation_status": self._drawing_annotation_status,
                    "annotation_result": self._drawing_annotation_result,
                    "dimension_status": self._drawing_dimension_status,
                    "dimension_result": self._drawing_dimension_result,
                    "metadata_note_result": self._drawing_metadata_note_result,
                    "note": "Mock drawing manifest. Generate the real drawing on Windows with SolidWorks.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        slddrw_path.write_text(
            f"Mock SLDDRW export for {plan.name}\n",
            encoding="utf-8",
        )
        return {"drawing_manifest": path_to_string(drawing_path), "slddrw": path_to_string(slddrw_path)}

    def export_outputs(self, plan: ModelPlan, formats: tuple[str, ...]) -> dict[str, str]:
        """Create placeholder files so downstream agents can verify paths."""

        workspace = self._require_workspace() / "exports"
        workspace.mkdir(parents=True, exist_ok=True)
        outputs: dict[str, str] = {}
        base_name = safe_output_name(plan.name)
        failed: list[dict[str, Any]] = []
        forced_failure_format = formats[0] if self._config.force_export_failure and formats else None
        for file_format in formats:
            if file_format == forced_failure_format:
                failure = {
                    "format": file_format,
                    "path": path_to_string(workspace / f"{base_name}.{file_format.lower()}"),
                    "error": "SOLIDWORKS_MCP_FORCE_EXPORT_FAILURE is enabled",
                    "forced": True,
                }
                failed.append(failure)
                self.record_event("outputs.export_format", "failed", failure)
                continue
            output_path = workspace / f"{base_name}.{file_format.lower()}"
            if file_format == "csv" and self._bom_result.get("status") == "bom_verified":
                columns = [str(item) for item in self._bom_result.get("columns", [])]
                rows = self._bom_result.get("rows", [])
                lines = [",".join(columns)]
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            lines.append(",".join(str(row.get(column, "")) for column in columns))
                output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            elif file_format == "csv" and self._cut_list_result.get("status") == "cut_list_verified":
                columns = [str(item) for item in self._cut_list_result.get("columns", [])]
                rows = self._cut_list_result.get("rows", [])
                lines = [",".join(columns)]
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            lines.append(",".join(str(row.get(column, "")) for column in columns))
                output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            elif file_format == "csv" and self._simulation_result.get("status") == "simulation_verified":
                columns = [str(item) for item in self._simulation_result.get("columns", [])]
                rows = self._simulation_result.get("rows", [])
                lines = [",".join(columns)]
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            lines.append(",".join(str(row.get(column, "")) for column in columns))
                output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            elif file_format == "dxf" and self._sheet_metal_result.get("status") == "sheet_metal_verified":
                output_path.write_text(
                    f"Mock DXF flat-pattern export for {plan.name}\n",
                    encoding="utf-8",
                )
                flat_pattern_result = {
                    "status": "flat_pattern_exported",
                    "ok": True,
                    "format": "dxf",
                    "path": path_to_string(output_path),
                    "method": "mock_flat_pattern_dxf",
                }
                self._sheet_metal_result["flat_pattern_result"] = flat_pattern_result
                self.record_event("sheet_metal.flat_pattern_export", "completed", flat_pattern_result)
            else:
                output_path.write_text(
                    f"Mock {file_format.upper()} export for {plan.name}\n",
                    encoding="utf-8",
                )
            outputs[file_format] = path_to_string(output_path)
        self._export_result = {
            "status": "partial_export_failure" if failed else "exports_completed",
            "formats": list(formats),
            "exported": sorted(outputs),
            "failed": failed,
            "failed_count": len(failed),
        }
        return outputs

    def inspect_active_model(self) -> dict[str, Any]:
        """Return the recorded feature list for self-review."""

        return {
            "adapter": self.name,
            "active_document": self._active_plan.name if self._active_plan else None,
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
            "hole_result": self._last_hole_result,
            "fallbacks": list(self._fallbacks),
            "warnings": list(self._warnings),
        }

    def document_state_snapshot(self, phase: str) -> dict[str, Any]:
        """Return deterministic no-document diagnostics for mock executions."""

        return {
            "status": "not_applicable",
            "adapter": self.name,
            "phase": phase,
            "open_document_count": 0,
            "run_created_open_count": 0,
            "run_created_documents": [],
            "open_documents": [],
            "tracked_documents": [],
            "message": "Mock adapter has no SolidWorks document state to audit.",
        }

    def capture_previews(self, plan: ModelPlan) -> dict[str, str]:
        """Create text placeholders for standard view previews."""

        workspace = self._require_workspace() / "previews"
        workspace.mkdir(parents=True, exist_ok=True)
        previews: dict[str, str] = {}
        for view_name in ("front", "top", "right", "isometric"):
            preview_path = workspace / f"{safe_output_name(plan.name)}_{view_name}_preview.txt"
            preview_path.write_text(
                f"Mock {view_name} preview for {plan.name}\n",
                encoding="utf-8",
            )
            previews[view_name] = path_to_string(preview_path)
        return previews

    def cleanup_after_run(self, plan: ModelPlan | None = None) -> dict[str, Any]:
        """Return deterministic cleanup diagnostics for mock executions."""

        if not self._config.close_documents_after_run:
            self._active_plan = None
            return {
                "status": "disabled",
                "enabled": False,
                "adapter": self.name,
                "closed_documents": [],
                "attempts": [],
                "cleanup_verification_status": "not_attempted",
                "message": "SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN is disabled.",
            }

        if self._config.force_cleanup_failure:
            self._active_plan = None
            return {
                "status": "forced_failure",
                "enabled": True,
                "adapter": self.name,
                "closed_documents": [],
                "attempts": [],
                "cleanup_verification_status": "failed",
                "failure_reason": "SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE is enabled",
                "message": "Forced cleanup failure for regression testing.",
            }

        result = {
            "status": "skipped_no_documents",
            "enabled": True,
            "adapter": self.name,
            "closed_documents": [],
            "attempts": [],
            "cleanup_verification_status": "not_applicable",
            "message": "Mock adapter has no SolidWorks documents to close.",
        }
        self._active_plan = None
        return result

    def cleanup_run_documents(self, run_dir: str | Path) -> dict[str, Any]:
        """Return deterministic post-run cleanup diagnostics for mock executions."""

        if self._config.force_cleanup_failure:
            return {
                "status": "forced_failure",
                "enabled": True,
                "adapter": self.name,
                "run_dir": path_to_string(Path(run_dir).expanduser()),
                "closed_documents": [],
                "attempts": [],
                "candidate_documents": [],
                "cleanup_verification_status": "failed",
                "failure_reason": "SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE is enabled",
                "message": "Forced post-run cleanup failure for regression testing.",
            }

        return {
            "status": "skipped_no_documents",
            "enabled": True,
            "adapter": self.name,
            "run_dir": path_to_string(Path(run_dir).expanduser()),
            "closed_documents": [],
            "attempts": [],
            "candidate_documents": [],
            "cleanup_verification_status": "not_applicable",
            "message": "Mock adapter has no SolidWorks documents to close for a completed run.",
        }

    def _require_workspace(self) -> Path:
        """Return the active transaction directory or fail with a useful error."""

        if self._workspace is None:
            raise RuntimeError("No active mock transaction. Call begin_transaction first.")
        return self._workspace


def _mock_standard_drawing_view_result() -> dict[str, Any]:
    """Return deterministic standard-view diagnostics for mock smoke."""

    views = [
        {"role": "front", "name": "*Front", "x": 0.18, "y": 0.16},
        {"role": "top", "name": "*Top", "x": 0.18, "y": 0.28},
        {"role": "right", "name": "*Right", "x": 0.34, "y": 0.16},
        {"role": "isometric", "name": "*Isometric", "x": 0.34, "y": 0.28},
    ]
    return {
        "status": "created",
        "views": views,
        "created_count": len(views),
        "required_roles": [view["role"] for view in views],
        "missing_roles": [],
        "errors": [],
    }


def _mock_basic_dimension_result(forced_failure: bool, required: list[str]) -> dict[str, Any]:
    """Return deterministic MVP drawing-dimension diagnostics for mock smoke."""

    if forced_failure:
        return {
            "status": "forced_failure",
            "required_dimensions": required,
            "created_dimensions": [],
            "created_dimension_count": 0,
            "missing_dimensions": required,
            "dimension_layout_status": "not_attempted",
            "failure_reason": "SOLIDWORKS_MCP_FORCE_DRAWING_DIMENSION_FAILURE is enabled",
            "attempts": [],
        }

    return {
        "status": "basic_dimensions_created",
        "required_dimensions": required,
        "created_dimensions": [
            {
                "id": dimension_id,
                "method": "AddRadialDimension2" if dimension_id.startswith("corner_radius_") else "mock_display_dimension",
                "is_display_dimension": True,
            }
            for dimension_id in required
        ],
        "created_dimension_count": len(required),
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
        "attempts": [
            {
                "id": dimension_id,
                "created": True,
                "method": "AddRadialDimension2" if dimension_id.startswith("corner_radius_") else "mock_display_dimension",
            }
            for dimension_id in required
        ],
    }


def _mock_required_basic_dimension_ids(plan: ModelPlan) -> list[str]:
    """Return the mock required drawing dimensions for the controlled workflow."""

    atomic_required = atomic_dimension_ids_from_metadata(plan.metadata)
    if atomic_required:
        return atomic_required
    required = mounting_plate_basic_dimension_ids_from_plan(plan)
    if required:
        return required
    bracket_required = bracket_basic_dimension_ids_from_plan(plan)
    if bracket_required:
        return bracket_required
    flange_required = center_hole_flange_basic_dimension_ids_from_plan(plan)
    if flange_required:
        return flange_required
    center_hole_plate_required = center_hole_plate_basic_dimension_ids_from_plan(plan)
    if center_hole_plate_required:
        return center_hole_plate_required
    end_cap_required = end_cap_basic_dimension_ids_from_plan(plan)
    if end_cap_required:
        return end_cap_required
    mounting_block_required = mounting_block_basic_dimension_ids_from_plan(plan)
    if mounting_block_required:
        return mounting_block_required
    shaft_required = shaft_basic_dimension_ids_from_plan(plan)
    if shaft_required:
        return shaft_required
    sheet_metal_required = sheet_metal_base_flange_basic_dimension_ids_from_plan(plan)
    if sheet_metal_required:
        return sheet_metal_required
    weldment_required = weldment_frame_basic_dimension_ids_from_plan(plan)
    if weldment_required:
        return weldment_required
    simulation_required = static_simulation_basic_dimension_ids_from_plan(plan)
    if simulation_required:
        return simulation_required
    washer_required = washer_basic_dimension_ids_from_plan(plan)
    if washer_required:
        return washer_required
    sleeve_required = sleeve_basic_dimension_ids_from_plan(plan)
    if sleeve_required:
        return sleeve_required
    return slotted_array_plate_basic_dimension_ids_from_plan(plan)


def _mock_metadata_note_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic drawing metadata note diagnostics for mock smoke."""

    properties: dict[str, str] = {}
    for operation in plan.operations:
        if operation.op == "set_custom_properties":
            properties = {
                str(key).strip(): str(value)
                for key, value in operation.parameters.get("properties", {}).items()
            }
    if not properties:
        return {"status": "not_requested", "properties": {}}
    text = "\n".join(f"{key}: {properties[key]}" for key in sorted(properties))
    return {
        "status": "metadata_note_created",
        "method": "mock_manifest_note",
        "properties": properties,
        "text": text,
    }


def _mock_mounting_plate_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for the mock adapter."""

    params = mounting_plate_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_mounting_plate operation was found."}
    expected_dimensions = sorted(
        [float(params["length"]), float(params["width"]), float(params["thickness"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    return {
        "status": "geometry_verified",
        "method": "mock_mounting_plate_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-float(params["length"]) / 2000, -float(params["width"]) / 2000, 0.0],
        "bbox_max_m": [
            float(params["length"]) / 2000,
            float(params["width"]) / 2000,
            float(params["thickness"]) / 1000,
        ],
        "failure_reason": None,
    }


def _mock_mounting_plate_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock mounting plates."""

    params = mounting_plate_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_mounting_plate operation was found."}
    length_m = float(params["length"]) / 1000
    width_m = float(params["width"]) / 1000
    thickness_m = float(params["thickness"]) / 1000
    radius_m = float(params["corner_radius"]) / 1000
    removed_corner_area_m2 = 4 * (radius_m * radius_m - 3.141592653589793 * radius_m * radius_m / 4)
    volume_m3 = max((length_m * width_m - removed_corner_area_m2) * thickness_m, 0.0)
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_mounting_plate_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2 * (length_m * width_m) + 2 * thickness_m * (length_m + width_m),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_bracket_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock L brackets."""

    params = bracket_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_bracket operation was found."}
    expected_dimensions = sorted(
        [float(params["base_length"]), float(params["base_width"]), float(params["upright_height"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    return {
        "status": "geometry_verified",
        "method": "mock_bracket_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "expected_hole_diameter_mm": float(params["hole_diameter"]),
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-float(params["base_length"]) / 2000, -float(params["base_width"]) / 2000, 0.0],
        "bbox_max_m": [
            float(params["base_length"]) / 2000,
            float(params["base_width"]) / 2000,
            float(params["upright_height"]) / 1000,
        ],
        "failure_reason": None,
    }


def _mock_bracket_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock L brackets."""

    params = bracket_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_bracket operation was found."}
    base_length_m = float(params["base_length"]) / 1000
    base_width_m = float(params["base_width"]) / 1000
    base_thickness_m = float(params["base_thickness"]) / 1000
    upright_height_m = float(params["upright_height"]) / 1000
    upright_thickness_m = float(params["upright_thickness"]) / 1000
    hole_radius_m = float(params["hole_diameter"]) / 2000
    base_volume_m3 = base_length_m * base_width_m * base_thickness_m
    upright_volume_m3 = upright_thickness_m * base_width_m * (upright_height_m - base_thickness_m)
    hole_volume_m3 = 2 * 3.141592653589793 * hole_radius_m**2 * base_width_m
    volume_m3 = base_volume_m3 + upright_volume_m3 - hole_volume_m3
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_bracket_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2 * (base_volume_m3 + upright_volume_m3),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_center_hole_flange_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock center-hole flanges."""

    params = center_hole_flange_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_center_hole_flange operation was found."}
    expected_dimensions = sorted(
        [float(params["outer_diameter"]), float(params["outer_diameter"]), float(params["thickness"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    radius_m = float(params["outer_diameter"]) / 2000
    thickness_m = float(params["thickness"]) / 1000
    return {
        "status": "geometry_verified",
        "method": "mock_center_hole_flange_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-radius_m, -radius_m, 0.0],
        "bbox_max_m": [radius_m, radius_m, thickness_m],
        "failure_reason": None,
    }


def _mock_center_hole_flange_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock center-hole flanges."""

    params = center_hole_flange_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_center_hole_flange operation was found."}
    outer_radius_m = float(params["outer_diameter"]) / 2000
    hole_radius_m = float(params["hole_diameter"]) / 2000
    thickness_m = float(params["thickness"]) / 1000
    volume_m3 = 3.141592653589793 * (outer_radius_m**2 - hole_radius_m**2) * thickness_m
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_center_hole_flange_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2 * 3.141592653589793 * (outer_radius_m**2 - hole_radius_m**2),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_end_cap_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock end caps."""

    params = end_cap_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_end_cap operation was found."}
    expected_dimensions = sorted(
        [float(params["outer_diameter"]), float(params["outer_diameter"]), float(params["thickness"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    radius_m = float(params["outer_diameter"]) / 2000
    thickness_m = float(params["thickness"]) / 1000
    return {
        "status": "geometry_verified",
        "method": "mock_end_cap_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "expected_center_hole_diameter_mm": float(params["center_hole_diameter"]),
        "expected_bolt_circle_diameter_mm": float(params["bolt_circle_diameter"]),
        "expected_bolt_hole_diameter_mm": float(params["bolt_hole_diameter"]),
        "expected_bolt_hole_count": int(params["bolt_hole_count"]),
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-radius_m, -radius_m, 0.0],
        "bbox_max_m": [radius_m, radius_m, thickness_m],
        "failure_reason": None,
    }


def _mock_end_cap_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock end caps."""

    params = end_cap_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_end_cap operation was found."}
    outer_radius_m = float(params["outer_diameter"]) / 2000
    center_radius_m = float(params["center_hole_diameter"]) / 2000
    bolt_radius_m = float(params["bolt_hole_diameter"]) / 2000
    thickness_m = float(params["thickness"]) / 1000
    bolt_count = int(params["bolt_hole_count"])
    volume_m3 = 3.141592653589793 * (
        outer_radius_m**2 - center_radius_m**2 - bolt_count * bolt_radius_m**2
    ) * thickness_m
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_end_cap_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2
        * 3.141592653589793
        * (outer_radius_m**2 - center_radius_m**2 - bolt_count * bolt_radius_m**2),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_center_hole_plate_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock center-hole plates."""

    params = center_hole_plate_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_center_hole_plate operation was found."}
    expected_dimensions = sorted(
        [float(params["length"]), float(params["width"]), float(params["thickness"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    return {
        "status": "geometry_verified",
        "method": "mock_center_hole_plate_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-float(params["length"]) / 2000, -float(params["width"]) / 2000, 0.0],
        "bbox_max_m": [
            float(params["length"]) / 2000,
            float(params["width"]) / 2000,
            float(params["thickness"]) / 1000,
        ],
        "failure_reason": None,
    }

def _mock_center_hole_plate_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock center-hole plates."""

    params = center_hole_plate_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_center_hole_plate operation was found."}
    length_m = float(params["length"]) / 1000
    width_m = float(params["width"]) / 1000
    thickness_m = float(params["thickness"]) / 1000
    hole_radius_m = float(params["hole_diameter"]) / 2000
    volume_m3 = max((length_m * width_m - 3.141592653589793 * hole_radius_m**2) * thickness_m, 0.0)
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_center_hole_plate_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2 * (length_m * width_m) + 2 * thickness_m * (length_m + width_m),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_slotted_array_plate_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock slotted-array plates."""

    params = slotted_array_plate_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_slotted_array_plate operation was found."}
    expected_dimensions = sorted(
        [float(params["length"]), float(params["width"]), float(params["thickness"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    hole_count = int(params["hole_rows"]) * int(params["hole_columns"])
    return {
        "status": "geometry_verified",
        "method": "mock_slotted_array_plate_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "expected_slot_length_mm": float(params["slot_length"]),
        "expected_slot_width_mm": float(params["slot_width"]),
        "expected_hole_diameter_mm": float(params["hole_diameter"]),
        "expected_hole_count": hole_count,
        "expected_hole_rows": int(params["hole_rows"]),
        "expected_hole_columns": int(params["hole_columns"]),
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-float(params["length"]) / 2000, -float(params["width"]) / 2000, 0.0],
        "bbox_max_m": [
            float(params["length"]) / 2000,
            float(params["width"]) / 2000,
            float(params["thickness"]) / 1000,
        ],
        "failure_reason": None,
    }


def _mock_slotted_array_plate_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock slotted-array plates."""

    params = slotted_array_plate_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_slotted_array_plate operation was found."}
    length_m = float(params["length"]) / 1000
    width_m = float(params["width"]) / 1000
    thickness_m = float(params["thickness"]) / 1000
    slot_length_m = float(params["slot_length"]) / 1000
    slot_width_m = float(params["slot_width"]) / 1000
    hole_radius_m = float(params["hole_diameter"]) / 2000
    hole_count = int(params["hole_rows"]) * int(params["hole_columns"])
    slot_radius_m = slot_width_m / 2
    slot_area_m2 = max(slot_length_m - slot_width_m, 0.0) * slot_width_m + 3.141592653589793 * slot_radius_m**2
    hole_area_m2 = hole_count * 3.141592653589793 * hole_radius_m**2
    volume_m3 = max((length_m * width_m - slot_area_m2 - hole_area_m2) * thickness_m, 0.0)
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_slotted_array_plate_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2 * (length_m * width_m) + 2 * thickness_m * (length_m + width_m),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_washer_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock washers."""

    params = washer_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_washer operation was found."}
    expected_dimensions = sorted(
        [float(params["outer_diameter"]), float(params["outer_diameter"]), float(params["thickness"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    radius_m = float(params["outer_diameter"]) / 2000
    thickness_m = float(params["thickness"]) / 1000
    return {
        "status": "geometry_verified",
        "method": "mock_washer_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-radius_m, -radius_m, 0.0],
        "bbox_max_m": [radius_m, radius_m, thickness_m],
        "failure_reason": None,
    }


def _mock_washer_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock washers."""

    params = washer_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_washer operation was found."}
    outer_radius_m = float(params["outer_diameter"]) / 2000
    inner_radius_m = float(params["inner_diameter"]) / 2000
    thickness_m = float(params["thickness"]) / 1000
    volume_m3 = 3.141592653589793 * (outer_radius_m**2 - inner_radius_m**2) * thickness_m
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_washer_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2 * 3.141592653589793 * (outer_radius_m**2 - inner_radius_m**2),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_mounting_block_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock mounting blocks."""

    params = mounting_block_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_mounting_block operation was found."}
    expected_dimensions = sorted(
        [float(params["length"]), float(params["width"]), float(params["height"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    length_m = float(params["length"]) / 1000
    width_m = float(params["width"]) / 1000
    height_m = float(params["height"]) / 1000
    return {
        "status": "geometry_verified",
        "method": "mock_mounting_block_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-length_m / 2, -width_m / 2, 0.0],
        "bbox_max_m": [length_m / 2, width_m / 2, height_m],
        "failure_reason": None,
    }


def _mock_mounting_block_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock mounting blocks."""

    params = mounting_block_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_mounting_block operation was found."}
    length_m = float(params["length"]) / 1000
    width_m = float(params["width"]) / 1000
    height_m = float(params["height"]) / 1000
    hole_radius_m = float(params["hole_diameter"]) / 2000
    volume_m3 = max((length_m * width_m - 3.141592653589793 * hole_radius_m**2) * height_m, 0.0)
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_mounting_block_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2 * (length_m * width_m) + 2 * height_m * (length_m + width_m),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_shaft_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock shafts."""

    params = shaft_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_shaft operation was found."}
    expected_dimensions = sorted(
        [float(params["diameter"]), float(params["diameter"]), float(params["length"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    radius_m = float(params["diameter"]) / 2000
    length_m = float(params["length"]) / 1000
    return {
        "status": "geometry_verified",
        "method": "mock_shaft_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-radius_m, -radius_m, 0.0],
        "bbox_max_m": [radius_m, radius_m, length_m],
        "failure_reason": None,
    }


def _mock_shaft_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock shafts."""

    params = shaft_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_shaft operation was found."}
    radius_m = float(params["diameter"]) / 2000
    length_m = float(params["length"]) / 1000
    volume_m3 = 3.141592653589793 * radius_m**2 * length_m
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_shaft_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": (2 * 3.141592653589793 * radius_m * length_m) + (2 * 3.141592653589793 * radius_m**2),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_sheet_metal_base_flange_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock sheet metal."""

    params = sheet_metal_base_flange_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_sheet_metal_base_flange operation was found."}
    expected_dimensions = sorted(
        [float(params["length"]), float(params["width"]), float(params["thickness"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    return {
        "status": "geometry_verified",
        "method": "mock_sheet_metal_base_flange_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "sheet_metal_thickness_mm": float(params["thickness"]),
        "bend_radius_mm": float(params["bend_radius"]),
        "bbox_min_m": [-float(params["length"]) / 2000, -float(params["width"]) / 2000, 0.0],
        "bbox_max_m": [
            float(params["length"]) / 2000,
            float(params["width"]) / 2000,
            float(params["thickness"]) / 1000,
        ],
        "failure_reason": None,
    }


def _mock_sheet_metal_base_flange_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock sheet metal."""

    params = sheet_metal_base_flange_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_sheet_metal_base_flange operation was found."}
    length_m = float(params["length"]) / 1000
    width_m = float(params["width"]) / 1000
    thickness_m = float(params["thickness"]) / 1000
    volume_m3 = length_m * width_m * thickness_m
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_sheet_metal_base_flange_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2 * (length_m * width_m) + 2 * thickness_m * (length_m + width_m),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_weldment_cut_list_rows(params: dict[str, Any], plan: ModelPlan) -> list[dict[str, Any]]:
    """Return deterministic cut-list rows for the controlled rectangular weldment frame."""

    profile = params["profile"]
    material = _mock_plan_material(plan)
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


def _mock_plan_material(plan: ModelPlan) -> str:
    """Return the final requested material, or a deterministic steel default."""

    material = "Plain Carbon Steel"
    for operation in plan.operations:
        if operation.op == "assign_material":
            material = str(operation.parameters.get("material") or material)
    return material


def _mock_weldment_frame_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock weldment frames."""

    params = weldment_frame_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_weldment_frame operation was found."}
    profile_size = float(params["profile_outer_width"])
    expected_dimensions = sorted(
        [float(params["length"]), float(params["width"]), profile_size]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    length_m = float(params["length"]) / 1000
    width_m = float(params["width"]) / 1000
    profile_m = profile_size / 1000
    return {
        "status": "geometry_verified",
        "workflow": "weldment_frame",
        "method": "mock_weldment_frame_bounding_box",
        "body_count": 4,
        "expected_dimensions_mm": expected_dimensions,
        "measured_dimensions_mm": list(expected_dimensions),
        "profile_outer_width_mm": profile_size,
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-length_m / 2, -width_m / 2, -profile_m / 2],
        "bbox_max_m": [length_m / 2, width_m / 2, profile_m / 2],
        "failure_reason": None,
    }


def _mock_weldment_frame_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock weldment frames."""

    params = weldment_frame_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_weldment_frame operation was found."}
    profile = params["profile"]
    outer_m = float(profile["outer_width"]) / 1000
    inner_m = max(outer_m - (2 * float(profile["wall_thickness"]) / 1000), 0.0)
    area_m2 = max(outer_m**2 - inner_m**2, 0.0)
    total_length_m = 2 * (float(params["centerline_length"]) + float(params["centerline_width"])) / 1000
    volume_m3 = area_m2 * total_length_m
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_weldment_frame_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": total_length_m * outer_m * 4,
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _mock_static_simulation_result(params: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic static-study evidence for the controlled cantilever beam."""

    metrics = _static_simulation_metrics(params)
    acceptance = params["acceptance"]
    checks = {
        "von_mises_within_limit": metrics["max_von_mises_mpa"] <= acceptance["max_von_mises_mpa"],
        "factor_of_safety_within_limit": metrics["min_factor_of_safety"] >= acceptance["min_factor_of_safety"],
        "displacement_within_limit": metrics["max_displacement_mm"] <= acceptance["max_displacement_mm"],
    }
    rows = [
        {
            "metric": "max_von_mises_mpa",
            "value": round(metrics["max_von_mises_mpa"], 6),
            "unit": "MPa",
            "status": "pass" if checks["von_mises_within_limit"] else "fail",
            "limit": acceptance["max_von_mises_mpa"],
        },
        {
            "metric": "min_factor_of_safety",
            "value": round(metrics["min_factor_of_safety"], 6),
            "unit": "ratio",
            "status": "pass" if checks["factor_of_safety_within_limit"] else "fail",
            "limit": acceptance["min_factor_of_safety"],
        },
        {
            "metric": "max_displacement_mm",
            "value": round(metrics["max_displacement_mm"], 6),
            "unit": "mm",
            "status": "pass" if checks["displacement_within_limit"] else "fail",
            "limit": acceptance["max_displacement_mm"],
        },
    ]
    return {
        "status": "simulation_verified" if all(checks.values()) else "simulation_failed_limits",
        "method": "mock_cantilever_static_study",
        "study_type": "static",
        "study_name": "cantilever_static_baseline",
        "solver": "mock_analytic_cantilever_regression",
        "geometry": {
            "length_mm": params["length"],
            "width_mm": params["width"],
            "height_mm": params["height"],
        },
        "fixture": params["fixture"],
        "load": params["load"],
        "mesh": params["mesh"],
        "material": params["material"],
        "max_von_mises_mpa": metrics["max_von_mises_mpa"],
        "min_factor_of_safety": metrics["min_factor_of_safety"],
        "max_displacement_mm": metrics["max_displacement_mm"],
        "yield_strength_mpa": metrics["yield_strength_mpa"],
        "elastic_modulus_pa": metrics["elastic_modulus_pa"],
        "checks": checks,
        "columns": params["report"]["columns"],
        "rows": rows,
        "row_count": len(rows),
        "export_formats": params["report"]["export_formats"],
    }


def _mock_static_simulation_geometry_result(params: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for the controlled simulation beam."""

    expected_dimensions = sorted([float(params["length"]), float(params["width"]), float(params["height"])])
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    return {
        "status": "geometry_verified",
        "workflow": "static_simulation",
        "method": "mock_cantilever_beam_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [0.0, -float(params["width"]) / 2000, -float(params["height"]) / 2000],
        "bbox_max_m": [float(params["length"]) / 1000, float(params["width"]) / 2000, float(params["height"]) / 2000],
        "failure_reason": None,
    }


def _mock_static_simulation_mass_property_result(params: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic mass-property diagnostics for the controlled simulation beam."""

    length_m = float(params["length"]) / 1000
    width_m = float(params["width"]) / 1000
    height_m = float(params["height"]) / 1000
    volume_m3 = length_m * width_m * height_m
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_cantilever_beam_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2 * ((length_m * width_m) + (length_m * height_m) + (width_m * height_m)),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }


def _static_simulation_metrics(params: dict[str, Any]) -> dict[str, float]:
    """Compute a conservative cantilever-beam regression baseline."""

    length_m = float(params["length"]) / 1000
    width_m = float(params["width"]) / 1000
    height_m = float(params["height"]) / 1000
    force_n = float(params["load"]["magnitude"])
    elastic_modulus_pa = 200_000_000_000.0
    yield_strength_mpa = 250.0
    inertia_m4 = width_m * (height_m ** 3) / 12
    max_moment_nm = force_n * length_m
    max_stress_pa = max_moment_nm * (height_m / 2) / inertia_m4
    max_von_mises_mpa = max_stress_pa / 1_000_000
    max_displacement_mm = (force_n * (length_m ** 3) / (3 * elastic_modulus_pa * inertia_m4)) * 1000
    return {
        "max_von_mises_mpa": max_von_mises_mpa,
        "min_factor_of_safety": yield_strength_mpa / max_von_mises_mpa if max_von_mises_mpa else 0.0,
        "max_displacement_mm": max_displacement_mm,
        "yield_strength_mpa": yield_strength_mpa,
        "elastic_modulus_pa": elastic_modulus_pa,
    }


def _mock_sleeve_geometry_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic geometry readback diagnostics for mock sleeves."""

    params = sleeve_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_sleeve operation was found."}
    expected_dimensions = sorted(
        [float(params["outer_diameter"]), float(params["outer_diameter"]), float(params["length"])]
    )
    checks = [
        {
            "axis_index": index,
            "expected_mm": expected,
            "measured_mm": expected,
            "error_mm": 0.0,
            "tolerance_mm": max(0.5, expected * 0.005),
            "ok": True,
        }
        for index, expected in enumerate(expected_dimensions)
    ]
    radius_m = float(params["outer_diameter"]) / 2000
    length_m = float(params["length"]) / 1000
    return {
        "status": "geometry_verified",
        "method": "mock_sleeve_bounding_box",
        "body_count": 1,
        "expected_dimensions_mm": expected_dimensions,
        "measured_dimensions_mm": list(expected_dimensions),
        "max_error_mm": 0.0,
        "dimension_checks": checks,
        "bbox_min_m": [-radius_m, -radius_m, 0.0],
        "bbox_max_m": [radius_m, radius_m, length_m],
        "failure_reason": None,
    }


def _mock_sleeve_mass_property_result(plan: ModelPlan) -> dict[str, Any]:
    """Return deterministic positive mass-property diagnostics for mock sleeves."""

    params = sleeve_parameters_from_plan(plan)
    if params is None:
        return {"status": "not_requested", "failure_reason": "No create_sleeve operation was found."}
    outer_radius_m = float(params["outer_diameter"]) / 2000
    inner_radius_m = float(params["inner_diameter"]) / 2000
    length_m = float(params["length"]) / 1000
    volume_m3 = 3.141592653589793 * (outer_radius_m**2 - inner_radius_m**2) * length_m
    density_kg_per_m3 = 7850.0
    mass_kg = volume_m3 * density_kg_per_m3
    return {
        "status": "mass_properties_verified",
        "method": "mock_sleeve_mass_properties",
        "mass_kg": mass_kg,
        "volume_m3": volume_m3,
        "surface_area_m2": 2 * 3.141592653589793 * (outer_radius_m**2 - inner_radius_m**2),
        "density_kg_per_m3": density_kg_per_m3,
        "checks": {
            "positive_mass": mass_kg > 0,
            "positive_volume": volume_m3 > 0,
        },
        "failure_reason": None,
    }
