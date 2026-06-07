"""Production repair-action routing for trusted SolidWorks MCP runs."""

from __future__ import annotations

from typing import Any


REPAIR_ACTION_TEMPLATES: dict[str, dict[str, Any]] = {
    "execution_ok": {
        "severity": "blocker",
        "title": "Repair execution failure before reviewing CAD artifacts.",
        "next_step": "Open execution_report.json, inspect failure_class, failed step details, and events.jsonl, then rerun after the root execution error is fixed.",
        "evidence_fields": ["failure_class", "message", "step_results", "events.jsonl"],
    },
    "trusted_controlled_workflow": {
        "severity": "blocker",
        "title": "Use a controlled trusted workflow for production.",
        "next_step": "Rewrite the plan to contain exactly one trusted geometry operation, create_mounting_plate, create_center_hole_flange, create_center_hole_plate, create_bracket, create_end_cap, create_mounting_block, create_shaft, create_washer, create_sleeve, or create_slotted_array_plate, plus allowed metadata/drawing/export operations; otherwise run it as a clearly labelled non-production experiment.",
        "evidence_fields": ["summary.trusted_workflow_status", "summary.trusted_workflow.untrusted_operations"],
    },
    "preflight_ready": {
        "severity": "blocker",
        "title": "Resolve preflight blockers before creating SolidWorks documents.",
        "next_step": "Run preflight_environment with the same plan and fix failed checks such as templates, output directory, cleanup policy, direct callout policy, or trusted workflow policy.",
        "evidence_fields": ["diagnostics.preflight_result.failures", "diagnostics.preflight_result.checks"],
    },
    "trusted_workflow_policy": {
        "severity": "blocker",
        "title": "Rewrite the plan into a trusted production workflow.",
        "next_step": "Use a controlled create_mounting_plate, create_center_hole_flange, create_center_hole_plate, create_bracket, create_end_cap, create_mounting_block, create_shaft, create_washer, create_sleeve, or create_slotted_array_plate workflow for production, or keep SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW=0 only for a clearly labelled non-production experiment.",
        "evidence_fields": ["diagnostics.preflight_result.checks.trusted_workflow_policy"],
    },
    "cleanup_policy": {
        "severity": "blocker",
        "title": "Enable run-created document cleanup before execution.",
        "next_step": "Set SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN=1 so confirmed execution cannot leave run-created SolidWorks documents open.",
        "evidence_fields": ["diagnostics.preflight_result.checks.cleanup_policy"],
    },
    "direct_hole_callout_policy": {
        "severity": "blocker",
        "title": "Require direct selected-edge Hole Callout creation.",
        "next_step": "Set SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT=1 for real SolidWorks production runs before creating new documents.",
        "evidence_fields": ["diagnostics.preflight_result.checks.direct_hole_callout_policy"],
    },
    "forced_preflight_failure": {
        "severity": "blocker",
        "title": "Disable forced preflight failure.",
        "next_step": "Unset SOLIDWORKS_MCP_FORCE_PREFLIGHT_FAILURE before production execution.",
        "evidence_fields": ["diagnostics.preflight_result.checks.forced_preflight_failure"],
    },
    "trusted_thread_model": {
        "severity": "blocker",
        "title": "Create real threaded holes instead of geometry-only cuts.",
        "next_step": "Review hole_result and macro_result, fix HoleWizard parameters or the controlled macro trust path, then rerun until thread_model_status is holewizard_threaded_hole or macro_threaded_hole.",
        "evidence_fields": ["summary.thread_model_status", "diagnostics.hole_result"],
    },
    "corner_radius_feature": {
        "severity": "blocker",
        "title": "Create real SolidWorks fillet features for controlled rounded corners.",
        "next_step": "Inspect corner_radius_result and outer-edge selection attempts, then rerun until corner_radius_status is fillet_feature for workflows that require rounded corners.",
        "evidence_fields": ["summary.corner_radius_status", "diagnostics.corner_radius_result"],
    },
    "drawing_standard_views_created": {
        "severity": "blocker",
        "title": "Create all required standard drawing views.",
        "next_step": "Repair CreateDrawViewFromModelView3 view creation so front, top, right, and isometric drawing views are created before dimensions, callouts, and drawing exports.",
        "evidence_fields": [
            "summary.drawing_view_status",
            "summary.drawing_view_roles",
            "summary.missing_drawing_view_roles",
            "summary.drawing_view_errors",
            "diagnostics.drawing_view_result",
        ],
    },
    "hole_callouts_created": {
        "severity": "blocker",
        "title": "Create a real drawing Hole Callout.",
        "next_step": "Review drawing_annotation_result attempts, top-view handle selection, visible hole edges, and AddHoleCallout2 return values; do not accept hole tables or notes as success.",
        "evidence_fields": ["summary.drawing_annotation_status", "diagnostics.drawing_annotation_result"],
    },
    "direct_hole_callouts_created": {
        "severity": "blocker",
        "title": "Use the direct selected-edge AddHoleCallout2 path.",
        "next_step": "Enable SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT=1 and repair selected-edge callout creation instead of relying on InsertModelAnnotations fallback.",
        "evidence_fields": ["summary.direct_hole_callout_created", "diagnostics.drawing_annotation_result"],
    },
    "basic_dimensions_created": {
        "severity": "blocker",
        "title": "Create all required trusted drawing dimensions.",
        "next_step": "Review drawing_dimension_result missing_dimensions and entity selection attempts, then rerun until every workflow-required production dimension is created.",
        "evidence_fields": ["summary.missing_dimensions", "diagnostics.drawing_dimension_result"],
    },
    "trusted_basic_dimensions": {
        "severity": "blocker",
        "title": "Replace proxy dimensions with trusted SolidWorks display dimensions.",
        "next_step": "Repair selected drawing-view edge/point selection so dimensions are created by real SolidWorks display-dimension APIs instead of proxy annotations or untrusted layout fallbacks.",
        "evidence_fields": ["summary.dimension_layout_status", "summary.proxy_dimensions", "summary.non_radial_radius_dimensions"],
    },
    "material_verified": {
        "severity": "blocker",
        "title": "Verify requested material by SolidWorks readback.",
        "next_step": "Inspect material_result attempts, material database names, and effective_material aliasing; production requires current_material to match the request or a verified controlled alias.",
        "evidence_fields": ["summary.requested_material", "summary.effective_material", "summary.current_material", "diagnostics.material_result"],
    },
    "custom_properties_verified": {
        "severity": "blocker",
        "title": "Verify requested custom properties by SolidWorks readback.",
        "next_step": "Inspect custom_property_result current_properties and write attempts, then repair the CustomPropertyManager or legacy custom info path.",
        "evidence_fields": ["summary.requested_custom_properties", "summary.current_custom_properties", "diagnostics.custom_property_result"],
    },
    "model_geometry_verified": {
        "severity": "blocker",
        "title": "Verify generated model geometry against the controlled plan.",
        "next_step": "Inspect model_geometry_result measured_dimensions_mm and max_error_mm, then repair unit scale, feature creation, or body readback before accepting exports.",
        "evidence_fields": ["summary.model_geometry_status", "summary.model_geometry_measured_dimensions_mm", "summary.model_geometry_expected_dimensions_mm"],
    },
    "mass_properties_verified": {
        "severity": "blocker",
        "title": "Verify positive SolidWorks mass properties.",
        "next_step": "Inspect mass_property_result attempts and material/body state; production requires positive mass_kg and volume_m3.",
        "evidence_fields": ["summary.mass_property_status", "summary.mass_kg", "summary.volume_m3", "diagnostics.mass_property_result"],
    },
    "artifacts_ready": {
        "severity": "blocker",
        "title": "Regenerate missing or empty output artifacts.",
        "next_step": "Inspect artifact_validation_result missing_or_empty entries and rerun exports/previews until every required artifact exists and is non-empty.",
        "evidence_fields": ["diagnostics.artifact_validation_result", "output_files", "preview_files"],
    },
    "artifact_content_ready": {
        "severity": "blocker",
        "title": "Repair unreadable or placeholder artifact content.",
        "next_step": "Inspect artifact_content_result failed entries, then rerun export or preview generation until CAD/PDF/PNG content checks pass.",
        "evidence_fields": ["summary.artifact_content_status", "diagnostics.artifact_content_result"],
    },
    "cad_artifact_content": {
        "severity": "blocker",
        "title": "Repair CAD exchange/native file content.",
        "next_step": "Inspect cad_content_failed and export_result; regenerate STEP/STL/DWG/native files or requested DXF/IGES/Parasolid outputs until format signatures validate.",
        "evidence_fields": ["summary.cad_content_status", "summary.cad_content_failed", "summary.export_failed"],
    },
    "drawing_pdf_semantic_content": {
        "severity": "blocker",
        "title": "Repair drawing PDF semantic content.",
        "next_step": "Inspect pdf_semantic_content_missing and ensure required thread, dimension, and metadata text appears in the exported drawing PDF.",
        "evidence_fields": ["summary.pdf_semantic_content_status", "summary.pdf_semantic_content_missing"],
    },
    "required_output_files": {
        "severity": "blocker",
        "title": "Export all required production formats.",
        "next_step": "Rerun export_outputs or the full smoke workflow until SLDPRT, STEP, STL, SLDDRW, PDF, and DWG are all present.",
        "evidence_fields": ["summary.output_files", "expected.required_output_files"],
    },
    "requested_output_files": {
        "severity": "blocker",
        "title": "Repair requested optional export failures.",
        "next_step": "Inspect export_result.failed and missing_requested_output_files, then fix SaveAs support or remove unsupported requested formats from the production plan.",
        "evidence_fields": ["summary.missing_requested_output_files", "summary.export_failed"],
    },
    "required_preview_files": {
        "severity": "blocker",
        "title": "Generate all required standard-view previews.",
        "next_step": "Repair preview generation until front, top, right, and isometric preview files exist and pass content checks.",
        "evidence_fields": ["summary.preview_files", "expected.required_preview_files"],
    },
    "cleanup_completed": {
        "severity": "blocker",
        "title": "Close run-created SolidWorks documents after export.",
        "next_step": "Enable SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN=1 and repair cleanup_after_run so the run-created part and drawing are closed after outputs are captured.",
        "evidence_fields": ["summary.cleanup_status", "diagnostics.cleanup_result"],
    },
    "cleanup_verified": {
        "severity": "blocker",
        "title": "Verify run-created SolidWorks documents are closed.",
        "next_step": "Repair cleanup verification with GetOpenDocumentByName or a supported equivalent so completed cleanup reports cleanup_verification_status=verified.",
        "evidence_fields": ["summary.cleanup_verification_status", "diagnostics.cleanup_result"],
    },
    "document_state_audit_verified": {
        "severity": "blocker",
        "title": "Verify no run-created SolidWorks documents remain open.",
        "next_step": "Repair document_state_snapshot and cleanup_after_run so after_cleanup reports run_created_open_count=0 and document_state_audit_result.status=verified_no_run_documents_open.",
        "evidence_fields": [
            "summary.document_state_audit_status",
            "summary.document_state_after_cleanup_run_created_open_count",
            "diagnostics.document_state_audit_result",
            "diagnostics.document_state_after_cleanup",
        ],
    },
    "offline_run_diagnosis": {
        "severity": "blocker",
        "title": "Repair the offline handoff diagnosis failure.",
        "next_step": "Run diagnose_run on the returned run_dir and fix artifact integrity, event log, delivery manifest, environment snapshot, or production verdict issues before handoff.",
        "evidence_fields": ["offline_diagnosis", "delivery_manifest.json", "artifacts.json", "events.jsonl", "environment.json"],
    },
    "offline_artifact_integrity": {
        "severity": "blocker",
        "title": "Repair offline artifact integrity.",
        "next_step": "Inspect diagnose_run missing_artifacts and artifacts.json hash/path entries, then regenerate or re-index changed files.",
        "evidence_fields": ["offline_diagnosis.missing_artifacts", "artifacts.json"],
    },
    "offline_event_log": {
        "severity": "blocker",
        "title": "Repair offline event-log integrity.",
        "next_step": "Inspect diagnose_run event_log_issues and unrecovered failed events, then fix run logging or rerun the workflow.",
        "evidence_fields": ["offline_diagnosis.event_log_issues", "events.jsonl"],
    },
    "offline_delivery_manifest": {
        "severity": "blocker",
        "title": "Repair delivery manifest consistency.",
        "next_step": "Inspect diagnose_run delivery_manifest_issues and regenerate delivery_manifest.json so it matches the report and artifact index.",
        "evidence_fields": ["offline_diagnosis.delivery_manifest_issues", "delivery_manifest.json"],
    },
    "offline_environment": {
        "severity": "blocker",
        "title": "Repair environment snapshot evidence.",
        "next_step": "Inspect diagnose_run environment_issues and ensure accepted real runs prove cleanup, direct callout enforcement, trusted workflow enforcement, run id, adapter, and run_dir.",
        "evidence_fields": ["offline_diagnosis.environment_issues", "environment.json"],
    },
}


def build_repair_actions(
    failures: list[Any] | tuple[Any, ...] | None,
    summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return stable client-facing repair actions for production failures."""

    summary = summary if isinstance(summary, dict) else {}
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for failure in failures or []:
        failure_id = str(failure)
        if failure_id in seen:
            continue
        seen.add(failure_id)
        template = REPAIR_ACTION_TEMPLATES.get(failure_id, _default_repair_action(failure_id))
        action = {
            "id": failure_id,
            "severity": template["severity"],
            "title": template["title"],
            "next_step": template["next_step"],
            "evidence_fields": list(template.get("evidence_fields", [])),
        }
        details = _repair_action_details(failure_id, summary)
        if details:
            action["details"] = details
        actions.append(action)
    return actions


def _default_repair_action(failure_id: str) -> dict[str, Any]:
    """Return a conservative fallback repair action for an unknown failure id."""

    return {
        "severity": "blocker",
        "title": f"Repair production gate failure: {failure_id}.",
        "next_step": "Inspect production_acceptance_result.checks, summary, diagnostics, and events.jsonl for the failed gate, then rerun the trusted smoke workflow.",
        "evidence_fields": ["diagnostics.production_acceptance_result", "events.jsonl"],
    }


def _repair_action_details(failure_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Attach compact failure-specific evidence copied from the acceptance summary."""

    detail_fields = {
        "trusted_controlled_workflow": ("trusted_workflow_status", "trusted_workflow"),
        "trusted_thread_model": ("thread_model_status", "hole_count"),
        "corner_radius_feature": ("corner_radius_status",),
        "drawing_standard_views_created": (
            "drawing_view_status",
            "drawing_view_roles",
            "missing_drawing_view_roles",
        ),
        "hole_callouts_created": ("drawing_annotation_status", "callout_count", "callout_creation_method"),
        "direct_hole_callouts_created": ("direct_hole_callout_created", "callout_creation_method"),
        "basic_dimensions_created": ("dimension_count", "missing_dimensions"),
        "trusted_basic_dimensions": ("dimension_layout_status", "proxy_dimensions", "non_radial_radius_dimensions"),
        "material_verified": ("requested_material", "effective_material", "current_material", "material_status"),
        "custom_properties_verified": (
            "requested_custom_properties",
            "current_custom_properties",
            "custom_property_status",
        ),
        "model_geometry_verified": (
            "model_geometry_status",
            "model_geometry_max_error_mm",
            "model_geometry_measured_dimensions_mm",
            "model_geometry_expected_dimensions_mm",
        ),
        "mass_properties_verified": ("mass_property_status", "mass_kg", "volume_m3"),
        "artifact_content_ready": ("artifact_content_status",),
        "cad_artifact_content": ("cad_content_status", "cad_content_failed", "export_failed"),
        "drawing_pdf_semantic_content": ("pdf_semantic_content_status", "pdf_semantic_content_missing"),
        "required_output_files": ("output_files",),
        "requested_output_files": ("missing_requested_output_files", "export_failed"),
        "required_preview_files": ("preview_files",),
        "cleanup_completed": ("cleanup_status",),
        "cleanup_verified": ("cleanup_status", "cleanup_verification_status"),
        "document_state_audit_verified": (
            "document_state_audit_status",
            "document_state_after_cleanup_run_created_open_count",
        ),
    }.get(failure_id, ())
    return {
        field: summary.get(field)
        for field in detail_fields
        if field in summary
    }
