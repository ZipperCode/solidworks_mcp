from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from solidworks_mcp.tool_input_examples import (
    DRAWING_PROFILE_EXAMPLES,
    MODEL_PLAN_EXAMPLES,
    OPERATION_EXAMPLES,
)


class DrawingProfileInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": DRAWING_PROFILE_EXAMPLES},
    )

    enabled: bool = Field(
        default=True,
        description="Whether this plan/session should create an engineering drawing after the model is built.",
    )
    template_path: str | None = Field(
        default=None,
        description=(
            "Optional absolute SolidWorks drawing template path, for example a GB A3 .drwdot file. "
            "Leave null to use the configured/default drawing template."
        ),
    )
    sheet_format: str = Field(
        default="A3",
        description="Drawing sheet size label to request from the adapter, for example A4, A3, or A2.",
    )
    projection: Literal["third_angle", "first_angle"] = Field(
        default="third_angle",
        description=(
            "Projection convention for generated drawing views: first_angle for GB/ISO style drawings, "
            "third_angle for ANSI-style drawings."
        ),
    )
    view_style: Literal["standard", "manufacturing_rotational", "assembly_general"] = Field(
        default="standard",
        description=(
            "Drawing layout strategy: standard for normal part views, manufacturing_rotational for turned/shaft-like "
            "parts, assembly_general for assembly overview drawings."
        ),
    )
    include_isometric: bool = Field(
        default=True,
        description="Whether to include a small isometric reference view in addition to orthographic views.",
    )
    include_basic_dimensions: bool = Field(
        default=True,
        description="Whether to ask the adapter to add basic manufacturing dimensions when it can do so reliably.",
    )
    export_formats: list[str] = Field(
        default_factory=lambda: ["pdf", "dwg"],
        description=(
            "Drawing output formats to export after drawing creation, for example pdf, dwg, dxf, or slddrw."
        ),
    )
    auto_layout: bool = Field(
        default=True,
        description="Whether the adapter should compute view placement automatically instead of using fixed positions.",
    )
    margin_mm: float = Field(
        default=18.0,
        gt=0,
        description="Reserved drawing sheet margin in millimeters for generated view layout.",
    )
    title_block_height_mm: float = Field(
        default=42.0,
        gt=0,
        description="Reserved title-block height in millimeters; generated views should stay above this area.",
    )


class OperationInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": OPERATION_EXAMPLES},
    )

    op: str = Field(
        description=(
            "Required operation name. Common atomic values are create_sketch, extrude, cut, hole, fillet, chamfer, "
            "linear_pattern, circular_pattern, revolve, sweep, loft, assign_material, set_custom_properties, and "
            "make_drawing. Trusted workflow values such as create_mounting_plate are also accepted when supported by "
            "validate_model_plan."
        ),
    )
    parameters: dict[str, JsonValue] = Field(
        default_factory=dict,
        description=(
            "Canonical operation parameter object. Preferred shape is {'op': 'create_sketch', 'parameters': {...}}. "
            "For cross-agent compatibility, common atomic fields may also be passed flat at the operation top level; "
            "flat fields are merged into parameters before validation."
        ),
    )
    id: str | None = Field(
        default=None,
        description=(
            "Optional feature graph id produced by this operation, for example front_profile or head_block. "
            "Later operations reference this id through fields such as sketch_id, seed_id, or target_refs."
        ),
    )
    description: str | None = Field(
        default=None,
        description="Optional human-readable note explaining the modeling intent of this operation.",
    )
    base_plane: str | None = Field(
        default=None,
        description="Flat create_plane base reference id, for example front, top, right, or another created plane id.",
    )
    distance: float | None = Field(
        default=None,
        gt=0,
        description="Flat create_plane offset distance or chamfer distance in the active model units.",
    )
    plane: str | dict[str, JsonValue] | None = Field(
        default=None,
        description=(
            "Flat create_sketch target plane reference. Use built-in ids front, top, or right, or a created plane id. "
            "Object references such as {'ref': 'front'} are accepted for compatibility."
        ),
    )
    entities: list[dict[str, JsonValue]] | None = Field(
        default=None,
        description=(
            "Flat create_sketch entity list. Each entity is an object with a type such as center_rectangle, "
            "rectangle, circle, line, arc, slot, or polyline plus its required geometry values."
        ),
    )
    dimensions: list[dict[str, JsonValue]] | None = Field(
        default=None,
        description="Flat create_sketch dimension definitions that constrain named sketch entities or points.",
    )
    constraints: list[dict[str, JsonValue]] | None = Field(
        default=None,
        description="Flat create_sketch geometric constraints such as coincident, horizontal, vertical, or tangent.",
    )
    sketch_id: str | None = Field(
        default=None,
        description="Flat id of an existing sketch consumed by extrude, cut, revolve, sweep, or loft operations.",
    )
    depth: float | None = Field(
        default=None,
        gt=0,
        description="Flat extrusion, cut, or hole depth in the active model units. Use through_all when depth is not finite.",
    )
    direction: str | None = Field(
        default=None,
        description="Flat direction reference for extrude, cut, or linear_pattern, for example +x, -x, +y, -y, +z, or -z.",
    )
    merge: bool | None = Field(
        default=None,
        description="Flat extrude merge flag; true joins the new boss to existing solid bodies when possible.",
    )
    through_all: bool | None = Field(
        default=None,
        description="Flat cut flag for a complete through-all cut instead of a blind depth.",
    )
    target_face: str | dict[str, JsonValue] | None = Field(
        default=None,
        description=(
            "Flat hole target face reference. Use a feature/face id returned by prior operations, or an object reference "
            "such as {'ref': 'head_block.top'} when the client emits structured refs."
        ),
    )
    position: list[float] | None = Field(
        default=None,
        min_length=2,
        description="Flat hole center position as [x, y] on the target face sketch plane, in active model units.",
    )
    positions: list[list[float]] | None = Field(
        default=None,
        description="Flat repeated hole center positions as [[x, y], ...] on the target face sketch plane.",
    )
    diameter: float | None = Field(default=None, gt=0, description="Flat hole diameter in the active model units.")
    target_refs: list[str] | None = Field(
        default=None,
        description="Flat fillet or chamfer target reference ids, usually edge/face ids produced by earlier operations.",
    )
    targets: list[str] | None = Field(
        default=None,
        description="Alias for flat fillet or chamfer target reference ids when a client emits targets instead of target_refs.",
    )
    radius: float | None = Field(default=None, gt=0, description="Flat fillet radius in the active model units.")
    seed_id: str | None = Field(
        default=None,
        description="Flat id of the seed feature to repeat in linear_pattern or circular_pattern operations.",
    )
    axis: str | dict[str, JsonValue] | None = Field(
        default=None,
        description="Flat axis reference for revolve or circular_pattern, for example x_axis, y_axis, z_axis, or a created axis id.",
    )
    spacing: float | None = Field(default=None, gt=0, description="Flat linear_pattern spacing between instances.")
    count: int | None = Field(default=None, gt=0, description="Flat pattern instance count, including the seed instance.")
    angle: float | None = Field(
        default=None,
        gt=0,
        le=360,
        description="Flat revolve or circular_pattern angle in degrees, from greater than 0 through 360.",
    )
    profile_sketch_id: str | None = Field(default=None, description="Flat sweep profile sketch id.")
    profile_id: str | None = Field(default=None, description="Flat sweep profile id alias for profile_sketch_id.")
    path_sketch_id: str | None = Field(default=None, description="Flat sweep path sketch id.")
    path_id: str | None = Field(default=None, description="Flat sweep path id alias for path_sketch_id.")
    profile_sketch_ids: list[str] | None = Field(
        default=None,
        description="Flat ordered loft profile sketch ids, from the start profile to the end profile.",
    )
    material: str | None = Field(
        default=None,
        description="Flat material name for assign_material, using a SolidWorks material display name or library name.",
    )
    properties: dict[str, JsonValue] | None = Field(
        default=None,
        description="Flat custom property map for set_custom_properties, such as part number, material, finish, or revision.",
    )

    def to_operation_dict(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)


class ModelPlanInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": MODEL_PLAN_EXAMPLES},
    )

    name: str = Field(
        description="Required plan name shown in reports and generated run artifacts; use a short stable CAD job name.",
        min_length=1,
    )
    units: str = Field(
        default="mm",
        description="Model units for all numeric dimensions in this plan: mm, cm, m, inch, or ft.",
    )
    operations: list[OperationInput] = Field(
        description=(
            "Ordered executable modeling operations. Each item must include op and either a canonical parameters object "
            "or supported flat fields. Call validate_model_plan before execute_model_plan."
        ),
        min_length=1,
    )
    drawing_profile: DrawingProfileInput | None = Field(
        default=None,
        description="Optional engineering drawing settings used by execute_model_plan, generate_drawing, or start_model_session.",
    )
    metadata: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Freeform JSON metadata carried into reports and run artifacts, for example user intent or source prompt.",
    )
    output_formats: list[str] = Field(
        default_factory=lambda: ["sldprt", "step", "stl"],
        description="Requested model output formats, for example sldprt, step, stl, pdf, dwg, dxf, or slddrw.",
    )

    def to_plan_dict(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)
