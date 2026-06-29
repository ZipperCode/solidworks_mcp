from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, assert_never

from solidworks_mcp.schemas import ModelPlan, existing_model_parameters_from_plan

DrawingIntent = Literal[
    "controlled_part_drawing",
    "imported_model_manufacturing_draft",
    "assembly_general_drawing",
    "explicit_simulation_drawing",
]
DrawingReviewStatus = Literal[
    "manufacturing_ready_candidate",
    "needs_engineering_confirmation",
    "draft_only",
]
DrawingReleasePolicy = Literal["manufacturing_ready_candidate", "engineering_confirmation_required", "review_only"]


@dataclass(frozen=True, slots=True)
class DrawingStandard:
    standard: Literal["GB", "ISO"]
    projection: Literal["first_angle", "third_angle"]
    sheet_format: str
    title_block_required: bool
    default_note_language: Literal["zh-CN", "en-US"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "standard": self.standard,
            "projection": self.projection,
            "sheet_format": self.sheet_format,
            "title_block_required": self.title_block_required,
            "default_note_language": self.default_note_language,
        }


@dataclass(frozen=True, slots=True)
class DrawingRecipe:
    view_roles: tuple[str, ...]
    title_block_fields: tuple[str, ...]
    required_notes: tuple[str, ...]
    export_formats: tuple[str, ...]
    release_policy: DrawingReleasePolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "view_roles": list(self.view_roles),
            "title_block_fields": list(self.title_block_fields),
            "required_notes": list(self.required_notes),
            "export_formats": list(self.export_formats),
            "release_policy": self.release_policy,
        }


def drawing_intent_for_plan(plan: ModelPlan) -> DrawingIntent:
    existing_model = existing_model_parameters_from_plan(plan)
    if existing_model is not None:
        return "assembly_general_drawing" if existing_model.get("document_type") == "assembly" else "imported_model_manufacturing_draft"
    operation_names = {operation.op for operation in plan.operations}
    if "run_static_simulation" in operation_names:
        return "explicit_simulation_drawing"
    if "create_bom_assembly" in operation_names:
        return "assembly_general_drawing"
    return "controlled_part_drawing"


def drawing_standard_for_plan(plan: ModelPlan) -> DrawingStandard:
    profile = plan.drawing_profile
    standard: Literal["GB", "ISO"] = "GB" if profile.projection == "first_angle" else "ISO"
    language: Literal["zh-CN", "en-US"] = "zh-CN" if standard == "GB" else "en-US"
    return DrawingStandard(
        standard=standard,
        projection=profile.projection,
        sheet_format=profile.sheet_format,
        title_block_required=True,
        default_note_language=language,
    )


def drawing_recipe_for_plan(
    plan: ModelPlan,
    intent: DrawingIntent | None = None,
    status: DrawingReviewStatus = "needs_engineering_confirmation",
) -> DrawingRecipe:
    resolved_intent = intent or drawing_intent_for_plan(plan)
    export_formats = plan.drawing_profile.export_formats
    release_policy = _release_policy(status)
    match resolved_intent:
        case "controlled_part_drawing":
            return DrawingRecipe(
                view_roles=("front", "top", "right", "isometric"),
                title_block_fields=("part_number", "revision", "material", "author"),
                required_notes=("material",),
                export_formats=export_formats,
                release_policy=release_policy,
            )
        case "imported_model_manufacturing_draft":
            return DrawingRecipe(
                view_roles=("section", "end", "isometric"),
                title_block_fields=("source_model", "revision", "material", "author"),
                required_notes=("manufacturing_uncertainty", "material"),
                export_formats=export_formats,
                release_policy=release_policy,
            )
        case "assembly_general_drawing":
            return DrawingRecipe(
                view_roles=("front", "top", "right", "isometric", "assembly"),
                title_block_fields=("assembly_number", "revision", "author"),
                required_notes=("bom",),
                export_formats=export_formats,
                release_policy=release_policy,
            )
        case "explicit_simulation_drawing":
            return DrawingRecipe(
                view_roles=("front", "top", "right", "isometric"),
                title_block_fields=("part_number", "revision", "material", "author"),
                required_notes=("material", "simulation_report"),
                export_formats=export_formats,
                release_policy=release_policy,
            )
        case unreachable:
            assert_never(unreachable)


def drawing_recipe_contract(plan: ModelPlan) -> dict[str, Any]:
    intent = drawing_intent_for_plan(plan)
    standard = drawing_standard_for_plan(plan)
    recipe = drawing_recipe_for_plan(plan, intent)
    return {
        "intent": intent,
        "standard": standard.to_dict(),
        "recipe": recipe.to_dict(),
        "note_text": drawing_recipe_note_text(intent, standard, recipe),
    }


def drawing_recipe_note_text(intent: DrawingIntent, standard: DrawingStandard, recipe: DrawingRecipe) -> str:
    lines = (
        "ENGINEERING DRAWING RECIPE",
        f"Intent: {intent}",
        f"Standard: {standard.standard}; projection: {standard.projection}; sheet: {standard.sheet_format}",
        f"Required views: {', '.join(recipe.view_roles)}",
        f"Title block fields: {', '.join(recipe.title_block_fields)}",
        f"Required notes: {', '.join(recipe.required_notes)}",
        f"Release policy: {recipe.release_policy}",
    )
    return "\n".join(lines)


def _release_policy(status: DrawingReviewStatus) -> DrawingReleasePolicy:
    match status:
        case "manufacturing_ready_candidate":
            return "manufacturing_ready_candidate"
        case "needs_engineering_confirmation":
            return "engineering_confirmation_required"
        case "draft_only":
            return "review_only"
        case unreachable:
            assert_never(unreachable)
