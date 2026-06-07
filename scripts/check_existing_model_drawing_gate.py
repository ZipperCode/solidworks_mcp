"""Regression checks for existing-model engineering drawing acceptance."""

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
    """Verify that existing-model drawing exports require useful annotations."""

    with tempfile.TemporaryDirectory(prefix="solidworks_mcp_existing_gate_") as tmp:
        source = Path(tmp) / "existing_model.SLDPRT"
        source.write_bytes(b"placeholder")
        plan = _existing_model_plan(source)
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
                "front": "front.png",
                "top": "top.png",
                "right": "right.png",
                "isometric": "isometric.png",
            },
        )
        _assert(verdict["ok"] is True, f"Expected existing-model verdict to pass: {verdict}")
        summary = verdict.get("summary") or {}
        _assert(
            summary.get("required_dimensions") == ["overall_outer_diameter", "overall_size_note"],
            f"Wrong existing-model dimension contract: {summary}",
        )
        _assert(
            summary.get("existing_model_note_status") == "existing_model_note_created",
            f"Missing note status in summary: {summary}",
        )
        _assert(
            _trusted_dimension_evidence_ok(diagnostics) is True,
            f"Expected trusted existing-model annotation evidence: {diagnostics['drawing_dimension_result']}",
        )

        missing_note = _accepted_diagnostics(source)
        missing_note["drawing_metadata_note_result"]["existing_model_note"] = {
            "status": "existing_model_note_failed"
        }
        missing_note["drawing_dimension_result"]["created_dimensions"] = [
            item
            for item in missing_note["drawing_dimension_result"]["created_dimensions"]
            if item["id"] != "overall_size_note"
        ]
        missing_note["drawing_dimension_result"]["created_dimension_count"] = 1
        missing_note["drawing_dimension_result"]["missing_dimensions"] = ["overall_size_note"]
        missing_note["drawing_dimension_result"]["status"] = "dimension_creation_failed"
        missing_note["drawing_dimension_result"]["dimension_layout_status"] = (
            "existing_model_overall_annotations_incomplete"
        )
        rejected = _build_production_acceptance_result(
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
                "front": "front.png",
                "top": "top.png",
                "right": "right.png",
                "isometric": "isometric.png",
            },
        )
        _assert(rejected["ok"] is False, f"Expected missing note rejection: {rejected}")
        _assert(
            "existing_model_overall_note_created" in rejected.get("failures", []),
            f"Missing note gate failure: {rejected}",
        )
        _assert(
            _trusted_dimension_evidence_ok(missing_note) is False,
            f"Expected missing note to fail trusted evidence: {missing_note['drawing_dimension_result']}",
        )

    print(
        {
            "ok": True,
            "checks": [
                "existing_model_annotations_accepted",
                "existing_model_missing_note_rejected",
            ],
        }
    )


def _existing_model_plan(source: Path) -> ModelPlan:
    return ModelPlan.from_dict(
        {
            "name": "existing_model_gate_check",
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
                "include_isometric": True,
                "include_basic_dimensions": True,
                "export_formats": ["pdf", "dwg"],
                "auto_layout": True,
            },
        }
    )


def _accepted_diagnostics(source: Path) -> dict:
    dimension_result = {
        "status": "basic_dimensions_created",
        "required_dimensions": ["overall_outer_diameter", "overall_size_note"],
        "created_dimensions": [
            {
                "id": "overall_outer_diameter",
                "method": "AddDimension2",
                "is_display_dimension": True,
                "proxy_dimension": False,
            },
            {
                "id": "overall_size_note",
                "method": "CreateText",
                "annotation_kind": "existing_model_overall_size_note",
                "is_display_dimension": False,
                "proxy_dimension": False,
            },
        ],
        "created_dimension_count": 2,
        "missing_dimensions": [],
        "display_dimension_count": 1,
        "overall_note_created": True,
        "dimension_layout_status": "existing_model_overall_annotations_created",
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
                {"role": "front"},
                {"role": "top"},
                {"role": "right"},
                {"role": "isometric"},
            ],
            "layout": {"status": "layout_verified", "clipped_view_count": 0, "scale": 1.0},
        },
        "drawing_annotation_status": "not_requested",
        "drawing_dimension_status": "basic_dimensions_created",
        "drawing_dimension_result": dimension_result,
        "drawing_metadata_note_result": {
            "status": "existing_model_note_created",
            "existing_model_note": {
                "status": "existing_model_note_created",
                "text": (
                    "Source: existing_model.SLDPRT\n"
                    "Overall size: X 50.50 mm / Y 21.50 mm / Z 50.50 mm\n"
                    "View layout: layout_verified\n"
                    "Dimensions: existing_model_overall_annotations_created"
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
