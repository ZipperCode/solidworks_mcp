"""Regression checks for the controlled mounting-plate plan schema."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solidworks_mcp.schemas import (
    bracket_basic_dimension_ids_from_plan,
    center_hole_flange_basic_dimension_ids_from_plan,
    center_hole_plate_basic_dimension_ids_from_plan,
    DrawingProfile,
    end_cap_basic_dimension_ids_from_plan,
    ExecutionReport,
    ModelPlan,
    PlanValidationError,
    mounting_block_basic_dimension_ids_from_plan,
    mounting_plate_basic_dimension_ids_from_plan,
    shaft_basic_dimension_ids_from_plan,
    sheet_metal_base_flange_basic_dimension_ids_from_plan,
    sleeve_basic_dimension_ids_from_plan,
    slotted_array_plate_basic_dimension_ids_from_plan,
    static_simulation_basic_dimension_ids_from_plan,
    washer_basic_dimension_ids_from_plan,
    weldment_frame_basic_dimension_ids_from_plan,
)
from solidworks_mcp.adapters import create_adapter
from solidworks_mcp.adapters.solidworks import SolidWorksCOMAdapter, _solidworks_suffix
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.executor import (
    ModelPlanExecutor,
    _build_production_acceptance_result,
    _validate_cad_artifact_content,
)
from solidworks_mcp.release_diagnostics import diagnose_release_gate_report
from solidworks_mcp.run_diagnostics import diagnose_run_collection, diagnose_run_directory

from scripts.release_production_gate import (
    DEFAULT_SCENARIOS,
    _compact_release_payload,
    _emergency_cleanup_completed_runs,
    _release_gate_exception_payload,
    _release_gate_payload,
)
from scripts.smoke_mounting_plate import (
    _compact_result_item,
    _compact_smoke_exception_payload,
    _emergency_cleanup_recent_runs,
    _production_scenarios,
    _recent_completed_run_dirs,
    _raise_forced_smoke_exception_after_run,
    _smoke_exception_payload,
    _write_smoke_exception_report,
)


THREAD_SPECS = ("M3", "M4", "M5", "M6", "M8")


def main() -> int:
    """Check valid thread variants and representative invalid geometry."""

    base_plan = json.loads((ROOT / "examples" / "mounting_plate_plan.json").read_text(encoding="utf-8"))
    valid = []
    for thread_spec in THREAD_SPECS:
        plan = _with_thread_spec(base_plan, thread_spec)
        ModelPlan.from_dict(plan)
        valid.append(thread_spec)

    default_plan = ModelPlan.from_dict(base_plan)
    default_dimensions = mounting_plate_basic_dimension_ids_from_plan(default_plan)
    expected_default_dimensions = ["length_120", "width_80", "thickness_10", "corner_radius_r5", "hole_edge_offset_15"]
    if default_dimensions != expected_default_dimensions:
        raise SystemExit(f"Unexpected default dimension ids: {default_dimensions}")

    wide_plan_raw = copy.deepcopy(base_plan)
    wide_params = wide_plan_raw["operations"][0]["parameters"]
    wide_params.update({"length": 140, "width": 90, "thickness": 12, "corner_radius": 6, "edge_offset": 18})
    wide_plan = ModelPlan.from_dict(wide_plan_raw)
    wide_dimensions = mounting_plate_basic_dimension_ids_from_plan(wide_plan)
    expected_wide_dimensions = ["length_140", "width_90", "thickness_12", "corner_radius_r6", "hole_edge_offset_18"]
    if wide_dimensions != expected_wide_dimensions:
        raise SystemExit(f"Unexpected wide dimension ids: {wide_dimensions}")
    validation_readiness = _check_validation_production_readiness(base_plan)
    flange_diagnostics = _check_center_hole_flange_candidate()

    acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(wide_dimensions, "trusted_dimensions_created"),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    summary = acceptance.get("summary", {})
    if acceptance.get("status") != "accepted":
        raise SystemExit(f"Expected production acceptance fixture to pass: {acceptance}")
    if acceptance.get("repair_actions"):
        raise SystemExit(f"Accepted production fixture should not expose repair actions: {acceptance}")
    if summary.get("dimension_layout_status") != "trusted_dimensions_created":
        raise SystemExit(f"Missing dimension_layout_status in acceptance summary: {summary}")
    if summary.get("proxy_dimensions") != []:
        raise SystemExit(f"Trusted fixture should not include proxy dimensions: {summary}")

    export_format_diagnostics = _check_optional_export_formats(base_plan)
    if _solidworks_suffix("iges") != "igs":
        raise SystemExit(f"Expected SolidWorks IGES suffix to be .igs: {_solidworks_suffix('iges')}")
    production_scenario_diagnostics = _check_production_scenarios(base_plan)
    release_gate_diagnostics = _check_release_gate_contract()
    smoke_exception_diagnostics = _check_smoke_exception_contract()

    untrusted_extra_op_plan_raw = copy.deepcopy(wide_plan_raw)
    untrusted_extra_op_plan_raw["operations"].insert(
        1,
        {
            "id": "extra_freeform_fillet",
            "op": "fillet",
            "description": "Extra freeform operation that is schema-valid but outside the trusted production workflow.",
            "parameters": {"radius": 2},
        },
    )
    untrusted_extra_op_plan = ModelPlan.from_dict(untrusted_extra_op_plan_raw)
    untrusted_workflow_acceptance = _build_production_acceptance_result(
        untrusted_extra_op_plan,
        True,
        _accepted_diagnostics(wide_dimensions, "trusted_dimensions_created"),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if untrusted_workflow_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected extra freeform operation fixture to be rejected: {untrusted_workflow_acceptance}")
    if "trusted_controlled_workflow" not in untrusted_workflow_acceptance.get("failures", []):
        raise SystemExit(
            "Extra freeform operation fixture did not report trusted_controlled_workflow failure: "
            f"{untrusted_workflow_acceptance}"
        )
    if not _has_repair_action(untrusted_workflow_acceptance, "trusted_controlled_workflow"):
        raise SystemExit(f"Missing trusted workflow repair action: {untrusted_workflow_acceptance}")
    compact_untrusted = _compact_result_item({"acceptance": untrusted_workflow_acceptance})
    if not _has_repair_action(compact_untrusted, "trusted_controlled_workflow"):
        raise SystemExit(f"Compact smoke summary did not expose repair action: {compact_untrusted}")
    if untrusted_workflow_acceptance.get("summary", {}).get("trusted_workflow_status") != "unsupported_workflow":
        raise SystemExit(f"Expected unsupported_workflow summary: {untrusted_workflow_acceptance}")

    report_payload = ExecutionReport(
        ok=True,
        adapter="mock",
        message="fixture",
        diagnostics={"production_acceptance_result": acceptance},
    ).to_dict()
    if report_payload.get("production_verdict", {}).get("status") != "accepted":
        raise SystemExit(f"ExecutionReport did not expose accepted production_verdict: {report_payload}")
    empty_verdict = ExecutionReport(ok=True, adapter="mock", message="fixture").to_dict().get("production_verdict", {})
    if empty_verdict.get("status") != "not_evaluated":
        raise SystemExit(f"ExecutionReport missing not_evaluated production_verdict: {empty_verdict}")

    proxy_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(wide_dimensions, "radius_proxy_used", proxy_dimension_id="corner_radius_r6"),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if proxy_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected radius proxy fixture to be rejected: {proxy_acceptance}")
    if "trusted_basic_dimensions" not in proxy_acceptance.get("failures", []):
        raise SystemExit(f"Proxy fixture did not report trusted_basic_dimensions failure: {proxy_acceptance}")
    if not _has_repair_action(proxy_acceptance, "trusted_basic_dimensions"):
        raise SystemExit(f"Missing trusted dimensions repair action: {proxy_acceptance}")

    untrusted_layout_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(wide_dimensions, "not_created"),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if untrusted_layout_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected untrusted layout fixture to be rejected: {untrusted_layout_acceptance}")
    if "trusted_basic_dimensions" not in untrusted_layout_acceptance.get("failures", []):
        raise SystemExit(
            f"Untrusted layout fixture did not report trusted_basic_dimensions failure: {untrusted_layout_acceptance}"
        )

    non_radial_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(wide_dimensions, "trusted_dimensions_created", radius_method="AddHorizontalDimension2"),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if non_radial_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected non-radial radius fixture to be rejected: {non_radial_acceptance}")
    if "trusted_basic_dimensions" not in non_radial_acceptance.get("failures", []):
        raise SystemExit(f"Non-radial fixture did not report trusted_basic_dimensions failure: {non_radial_acceptance}")

    missing_view_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(
            wide_dimensions,
            "trusted_dimensions_created",
            drawing_view_status="partial:2/4",
            drawing_view_roles=["front", "right"],
            drawing_view_errors=["*Top:no_view", "*Isometric:no_view"],
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if missing_view_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected missing-view fixture to be rejected: {missing_view_acceptance}")
    if "drawing_standard_views_created" not in missing_view_acceptance.get("failures", []):
        raise SystemExit(f"Missing-view fixture did not report drawing view failure: {missing_view_acceptance}")
    if not _has_repair_action(missing_view_acceptance, "drawing_standard_views_created"):
        raise SystemExit(f"Missing drawing-view repair action: {missing_view_acceptance}")

    strict_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(
            wide_dimensions,
            "trusted_dimensions_created",
            require_direct_hole_callout=True,
            direct_hole_callout_created=False,
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if strict_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected strict direct-hole-callout fixture to reject fallback callouts: {strict_acceptance}")
    if "direct_hole_callouts_created" not in strict_acceptance.get("failures", []):
        raise SystemExit(f"Strict fixture did not report direct_hole_callouts_created failure: {strict_acceptance}")

    material_plan_raw = copy.deepcopy(wide_plan_raw)
    material_plan_raw["operations"].insert(
        1,
        {
            "id": "material",
            "op": "assign_material",
            "description": "Assign a production material for acceptance-gate regression.",
            "parameters": {"material": "Plain Carbon Steel"},
        },
    )
    material_plan = ModelPlan.from_dict(material_plan_raw)
    material_dimensions = mounting_plate_basic_dimension_ids_from_plan(material_plan)
    material_acceptance = _build_production_acceptance_result(
        material_plan,
        True,
        _accepted_diagnostics(
            material_dimensions,
            "trusted_dimensions_created",
            material_status="material_verified",
            current_material="Plain Carbon Steel",
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if material_acceptance.get("status") != "accepted":
        raise SystemExit(f"Expected verified material fixture to pass: {material_acceptance}")

    alias_material_acceptance = _build_production_acceptance_result(
        material_plan,
        True,
        _accepted_diagnostics(
            material_dimensions,
            "trusted_dimensions_created",
            material_status="material_verified",
            current_material="普通碳钢",
            effective_material="普通碳钢",
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if alias_material_acceptance.get("status") != "accepted":
        raise SystemExit(f"Expected controlled material alias fixture to pass: {alias_material_acceptance}")
    if alias_material_acceptance.get("summary", {}).get("effective_material") != "普通碳钢":
        raise SystemExit(f"Expected alias summary to include effective_material: {alias_material_acceptance}")

    property_plan_raw = copy.deepcopy(wide_plan_raw)
    property_plan_raw["operations"].insert(
        1,
        {
            "id": "custom_properties",
            "op": "set_custom_properties",
            "description": "Assign trusted manufacturing metadata for acceptance-gate regression.",
            "parameters": {
                "properties": {
                    "PartNo": "MP-120-080-M8",
                    "Revision": "A",
                    "Description": "Mounting plate smoke fixture",
                }
            },
        },
    )
    property_plan = ModelPlan.from_dict(property_plan_raw)
    property_dimensions = mounting_plate_basic_dimension_ids_from_plan(property_plan)
    property_acceptance = _build_production_acceptance_result(
        property_plan,
        True,
        _accepted_diagnostics(
            property_dimensions,
            "trusted_dimensions_created",
            custom_property_status="custom_properties_verified",
            current_custom_properties={
                "PartNo": "MP-120-080-M8",
                "Revision": "A",
                "Description": "Mounting plate smoke fixture",
            },
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if property_acceptance.get("status") != "accepted":
        raise SystemExit(f"Expected verified custom property fixture to pass: {property_acceptance}")

    unverified_property_acceptance = _build_production_acceptance_result(
        property_plan,
        True,
        _accepted_diagnostics(
            property_dimensions,
            "trusted_dimensions_created",
            custom_property_status="custom_property_unverified",
            current_custom_properties={"PartNo": "wrong"},
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if unverified_property_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected unverified custom property fixture to be rejected: {unverified_property_acceptance}")
    if "custom_properties_verified" not in unverified_property_acceptance.get("failures", []):
        raise SystemExit(
            f"Unverified custom property fixture did not report custom_properties_verified failure: {unverified_property_acceptance}"
        )

    property_pdf_acceptance = _build_production_acceptance_result(
        property_plan,
        True,
        _accepted_diagnostics(
            property_dimensions,
            "trusted_dimensions_created",
            custom_property_status="custom_properties_verified",
            current_custom_properties={
                "PartNo": "MP-120-080-M8",
                "Revision": "A",
                "Description": "Mounting plate smoke fixture",
            },
            pdf_semantic_status="pdf_semantic_content_missing",
            pdf_semantic_missing=["custom_property_PartNo"],
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if property_pdf_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected custom-property PDF fixture to be rejected: {property_pdf_acceptance}")
    if "drawing_pdf_semantic_content" not in property_pdf_acceptance.get("failures", []):
        raise SystemExit(
            f"Custom-property PDF fixture did not report drawing_pdf_semantic_content failure: {property_pdf_acceptance}"
        )

    unverified_cleanup_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(
            wide_dimensions,
            "trusted_dimensions_created",
            cleanup_verification_status="unverified",
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if unverified_cleanup_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected unverified cleanup fixture to be rejected: {unverified_cleanup_acceptance}")
    if "cleanup_verified" not in unverified_cleanup_acceptance.get("failures", []):
        raise SystemExit(
            f"Unverified cleanup fixture did not report cleanup_verified failure: {unverified_cleanup_acceptance}"
        )
    if not _has_repair_action(unverified_cleanup_acceptance, "cleanup_verified"):
        raise SystemExit(f"Missing cleanup verification repair action: {unverified_cleanup_acceptance}")

    document_state_audit_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(
            wide_dimensions,
            "trusted_dimensions_created",
            document_state_audit_status="run_documents_still_open",
            document_state_after_cleanup_run_created_open_count=1,
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if document_state_audit_acceptance.get("status") != "rejected":
        raise SystemExit(
            f"Expected document-state audit fixture to be rejected: {document_state_audit_acceptance}"
        )
    if "document_state_audit_verified" not in document_state_audit_acceptance.get("failures", []):
        raise SystemExit(
            f"Document-state fixture did not report audit failure: {document_state_audit_acceptance}"
        )
    if not _has_repair_action(document_state_audit_acceptance, "document_state_audit_verified"):
        raise SystemExit(f"Missing document-state audit repair action: {document_state_audit_acceptance}")

    forced_cleanup_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(
            wide_dimensions,
            "trusted_dimensions_created",
            cleanup_status="forced_failure",
            cleanup_verification_status="failed",
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if forced_cleanup_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected forced cleanup fixture to be rejected: {forced_cleanup_acceptance}")
    forced_cleanup_failures = forced_cleanup_acceptance.get("failures", [])
    if "cleanup_completed" not in forced_cleanup_failures or "cleanup_verified" not in forced_cleanup_failures:
        raise SystemExit(
            f"Forced cleanup fixture did not report cleanup failures: {forced_cleanup_acceptance}"
        )

    unverified_material_acceptance = _build_production_acceptance_result(
        material_plan,
        True,
        _accepted_diagnostics(
            material_dimensions,
            "trusted_dimensions_created",
            material_status="material_set_unverified",
            current_material="",
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if unverified_material_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected unverified material fixture to be rejected: {unverified_material_acceptance}")
    if "material_verified" not in unverified_material_acceptance.get("failures", []):
        raise SystemExit(
            f"Unverified material fixture did not report material_verified failure: {unverified_material_acceptance}"
        )
    if not _has_repair_action(unverified_material_acceptance, "material_verified"):
        raise SystemExit(f"Missing material repair action: {unverified_material_acceptance}")

    forced_material_acceptance = _build_production_acceptance_result(
        material_plan,
        True,
        _accepted_diagnostics(
            material_dimensions,
            "trusted_dimensions_created",
            material_status="forced_failure",
            current_material=None,
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if forced_material_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected forced material fixture to be rejected: {forced_material_acceptance}")
    if "material_verified" not in forced_material_acceptance.get("failures", []):
        raise SystemExit(
            f"Forced material fixture did not report material_verified failure: {forced_material_acceptance}"
        )

    bad_geometry_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(
            wide_dimensions,
            "trusted_dimensions_created",
            model_geometry_status="geometry_mismatch",
            measured_dimensions_mm=[12.0, 90.0, 130.0],
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if bad_geometry_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected geometry mismatch fixture to be rejected: {bad_geometry_acceptance}")
    if "model_geometry_verified" not in bad_geometry_acceptance.get("failures", []):
        raise SystemExit(
            f"Geometry mismatch fixture did not report model_geometry_verified failure: {bad_geometry_acceptance}"
        )

    bad_mass_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(
            wide_dimensions,
            "trusted_dimensions_created",
            mass_property_status="mass_property_invalid",
            mass_kg=0.0,
            volume_m3=0.0,
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if bad_mass_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected invalid mass property fixture to be rejected: {bad_mass_acceptance}")
    if "mass_properties_verified" not in bad_mass_acceptance.get("failures", []):
        raise SystemExit(
            f"Invalid mass property fixture did not report mass_properties_verified failure: {bad_mass_acceptance}"
        )

    bad_pdf_content_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(
            wide_dimensions,
            "trusted_dimensions_created",
            pdf_semantic_status="pdf_semantic_content_missing",
            pdf_semantic_missing=["thread_spec"],
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if bad_pdf_content_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected PDF semantic-content fixture to be rejected: {bad_pdf_content_acceptance}")
    if "drawing_pdf_semantic_content" not in bad_pdf_content_acceptance.get("failures", []):
        raise SystemExit(
            "PDF semantic-content fixture did not report drawing_pdf_semantic_content failure: "
            f"{bad_pdf_content_acceptance}"
        )

    bad_cad_content_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(
            wide_dimensions,
            "trusted_dimensions_created",
            cad_content_status="cad_artifact_content_failed",
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if bad_cad_content_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected CAD content fixture to be rejected: {bad_cad_content_acceptance}")
    if "cad_artifact_content" not in bad_cad_content_acceptance.get("failures", []):
        raise SystemExit(
            f"CAD content fixture did not report cad_artifact_content failure: {bad_cad_content_acceptance}"
        )

    missing_requested_output_acceptance = _build_production_acceptance_result(
        wide_plan,
        True,
        _accepted_diagnostics(wide_dimensions, "trusted_dimensions_created"),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if missing_requested_output_acceptance.get("status") != "rejected":
        raise SystemExit(
            f"Expected missing requested output fixture to be rejected: {missing_requested_output_acceptance}"
        )
    if "requested_output_files" not in missing_requested_output_acceptance.get("failures", []):
        raise SystemExit(
            "Missing requested output fixture did not report requested_output_files failure: "
            f"{missing_requested_output_acceptance}"
        )

    hash_diagnostics = _check_artifact_hash_diagnostics(acceptance)
    cleanup_preflight = _check_cleanup_policy_preflight(base_plan)
    post_run_cleanup = _check_post_run_cleanup_tool()
    post_run_attach_only = _check_post_run_cleanup_attach_only()
    direct_callout_preflight = _check_direct_callout_policy_preflight(base_plan)
    trusted_workflow_preflight = _check_trusted_workflow_policy_preflight(base_plan)

    invalid_cases = [
        ("m8_edge_offset_too_small", {"thread_spec": "M8", "edge_offset": 5}),
        ("corner_radius_too_large", {"corner_radius": 41}),
        ("thickness_too_small_for_m8", {"thread_spec": "M8", "thickness": 6}),
    ]
    rejected = []
    for case_name, overrides in invalid_cases:
        plan = copy.deepcopy(base_plan)
        params = plan["operations"][0]["parameters"]
        params.update(overrides)
        try:
            ModelPlan.from_dict(plan)
        except PlanValidationError as exc:
            rejected.append({"case": case_name, "reason": str(exc)})
        else:
            raise SystemExit(f"Expected validation failure for {case_name}")

    washer_rejection = _check_washer_schema_rejection()
    sleeve_rejection = _check_sleeve_schema_rejection()
    mounting_block_rejection = _check_mounting_block_schema_rejection()
    bracket_rejection = _check_bracket_schema_rejection()
    slotted_array_plate_rejection = _check_slotted_array_plate_schema_rejection()
    shaft_rejection = _check_shaft_schema_rejection()
    end_cap_rejection = _check_end_cap_schema_rejection()

    print(json.dumps({
        "ok": True,
        "valid_thread_specs": valid,
        "default_dimension_ids": default_dimensions,
        "wide_dimension_ids": wide_dimensions,
        "optional_export_formats": export_format_diagnostics,
        "production_scenarios": production_scenario_diagnostics,
        "release_gate": release_gate_diagnostics,
        "smoke_exception": smoke_exception_diagnostics,
        "center_hole_flange_candidate": flange_diagnostics,
        "acceptance_dimension_layout_status": summary["dimension_layout_status"],
        "untrusted_workflow_rejection": untrusted_workflow_acceptance["failures"],
        "proxy_dimension_rejection": proxy_acceptance["failures"],
        "untrusted_dimension_layout_rejection": untrusted_layout_acceptance["failures"],
        "non_radial_radius_rejection": non_radial_acceptance["failures"],
        "strict_callout_rejection": strict_acceptance["failures"],
        "drawing_view_rejection": missing_view_acceptance["failures"],
        "cleanup_rejection": unverified_cleanup_acceptance["failures"],
        "forced_cleanup_rejection": forced_cleanup_acceptance["failures"],
        "document_state_audit_rejection": document_state_audit_acceptance["failures"],
        "material_rejection": unverified_material_acceptance["failures"],
        "forced_material_rejection": forced_material_acceptance["failures"],
        "geometry_rejection": bad_geometry_acceptance["failures"],
        "mass_property_rejection": bad_mass_acceptance["failures"],
        "pdf_semantic_rejection": bad_pdf_content_acceptance["failures"],
        "custom_property_pdf_rejection": property_pdf_acceptance["failures"],
        "cad_content_rejection": bad_cad_content_acceptance["failures"],
        "missing_requested_output_rejection": missing_requested_output_acceptance["failures"],
        "artifact_hash_status": hash_diagnostics["ok_status"],
        "fixed_file_hash_rejection": hash_diagnostics["fixed_hash_status"],
        "delivery_manifest_status": hash_diagnostics["manifest_status"],
        "handoff_summary_status": hash_diagnostics["handoff_summary_status"],
        "batch_handoff_summary_status": hash_diagnostics["batch_handoff_summary_status"],
        "environment_status": hash_diagnostics["environment_status"],
        "environment_run_id_mismatch_status": hash_diagnostics["environment_run_id_mismatch_status"],
        "environment_adapter_mismatch_status": hash_diagnostics["environment_adapter_mismatch_status"],
        "environment_env_adapter_mismatch_status": hash_diagnostics["environment_env_adapter_mismatch_status"],
        "environment_run_dir_mismatch_status": hash_diagnostics["environment_run_dir_mismatch_status"],
        "real_missing_safety_status": hash_diagnostics["real_missing_safety_status"],
        "batch_ok_status": hash_diagnostics["batch_ok_status"],
        "batch_rejected_status": hash_diagnostics["batch_rejected_status"],
        "batch_truncated_status": hash_diagnostics["batch_truncated_status"],
        "cleanup_policy_preflight_status": cleanup_preflight["preflight_status"],
        "cleanup_policy_failure_class": cleanup_preflight["failure_class"],
        "post_run_cleanup_status": post_run_cleanup["status"],
        "post_run_cleanup_verification_status": post_run_cleanup["cleanup_verification_status"],
        "post_run_cleanup_forced_status": post_run_cleanup["forced_status"],
        "post_run_cleanup_forced_verification_status": post_run_cleanup["forced_cleanup_verification_status"],
        "post_run_cleanup_attach_only_status": post_run_attach_only["status"],
        "post_run_cleanup_attach_only_failure_reason": post_run_attach_only["failure_reason"],
        "direct_callout_policy_preflight_status": direct_callout_preflight["preflight_status"],
        "direct_callout_policy_failure_class": direct_callout_preflight["failure_class"],
        "trusted_workflow_policy_preflight_status": trusted_workflow_preflight["preflight_status"],
        "trusted_workflow_policy_failure_class": trusted_workflow_preflight["failure_class"],
        "trusted_workflow_policy_event_log_status": trusted_workflow_preflight["event_log_status"],
        "trusted_workflow_policy_recovered_preflight_events": trusted_workflow_preflight["recovered_preflight_event_count"],
        "controlled_plan_validation_readiness": validation_readiness["controlled_plan"],
        "freeform_plan_validation_readiness": validation_readiness["freeform_plan"],
        "event_log_status": hash_diagnostics["event_log_status"],
        "artifact_run_id_mismatch_status": hash_diagnostics["artifact_run_id_mismatch_status"],
        "event_run_id_mismatch_status": hash_diagnostics["event_run_id_mismatch_status"],
        "missing_event_run_id_status": hash_diagnostics["missing_event_run_id_status"],
        "terminal_count_mismatch_status": hash_diagnostics["terminal_count_mismatch_status"],
        "terminal_missing_status": hash_diagnostics["terminal_missing_status"],
        "terminal_mismatch_status": hash_diagnostics["terminal_mismatch_status"],
        "recovered_event_status": hash_diagnostics["recovered_event_status"],
        "failed_event_status": hash_diagnostics["failed_event_status"],
        "delivery_manifest_mismatch_status": hash_diagnostics["bad_manifest_status"],
        "delivery_manifest_missing_status": hash_diagnostics["missing_manifest_status"],
        "missing_output_sha_status": hash_diagnostics["missing_output_sha_status"],
        "missing_relative_path_status": hash_diagnostics["missing_relative_path_status"],
        "missing_preview_sha_status": hash_diagnostics["missing_preview_sha_status"],
        "missing_fixed_sha_status": hash_diagnostics["missing_fixed_sha_status"],
        "manifest_missing_sha_status": hash_diagnostics["manifest_missing_sha_status"],
        "manifest_relative_path_mismatch_status": hash_diagnostics["manifest_relative_path_mismatch_status"],
        "handoff_relative_path_mismatch_status": hash_diagnostics["handoff_relative_path_mismatch_status"],
        "old_manifest_handoff_compat_status": hash_diagnostics["old_manifest_handoff_compat_status"],
        "missing_handoff_summary_status": hash_diagnostics["missing_handoff_summary_status"],
        "bad_handoff_summary_status": hash_diagnostics["bad_handoff_summary_status"],
        "sparse_index_status": hash_diagnostics["sparse_index_status"],
        "invalid_group_status": hash_diagnostics["invalid_group_status"],
        "missing_fixed_entry_status": hash_diagnostics["missing_fixed_entry_status"],
        "report_key_mismatch_status": hash_diagnostics["report_key_mismatch_status"],
        "report_path_mismatch_status": hash_diagnostics["report_path_mismatch_status"],
        "artifact_hash_rejection": hash_diagnostics["tampered_status"],
        "artifact_hash_missing_index": hash_diagnostics["missing_index_status"],
        "washer_schema_rejection": washer_rejection,
        "sleeve_schema_rejection": sleeve_rejection,
        "mounting_block_schema_rejection": mounting_block_rejection,
        "bracket_schema_rejection": bracket_rejection,
        "slotted_array_plate_schema_rejection": slotted_array_plate_rejection,
        "shaft_schema_rejection": shaft_rejection,
        "end_cap_schema_rejection": end_cap_rejection,
        "rejected_cases": rejected,
    }, indent=2))
    return 0


def _check_washer_schema_rejection() -> str:
    """Verify unsafe controlled washer wall thickness is rejected before execution."""

    washer_raw = json.loads((ROOT / "examples" / "washer_plan.json").read_text(encoding="utf-8"))
    invalid_raw = copy.deepcopy(washer_raw)
    invalid_raw["operations"][0]["parameters"]["inner_diameter"] = 29.5
    try:
        ModelPlan.from_dict(invalid_raw)
    except PlanValidationError as exc:
        return str(exc)
    raise SystemExit("Expected unsafe washer radial wall to be rejected")


def _check_sleeve_schema_rejection() -> str:
    """Verify unsafe controlled sleeve wall thickness is rejected before execution."""

    sleeve_raw = json.loads((ROOT / "examples" / "sleeve_plan.json").read_text(encoding="utf-8"))
    invalid_raw = copy.deepcopy(sleeve_raw)
    invalid_raw["operations"][0]["parameters"]["inner_diameter"] = 38.5
    try:
        ModelPlan.from_dict(invalid_raw)
    except PlanValidationError as exc:
        return str(exc)
    raise SystemExit("Expected unsafe sleeve radial wall to be rejected")


def _check_mounting_block_schema_rejection() -> str:
    """Verify unsafe controlled mounting-block side wall is rejected before execution."""

    mounting_block_raw = json.loads((ROOT / "examples" / "mounting_block_plan.json").read_text(encoding="utf-8"))
    invalid_raw = copy.deepcopy(mounting_block_raw)
    invalid_raw["operations"][0]["parameters"]["hole_diameter"] = 45
    try:
        ModelPlan.from_dict(invalid_raw)
    except PlanValidationError as exc:
        return str(exc)
    raise SystemExit("Expected unsafe mounting block side wall to be rejected")


def _check_bracket_schema_rejection() -> str:
    """Verify unsafe controlled bracket hole geometry is rejected before execution."""

    bracket_raw = json.loads((ROOT / "examples" / "bracket_plan.json").read_text(encoding="utf-8"))
    invalid_raw = copy.deepcopy(bracket_raw)
    invalid_raw["operations"][0]["parameters"]["hole_diameter"] = 10
    try:
        ModelPlan.from_dict(invalid_raw)
    except PlanValidationError as exc:
        return str(exc)
    raise SystemExit("Expected unsafe bracket hole geometry to be rejected")


def _check_slotted_array_plate_schema_rejection() -> str:
    """Verify unsafe controlled slotted-array plate clearance is rejected before execution."""

    plate_raw = json.loads((ROOT / "examples" / "slotted_array_plate_plan.json").read_text(encoding="utf-8"))
    invalid_raw = copy.deepcopy(plate_raw)
    invalid_raw["operations"][0]["parameters"]["hole_spacing_y"] = 24
    try:
        ModelPlan.from_dict(invalid_raw)
    except PlanValidationError as exc:
        return str(exc)
    raise SystemExit("Expected unsafe slotted-array plate slot-to-hole clearance to be rejected")


def _check_shaft_schema_rejection() -> str:
    """Verify unsafe controlled shaft proportions are rejected before execution."""

    shaft_raw = json.loads((ROOT / "examples" / "shaft_plan.json").read_text(encoding="utf-8"))
    invalid_raw = copy.deepcopy(shaft_raw)
    invalid_raw["operations"][0]["parameters"]["length"] = 10
    try:
        ModelPlan.from_dict(invalid_raw)
    except PlanValidationError as exc:
        return str(exc)
    raise SystemExit("Expected unsafe shaft length to be rejected")


def _check_end_cap_schema_rejection() -> str:
    """Verify unsafe controlled end-cap bolt-hole wall thickness is rejected before execution."""

    end_cap_raw = json.loads((ROOT / "examples" / "end_cap_plan.json").read_text(encoding="utf-8"))
    invalid_raw = copy.deepcopy(end_cap_raw)
    invalid_raw["operations"][0]["parameters"]["bolt_circle_diameter"] = 96
    try:
        ModelPlan.from_dict(invalid_raw)
    except PlanValidationError as exc:
        return str(exc)
    raise SystemExit("Expected unsafe end-cap outer wall to be rejected")


def _check_artifact_hash_diagnostics(acceptance: dict[str, object]) -> dict[str, object]:
    """Verify copied-run artifact SHA-256 diagnostics."""

    with tempfile.TemporaryDirectory() as temp_dir:
        run_dir = Path(temp_dir) / "run_hash_fixture"
        exports_dir = run_dir / "exports"
        previews_dir = run_dir / "previews"
        exports_dir.mkdir(parents=True)
        previews_dir.mkdir(parents=True)
        artifact = exports_dir / "fixture.step"
        artifact.write_text("fixture-step-data", encoding="utf-8")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        preview = previews_dir / "fixture_top.png"
        preview.write_text("fixture-preview-data", encoding="utf-8")
        preview_digest = hashlib.sha256(preview.read_bytes()).hexdigest()
        plan_file = run_dir / "plan.normalized.json"
        plan_fixture_text = json.dumps({"name": "hash_fixture"}, ensure_ascii=False)
        plan_file.write_text(plan_fixture_text, encoding="utf-8")
        plan_digest = hashlib.sha256(plan_file.read_bytes()).hexdigest()
        fixture_required_dimensions = (
            acceptance.get("expected", {}).get("required_dimensions", [])
            if isinstance(acceptance.get("expected"), dict)
            else []
        )
        fixture_diagnostics = _accepted_diagnostics(
            [str(item) for item in fixture_required_dimensions],
            "trusted_dimensions_created",
            require_direct_hole_callout=True,
        )
        fixture_diagnostics["production_acceptance_result"] = acceptance
        report_payload = {
            "ok": True,
            "adapter": "mock",
            "run_id": "hash_fixture",
            "run_dir": str(run_dir),
            "plan_name": "hash_fixture",
            "message": "fixture",
            "production_verdict": acceptance,
            "report_file": str(run_dir / "execution_report.json"),
            "artifacts_file": str(run_dir / "artifacts.json"),
            "delivery_manifest_file": str(run_dir / "delivery_manifest.json"),
            "events_file": str(run_dir / "events.jsonl"),
            "environment_file": str(run_dir / "environment.json"),
            "output_files": {"step": str(artifact)},
            "preview_files": {"top": str(preview)},
            "diagnostics": fixture_diagnostics,
        }
        artifacts_payload = {
            "schema_version": "2026-06-06.2",
            "run_id": "hash_fixture",
            "run_dir": str(run_dir),
            "fixed_files": {
                "plan": _fixture_file_entry(plan_file, base_dir=run_dir),
            },
            "output_files": {
                "step": {
                    "path": str(artifact),
                    "relative_path": "exports/fixture.step",
                    "exists": True,
                    "is_file": True,
                    "size_bytes": artifact.stat().st_size,
                    "ok": True,
                    "sha256": digest,
                }
            },
            "preview_files": {},
            "directories": {},
        }
        artifacts_payload["preview_files"]["top"] = {
            "path": str(preview),
            "relative_path": "previews/fixture_top.png",
            "exists": True,
            "is_file": True,
            "size_bytes": preview.stat().st_size,
            "ok": True,
            "sha256": preview_digest,
        }
        manifest_payload = {
            "schema_version": "2026-06-06.2",
            "run_id": "hash_fixture",
            "run_dir": str(run_dir),
            "plan_name": "hash_fixture",
            "adapter": "mock",
            "ok": True,
            "production_verdict": acceptance,
            "report_file": str(run_dir / "execution_report.json"),
            "artifacts_file": str(run_dir / "artifacts.json"),
            "delivery_manifest_file": str(run_dir / "delivery_manifest.json"),
            "events_file": str(run_dir / "events.jsonl"),
            "environment_file": str(run_dir / "environment.json"),
            "output_files": artifacts_payload["output_files"],
            "preview_files": artifacts_payload["preview_files"],
            "diagnose_command": f"python scripts/diagnose_run.py {run_dir} --summary-only",
        }
        manifest_payload["handoff_summary"] = _fixture_handoff_summary(manifest_payload, report_payload)
        environment_payload = _fixture_environment_payload(run_dir, adapter="mock")
        (run_dir / "execution_report.json").write_text(
            json.dumps(report_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        (run_dir / "environment.json").write_text(json.dumps(environment_payload, ensure_ascii=False), encoding="utf-8")
        (run_dir / "events.jsonl").write_text(
            json.dumps({"event": "fixture.ready", "status": "completed", "run_id": "hash_fixture"}, ensure_ascii=False)
            + "\n"
            + json.dumps(
                {
                    "event": "plan.execution",
                    "status": "completed",
                    "run_id": "hash_fixture",
                    "details": {"ok": True, "output_count": 1, "preview_count": 1},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (run_dir / "artifacts.json").write_text(
            json.dumps(artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(manifest_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        artifacts_payload["fixed_files"].update(
            {
                "report": _fixture_file_entry(run_dir / "execution_report.json", base_dir=run_dir),
                "events": _fixture_file_entry(run_dir / "events.jsonl", base_dir=run_dir),
                "environment": _fixture_file_entry(run_dir / "environment.json", base_dir=run_dir),
                "delivery_manifest": _fixture_file_entry(run_dir / "delivery_manifest.json", base_dir=run_dir),
                "artifacts": _fixture_file_entry(run_dir / "artifacts.json", include_hash=False, base_dir=run_dir),
            }
        )
        (run_dir / "artifacts.json").write_text(
            json.dumps(artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        ok_result = diagnose_run_directory(run_dir, summary_only=True)
        if ok_result.get("artifact_integrity_status") != "verified":
            raise SystemExit(f"Expected artifact hash fixture to verify: {ok_result}")
        if ok_result.get("delivery_manifest_status") != "verified":
            raise SystemExit(f"Expected delivery manifest fixture to verify: {ok_result}")
        if ok_result.get("environment_status") != "verified":
            raise SystemExit(f"Expected environment fixture to verify: {ok_result}")
        if ok_result.get("repair_actions"):
            raise SystemExit(f"Accepted diagnosis should expose no repair actions: {ok_result}")
        current_recheck = ok_result.get("current_acceptance_recheck")
        if not isinstance(current_recheck, dict) or current_recheck.get("status") != "verified":
            raise SystemExit(f"Expected accepted fixture to verify current acceptance gates: {ok_result}")
        handoff_summary = ok_result.get("delivery_handoff_summary")
        if not isinstance(handoff_summary, dict):
            raise SystemExit(f"Expected diagnosis to expose delivery_handoff_summary: {ok_result}")
        if handoff_summary.get("delivery_status") != "accepted":
            raise SystemExit(f"Expected handoff summary delivery_status=accepted: {ok_result}")
        if handoff_summary.get("artifact_counts") != {"outputs": 1, "previews": 1}:
            raise SystemExit(f"Expected handoff summary artifact counts: {ok_result}")
        if handoff_summary.get("repair_actions"):
            raise SystemExit(f"Accepted handoff should expose no repair actions: {ok_result}")
        key_statuses = handoff_summary.get("key_statuses") if isinstance(handoff_summary, dict) else {}
        if not isinstance(key_statuses, dict) or key_statuses.get("drawing_annotation_status") != "hole_callout_created":
            raise SystemExit(f"Expected handoff summary drawing_annotation_status=hole_callout_created: {ok_result}")
        stale_report_payload = copy.deepcopy(report_payload)
        stale_report_payload["diagnostics"].pop("drawing_view_result", None)
        (run_dir / "execution_report.json").write_text(
            json.dumps(stale_report_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        stale_artifacts_payload = copy.deepcopy(artifacts_payload)
        stale_artifacts_payload["fixed_files"]["report"] = _fixture_file_entry(
            run_dir / "execution_report.json",
            base_dir=run_dir,
        )
        (run_dir / "artifacts.json").write_text(
            json.dumps(stale_artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        stale_result = diagnose_run_directory(run_dir, summary_only=True)
        if stale_result.get("production_acceptance_status") != "rejected":
            raise SystemExit(f"Expected stale current-acceptance fixture to reject: {stale_result}")
        if "drawing_standard_views_created" not in (stale_result.get("production_acceptance_failures") or []):
            raise SystemExit(f"Expected stale fixture to report drawing view failure: {stale_result}")
        if stale_result.get("stored_production_acceptance_status") != "accepted":
            raise SystemExit(f"Expected stale fixture to preserve stored accepted status: {stale_result}")
        stale_callout_report_payload = copy.deepcopy(report_payload)
        stale_callout_report_payload["diagnostics"].pop("drawing_annotation_result", None)
        (run_dir / "execution_report.json").write_text(
            json.dumps(stale_callout_report_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        stale_callout_artifacts_payload = copy.deepcopy(artifacts_payload)
        stale_callout_artifacts_payload["fixed_files"]["report"] = _fixture_file_entry(
            run_dir / "execution_report.json",
            base_dir=run_dir,
        )
        (run_dir / "artifacts.json").write_text(
            json.dumps(stale_callout_artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        stale_callout_result = diagnose_run_directory(run_dir, summary_only=True)
        if stale_callout_result.get("production_acceptance_status") != "rejected":
            raise SystemExit(f"Expected stale callout evidence fixture to reject: {stale_callout_result}")
        if "hole_callouts_created" not in (stale_callout_result.get("production_acceptance_failures") or []):
            raise SystemExit(f"Expected stale fixture to report hole callout failure: {stale_callout_result}")
        stale_cleanup_report_payload = copy.deepcopy(report_payload)
        stale_cleanup_report_payload["diagnostics"].pop("cleanup_result", None)
        (run_dir / "execution_report.json").write_text(
            json.dumps(stale_cleanup_report_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        stale_cleanup_artifacts_payload = copy.deepcopy(artifacts_payload)
        stale_cleanup_artifacts_payload["fixed_files"]["report"] = _fixture_file_entry(
            run_dir / "execution_report.json",
            base_dir=run_dir,
        )
        (run_dir / "artifacts.json").write_text(
            json.dumps(stale_cleanup_artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        stale_cleanup_result = diagnose_run_directory(run_dir, summary_only=True)
        if stale_cleanup_result.get("production_acceptance_status") != "rejected":
            raise SystemExit(f"Expected stale cleanup evidence fixture to reject: {stale_cleanup_result}")
        if "cleanup_completed" not in (stale_cleanup_result.get("production_acceptance_failures") or []):
            raise SystemExit(f"Expected stale fixture to report cleanup failure: {stale_cleanup_result}")
        (run_dir / "execution_report.json").write_text(
            json.dumps(report_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        artifacts_payload["fixed_files"]["report"] = _fixture_file_entry(
            run_dir / "execution_report.json",
            base_dir=run_dir,
        )
        (run_dir / "artifacts.json").write_text(
            json.dumps(artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        collection_ok_result = diagnose_run_collection(temp_dir, summary_only=True, max_runs=10)
        if collection_ok_result.get("ok") is not True:
            raise SystemExit(f"Expected batch diagnosis fixture to pass: {collection_ok_result}")
        if collection_ok_result.get("accepted_count") != 1 or collection_ok_result.get("rejected_count") != 0:
            raise SystemExit(f"Expected one accepted batch diagnosis run: {collection_ok_result}")
        collection_default_result = diagnose_run_collection(temp_dir, summary_only=True)
        if collection_default_result.get("ok") is not True:
            raise SystemExit(f"Expected default unbounded batch diagnosis fixture to pass: {collection_default_result}")
        if collection_default_result.get("scan_status") != "complete":
            raise SystemExit(f"Expected default batch diagnosis to complete: {collection_default_result}")
        if collection_default_result.get("scan_unbounded") is not True:
            raise SystemExit(f"Expected default batch diagnosis to be unbounded: {collection_default_result}")
        release_report = {
            "ok": True,
            "status": "accepted",
            "schema_version": "2026-06-06.1",
            "adapter": "mock",
            "output_root": temp_dir,
            "scenarios": ["baseline"],
            "checks": {
                "scenario_smoke": True,
                "batch_diagnosis": True,
                "batch_complete": True,
                "batch_no_rejections": True,
                "batch_count_matches": True,
            },
            "failures": [],
            "scenario_results": [
                {
                    "scenario": "baseline",
                    "ok": True,
                    "run_dir": str(run_dir),
                    "report_file": str(run_dir / "execution_report.json"),
                }
            ],
            "batch_diagnosis": collection_default_result,
        }
        release_report_file = Path(temp_dir) / "release_gate_report.json"
        release_report_file.write_text(json.dumps(release_report, ensure_ascii=False), encoding="utf-8")
        release_report_result = diagnose_release_gate_report(release_report_file, summary_only=True)
        if release_report_result.get("status") != "failed":
            raise SystemExit(f"Expected sparse release report diagnosis to fail current evidence: {release_report_result}")
        sparse_issue_fields = {
            item.get("field") for item in release_report_result.get("issues", []) if isinstance(item, dict)
        }
        if "current_evidence.required_outputs" not in sparse_issue_fields:
            raise SystemExit(f"Expected sparse release report to flag current evidence: {release_report_result}")
        stale_release_report = copy.deepcopy(release_report)
        stale_release_report["batch_diagnosis"]["run_count"] = 2
        release_report_file.write_text(json.dumps(stale_release_report, ensure_ascii=False), encoding="utf-8")
        stale_release_result = diagnose_release_gate_report(release_report_file, summary_only=True)
        if stale_release_result.get("status") != "failed":
            raise SystemExit(f"Expected stale release report diagnosis to fail: {stale_release_result}")
        issue_fields = {item.get("field") for item in stale_release_result.get("issues", []) if isinstance(item, dict)}
        if "batch_diagnosis.run_count" not in issue_fields:
            raise SystemExit(f"Expected stale release report to flag batch run_count: {stale_release_result}")
        release_report_file.write_text(json.dumps(release_report, ensure_ascii=False), encoding="utf-8")

        with tempfile.TemporaryDirectory() as release_temp_dir:
            release_root = Path(release_temp_dir) / "release_gate_full_fixture"
            release_report_file = _write_release_gate_directory_fixture(
                release_root,
                (
                    "baseline",
                    "material_alias",
                    "custom_properties",
                    "combined",
                    "drawing_exchange",
                    "neutral_exports",
                    "wide_combined",
                    "flange_baseline",
                    "center_hole_plate_baseline",
                    "bracket_baseline",
                    "end_cap_baseline",
                    "mounting_block_baseline",
                    "shaft_baseline",
                    "washer_baseline",
                    "sleeve_baseline",
                    "slotted_array_plate_baseline",
                ),
            )
            release_report_result = diagnose_release_gate_report(release_report_file, summary_only=True)
            if release_report_result.get("status") != "verified":
                raise SystemExit(f"Expected full release report diagnosis to verify: {release_report_result}")
            current_evidence = release_report_result.get("current_evidence_summary")
            if not isinstance(current_evidence, dict) or current_evidence.get("direct_callout_count") != 16:
                raise SystemExit(f"Expected recomputed release evidence in diagnosis: {release_report_result}")
            tampered_run = release_root / "release_fixture_combined"
            tampered_report = json.loads((tampered_run / "execution_report.json").read_text(encoding="utf-8"))
            tampered_report["output_files"].pop("dwg", None)
            (tampered_run / "execution_report.json").write_text(
                json.dumps(tampered_report, ensure_ascii=False),
                encoding="utf-8",
            )
            tampered_artifacts = json.loads((tampered_run / "artifacts.json").read_text(encoding="utf-8"))
            tampered_artifacts["fixed_files"]["report"] = _fixture_file_entry(
                tampered_run / "execution_report.json",
                base_dir=tampered_run,
            )
            (tampered_run / "artifacts.json").write_text(
                json.dumps(tampered_artifacts, ensure_ascii=False),
                encoding="utf-8",
            )
            tampered_release_result = diagnose_release_gate_report(release_report_file, summary_only=True)
            if tampered_release_result.get("status") != "failed":
                raise SystemExit(f"Expected tampered current release evidence to fail: {tampered_release_result}")
            tampered_issue_fields = {
                item.get("field") for item in tampered_release_result.get("issues", []) if isinstance(item, dict)
            }
            if "current_evidence.required_outputs" not in tampered_issue_fields:
                raise SystemExit(f"Expected tampered release to flag required outputs: {tampered_release_result}")

        collection_handoff = collection_ok_result.get("results", [{}])[0].get("delivery_handoff_summary")
        if not isinstance(collection_handoff, dict):
            raise SystemExit(f"Expected batch compact result to expose delivery_handoff_summary: {collection_ok_result}")
        if collection_handoff.get("artifact_counts") != {"outputs": 1, "previews": 1}:
            raise SystemExit(f"Expected batch compact handoff artifact counts: {collection_ok_result}")
        collection_key_statuses = collection_handoff.get("key_statuses")
        if not isinstance(collection_key_statuses, dict) or collection_key_statuses.get("drawing_annotation_status") != "hole_callout_created":
            raise SystemExit(f"Expected batch compact handoff drawing_annotation_status=hole_callout_created: {collection_ok_result}")
        truncated_run_dir = Path(temp_dir) / "run_hash_fixture_copy"
        shutil.copytree(run_dir, truncated_run_dir)
        collection_truncated_result = diagnose_run_collection(temp_dir, summary_only=True, max_runs=1)
        if collection_truncated_result.get("ok") is not False:
            raise SystemExit(f"Expected truncated batch diagnosis to fail gate: {collection_truncated_result}")
        if collection_truncated_result.get("scan_status") != "truncated":
            raise SystemExit(f"Expected truncated scan status: {collection_truncated_result}")
        environment_run_id_mismatch = copy.deepcopy(environment_payload)
        environment_run_id_mismatch["run_id"] = "other_run"
        environment_run_id_mismatch_result = _diagnose_with_fixture_environment(
            run_dir,
            artifacts_payload,
            environment_run_id_mismatch,
        )
        if environment_run_id_mismatch_result.get("environment_status") != "failed":
            raise SystemExit(
                f"Expected environment run-id mismatch fixture to fail: {environment_run_id_mismatch_result}"
            )
        if not _has_environment_issue(environment_run_id_mismatch_result, "run_id", "mismatch"):
            raise SystemExit(f"Expected environment run-id mismatch status: {environment_run_id_mismatch_result}")
        environment_adapter_mismatch = copy.deepcopy(environment_payload)
        environment_adapter_mismatch["adapter"] = "unexpected"
        environment_adapter_mismatch_result = _diagnose_with_fixture_environment(
            run_dir,
            artifacts_payload,
            environment_adapter_mismatch,
        )
        if environment_adapter_mismatch_result.get("environment_status") != "failed":
            raise SystemExit(
                f"Expected environment adapter mismatch fixture to fail: {environment_adapter_mismatch_result}"
            )
        if not _has_environment_issue(environment_adapter_mismatch_result, "adapter", "mismatch"):
            raise SystemExit(f"Expected environment adapter mismatch status: {environment_adapter_mismatch_result}")
        environment_env_adapter_mismatch = copy.deepcopy(environment_payload)
        environment_env_adapter_mismatch["env"]["SOLIDWORKS_MCP_ADAPTER"] = "unexpected"
        environment_env_adapter_result = _diagnose_with_fixture_environment(
            run_dir,
            artifacts_payload,
            environment_env_adapter_mismatch,
        )
        if environment_env_adapter_result.get("environment_status") != "failed":
            raise SystemExit(
                f"Expected environment env-adapter mismatch fixture to fail: {environment_env_adapter_result}"
            )
        if not _has_environment_issue(
            environment_env_adapter_result,
            "env.SOLIDWORKS_MCP_ADAPTER",
            "mismatch",
        ):
            raise SystemExit(f"Expected environment env-adapter mismatch status: {environment_env_adapter_result}")
        environment_run_dir_mismatch = copy.deepcopy(environment_payload)
        environment_run_dir_mismatch["paths"]["run_dir"] = str(run_dir.parent / "other_run")
        environment_run_dir_result = _diagnose_with_fixture_environment(
            run_dir,
            artifacts_payload,
            environment_run_dir_mismatch,
        )
        if environment_run_dir_result.get("environment_status") != "failed":
            raise SystemExit(f"Expected environment run-dir mismatch fixture to fail: {environment_run_dir_result}")
        if not _has_environment_issue(environment_run_dir_result, "paths.run_dir", "mismatch"):
            raise SystemExit(f"Expected environment run-dir mismatch status: {environment_run_dir_result}")
        _write_fixture_environment(run_dir, artifacts_payload, environment_payload)
        real_report_payload = copy.deepcopy(report_payload)
        real_report_payload["adapter"] = "solidworks"
        real_manifest_payload = copy.deepcopy(manifest_payload)
        real_manifest_payload["adapter"] = "solidworks"
        real_environment_payload = _fixture_environment_payload(
            run_dir,
            adapter="solidworks",
            close_documents_after_run=False,
            require_direct_hole_callout=False,
            enforce_trusted_workflow=False,
        )
        _write_fixture_report_manifest_environment(
            run_dir,
            artifacts_payload,
            real_report_payload,
            real_manifest_payload,
            real_environment_payload,
        )
        real_missing_safety_result = diagnose_run_directory(run_dir, summary_only=True)
        if real_missing_safety_result.get("environment_status") != "failed":
            raise SystemExit(f"Expected real safety-flag fixture to fail: {real_missing_safety_result}")
        if not _has_environment_issue(
            real_missing_safety_result,
            "env.SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN",
            "required_true",
        ):
            raise SystemExit(f"Expected close-documents required_true status: {real_missing_safety_result}")
        if not _has_environment_issue(
            real_missing_safety_result,
            "env.SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT",
            "required_true",
        ):
            raise SystemExit(f"Expected direct-callout required_true status: {real_missing_safety_result}")
        if not _has_environment_issue(
            real_missing_safety_result,
            "env.SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW",
            "required_true",
        ):
            raise SystemExit(f"Expected trusted-workflow required_true status: {real_missing_safety_result}")
        collection_rejected_result = diagnose_run_collection(temp_dir, summary_only=True, max_runs=10)
        if collection_rejected_result.get("ok") is not False:
            raise SystemExit(f"Expected batch diagnosis fixture to reject unsafe real run: {collection_rejected_result}")
        if collection_rejected_result.get("rejected_count") != 1:
            raise SystemExit(f"Expected one rejected batch diagnosis run: {collection_rejected_result}")
        issue_counts = collection_rejected_result.get("issue_counts", {})
        if not isinstance(issue_counts, dict) or issue_counts.get(
            "environment:env.SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN:required_true"
        ) != 1:
            raise SystemExit(f"Expected batch environment close-documents issue count: {collection_rejected_result}")
        collection_results = collection_rejected_result.get("results", [])
        if not isinstance(collection_results, list) or not collection_results:
            raise SystemExit(f"Expected batch diagnosis compact result entries: {collection_rejected_result}")
        first_collection_result = collection_results[0]
        if not isinstance(first_collection_result, dict):
            raise SystemExit(f"Expected batch diagnosis result dict: {collection_rejected_result}")
        for field in ("report_file", "artifacts_file", "delivery_manifest_file"):
            if not first_collection_result.get(field):
                raise SystemExit(f"Expected batch diagnosis compact result to include {field}: {collection_rejected_result}")
        per_run_issue_counts = first_collection_result.get("issue_counts")
        if not isinstance(per_run_issue_counts, dict) or per_run_issue_counts.get(
            "environment:env.SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN:required_true"
        ) != 1:
            raise SystemExit(f"Expected per-run batch issue count: {collection_rejected_result}")
        _write_fixture_report_manifest_environment(
            run_dir,
            artifacts_payload,
            report_payload,
            manifest_payload,
            environment_payload,
        )
        artifact_run_id_mismatch_artifacts = copy.deepcopy(artifacts_payload)
        artifact_run_id_mismatch_artifacts["run_id"] = "other_run"
        (run_dir / "artifacts.json").write_text(
            json.dumps(artifact_run_id_mismatch_artifacts, ensure_ascii=False),
            encoding="utf-8",
        )
        artifact_run_id_mismatch_result = diagnose_run_directory(run_dir, summary_only=True)
        if artifact_run_id_mismatch_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(
                f"Expected artifact run-id mismatch fixture to fail: {artifact_run_id_mismatch_result}"
            )
        if not _has_missing_artifact_status(artifact_run_id_mismatch_result, "artifact_run_id_mismatch"):
            raise SystemExit(f"Expected artifact run-id mismatch status: {artifact_run_id_mismatch_result}")
        (run_dir / "artifacts.json").write_text(
            json.dumps(artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        event_run_id_mismatch_result = _diagnose_with_fixture_events(
            run_dir,
            artifacts_payload,
            [
                {"event": "fixture.ready", "status": "completed", "run_id": "other_run"},
                {"event": "plan.execution", "status": "completed", "details": {"ok": True}},
            ],
        )
        if event_run_id_mismatch_result.get("event_log_status") != "failed":
            raise SystemExit(f"Expected event run-id mismatch fixture to fail: {event_run_id_mismatch_result}")
        if not _has_event_log_issue(event_run_id_mismatch_result, "event_run_id_mismatch"):
            raise SystemExit(f"Expected event run-id mismatch issue: {event_run_id_mismatch_result}")
        missing_event_run_id_result = _diagnose_with_fixture_events(
            run_dir,
            artifacts_payload,
            [
                {"event": "fixture.ready", "status": "completed", "run_id": None},
                {"event": "plan.execution", "status": "completed", "details": {"ok": True}},
            ],
        )
        if missing_event_run_id_result.get("event_log_status") != "failed":
            raise SystemExit(f"Expected missing event run-id fixture to fail: {missing_event_run_id_result}")
        if not _has_event_log_issue(missing_event_run_id_result, "missing_event_run_id"):
            raise SystemExit(f"Expected missing event run-id issue: {missing_event_run_id_result}")
        terminal_count_mismatch_result = _diagnose_with_fixture_events(
            run_dir,
            artifacts_payload,
            [
                {"event": "plan.execution", "status": "completed", "details": {"ok": True, "output_count": 99}},
            ],
        )
        if terminal_count_mismatch_result.get("event_log_status") != "failed":
            raise SystemExit(f"Expected terminal count mismatch fixture to fail: {terminal_count_mismatch_result}")
        if not _has_event_log_issue(terminal_count_mismatch_result, "terminal_output_count_mismatch"):
            raise SystemExit(f"Expected terminal output-count mismatch issue: {terminal_count_mismatch_result}")
        _write_fixture_events(
            run_dir,
            artifacts_payload,
            [
                {"event": "fixture.ready", "status": "completed"},
                {"event": "plan.execution", "status": "completed", "details": {"ok": True}},
            ],
        )
        terminal_missing_result = _diagnose_with_fixture_events(
            run_dir,
            artifacts_payload,
            [{"event": "fixture.ready", "status": "completed"}],
        )
        if terminal_missing_result.get("event_log_status") != "failed":
            raise SystemExit(f"Expected missing terminal event fixture to fail: {terminal_missing_result}")
        if not _has_event_log_issue(terminal_missing_result, "missing_terminal_event"):
            raise SystemExit(f"Expected missing terminal event issue: {terminal_missing_result}")
        terminal_mismatch_result = _diagnose_with_fixture_events(
            run_dir,
            artifacts_payload,
            [{"event": "plan.execution", "status": "failed", "details": {"ok": False}}],
        )
        if terminal_mismatch_result.get("event_log_status") != "failed":
            raise SystemExit(f"Expected terminal status mismatch fixture to fail: {terminal_mismatch_result}")
        if not _has_event_log_issue(terminal_mismatch_result, "terminal_status_mismatch"):
            raise SystemExit(f"Expected terminal status mismatch issue: {terminal_mismatch_result}")
        _write_fixture_events(
            run_dir,
            artifacts_payload,
            [
                {"event": "fixture.ready", "status": "completed"},
                {"event": "plan.execution", "status": "completed", "details": {"ok": True}},
            ],
        )
        sparse_artifacts_payload = {"run_id": "hash_fixture"}
        (run_dir / "artifacts.json").write_text(
            json.dumps(sparse_artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        sparse_index_result = diagnose_run_directory(run_dir, summary_only=True)
        if sparse_index_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected sparse artifacts index fixture to fail: {sparse_index_result}")
        if not any(
            item.get("status") == "missing_group"
            for item in sparse_index_result.get("missing_artifacts", [])
            if isinstance(item, dict)
        ):
            raise SystemExit(f"Expected sparse artifacts index to report missing_group: {sparse_index_result}")
        invalid_group_artifacts = copy.deepcopy(artifacts_payload)
        invalid_group_artifacts["output_files"] = []
        (run_dir / "artifacts.json").write_text(
            json.dumps(invalid_group_artifacts, ensure_ascii=False),
            encoding="utf-8",
        )
        invalid_group_result = diagnose_run_directory(run_dir, summary_only=True)
        if invalid_group_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected invalid artifacts group fixture to fail: {invalid_group_result}")
        if not any(
            item.get("group") == "output_files" and item.get("status") == "invalid_group"
            for item in invalid_group_result.get("missing_artifacts", [])
            if isinstance(item, dict)
        ):
            raise SystemExit(f"Expected invalid artifacts group status: {invalid_group_result}")
        missing_fixed_entry_artifacts = copy.deepcopy(artifacts_payload)
        missing_fixed_entry_artifacts["fixed_files"].pop("events", None)
        (run_dir / "artifacts.json").write_text(
            json.dumps(missing_fixed_entry_artifacts, ensure_ascii=False),
            encoding="utf-8",
        )
        missing_fixed_entry_result = diagnose_run_directory(run_dir, summary_only=True)
        if missing_fixed_entry_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected missing fixed entry fixture to fail: {missing_fixed_entry_result}")
        if not any(
            item.get("group") == "fixed_files"
            and item.get("name") == "events"
            and item.get("status") == "missing_fixed_file_entry"
            for item in missing_fixed_entry_result.get("missing_artifacts", [])
            if isinstance(item, dict)
        ):
            raise SystemExit(f"Expected missing fixed entry status: {missing_fixed_entry_result}")
        report_key_mismatch_artifacts = copy.deepcopy(artifacts_payload)
        report_key_mismatch_artifacts["output_files"].pop("step", None)
        (run_dir / "artifacts.json").write_text(
            json.dumps(report_key_mismatch_artifacts, ensure_ascii=False),
            encoding="utf-8",
        )
        report_key_mismatch_result = diagnose_run_directory(run_dir, summary_only=True)
        if report_key_mismatch_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected report/artifact key mismatch fixture to fail: {report_key_mismatch_result}")
        if not any(
            item.get("group") == "output_files" and item.get("status") == "report_keys_mismatch"
            for item in report_key_mismatch_result.get("missing_artifacts", [])
            if isinstance(item, dict)
        ):
            raise SystemExit(f"Expected report/artifact key mismatch status: {report_key_mismatch_result}")
        report_path_mismatch_artifacts = copy.deepcopy(artifacts_payload)
        report_path_mismatch_artifacts["output_files"]["step"]["path"] = str(exports_dir / "unexpected.step")
        (run_dir / "artifacts.json").write_text(
            json.dumps(report_path_mismatch_artifacts, ensure_ascii=False),
            encoding="utf-8",
        )
        report_path_mismatch_result = diagnose_run_directory(run_dir, summary_only=True)
        if report_path_mismatch_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected report/artifact path mismatch fixture to fail: {report_path_mismatch_result}")
        if not any(
            item.get("group") == "output_files" and item.get("status") == "report_path_mismatch"
            for item in report_path_mismatch_result.get("missing_artifacts", [])
            if isinstance(item, dict)
        ):
            raise SystemExit(f"Expected report/artifact path mismatch status: {report_path_mismatch_result}")
        (run_dir / "artifacts.json").write_text(
            json.dumps(artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        missing_output_sha_artifacts = copy.deepcopy(artifacts_payload)
        missing_output_sha_artifacts["output_files"]["step"].pop("sha256", None)
        (run_dir / "artifacts.json").write_text(
            json.dumps(missing_output_sha_artifacts, ensure_ascii=False),
            encoding="utf-8",
        )
        missing_output_sha_result = diagnose_run_directory(run_dir, summary_only=True)
        if missing_output_sha_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected missing output sha256 fixture to fail: {missing_output_sha_result}")
        if not _has_missing_sha256(missing_output_sha_result, "output_files", "step"):
            raise SystemExit(f"Expected missing output sha256 status: {missing_output_sha_result}")
        missing_relative_path_artifacts = copy.deepcopy(artifacts_payload)
        missing_relative_path_artifacts["output_files"]["step"].pop("relative_path", None)
        (run_dir / "artifacts.json").write_text(
            json.dumps(missing_relative_path_artifacts, ensure_ascii=False),
            encoding="utf-8",
        )
        missing_relative_path_result = diagnose_run_directory(run_dir, summary_only=True)
        if missing_relative_path_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected missing relative path fixture to fail: {missing_relative_path_result}")
        if not _has_missing_artifact_status(missing_relative_path_result, "missing_relative_path"):
            raise SystemExit(f"Expected missing relative path status: {missing_relative_path_result}")
        missing_preview_sha_artifacts = copy.deepcopy(artifacts_payload)
        missing_preview_sha_artifacts["preview_files"]["top"].pop("sha256", None)
        (run_dir / "artifacts.json").write_text(
            json.dumps(missing_preview_sha_artifacts, ensure_ascii=False),
            encoding="utf-8",
        )
        missing_preview_sha_result = diagnose_run_directory(run_dir, summary_only=True)
        if missing_preview_sha_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected missing preview sha256 fixture to fail: {missing_preview_sha_result}")
        if not _has_missing_sha256(missing_preview_sha_result, "preview_files", "top"):
            raise SystemExit(f"Expected missing preview sha256 status: {missing_preview_sha_result}")
        missing_fixed_sha_artifacts = copy.deepcopy(artifacts_payload)
        missing_fixed_sha_artifacts["fixed_files"]["plan"].pop("sha256", None)
        (run_dir / "artifacts.json").write_text(
            json.dumps(missing_fixed_sha_artifacts, ensure_ascii=False),
            encoding="utf-8",
        )
        missing_fixed_sha_result = diagnose_run_directory(run_dir, summary_only=True)
        if missing_fixed_sha_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected missing fixed-file sha256 fixture to fail: {missing_fixed_sha_result}")
        if not _has_missing_sha256(missing_fixed_sha_result, "fixed_files", "plan"):
            raise SystemExit(f"Expected missing fixed-file sha256 status: {missing_fixed_sha_result}")
        (run_dir / "artifacts.json").write_text(
            json.dumps(artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        manifest_missing_sha = copy.deepcopy(manifest_payload)
        manifest_missing_sha["output_files"]["step"].pop("sha256", None)
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(manifest_missing_sha, ensure_ascii=False),
            encoding="utf-8",
        )
        manifest_missing_sha_result = diagnose_run_directory(run_dir, summary_only=True)
        if manifest_missing_sha_result.get("delivery_manifest_status") != "failed":
            raise SystemExit(f"Expected missing manifest sha256 fixture to fail: {manifest_missing_sha_result}")
        (run_dir / "artifacts.json").write_text(
            json.dumps(artifacts_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        manifest_bad_relative = copy.deepcopy(manifest_payload)
        manifest_bad_relative["output_files"]["step"]["relative_path"] = "exports/unexpected.step"
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(manifest_bad_relative, ensure_ascii=False),
            encoding="utf-8",
        )
        manifest_bad_relative_result = diagnose_run_directory(run_dir, summary_only=True)
        if manifest_bad_relative_result.get("delivery_manifest_status") != "failed":
            raise SystemExit(f"Expected manifest relative path mismatch fixture to fail: {manifest_bad_relative_result}")
        handoff_bad_relative = copy.deepcopy(manifest_payload)
        handoff_bad_relative["handoff_summary"]["outputs"][0]["relative_path"] = "exports/unexpected.step"
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(handoff_bad_relative, ensure_ascii=False),
            encoding="utf-8",
        )
        handoff_bad_relative_result = diagnose_run_directory(run_dir, summary_only=True)
        if handoff_bad_relative_result.get("delivery_manifest_status") != "failed":
            raise SystemExit(f"Expected handoff relative path mismatch fixture to fail: {handoff_bad_relative_result}")
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(manifest_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        old_manifest_without_handoff = copy.deepcopy(manifest_payload)
        old_manifest_without_handoff["schema_version"] = "2026-06-06.1"
        old_manifest_without_handoff.pop("handoff_summary", None)
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(old_manifest_without_handoff, ensure_ascii=False),
            encoding="utf-8",
        )
        old_manifest_result = diagnose_run_directory(run_dir, summary_only=True)
        if old_manifest_result.get("delivery_manifest_status") != "verified":
            raise SystemExit(f"Expected old delivery manifest schema to remain compatible: {old_manifest_result}")
        if old_manifest_result.get("delivery_handoff_summary") is not None:
            raise SystemExit(f"Expected old delivery manifest schema to omit handoff summary: {old_manifest_result}")
        missing_handoff_manifest = copy.deepcopy(manifest_payload)
        missing_handoff_manifest.pop("handoff_summary", None)
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(missing_handoff_manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        missing_handoff_result = diagnose_run_directory(run_dir, summary_only=True)
        if missing_handoff_result.get("delivery_manifest_status") != "failed":
            raise SystemExit(f"Expected missing handoff summary fixture to fail: {missing_handoff_result}")
        bad_handoff_manifest = copy.deepcopy(manifest_payload)
        bad_handoff_manifest["handoff_summary"]["artifact_counts"]["outputs"] = 99
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(bad_handoff_manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        bad_handoff_result = diagnose_run_directory(run_dir, summary_only=True)
        if bad_handoff_result.get("delivery_manifest_status") != "failed":
            raise SystemExit(f"Expected mismatched handoff summary fixture to fail: {bad_handoff_result}")
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(manifest_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        plan_file.write_text(json.dumps({"name": "tampered_fixture"}, ensure_ascii=False), encoding="utf-8")
        tampered_fixed_result = diagnose_run_directory(run_dir, summary_only=True)
        if tampered_fixed_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected tampered fixed-file fixture to fail: {tampered_fixed_result}")
        fixed_missing = tampered_fixed_result.get("missing_artifacts", [])
        if not any(
            item.get("group") == "fixed_files" and item.get("status") == "sha256_mismatch"
            for item in fixed_missing
            if isinstance(item, dict)
        ):
            raise SystemExit(
                f"Expected tampered fixed-file fixture to report fixed_files sha256_mismatch: {tampered_fixed_result}"
            )
        plan_file.write_text(plan_fixture_text, encoding="utf-8")
        _write_fixture_events(
            run_dir,
            artifacts_payload,
            [
                {
                    "event": "com.call",
                    "status": "failed",
                    "details": {"parameters": {"purpose": "hole_edge_probe"}},
                },
                {"event": "plan.execution", "status": "completed", "details": {"ok": True}},
            ],
        )
        recovered_event_result = diagnose_run_directory(run_dir, summary_only=True)
        if recovered_event_result.get("ok") is not True:
            raise SystemExit(f"Expected recovered probe event fixture to pass: {recovered_event_result}")
        if recovered_event_result.get("event_log_status") != "verified":
            raise SystemExit(f"Expected recovered probe event log to verify: {recovered_event_result}")
        if recovered_event_result.get("recovered_probe_event_count") != 1:
            raise SystemExit(f"Expected recovered probe event count: {recovered_event_result}")
        export_failure_report = copy.deepcopy(report_payload)
        export_failure_report["diagnostics"]["export_result"] = {
            "status": "partial_export_failure",
            "formats": ["step", "iges"],
            "exported": ["step"],
            "failed": [
                {
                    "format": "iges",
                    "path": str(exports_dir / "fixture.iges"),
                    "error": "fixture IGES export failure",
                }
            ],
            "failed_count": 1,
        }
        (run_dir / "execution_report.json").write_text(
            json.dumps(export_failure_report, ensure_ascii=False),
            encoding="utf-8",
        )
        fixed_files = artifacts_payload.get("fixed_files")
        if isinstance(fixed_files, dict):
            fixed_files["report"] = _fixture_file_entry(run_dir / "execution_report.json", base_dir=run_dir)
        _write_fixture_events(
            run_dir,
            artifacts_payload,
            [
                {
                    "event": "outputs.export_format",
                    "status": "failed",
                    "details": {
                        "format": "iges",
                        "path": str(exports_dir / "fixture.iges"),
                        "error": "fixture IGES export failure",
                    },
                },
                {"event": "plan.execution", "status": "completed", "details": {"ok": True}},
            ],
        )
        recovered_export_event_result = diagnose_run_directory(run_dir, summary_only=True)
        if recovered_export_event_result.get("event_log_status") != "verified":
            raise SystemExit(f"Expected recovered export event log to verify: {recovered_export_event_result}")
        if recovered_export_event_result.get("recovered_export_event_count") != 1:
            raise SystemExit(f"Expected recovered export event count: {recovered_export_event_result}")
        (run_dir / "execution_report.json").write_text(
            json.dumps(report_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        if isinstance(fixed_files, dict):
            fixed_files["report"] = _fixture_file_entry(run_dir / "execution_report.json", base_dir=run_dir)
        _write_fixture_events(
            run_dir,
            artifacts_payload,
            [
                {
                    "event": "outputs.export",
                    "status": "failed",
                    "details": {"error": "fixture export warning must not be hidden"},
                },
                {"event": "plan.execution", "status": "completed", "details": {"ok": True}},
            ],
        )
        failed_event_result = diagnose_run_directory(run_dir, summary_only=True)
        if failed_event_result.get("ok") is not False:
            raise SystemExit(f"Expected hard failed event fixture to fail: {failed_event_result}")
        if failed_event_result.get("event_log_status") != "failed":
            raise SystemExit(f"Expected hard failed event log status: {failed_event_result}")
        if failed_event_result.get("failed_event_count") != 1:
            raise SystemExit(f"Expected hard failed event count: {failed_event_result}")
        _write_fixture_events(
            run_dir,
            artifacts_payload,
            [
                {"event": "fixture.ready", "status": "completed"},
                {"event": "plan.execution", "status": "completed", "details": {"ok": True}},
            ],
        )
        bad_manifest = dict(manifest_payload)
        bad_manifest["adapter"] = "unexpected"
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(bad_manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        bad_manifest_result = diagnose_run_directory(run_dir, summary_only=True)
        if bad_manifest_result.get("delivery_manifest_status") != "failed":
            raise SystemExit(f"Expected mismatched delivery manifest fixture to fail: {bad_manifest_result}")
        (run_dir / "delivery_manifest.json").unlink()
        missing_manifest_result = diagnose_run_directory(run_dir, summary_only=True)
        if missing_manifest_result.get("ok") is not False:
            raise SystemExit(f"Expected missing delivery manifest fixture to fail: {missing_manifest_result}")
        if missing_manifest_result.get("delivery_manifest_status") != "missing":
            raise SystemExit(f"Expected missing delivery manifest status: {missing_manifest_result}")
        (run_dir / "delivery_manifest.json").write_text(
            json.dumps(manifest_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        artifact.write_text("tampered-step-data", encoding="utf-8")
        tampered_result = diagnose_run_directory(run_dir, summary_only=True)
        missing = tampered_result.get("missing_artifacts", [])
        if tampered_result.get("artifact_integrity_status") != "failed":
            raise SystemExit(f"Expected tampered artifact fixture to fail: {tampered_result}")
        if not any(item.get("status") == "sha256_mismatch" for item in missing if isinstance(item, dict)):
            raise SystemExit(f"Expected tampered artifact fixture to report sha256_mismatch: {tampered_result}")
        (run_dir / "artifacts.json").unlink()
        missing_index_result = diagnose_run_directory(run_dir, summary_only=True)
        if missing_index_result.get("ok") is not False:
            raise SystemExit(f"Expected missing artifacts index fixture to fail: {missing_index_result}")
        if missing_index_result.get("artifact_integrity_status") != "missing_index":
            raise SystemExit(f"Expected missing artifacts index status: {missing_index_result}")
        return {
            "ok_status": ok_result.get("artifact_integrity_status"),
            "manifest_status": ok_result.get("delivery_manifest_status"),
            "handoff_summary_status": handoff_summary.get("delivery_status"),
            "batch_handoff_summary_status": collection_handoff.get("delivery_status"),
            "environment_status": ok_result.get("environment_status"),
            "environment_run_id_mismatch_status": environment_run_id_mismatch_result.get("environment_status"),
            "environment_adapter_mismatch_status": environment_adapter_mismatch_result.get("environment_status"),
            "environment_env_adapter_mismatch_status": environment_env_adapter_result.get("environment_status"),
            "environment_run_dir_mismatch_status": environment_run_dir_result.get("environment_status"),
            "real_missing_safety_status": real_missing_safety_result.get("environment_status"),
            "batch_ok_status": collection_ok_result.get("ok"),
            "batch_rejected_status": collection_rejected_result.get("ok"),
            "batch_truncated_status": collection_truncated_result.get("scan_status"),
            "fixed_hash_status": tampered_fixed_result.get("artifact_integrity_status"),
            "event_log_status": ok_result.get("event_log_status"),
            "artifact_run_id_mismatch_status": artifact_run_id_mismatch_result.get("artifact_integrity_status"),
            "event_run_id_mismatch_status": event_run_id_mismatch_result.get("event_log_status"),
            "missing_event_run_id_status": missing_event_run_id_result.get("event_log_status"),
            "terminal_count_mismatch_status": terminal_count_mismatch_result.get("event_log_status"),
            "terminal_missing_status": terminal_missing_result.get("event_log_status"),
            "terminal_mismatch_status": terminal_mismatch_result.get("event_log_status"),
            "recovered_event_status": recovered_event_result.get("event_log_status"),
            "recovered_export_event_status": recovered_export_event_result.get("event_log_status"),
            "failed_event_status": failed_event_result.get("event_log_status"),
            "bad_manifest_status": bad_manifest_result.get("delivery_manifest_status"),
            "missing_manifest_status": missing_manifest_result.get("delivery_manifest_status"),
            "missing_output_sha_status": missing_output_sha_result.get("artifact_integrity_status"),
            "missing_relative_path_status": missing_relative_path_result.get("artifact_integrity_status"),
            "missing_preview_sha_status": missing_preview_sha_result.get("artifact_integrity_status"),
            "missing_fixed_sha_status": missing_fixed_sha_result.get("artifact_integrity_status"),
            "manifest_missing_sha_status": manifest_missing_sha_result.get("delivery_manifest_status"),
            "manifest_relative_path_mismatch_status": manifest_bad_relative_result.get("delivery_manifest_status"),
            "handoff_relative_path_mismatch_status": handoff_bad_relative_result.get("delivery_manifest_status"),
            "old_manifest_handoff_compat_status": old_manifest_result.get("delivery_manifest_status"),
            "missing_handoff_summary_status": missing_handoff_result.get("delivery_manifest_status"),
            "bad_handoff_summary_status": bad_handoff_result.get("delivery_manifest_status"),
            "sparse_index_status": sparse_index_result.get("artifact_integrity_status"),
            "invalid_group_status": invalid_group_result.get("artifact_integrity_status"),
            "missing_fixed_entry_status": missing_fixed_entry_result.get("artifact_integrity_status"),
            "report_key_mismatch_status": report_key_mismatch_result.get("artifact_integrity_status"),
            "report_path_mismatch_status": report_path_mismatch_result.get("artifact_integrity_status"),
            "tampered_status": tampered_result.get("missing_artifacts"),
            "missing_index_status": missing_index_result.get("artifact_integrity_status"),
        }


def _check_validation_production_readiness(base_plan: dict[str, object]) -> dict[str, str]:
    """Verify validate_model_plan exposes production-readiness guidance before preflight."""

    with tempfile.TemporaryDirectory() as temp_dir:
        config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=Path(temp_dir),
            part_template=None,
            drawing_template=None,
            visible=False,
            macro_fallback_enabled=True,
            macro_execution_disabled=False,
            force_holewizard_failure=False,
            force_drawing_callout_failure=False,
            force_drawing_dimension_failure=False,
            force_cad_content_failure=False,
            force_cleanup_failure=False,
            force_material_failure=False,
            force_preflight_failure=False,
            enforce_trusted_workflow=True,
            require_direct_hole_callout=False,
            close_documents_after_run=True,
            cleanup_attach_only=True,
            debug_level="basic",
            run_id="validation_production_readiness",
        )
        executor = ModelPlanExecutor(create_adapter(config), config)
        controlled_report = executor.validate_plan(base_plan).to_dict()
        freeform_plan = _freeform_sketch_plan()
        freeform_report = executor.validate_plan(freeform_plan).to_dict()

    controlled_status = controlled_report.get("diagnostics", {}).get("production_readiness_status")
    freeform_status = freeform_report.get("diagnostics", {}).get("production_readiness_status")
    if controlled_report.get("ok") is not True or controlled_status != "trusted_workflow_ready":
        raise SystemExit(f"Expected controlled plan validation to be production-ready: {controlled_report}")
    if freeform_report.get("ok") is not True or freeform_status != "blocked_by_trusted_workflow_policy":
        raise SystemExit(f"Expected freeform plan validation to be preflight-blocked for production: {freeform_report}")
    return {
        "controlled_plan": str(controlled_status),
        "freeform_plan": str(freeform_status),
    }


def _freeform_sketch_plan() -> dict[str, object]:
    """Return a schema-valid freeform plan that must remain non-production."""

    return {
        "name": "freeform_sketch_probe",
        "units": "mm",
        "output_formats": ["sldprt", "step", "stl"],
        "drawing_profile": {"enabled": False},
        "operations": [
            {
                "id": "base_sketch",
                "op": "create_sketch",
                "parameters": {
                    "plane": "front",
                    "entities": [{"type": "circle", "center": [0, 0], "radius": 10}],
                },
            },
            {
                "id": "base_extrude",
                "op": "extrude",
                "parameters": {"sketch_id": "base_sketch", "depth": 5},
            },
        ],
    }


def _check_cleanup_policy_preflight(base_plan: dict[str, object]) -> dict[str, object]:
    """Verify disabled document cleanup blocks execution before a transaction starts."""

    with tempfile.TemporaryDirectory() as temp_dir:
        config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=Path(temp_dir),
            part_template=None,
            drawing_template=None,
            visible=False,
            macro_fallback_enabled=True,
            macro_execution_disabled=False,
            force_holewizard_failure=False,
            force_drawing_callout_failure=False,
            force_drawing_dimension_failure=False,
            force_cad_content_failure=False,
            force_cleanup_failure=False,
            force_material_failure=False,
            force_preflight_failure=False,
            enforce_trusted_workflow=True,
            require_direct_hole_callout=False,
            close_documents_after_run=False,
            cleanup_attach_only=True,
            debug_level="basic",
            run_id="cleanup_policy_preflight",
        )
        executor = ModelPlanExecutor(create_adapter(config), config)
        report = executor.execute_plan(base_plan, confirmed=True).to_dict()
        diagnostics = report.get("diagnostics", {})
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        preflight = diagnostics.get("preflight_result", {})
        preflight = preflight if isinstance(preflight, dict) else {}
        if report.get("ok") is not False:
            raise SystemExit(f"Expected disabled cleanup policy to fail preflight: {report}")
        if report.get("failure_class") != "preflight":
            raise SystemExit(f"Expected disabled cleanup policy failure_class=preflight: {report}")
        if "cleanup_policy" not in preflight.get("failures", []):
            raise SystemExit(f"Expected cleanup_policy preflight failure: {report}")
        if "cleanup_result" in diagnostics:
            raise SystemExit(f"Cleanup should not run when preflight blocks execution: {report}")
        return {
            "preflight_status": diagnostics.get("preflight_status"),
            "failure_class": report.get("failure_class"),
        }


def _check_post_run_cleanup_tool() -> dict[str, object]:
    """Verify the post-run cleanup tool is wired through executor and mock adapter."""

    with tempfile.TemporaryDirectory() as temp_dir:
        run_dir = Path(temp_dir) / "run_cleanup_fixture"
        run_dir.mkdir()
        config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=Path(temp_dir),
            part_template=None,
            drawing_template=None,
            visible=False,
            macro_fallback_enabled=True,
            macro_execution_disabled=False,
            force_holewizard_failure=False,
            force_drawing_callout_failure=False,
            force_drawing_dimension_failure=False,
            force_cad_content_failure=False,
            force_cleanup_failure=False,
            force_material_failure=False,
            force_preflight_failure=False,
            enforce_trusted_workflow=True,
            require_direct_hole_callout=False,
            close_documents_after_run=True,
            cleanup_attach_only=True,
            debug_level="basic",
            run_id="post_run_cleanup_tool",
        )
        executor = ModelPlanExecutor(create_adapter(config), config)
        result = executor.cleanup_run_documents(str(run_dir))
        forced_config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=Path(temp_dir),
            part_template=None,
            drawing_template=None,
            visible=False,
            macro_fallback_enabled=True,
            macro_execution_disabled=False,
            force_holewizard_failure=False,
            force_drawing_callout_failure=False,
            force_drawing_dimension_failure=False,
            force_cad_content_failure=False,
            force_cleanup_failure=True,
            force_material_failure=False,
            force_preflight_failure=False,
            enforce_trusted_workflow=True,
            require_direct_hole_callout=False,
            close_documents_after_run=True,
            cleanup_attach_only=True,
            debug_level="basic",
            run_id="post_run_cleanup_tool_forced",
        )
        forced_executor = ModelPlanExecutor(create_adapter(forced_config), forced_config)
        forced_result = forced_executor.cleanup_run_documents(str(run_dir))

    if result.get("status") != "skipped_no_documents":
        raise SystemExit(f"Expected mock post-run cleanup to skip with no documents: {result}")
    if result.get("cleanup_verification_status") != "not_applicable":
        raise SystemExit(f"Expected mock post-run cleanup verification not_applicable: {result}")
    if result.get("closed_documents") != [] or result.get("candidate_documents") != []:
        raise SystemExit(f"Expected mock post-run cleanup to report empty document lists: {result}")
    if forced_result.get("status") != "forced_failure":
        raise SystemExit(f"Expected forced mock post-run cleanup failure: {forced_result}")
    if forced_result.get("cleanup_verification_status") != "failed":
        raise SystemExit(f"Expected forced mock post-run cleanup verification failed: {forced_result}")
    return {
        "status": result.get("status"),
        "cleanup_verification_status": result.get("cleanup_verification_status"),
        "forced_status": forced_result.get("status"),
        "forced_cleanup_verification_status": forced_result.get("cleanup_verification_status"),
    }


def _check_post_run_cleanup_attach_only() -> dict[str, object]:
    """Verify real post-run cleanup does not launch SolidWorks by default."""

    with tempfile.TemporaryDirectory() as temp_dir:
        run_dir = Path(temp_dir) / "run_attach_only_fixture"
        exports_dir = run_dir / "exports"
        exports_dir.mkdir(parents=True)
        part_file = exports_dir / "fixture.SLDPRT"
        part_file.write_bytes(b"fixture-native-placeholder")
        (run_dir / "execution_report.json").write_text(
            json.dumps({"output_files": {"sldprt": str(part_file)}}, ensure_ascii=False),
            encoding="utf-8",
        )
        config = SolidWorksMCPConfig(
            adapter="solidworks",
            output_root=Path(temp_dir),
            part_template=None,
            drawing_template=None,
            visible=False,
            macro_fallback_enabled=True,
            macro_execution_disabled=False,
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
            run_id="post_run_cleanup_attach_only",
        )
        adapter = SolidWorksCOMAdapter(config)
        connect_called = {"value": False}

        def fail_if_connect_called() -> dict[str, object]:
            connect_called["value"] = True
            raise RuntimeError("connect should not be called during attach-only cleanup")

        def no_running_session() -> dict[str, object]:
            raise RuntimeError("fixture no running SolidWorks session")

        adapter.connect = fail_if_connect_called  # type: ignore[method-assign]
        adapter._attach_existing_solidworks = no_running_session  # type: ignore[method-assign]
        result = adapter.cleanup_run_documents(str(run_dir))

    if connect_called["value"]:
        raise SystemExit(f"Attach-only post-run cleanup unexpectedly called connect(): {result}")
    if result.get("status") != "failed":
        raise SystemExit(f"Expected attach-only cleanup without running SolidWorks to fail clearly: {result}")
    if result.get("failure_reason") != "solidworks_not_running_attach_only":
        raise SystemExit(f"Expected stable attach-only failure_reason: {result}")
    if result.get("attach_only") is not True:
        raise SystemExit(f"Expected attach_only=true in cleanup result: {result}")
    if not result.get("candidate_documents"):
        raise SystemExit(f"Expected fixture native document candidate: {result}")
    return {
        "status": result.get("status"),
        "failure_reason": result.get("failure_reason"),
    }


def _check_direct_callout_policy_preflight(base_plan: dict[str, object]) -> dict[str, object]:
    """Verify real SolidWorks execution requires direct hole-callout enforcement."""

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        part_template = temp_path / "fixture.prtdot"
        drawing_template = temp_path / "fixture.drwdot"
        part_template.write_text("fixture", encoding="utf-8")
        drawing_template.write_text("fixture", encoding="utf-8")
        config = SolidWorksMCPConfig(
            adapter="solidworks",
            output_root=temp_path / "outputs",
            part_template=str(part_template),
            drawing_template=str(drawing_template),
            visible=False,
            macro_fallback_enabled=True,
            macro_execution_disabled=False,
            force_holewizard_failure=False,
            force_drawing_callout_failure=False,
            force_drawing_dimension_failure=False,
            force_cad_content_failure=False,
            force_cleanup_failure=False,
            force_material_failure=False,
            force_preflight_failure=False,
            enforce_trusted_workflow=True,
            require_direct_hole_callout=False,
            close_documents_after_run=True,
            cleanup_attach_only=True,
            debug_level="basic",
            run_id="direct_callout_policy_preflight",
        )
        adapter = SolidWorksCOMAdapter(config)
        adapter.connect = lambda: {  # type: ignore[method-assign]
            "adapter": adapter.name,
            "connected": True,
            "revision": "fixture",
            "visible": False,
        }
        executor = ModelPlanExecutor(adapter, config)
        report = executor.execute_plan(base_plan, confirmed=True).to_dict()
        diagnostics = report.get("diagnostics", {})
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        preflight = diagnostics.get("preflight_result", {})
        preflight = preflight if isinstance(preflight, dict) else {}
        if report.get("ok") is not False:
            raise SystemExit(f"Expected disabled direct callout policy to fail preflight: {report}")
        if report.get("failure_class") != "preflight":
            raise SystemExit(f"Expected direct callout policy failure_class=preflight: {report}")
        if "direct_hole_callout_policy" not in preflight.get("failures", []):
            raise SystemExit(f"Expected direct_hole_callout_policy preflight failure: {report}")
        if "cleanup_result" in diagnostics:
            raise SystemExit(f"Cleanup should not run when direct callout preflight blocks execution: {report}")
        return {
            "preflight_status": diagnostics.get("preflight_status"),
            "failure_class": report.get("failure_class"),
        }


def _check_trusted_workflow_policy_preflight(base_plan: dict[str, object]) -> dict[str, object]:
    """Verify untrusted workflows are blocked before a transaction starts."""

    with tempfile.TemporaryDirectory() as temp_dir:
        plan = copy.deepcopy(base_plan)
        plan["operations"] = [
            {
                "id": "untrusted_freeform_sketch",
                "op": "create_sketch",
                "description": "Schema-valid freeform operation outside trusted production workflow.",
                "parameters": {
                    "plane": "front",
                    "entities": [{"type": "circle", "center": [0, 0], "radius": 10}],
                },
            },
            *plan["operations"],
        ]
        config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=Path(temp_dir),
            part_template=None,
            drawing_template=None,
            visible=False,
            macro_fallback_enabled=True,
            macro_execution_disabled=False,
            force_holewizard_failure=False,
            force_drawing_callout_failure=False,
            force_drawing_dimension_failure=False,
            force_cad_content_failure=False,
            force_cleanup_failure=False,
            force_material_failure=False,
            force_preflight_failure=False,
            enforce_trusted_workflow=True,
            require_direct_hole_callout=False,
            close_documents_after_run=True,
            cleanup_attach_only=True,
            debug_level="basic",
            run_id="trusted_workflow_policy_preflight",
        )
        executor = ModelPlanExecutor(create_adapter(config), config)
        report = executor.execute_plan(plan, confirmed=True).to_dict()
        diagnostics = report.get("diagnostics", {})
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        preflight = diagnostics.get("preflight_result", {})
        preflight = preflight if isinstance(preflight, dict) else {}
        if report.get("ok") is not False:
            raise SystemExit(f"Expected untrusted workflow policy to fail preflight: {report}")
        if report.get("failure_class") != "preflight":
            raise SystemExit(f"Expected trusted workflow policy failure_class=preflight: {report}")
        if "trusted_workflow_policy" not in preflight.get("failures", []):
            raise SystemExit(f"Expected trusted_workflow_policy preflight failure: {report}")
        checks = preflight.get("checks", [])
        if not isinstance(checks, list) or len(checks) != 1:
            raise SystemExit(f"Trusted workflow preflight should stop before adapter checks: {report}")
        if "cleanup_result" in diagnostics:
            raise SystemExit(f"Cleanup should not run when trusted workflow preflight blocks execution: {report}")
        diagnosis = diagnose_run_directory(Path(str(report["run_dir"])), summary_only=True)
        if diagnosis.get("event_log_status") != "verified":
            raise SystemExit(f"Expected preflight-blocked event log to verify: {diagnosis}")
        if diagnosis.get("recovered_preflight_event_count") != 2:
            raise SystemExit(f"Expected recovered preflight event count: {diagnosis}")
        return {
            "preflight_status": diagnostics.get("preflight_status"),
            "failure_class": report.get("failure_class"),
            "event_log_status": diagnosis.get("event_log_status"),
            "recovered_preflight_event_count": diagnosis.get("recovered_preflight_event_count"),
        }


def _fixture_handoff_summary(
    manifest_payload: dict[str, object],
    report_payload: dict[str, object],
) -> dict[str, object]:
    """Return a delivery handoff summary fixture matching the manifest contract."""

    verdict = manifest_payload.get("production_verdict")
    verdict = verdict if isinstance(verdict, dict) else {}
    summary = verdict.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    output_files = manifest_payload.get("output_files")
    output_files = output_files if isinstance(output_files, dict) else {}
    preview_files = manifest_payload.get("preview_files")
    preview_files = preview_files if isinstance(preview_files, dict) else {}
    return {
        "delivery_status": verdict.get("status"),
        "delivery_ok": verdict.get("ok"),
        "production_failures": verdict.get("failures", []),
        "repair_actions": verdict.get("repair_actions", []),
        "run_id": manifest_payload.get("run_id"),
        "plan_name": manifest_payload.get("plan_name"),
        "adapter": manifest_payload.get("adapter"),
        "key_statuses": {
            "trusted_workflow_status": summary.get("trusted_workflow_status"),
            "thread_model_status": summary.get("thread_model_status"),
            "corner_radius_status": summary.get("corner_radius_status"),
            "drawing_view_status": summary.get("drawing_view_status"),
            "drawing_annotation_status": summary.get("drawing_annotation_status"),
            "dimension_layout_status": summary.get("dimension_layout_status"),
            "model_geometry_status": summary.get("model_geometry_status"),
            "mass_property_status": summary.get("mass_property_status"),
            "artifact_validation_status": summary.get("artifact_validation_status"),
            "artifact_content_status": summary.get("artifact_content_status"),
            "cleanup_status": summary.get("cleanup_status"),
            "cleanup_verification_status": summary.get("cleanup_verification_status"),
            "document_state_audit_status": summary.get("document_state_audit_status"),
            "document_state_after_cleanup_run_created_open_count": summary.get(
                "document_state_after_cleanup_run_created_open_count"
            ),
        },
        "artifact_counts": {
            "outputs": len(output_files),
            "previews": len(preview_files),
        },
        "outputs": _fixture_manifest_file_list(output_files),
        "previews": _fixture_manifest_file_list(preview_files),
        "diagnose_command": manifest_payload.get("diagnose_command"),
        "repro_command": report_payload.get("repro_command"),
    }


def _write_release_gate_directory_fixture(root: Path, scenarios: tuple[str, ...]) -> Path:
    """Write a full accepted release-gate fixture and return its report path."""

    root.mkdir(parents=True, exist_ok=True)
    scenario_results = []
    for scenario in scenarios:
        run_dir = root / f"release_fixture_{scenario}"
        plan_name = _fixture_release_plan_name(scenario)
        workflow = _fixture_release_workflow(scenario)
        diagnostics = _accepted_diagnostics(
            _fixture_release_dimension_ids(scenario),
            "trusted_dimensions_created",
            require_direct_hole_callout=True,
        )
        if _is_fixture_assembly_scenario(scenario):
            diagnostics = _accepted_bom_assembly_diagnostics()
        if workflow == "shaft":
            diagnostics["drawing_annotation_status"] = "not_requested"
            diagnostics["drawing_annotation_result"] = {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_shaft_has_no_holes",
            }
        if workflow == "sheet_metal_base_flange":
            diagnostics["drawing_annotation_status"] = "not_requested"
            diagnostics["drawing_annotation_result"] = {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_sheet_metal_base_flange_has_no_holes",
            }
            diagnostics["sheet_metal_status"] = "sheet_metal_verified"
            diagnostics["sheet_metal_result"] = {
                "status": "sheet_metal_verified",
                "base_flange_created": True,
                "feature_name": "Base-Flange1",
                "flat_pattern_result": {
                    "status": "flat_pattern_exported",
                    "ok": True,
                    "format": "dxf",
                    "path": "mock.dxf",
                },
            }
        if workflow == "weldment_frame":
            diagnostics["drawing_annotation_status"] = "not_requested"
            diagnostics["drawing_annotation_result"] = {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_weldment_frame_has_no_holes",
            }
            diagnostics["weldment_status"] = "weldment_verified"
            diagnostics["weldment_result"] = {
                "status": "weldment_verified",
                "structural_member_created": True,
                "feature_type": "WeldMemberFeat",
                "body_count": 4,
            }
            diagnostics["cut_list_status"] = "cut_list_verified"
            diagnostics["cut_list_result"] = {
                "status": "cut_list_verified",
                "row_count": 2,
                "columns": ["item", "member_id", "description", "quantity", "length_mm", "profile", "material"],
            }
        if workflow == "static_simulation":
            diagnostics["drawing_annotation_status"] = "not_requested"
            diagnostics["drawing_annotation_result"] = {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_static_simulation_has_no_holes",
            }
            diagnostics["simulation_status"] = "simulation_verified"
            diagnostics["simulation_result"] = {
                "status": "simulation_verified",
                "study_type": "static",
                "solver": "fixture_static_solver",
                "row_count": 3,
                "checks": {
                    "von_mises_within_limit": True,
                    "factor_of_safety_within_limit": True,
                    "displacement_within_limit": True,
                },
                "max_von_mises_mpa": 140.625,
                "min_factor_of_safety": 1.777778,
                "max_displacement_mm": 0.84375,
                "columns": ["metric", "value", "unit", "status", "limit"],
            }
        if _is_fixture_atomic_no_hole_scenario(scenario):
            diagnostics["drawing_annotation_status"] = "not_requested"
            diagnostics["drawing_annotation_result"] = {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_atomic_model_has_no_hole_operation",
            }
        diagnostics["thread_model_status"] = "holewizard_threaded_hole" if workflow == "mounting_plate" else "not_requested"
        diagnostics["corner_radius_status"] = "fillet_feature" if workflow == "mounting_plate" else "not_requested"
        dimension_count = len(_fixture_release_dimension_ids(scenario))
        output_file_ids = (
            ["csv", "dwg", "pdf", "sldasm", "slddrw", "step"]
            if _is_fixture_assembly_scenario(scenario)
            else ["dwg", "dxf", "pdf", "slddrw", "sldprt", "step", "stl"]
            if _is_fixture_sheet_metal_scenario(scenario)
            else ["csv", "dwg", "pdf", "slddrw", "sldprt", "step", "stl"]
            if _is_fixture_weldment_scenario(scenario)
            else ["csv", "dwg", "pdf", "slddrw", "sldprt", "step", "stl"]
            if _is_fixture_simulation_scenario(scenario)
            else ["dwg", "pdf", "slddrw", "sldprt", "step", "stl"]
        )
        no_hole_workflows = {"shaft", "bom_assembly", "sheet_metal_base_flange", "weldment_frame", "static_simulation"}
        no_hole_scenario = workflow in no_hole_workflows or _is_fixture_atomic_no_hole_scenario(scenario)
        acceptance_summary = {
            "thread_model_status": "holewizard_threaded_hole" if workflow == "mounting_plate" else "not_requested",
            "trusted_workflow_status": f"controlled_{workflow}",
            "trusted_workflow": {"ok": True, "workflow": workflow},
            "hole_count": 4 if workflow == "mounting_plate" else 0,
            "corner_radius_status": "fillet_feature" if workflow == "mounting_plate" else "not_requested",
            "drawing_view_status": "created",
            "drawing_annotation_status": "not_requested"
            if no_hole_scenario
            else "hole_callout_created",
            "callout_count": 0 if no_hole_scenario else 1,
            "callout_creation_method": None
            if no_hole_scenario
            else "add_hole_callout2",
            "direct_hole_callout_created": None
            if no_hole_scenario
            else True,
            "drawing_dimension_status": "not_requested" if workflow == "bom_assembly" else "basic_dimensions_created",
            "dimension_count": dimension_count,
            "dimension_layout_status": "not_requested" if workflow == "bom_assembly" else "trusted_dimensions_created",
            "proxy_dimensions": [],
            "non_radial_radius_dimensions": [],
            "missing_dimensions": [],
            "material_status": "not_requested",
            "custom_property_status": "not_requested",
            "model_geometry_status": "not_requested" if workflow == "bom_assembly" else "geometry_verified",
            "model_geometry_body_count": 0 if workflow == "bom_assembly" else 1,
            "mass_property_status": "not_requested" if workflow == "bom_assembly" else "mass_properties_verified",
            "mass_kg": None if workflow == "bom_assembly" else 0.821,
            "volume_m3": None if workflow == "bom_assembly" else 0.0001046,
            "sheet_metal_status": "sheet_metal_verified" if workflow == "sheet_metal_base_flange" else "not_requested",
            "flat_pattern_status": "flat_pattern_exported"
            if workflow == "sheet_metal_base_flange"
            else "not_requested",
            "flat_pattern_dxf_path": "mock.dxf" if workflow == "sheet_metal_base_flange" else None,
            "weldment_status": "weldment_verified" if workflow == "weldment_frame" else "not_requested",
            "structural_member_created": True if workflow == "weldment_frame" else None,
            "weldment_feature_type": "WeldMemberFeat" if workflow == "weldment_frame" else None,
            "weldment_body_count": 4 if workflow == "weldment_frame" else 0,
            "cut_list_status": "cut_list_verified" if workflow == "weldment_frame" else "not_requested",
            "cut_list_row_count": 2 if workflow == "weldment_frame" else 0,
            "cut_list_columns": ["item", "member_id", "description", "quantity", "length_mm", "profile", "material"]
            if workflow == "weldment_frame"
            else [],
            "simulation_status": "simulation_verified" if workflow == "static_simulation" else "not_requested",
            "simulation_study_type": "static" if workflow == "static_simulation" else None,
            "simulation_solver": "fixture_static_solver" if workflow == "static_simulation" else None,
            "simulation_report_row_count": 3 if workflow == "static_simulation" else 0,
            "simulation_max_von_mises_mpa": 140.625 if workflow == "static_simulation" else None,
            "simulation_min_factor_of_safety": 1.777778 if workflow == "static_simulation" else None,
            "simulation_max_displacement_mm": 0.84375 if workflow == "static_simulation" else None,
            "assembly_status": "assembly_verified" if workflow == "bom_assembly" else "not_requested",
            "component_instance_count": 3 if workflow == "bom_assembly" else 0,
            "component_definitions": ["plate_a", "spacer_a"] if workflow == "bom_assembly" else [],
            "bom_status": "bom_verified" if workflow == "bom_assembly" else "not_requested",
            "bom_row_count": 2 if workflow == "bom_assembly" else 0,
            "bom_columns": ["item", "component_id", "part_number", "description", "quantity", "material"]
            if workflow == "bom_assembly"
            else [],
            "artifact_validation_status": "artifacts_ready",
            "artifact_content_status": "content_ready",
            "cad_content_status": "cad_artifacts_verified",
            "pdf_semantic_content_status": "pdf_semantic_content_verified",
            "cleanup_status": "completed",
            "cleanup_verification_status": "verified",
            "document_state_audit_status": "verified_no_run_documents_open",
            "document_state_after_cleanup_run_created_open_count": 0,
            "output_files": output_file_ids,
            "preview_files": ["front", "isometric", "right", "top"],
        }
        acceptance = {
            "status": "accepted",
            "ok": True,
            "checks": {gate_id: True for gate_id in _fixture_release_gate_ids(scenario)},
            "failures": [],
            "repair_actions": [],
            "summary": acceptance_summary,
        }
        diagnostics["production_acceptance_result"] = acceptance
        output_files, preview_files, artifacts_payload = _write_release_artifacts(run_dir, scenario)
        plan_file = run_dir / "plan.normalized.json"
        plan_file.write_text(
            json.dumps({"name": plan_name, "scenario": scenario}, ensure_ascii=False),
            encoding="utf-8",
        )
        artifacts_payload["fixed_files"]["plan"] = _fixture_file_entry(plan_file, base_dir=run_dir)
        report_payload = {
            "ok": True,
            "adapter": "mock",
            "run_id": f"release_fixture_{scenario}",
            "run_dir": str(run_dir),
            "plan_name": plan_name,
            "message": "release fixture",
            "production_verdict": acceptance,
            "report_file": str(run_dir / "execution_report.json"),
            "artifacts_file": str(run_dir / "artifacts.json"),
            "delivery_manifest_file": str(run_dir / "delivery_manifest.json"),
            "events_file": str(run_dir / "events.jsonl"),
            "environment_file": str(run_dir / "environment.json"),
            "output_files": output_files,
            "preview_files": preview_files,
            "diagnostics": diagnostics,
            "repro_command": f"python scripts/smoke_mounting_plate.py --scenario {scenario}",
        }
        manifest_payload = {
            "schema_version": "2026-06-06.2",
            "run_id": f"release_fixture_{scenario}",
            "run_dir": str(run_dir),
            "plan_name": plan_name,
            "adapter": "mock",
            "ok": True,
            "production_verdict": acceptance,
            "report_file": str(run_dir / "execution_report.json"),
            "artifacts_file": str(run_dir / "artifacts.json"),
            "delivery_manifest_file": str(run_dir / "delivery_manifest.json"),
            "events_file": str(run_dir / "events.jsonl"),
            "environment_file": str(run_dir / "environment.json"),
            "output_files": artifacts_payload["output_files"],
            "preview_files": artifacts_payload["preview_files"],
            "diagnose_command": f"python scripts/diagnose_run.py {run_dir} --summary-only",
        }
        manifest_payload["handoff_summary"] = _fixture_handoff_summary(manifest_payload, report_payload)
        environment_payload = _fixture_environment_payload(
            run_dir,
            adapter="mock",
            close_documents_after_run=True,
            require_direct_hole_callout=True,
            enforce_trusted_workflow=True,
        )
        environment_payload["run_id"] = f"release_fixture_{scenario}"
        (run_dir / "events.jsonl").write_text(
            json.dumps({"event": "fixture.ready", "status": "completed", "run_id": f"release_fixture_{scenario}"}, ensure_ascii=False)
            + "\n"
            + json.dumps(
                {
                    "event": "plan.execution",
                    "status": "completed",
                    "run_id": f"release_fixture_{scenario}",
                    "details": {"ok": True, "output_count": len(output_files), "preview_count": len(preview_files)},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        _write_fixture_report_manifest_environment(
            run_dir,
            artifacts_payload,
            report_payload,
            manifest_payload,
            environment_payload,
        )
        artifacts_payload["fixed_files"]["artifacts"] = _fixture_file_entry(
            run_dir / "artifacts.json",
            include_hash=False,
            base_dir=run_dir,
        )
        artifacts_payload["fixed_files"]["events"] = _fixture_file_entry(run_dir / "events.jsonl", base_dir=run_dir)
        (run_dir / "artifacts.json").write_text(json.dumps(artifacts_payload, ensure_ascii=False), encoding="utf-8")
        scenario_results.append(
            {
                "scenario": scenario,
                "ok": True,
                "validation_ok": True,
                "execution_ok": True,
                "run_id": f"release_fixture_{scenario}",
                "run_dir": str(run_dir),
                "report_file": str(run_dir / "execution_report.json"),
                "delivery_manifest_file": str(run_dir / "delivery_manifest.json"),
                "offline_diagnosis": diagnose_run_directory(run_dir, summary_only=True),
                "acceptance": acceptance,
                "summary": acceptance_summary,
                "diagnostics": diagnostics,
            }
        )

    batch = diagnose_run_collection(root, summary_only=True, max_runs=0)
    payload = _release_gate_payload(
        adapter="mock",
        output_root=root,
        scenario_names=list(scenarios),
        smoke_results=[{"results": scenario_results}],
        batch_diagnosis=batch,
    )
    report_file = root / "release_gate_report.json"
    report_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return report_file


def _write_release_artifacts(
    run_dir: Path,
    scenario: str,
) -> tuple[dict[str, str], dict[str, str], dict[str, object]]:
    """Write complete output and preview fixtures for one release run."""

    exports_dir = run_dir / "exports"
    previews_dir = run_dir / "previews"
    exports_dir.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)
    output_files: dict[str, str] = {}
    preview_files: dict[str, str] = {}
    artifacts_payload: dict[str, object] = {
        "schema_version": "2026-06-06.2",
        "run_id": f"release_fixture_{scenario}",
        "run_dir": str(run_dir),
        "fixed_files": {},
        "output_files": {},
        "preview_files": {},
        "directories": {
            "exports": _fixture_file_entry(exports_dir, base_dir=run_dir),
            "previews": _fixture_file_entry(previews_dir, base_dir=run_dir),
        },
    }
    output_group = artifacts_payload["output_files"]
    preview_group = artifacts_payload["preview_files"]
    assert isinstance(output_group, dict)
    assert isinstance(preview_group, dict)
    artifact_ids = (
        ("sldasm", "step", "slddrw", "pdf", "dwg", "csv")
        if _is_fixture_assembly_scenario(scenario)
        else ("sldprt", "step", "stl", "slddrw", "pdf", "dwg", "dxf")
        if _is_fixture_sheet_metal_scenario(scenario)
        else ("sldprt", "step", "stl", "slddrw", "pdf", "dwg", "csv")
        if _is_fixture_weldment_scenario(scenario)
        else ("sldprt", "step", "stl", "slddrw", "pdf", "dwg", "csv")
        if _is_fixture_simulation_scenario(scenario)
        else ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")
    )
    for artifact_id in artifact_ids:
        path = exports_dir / f"{scenario}.{artifact_id}"
        path.write_text(f"release fixture {scenario} {artifact_id}\n", encoding="utf-8")
        output_files[artifact_id] = str(path)
        output_group[artifact_id] = _fixture_file_entry(path, base_dir=run_dir)
    for preview_id in ("front", "top", "right", "isometric"):
        path = previews_dir / f"{scenario}_{preview_id}.png"
        path.write_text(f"release fixture {scenario} {preview_id}\n", encoding="utf-8")
        preview_files[preview_id] = str(path)
        preview_group[preview_id] = _fixture_file_entry(path, base_dir=run_dir)
    return output_files, preview_files, artifacts_payload


def _fixture_release_plan_name(scenario: str) -> str:
    """Return the deterministic plan name used by release gate scenarios."""

    mapping = {
        "baseline": "m6_mounting_plate_prod_baseline",
        "material_alias": "m6_mounting_plate_prod_material_alias",
        "custom_properties": "m6_mounting_plate_prod_custom_properties",
        "combined": "m6_mounting_plate_prod_combined",
        "drawing_exchange": "m6_mounting_plate_prod_drawing_exchange",
        "neutral_exports": "m6_mounting_plate_prod_neutral_exports",
        "wide_combined": "m6_mounting_plate_prod_wide_combined",
        "flange_baseline": "center_hole_flange_prod_baseline",
        "center_hole_plate_baseline": "center_hole_plate_prod_baseline",
        "bracket_baseline": "bracket_prod_baseline",
        "end_cap_baseline": "end_cap_prod_baseline",
        "mounting_block_baseline": "mounting_block_prod_baseline",
        "shaft_baseline": "shaft_prod_baseline",
        "sheet_metal_base_flange_baseline": "sheet_metal_base_flange_prod_baseline",
        "weldment_frame_baseline": "weldment_frame_prod_baseline",
        "simulation_cantilever_baseline": "simulation_cantilever_prod_baseline",
        "washer_baseline": "washer_prod_baseline",
        "sleeve_baseline": "sleeve_prod_baseline",
        "slotted_array_plate_baseline": "slotted_array_plate_prod_baseline",
        "bom_assembly_baseline": "bom_assembly_prod_baseline",
        "atomic_baseline": "atomic_model_prod_baseline",
        "atomic_cut_baseline": "atomic_model_prod_cut_baseline",
        "atomic_pattern_baseline": "atomic_model_prod_pattern_baseline",
        "atomic_revolve_baseline": "atomic_model_prod_revolve_baseline",
        "atomic_sweep_baseline": "atomic_model_prod_sweep_baseline",
        "atomic_loft_baseline": "atomic_model_prod_loft_baseline",
    }
    return mapping[scenario]


def _fixture_release_workflow(scenario: str) -> str:
    """Return the trusted workflow id for a release fixture scenario."""

    if scenario == "flange_baseline":
        return "center_hole_flange"
    if scenario == "center_hole_plate_baseline":
        return "center_hole_plate"
    if scenario == "bracket_baseline":
        return "bracket"
    if scenario == "end_cap_baseline":
        return "end_cap"
    if scenario == "mounting_block_baseline":
        return "mounting_block"
    if scenario == "shaft_baseline":
        return "shaft"
    if scenario == "sheet_metal_base_flange_baseline":
        return "sheet_metal_base_flange"
    if scenario == "weldment_frame_baseline":
        return "weldment_frame"
    if scenario == "simulation_cantilever_baseline":
        return "static_simulation"
    if scenario == "washer_baseline":
        return "washer"
    if scenario == "sleeve_baseline":
        return "sleeve"
    if scenario == "slotted_array_plate_baseline":
        return "slotted_array_plate"
    if scenario == "bom_assembly_baseline":
        return "bom_assembly"
    if scenario in {
        "atomic_baseline",
        "atomic_cut_baseline",
        "atomic_pattern_baseline",
        "atomic_revolve_baseline",
        "atomic_sweep_baseline",
        "atomic_loft_baseline",
    }:
        return "atomic_model"
    return "mounting_plate"


def _fixture_release_dimension_ids(scenario: str) -> list[str]:
    """Return trusted drawing dimension ids for one release fixture scenario."""

    if scenario == "flange_baseline":
        return ["outer_diameter_100", "hole_diameter_24", "thickness_12"]
    if scenario == "center_hole_plate_baseline":
        return ["length_100", "width_60", "thickness_12", "hole_diameter_24"]
    if scenario == "bracket_baseline":
        return [
            "base_length_80",
            "base_width_50",
            "base_thickness_12",
            "upright_height_70",
            "upright_thickness_12",
            "hole_diameter_6",
        ]
    if scenario == "end_cap_baseline":
        return ["outer_diameter_100", "center_hole_diameter_20", "bolt_hole_diameter_8", "thickness_10"]
    if scenario == "mounting_block_baseline":
        return ["length_80", "width_50", "height_30", "hole_diameter_18"]
    if scenario == "shaft_baseline":
        return ["diameter_25", "length_100"]
    if scenario == "sheet_metal_base_flange_baseline":
        return ["length_120", "width_80", "thickness_2"]
    if scenario == "weldment_frame_baseline":
        return ["overall_length_300", "overall_width_220", "profile_size_50p8"]
    if scenario == "simulation_cantilever_baseline":
        return ["beam_length_120", "beam_width_20", "beam_height_8"]
    if scenario == "washer_baseline":
        return ["outer_diameter_30", "inner_diameter_10", "thickness_3"]
    if scenario == "sleeve_baseline":
        return ["outer_diameter_40", "inner_diameter_20", "length_60"]
    if scenario == "slotted_array_plate_baseline":
        return [
            "length_120",
            "width_80",
            "thickness_10",
            "slot_length_50",
            "slot_width_14",
            "hole_diameter_8",
            "hole_spacing_x_90",
            "hole_spacing_y_50",
        ]
    if scenario in {"atomic_baseline", "atomic_cut_baseline"}:
        return ["dim_width"]
    if scenario == "atomic_pattern_baseline":
        return ["dim_pattern_width"]
    if scenario == "atomic_revolve_baseline":
        return ["dim_revolve_outer_diameter"]
    if scenario == "atomic_sweep_baseline":
        return ["dim_sweep_profile_diameter"]
    if scenario == "atomic_loft_baseline":
        return ["dim_loft_primary_diameter"]
    if scenario == "bom_assembly_baseline":
        return []
    if scenario == "wide_combined":
        return ["length_140", "width_90", "thickness_12", "corner_radius_r6", "hole_edge_offset_18"]
    return ["length_120", "width_80", "thickness_10", "corner_radius_r5", "hole_edge_offset_15"]


def _fixture_release_gate_ids(scenario: str | None = None) -> tuple[str, ...]:
    """Return the production gate ids expected in accepted fixtures."""

    if scenario is not None and _is_fixture_assembly_scenario(scenario):
        return (
            "execution_ok",
            "trusted_controlled_workflow",
            "preflight_ready",
            "assembly_structure_verified",
            "bom_verified",
            "drawing_standard_views_created",
            "custom_properties_verified",
            "artifacts_ready",
            "artifact_content_ready",
            "cad_artifact_content",
            "drawing_pdf_semantic_content",
            "required_output_files",
            "requested_output_files",
            "required_preview_files",
            "cleanup_completed",
            "cleanup_verified",
            "document_state_audit_verified",
        )
    if scenario is not None and _is_fixture_sheet_metal_scenario(scenario):
        return (
            "execution_ok",
            "trusted_controlled_workflow",
            "preflight_ready",
            "sheet_metal_feature_verified",
            "flat_pattern_exported",
            "drawing_standard_views_created",
            "basic_dimensions_created",
            "trusted_basic_dimensions",
            "material_verified",
            "custom_properties_verified",
            "model_geometry_verified",
            "mass_properties_verified",
            "artifacts_ready",
            "artifact_content_ready",
            "cad_artifact_content",
            "drawing_pdf_semantic_content",
            "required_output_files",
            "requested_output_files",
            "required_preview_files",
            "cleanup_completed",
            "cleanup_verified",
            "document_state_audit_verified",
        )
    if scenario is not None and _is_fixture_weldment_scenario(scenario):
        return (
            "execution_ok",
            "trusted_controlled_workflow",
            "preflight_ready",
            "weldment_feature_verified",
            "cut_list_verified",
            "drawing_standard_views_created",
            "basic_dimensions_created",
            "trusted_basic_dimensions",
            "material_verified",
            "custom_properties_verified",
            "model_geometry_verified",
            "mass_properties_verified",
            "artifacts_ready",
            "artifact_content_ready",
            "cad_artifact_content",
            "drawing_pdf_semantic_content",
            "required_output_files",
            "requested_output_files",
            "required_preview_files",
            "cleanup_completed",
            "cleanup_verified",
            "document_state_audit_verified",
        )
    if scenario is not None and _is_fixture_simulation_scenario(scenario):
        return (
            "execution_ok",
            "trusted_controlled_workflow",
            "preflight_ready",
            "simulation_study_verified",
            "simulation_results_within_limits",
            "simulation_report_verified",
            "drawing_standard_views_created",
            "basic_dimensions_created",
            "trusted_basic_dimensions",
            "material_verified",
            "custom_properties_verified",
            "model_geometry_verified",
            "mass_properties_verified",
            "artifacts_ready",
            "artifact_content_ready",
            "cad_artifact_content",
            "drawing_pdf_semantic_content",
            "required_output_files",
            "requested_output_files",
            "required_preview_files",
            "cleanup_completed",
            "cleanup_verified",
            "document_state_audit_verified",
        )
    return (
        "execution_ok",
        "trusted_controlled_workflow",
        "preflight_ready",
        "trusted_thread_model",
        "corner_radius_feature",
        "drawing_standard_views_created",
        "hole_callouts_created",
        "direct_hole_callouts_created",
        "basic_dimensions_created",
        "trusted_basic_dimensions",
        "material_verified",
        "custom_properties_verified",
        "model_geometry_verified",
        "mass_properties_verified",
        "artifacts_ready",
        "artifact_content_ready",
        "cad_artifact_content",
        "drawing_pdf_semantic_content",
        "required_output_files",
        "requested_output_files",
        "required_preview_files",
        "cleanup_completed",
        "cleanup_verified",
        "document_state_audit_verified",
    )


def _is_fixture_assembly_scenario(scenario: str) -> bool:
    """Return whether a release fixture scenario is the controlled assembly+BOM baseline."""

    return scenario == "bom_assembly_baseline"


def _is_fixture_sheet_metal_scenario(scenario: str) -> bool:
    """Return whether a release fixture scenario is the controlled sheet-metal baseline."""

    return scenario == "sheet_metal_base_flange_baseline"


def _is_fixture_weldment_scenario(scenario: str) -> bool:
    """Return whether a release fixture scenario is the controlled weldment baseline."""

    return scenario == "weldment_frame_baseline"


def _is_fixture_simulation_scenario(scenario: str) -> bool:
    """Return whether a release fixture scenario is the controlled simulation baseline."""

    return scenario == "simulation_cantilever_baseline"


def _is_fixture_atomic_no_hole_scenario(scenario: str) -> bool:
    """Return whether an atomic release fixture intentionally has no hole callout gate."""

    return scenario in {
        "atomic_revolve_baseline",
        "atomic_sweep_baseline",
        "atomic_loft_baseline",
    }


def _fixture_drawing_view_result() -> dict[str, object]:
    """Return a minimal current-standard drawing view diagnostic fixture."""

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
        "required_roles": [str(view["role"]) for view in views],
        "missing_roles": [],
        "errors": [],
    }


def _fixture_manifest_file_list(files: dict[object, object]) -> list[dict[str, object]]:
    """Return compact fixture file entries in manifest order."""

    entries: list[dict[str, object]] = []
    for name in sorted(str(key) for key in files):
        item = files.get(name)
        item = item if isinstance(item, dict) else {}
        entries.append(
            {
                "id": name,
                "path": item.get("path"),
                "relative_path": item.get("relative_path"),
                "sha256": item.get("sha256"),
                "size_bytes": item.get("size_bytes"),
                "ok": item.get("ok"),
            }
        )
    return entries


def _fixture_file_entry(path: Path, *, include_hash: bool = True, base_dir: Path | None = None) -> dict[str, object]:
    """Return one artifact index file entry for a regression fixture."""

    entry: dict[str, object] = {
        "path": str(path),
        "exists": path.exists(),
        "is_file": path.is_file(),
        "is_dir": path.is_dir(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "ok": path.exists() and path.is_file() and path.stat().st_size > 0,
    }
    if base_dir is not None:
        try:
            entry["relative_path"] = path.resolve().relative_to(base_dir.resolve()).as_posix()
        except Exception:
            pass
    if include_hash and path.is_file() and path.stat().st_size > 0:
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return entry


def _diagnose_with_fixture_events(
    run_dir: Path,
    artifacts_payload: dict[str, object],
    events: list[dict[str, object]],
) -> dict[str, object]:
    """Write fixture events with a fresh artifact hash and diagnose the run."""

    _write_fixture_events(run_dir, artifacts_payload, events)
    return diagnose_run_directory(run_dir, summary_only=True)


def _write_fixture_events(
    run_dir: Path,
    artifacts_payload: dict[str, object],
    events: list[dict[str, object]],
) -> None:
    """Write fixture JSONL events and refresh the indexed event-file hash."""

    events_file = run_dir / "events.jsonl"
    normalized_events = [_normalize_fixture_event(event) for event in events]
    events_file.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in normalized_events),
        encoding="utf-8",
    )
    fixed_files = artifacts_payload.get("fixed_files")
    if isinstance(fixed_files, dict):
        fixed_files["events"] = _fixture_file_entry(events_file, base_dir=run_dir)
    (run_dir / "artifacts.json").write_text(
        json.dumps(artifacts_payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _fixture_environment_payload(
    run_dir: Path,
    *,
    adapter: str,
    close_documents_after_run: bool = False,
    require_direct_hole_callout: bool = False,
    enforce_trusted_workflow: bool = False,
    cleanup_attach_only: bool = True,
) -> dict[str, object]:
    """Return a complete environment snapshot fixture."""

    return {
        "run_id": "hash_fixture",
        "adapter": adapter,
        "debug_level": "normal",
        "platform": {"system": "fixture", "python_version": "fixture"},
        "paths": {
            "output_root": str(run_dir.parent),
            "run_dir": str(run_dir),
            "part_template": {"path": None, "exists": False},
            "drawing_template": {"path": None, "exists": False},
        },
        "env": {
            "SOLIDWORKS_MCP_ADAPTER": adapter,
            "SOLIDWORKS_MCP_DEBUG_LEVEL": "normal",
            "SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT": require_direct_hole_callout,
            "SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN": close_documents_after_run,
            "SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW": enforce_trusted_workflow,
            "SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY": cleanup_attach_only,
        },
        "extra": {},
    }


def _diagnose_with_fixture_environment(
    run_dir: Path,
    artifacts_payload: dict[str, object],
    environment_payload: dict[str, object],
) -> dict[str, object]:
    """Write an environment fixture with a fresh artifact hash and diagnose the run."""

    _write_fixture_environment(run_dir, artifacts_payload, environment_payload)
    return diagnose_run_directory(run_dir, summary_only=True)


def _write_fixture_environment(
    run_dir: Path,
    artifacts_payload: dict[str, object],
    environment_payload: dict[str, object],
) -> None:
    """Write environment.json and refresh the indexed fixed-file hash."""

    environment_file = run_dir / "environment.json"
    environment_file.write_text(json.dumps(environment_payload, ensure_ascii=False), encoding="utf-8")
    fixed_files = artifacts_payload.get("fixed_files")
    if isinstance(fixed_files, dict):
        fixed_files["environment"] = _fixture_file_entry(environment_file, base_dir=run_dir)
    (run_dir / "artifacts.json").write_text(
        json.dumps(artifacts_payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_fixture_report_manifest_environment(
    run_dir: Path,
    artifacts_payload: dict[str, object],
    report_payload: dict[str, object],
    manifest_payload: dict[str, object],
    environment_payload: dict[str, object],
) -> None:
    """Write report, manifest, and environment fixtures, then refresh fixed-file hashes."""

    (run_dir / "execution_report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "delivery_manifest.json").write_text(
        json.dumps(manifest_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "environment.json").write_text(
        json.dumps(environment_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    fixed_files = artifacts_payload.get("fixed_files")
    if isinstance(fixed_files, dict):
        fixed_files["report"] = _fixture_file_entry(run_dir / "execution_report.json", base_dir=run_dir)
        fixed_files["delivery_manifest"] = _fixture_file_entry(run_dir / "delivery_manifest.json", base_dir=run_dir)
        fixed_files["environment"] = _fixture_file_entry(run_dir / "environment.json", base_dir=run_dir)
    (run_dir / "artifacts.json").write_text(
        json.dumps(artifacts_payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _normalize_fixture_event(event: dict[str, object]) -> dict[str, object]:
    """Return one fixture event with default run metadata and terminal counts."""

    normalized = {"run_id": "hash_fixture", **event}
    if normalized.get("event") == "plan.execution" and normalized.get("status") in {"completed", "failed"}:
        details = normalized.get("details")
        details = dict(details) if isinstance(details, dict) else {}
        details.setdefault("output_count", 1)
        details.setdefault("preview_count", 1)
        normalized["details"] = details
    return normalized


def _has_event_log_issue(result: dict[str, object], status: str) -> bool:
    """Return whether diagnosis reported a specific event-log issue."""

    return any(
        item.get("status") == status
        for item in result.get("event_log_issues", [])
        if isinstance(item, dict)
    )


def _has_repair_action(result: dict[str, object], action_id: str) -> bool:
    """Return whether a production acceptance result includes a repair action id."""

    return any(
        item.get("id") == action_id and bool(item.get("next_step"))
        for item in result.get("repair_actions", []) or []
        if isinstance(item, dict)
    )


def _has_missing_artifact_status(result: dict[str, object], status: str) -> bool:
    """Return whether diagnosis reported a specific artifact-integrity issue."""

    return any(
        item.get("status") == status
        for item in result.get("missing_artifacts", [])
        if isinstance(item, dict)
    )


def _has_missing_sha256(result: dict[str, object], group: str, name: str) -> bool:
    """Return whether diagnosis reported a missing SHA-256 for one artifact entry."""

    return any(
        item.get("group") == group and item.get("name") == name and item.get("status") == "missing_sha256"
        for item in result.get("missing_artifacts", [])
        if isinstance(item, dict)
    )


def _has_environment_issue(result: dict[str, object], field: str, status: str) -> bool:
    """Return whether diagnosis reported a specific environment issue."""

    return any(
        item.get("field") == field and item.get("status") == status
        for item in result.get("environment_issues", [])
        if isinstance(item, dict)
    )


def _with_thread_spec(base_plan: dict[str, object], thread_spec: str) -> dict[str, object]:
    """Return a copy of the base plan with one thread spec selected."""

    plan = copy.deepcopy(base_plan)
    plan["operations"][0]["parameters"]["thread_spec"] = thread_spec
    return plan


def _check_optional_export_formats(base_plan: dict[str, object]) -> dict[str, object]:
    """Verify optional DXF, IGES and Parasolid formats are schema-valid and content-checked."""

    optional_plan_raw = copy.deepcopy(base_plan)
    optional_plan_raw["output_formats"] = ["sldprt", "step", "stl", "dxf", "iges", "x_t", "x_b"]
    optional_plan = ModelPlan.from_dict(optional_plan_raw)
    expected_formats = ("sldprt", "step", "stl", "dxf", "iges", "x_t", "x_b")
    if optional_plan.output_formats != expected_formats:
        raise SystemExit(f"Unexpected optional export formats: {optional_plan.output_formats}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        output_files = _cad_content_fixture_files(tmp_dir)
        ok_result = _validate_cad_artifact_content(output_files)
        if ok_result.get("status") != "cad_artifacts_verified":
            raise SystemExit(f"Expected optional CAD export fixture to verify: {ok_result}")

        bad_files = dict(output_files)
        bad_iges = tmp_dir / "bad.igs"
        bad_iges.write_text("not an IGES export\n", encoding="utf-8")
        bad_files["iges"] = str(bad_iges)
        bad_result = _validate_cad_artifact_content(bad_files)
        if bad_result.get("status") != "cad_artifact_content_failed":
            raise SystemExit(f"Expected invalid IGES optional export to fail: {bad_result}")
        failed_ids = {
            str(item.get("id"))
            for item in bad_result.get("failed", [])
            if isinstance(item, dict)
        }
        if "iges" not in failed_ids:
            raise SystemExit(f"Expected invalid IGES failure id: {bad_result}")

        bad_files = dict(output_files)
        bad_dxf = tmp_dir / "bad.dxf"
        bad_dxf.write_text("not a DXF drawing exchange export\n", encoding="utf-8")
        bad_files["dxf"] = str(bad_dxf)
        bad_dxf_result = _validate_cad_artifact_content(bad_files)
        if bad_dxf_result.get("status") != "cad_artifact_content_failed":
            raise SystemExit(f"Expected invalid DXF optional export to fail: {bad_dxf_result}")
        failed_dxf_ids = {
            str(item.get("id"))
            for item in bad_dxf_result.get("failed", [])
            if isinstance(item, dict)
        }
        if "dxf" not in failed_dxf_ids:
            raise SystemExit(f"Expected invalid DXF failure id: {bad_dxf_result}")

    invalid_plan_raw = copy.deepcopy(base_plan)
    invalid_plan_raw["output_formats"] = ["sldprt", "sat"]
    try:
        ModelPlan.from_dict(invalid_plan_raw)
    except PlanValidationError as exc:
        invalid_reason = str(exc)
    else:
        raise SystemExit("Expected unsupported SAT export format to be rejected")

    return {
        "accepted": list(optional_plan.output_formats),
        "cad_content_status": ok_result.get("status"),
        "invalid_dxf_status": bad_dxf_result.get("status"),
        "invalid_iges_status": bad_result.get("status"),
        "unsupported_format_rejection": invalid_reason,
    }


def _check_production_scenarios(base_plan: dict[str, object]) -> dict[str, object]:
    """Verify the trusted production suite includes the optional neutral-export gate."""

    scenarios = _production_scenarios(base_plan)
    by_name = {
        str(scenario.get("name")): scenario
        for scenario in scenarios
        if isinstance(scenario, dict)
    }
    required = {
        "baseline",
        "material_alias",
        "custom_properties",
        "combined",
        "drawing_exchange",
        "neutral_exports",
        "wide_combined",
        "flange_baseline",
        "center_hole_plate_baseline",
        "bracket_baseline",
        "end_cap_baseline",
        "mounting_block_baseline",
        "shaft_baseline",
        "sheet_metal_base_flange_baseline",
        "weldment_frame_baseline",
        "washer_baseline",
        "sleeve_baseline",
        "slotted_array_plate_baseline",
        "bom_assembly_baseline",
        "atomic_baseline",
        "atomic_cut_baseline",
        "atomic_pattern_baseline",
        "atomic_revolve_baseline",
        "atomic_sweep_baseline",
        "atomic_loft_baseline",
    }
    missing = sorted(required - set(by_name))
    if missing:
        raise SystemExit(f"Production scenarios missing required entries: {missing}")

    neutral_plan_raw = by_name["neutral_exports"].get("plan")
    if not isinstance(neutral_plan_raw, dict):
        raise SystemExit(f"neutral_exports scenario did not expose a plan: {by_name['neutral_exports']}")
    neutral_plan = ModelPlan.from_dict(neutral_plan_raw)
    expected_formats = ("sldprt", "step", "stl", "iges", "x_t", "x_b")
    if neutral_plan.output_formats != expected_formats:
        raise SystemExit(f"neutral_exports output formats drifted: {neutral_plan.output_formats}")

    drawing_exchange_plan_raw = by_name["drawing_exchange"].get("plan")
    if not isinstance(drawing_exchange_plan_raw, dict):
        raise SystemExit(f"drawing_exchange scenario did not expose a plan: {by_name['drawing_exchange']}")
    drawing_exchange_plan = ModelPlan.from_dict(drawing_exchange_plan_raw)
    expected_drawing_exchange_formats = ("sldprt", "step", "stl", "dxf")
    if drawing_exchange_plan.output_formats != expected_drawing_exchange_formats:
        raise SystemExit(f"drawing_exchange output formats drifted: {drawing_exchange_plan.output_formats}")

    flange_plan_raw = by_name["flange_baseline"].get("plan")
    if not isinstance(flange_plan_raw, dict):
        raise SystemExit(f"flange_baseline scenario did not expose a plan: {by_name['flange_baseline']}")
    flange_plan = ModelPlan.from_dict(flange_plan_raw)
    flange_workflow = _build_production_acceptance_result(
        flange_plan,
        True,
        _accepted_center_hole_flange_diagnostics(flange_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if flange_workflow.get("status") != "accepted":
        raise SystemExit(f"flange_baseline scenario did not satisfy accepted fixture gates: {flange_workflow}")

    center_hole_plate_plan_raw = by_name["center_hole_plate_baseline"].get("plan")
    if not isinstance(center_hole_plate_plan_raw, dict):
        raise SystemExit(
            f"center_hole_plate_baseline scenario did not expose a plan: {by_name['center_hole_plate_baseline']}"
        )
    center_hole_plate_plan = ModelPlan.from_dict(center_hole_plate_plan_raw)
    center_hole_plate_workflow = _build_production_acceptance_result(
        center_hole_plate_plan,
        True,
        _accepted_center_hole_plate_diagnostics(center_hole_plate_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if center_hole_plate_workflow.get("status") != "accepted":
        raise SystemExit(
            "center_hole_plate_baseline scenario did not satisfy accepted fixture gates: "
            f"{center_hole_plate_workflow}"
        )

    bracket_plan_raw = by_name["bracket_baseline"].get("plan")
    if not isinstance(bracket_plan_raw, dict):
        raise SystemExit(f"bracket_baseline scenario did not expose a plan: {by_name['bracket_baseline']}")
    bracket_plan = ModelPlan.from_dict(bracket_plan_raw)
    bracket_workflow = _build_production_acceptance_result(
        bracket_plan,
        True,
        _accepted_bracket_diagnostics(bracket_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if bracket_workflow.get("status") != "accepted":
        raise SystemExit(f"bracket_baseline scenario did not satisfy accepted fixture gates: {bracket_workflow}")

    end_cap_plan_raw = by_name["end_cap_baseline"].get("plan")
    if not isinstance(end_cap_plan_raw, dict):
        raise SystemExit(f"end_cap_baseline scenario did not expose a plan: {by_name['end_cap_baseline']}")
    end_cap_plan = ModelPlan.from_dict(end_cap_plan_raw)
    end_cap_workflow = _build_production_acceptance_result(
        end_cap_plan,
        True,
        _accepted_end_cap_diagnostics(end_cap_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if end_cap_workflow.get("status") != "accepted":
        raise SystemExit(f"end_cap_baseline scenario did not satisfy accepted fixture gates: {end_cap_workflow}")

    washer_plan_raw = by_name["washer_baseline"].get("plan")
    if not isinstance(washer_plan_raw, dict):
        raise SystemExit(f"washer_baseline scenario did not expose a plan: {by_name['washer_baseline']}")
    washer_plan = ModelPlan.from_dict(washer_plan_raw)
    washer_workflow = _build_production_acceptance_result(
        washer_plan,
        True,
        _accepted_washer_diagnostics(washer_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if washer_workflow.get("status") != "accepted":
        raise SystemExit(f"washer_baseline scenario did not satisfy accepted fixture gates: {washer_workflow}")

    sleeve_plan_raw = by_name["sleeve_baseline"].get("plan")
    if not isinstance(sleeve_plan_raw, dict):
        raise SystemExit(f"sleeve_baseline scenario did not expose a plan: {by_name['sleeve_baseline']}")
    sleeve_plan = ModelPlan.from_dict(sleeve_plan_raw)
    sleeve_workflow = _build_production_acceptance_result(
        sleeve_plan,
        True,
        _accepted_sleeve_diagnostics(sleeve_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if sleeve_workflow.get("status") != "accepted":
        raise SystemExit(f"sleeve_baseline scenario did not satisfy accepted fixture gates: {sleeve_workflow}")

    slotted_array_plate_plan_raw = by_name["slotted_array_plate_baseline"].get("plan")
    if not isinstance(slotted_array_plate_plan_raw, dict):
        raise SystemExit(
            "slotted_array_plate_baseline scenario did not expose a plan: "
            f"{by_name['slotted_array_plate_baseline']}"
        )
    slotted_array_plate_plan = ModelPlan.from_dict(slotted_array_plate_plan_raw)
    slotted_array_plate_workflow = _build_production_acceptance_result(
        slotted_array_plate_plan,
        True,
        _accepted_slotted_array_plate_diagnostics(slotted_array_plate_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if slotted_array_plate_workflow.get("status") != "accepted":
        raise SystemExit(
            "slotted_array_plate_baseline scenario did not satisfy accepted fixture gates: "
            f"{slotted_array_plate_workflow}"
        )

    bom_assembly_plan_raw = by_name["bom_assembly_baseline"].get("plan")
    if not isinstance(bom_assembly_plan_raw, dict):
        raise SystemExit(f"bom_assembly_baseline scenario did not expose a plan: {by_name['bom_assembly_baseline']}")
    bom_assembly_plan = ModelPlan.from_dict(bom_assembly_plan_raw)
    bom_assembly_workflow = _build_production_acceptance_result(
        bom_assembly_plan,
        True,
        _accepted_bom_assembly_diagnostics(),
        {key: f"mock.{key}" for key in ("sldasm", "step", "slddrw", "pdf", "dwg", "csv")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if bom_assembly_workflow.get("status") != "accepted":
        raise SystemExit(
            "bom_assembly_baseline scenario did not satisfy accepted fixture gates: "
            f"{bom_assembly_workflow}"
        )

    mounting_block_plan_raw = by_name["mounting_block_baseline"].get("plan")
    if not isinstance(mounting_block_plan_raw, dict):
        raise SystemExit(f"mounting_block_baseline scenario did not expose a plan: {by_name['mounting_block_baseline']}")
    mounting_block_plan = ModelPlan.from_dict(mounting_block_plan_raw)
    mounting_block_workflow = _build_production_acceptance_result(
        mounting_block_plan,
        True,
        _accepted_mounting_block_diagnostics(mounting_block_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if mounting_block_workflow.get("status") != "accepted":
        raise SystemExit(
            "mounting_block_baseline scenario did not satisfy accepted fixture gates: "
            f"{mounting_block_workflow}"
        )

    shaft_plan_raw = by_name["shaft_baseline"].get("plan")
    if not isinstance(shaft_plan_raw, dict):
        raise SystemExit(f"shaft_baseline scenario did not expose a plan: {by_name['shaft_baseline']}")
    shaft_plan = ModelPlan.from_dict(shaft_plan_raw)
    shaft_workflow = _build_production_acceptance_result(
        shaft_plan,
        True,
        _accepted_shaft_diagnostics(shaft_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if shaft_workflow.get("status") != "accepted":
        raise SystemExit(f"shaft_baseline scenario did not satisfy accepted fixture gates: {shaft_workflow}")

    sheet_metal_plan_raw = by_name["sheet_metal_base_flange_baseline"].get("plan")
    if not isinstance(sheet_metal_plan_raw, dict):
        raise SystemExit(
            "sheet_metal_base_flange_baseline scenario did not expose a plan: "
            f"{by_name['sheet_metal_base_flange_baseline']}"
        )
    sheet_metal_plan = ModelPlan.from_dict(sheet_metal_plan_raw)
    sheet_metal_workflow = _build_production_acceptance_result(
        sheet_metal_plan,
        True,
        _accepted_sheet_metal_base_flange_diagnostics(sheet_metal_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg", "dxf")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if sheet_metal_workflow.get("status") != "accepted":
        raise SystemExit(
            "sheet_metal_base_flange_baseline scenario did not satisfy accepted fixture gates: "
            f"{sheet_metal_workflow}"
        )

    weldment_plan_raw = by_name["weldment_frame_baseline"].get("plan")
    if not isinstance(weldment_plan_raw, dict):
        raise SystemExit(
            "weldment_frame_baseline scenario did not expose a plan: "
            f"{by_name['weldment_frame_baseline']}"
        )
    weldment_plan = ModelPlan.from_dict(weldment_plan_raw)
    weldment_workflow = _build_production_acceptance_result(
        weldment_plan,
        True,
        _accepted_weldment_frame_diagnostics(weldment_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg", "csv")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if weldment_workflow.get("status") != "accepted":
        raise SystemExit(
            "weldment_frame_baseline scenario did not satisfy accepted fixture gates: "
            f"{weldment_workflow}"
        )

    simulation_plan_raw = by_name["simulation_cantilever_baseline"].get("plan")
    if not isinstance(simulation_plan_raw, dict):
        raise SystemExit(
            "simulation_cantilever_baseline scenario did not expose a plan: "
            f"{by_name['simulation_cantilever_baseline']}"
        )
    simulation_plan = ModelPlan.from_dict(simulation_plan_raw)
    simulation_workflow = _build_production_acceptance_result(
        simulation_plan,
        True,
        _accepted_static_simulation_diagnostics(simulation_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg", "csv")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if simulation_workflow.get("status") != "accepted":
        raise SystemExit(
            "simulation_cantilever_baseline scenario did not satisfy accepted fixture gates: "
            f"{simulation_workflow}"
        )

    atomic_plan_raw = by_name["atomic_baseline"].get("plan")
    if not isinstance(atomic_plan_raw, dict):
        raise SystemExit(f"atomic_baseline scenario did not expose a plan: {by_name['atomic_baseline']}")
    atomic_plan = ModelPlan.from_dict(atomic_plan_raw)
    atomic_workflow = _build_production_acceptance_result(
        atomic_plan,
        True,
        _accepted_diagnostics(["dim_width"], "trusted_dimensions_created", require_direct_hole_callout=True),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if atomic_workflow.get("status") != "accepted":
        raise SystemExit(f"atomic_baseline scenario did not satisfy accepted fixture gates: {atomic_workflow}")

    atomic_cut_plan_raw = by_name["atomic_cut_baseline"].get("plan")
    if not isinstance(atomic_cut_plan_raw, dict):
        raise SystemExit(f"atomic_cut_baseline scenario did not expose a plan: {by_name['atomic_cut_baseline']}")
    atomic_cut_plan = ModelPlan.from_dict(atomic_cut_plan_raw)
    atomic_cut_workflow = _build_production_acceptance_result(
        atomic_cut_plan,
        True,
        _accepted_diagnostics(["dim_width"], "trusted_dimensions_created", require_direct_hole_callout=True),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if atomic_cut_workflow.get("status") != "accepted":
        raise SystemExit(f"atomic_cut_baseline scenario did not satisfy accepted fixture gates: {atomic_cut_workflow}")

    atomic_pattern_plan_raw = by_name["atomic_pattern_baseline"].get("plan")
    if not isinstance(atomic_pattern_plan_raw, dict):
        raise SystemExit(
            f"atomic_pattern_baseline scenario did not expose a plan: {by_name['atomic_pattern_baseline']}"
        )
    atomic_pattern_plan = ModelPlan.from_dict(atomic_pattern_plan_raw)
    atomic_pattern_workflow = _build_production_acceptance_result(
        atomic_pattern_plan,
        True,
        _accepted_diagnostics(
            ["dim_pattern_width"],
            "trusted_dimensions_created",
            require_direct_hole_callout=True,
        ),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if atomic_pattern_workflow.get("status") != "accepted":
        raise SystemExit(
            "atomic_pattern_baseline scenario did not satisfy accepted fixture gates: "
            f"{atomic_pattern_workflow}"
        )

    atomic_revolve_plan_raw = by_name["atomic_revolve_baseline"].get("plan")
    if not isinstance(atomic_revolve_plan_raw, dict):
        raise SystemExit(
            f"atomic_revolve_baseline scenario did not expose a plan: {by_name['atomic_revolve_baseline']}"
        )
    atomic_revolve_plan = ModelPlan.from_dict(atomic_revolve_plan_raw)
    atomic_revolve_diagnostics = _accepted_diagnostics(
        ["dim_revolve_outer_diameter"],
        "trusted_dimensions_created",
        require_direct_hole_callout=False,
    )
    atomic_revolve_diagnostics["drawing_annotation_status"] = "not_requested"
    atomic_revolve_diagnostics["drawing_annotation_result"] = {
        "status": "not_requested",
        "created_callout_count": 0,
        "callout_creation_method": None,
        "direct_hole_callout_created": None,
    }
    atomic_revolve_workflow = _build_production_acceptance_result(
        atomic_revolve_plan,
        True,
        atomic_revolve_diagnostics,
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if atomic_revolve_workflow.get("status") != "accepted":
        raise SystemExit(
            "atomic_revolve_baseline scenario did not satisfy accepted fixture gates: "
            f"{atomic_revolve_workflow}"
        )

    atomic_sweep_plan_raw = by_name["atomic_sweep_baseline"].get("plan")
    if not isinstance(atomic_sweep_plan_raw, dict):
        raise SystemExit(
            f"atomic_sweep_baseline scenario did not expose a plan: {by_name['atomic_sweep_baseline']}"
        )
    atomic_sweep_plan = ModelPlan.from_dict(atomic_sweep_plan_raw)
    atomic_sweep_diagnostics = _accepted_diagnostics(
        ["dim_sweep_profile_diameter"],
        "trusted_dimensions_created",
        require_direct_hole_callout=False,
    )
    atomic_sweep_diagnostics["drawing_annotation_status"] = "not_requested"
    atomic_sweep_diagnostics["drawing_annotation_result"] = {
        "status": "not_requested",
        "created_callout_count": 0,
        "callout_creation_method": None,
        "direct_hole_callout_created": None,
    }
    atomic_sweep_workflow = _build_production_acceptance_result(
        atomic_sweep_plan,
        True,
        atomic_sweep_diagnostics,
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if atomic_sweep_workflow.get("status") != "accepted":
        raise SystemExit(
            "atomic_sweep_baseline scenario did not satisfy accepted fixture gates: "
            f"{atomic_sweep_workflow}"
        )

    atomic_loft_plan_raw = by_name["atomic_loft_baseline"].get("plan")
    if not isinstance(atomic_loft_plan_raw, dict):
        raise SystemExit(
            f"atomic_loft_baseline scenario did not expose a plan: {by_name['atomic_loft_baseline']}"
        )
    atomic_loft_plan = ModelPlan.from_dict(atomic_loft_plan_raw)
    atomic_loft_diagnostics = _accepted_diagnostics(
        ["dim_loft_primary_diameter"],
        "trusted_dimensions_created",
        require_direct_hole_callout=False,
    )
    atomic_loft_diagnostics["drawing_annotation_status"] = "not_requested"
    atomic_loft_diagnostics["drawing_annotation_result"] = {
        "status": "not_requested",
        "created_callout_count": 0,
        "callout_creation_method": None,
        "direct_hole_callout_created": None,
    }
    atomic_loft_workflow = _build_production_acceptance_result(
        atomic_loft_plan,
        True,
        atomic_loft_diagnostics,
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if atomic_loft_workflow.get("status") != "accepted":
        raise SystemExit(
            "atomic_loft_baseline scenario did not satisfy accepted fixture gates: "
            f"{atomic_loft_workflow}"
        )

    return {
        "names": [str(scenario.get("name")) for scenario in scenarios],
        "drawing_exchange": list(drawing_exchange_plan.output_formats),
        "neutral_exports": list(neutral_plan.output_formats),
        "flange_status": flange_workflow.get("status"),
        "center_hole_plate_status": center_hole_plate_workflow.get("status"),
        "bracket_status": bracket_workflow.get("status"),
        "end_cap_status": end_cap_workflow.get("status"),
        "mounting_block_status": mounting_block_workflow.get("status"),
        "shaft_status": shaft_workflow.get("status"),
        "sheet_metal_status": sheet_metal_workflow.get("status"),
        "weldment_status": weldment_workflow.get("status"),
        "simulation_status": simulation_workflow.get("status"),
        "washer_status": washer_workflow.get("status"),
        "sleeve_status": sleeve_workflow.get("status"),
        "slotted_array_plate_status": slotted_array_plate_workflow.get("status"),
        "bom_assembly_status": bom_assembly_workflow.get("status"),
        "atomic_status": atomic_workflow.get("status"),
        "atomic_cut_status": atomic_cut_workflow.get("status"),
        "atomic_pattern_status": atomic_pattern_workflow.get("status"),
        "atomic_revolve_status": atomic_revolve_workflow.get("status"),
        "atomic_sweep_status": atomic_sweep_workflow.get("status"),
        "atomic_loft_status": atomic_loft_workflow.get("status"),
    }


def _check_release_gate_contract() -> dict[str, object]:
    """Verify release-gate scenario defaults and aggregate acceptance contract."""

    expected_default = (
        "baseline",
        "material_alias",
        "custom_properties",
        "combined",
        "drawing_exchange",
        "neutral_exports",
        "wide_combined",
        "flange_baseline",
        "center_hole_plate_baseline",
        "bracket_baseline",
        "end_cap_baseline",
        "mounting_block_baseline",
        "shaft_baseline",
        "sheet_metal_base_flange_baseline",
        "weldment_frame_baseline",
        "washer_baseline",
        "sleeve_baseline",
        "slotted_array_plate_baseline",
        "bom_assembly_baseline",
        "atomic_baseline",
        "atomic_cut_baseline",
        "atomic_pattern_baseline",
        "atomic_revolve_baseline",
        "atomic_sweep_baseline",
        "atomic_loft_baseline",
    )
    if DEFAULT_SCENARIOS != expected_default:
        raise SystemExit(f"Release gate default scenarios drifted: {DEFAULT_SCENARIOS}")
    fixture = _release_gate_payload(
        adapter="mock",
        output_root=ROOT / "outputs" / "release_gate_fixture",
        scenario_names=list(expected_default),
        smoke_results=[
            {
                "results": [
                    _release_gate_scenario_fixture(scenario)
                    for scenario in expected_default
                ]
            }
        ],
        batch_diagnosis={
            "ok": True,
            "scan_status": "complete",
            "run_count": len(expected_default),
            "accepted_count": len(expected_default),
            "rejected_count": 0,
            "issue_counts": {},
        },
    )
    if fixture.get("status") != "accepted":
        raise SystemExit(f"Expected release-gate aggregate fixture to accept: {fixture}")
    if fixture.get("schema_version") != "2026-06-06.1":
        raise SystemExit(f"Release-gate schema version drifted: {fixture}")
    fixture["report_file"] = str(ROOT / "outputs" / "release_gate_fixture" / "release_gate_report.json")
    compact_fixture = _compact_release_payload(fixture)
    if compact_fixture.get("report_file") != fixture["report_file"]:
        raise SystemExit(f"Compact release-gate payload did not expose report_file: {compact_fixture}")
    bad_count = _release_gate_payload(
        adapter="mock",
        output_root=ROOT / "outputs" / "release_gate_fixture",
        scenario_names=list(expected_default),
        smoke_results=[
            {
                "results": [
                    _release_gate_scenario_fixture(scenario)
                    for scenario in expected_default
                ]
            }
        ],
        batch_diagnosis={
            "ok": True,
            "scan_status": "complete",
            "run_count": len(expected_default) - 1,
            "accepted_count": len(expected_default) - 1,
            "rejected_count": 0,
            "issue_counts": {},
        },
    )
    if bad_count.get("status") != "rejected" or "batch_count_matches" not in bad_count.get("failures", []):
        raise SystemExit(f"Expected release-gate count mismatch fixture to reject: {bad_count}")
    with tempfile.TemporaryDirectory() as temp_dir:
        output_root = Path(temp_dir) / "release_gate_interrupted"
        run_dir = output_root / "partial_run"
        run_dir.mkdir(parents=True)
        (run_dir / "execution_report.json").write_text(
            json.dumps({"output_files": {}}, ensure_ascii=False),
            encoding="utf-8",
        )
        cleanup_config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=output_root,
            part_template=None,
            drawing_template=None,
            visible=False,
            macro_fallback_enabled=True,
            macro_execution_disabled=False,
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
            run_id="release_gate_interrupted",
        )
        cleanup_result = _emergency_cleanup_completed_runs(output_root, cleanup_config)
        if cleanup_result.get("status") != "completed" or cleanup_result.get("attempted_count") != 1:
            raise SystemExit(f"Expected interrupted release cleanup to complete: {cleanup_result}")
        exception_payload = _release_gate_exception_payload(
            adapter="mock",
            output_root=output_root,
            scenario_names=["baseline"],
            smoke_results=[],
            config=cleanup_config,
            exc=RuntimeError("fixture release failure"),
            failure_class="release_gate_exception",
        )
        if exception_payload.get("status") != "rejected":
            raise SystemExit(f"Expected exception release payload to reject: {exception_payload}")
        if "release_gate_exception" not in exception_payload.get("failures", []):
            raise SystemExit(f"Expected exception payload failure id: {exception_payload}")
        compact_exception = _compact_release_payload(exception_payload)
        compact_cleanup = compact_exception.get("emergency_cleanup_result")
        if not isinstance(compact_cleanup, dict) or compact_cleanup.get("attempted_count") != 1:
            raise SystemExit(f"Expected compact exception payload to expose cleanup: {compact_exception}")
    return {
        "default_scenarios": list(DEFAULT_SCENARIOS),
        "accepted_status": fixture.get("status"),
        "schema_version": fixture.get("schema_version"),
        "compact_report_file": compact_fixture.get("report_file"),
        "count_mismatch_status": bad_count.get("status"),
        "count_mismatch_failures": bad_count.get("failures", []),
        "exception_cleanup_status": cleanup_result.get("status"),
        "exception_payload_status": exception_payload.get("status"),
    }


def _check_smoke_exception_contract() -> dict[str, object]:
    """Verify the smoke CLI exception payload performs scoped emergency cleanup."""

    with tempfile.TemporaryDirectory() as temp_dir:
        output_root = Path(temp_dir) / "smoke_exception"
        run_dir = output_root / "recent_run"
        run_dir.mkdir(parents=True)
        (run_dir / "execution_report.json").write_text(
            json.dumps({"output_files": {}}, ensure_ascii=False),
            encoding="utf-8",
        )
        config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=output_root,
            part_template=None,
            drawing_template=None,
            visible=False,
            macro_fallback_enabled=True,
            macro_execution_disabled=False,
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
            run_id="smoke_exception_fixture",
        )
        recent_run_dirs = _recent_completed_run_dirs(output_root, 0.0)
        if recent_run_dirs != [run_dir.resolve()]:
            raise SystemExit(f"Expected smoke exception fixture run dir discovery: {recent_run_dirs}")
        cleanup_result = _emergency_cleanup_recent_runs(output_root, config, 0.0)
        if cleanup_result.get("status") != "completed" or cleanup_result.get("attempted_count") != 1:
            raise SystemExit(f"Expected smoke emergency cleanup to complete: {cleanup_result}")
        payload = _smoke_exception_payload(
            config=config,
            output_root=output_root,
            started_at=0.0,
            exc=RuntimeError("fixture smoke failure"),
            failure_class="smoke_exception",
        )
        if payload.get("status") != "rejected" or "smoke_exception" not in payload.get("failures", []):
            raise SystemExit(f"Expected smoke exception payload to reject: {payload}")
        report_file = _write_smoke_exception_report(output_root, payload)
        saved_payload = json.loads(report_file.read_text(encoding="utf-8"))
        if saved_payload.get("failure_class") != "smoke_exception":
            raise SystemExit(f"Expected smoke exception report to persist failure class: {saved_payload}")
        payload["report_file"] = str(report_file)
        compact = _compact_smoke_exception_payload(payload)
        compact_cleanup = compact.get("emergency_cleanup_result")
        if not isinstance(compact_cleanup, dict) or compact_cleanup.get("attempted_count") != 1:
            raise SystemExit(f"Expected compact smoke exception payload to expose cleanup: {compact}")
        os.environ["SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION_AFTER_RUN"] = "1"
        try:
            try:
                _raise_forced_smoke_exception_after_run()
            except RuntimeError as exc:
                if "SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION_AFTER_RUN" not in str(exc):
                    raise SystemExit(f"Unexpected smoke after-run exception message: {exc}") from exc
            else:
                raise SystemExit("Expected forced after-run smoke exception hook to raise")
        finally:
            os.environ.pop("SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION_AFTER_RUN", None)
        return {
            "recent_run_count": len(recent_run_dirs),
            "cleanup_status": cleanup_result.get("status"),
            "payload_status": payload.get("status"),
            "report_file_name": report_file.name,
            "compact_cleanup_attempted_count": compact_cleanup.get("attempted_count"),
            "after_run_hook": "verified",
        }


def _release_gate_scenario_fixture(scenario: str) -> dict[str, object]:
    """Return a compact accepted release-gate scenario fixture."""

    workflow = _fixture_release_workflow(scenario)
    has_hole_callout_requirement = workflow not in {
        "shaft",
        "bom_assembly",
        "sheet_metal_base_flange",
        "weldment_frame",
        "static_simulation",
    } and not _is_fixture_atomic_no_hole_scenario(scenario)
    output_files = (
        ["csv", "dwg", "pdf", "sldasm", "slddrw", "step"]
        if workflow == "bom_assembly"
        else ["dwg", "dxf", "pdf", "slddrw", "sldprt", "step", "stl"]
        if workflow == "sheet_metal_base_flange"
        else ["csv", "dwg", "pdf", "slddrw", "sldprt", "step", "stl"]
        if workflow == "weldment_frame"
        else ["csv", "dwg", "pdf", "slddrw", "sldprt", "step", "stl"]
        if workflow == "static_simulation"
        else ["dwg", "pdf", "slddrw", "sldprt", "step", "stl"]
    )
    return {
        "scenario": scenario,
        "ok": True,
        "validation_ok": True,
        "execution_ok": True,
        "report_file": f"mock/{scenario}/execution_report.json",
        "delivery_manifest_file": f"mock/{scenario}/delivery_manifest.json",
        "run_id": f"fixture_{scenario}",
        "run_dir": f"mock/{scenario}",
        "offline_diagnosis": {"ok": True},
        "acceptance": {"ok": True, "status": "accepted", "failures": []},
        "summary": {
            "trusted_workflow_status": f"controlled_{workflow}",
            "thread_model_status": "holewizard_threaded_hole" if workflow == "mounting_plate" else "not_requested",
            "corner_radius_status": "fillet_feature" if workflow == "mounting_plate" else "not_requested",
            "drawing_view_status": "created",
            "missing_drawing_view_roles": [],
            "drawing_annotation_status": "hole_callout_created"
            if has_hole_callout_requirement
            else "not_requested",
            "callout_creation_method": "add_hole_callout2" if has_hole_callout_requirement else None,
            "direct_hole_callout_created": True if has_hole_callout_requirement else None,
            "drawing_dimension_status": "not_requested" if workflow == "bom_assembly" else "basic_dimensions_created",
            "dimension_layout_status": "not_requested" if workflow == "bom_assembly" else "trusted_dimensions_created",
            "proxy_dimensions": [],
            "non_radial_radius_dimensions": [],
            "missing_dimensions": [],
            "model_geometry_status": "not_requested" if workflow == "bom_assembly" else "geometry_verified",
            "mass_property_status": "not_requested" if workflow == "bom_assembly" else "mass_properties_verified",
            "sheet_metal_status": "sheet_metal_verified" if workflow == "sheet_metal_base_flange" else "not_requested",
            "flat_pattern_status": "flat_pattern_exported"
            if workflow == "sheet_metal_base_flange"
            else "not_requested",
            "weldment_status": "weldment_verified" if workflow == "weldment_frame" else "not_requested",
            "structural_member_created": True if workflow == "weldment_frame" else None,
            "weldment_feature_type": "WeldMemberFeat" if workflow == "weldment_frame" else None,
            "weldment_body_count": 4 if workflow == "weldment_frame" else 0,
            "cut_list_status": "cut_list_verified" if workflow == "weldment_frame" else "not_requested",
            "cut_list_row_count": 2 if workflow == "weldment_frame" else 0,
            "simulation_status": "simulation_verified" if workflow == "static_simulation" else "not_requested",
            "simulation_study_type": "static" if workflow == "static_simulation" else None,
            "simulation_solver": "fixture_static_solver" if workflow == "static_simulation" else None,
            "simulation_report_row_count": 3 if workflow == "static_simulation" else 0,
            "simulation_max_von_mises_mpa": 140.625 if workflow == "static_simulation" else None,
            "simulation_min_factor_of_safety": 1.777778 if workflow == "static_simulation" else None,
            "simulation_max_displacement_mm": 0.84375 if workflow == "static_simulation" else None,
            "assembly_status": "assembly_verified" if workflow == "bom_assembly" else "not_requested",
            "component_instance_count": 3 if workflow == "bom_assembly" else 0,
            "bom_status": "bom_verified" if workflow == "bom_assembly" else "not_requested",
            "bom_row_count": 2 if workflow == "bom_assembly" else 0,
            "artifact_content_status": "content_ready",
            "cad_content_status": "cad_artifacts_verified",
            "pdf_semantic_content_status": "pdf_semantic_content_verified",
            "cleanup_status": "completed",
            "cleanup_verification_status": "verified",
            "document_state_audit_status": "verified_no_run_documents_open",
            "document_state_after_cleanup_run_created_open_count": 0,
            "output_files": output_files,
            "preview_files": ["front", "top", "right", "isometric"],
        },
    }


def _check_center_hole_flange_candidate() -> dict[str, object]:
    """Verify the controlled flange candidate is schema-valid but not falsely accepted."""

    flange_raw = json.loads((ROOT / "examples" / "flange_plan.json").read_text(encoding="utf-8"))
    flange_plan = ModelPlan.from_dict(flange_raw)
    with tempfile.TemporaryDirectory() as temp_dir:
        config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=Path(temp_dir),
            part_template=None,
            drawing_template=None,
            visible=False,
            macro_fallback_enabled=True,
            macro_execution_disabled=False,
            force_holewizard_failure=False,
            force_drawing_callout_failure=False,
            force_drawing_dimension_failure=False,
            force_cad_content_failure=False,
            force_cleanup_failure=False,
            force_material_failure=False,
            force_preflight_failure=False,
            enforce_trusted_workflow=True,
            require_direct_hole_callout=False,
            close_documents_after_run=True,
            cleanup_attach_only=True,
            debug_level="basic",
            run_id="center_hole_flange_candidate",
        )
        validation_report = ModelPlanExecutor(create_adapter(config), config).validate_plan(flange_raw).to_dict()
    readiness = validation_report.get("diagnostics", {}).get("production_readiness_status")
    if readiness != "trusted_workflow_ready":
        raise SystemExit(f"Expected controlled flange validation to be production-ready: {validation_report}")

    invalid_raw = copy.deepcopy(flange_raw)
    invalid_raw["operations"][0]["parameters"]["hole_diameter"] = 99
    try:
        ModelPlan.from_dict(invalid_raw)
    except PlanValidationError as exc:
        invalid_reason = str(exc)
    else:
        raise SystemExit("Expected unsafe center-hole flange wall thickness to be rejected")

    acceptance = _build_production_acceptance_result(
        flange_plan,
        True,
        _accepted_center_hole_flange_diagnostics(flange_plan),
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if acceptance.get("status") != "accepted":
        raise SystemExit(f"Controlled flange fixture with trusted drawing gates should be accepted: {acceptance}")

    missing_dimension_diagnostics = _accepted_center_hole_flange_diagnostics(flange_plan)
    missing_dimension_diagnostics["drawing_dimension_result"] = dict(
        missing_dimension_diagnostics["drawing_dimension_result"]
    )
    missing_dimension_diagnostics["drawing_dimension_result"]["created_dimension_count"] = 2
    missing_dimension_diagnostics["drawing_dimension_result"]["created_dimensions"] = (
        missing_dimension_diagnostics["drawing_dimension_result"]["created_dimensions"][:2]
    )
    missing_dimension_diagnostics["drawing_dimension_result"]["missing_dimensions"] = ["thickness_12"]
    missing_dimension_acceptance = _build_production_acceptance_result(
        flange_plan,
        True,
        missing_dimension_diagnostics,
        {key: f"mock.{key}" for key in ("sldprt", "step", "stl", "slddrw", "pdf", "dwg")},
        {key: f"mock_{key}.png" for key in ("front", "top", "right", "isometric")},
    )
    if missing_dimension_acceptance.get("status") != "rejected":
        raise SystemExit(f"Expected flange missing-dimension fixture to reject: {missing_dimension_acceptance}")
    if not _has_repair_action(missing_dimension_acceptance, "basic_dimensions_created"):
        raise SystemExit(f"Missing flange basic-dimension repair action: {missing_dimension_acceptance}")
    repair_text = json.dumps(missing_dimension_acceptance.get("repair_actions", []), ensure_ascii=False)
    stale_terms = ("mounting-plate", "mounting plate", "corner radius", "AddRadialDimension2")
    if any(term in repair_text for term in stale_terms):
        raise SystemExit(f"Flange repair action still contains mounting-plate-specific text: {repair_text}")

    return {
        "validation_readiness": readiness,
        "workflow_status": acceptance.get("summary", {}).get("trusted_workflow_status"),
        "acceptance_status": acceptance.get("status"),
        "acceptance_failures": acceptance.get("failures", []),
        "missing_dimension_repair_status": missing_dimension_acceptance.get("status"),
        "invalid_wall_rejection": invalid_reason,
    }


def _accepted_center_hole_flange_diagnostics(flange_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled center-hole flange fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    flange_dimensions = center_hole_flange_basic_dimension_ids_from_plan(flange_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "hole_callout_created"
    diagnostics["drawing_annotation_result"] = {
        "status": "hole_callout_created",
        "created_callout_count": 1,
        "direct_hole_callout_created": True,
        "callout_creation_method": "add_hole_callout2",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(flange_dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in flange_dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["material_status"] = "material_verified"
    diagnostics["material_result"] = {
        "status": "material_verified",
        "requested_material": "Plain Carbon Steel",
        "effective_material": "Plain Carbon Steel",
        "current_material": "Plain Carbon Steel",
        "verified": True,
    }
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [12.0, 100.0, 100.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [12.0, 100.0, 100.0]
    return diagnostics


def _accepted_center_hole_plate_diagnostics(center_hole_plate_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled center-hole plate fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = center_hole_plate_basic_dimension_ids_from_plan(center_hole_plate_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "hole_callout_created"
    diagnostics["drawing_annotation_result"] = {
        "status": "hole_callout_created",
        "created_callout_count": 1,
        "direct_hole_callout_created": True,
        "callout_creation_method": "add_hole_callout2",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["material_status"] = "material_verified"
    diagnostics["material_result"] = {
        "status": "material_verified",
        "requested_material": "Plain Carbon Steel",
        "effective_material": "Plain Carbon Steel",
        "current_material": "Plain Carbon Steel",
        "verified": True,
    }
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [12.0, 60.0, 100.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [12.0, 60.0, 100.0]
    return diagnostics


def _accepted_washer_diagnostics(washer_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled washer fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = washer_basic_dimension_ids_from_plan(washer_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "hole_callout_created"
    diagnostics["drawing_annotation_result"] = {
        "status": "hole_callout_created",
        "created_callout_count": 1,
        "direct_hole_callout_created": True,
        "callout_creation_method": "add_hole_callout2",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["material_status"] = "material_verified"
    diagnostics["material_result"] = {
        "status": "material_verified",
        "requested_material": "Plain Carbon Steel",
        "effective_material": "Plain Carbon Steel",
        "current_material": "Plain Carbon Steel",
        "verified": True,
    }
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [3.0, 30.0, 30.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [3.0, 30.0, 30.0]
    return diagnostics


def _accepted_mounting_block_diagnostics(mounting_block_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled mounting-block fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = mounting_block_basic_dimension_ids_from_plan(mounting_block_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "hole_callout_created"
    diagnostics["drawing_annotation_result"] = {
        "status": "hole_callout_created",
        "created_callout_count": 1,
        "direct_hole_callout_created": True,
        "callout_creation_method": "add_hole_callout2",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["material_status"] = "material_verified"
    diagnostics["material_result"] = {
        "status": "material_verified",
        "requested_material": "Plain Carbon Steel",
        "effective_material": "Plain Carbon Steel",
        "current_material": "Plain Carbon Steel",
        "verified": True,
    }
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [30.0, 50.0, 80.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [30.0, 50.0, 80.0]
    return diagnostics


def _accepted_bracket_diagnostics(bracket_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled bracket fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = bracket_basic_dimension_ids_from_plan(bracket_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "hole_callout_created"
    diagnostics["drawing_annotation_result"] = {
        "status": "hole_callout_created",
        "created_callout_count": 2,
        "direct_hole_callout_created": True,
        "callout_creation_method": "add_hole_callout2",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["material_status"] = "material_verified"
    diagnostics["material_result"] = {
        "status": "material_verified",
        "requested_material": "Plain Carbon Steel",
        "effective_material": "Plain Carbon Steel",
        "current_material": "Plain Carbon Steel",
        "verified": True,
    }
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [50.0, 70.0, 80.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [50.0, 70.0, 80.0]
    diagnostics["model_geometry_result"]["expected_hole_diameter_mm"] = 6.0
    return diagnostics


def _accepted_end_cap_diagnostics(end_cap_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled end-cap fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = end_cap_basic_dimension_ids_from_plan(end_cap_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "hole_callout_created"
    diagnostics["drawing_annotation_result"] = {
        "status": "hole_callout_created",
        "created_callout_count": 2,
        "direct_hole_callout_created": True,
        "callout_creation_method": "add_hole_callout2",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["material_status"] = "material_verified"
    diagnostics["material_result"] = {
        "status": "material_verified",
        "requested_material": "Plain Carbon Steel",
        "effective_material": "Plain Carbon Steel",
        "current_material": "Plain Carbon Steel",
        "verified": True,
    }
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [10.0, 100.0, 100.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [10.0, 100.0, 100.0]
    diagnostics["model_geometry_result"]["expected_bolt_hole_count"] = 6
    return diagnostics


def _accepted_shaft_diagnostics(shaft_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled shaft fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = shaft_basic_dimension_ids_from_plan(shaft_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "not_requested"
    diagnostics["drawing_annotation_result"] = {
        "status": "not_requested",
        "created_callout_count": 0,
        "direct_hole_callout_created": None,
        "callout_creation_method": None,
        "reason": "controlled_shaft_has_no_holes",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["material_status"] = "material_verified"
    diagnostics["material_result"] = {
        "status": "material_verified",
        "requested_material": "Plain Carbon Steel",
        "effective_material": "Plain Carbon Steel",
        "current_material": "Plain Carbon Steel",
        "verified": True,
    }
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [25.0, 25.0, 100.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [25.0, 25.0, 100.0]
    return diagnostics


def _accepted_sheet_metal_base_flange_diagnostics(sheet_metal_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled sheet-metal fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = sheet_metal_base_flange_basic_dimension_ids_from_plan(sheet_metal_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "not_requested"
    diagnostics["drawing_annotation_result"] = {
        "status": "not_requested",
        "created_callout_count": 0,
        "direct_hole_callout_created": None,
        "callout_creation_method": None,
        "reason": "controlled_sheet_metal_base_flange_has_no_holes",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["material_status"] = "material_verified"
    diagnostics["material_result"] = {
        "status": "material_verified",
        "requested_material": "Plain Carbon Steel",
        "effective_material": "Plain Carbon Steel",
        "current_material": "Plain Carbon Steel",
        "verified": True,
    }
    diagnostics["sheet_metal_status"] = "sheet_metal_verified"
    diagnostics["sheet_metal_result"] = {
        "status": "sheet_metal_verified",
        "method": "mock_sheet_metal_base_flange",
        "base_flange_created": True,
        "feature_name": "Base-Flange1",
        "thickness_mm": 2.0,
        "bend_radius_mm": 2.0,
        "flat_pattern_result": {
            "status": "flat_pattern_exported",
            "ok": True,
            "format": "dxf",
            "path": "mock.dxf",
            "method": "mock_flat_pattern_dxf",
        },
    }
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [2.0, 80.0, 120.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [2.0, 80.0, 120.0]
    diagnostics["model_geometry_result"]["sheet_metal_thickness_mm"] = 2.0
    diagnostics["model_geometry_result"]["bend_radius_mm"] = 2.0
    return diagnostics


def _accepted_weldment_frame_diagnostics(weldment_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled weldment fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = weldment_frame_basic_dimension_ids_from_plan(weldment_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "not_requested"
    diagnostics["drawing_annotation_result"] = {
        "status": "not_requested",
        "created_callout_count": 0,
        "direct_hole_callout_created": None,
        "callout_creation_method": None,
        "reason": "controlled_weldment_frame_has_no_holes",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["weldment_status"] = "weldment_verified"
    diagnostics["weldment_result"] = {
        "status": "weldment_verified",
        "method": "mock_structural_member_weldment",
        "structural_member_created": True,
        "feature_type": "WeldMemberFeat",
        "body_count": 4,
    }
    diagnostics["cut_list_status"] = "cut_list_verified"
    diagnostics["cut_list_result"] = {
        "status": "cut_list_verified",
        "method": "mock_weldment_cut_list",
        "row_count": 2,
        "columns": ["item", "member_id", "description", "quantity", "length_mm", "profile", "material"],
    }
    diagnostics["model_geometry_result"]["body_count"] = 4
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [50.8, 220.0, 300.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [50.8, 220.0, 300.0]
    return diagnostics


def _accepted_static_simulation_diagnostics(simulation_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled static simulation fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = static_simulation_basic_dimension_ids_from_plan(simulation_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "not_requested"
    diagnostics["drawing_annotation_result"] = {
        "status": "not_requested",
        "created_callout_count": 0,
        "direct_hole_callout_created": None,
        "callout_creation_method": None,
        "reason": "controlled_static_simulation_has_no_holes",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["simulation_status"] = "simulation_verified"
    diagnostics["simulation_result"] = {
        "status": "simulation_verified",
        "method": "mock_static_simulation_fixture",
        "study_type": "static",
        "study_name": "cantilever_static_baseline",
        "solver": "fixture_static_solver",
        "row_count": 3,
        "columns": ["metric", "value", "unit", "status", "limit"],
        "checks": {
            "von_mises_within_limit": True,
            "factor_of_safety_within_limit": True,
            "displacement_within_limit": True,
        },
        "max_von_mises_mpa": 140.625,
        "min_factor_of_safety": 1.777778,
        "max_displacement_mm": 0.84375,
    }
    diagnostics["model_geometry_result"]["body_count"] = 1
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [8.0, 20.0, 120.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [8.0, 20.0, 120.0]
    return diagnostics


def _accepted_sleeve_diagnostics(sleeve_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled sleeve fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = sleeve_basic_dimension_ids_from_plan(sleeve_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "hole_callout_created"
    diagnostics["drawing_annotation_result"] = {
        "status": "hole_callout_created",
        "created_callout_count": 1,
        "direct_hole_callout_created": True,
        "callout_creation_method": "add_hole_callout2",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["material_status"] = "material_verified"
    diagnostics["material_result"] = {
        "status": "material_verified",
        "requested_material": "Plain Carbon Steel",
        "effective_material": "Plain Carbon Steel",
        "current_material": "Plain Carbon Steel",
        "verified": True,
    }
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [40.0, 40.0, 60.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [40.0, 40.0, 60.0]
    return diagnostics


def _accepted_slotted_array_plate_diagnostics(slotted_array_plate_plan: ModelPlan) -> dict[str, object]:
    """Return accepted production diagnostics for the controlled slotted-array plate fixture."""

    diagnostics = _accepted_diagnostics([], "trusted_dimensions_created")
    dimensions = slotted_array_plate_basic_dimension_ids_from_plan(slotted_array_plate_plan)
    diagnostics["thread_model_status"] = "not_requested"
    diagnostics["corner_radius_status"] = "not_requested"
    diagnostics["hole_result"] = {"status": "not_requested", "hole_count": 0}
    diagnostics["drawing_annotation_status"] = "hole_callout_created"
    diagnostics["drawing_annotation_result"] = {
        "status": "hole_callout_created",
        "created_callout_count": 4,
        "direct_hole_callout_created": True,
        "callout_creation_method": "add_hole_callout2",
    }
    diagnostics["drawing_dimension_status"] = "basic_dimensions_created"
    diagnostics["drawing_dimension_result"] = {
        "status": "basic_dimensions_created",
        "created_dimension_count": len(dimensions),
        "created_dimensions": [
            {"id": dimension_id, "method": "mock_display_dimension", "is_display_dimension": True}
            for dimension_id in dimensions
        ],
        "missing_dimensions": [],
        "dimension_layout_status": "trusted_dimensions_created",
    }
    diagnostics["material_status"] = "material_verified"
    diagnostics["material_result"] = {
        "status": "material_verified",
        "requested_material": "Plain Carbon Steel",
        "effective_material": "Plain Carbon Steel",
        "current_material": "Plain Carbon Steel",
        "verified": True,
    }
    diagnostics["model_geometry_result"]["expected_dimensions_mm"] = [10.0, 80.0, 120.0]
    diagnostics["model_geometry_result"]["measured_dimensions_mm"] = [10.0, 80.0, 120.0]
    diagnostics["model_geometry_result"]["expected_slot_length_mm"] = 50.0
    diagnostics["model_geometry_result"]["expected_slot_width_mm"] = 14.0
    diagnostics["model_geometry_result"]["expected_hole_diameter_mm"] = 8.0
    diagnostics["model_geometry_result"]["expected_hole_count"] = 4
    return diagnostics


def _cad_content_fixture_files(tmp_dir: Path) -> dict[str, str]:
    """Create lightweight CAD files for optional export content checks."""

    files: dict[str, str] = {}
    for artifact_id in ("sldprt", "step", "stl", "slddrw", "dwg"):
        path = tmp_dir / f"mock.{artifact_id}"
        path.write_text(f"Mock {artifact_id.upper()} export\n", encoding="utf-8")
        files[artifact_id] = str(path)

    dxf = tmp_dir / "fixture.dxf"
    dxf.write_text(
        "0\nSECTION\n2\nHEADER\n"
        "9\n$ACADVER\n1\nAC1032\n"
        "0\nENDSEC\n"
        "0\nSECTION\n2\nENTITIES\n"
        + ("0\nLINE\n8\n0\n10\n0.0\n20\n0.0\n11\n1.0\n21\n1.0\n" * 24)
        + "0\nENDSEC\n0\nEOF\n",
        encoding="latin-1",
    )
    files["dxf"] = str(dxf)

    iges = tmp_dir / "fixture.igs"
    iges.write_text(
        "IGES fixture export generated by schema regression\n"
        + (" " * 1100)
        + "\nT      1\n",
        encoding="latin-1",
    )
    files["iges"] = str(iges)

    parasolid_text = tmp_dir / "fixture.x_t"
    parasolid_text.write_text(
        "Parasolid transmit schema fixture\n" + ("body\n" * 260),
        encoding="latin-1",
    )
    files["x_t"] = str(parasolid_text)

    parasolid_binary = tmp_dir / "fixture.x_b"
    parasolid_binary.write_bytes(b"\x00\x00parasolid binary fixture" + b"\x00" * 1400)
    files["x_b"] = str(parasolid_binary)
    return files


def _accepted_diagnostics(
    required_dimensions: list[str],
    dimension_layout_status: str,
    *,
    require_direct_hole_callout: bool = False,
    direct_hole_callout_created: bool = True,
    proxy_dimension_id: str | None = None,
    radius_method: str = "AddRadialDimension2",
    material_status: str = "not_requested",
    current_material: str | None = None,
    effective_material: str | None = None,
    custom_property_status: str = "not_requested",
    current_custom_properties: dict[str, str] | None = None,
    model_geometry_status: str = "geometry_verified",
    measured_dimensions_mm: list[float] | None = None,
    mass_property_status: str = "mass_properties_verified",
    mass_kg: float = 0.821,
    volume_m3: float = 0.0001046,
    pdf_semantic_status: str = "pdf_semantic_content_verified",
    pdf_semantic_missing: list[str] | None = None,
    cad_content_status: str = "cad_artifacts_verified",
    drawing_view_status: str = "created",
    drawing_view_roles: list[str] | None = None,
    drawing_view_errors: list[str] | None = None,
    cleanup_status: str = "completed",
    cleanup_verification_status: str = "verified",
    document_state_audit_status: str = "verified_no_run_documents_open",
    document_state_after_cleanup_run_created_open_count: int | None = 0,
) -> dict[str, object]:
    """Return a compact accepted diagnostics fixture for production acceptance checks."""

    created_dimensions = [
        {
            "id": dimension_id,
            "method": radius_method if dimension_id.startswith("corner_radius_") else "fixture",
            "is_display_dimension": True,
            **({"proxy_dimension": True} if dimension_id == proxy_dimension_id else {}),
        }
        for dimension_id in required_dimensions
    ]
    view_roles = drawing_view_roles or ["front", "top", "right", "isometric"]
    required_view_roles = ["front", "top", "right", "isometric"]
    return {
        "preflight_status": "ready",
        "require_direct_hole_callout": require_direct_hole_callout,
        "thread_model_status": "holewizard_threaded_hole",
        "corner_radius_status": "fillet_feature",
        "drawing_view_status": drawing_view_status,
        "drawing_view_result": {
            "status": drawing_view_status,
            "views": [
                {"role": role, "name": f"*{role.title()}", "x": 0.1, "y": 0.1}
                for role in view_roles
            ],
            "created_count": len(view_roles),
            "required_roles": required_view_roles,
            "missing_roles": [role for role in required_view_roles if role not in set(view_roles)],
            "errors": drawing_view_errors or [],
        },
        "drawing_annotation_status": "hole_callout_created",
        "drawing_annotation_result": {
            "created_callout_count": 1,
            "callout_creation_method": "add_hole_callout2"
            if direct_hole_callout_created
            else "insert_model_annotations3",
            "direct_hole_callout_created": direct_hole_callout_created,
        },
        "drawing_dimension_status": "basic_dimensions_created",
        "drawing_dimension_result": {
            "created_dimension_count": len(required_dimensions),
            "created_dimensions": created_dimensions,
            "missing_dimensions": [],
            "dimension_layout_status": dimension_layout_status,
        },
        "drawing_metadata_note_result": {
            "status": "metadata_note_created" if custom_property_status != "not_requested" else "not_requested",
            "method": "fixture_note" if custom_property_status != "not_requested" else None,
        },
        "artifact_validation_result": {"ok": True, "status": "artifacts_ready"},
        "artifact_content_result": {
            "ok": pdf_semantic_status == "pdf_semantic_content_verified"
            and cad_content_status == "cad_artifacts_verified",
            "status": "content_ready"
            if pdf_semantic_status == "pdf_semantic_content_verified"
            and cad_content_status == "cad_artifacts_verified"
            else "content_failed",
            "cad_content_result": {
                "ok": cad_content_status == "cad_artifacts_verified",
                "status": cad_content_status,
                "failed": []
                if cad_content_status == "cad_artifacts_verified"
                else [{"id": "step", "status": "step_invalid", "path": "mock.step"}],
            },
            "pdf_semantic_content_result": {
                "ok": pdf_semantic_status == "pdf_semantic_content_verified",
                "status": pdf_semantic_status,
                "matches": {},
                "missing": pdf_semantic_missing or [],
            },
        },
        "cleanup_result": {
            "enabled": True,
            "status": cleanup_status,
            "cleanup_verification_status": cleanup_verification_status,
        },
        "document_state_audit_result": {
            "status": document_state_audit_status,
            "ok": document_state_audit_status == "verified_no_run_documents_open",
            "before_cleanup_run_created_open_count": 0,
            "after_cleanup_run_created_open_count": document_state_after_cleanup_run_created_open_count,
            "after_cleanup_snapshot_status": "verified_by_tracked_candidates",
            "failure_reason": None
            if document_state_audit_status == "verified_no_run_documents_open"
            else "fixture failure",
            "phases": [
                "after_cleanup",
                "after_transaction",
                "before_cleanup",
                "before_transaction",
            ],
        },
        "hole_result": {"hole_count": 4},
        "model_geometry_status": model_geometry_status,
        "model_geometry_result": {
            "status": model_geometry_status,
            "body_count": 1,
            "expected_dimensions_mm": [12.0, 90.0, 140.0],
            "measured_dimensions_mm": measured_dimensions_mm or [12.0, 90.0, 140.0],
            "max_error_mm": 0.0 if model_geometry_status == "geometry_verified" else 10.0,
        },
        "mass_property_status": mass_property_status,
        "mass_property_result": {
            "status": mass_property_status,
            "mass_kg": mass_kg,
            "volume_m3": volume_m3,
            "surface_area_m2": 0.03,
            "checks": {
                "positive_mass": mass_kg > 0,
                "positive_volume": volume_m3 > 0,
            },
            "failure_reason": None if mass_property_status == "mass_properties_verified" else "fixture failure",
        },
        "material_status": material_status,
        "material_result": {
            "status": material_status,
            "requested_material": "Plain Carbon Steel" if material_status != "not_requested" else None,
            "effective_material": effective_material,
            "current_material": current_material,
            "verified": material_status == "material_verified",
        },
        "custom_property_status": custom_property_status,
        "custom_property_result": {
            "status": custom_property_status,
            "requested_properties": {
                "PartNo": "MP-120-080-M8",
                "Revision": "A",
                "Description": "Mounting plate smoke fixture",
            }
            if custom_property_status != "not_requested"
            else {},
            "current_properties": current_custom_properties or {},
            "verified": custom_property_status == "custom_properties_verified",
        },
        "drawing_profile": DrawingProfile().to_dict(),
    }


def _accepted_bom_assembly_diagnostics() -> dict[str, object]:
    """Return accepted diagnostics for the controlled assembly+BOM fixture."""

    diagnostics = _accepted_diagnostics([], "not_requested", require_direct_hole_callout=False)
    diagnostics.update(
        {
            "thread_model_status": "not_requested",
            "corner_radius_status": "not_requested",
            "drawing_annotation_status": "not_requested",
            "drawing_annotation_result": {
                "status": "not_requested",
                "created_callout_count": 0,
                "direct_hole_callout_created": None,
                "callout_creation_method": None,
                "reason": "controlled_bom_assembly_uses_bom_evidence",
            },
            "drawing_dimension_status": "not_requested",
            "drawing_dimension_result": {
                "created_dimension_count": 0,
                "created_dimensions": [],
                "missing_dimensions": [],
                "dimension_layout_status": "not_requested",
            },
            "hole_result": {"hole_count": 0},
            "model_geometry_status": "not_requested",
            "model_geometry_result": {
                "status": "not_requested",
                "reason": "assembly workflow uses assembly_result",
                "body_count": 0,
            },
            "mass_property_status": "not_requested",
            "mass_property_result": {
                "status": "not_requested",
                "reason": "assembly workflow uses bom_result",
                "mass_kg": None,
                "volume_m3": None,
                "surface_area_m2": None,
            },
            "assembly_result": {
                "status": "assembly_verified",
                "component_instance_count": 3,
                "component_definitions": ["plate_a", "spacer_a"],
                "mate_count": 0,
            },
            "bom_result": {
                "status": "bom_verified",
                "row_count": 2,
                "columns": ["item", "component_id", "part_number", "description", "quantity", "material"],
                "rows": [
                    {
                        "item": 1,
                        "component_id": "plate_a",
                        "part_number": "ASM-PLATE-A",
                        "description": "Base plate",
                        "quantity": 1,
                        "material": "Plain Carbon Steel",
                    },
                    {
                        "item": 2,
                        "component_id": "spacer_a",
                        "part_number": "ASM-SPACER-A",
                        "description": "Center spacer",
                        "quantity": 2,
                        "material": "Plain Carbon Steel",
                    },
                ],
            },
        }
    )
    return diagnostics


if __name__ == "__main__":
    raise SystemExit(main())

