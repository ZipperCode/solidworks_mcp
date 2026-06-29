from __future__ import annotations

import json
from pathlib import Path
import tempfile

from solidworks_mcp.adapters.mock import MockCADAdapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.drawing_review import assess_drawing_review
from solidworks_mcp.executor import ModelPlanExecutor
from solidworks_mcp.schemas import ExecutionReport, ModelPlan


ROOT = Path(__file__).resolve().parent


def test_mounting_plate_review_is_manufacturing_candidate_when_gate_evidence_is_complete() -> None:
    plan = ModelPlan.from_dict(
        json.loads((ROOT / "examples" / "mounting_plate_plan.json").read_text(encoding="utf-8"))
    )
    diagnostics = {
        "drawing_view_result": {
            "status": "created",
            "views": [
                {"role": "front"},
                {"role": "top"},
                {"role": "right"},
                {"role": "isometric"},
            ],
            "layout": {"status": "layout_verified", "projection": "third_angle"},
        },
        "drawing_annotation_result": {
            "status": "hole_callout_created",
            "direct_hole_callout_created": True,
        },
        "drawing_dimension_result": {
            "status": "basic_dimensions_created",
            "dimension_layout_status": "trusted_dimensions_created",
            "created_dimension_count": 5,
            "required_dimensions": [
                "length_120",
                "width_80",
                "thickness_10",
                "corner_radius_r5",
                "hole_edge_offset_15",
            ],
        },
        "material_result": {"status": "material_verified", "effective_material": "Plain Carbon Steel"},
        "custom_property_result": {"status": "custom_properties_verified"},
    }

    review = assess_drawing_review(plan, diagnostics)

    assert review["status"] == "manufacturing_ready_candidate"
    assert review["standard"]["standard"] == "ISO"
    assert review["intent"] == "controlled_part_drawing"
    assert review["summary"]["missing_required_items"] == []


def test_mounting_plate_review_exposes_standardized_drawing_recipe() -> None:
    plan = ModelPlan.from_dict(
        json.loads((ROOT / "examples" / "mounting_plate_plan.json").read_text(encoding="utf-8"))
    )

    review = assess_drawing_review(plan, {})

    recipe = review["recipe"]
    assert recipe["view_roles"] == ["front", "top", "right", "isometric"]
    assert recipe["title_block_fields"] == ["part_number", "revision", "material", "author"]
    assert recipe["required_notes"] == ["material"]
    assert recipe["export_formats"] == ["pdf", "dwg"]
    assert recipe["release_policy"] == "engineering_confirmation_required"


def test_imported_model_review_requires_engineering_confirmation_when_material_is_unknown() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        fixture_path = Path(temp_dir) / "fixture.SLDPRT"
        fixture_path.write_text("placeholder", encoding="utf-8")
        plan = ModelPlan.from_dict(
            {
                "name": "imported_model_draft",
                "units": "mm",
                "metadata": {},
                "output_formats": ["sldprt", "step", "stl"],
                "drawing_profile": {
                    "enabled": True,
                    "sheet_format": "A3",
                    "projection": "first_angle",
                    "view_style": "manufacturing_rotational",
                    "include_isometric": True,
                    "include_basic_dimensions": True,
                    "export_formats": ["pdf", "dwg"],
                },
                "operations": [
                    {
                        "id": "import",
                        "op": "import_existing_model",
                        "parameters": {
                            "path": str(fixture_path),
                            "copy_to_run_dir": True,
                            "document_type": "part",
                        },
                    },
                    {"id": "drawing", "op": "make_drawing", "parameters": {}},
                ],
            }
        )
    diagnostics = {
        "drawing_view_result": {
            "status": "created",
            "views": [{"role": "section"}, {"role": "end"}, {"role": "isometric"}],
            "layout": {
                "status": "layout_verified",
                "projection": "first_angle",
                "layout_style": "manufacturing_rotational",
            },
        },
        "drawing_dimension_result": {
            "status": "basic_dimensions_created",
            "dimension_layout_status": "trusted_dimensions_created",
            "created_dimension_count": 7,
        },
        "drawing_metadata_note_result": {
            "status": "manufacturing_note_created",
            "manufacturing_note": {"status": "manufacturing_note_created"},
        },
        "material_result": {"status": "not_requested"},
        "custom_property_result": {"status": "not_requested"},
    }

    review = assess_drawing_review(plan, diagnostics)

    assert review["status"] == "needs_engineering_confirmation"
    assert review["intent"] == "imported_model_manufacturing_draft"
    assert "material_or_manual_material_note" in review["summary"]["missing_required_items"]


def test_execution_report_promotes_drawing_review_to_production_verdict() -> None:
    plan = ModelPlan.from_dict(
        json.loads((ROOT / "examples" / "mounting_plate_plan.json").read_text(encoding="utf-8"))
    )
    review = assess_drawing_review(plan, {})
    report = ExecutionReport(
        ok=True,
        adapter="mock",
        message="ok",
        diagnostics={
            "production_acceptance_result": {
                "status": "accepted",
                "ok": True,
                "failures": [],
                "summary": {},
            },
            "drawing_review": review,
        },
    )

    verdict = report.to_dict()["production_verdict"]

    assert verdict["drawing_review"]["status"] == "needs_engineering_confirmation"
    assert verdict["drawing_review"]["intent"] == "controlled_part_drawing"


def test_execute_plan_writes_recipe_contract_to_drawing_manifest() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=Path(temp_dir),
            part_template=None,
            drawing_template=None,
            visible=False,
            macro_fallback_enabled=False,
            macro_execution_disabled=True,
            force_holewizard_failure=False,
            force_drawing_callout_failure=False,
            force_drawing_dimension_failure=False,
            force_cad_content_failure=False,
            force_cleanup_failure=False,
            force_material_failure=False,
            force_preflight_failure=False,
            enforce_trusted_workflow=True,
            require_direct_hole_callout=True,
            close_documents_after_run=True,
            cleanup_attach_only=True,
            debug_level="basic",
            run_id="drawing-recipe-test",
        )
        plan = json.loads((ROOT / "examples" / "mounting_plate_plan.json").read_text(encoding="utf-8"))
        report = ModelPlanExecutor(MockCADAdapter(config), config).execute_plan(plan, confirmed=True).to_dict()

        manifest_path = Path(report["output_files"]["drawing_manifest"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    recipe = manifest["recipe"]
    recipe_note = manifest["recipe_note_result"]
    assert recipe["view_roles"] == ["front", "top", "right", "isometric"]
    assert recipe["title_block_fields"] == ["part_number", "revision", "material", "author"]
    assert recipe_note["status"] == "recipe_note_created"
    assert report["diagnostics"]["drawing_recipe_result"]["status"] == "recipe_manifest_created"
