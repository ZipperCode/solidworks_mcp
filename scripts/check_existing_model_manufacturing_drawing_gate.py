"""Regression checks for imported rotational manufacturing drawing acceptance."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solidworks_mcp.executor import _build_production_acceptance_result
from solidworks_mcp.run_diagnostics import _trusted_dimension_evidence_ok
from solidworks_mcp.schemas import ModelPlan


def main() -> None:
    """Verify that imported rotational drawings are gated as manufacturing drafts."""

    with tempfile.TemporaryDirectory(prefix="solidworks_mcp_mfg_drawing_gate_") as tmp:
        source = Path(tmp) / "quick_coupler.SLDPRT"
        source.write_bytes(b"placeholder")
        plan = _manufacturing_plan(source)
        diagnostics = _accepted_diagnostics(source)

        verdict = _build_production_acceptance_result(
            plan,
            True,
            diagnostics,
            {
                "sldprt": "model.SLDPRT",
                "slddrw": "drawing.SLDDRW",
                "pdf": "drawing.pdf",
                "dwg": "drawing.dwg",
            },
            {
                "section": "section.png",
                "end": "end.png",
                "isometric": "isometric.png",
            },
        )
        _assert(verdict["ok"] is True, f"Expected manufacturing draft verdict to pass: {verdict}")
        summary = verdict.get("summary") or {}
        _assert(
            summary.get("manufacturing_draft_status") == "existing_model_manufacturing_draft_created",
            f"Missing manufacturing draft status: {summary}",
        )
        _assert(
            summary.get("section_view_status") == "section_view_created",
            f"Missing real section-view status: {summary}",
        )
        _assert(
            summary.get("dimension_layout_status") == "existing_model_manufacturing_dimensions_created",
            f"Wrong manufacturing dimension status: {summary}",
        )
        _assert(_trusted_dimension_evidence_ok(diagnostics) is True, "Expected trusted manufacturing dimensions")

        missing_section = _accepted_diagnostics(source)
        missing_section["drawing_view_result"]["views"] = [
            view for view in missing_section["drawing_view_result"]["views"] if view["role"] != "section"
        ]
        missing_section["drawing_view_result"]["manufacturing_draft"]["section_view"] = {
            "status": "section_view_failed"
        }
        rejected = _build_production_acceptance_result(
            plan,
            True,
            missing_section,
            {
                "sldprt": "model.SLDPRT",
                "slddrw": "drawing.SLDDRW",
                "pdf": "drawing.pdf",
                "dwg": "drawing.dwg",
            },
            {
                "end": "end.png",
                "isometric": "isometric.png",
            },
        )
        _assert(rejected["ok"] is False, f"Expected missing section-view rejection: {rejected}")
        _assert("section_view_created" in rejected.get("failures", []), f"Missing section gate failure: {rejected}")

        missing_note = _accepted_diagnostics(source)
        missing_note["drawing_metadata_note_result"]["manufacturing_note"] = {
            "status": "manufacturing_note_failed",
            "text": "Source: quick_coupler.SLDPRT",
        }
        rejected_note = _build_production_acceptance_result(
            plan,
            True,
            missing_note,
            {
                "sldprt": "model.SLDPRT",
                "slddrw": "drawing.SLDDRW",
                "pdf": "drawing.pdf",
                "dwg": "drawing.dwg",
            },
            {
                "section": "section.png",
                "end": "end.png",
                "isometric": "isometric.png",
            },
        )
        _assert(rejected_note["ok"] is False, f"Expected missing manufacturing note rejection: {rejected_note}")
        _assert(
            "imported_model_uncertainty_note_created" in rejected_note.get("failures", []),
            f"Missing uncertainty-note gate failure: {rejected_note}",
        )

    print(
        {
            "ok": True,
            "checks": [
                "manufacturing_draft_accepted",
                "missing_section_view_rejected",
                "missing_uncertainty_note_rejected",
            ],
        }
    )


def _manufacturing_plan(source: Path) -> ModelPlan:
    return ModelPlan.from_dict(
        {
            "name": "quick_coupler_manufacturing_draft",
            "units": "mm",
            "output_formats": ["sldprt", "pdf", "dwg"],
            "operations": [
                {
                    "op": "import_existing_model",
                    "parameters": {"path": os.fspath(source), "copy_to_run_dir": True},
                },
                {"op": "make_drawing", "parameters": {}},
            ],
            "drawing_profile": {
                "enabled": True,
                "sheet_format": "A3",
                "projection": "first_angle",
                "view_style": "manufacturing_rotational",
                "include_isometric": True,
                "include_basic_dimensions": True,
                "export_formats": ["pdf", "dwg"],
            },
        }
    )


def _accepted_diagnostics(source: Path) -> dict:
    dimension_result = {
        "status": "basic_dimensions_created",
        "required_dimensions": ["overall_outer_diameter", "inner_diameter", "overall_length"],
        "created_dimensions": [
            {
                "id": "overall_outer_diameter",
                "method": "AddDiameterDimension2",
                "is_display_dimension": True,
                "classification": "geometry_verified_dimension",
                "proxy_dimension": False,
            },
            {
                "id": "inner_diameter",
                "method": "AddDiameterDimension2",
                "is_display_dimension": True,
                "classification": "geometry_verified_dimension",
                "proxy_dimension": False,
            },
            {
                "id": "overall_length",
                "method": "AddVerticalDimension2",
                "is_display_dimension": True,
                "classification": "geometry_verified_dimension",
                "proxy_dimension": False,
            },
        ],
        "created_dimension_count": 3,
        "missing_dimensions": [],
        "display_dimension_count": 3,
        "dimension_layout_status": "existing_model_manufacturing_dimensions_created",
        "geometry_verified_dimension_count": 3,
    }
    return {
        "preflight_status": "ready",
        "existing_model_result": {
            "status": "existing_model_imported",
            "copied_to_run_dir": True,
            "source_path": os.fspath(source),
            "run_model_path": os.fspath(source),
            "document_type": "part",
        },
        "drawing_view_status": "created",
        "drawing_view_result": {
            "status": "created",
            "views": [
                {"role": "section"},
                {"role": "end"},
                {"role": "isometric"},
            ],
            "layout": {
                "status": "layout_verified",
                "layout_style": "manufacturing_rotational",
                "projection": "first_angle",
                "clipped_view_count": 0,
                "scale": 1.0,
            },
            "manufacturing_draft": {
                "status": "existing_model_manufacturing_draft_created",
                "classification": "imported_rotational_machining_draft",
                "rotational_axis": {"status": "axis_verified", "confidence": 0.92, "axis": "z"},
                "section_view": {
                    "status": "section_view_created",
                    "method": "CreateSectionViewAt5",
                    "section_object_verified": True,
                    "hatching_verified": True,
                },
                "centerline": {"status": "centerline_created", "centerline_count": 1},
                "center_mark": {"status": "center_mark_created", "center_mark_count": 2},
            },
        },
        "drawing_annotation_status": "not_requested",
        "drawing_dimension_status": "basic_dimensions_created",
        "drawing_dimension_result": dimension_result,
        "drawing_metadata_note_result": {
            "status": "manufacturing_note_created",
            "manufacturing_note": {
                "status": "manufacturing_note_created",
                "text": (
                    "本图基于导入三维模型自动生成。\n"
                    "未注明尺寸由导入三维模型几何读取，仅供审图/加工前确认。\n"
                    "未注公差、材料、表面处理按人工补充文件或订单要求执行。\n"
                    "关键尺寸/公差需人工确认后方可生产放行。"
                ),
            },
        },
        "model_geometry_status": "geometry_verified",
        "model_geometry_result": {"status": "geometry_verified", "body_count": 1},
        "mass_property_status": "mass_properties_verified",
        "mass_property_result": {"status": "mass_properties_verified", "mass_kg": 0.01, "volume_m3": 1e-6},
        "material_status": "not_requested",
        "material_result": {},
        "custom_property_status": "not_requested",
        "custom_property_result": {},
        "artifact_validation_result": {"ok": True, "status": "artifacts_ready"},
        "artifact_content_result": {
            "ok": True,
            "status": "content_ready",
            "cad_content_result": {"ok": True, "status": "cad_artifacts_verified"},
            "pdf_semantic_content_result": {"ok": True, "status": "pdf_semantic_content_verified"},
        },
        "export_result": {"status": "exported", "failed": []},
        "cleanup_result": {
            "enabled": True,
            "status": "completed",
            "cleanup_verification_status": "verified",
        },
        "document_state_audit_result": {
            "status": "verified_no_run_documents_open",
            "after_cleanup_run_created_open_count": 0,
        },
    }


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    main()
