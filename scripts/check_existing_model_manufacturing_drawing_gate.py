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
from solidworks_mcp.schemas import DrawingProfile, ModelPlan, existing_model_parameters_from_plan
from solidworks_mcp.adapters import solidworks as solidworks_adapter
from solidworks_mcp.adapters.solidworks import (
    SolidWorksCOMAdapter,
    _estimated_view_outline,
    _outline_inside_safe_rect,
)


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
        _assert_large_frame_layout_not_clipped(plan)
        _assert_prismatic_imported_frame_accepted(source, plan)
        _assert_imported_assembly_acceptance(source)

    print(
        {
            "ok": True,
            "checks": [
                "manufacturing_draft_accepted",
                "missing_section_view_rejected",
                "missing_uncertainty_note_rejected",
                "large_frame_layout_scaled_to_fit",
                "prismatic_imported_frame_accepted",
                "imported_assembly_reference_paths_and_gate",
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


def _assert_large_frame_layout_not_clipped(plan: ModelPlan) -> None:
    adapter = object.__new__(SolidWorksCOMAdapter)
    adapter._drawing = None
    adapter._require_model = lambda: object()
    old_reader = solidworks_adapter._read_model_bounding_box
    solidworks_adapter._read_model_bounding_box = lambda model: {
        "status": "read",
        "body_count": 1,
        "bbox_m": [0.0, 0.0, 0.0, 2.36, 1.43, 1.06],
    }
    try:
        layout = SolidWorksCOMAdapter._build_existing_model_manufacturing_layout(
            adapter,
            plan,
            DrawingProfile.from_dict(plan.drawing_profile.to_dict()),
        )
    finally:
        solidworks_adapter._read_model_bounding_box = old_reader

    safe_rect = layout["safe_rect_m"]
    checks = []
    for role in ("section", "end", "isometric"):
        slot = layout["slots"][role]
        scale = float(layout["isometric_scale"] if role == "isometric" else layout["scale"])
        outline = _estimated_view_outline(slot, scale)
        checks.append(
            {
                "role": role,
                "scale": scale,
                "outline": outline,
                "inside_safe_rect": _outline_inside_safe_rect(outline, safe_rect),
            }
        )
    _assert(
        all(check["inside_safe_rect"] for check in checks),
        f"Expected large imported frame layout to stay inside A3 safe rect: {checks}; layout={layout}",
    )


def _assert_prismatic_imported_frame_accepted(source: Path, plan: ModelPlan) -> None:
    diagnostics = _accepted_diagnostics(source)
    diagnostics["drawing_view_result"]["layout"]["model_dimensions_m"] = {"x": 2.36, "y": 1.43, "z": 1.06}
    diagnostics["drawing_view_result"]["manufacturing_draft"] = {
        **diagnostics["drawing_view_result"]["manufacturing_draft"],
        "classification": "imported_prismatic_machining_draft",
        "rotational_axis": {"status": "not_required", "reason": "non_rotational_imported_model"},
        "centerline": {"status": "not_required", "reason": "non_rotational_imported_model"},
        "center_mark": {"status": "not_required", "reason": "non_rotational_imported_model"},
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "required_dimensions": ["overall_length"],
        "created_dimensions": [
            {
                "id": "overall_length",
                "method": "AddVerticalDimension2",
                "is_display_dimension": True,
                "classification": "geometry_verified_dimension",
                "proxy_dimension": False,
            }
        ],
        "created_dimension_count": 1,
        "missing_dimensions": [],
        "display_dimension_count": 1,
        "dimension_layout_status": "existing_model_manufacturing_dimensions_created",
        "geometry_verified_dimension_count": 1,
    }
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
    _assert(verdict["ok"] is True, f"Expected prismatic imported frame verdict to pass: {verdict}")
    summary = verdict.get("summary") or {}
    _assert(
        summary.get("required_dimensions") == ["overall_length"],
        f"Expected prismatic frame to require only overall_length evidence: {summary}",
    )


def _assert_imported_assembly_acceptance(source: Path) -> None:
    reference_dir = source.parent / "assembly_refs"
    reference_dir.mkdir()
    (reference_dir / "sample_component.SLDPRT").write_bytes(b"placeholder")
    assembly = source.parent / "resolved_assembly.SLDASM"
    assembly.write_bytes(b"placeholder")
    plan = ModelPlan.from_dict(
        {
            "name": "resolved_assembly_existing_model_drawing",
            "units": "mm",
            "output_formats": ["sldasm", "pdf", "dwg"],
            "operations": [
                {
                    "op": "import_existing_model",
                    "parameters": {
                        "path": os.fspath(assembly),
                        "copy_to_run_dir": True,
                        "document_type": "assembly",
                        "reference_search_paths": [os.fspath(reference_dir)],
                    },
                },
                {"op": "make_drawing", "parameters": {}},
            ],
            "drawing_profile": {
                "enabled": True,
                "sheet_format": "A3",
                "projection": "first_angle",
                "view_style": "assembly_general",
                "include_isometric": True,
                "include_basic_dimensions": True,
                "export_formats": ["pdf", "dwg"],
            },
        }
    )
    params = existing_model_parameters_from_plan(plan)
    _assert(params is not None, "Expected imported assembly parameters")
    _assert(
        params.get("reference_search_paths") == [os.fspath(reference_dir)],
        f"Expected reference search paths to be preserved: {params}",
    )
    diagnostics = _accepted_assembly_diagnostics(assembly)
    verdict = _build_production_acceptance_result(
        plan,
        True,
        diagnostics,
        {
            "sldasm": "model.SLDASM",
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
    _assert(verdict["ok"] is True, f"Expected resolved imported assembly verdict to pass: {verdict}")
    summary = verdict.get("summary") or {}
    _assert(
        summary.get("required_dimensions") == ["overall_height", "overall_length", "overall_width"],
        f"Expected imported assembly to require overall L/W/H evidence: {summary}",
    )

    unresolved = _accepted_assembly_diagnostics(assembly)
    unresolved["existing_model_result"]["assembly_resolution"] = {
        **unresolved["existing_model_result"]["assembly_resolution"],
        "active_component_count": 0,
        "missing_path_count": 3,
    }
    rejected = _build_production_acceptance_result(
        plan,
        True,
        unresolved,
        {
            "sldasm": "model.SLDASM",
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
    _assert(rejected["ok"] is False, f"Expected unresolved imported assembly rejection: {rejected}")
    _assert(
        "assembly_components_resolved" in rejected.get("failures", []),
        f"Expected assembly component gate failure: {rejected}",
    )


def _accepted_assembly_diagnostics(source: Path) -> dict:
    diagnostics = _accepted_diagnostics(source)
    diagnostics["existing_model_result"] = {
        "status": "existing_model_imported",
        "copied_to_run_dir": True,
        "source_path": os.fspath(source),
        "run_model_path": os.fspath(source),
        "document_type": "assembly",
        "reference_copy_result": {
            "status": "references_copied",
            "copied_count": 9,
            "search_paths": [os.fspath(source.parent / "assembly_refs")],
        },
        "assembly_resolution": {
            "status": "assembly_components_resolved",
            "component_count": 12,
            "active_component_count": 12,
            "suppressed_component_count": 0,
            "missing_path_count": 0,
        },
    }
    diagnostics["drawing_view_result"] = {
        "status": "created",
        "views": [
            {"role": "front"},
            {"role": "top"},
            {"role": "right"},
            {"role": "isometric"},
        ],
        "layout": {
            "status": "layout_verified",
            "layout_style": "existing_model_assembly",
            "projection": "first_angle",
            "clipped_view_count": 0,
            "scale": 0.16,
        },
        "assembly_draft": {
            "status": "existing_model_assembly_draft_created",
            "classification": "imported_assembly_draft",
        },
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "required_dimensions": ["overall_length", "overall_width", "overall_height"],
        "created_dimensions": [
            {
                "id": "overall_length",
                "method": "AddHorizontalDimension2",
                "is_display_dimension": True,
                "classification": "geometry_verified_dimension",
                "proxy_dimension": False,
            },
            {
                "id": "overall_width",
                "method": "model_bbox_readback_note",
                "is_display_dimension": False,
                "classification": "geometry_readback_note",
                "annotation_kind": "existing_model_assembly_overall_size_note",
                "proxy_dimension": False,
            },
            {
                "id": "overall_height",
                "method": "AddVerticalDimension2",
                "is_display_dimension": True,
                "classification": "geometry_verified_dimension",
                "proxy_dimension": False,
            },
        ],
        "created_dimension_count": 3,
        "missing_dimensions": [],
        "display_dimension_count": 2,
        "dimension_layout_status": "existing_model_assembly_dimensions_created",
        "geometry_verified_dimension_count": 3,
    }
    diagnostics["drawing_metadata_note_result"] = {
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
    }
    diagnostics["model_geometry_result"] = {"status": "geometry_verified", "body_count": 12}
    diagnostics["mass_property_status"] = "mass_property_failed"
    diagnostics["mass_property_result"] = {
        "status": "mass_property_failed",
        "failure_reason": "Imported assembly has unresolved material mass readback.",
    }
    return diagnostics


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    main()
