"""Restricted plan and report types for AI-assisted CAD execution.

The plan schema intentionally describes high-level modeling intent instead of
raw SolidWorks API calls.  This gives the AI a stable contract while keeping COM
quirks, unit conversion and fallback behavior inside the adapter layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Literal


SUPPORTED_UNITS = {"mm", "cm", "m", "inch", "ft"}
SUPPORTED_EXPORT_FORMATS = {"sldprt", "slddrw", "pdf", "dwg", "dxf", "step", "stl"}
SUPPORTED_THREAD_STANDARDS = {"ISO_metric_coarse"}
SUPPORTED_THREAD_SPECS = {"M3", "M4", "M5", "M6", "M8"}
SUPPORTED_SEMANTIC_SELECTORS = {"top_face", "outer_edges"}
SUPPORTED_OPERATIONS = {
    "create_mounting_plate",
    "create_sketch",
    "extrude",
    "cut",
    "hole",
    "fillet",
    "chamfer",
    "linear_pattern",
    "circular_pattern",
    "assign_material",
    "make_drawing",
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
    include_isometric: bool = True
    include_basic_dimensions: bool = True
    export_formats: tuple[str, ...] = ("pdf", "dwg")

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

        return cls(
            enabled=bool(raw.get("enabled", True)),
            template_path=raw.get("template_path"),
            sheet_format=str(raw.get("sheet_format", "A3")),
            projection=projection,
            include_isometric=bool(raw.get("include_isometric", True)),
            include_basic_dimensions=bool(raw.get("include_basic_dimensions", True)),
            export_formats=export_formats,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for MCP responses."""

        return {
            "enabled": self.enabled,
            "template_path": self.template_path,
            "sheet_format": self.sheet_format,
            "projection": self.projection,
            "include_isometric": self.include_isometric,
            "include_basic_dimensions": self.include_basic_dimensions,
            "export_formats": list(self.export_formats),
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
            "adapter": self.adapter,
            "message": self.message,
            "plan": self.plan,
            "plan_name": self.plan_name,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "events_file": self.events_file,
            "environment_file": self.environment_file,
            "artifacts_file": self.artifacts_file,
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


def _validate_required_operation_fields(op: str, parameters: dict[str, Any], index: int) -> None:
    """Validate only fields that are required to route the MVP operation safely."""

    required_by_operation = {
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
        "create_sketch": ("plane", "entities"),
        "extrude": ("sketch_id", "depth"),
        "cut": ("sketch_id", "depth"),
        "hole": ("position", "diameter", "depth"),
        "fillet": ("radius",),
        "chamfer": ("distance",),
        "linear_pattern": ("seed_id", "direction", "spacing", "count"),
        "circular_pattern": ("seed_id", "axis", "count"),
        "assign_material": ("material",),
        "make_drawing": (),
    }
    missing = [field_name for field_name in required_by_operation[op] if field_name not in parameters]
    if missing:
        raise PlanValidationError(f"operations[{index}] missing required fields for {op}: {missing}")

    if op == "create_mounting_plate":
        _validate_mounting_plate_fields(parameters, index)


def _validate_mounting_plate_fields(parameters: dict[str, Any], index: int) -> None:
    """Validate the high-level mounting plate template used for Windows smoke tests."""

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


def _is_supported_selector(selector: Any) -> bool:
    """Return whether a selector can be routed by the current semantic selector layer."""

    if selector in SUPPORTED_SEMANTIC_SELECTORS:
        return True
    if isinstance(selector, str) and (selector.startswith("feature:") or selector.startswith("sketch:")):
        return len(selector.split(":", 1)[1]) > 0
    return False


def _parse_string_list(raw: Any, field_name: str) -> tuple[str, ...]:
    """Parse a JSON array of strings without accepting ambiguous scalar values."""

    if not isinstance(raw, (list, tuple)):
        raise PlanValidationError(f"{field_name} must be an array of strings")
    values = tuple(str(value).lower() for value in raw)
    if not values:
        raise PlanValidationError(f"{field_name} must not be empty")
    return values


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
