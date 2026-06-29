from __future__ import annotations

from typing import Any, assert_never

from solidworks_mcp.drawing_recipe import (
    DrawingIntent,
    DrawingReviewStatus,
    drawing_intent_for_plan,
    drawing_recipe_for_plan,
    drawing_standard_for_plan,
)
from solidworks_mcp.schemas import ModelPlan


def assess_drawing_review(plan: ModelPlan, diagnostics: dict[str, Any]) -> dict[str, Any]:
    intent = drawing_intent_for_plan(plan)
    standard = drawing_standard_for_plan(plan)
    required_items = _required_items(intent)
    present_items = _present_items(plan, diagnostics)
    missing_items = [item for item in required_items if item not in present_items]
    status = _review_status(intent, missing_items)
    recipe = drawing_recipe_for_plan(plan, intent, status)
    return {
        "status": status,
        "intent": intent,
        "standard": standard.to_dict(),
        "recipe": recipe.to_dict(),
        "required_items": required_items,
        "present_items": sorted(present_items),
        "summary": {
            "missing_required_items": missing_items,
            "requires_human_release": status != "manufacturing_ready_candidate",
            "message": _review_message(status),
        },
    }


def _required_items(intent: DrawingIntent) -> list[str]:
    match intent:
        case "controlled_part_drawing":
            return [
                "standard_views",
                "trusted_dimensions",
                "title_block_metadata",
                "material_or_manual_material_note",
            ]
        case "imported_model_manufacturing_draft":
            return [
                "manufacturing_views",
                "trusted_dimensions",
                "manufacturing_uncertainty_note",
                "material_or_manual_material_note",
            ]
        case "assembly_general_drawing":
            return [
                "assembly_views",
                "bom_or_structure_note",
                "title_block_metadata",
            ]
        case "explicit_simulation_drawing":
            return [
                "standard_views",
                "trusted_dimensions",
                "simulation_report_note",
                "material_or_manual_material_note",
            ]
        case unreachable:
            assert_never(unreachable)


def _present_items(plan: ModelPlan, diagnostics: dict[str, Any]) -> set[str]:
    present: set[str] = set()
    _add_view_evidence(present, diagnostics)
    _add_dimension_evidence(present, diagnostics)
    _add_note_evidence(present, diagnostics)
    _add_metadata_evidence(present, plan, diagnostics)
    _add_material_evidence(present, diagnostics)
    return present


def _add_view_evidence(present: set[str], diagnostics: dict[str, Any]) -> None:
    view_result = _as_dict(diagnostics.get("drawing_view_result"))
    view_roles = {str(view.get("role")) for view in _as_dict_list(view_result.get("views"))}
    if {"front", "top", "right", "isometric"}.issubset(view_roles):
        present.add("standard_views")
    if {"section", "end", "isometric"}.issubset(view_roles):
        present.add("manufacturing_views")
    if "assembly" in view_roles or view_result.get("layout", {}).get("layout_style") == "existing_model_assembly":
        present.add("assembly_views")


def _add_dimension_evidence(present: set[str], diagnostics: dict[str, Any]) -> None:
    dimension_result = _as_dict(diagnostics.get("drawing_dimension_result"))
    if dimension_result.get("dimension_layout_status") == "trusted_dimensions_created":
        present.add("trusted_dimensions")
    if dimension_result.get("dimension_layout_status") == "existing_model_assembly_dimensions_created":
        present.add("trusted_dimensions")


def _add_note_evidence(present: set[str], diagnostics: dict[str, Any]) -> None:
    metadata_note = _as_dict(diagnostics.get("drawing_metadata_note_result"))
    manufacturing_note = _as_dict(metadata_note.get("manufacturing_note"))
    if metadata_note.get("status") == "manufacturing_note_created" or manufacturing_note.get("status") == "manufacturing_note_created":
        present.add("manufacturing_uncertainty_note")
    if _as_dict(diagnostics.get("bom_result")).get("status") == "bom_verified":
        present.add("bom_or_structure_note")
    if _as_dict(diagnostics.get("simulation_result")).get("status") == "simulation_verified":
        present.add("simulation_report_note")


def _add_metadata_evidence(present: set[str], plan: ModelPlan, diagnostics: dict[str, Any]) -> None:
    custom_property = _as_dict(diagnostics.get("custom_property_result"))
    has_metadata = any(key in plan.metadata for key in ("part_number", "revision", "author"))
    if custom_property.get("status") == "custom_properties_verified" or has_metadata:
        present.add("title_block_metadata")


def _add_material_evidence(present: set[str], diagnostics: dict[str, Any]) -> None:
    material = _as_dict(diagnostics.get("material_result"))
    if material.get("status") == "material_verified" or material.get("effective_material"):
        present.add("material_or_manual_material_note")


def _review_status(intent: DrawingIntent, missing_items: list[str]) -> DrawingReviewStatus:
    if not missing_items:
        return "manufacturing_ready_candidate"
    match intent:
        case "controlled_part_drawing" | "imported_model_manufacturing_draft":
            return "needs_engineering_confirmation"
        case "assembly_general_drawing" | "explicit_simulation_drawing":
            return "draft_only"
        case unreachable:
            assert_never(unreachable)


def _review_message(status: DrawingReviewStatus) -> str:
    match status:
        case "manufacturing_ready_candidate":
            return "Drawing evidence is complete enough for engineering handoff candidate status."
        case "needs_engineering_confirmation":
            return "Drawing is a draft until missing engineering details are confirmed."
        case "draft_only":
            return "Drawing is diagnostic or review-only and must not be used as production release evidence."
        case unreachable:
            assert_never(unreachable)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
