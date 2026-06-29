"""Restricted plan and report types for AI-assisted CAD execution.

The plan schema intentionally describes high-level modeling intent instead of
raw SolidWorks API calls.  This gives the AI a stable contract while keeping COM
quirks, unit conversion and fallback behavior inside the adapter layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
from typing import Any, Literal

from solidworks_mcp.repair import build_repair_actions


SUPPORTED_UNITS = {"mm", "cm", "m", "inch", "ft"}
SUPPORTED_EXPORT_FORMATS = {
    "sldprt",
    "sldasm",
    "slddrw",
    "csv",
    "pdf",
    "dwg",
    "dxf",
    "step",
    "stl",
    "iges",
    "x_t",
    "x_b",
}
SUPPORTED_THREAD_STANDARDS = {"ISO_metric_coarse"}
SUPPORTED_THREAD_SPECS = {"M3", "M4", "M5", "M6", "M8"}
SUPPORTED_SEMANTIC_SELECTORS = {"top_face", "outer_edges"}
SUPPORTED_DRAWING_VIEW_STYLES = {"standard", "manufacturing_rotational", "assembly_general"}
ISO_METRIC_COARSE_THREAD_GEOMETRY = {
    "M3": {"nominal_diameter": 3.0, "tap_drill_diameter": 2.5, "pitch": 0.5},
    "M4": {"nominal_diameter": 4.0, "tap_drill_diameter": 3.3, "pitch": 0.7},
    "M5": {"nominal_diameter": 5.0, "tap_drill_diameter": 4.2, "pitch": 0.8},
    "M6": {"nominal_diameter": 6.0, "tap_drill_diameter": 5.0, "pitch": 1.0},
    "M8": {"nominal_diameter": 8.0, "tap_drill_diameter": 6.8, "pitch": 1.25},
}
MIN_MOUNTING_PLATE_WALL_MM = 2.0
MIN_MOUNTING_PLATE_HOLE_TO_FILLET_CLEARANCE_MM = 1.0
SUPPORTED_OPERATIONS = {
    "create_bom_assembly",
    "create_bracket",
    "create_center_hole_flange",
    "create_center_hole_plate",
    "create_end_cap",
    "create_mounting_block",
    "create_mounting_plate",
    "create_shaft",
    "create_sheet_metal_base_flange",
    "create_sleeve",
    "create_slotted_array_plate",
    "create_washer",
    "create_weldment_frame",
    "create_plane",
    "create_sketch",
    "extrude",
    "cut",
    "hole",
    "fillet",
    "chamfer",
    "import_existing_model",
    "linear_pattern",
    "circular_pattern",
    "revolve",
    "sweep",
    "loft",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
    "run_static_simulation",
}


class PlanValidationError(ValueError):
    """Raised when an incoming model plan is not safe to execute."""


@dataclass(frozen=True)
class DrawingProfile:
    """Engineering drawing protocol block requested by a model plan.

    ``DrawingProfile`` describes the drawing deliverables the AI wants after
    modeling, such as sheet format, projection style, default views and drawing
    export formats.  It is deliberately small so the adapter can choose stable
    SolidWorks API calls and report partial annotation failures without changing
    the main model execution contract.
    """

    enabled: bool = True
    template_path: str | None = None
    sheet_format: str = "A3"
    projection: Literal["third_angle", "first_angle"] = "third_angle"
    view_style: Literal["standard", "manufacturing_rotational", "assembly_general"] = "standard"
    include_isometric: bool = True
    include_basic_dimensions: bool = True
    export_formats: tuple[str, ...] = ("pdf", "dwg")
    auto_layout: bool = True
    margin_mm: float = 18.0
    title_block_height_mm: float = 42.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "DrawingProfile":
        """Parse and validate the optional drawing profile section."""

        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise PlanValidationError("drawing_profile must be an object when provided")

        export_formats = _parse_string_list(
            raw.get("export_formats", ("pdf", "dwg")),
            "drawing_profile.export_formats",
        )
        unsupported = sorted(set(export_formats) - SUPPORTED_EXPORT_FORMATS)
        if unsupported:
            raise PlanValidationError(f"Unsupported drawing export formats: {unsupported}")

        projection = raw.get("projection", "third_angle")
        if projection not in {"third_angle", "first_angle"}:
            raise PlanValidationError("drawing_profile.projection must be third_angle or first_angle")
        view_style = raw.get("view_style", raw.get("style", "standard"))
        if view_style not in SUPPORTED_DRAWING_VIEW_STYLES:
            raise PlanValidationError(
                "drawing_profile.view_style must be standard, manufacturing_rotational, or assembly_general"
            )

        return cls(
            enabled=bool(raw.get("enabled", True)),
            template_path=raw.get("template_path"),
            sheet_format=str(raw.get("sheet_format", "A3")),
            projection=projection,
            view_style=view_style,
            include_isometric=bool(raw.get("include_isometric", True)),
            include_basic_dimensions=bool(raw.get("include_basic_dimensions", True)),
            export_formats=export_formats,
            auto_layout=bool(raw.get("auto_layout", True)),
            margin_mm=_positive_float_or_default(raw.get("margin_mm"), 18.0, "drawing_profile.margin_mm"),
            title_block_height_mm=_positive_float_or_default(
                raw.get("title_block_height_mm"),
                42.0,
                "drawing_profile.title_block_height_mm",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for MCP responses."""

        return {
            "enabled": self.enabled,
            "template_path": self.template_path,
            "sheet_format": self.sheet_format,
            "projection": self.projection,
            "view_style": self.view_style,
            "include_isometric": self.include_isometric,
            "include_basic_dimensions": self.include_basic_dimensions,
            "export_formats": list(self.export_formats),
            "auto_layout": self.auto_layout,
            "margin_mm": self.margin_mm,
            "title_block_height_mm": self.title_block_height_mm,
        }


@dataclass(frozen=True)
class ModelOperation:
    """One executable modeling operation in execution order.

    The operation name must be present in ``SUPPORTED_OPERATIONS``.  Capability
    catalog entries marked ``planned``, ``research`` or ``blocked`` are protocol
    notes only and must not appear here until schema validation and adapter
    execution are both implemented.
    """

    op: str
    parameters: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    description: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any], index: int) -> "ModelOperation":
        """Parse one operation and enforce the whitelisted operation set."""

        if not isinstance(raw, dict):
            raise PlanValidationError(f"operations[{index}] must be an object")

        op = raw.get("op")
        if op not in SUPPORTED_OPERATIONS:
            raise PlanValidationError(
                f"operations[{index}].op must be one of {sorted(SUPPORTED_OPERATIONS)}"
            )

        parameters = raw.get("parameters", {})
        if not isinstance(parameters, dict):
            raise PlanValidationError(f"operations[{index}].parameters must be an object")

        _validate_required_operation_fields(op, parameters, index)
        return cls(
            op=op,
            parameters=parameters,
            id=raw.get("id"),
            description=raw.get("description"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for reports."""

        return {
            "id": self.id,
            "op": self.op,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class ModelPlan:
    """A complete, reviewable CAD protocol payload for AI-assisted modeling.

    MCP clients should create a ``ModelPlan`` after discussing the design with
    the user, then call ``validate_model_plan`` before asking for confirmation.
    The plan keeps high-level CAD intent in JSON while SolidWorks COM details,
    semantic selection, fallback behavior and debug artifacts remain inside the
    executor and adapter layers.
    """

    name: str
    units: str
    operations: tuple[ModelOperation, ...]
    drawing_profile: DrawingProfile = field(default_factory=DrawingProfile)
    metadata: dict[str, Any] = field(default_factory=dict)
    output_formats: tuple[str, ...] = ("sldprt", "step", "stl")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ModelPlan":
        """Parse a raw MCP argument into a validated model plan."""

        if not isinstance(raw, dict):
            raise PlanValidationError("plan must be an object")

        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise PlanValidationError("plan.name is required")

        units = raw.get("units", "mm")
        if units not in SUPPORTED_UNITS:
            raise PlanValidationError(f"plan.units must be one of {sorted(SUPPORTED_UNITS)}")

        raw_operations = raw.get("operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise PlanValidationError("plan.operations must be a non-empty array")

        operations = tuple(
            ModelOperation.from_dict(operation, index)
            for index, operation in enumerate(raw_operations)
        )

        output_formats = _parse_string_list(
            raw.get("output_formats", ("sldprt", "step", "stl")),
            "plan.output_formats",
        )
        unsupported = sorted(set(output_formats) - SUPPORTED_EXPORT_FORMATS)
        if unsupported:
            raise PlanValidationError(f"Unsupported output formats: {unsupported}")

        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            raise PlanValidationError("plan.metadata must be an object when provided")

        return cls(
            name=name.strip(),
            units=units,
            operations=operations,
            drawing_profile=DrawingProfile.from_dict(raw.get("drawing_profile")),
            metadata=metadata,
            output_formats=output_formats,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for MCP responses."""

        return {
            "name": self.name,
            "units": self.units,
            "metadata": self.metadata,
            "output_formats": list(self.output_formats),
            "drawing_profile": self.drawing_profile.to_dict(),
            "operations": [operation.to_dict() for operation in self.operations],
        }


@dataclass(frozen=True)
class StepResult:
    """Execution result for one modeling operation."""

    index: int
    op: str
    ok: bool
    message: str
    id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable step result."""

        return {
            "index": self.index,
            "id": self.id,
            "op": self.op,
            "ok": self.ok,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class ExecutionReport:
    """Structured feedback package returned after validation or execution.

    The report is the main MCP response contract for review and bug location.
    Confirmed executions include run identifiers, artifact paths, step results,
    preview/export files, failure class, repro command and diagnostics so a
    later Windows SolidWorks test can be diagnosed without replaying the whole
    conversation.
    """

    ok: bool
    adapter: str
    message: str
    plan: dict[str, Any] | None = None
    plan_name: str | None = None
    run_id: str | None = None
    run_dir: str | None = None
    events_file: str | None = None
    environment_file: str | None = None
    artifacts_file: str | None = None
    delivery_manifest_file: str | None = None
    step_results: tuple[StepResult, ...] = ()
    output_files: dict[str, str] = field(default_factory=dict)
    preview_files: dict[str, str] = field(default_factory=dict)
    feature_summary: list[dict[str, Any]] = field(default_factory=list)
    active_document: str | None = None
    error_step: int | None = None
    failure_class: str | None = None
    repro_command: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    report_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report for MCP tool responses."""

        return {
            "ok": self.ok,
            "production_verdict": _production_verdict_from_diagnostics(self.diagnostics),
            "adapter": self.adapter,
            "message": self.message,
            "plan": self.plan,
            "plan_name": self.plan_name,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "events_file": self.events_file,
            "environment_file": self.environment_file,
            "artifacts_file": self.artifacts_file,
            "delivery_manifest_file": self.delivery_manifest_file,
            "step_results": [result.to_dict() for result in self.step_results],
            "output_files": self.output_files,
            "preview_files": self.preview_files,
            "feature_summary": self.feature_summary,
            "active_document": self.active_document,
            "error_step": self.error_step,
            "failure_class": self.failure_class,
            "repro_command": self.repro_command,
            "diagnostics": self.diagnostics,
            "report_file": self.report_file,
        }


def _production_verdict_from_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Return a compact top-level production verdict from diagnostics."""

    acceptance = diagnostics.get("production_acceptance_result") if isinstance(diagnostics, dict) else None
    drawing_review = diagnostics.get("drawing_review") if isinstance(diagnostics, dict) else None
    drawing_review_summary = drawing_review if isinstance(drawing_review, dict) else None
    if not isinstance(acceptance, dict) or not acceptance:
        return {
            "status": "not_evaluated",
            "ok": None,
            "failures": [],
            "repair_actions": [],
            "summary": {},
            "drawing_review": drawing_review_summary,
        }
    failures = acceptance.get("failures", [])
    summary = acceptance.get("summary", {})
    return {
        "status": acceptance.get("status"),
        "ok": acceptance.get("ok"),
        "failures": failures,
        "repair_actions": acceptance.get("repair_actions") or build_repair_actions(failures, summary),
        "summary": summary,
        "drawing_review": drawing_review_summary,
    }


def _validate_required_operation_fields(op: str, parameters: dict[str, Any], index: int) -> None:
    """Validate only fields that are required to route the MVP operation safely."""

    required_by_operation = {
        "create_bracket": (
            "base_length",
            "base_width",
            "base_thickness",
            "upright_height",
            "upright_thickness",
            "hole_diameter",
        ),
        "create_bom_assembly": (
            "components",
            "bom",
        ),
        "create_mounting_plate": (
            "length",
            "width",
            "thickness",
            "corner_radius",
            "hole_pattern",
            "thread_spec",
            "thread_standard",
            "edge_offset",
        ),
        "create_center_hole_flange": (
            "outer_diameter",
            "thickness",
            "hole_diameter",
        ),
        "create_center_hole_plate": (
            "length",
            "width",
            "thickness",
            "hole_diameter",
        ),
        "create_end_cap": (
            "outer_diameter",
            "thickness",
            "center_hole_diameter",
            "bolt_circle_diameter",
            "bolt_hole_diameter",
            "bolt_hole_count",
        ),
        "create_mounting_block": (
            "length",
            "width",
            "height",
            "hole_diameter",
        ),
        "create_shaft": (
            "diameter",
            "length",
        ),
        "create_sheet_metal_base_flange": (
            "length",
            "width",
            "thickness",
            "bend_radius",
        ),
        "create_weldment_frame": (
            "length",
            "width",
            "profile",
            "cut_list",
        ),
        "create_washer": (
            "outer_diameter",
            "inner_diameter",
            "thickness",
        ),
        "create_sleeve": (
            "outer_diameter",
            "inner_diameter",
            "length",
        ),
        "create_slotted_array_plate": (
            "length",
            "width",
            "thickness",
            "slot_length",
            "slot_width",
            "hole_diameter",
            "hole_rows",
            "hole_columns",
            "hole_spacing_x",
            "hole_spacing_y",
        ),
        "create_plane": ("base_plane", "distance"),
        "create_sketch": ("plane", "entities"),
        "extrude": ("sketch_id", "depth"),
        "cut": ("sketch_id", "depth"),
        "hole": ("position", "diameter", "depth"),
        "fillet": ("radius",),
        "chamfer": ("distance",),
        "import_existing_model": ("path",),
        "linear_pattern": ("seed_id", "direction", "spacing", "count"),
        "circular_pattern": ("seed_id", "axis", "count"),
        "revolve": ("sketch_id", "axis", "angle"),
        "sweep": ("profile_sketch_id", "path_sketch_id"),
        "loft": ("profile_sketch_ids",),
        "assign_material": ("material",),
        "set_custom_properties": ("properties",),
        "make_drawing": (),
        "run_static_simulation": (
            "study_type",
            "geometry",
            "fixture",
            "load",
            "mesh",
            "acceptance",
            "report",
        ),
    }
    missing = [field_name for field_name in required_by_operation[op] if field_name not in parameters]
    if missing:
        raise PlanValidationError(f"operations[{index}] missing required fields for {op}: {missing}")

    if op == "create_bracket":
        _validate_bracket_fields(parameters, index)
    if op == "create_bom_assembly":
        _validate_bom_assembly_fields(parameters, index)
    if op == "create_mounting_plate":
        _validate_mounting_plate_fields(parameters, index)
    if op == "create_center_hole_flange":
        _validate_center_hole_flange_fields(parameters, index)
    if op == "create_center_hole_plate":
        _validate_center_hole_plate_fields(parameters, index)
    if op == "create_end_cap":
        _validate_end_cap_fields(parameters, index)
    if op == "create_mounting_block":
        _validate_mounting_block_fields(parameters, index)
    if op == "create_shaft":
        _validate_shaft_fields(parameters, index)
    if op == "create_sheet_metal_base_flange":
        _validate_sheet_metal_base_flange_fields(parameters, index)
    if op == "create_weldment_frame":
        _validate_weldment_frame_fields(parameters, index)
    if op == "create_washer":
        _validate_washer_fields(parameters, index)
    if op == "create_sleeve":
        _validate_sleeve_fields(parameters, index)
    if op == "create_slotted_array_plate":
        _validate_slotted_array_plate_fields(parameters, index)
    if op == "set_custom_properties":
        _validate_custom_properties_fields(parameters, index)
    if op in {
        "create_sketch",
        "extrude",
        "cut",
        "hole",
        "fillet",
        "chamfer",
        "linear_pattern",
        "circular_pattern",
        "revolve",
    }:
        _validate_atomic_geometry_fields(op, parameters, index)
    if op in {"create_plane", "revolve", "sweep", "loft", "linear_pattern", "circular_pattern"}:
        _validate_atomic_reference_fields(op, parameters, index)
    if op == "run_static_simulation":
        _validate_static_simulation_fields(parameters, index)
    if op == "import_existing_model":
        _validate_existing_model_fields(parameters, index)


def _validate_existing_model_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate an existing SolidWorks model import request."""

    raw_path = parameters.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise PlanValidationError(f"operations[{index}].parameters.path must be a non-empty string")
    path = Path(raw_path)
    suffix = path.suffix.lower()
    if suffix not in {".sldprt", ".sldasm"}:
        raise PlanValidationError(
            f"operations[{index}].parameters.path must point to a .sldprt or .sldasm file"
        )
    if not path.exists():
        raise PlanValidationError(f"operations[{index}].parameters.path does not exist: {raw_path}")
    if not path.is_file():
        raise PlanValidationError(f"operations[{index}].parameters.path must be a file: {raw_path}")

    copy_to_run_dir = parameters.get("copy_to_run_dir", True)
    if not isinstance(copy_to_run_dir, bool):
        raise PlanValidationError(f"operations[{index}].parameters.copy_to_run_dir must be boolean when provided")

    reference_search_paths = parameters.get("reference_search_paths", [])
    if reference_search_paths is None:
        reference_search_paths = []
    if not isinstance(reference_search_paths, list):
        raise PlanValidationError(
            f"operations[{index}].parameters.reference_search_paths must be an array when provided"
        )
    for path_index, raw_reference_path in enumerate(reference_search_paths):
        if not isinstance(raw_reference_path, str) or not raw_reference_path.strip():
            raise PlanValidationError(
                f"operations[{index}].parameters.reference_search_paths[{path_index}] must be a non-empty string"
            )
        reference_path = Path(raw_reference_path)
        if not reference_path.exists():
            raise PlanValidationError(
                f"operations[{index}].parameters.reference_search_paths[{path_index}] does not exist: "
                f"{raw_reference_path}"
            )
        if not reference_path.is_dir():
            raise PlanValidationError(
                f"operations[{index}].parameters.reference_search_paths[{path_index}] must be a directory: "
                f"{raw_reference_path}"
            )

    document_type = parameters.get("document_type")
    if document_type is not None:
        document_type = str(document_type).lower()
        if document_type not in {"part", "assembly"}:
            raise PlanValidationError(
                f"operations[{index}].parameters.document_type must be part or assembly when provided"
            )
        if suffix == ".sldprt" and document_type != "part":
            raise PlanValidationError(
                f"operations[{index}].parameters.document_type=assembly conflicts with .sldprt path"
            )
        if suffix == ".sldasm" and document_type != "assembly":
            raise PlanValidationError(
                f"operations[{index}].parameters.document_type=part conflicts with .sldasm path"
            )


def _validate_atomic_geometry_fields(op: str, parameters: dict[str, Any], index: int) -> None:
    """Validate atomic geometry parameters before feature-graph staging."""

    if op == "create_sketch":
        _validate_atomic_sketch_fields(parameters, index)
    if op in {"extrude", "cut"}:
        _validate_positive_number(parameters.get("depth"), f"operations[{index}].parameters.depth")
    if op == "hole":
        _validate_point2(parameters.get("position"), f"operations[{index}].parameters.position")
        _validate_positive_number(parameters.get("diameter"), f"operations[{index}].parameters.diameter")
        _validate_positive_number(parameters.get("depth"), f"operations[{index}].parameters.depth")
        positions = parameters.get("positions")
        if positions is not None:
            if not isinstance(positions, list) or not positions:
                raise PlanValidationError(f"operations[{index}].parameters.positions must be a non-empty array")
            for point_index, point in enumerate(positions):
                _validate_point2(point, f"operations[{index}].parameters.positions[{point_index}]")
    if op == "fillet":
        _validate_positive_number(parameters.get("radius"), f"operations[{index}].parameters.radius")
    if op == "chamfer":
        _validate_positive_number(parameters.get("distance"), f"operations[{index}].parameters.distance")
    if op == "circular_pattern" and "angle" in parameters:
        _validate_bounded_positive_number(
            parameters.get("angle"),
            f"operations[{index}].parameters.angle",
            upper_bound=360,
        )
    if op == "revolve":
        _validate_bounded_positive_number(
            parameters.get("angle"),
            f"operations[{index}].parameters.angle",
            upper_bound=360,
        )


def _validate_atomic_sketch_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate supported atomic sketch entities and optional dimensions."""

    entities = parameters.get("entities")
    if not isinstance(entities, list) or not entities:
        raise PlanValidationError(f"operations[{index}].parameters.entities must be a non-empty array")
    for entity_index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            raise PlanValidationError(
                f"operations[{index}].parameters.entities[{entity_index}] must be an object"
            )
        entity_id = entity.get("id")
        if entity_id is not None and (not isinstance(entity_id, str) or not entity_id.strip()):
            raise PlanValidationError(
                f"operations[{index}].parameters.entities[{entity_index}].id must be a non-empty string"
            )
        entity_type = entity.get("type")
        if entity_type == "circle":
            _validate_point2(
                entity.get("center"),
                f"operations[{index}].parameters.entities[{entity_index}].center",
            )
            if "radius" in entity:
                _validate_positive_number(
                    entity.get("radius"),
                    f"operations[{index}].parameters.entities[{entity_index}].radius",
                )
            elif "diameter" in entity:
                _validate_positive_number(
                    entity.get("diameter"),
                    f"operations[{index}].parameters.entities[{entity_index}].diameter",
                )
            else:
                raise PlanValidationError(
                    f"operations[{index}].parameters.entities[{entity_index}] circle requires radius or diameter"
                )
        elif entity_type == "center_rectangle":
            _validate_point2(
                entity.get("center"),
                f"operations[{index}].parameters.entities[{entity_index}].center",
            )
            _validate_positive_number(
                entity.get("width"),
                f"operations[{index}].parameters.entities[{entity_index}].width",
            )
            _validate_positive_number(
                entity.get("height"),
                f"operations[{index}].parameters.entities[{entity_index}].height",
            )
        elif entity_type == "rectangle":
            _validate_point2(
                entity.get("corner1"),
                f"operations[{index}].parameters.entities[{entity_index}].corner1",
            )
            _validate_point2(
                entity.get("corner2"),
                f"operations[{index}].parameters.entities[{entity_index}].corner2",
            )
            if tuple(entity["corner1"]) == tuple(entity["corner2"]):
                raise PlanValidationError(
                    f"operations[{index}].parameters.entities[{entity_index}] rectangle corners must differ"
                )
        elif entity_type == "line":
            _validate_point2(
                entity.get("start"),
                f"operations[{index}].parameters.entities[{entity_index}].start",
            )
            _validate_point2(
                entity.get("end"),
                f"operations[{index}].parameters.entities[{entity_index}].end",
            )
            if tuple(entity["start"]) == tuple(entity["end"]):
                raise PlanValidationError(
                    f"operations[{index}].parameters.entities[{entity_index}] line endpoints must differ"
                )
        else:
            raise PlanValidationError(
                f"operations[{index}].parameters.entities[{entity_index}].type must be circle, center_rectangle, rectangle, or line"
            )
        construction = entity.get("construction")
        if construction is not None and not isinstance(construction, bool):
            raise PlanValidationError(
                f"operations[{index}].parameters.entities[{entity_index}].construction must be a boolean"
            )

    dimensions = parameters.get("dimensions", [])
    if not isinstance(dimensions, list):
        raise PlanValidationError(f"operations[{index}].parameters.dimensions must be an array")
    for dimension_index, dimension in enumerate(dimensions):
        if not isinstance(dimension, dict):
            raise PlanValidationError(
                f"operations[{index}].parameters.dimensions[{dimension_index}] must be an object"
            )
        dimension_id = dimension.get("id")
        if dimension_id is not None and (not isinstance(dimension_id, str) or not dimension_id.strip()):
            raise PlanValidationError(
                f"operations[{index}].parameters.dimensions[{dimension_index}].id must be a non-empty string"
            )
        if "value" in dimension:
            _validate_positive_number(
                dimension.get("value"),
                f"operations[{index}].parameters.dimensions[{dimension_index}].value",
            )


def _validate_atomic_reference_fields(op: str, parameters: dict[str, Any], index: int) -> None:
    """Validate production atomic operation fields that depend on named graph references."""

    if op in {"linear_pattern", "circular_pattern"}:
        _validate_positive_int(parameters.get("count"), f"operations[{index}].parameters.count")
    if op == "linear_pattern":
        _validate_positive_number(parameters.get("spacing"), f"operations[{index}].parameters.spacing")
    if op == "revolve":
        _validate_positive_number(parameters.get("angle"), f"operations[{index}].parameters.angle")
    if op == "create_plane":
        base_plane = parameters.get("base_plane")
        if not isinstance(base_plane, str) or not base_plane.strip():
            raise PlanValidationError(f"operations[{index}].parameters.base_plane must be a non-empty string")
        _validate_positive_number(parameters.get("distance"), f"operations[{index}].parameters.distance")
    if op == "loft":
        profiles = parameters.get("profile_sketch_ids")
        if not isinstance(profiles, list) or len(profiles) < 2:
            raise PlanValidationError(
                f"operations[{index}].parameters.profile_sketch_ids must contain at least two sketch ids"
            )
        for profile_index, profile_id in enumerate(profiles):
            if not isinstance(profile_id, str) or not profile_id.strip():
                raise PlanValidationError(
                    f"operations[{index}].parameters.profile_sketch_ids[{profile_index}] must be a non-empty string"
                )
    if op == "sweep" and "profile_diameter" in parameters:
        _validate_positive_number(parameters.get("profile_diameter"), f"operations[{index}].parameters.profile_diameter")


def _validate_point2(value: Any, field_name: str) -> None:
    """Validate a two-coordinate point used by atomic sketches and holes."""

    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PlanValidationError(f"{field_name} must be a two-number array")
    for coordinate_index, coordinate in enumerate(value):
        try:
            float(coordinate)
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(f"{field_name}[{coordinate_index}] must be numeric") from exc


def _validate_positive_number(value: Any, field_name: str) -> None:
    """Validate a numeric field used by atomic production operations."""

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(f"{field_name} must be numeric") from exc
    if number <= 0:
        raise PlanValidationError(f"{field_name} must be greater than zero")


def _validate_bounded_positive_number(value: Any, field_name: str, *, upper_bound: float) -> None:
    """Validate a positive number that must stay within a production-safe range."""

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(f"{field_name} must be numeric") from exc
    if number <= 0:
        raise PlanValidationError(f"{field_name} must be greater than zero")
    if number > upper_bound:
        raise PlanValidationError(f"{field_name} must be less than or equal to {upper_bound:g}")


def _validate_positive_int(value: Any, field_name: str) -> None:
    """Validate a positive integer count."""

    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(f"{field_name} must be an integer") from exc
    if number < 2:
        raise PlanValidationError(f"{field_name} must be at least 2")


def _validate_quantity_int(value: Any, field_name: str) -> None:
    """Validate a positive BOM quantity."""

    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(f"{field_name} must be an integer") from exc
    if number < 1:
        raise PlanValidationError(f"{field_name} must be at least 1")
    if number > 999:
        raise PlanValidationError(f"{field_name} must be at most 999")


def _validate_custom_properties_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate a controlled custom-property write request."""

    properties = parameters.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise PlanValidationError(
            f"operations[{index}].parameters.properties must be a non-empty object"
        )
    for key, value in properties.items():
        if not isinstance(key, str) or not key.strip():
            raise PlanValidationError(
                f"operations[{index}].parameters.properties keys must be non-empty strings"
            )
        if len(key.strip()) > 80:
            raise PlanValidationError(
                f"operations[{index}].parameters.properties key {key!r} is too long"
            )
        if isinstance(value, (dict, list, tuple, set)):
            raise PlanValidationError(
                f"operations[{index}].parameters.properties[{key!r}] must be a scalar value"
            )
    scope = str(parameters.get("scope", "document"))
    if scope not in {"document", "configuration"}:
        raise PlanValidationError(
            f"operations[{index}].parameters.scope must be document or configuration"
        )


def _validate_mounting_plate_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the high-level mounting plate template used for Windows smoke tests."""

    numeric_values: dict[str, float] = {}
    for numeric_field in ("length", "width", "thickness", "corner_radius", "edge_offset"):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    thread_standard = parameters["thread_standard"]
    if thread_standard not in SUPPORTED_THREAD_STANDARDS:
        raise PlanValidationError(
            f"operations[{index}].parameters.thread_standard must be one of {sorted(SUPPORTED_THREAD_STANDARDS)}"
        )

    thread_spec = str(parameters["thread_spec"]).upper()
    if thread_spec not in SUPPORTED_THREAD_SPECS:
        raise PlanValidationError(
            f"operations[{index}].parameters.thread_spec must be one of {sorted(SUPPORTED_THREAD_SPECS)}"
        )
    _validate_mounting_plate_geometry(numeric_values, thread_spec, index)

    hole_pattern = parameters["hole_pattern"]
    if not isinstance(hole_pattern, dict):
        raise PlanValidationError(f"operations[{index}].parameters.hole_pattern must be an object")

    pattern_type = hole_pattern.get("type")
    if pattern_type != "four_corner":
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_pattern.type must be four_corner for the MVP"
        )

    selector = hole_pattern.get("target_face", "top_face")
    if not _is_supported_selector(selector):
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_pattern.target_face is not a supported selector"
        )


def _validate_center_hole_flange_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the high-level center-hole flange template used for production expansion."""

    numeric_values: dict[str, float] = {}
    for numeric_field in ("outer_diameter", "thickness", "hole_diameter"):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    outer_diameter = numeric_values["outer_diameter"]
    thickness = numeric_values["thickness"]
    hole_diameter = numeric_values["hole_diameter"]
    if hole_diameter >= outer_diameter:
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_diameter must be smaller than outer_diameter"
        )
    radial_wall = (outer_diameter - hole_diameter) / 2
    minimum_wall = max(2.0, thickness * 0.25)
    if radial_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_diameter leaves only {radial_wall:.2f} mm radial wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted flange smoke coverage"
        )


def _validate_center_hole_plate_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the high-level center-hole plate template used for production expansion."""

    numeric_values: dict[str, float] = {}
    for numeric_field in ("length", "width", "thickness", "hole_diameter"):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    length = numeric_values["length"]
    width = numeric_values["width"]
    thickness = numeric_values["thickness"]
    hole_diameter = numeric_values["hole_diameter"]
    shorter_side = min(length, width)
    if hole_diameter >= shorter_side:
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_diameter must be smaller than the shorter plate side"
        )
    side_wall = (shorter_side - hole_diameter) / 2
    minimum_wall = max(3.0, thickness * 0.35)
    if side_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_diameter leaves only {side_wall:.2f} mm side wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted center-hole plate smoke coverage"
        )


def _validate_bracket_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the controlled L-bracket template."""

    numeric_values: dict[str, float] = {}
    for numeric_field in (
        "base_length",
        "base_width",
        "base_thickness",
        "upright_height",
        "upright_thickness",
        "hole_diameter",
    ):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    base_length = numeric_values["base_length"]
    base_width = numeric_values["base_width"]
    base_thickness = numeric_values["base_thickness"]
    upright_height = numeric_values["upright_height"]
    upright_thickness = numeric_values["upright_thickness"]
    hole_diameter = numeric_values["hole_diameter"]
    if upright_height <= base_thickness:
        raise PlanValidationError(
            f"operations[{index}].parameters.upright_height must be greater than base_thickness"
        )
    if upright_thickness >= base_length:
        raise PlanValidationError(
            f"operations[{index}].parameters.upright_thickness must be smaller than base_length"
        )
    minimum_wall = max(2.0, min(base_thickness, upright_thickness) * 0.25)
    base_hole_wall = (base_thickness - hole_diameter) / 2
    if base_hole_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_diameter leaves only {base_hole_wall:.2f} mm base-hole wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted bracket smoke coverage"
        )
    upright_hole_wall = (upright_thickness - hole_diameter) / 2
    if upright_hole_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_diameter leaves only {upright_hole_wall:.2f} mm upright-hole wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted bracket smoke coverage"
        )
    base_span_wall = (base_length - upright_thickness - hole_diameter) / 2
    if base_span_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.base_length leaves only {base_span_wall:.2f} mm base hole span; "
            f"requires at least {minimum_wall:.2f} mm for trusted bracket smoke coverage"
        )
    upright_span_wall = (upright_height - base_thickness - hole_diameter) / 2
    if upright_span_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.upright_height leaves only {upright_span_wall:.2f} mm upright hole span; "
            f"requires at least {minimum_wall:.2f} mm for trusted bracket smoke coverage"
        )
    width_wall = (base_width - hole_diameter) / 2
    if width_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.base_width leaves only {width_wall:.2f} mm through-hole side wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted bracket smoke coverage"
        )


def _validate_bom_assembly_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate a controlled assembly plus BOM production fixture."""

    components = parameters.get("components")
    if not isinstance(components, list) or len(components) < 2:
        raise PlanValidationError(f"operations[{index}].parameters.components must contain at least two components")

    component_ids: set[str] = set()
    for component_index, component in enumerate(components):
        if not isinstance(component, dict):
            raise PlanValidationError(
                f"operations[{index}].parameters.components[{component_index}] must be an object"
            )
        component_id = component.get("id")
        if not isinstance(component_id, str) or not component_id.strip():
            raise PlanValidationError(
                f"operations[{index}].parameters.components[{component_index}].id must be a non-empty string"
            )
        if component_id in component_ids:
            raise PlanValidationError(
                f"operations[{index}].parameters.components[{component_index}].id duplicates {component_id!r}"
            )
        component_ids.add(component_id)

        kind = component.get("kind")
        if kind not in {"plate", "spacer", "bracket"}:
            raise PlanValidationError(
                f"operations[{index}].parameters.components[{component_index}].kind must be plate, spacer, or bracket"
            )
        quantity = component.get("quantity", 1)
        _validate_quantity_int(quantity, f"operations[{index}].parameters.components[{component_index}].quantity")
        dimensions = component.get("dimensions")
        if not isinstance(dimensions, dict):
            raise PlanValidationError(
                f"operations[{index}].parameters.components[{component_index}].dimensions must be an object"
            )
        required_dimensions = {
            "plate": ("length", "width", "thickness"),
            "spacer": ("outer_diameter", "inner_diameter", "length"),
            "bracket": ("base_length", "base_width", "base_thickness", "upright_height", "upright_thickness"),
        }[str(kind)]
        for field_name in required_dimensions:
            if field_name not in dimensions:
                raise PlanValidationError(
                    f"operations[{index}].parameters.components[{component_index}].dimensions missing {field_name}"
                )
            _validate_positive_number(
                dimensions.get(field_name),
                f"operations[{index}].parameters.components[{component_index}].dimensions.{field_name}",
            )
        material = component.get("material")
        if material is not None and (not isinstance(material, str) or not material.strip()):
            raise PlanValidationError(
                f"operations[{index}].parameters.components[{component_index}].material must be a non-empty string"
            )

    bom = parameters.get("bom")
    if not isinstance(bom, dict):
        raise PlanValidationError(f"operations[{index}].parameters.bom must be an object")
    columns = bom.get("columns", ["item", "part_number", "description", "quantity", "material"])
    if not isinstance(columns, list) or not columns:
        raise PlanValidationError(f"operations[{index}].parameters.bom.columns must be a non-empty array")
    allowed_columns = {"item", "component_id", "part_number", "description", "quantity", "material"}
    for column_index, column in enumerate(columns):
        if column not in allowed_columns:
            raise PlanValidationError(
                f"operations[{index}].parameters.bom.columns[{column_index}] must be one of {sorted(allowed_columns)}"
            )
    export_formats = bom.get("export_formats", ["csv"])
    if not isinstance(export_formats, list) or not export_formats:
        raise PlanValidationError(f"operations[{index}].parameters.bom.export_formats must be a non-empty array")
    unsupported = sorted(set(str(item).lower() for item in export_formats) - {"csv"})
    if unsupported:
        raise PlanValidationError(
            f"operations[{index}].parameters.bom.export_formats has unsupported formats: {unsupported}"
        )


def _validate_slotted_array_plate_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the controlled slotted hole-array plate template."""

    numeric_values: dict[str, float] = {}
    for numeric_field in (
        "length",
        "width",
        "thickness",
        "slot_length",
        "slot_width",
        "hole_diameter",
        "hole_spacing_x",
        "hole_spacing_y",
    ):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    rows = _coerce_positive_count(parameters["hole_rows"], f"operations[{index}].parameters.hole_rows")
    columns = _coerce_positive_count(
        parameters["hole_columns"],
        f"operations[{index}].parameters.hole_columns",
    )
    length = numeric_values["length"]
    width = numeric_values["width"]
    thickness = numeric_values["thickness"]
    slot_length = numeric_values["slot_length"]
    slot_width = numeric_values["slot_width"]
    hole_diameter = numeric_values["hole_diameter"]
    hole_spacing_x = numeric_values["hole_spacing_x"]
    hole_spacing_y = numeric_values["hole_spacing_y"]
    minimum_wall = max(2.0, thickness * 0.25)
    if slot_width >= slot_length:
        raise PlanValidationError(
            f"operations[{index}].parameters.slot_width must be smaller than slot_length"
        )
    if slot_width <= hole_diameter * 0.5:
        raise PlanValidationError(
            f"operations[{index}].parameters.slot_width must be wider than half the hole diameter"
        )
    slot_length_wall = (length - slot_length) / 2
    if slot_length_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.slot_length leaves only {slot_length_wall:.2f} mm end wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted slotted-array plate smoke coverage"
        )
    slot_width_wall = (width - slot_width) / 2
    if slot_width_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.slot_width leaves only {slot_width_wall:.2f} mm side wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted slotted-array plate smoke coverage"
        )
    max_hole_x = hole_spacing_x * (columns - 1) / 2
    max_hole_y = hole_spacing_y * (rows - 1) / 2
    x_wall = length / 2 - max_hole_x - hole_diameter / 2
    y_wall = width / 2 - max_hole_y - hole_diameter / 2
    if x_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_spacing_x leaves only {x_wall:.2f} mm end wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted slotted-array plate smoke coverage"
        )
    if y_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_spacing_y leaves only {y_wall:.2f} mm side wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted slotted-array plate smoke coverage"
        )
    if rows > 1:
        nearest_hole_y = hole_spacing_y / 2
        slot_clearance = nearest_hole_y - hole_diameter / 2 - slot_width / 2
        if slot_clearance < minimum_wall:
            raise PlanValidationError(
                f"operations[{index}].parameters.hole_spacing_y leaves only {slot_clearance:.2f} mm slot-to-hole clearance; "
                f"requires at least {minimum_wall:.2f} mm for trusted slotted-array plate smoke coverage"
            )
    if columns > 1:
        nearest_hole_x = hole_spacing_x / 2
        slot_clearance_x = nearest_hole_x - hole_diameter / 2 - slot_length / 2
        if slot_clearance_x < -hole_diameter:
            raise PlanValidationError(
                f"operations[{index}].parameters.hole_spacing_x places the hole array too close to the slot ends"
            )


def _coerce_positive_count(value: Any, field_name: str) -> int:
    """Validate and return a count for controlled feature arrays."""

    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(f"{field_name} must be an integer") from exc
    if count < 2:
        raise PlanValidationError(f"{field_name} must be at least 2")
    if count > 6:
        raise PlanValidationError(f"{field_name} must be at most 6 for trusted smoke coverage")
    return count


def _validate_end_cap_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the controlled end-cap template with a centered bore and bolt-hole pattern."""

    numeric_values: dict[str, float] = {}
    for numeric_field in (
        "outer_diameter",
        "thickness",
        "center_hole_diameter",
        "bolt_circle_diameter",
        "bolt_hole_diameter",
    ):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    try:
        bolt_hole_count = int(parameters["bolt_hole_count"])
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(f"operations[{index}].parameters.bolt_hole_count must be an integer") from exc
    if bolt_hole_count < 3 or bolt_hole_count > 16:
        raise PlanValidationError(
            f"operations[{index}].parameters.bolt_hole_count must be between 3 and 16"
        )

    outer_diameter = numeric_values["outer_diameter"]
    thickness = numeric_values["thickness"]
    center_hole_diameter = numeric_values["center_hole_diameter"]
    bolt_circle_diameter = numeric_values["bolt_circle_diameter"]
    bolt_hole_diameter = numeric_values["bolt_hole_diameter"]
    if center_hole_diameter >= outer_diameter:
        raise PlanValidationError(
            f"operations[{index}].parameters.center_hole_diameter must be smaller than outer_diameter"
        )
    if bolt_circle_diameter >= outer_diameter:
        raise PlanValidationError(
            f"operations[{index}].parameters.bolt_circle_diameter must be smaller than outer_diameter"
        )
    if bolt_hole_diameter >= outer_diameter:
        raise PlanValidationError(
            f"operations[{index}].parameters.bolt_hole_diameter must be smaller than outer_diameter"
        )

    minimum_wall = max(2.0, thickness * 0.25)
    outer_wall = (outer_diameter - bolt_circle_diameter - bolt_hole_diameter) / 2
    if outer_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.bolt_hole_diameter leaves only {outer_wall:.2f} mm outer wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted end-cap smoke coverage"
        )
    center_to_bolt_wall = (bolt_circle_diameter - center_hole_diameter - bolt_hole_diameter) / 2
    if center_to_bolt_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.bolt_circle_diameter leaves only {center_to_bolt_wall:.2f} mm center-bore wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted end-cap smoke coverage"
        )
    adjacent_spacing = bolt_circle_diameter * math.sin(math.pi / bolt_hole_count) - bolt_hole_diameter
    if adjacent_spacing < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.bolt_hole_count leaves only {adjacent_spacing:.2f} mm adjacent-hole spacing; "
            f"requires at least {minimum_wall:.2f} mm for trusted end-cap smoke coverage"
        )


def _validate_washer_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the high-level washer template used for controlled-library expansion."""

    numeric_values: dict[str, float] = {}
    for numeric_field in ("outer_diameter", "inner_diameter", "thickness"):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    outer_diameter = numeric_values["outer_diameter"]
    inner_diameter = numeric_values["inner_diameter"]
    thickness = numeric_values["thickness"]
    if inner_diameter >= outer_diameter:
        raise PlanValidationError(
            f"operations[{index}].parameters.inner_diameter must be smaller than outer_diameter"
        )
    radial_wall = (outer_diameter - inner_diameter) / 2
    minimum_wall = max(1.0, thickness * 0.25)
    if radial_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.inner_diameter leaves only {radial_wall:.2f} mm radial wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted washer smoke coverage"
        )


def _validate_mounting_block_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the controlled mounting-block template."""

    numeric_values: dict[str, float] = {}
    for numeric_field in ("length", "width", "height", "hole_diameter"):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    length = numeric_values["length"]
    width = numeric_values["width"]
    height = numeric_values["height"]
    hole_diameter = numeric_values["hole_diameter"]
    shorter_side = min(length, width)
    if hole_diameter >= shorter_side:
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_diameter must be smaller than the shorter block side"
        )
    side_wall = (shorter_side - hole_diameter) / 2
    minimum_wall = max(4.0, height * 0.2)
    if side_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.hole_diameter leaves only {side_wall:.2f} mm side wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted mounting block smoke coverage"
        )
    if height < hole_diameter * 0.4:
        raise PlanValidationError(
            f"operations[{index}].parameters.height must be at least 40% of hole_diameter "
            f"for trusted mounting block smoke coverage"
        )


def _validate_shaft_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the controlled plain-shaft template."""

    numeric_values: dict[str, float] = {}
    for numeric_field in ("diameter", "length"):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    diameter = numeric_values["diameter"]
    length = numeric_values["length"]
    if length < diameter:
        raise PlanValidationError(
            f"operations[{index}].parameters.length must be at least diameter for trusted shaft smoke coverage"
        )
    if length > diameter * 20:
        raise PlanValidationError(
            f"operations[{index}].parameters.length must not exceed 20x diameter for trusted shaft smoke coverage"
        )


def _validate_sleeve_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the high-level sleeve template used for controlled-library expansion."""

    numeric_values: dict[str, float] = {}
    for numeric_field in ("outer_diameter", "inner_diameter", "length"):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    outer_diameter = numeric_values["outer_diameter"]
    inner_diameter = numeric_values["inner_diameter"]
    length = numeric_values["length"]
    if inner_diameter >= outer_diameter:
        raise PlanValidationError(
            f"operations[{index}].parameters.inner_diameter must be smaller than outer_diameter"
        )
    radial_wall = (outer_diameter - inner_diameter) / 2
    minimum_wall = max(1.5, outer_diameter * 0.05)
    if radial_wall < minimum_wall:
        raise PlanValidationError(
            f"operations[{index}].parameters.inner_diameter leaves only {radial_wall:.2f} mm radial wall; "
            f"requires at least {minimum_wall:.2f} mm for trusted sleeve smoke coverage"
        )
    if length < outer_diameter * 0.5:
        raise PlanValidationError(
            f"operations[{index}].parameters.length must be at least half of outer_diameter "
            f"for trusted sleeve smoke coverage"
        )


def _validate_sheet_metal_base_flange_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the controlled sheet-metal base-flange template."""

    numeric_values: dict[str, float] = {}
    for numeric_field in ("length", "width", "thickness", "bend_radius"):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    for optional_field in ("k_factor", "relief_width", "relief_depth"):
        if optional_field in parameters:
            _validate_positive_number(
                parameters.get(optional_field),
                f"operations[{index}].parameters.{optional_field}",
            )

    length = numeric_values["length"]
    width = numeric_values["width"]
    thickness = numeric_values["thickness"]
    bend_radius = numeric_values["bend_radius"]
    shorter_side = min(length, width)
    if thickness > shorter_side / 6:
        raise PlanValidationError(
            f"operations[{index}].parameters.thickness must be no more than one sixth of the shorter side "
            f"for trusted sheet-metal base-flange smoke coverage"
        )
    if bend_radius < thickness * 0.25:
        raise PlanValidationError(
            f"operations[{index}].parameters.bend_radius must be at least 25% of thickness"
        )
    if bend_radius > thickness * 4:
        raise PlanValidationError(
            f"operations[{index}].parameters.bend_radius must be no more than 4x thickness"
        )
    if "k_factor" in parameters:
        k_factor = float(parameters["k_factor"])
        if not 0 < k_factor <= 1:
            raise PlanValidationError(f"operations[{index}].parameters.k_factor must be between 0 and 1")


def _validate_weldment_frame_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the controlled structural-member weldment frame template."""

    numeric_values: dict[str, float] = {}
    for numeric_field in ("length", "width"):
        try:
            value = float(parameters[numeric_field])
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be numeric"
            ) from exc
        if value <= 0:
            raise PlanValidationError(
                f"operations[{index}].parameters.{numeric_field} must be greater than zero"
            )
        numeric_values[numeric_field] = value

    profile = parameters.get("profile")
    if not isinstance(profile, dict):
        raise PlanValidationError(f"operations[{index}].parameters.profile must be an object")
    profile_type = str(profile.get("type", "")).strip().lower()
    if profile_type not in {"square_tube"}:
        raise PlanValidationError(
            f"operations[{index}].parameters.profile.type must be square_tube for trusted weldment smoke coverage"
        )
    for text_field in ("standard", "size"):
        value = profile.get(text_field)
        if not isinstance(value, str) or not value.strip():
            raise PlanValidationError(
                f"operations[{index}].parameters.profile.{text_field} must be a non-empty string"
            )
    for numeric_field in ("outer_width", "outer_height", "wall_thickness"):
        _validate_positive_number(
            profile.get(numeric_field),
            f"operations[{index}].parameters.profile.{numeric_field}",
        )
    outer_width = float(profile["outer_width"])
    outer_height = float(profile["outer_height"])
    wall_thickness = float(profile["wall_thickness"])
    if abs(outer_width - outer_height) > 0.001:
        raise PlanValidationError(
            f"operations[{index}].parameters.profile square_tube requires outer_width and outer_height to match"
        )
    if wall_thickness >= outer_width / 2:
        raise PlanValidationError(
            f"operations[{index}].parameters.profile.wall_thickness must be less than half the tube size"
        )
    if numeric_values["length"] <= outer_width * 3 or numeric_values["width"] <= outer_width * 3:
        raise PlanValidationError(
            f"operations[{index}].parameters.length and width must each be greater than 3x profile.outer_width"
        )
    profile_path = profile.get("profile_path")
    if profile_path is not None and (not isinstance(profile_path, str) or not profile_path.strip()):
        raise PlanValidationError(
            f"operations[{index}].parameters.profile.profile_path must be a non-empty string when provided"
        )

    cut_list = parameters.get("cut_list")
    if not isinstance(cut_list, dict):
        raise PlanValidationError(f"operations[{index}].parameters.cut_list must be an object")
    columns = cut_list.get(
        "columns",
        ["item", "member_id", "description", "quantity", "length_mm", "profile", "material"],
    )
    if not isinstance(columns, list) or not columns:
        raise PlanValidationError(f"operations[{index}].parameters.cut_list.columns must be a non-empty array")
    allowed_columns = {
        "item",
        "member_id",
        "description",
        "quantity",
        "length_mm",
        "profile",
        "material",
    }
    for column_index, column in enumerate(columns):
        if column not in allowed_columns:
            raise PlanValidationError(
                f"operations[{index}].parameters.cut_list.columns[{column_index}] must be one of {sorted(allowed_columns)}"
            )
    export_formats = cut_list.get("export_formats", ["csv"])
    if not isinstance(export_formats, list) or not export_formats:
        raise PlanValidationError(f"operations[{index}].parameters.cut_list.export_formats must be a non-empty array")
    unsupported = sorted(set(str(item).lower() for item in export_formats) - {"csv"})
    if unsupported:
        raise PlanValidationError(
            f"operations[{index}].parameters.cut_list.export_formats has unsupported formats: {unsupported}"
        )


def _validate_static_simulation_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the controlled cantilever static-study fixture."""

    study_type = str(parameters.get("study_type", "")).strip().lower()
    if study_type != "cantilever_static":
        raise PlanValidationError(
            f"operations[{index}].parameters.study_type must be cantilever_static for trusted simulation coverage"
        )

    geometry = parameters.get("geometry")
    if not isinstance(geometry, dict):
        raise PlanValidationError(f"operations[{index}].parameters.geometry must be an object")
    for field_name in ("length", "width", "height"):
        _validate_positive_number(geometry.get(field_name), f"operations[{index}].parameters.geometry.{field_name}")
    length = float(geometry["length"])
    width = float(geometry["width"])
    height = float(geometry["height"])
    if length < max(width, height) * 3:
        raise PlanValidationError(
            f"operations[{index}].parameters.geometry.length must be at least 3x the larger section dimension"
        )

    material = geometry.get("material", parameters.get("material", "Plain Carbon Steel"))
    if material is not None and (not isinstance(material, str) or not material.strip()):
        raise PlanValidationError(
            f"operations[{index}].parameters.geometry.material must be a non-empty string when provided"
        )

    fixture = parameters.get("fixture")
    if not isinstance(fixture, dict):
        raise PlanValidationError(f"operations[{index}].parameters.fixture must be an object")
    if fixture.get("type") != "fixed_face" or fixture.get("face") != "left":
        raise PlanValidationError(
            f"operations[{index}].parameters.fixture must be {{type: fixed_face, face: left}}"
        )

    load = parameters.get("load")
    if not isinstance(load, dict):
        raise PlanValidationError(f"operations[{index}].parameters.load must be an object")
    if load.get("type") != "force" or load.get("face") != "right":
        raise PlanValidationError(
            f"operations[{index}].parameters.load must be a force on the right face"
        )
    if load.get("direction") not in {"-Y", "+Y"}:
        raise PlanValidationError(f"operations[{index}].parameters.load.direction must be -Y or +Y")
    _validate_positive_number(load.get("magnitude"), f"operations[{index}].parameters.load.magnitude")

    mesh = parameters.get("mesh")
    if not isinstance(mesh, dict):
        raise PlanValidationError(f"operations[{index}].parameters.mesh must be an object")
    _validate_positive_number(mesh.get("element_size"), f"operations[{index}].parameters.mesh.element_size")
    _validate_positive_number(mesh.get("tolerance"), f"operations[{index}].parameters.mesh.tolerance")
    if float(mesh["element_size"]) > min(width, height):
        raise PlanValidationError(
            f"operations[{index}].parameters.mesh.element_size must not exceed the smaller section dimension"
        )

    acceptance = parameters.get("acceptance")
    if not isinstance(acceptance, dict):
        raise PlanValidationError(f"operations[{index}].parameters.acceptance must be an object")
    for field_name in ("max_von_mises_mpa", "min_factor_of_safety", "max_displacement_mm"):
        _validate_positive_number(
            acceptance.get(field_name),
            f"operations[{index}].parameters.acceptance.{field_name}",
        )

    report = parameters.get("report")
    if not isinstance(report, dict):
        raise PlanValidationError(f"operations[{index}].parameters.report must be an object")
    columns = report.get("columns", ["metric", "value", "unit", "status", "limit"])
    if not isinstance(columns, list) or not columns:
        raise PlanValidationError(f"operations[{index}].parameters.report.columns must be a non-empty array")
    allowed_columns = {"metric", "value", "unit", "status", "limit"}
    for column_index, column in enumerate(columns):
        if column not in allowed_columns:
            raise PlanValidationError(
                f"operations[{index}].parameters.report.columns[{column_index}] must be one of {sorted(allowed_columns)}"
            )
    export_formats = report.get("export_formats", ["csv"])
    if not isinstance(export_formats, list) or not export_formats:
        raise PlanValidationError(f"operations[{index}].parameters.report.export_formats must be a non-empty array")
    unsupported = sorted(set(str(item).lower() for item in export_formats) - {"csv"})
    if unsupported:
        raise PlanValidationError(
            f"operations[{index}].parameters.report.export_formats has unsupported formats: {unsupported}"
        )


def _validate_mounting_plate_geometry(
    values: dict[str, float],
    thread_spec: str,
    index: int,
) -> None:
    """Reject mounting-plate inputs that would make the trusted MVP geometry invalid."""

    length = values["length"]
    width = values["width"]
    thickness = values["thickness"]
    corner_radius = values["corner_radius"]
    edge_offset = values["edge_offset"]
    thread_geometry = ISO_METRIC_COARSE_THREAD_GEOMETRY[thread_spec]
    tap_drill_diameter = float(thread_geometry["tap_drill_diameter"])
    nominal_diameter = float(thread_geometry["nominal_diameter"])
    tap_radius = tap_drill_diameter / 2
    shorter_side = min(length, width)

    if corner_radius * 2 >= shorter_side:
        raise PlanValidationError(
            f"operations[{index}].parameters.corner_radius must be less than half of the shorter plate side"
        )

    if edge_offset * 2 >= length or edge_offset * 2 >= width:
        raise PlanValidationError(
            f"operations[{index}].parameters.edge_offset must keep all four hole centers inside the plate"
        )

    minimum_edge_offset = tap_radius + MIN_MOUNTING_PLATE_WALL_MM
    if edge_offset < minimum_edge_offset:
        raise PlanValidationError(
            f"operations[{index}].parameters.edge_offset is too small for {thread_spec}; "
            f"requires at least {minimum_edge_offset:.2f} mm for tap-drill wall clearance"
        )

    minimum_fillet_offset = corner_radius + tap_radius + MIN_MOUNTING_PLATE_HOLE_TO_FILLET_CLEARANCE_MM
    if edge_offset < minimum_fillet_offset:
        raise PlanValidationError(
            f"operations[{index}].parameters.edge_offset is too close to the R{corner_radius:g} corner fillet "
            f"for {thread_spec}; requires at least {minimum_fillet_offset:.2f} mm"
        )

    minimum_hole_spacing = tap_drill_diameter + (2 * MIN_MOUNTING_PLATE_WALL_MM)
    if length - (2 * edge_offset) < minimum_hole_spacing:
        raise PlanValidationError(
            f"operations[{index}].parameters.length leaves less than {minimum_hole_spacing:.2f} mm "
            f"between {thread_spec} hole columns"
        )
    if width - (2 * edge_offset) < minimum_hole_spacing:
        raise PlanValidationError(
            f"operations[{index}].parameters.width leaves less than {minimum_hole_spacing:.2f} mm "
            f"between {thread_spec} hole rows"
        )

    if thickness < nominal_diameter:
        raise PlanValidationError(
            f"operations[{index}].parameters.thickness must be at least {nominal_diameter:.2f} mm "
            f"for trusted through-thread smoke coverage with {thread_spec}"
        )


def _is_supported_selector(selector: Any) -> bool:
    """Return whether a selector can be routed by the current semantic selector layer."""

    if selector in SUPPORTED_SEMANTIC_SELECTORS:
        return True
    if isinstance(selector, str) and (selector.startswith("feature:") or selector.startswith("sketch:")):
        return len(selector.split(":", 1)[1]) > 0
    return False


def mounting_plate_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return the trusted drawing-dimension ids implied by mounting-plate parameters."""

    return [
        f"length_{_dimension_token(parameters['length'])}",
        f"width_{_dimension_token(parameters['width'])}",
        f"thickness_{_dimension_token(parameters['thickness'])}",
        f"corner_radius_r{_dimension_token(parameters['corner_radius'])}",
        f"hole_edge_offset_{_dimension_token(parameters['edge_offset'])}",
    ]


def mounting_plate_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_mounting_plate operation in a plan."""

    for operation in plan.operations:
        if operation.op != "create_mounting_plate":
            continue
        params = operation.parameters
        return {
            "length": float(params["length"]),
            "width": float(params["width"]),
            "thickness": float(params["thickness"]),
            "corner_radius": float(params["corner_radius"]),
            "edge_offset": float(params["edge_offset"]),
        }
    return None


def mounting_plate_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required drawing-dimension ids for a plan, or an empty list when not applicable."""

    params = mounting_plate_parameters_from_plan(plan)
    return mounting_plate_basic_dimension_ids(params) if params else []


def center_hole_flange_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return the trusted drawing-dimension ids implied by center-hole flange parameters."""

    return [
        f"outer_diameter_{_dimension_token(parameters['outer_diameter'])}",
        f"hole_diameter_{_dimension_token(parameters['hole_diameter'])}",
        f"thickness_{_dimension_token(parameters['thickness'])}",
    ]


def center_hole_plate_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return the trusted drawing-dimension ids implied by center-hole plate parameters."""

    return [
        f"length_{_dimension_token(parameters['length'])}",
        f"width_{_dimension_token(parameters['width'])}",
        f"thickness_{_dimension_token(parameters['thickness'])}",
        f"hole_diameter_{_dimension_token(parameters['hole_diameter'])}",
    ]


def bracket_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return trusted drawing-dimension ids implied by bracket parameters."""

    return [
        f"base_length_{_dimension_token(parameters['base_length'])}",
        f"base_width_{_dimension_token(parameters['base_width'])}",
        f"base_thickness_{_dimension_token(parameters['base_thickness'])}",
        f"upright_height_{_dimension_token(parameters['upright_height'])}",
        f"upright_thickness_{_dimension_token(parameters['upright_thickness'])}",
        f"hole_diameter_{_dimension_token(parameters['hole_diameter'])}",
    ]


def end_cap_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return trusted drawing-dimension ids implied by end-cap parameters."""

    return [
        f"outer_diameter_{_dimension_token(parameters['outer_diameter'])}",
        f"center_hole_diameter_{_dimension_token(parameters['center_hole_diameter'])}",
        f"bolt_hole_diameter_{_dimension_token(parameters['bolt_hole_diameter'])}",
        f"thickness_{_dimension_token(parameters['thickness'])}",
    ]


def mounting_block_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return the trusted drawing-dimension ids implied by mounting-block parameters."""

    return [
        f"length_{_dimension_token(parameters['length'])}",
        f"width_{_dimension_token(parameters['width'])}",
        f"height_{_dimension_token(parameters['height'])}",
        f"hole_diameter_{_dimension_token(parameters['hole_diameter'])}",
    ]


def shaft_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return the trusted drawing-dimension ids implied by shaft parameters."""

    return [
        f"diameter_{_dimension_token(parameters['diameter'])}",
        f"length_{_dimension_token(parameters['length'])}",
    ]


def sheet_metal_base_flange_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return trusted drawing-dimension ids implied by sheet-metal base-flange parameters."""

    return [
        f"length_{_dimension_token(parameters['length'])}",
        f"width_{_dimension_token(parameters['width'])}",
        f"thickness_{_dimension_token(parameters['thickness'])}",
    ]


def weldment_frame_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return trusted drawing-dimension ids implied by weldment frame parameters."""

    return [
        f"overall_length_{_dimension_token(parameters['length'])}",
        f"overall_width_{_dimension_token(parameters['width'])}",
        f"profile_size_{_dimension_token(parameters['profile_outer_width'])}",
    ]


def static_simulation_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return trusted drawing-dimension ids implied by the static simulation beam."""

    return [
        f"beam_length_{_dimension_token(parameters['length'])}",
        f"beam_width_{_dimension_token(parameters['width'])}",
        f"beam_height_{_dimension_token(parameters['height'])}",
    ]


def washer_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return the trusted drawing-dimension ids implied by washer parameters."""

    return [
        f"outer_diameter_{_dimension_token(parameters['outer_diameter'])}",
        f"inner_diameter_{_dimension_token(parameters['inner_diameter'])}",
        f"thickness_{_dimension_token(parameters['thickness'])}",
    ]


def sleeve_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return the trusted drawing-dimension ids implied by sleeve parameters."""

    return [
        f"outer_diameter_{_dimension_token(parameters['outer_diameter'])}",
        f"inner_diameter_{_dimension_token(parameters['inner_diameter'])}",
        f"length_{_dimension_token(parameters['length'])}",
    ]


def slotted_array_plate_basic_dimension_ids(parameters: dict[str, Any]) -> list[str]:
    """Return trusted drawing-dimension ids implied by slotted-array plate parameters."""

    return [
        f"length_{_dimension_token(parameters['length'])}",
        f"width_{_dimension_token(parameters['width'])}",
        f"thickness_{_dimension_token(parameters['thickness'])}",
        f"slot_length_{_dimension_token(parameters['slot_length'])}",
        f"slot_width_{_dimension_token(parameters['slot_width'])}",
        f"hole_diameter_{_dimension_token(parameters['hole_diameter'])}",
        f"hole_spacing_x_{_dimension_token(parameters['hole_spacing_x'])}",
        f"hole_spacing_y_{_dimension_token(parameters['hole_spacing_y'])}",
    ]


def sleeve_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required sleeve drawing-dimension ids, or an empty list."""

    params = sleeve_parameters_from_plan(plan)
    return sleeve_basic_dimension_ids(params) if params else []


def sleeve_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_sleeve operation."""

    for operation in plan.operations:
        if operation.op != "create_sleeve":
            continue
        params = operation.parameters
        return {
            "outer_diameter": float(params["outer_diameter"]),
            "inner_diameter": float(params["inner_diameter"]),
            "length": float(params["length"]),
        }
    return None


def bom_assembly_parameters_from_plan(plan: "ModelPlan") -> dict[str, Any] | None:
    """Extract controlled assembly parameters for the first create_bom_assembly operation."""

    for operation in plan.operations:
        if operation.op != "create_bom_assembly":
            continue
        components: list[dict[str, Any]] = []
        for component in operation.parameters["components"]:
            dimensions = {
                str(key): float(value)
                for key, value in component["dimensions"].items()
            }
            components.append(
                {
                    "id": str(component["id"]),
                    "kind": str(component["kind"]),
                    "part_number": str(component.get("part_number") or component["id"]),
                    "description": str(component.get("description") or component["kind"]),
                    "quantity": int(component.get("quantity", 1)),
                    "material": str(component.get("material") or ""),
                    "dimensions": dimensions,
                }
            )
        bom = operation.parameters["bom"]
        return {
            "components": components,
            "bom": {
                "columns": [
                    str(item)
                    for item in bom.get("columns", ["item", "part_number", "description", "quantity", "material"])
                ],
                "export_formats": [str(item).lower() for item in bom.get("export_formats", ["csv"])],
            },
        }
    return None


def existing_model_parameters_from_plan(plan: "ModelPlan") -> dict[str, Any] | None:
    """Extract existing model import parameters from a plan."""

    for operation in plan.operations:
        if operation.op != "import_existing_model":
            continue
        path = Path(str(operation.parameters["path"]))
        suffix = path.suffix.lower()
        document_type = str(
            operation.parameters.get(
                "document_type",
                "assembly" if suffix == ".sldasm" else "part",
            )
        ).lower()
        return {
            "path": path_to_string(path),
            "document_type": document_type,
            "copy_to_run_dir": bool(operation.parameters.get("copy_to_run_dir", True)),
            "reference_search_paths": [
                path_to_string(Path(str(reference_path)))
                for reference_path in operation.parameters.get("reference_search_paths", []) or []
            ],
            "source_name": path.name,
            "source_suffix": suffix,
        }
    return None


def washer_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required washer drawing-dimension ids, or an empty list."""

    params = washer_parameters_from_plan(plan)
    return washer_basic_dimension_ids(params) if params else []


def sheet_metal_base_flange_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required sheet-metal drawing-dimension ids, or an empty list."""

    params = sheet_metal_base_flange_parameters_from_plan(plan)
    return sheet_metal_base_flange_basic_dimension_ids(params) if params else []


def weldment_frame_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required weldment frame drawing-dimension ids, or an empty list."""

    params = weldment_frame_parameters_from_plan(plan)
    return weldment_frame_basic_dimension_ids(params) if params else []


def static_simulation_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required static simulation drawing-dimension ids, or an empty list."""

    params = static_simulation_parameters_from_plan(plan)
    return static_simulation_basic_dimension_ids(params) if params else []


def sheet_metal_base_flange_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_sheet_metal_base_flange operation."""

    for operation in plan.operations:
        if operation.op != "create_sheet_metal_base_flange":
            continue
        params = operation.parameters
        thickness = float(params["thickness"])
        return {
            "length": float(params["length"]),
            "width": float(params["width"]),
            "thickness": thickness,
            "bend_radius": float(params["bend_radius"]),
            "k_factor": float(params.get("k_factor", 0.5)),
            "relief_width": float(params.get("relief_width", thickness)),
            "relief_depth": float(params.get("relief_depth", thickness)),
        }
    return None


def weldment_frame_parameters_from_plan(plan: "ModelPlan") -> dict[str, Any] | None:
    """Extract controlled weldment frame parameters for the first create_weldment_frame operation."""

    for operation in plan.operations:
        if operation.op != "create_weldment_frame":
            continue
        params = operation.parameters
        profile = params["profile"]
        outer_width = float(profile["outer_width"])
        cut_list = params["cut_list"]
        return {
            "length": float(params["length"]),
            "width": float(params["width"]),
            "centerline_length": float(params["length"]) - outer_width,
            "centerline_width": float(params["width"]) - outer_width,
            "profile": {
                "standard": str(profile["standard"]),
                "type": str(profile["type"]),
                "size": str(profile["size"]),
                "outer_width": outer_width,
                "outer_height": float(profile["outer_height"]),
                "wall_thickness": float(profile["wall_thickness"]),
                "profile_path": str(profile.get("profile_path") or ""),
            },
            "profile_outer_width": outer_width,
            "cut_list": {
                "columns": [
                    str(item)
                    for item in cut_list.get(
                        "columns",
                        ["item", "member_id", "description", "quantity", "length_mm", "profile", "material"],
                    )
                ],
                "export_formats": [str(item).lower() for item in cut_list.get("export_formats", ["csv"])],
            },
        }
    return None


def static_simulation_parameters_from_plan(plan: "ModelPlan") -> dict[str, Any] | None:
    """Extract controlled cantilever static-study parameters from a plan."""

    for operation in plan.operations:
        if operation.op != "run_static_simulation":
            continue
        params = operation.parameters
        geometry = params["geometry"]
        fixture = params["fixture"]
        load = params["load"]
        mesh = params["mesh"]
        acceptance = params["acceptance"]
        report = params["report"]
        material = geometry.get("material", params.get("material", "Plain Carbon Steel"))
        return {
            "study_type": str(params["study_type"]),
            "length": float(geometry["length"]),
            "width": float(geometry["width"]),
            "height": float(geometry["height"]),
            "material": str(material),
            "fixture": {
                "type": str(fixture["type"]),
                "face": str(fixture["face"]),
            },
            "load": {
                "type": str(load["type"]),
                "face": str(load["face"]),
                "direction": str(load["direction"]),
                "magnitude": float(load["magnitude"]),
            },
            "mesh": {
                "element_size": float(mesh["element_size"]),
                "tolerance": float(mesh["tolerance"]),
            },
            "acceptance": {
                "max_von_mises_mpa": float(acceptance["max_von_mises_mpa"]),
                "min_factor_of_safety": float(acceptance["min_factor_of_safety"]),
                "max_displacement_mm": float(acceptance["max_displacement_mm"]),
            },
            "report": {
                "columns": [
                    str(item)
                    for item in report.get("columns", ["metric", "value", "unit", "status", "limit"])
                ],
                "export_formats": [str(item).lower() for item in report.get("export_formats", ["csv"])],
            },
        }
    return None


def washer_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_washer operation."""

    for operation in plan.operations:
        if operation.op != "create_washer":
            continue
        params = operation.parameters
        return {
            "outer_diameter": float(params["outer_diameter"]),
            "inner_diameter": float(params["inner_diameter"]),
            "thickness": float(params["thickness"]),
        }
    return None


def mounting_block_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required mounting-block drawing-dimension ids, or an empty list."""

    params = mounting_block_parameters_from_plan(plan)
    return mounting_block_basic_dimension_ids(params) if params else []


def mounting_block_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_mounting_block operation."""

    for operation in plan.operations:
        if operation.op != "create_mounting_block":
            continue
        params = operation.parameters
        return {
            "length": float(params["length"]),
            "width": float(params["width"]),
            "height": float(params["height"]),
            "hole_diameter": float(params["hole_diameter"]),
        }
    return None


def bracket_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required bracket drawing-dimension ids, or an empty list."""

    params = bracket_parameters_from_plan(plan)
    return bracket_basic_dimension_ids(params) if params else []


def bracket_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_bracket operation."""

    for operation in plan.operations:
        if operation.op != "create_bracket":
            continue
        params = operation.parameters
        return {
            "base_length": float(params["base_length"]),
            "base_width": float(params["base_width"]),
            "base_thickness": float(params["base_thickness"]),
            "upright_height": float(params["upright_height"]),
            "upright_thickness": float(params["upright_thickness"]),
            "hole_diameter": float(params["hole_diameter"]),
        }
    return None


def slotted_array_plate_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required slotted-array plate drawing-dimension ids, or an empty list."""

    params = slotted_array_plate_parameters_from_plan(plan)
    return slotted_array_plate_basic_dimension_ids(params) if params else []


def slotted_array_plate_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_slotted_array_plate operation."""

    for operation in plan.operations:
        if operation.op != "create_slotted_array_plate":
            continue
        params = operation.parameters
        return {
            "length": float(params["length"]),
            "width": float(params["width"]),
            "thickness": float(params["thickness"]),
            "slot_length": float(params["slot_length"]),
            "slot_width": float(params["slot_width"]),
            "hole_diameter": float(params["hole_diameter"]),
            "hole_rows": int(params["hole_rows"]),
            "hole_columns": int(params["hole_columns"]),
            "hole_spacing_x": float(params["hole_spacing_x"]),
            "hole_spacing_y": float(params["hole_spacing_y"]),
        }
    return None


def end_cap_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required end-cap drawing-dimension ids, or an empty list."""

    params = end_cap_parameters_from_plan(plan)
    return end_cap_basic_dimension_ids(params) if params else []


def end_cap_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_end_cap operation."""

    for operation in plan.operations:
        if operation.op != "create_end_cap":
            continue
        params = operation.parameters
        return {
            "outer_diameter": float(params["outer_diameter"]),
            "thickness": float(params["thickness"]),
            "center_hole_diameter": float(params["center_hole_diameter"]),
            "bolt_circle_diameter": float(params["bolt_circle_diameter"]),
            "bolt_hole_diameter": float(params["bolt_hole_diameter"]),
            "bolt_hole_count": int(params["bolt_hole_count"]),
        }
    return None


def shaft_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required shaft drawing-dimension ids, or an empty list."""

    params = shaft_parameters_from_plan(plan)
    return shaft_basic_dimension_ids(params) if params else []


def shaft_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_shaft operation."""

    for operation in plan.operations:
        if operation.op != "create_shaft":
            continue
        params = operation.parameters
        return {
            "diameter": float(params["diameter"]),
            "length": float(params["length"]),
        }
    return None


def center_hole_plate_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required center-hole plate drawing-dimension ids, or an empty list."""

    params = center_hole_plate_parameters_from_plan(plan)
    return center_hole_plate_basic_dimension_ids(params) if params else []


def center_hole_plate_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_center_hole_plate operation."""

    for operation in plan.operations:
        if operation.op != "create_center_hole_plate":
            continue
        params = operation.parameters
        return {
            "length": float(params["length"]),
            "width": float(params["width"]),
            "thickness": float(params["thickness"]),
            "hole_diameter": float(params["hole_diameter"]),
        }
    return None


def center_hole_flange_basic_dimension_ids_from_plan(plan: "ModelPlan") -> list[str]:
    """Return required center-hole flange drawing-dimension ids, or an empty list."""

    params = center_hole_flange_parameters_from_plan(plan)
    return center_hole_flange_basic_dimension_ids(params) if params else []


def center_hole_flange_parameters_from_plan(plan: "ModelPlan") -> dict[str, float] | None:
    """Extract numeric parameters for the first create_center_hole_flange operation."""

    for operation in plan.operations:
        if operation.op != "create_center_hole_flange":
            continue
        params = operation.parameters
        return {
            "outer_diameter": float(params["outer_diameter"]),
            "thickness": float(params["thickness"]),
            "hole_diameter": float(params["hole_diameter"]),
        }
    return None


def _dimension_token(value: Any) -> str:
    """Convert a numeric plan value into a stable id token."""

    number = float(value)
    text = f"{number:.6f}".rstrip("0").rstrip(".")
    text = text.replace("-", "neg_").replace(".", "p")
    return re.sub(r"[^0-9A-Za-z_]+", "_", text)


def _parse_string_list(raw: Any, field_name: str) -> tuple[str, ...]:
    """Parse a JSON array of strings without accepting ambiguous scalar values."""

    if not isinstance(raw, (list, tuple)):
        raise PlanValidationError(f"{field_name} must be an array of strings")
    values = tuple(str(value).lower() for value in raw)
    if not values:
        raise PlanValidationError(f"{field_name} must not be empty")
    return values


def _positive_float_or_default(raw: Any, default: float, field_name: str) -> float:
    """Parse a strictly positive finite float, using the default when omitted."""

    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(f"{field_name} must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise PlanValidationError(f"{field_name} must be a positive number")
    return value


def safe_output_name(name: str) -> str:
    """Create a conservative file-system name from a plan name."""

    allowed = [character if character.isalnum() or character in {"-", "_"} else "_" for character in name]
    compact = "".join(allowed).strip("_")
    return compact or "solidworks_model"


def path_to_string(path: Path) -> str:
    """Normalize paths in reports so MCP clients receive plain strings."""

    return str(path.expanduser().resolve())


def write_json_file(path: Path, payload: dict[str, Any]) -> str:
    """Write a JSON artifact with stable formatting and return its absolute path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path_to_string(path)
