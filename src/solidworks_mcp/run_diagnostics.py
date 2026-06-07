"""Offline diagnosis helpers for SolidWorks MCP run directories."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def diagnose_run_directory(
    run_dir: str | Path,
    *,
    tail: int = 12,
    summary_only: bool = False,
) -> dict[str, Any]:
    """Read a run directory and return a production-verdict diagnosis payload."""

    run_path = Path(run_dir).expanduser().resolve()
    report = _read_json(run_path / "execution_report.json")
    artifacts = _read_json(run_path / "artifacts.json")
    delivery_manifest = _read_json(run_path / "delivery_manifest.json")
    environment = _read_json(run_path / "environment.json")
    events = _read_jsonl(run_path / "events.jsonl")

    missing_files = [
        *_artifact_index_structure_issues(artifacts, report, run_path),
        *_missing_artifacts(artifacts, run_path),
    ]
    artifact_integrity_status = "verified" if artifacts and not missing_files else "failed"
    if not artifacts:
        artifact_integrity_status = "missing_index"
    manifest_result = _validate_delivery_manifest(
        run_path / "delivery_manifest.json",
        delivery_manifest,
        report,
        artifacts,
        run_path,
    )
    step_results = report.get("step_results", []) if report else []
    failed_steps = [step for step in step_results if not step.get("ok", True)]
    diagnostics = report.get("diagnostics", {}) if report else {}
    stored_acceptance = diagnostics.get("production_acceptance_result", {}) if isinstance(diagnostics, dict) else {}
    acceptance_recheck = _current_acceptance_recheck(report, diagnostics)
    acceptance = acceptance_recheck.get("acceptance")
    acceptance = acceptance if isinstance(acceptance, dict) else stored_acceptance
    environment_result = _validate_environment_snapshot(
        run_path / "environment.json",
        environment,
        report,
        artifacts,
        acceptance,
    )
    acceptance_ok = acceptance.get("ok") if isinstance(acceptance, dict) else None
    failed_events, recovered_probe_events, recovered_export_events, recovered_preflight_events = _classify_failed_events(
        events,
        bool(acceptance_ok),
        report,
    )
    event_log_issues = _event_log_integrity_issues(events, report)
    event_log_status = "verified" if not failed_events and not event_log_issues else "failed"
    acceptance_summary = _acceptance_summary(diagnostics, acceptance)
    trusted_ok = bool(
        report
        and report.get("ok")
        and artifact_integrity_status == "verified"
        and manifest_result.get("status") == "verified"
        and environment_result.get("status") == "verified"
        and event_log_status == "verified"
    )
    if acceptance_ok is not None:
        trusted_ok = trusted_ok and bool(acceptance_ok)

    summary = {
        "run_dir": str(run_path),
        "ok": trusted_ok,
        "execution_ok": report.get("ok") if report else False,
        "production_acceptance_status": acceptance.get("status") if isinstance(acceptance, dict) else None,
        "production_acceptance_failures": acceptance.get("failures") if isinstance(acceptance, dict) else None,
        "stored_production_acceptance_status": stored_acceptance.get("status")
        if isinstance(stored_acceptance, dict)
        else None,
        "stored_production_acceptance_failures": stored_acceptance.get("failures")
        if isinstance(stored_acceptance, dict)
        else None,
        "current_acceptance_recheck": _compact_acceptance_recheck(acceptance_recheck),
        "repair_actions": acceptance.get("repair_actions") or [] if isinstance(acceptance, dict) else [],
        "acceptance_summary": acceptance_summary,
        "plan_name": report.get("plan_name") if report else None,
        "failure_class": report.get("failure_class") if report else "missing_report",
        "message": report.get("message") if report else "execution_report.json is missing or invalid",
        "adapter": report.get("adapter") if report else environment.get("adapter"),
        "debug_level": environment.get("debug_level"),
        "report_file": report.get("report_file") or str(run_path / "execution_report.json"),
        "artifacts_file": report.get("artifacts_file") or str(run_path / "artifacts.json"),
        "delivery_manifest_file": report.get("delivery_manifest_file")
        or delivery_manifest.get("delivery_manifest_file")
        or str(run_path / "delivery_manifest.json"),
        "delivery_manifest_status": manifest_result.get("status"),
        "delivery_manifest_issues": manifest_result.get("issues", []),
        "delivery_handoff_summary": delivery_manifest.get("handoff_summary")
        if isinstance(delivery_manifest.get("handoff_summary"), dict)
        else None,
        "environment_status": environment_result.get("status"),
        "environment_issues": environment_result.get("issues", []),
        "failed_steps": failed_steps,
        "diagnostics": diagnostics,
        "missing_artifacts": missing_files,
        "artifact_integrity_status": artifact_integrity_status,
        "event_log_status": event_log_status,
        "event_log_issues": event_log_issues,
        "failed_event_count": len(failed_events),
        "recovered_probe_event_count": len(recovered_probe_events),
        "recovered_export_event_count": len(recovered_export_events),
        "recovered_preflight_event_count": len(recovered_preflight_events),
        "failed_events": failed_events,
        "recovered_probe_events": recovered_probe_events,
        "recovered_export_events": recovered_export_events,
        "recovered_preflight_events": recovered_preflight_events,
        "last_events": events[-tail:],
        "repro_command": report.get("repro_command") if report else None,
    }
    return _summary_only_payload(summary) if summary_only else summary


def diagnose_run_collection(
    root_dir: str | Path,
    *,
    tail: int = 12,
    summary_only: bool = True,
    max_runs: int | None = 0,
) -> dict[str, Any]:
    """Diagnose completed run directories below a root without touching SolidWorks."""

    root_path = Path(root_dir).expanduser().resolve()
    scan_limit = _normalise_scan_limit(max_runs)
    run_paths, truncated = _discover_run_directories(root_path, scan_limit)
    results: list[dict[str, Any]] = []
    for run_path in run_paths:
        try:
            results.append(diagnose_run_directory(run_path, tail=tail, summary_only=True))
        except Exception as exc:
            results.append(
                {
                    "run_dir": str(run_path),
                    "ok": False,
                    "failure_class": "diagnosis_error",
                    "message": str(exc),
                }
            )

    summary = _collection_summary(root_path, results, scan_limit, truncated)
    summary["results"] = [_collection_result_payload(result) for result in results] if summary_only else [
        diagnose_run_directory(result["run_dir"], tail=tail, summary_only=False)
        if result.get("failure_class") != "diagnosis_error"
        else result
        for result in results
    ]
    return summary


def _summary_only_payload(summary: dict[str, Any]) -> dict[str, Any]:
    """Return the small CI/client-oriented diagnosis payload."""

    return {
        "run_dir": summary.get("run_dir"),
        "ok": summary.get("ok"),
        "execution_ok": summary.get("execution_ok"),
        "production_acceptance_status": summary.get("production_acceptance_status"),
        "production_acceptance_failures": summary.get("production_acceptance_failures"),
        "stored_production_acceptance_status": summary.get("stored_production_acceptance_status"),
        "stored_production_acceptance_failures": summary.get("stored_production_acceptance_failures"),
        "current_acceptance_recheck": summary.get("current_acceptance_recheck"),
        "repair_actions": summary.get("repair_actions", []),
        "plan_name": summary.get("plan_name"),
        "failure_class": summary.get("failure_class"),
        "message": summary.get("message"),
        "adapter": summary.get("adapter"),
        "report_file": summary.get("report_file"),
        "artifacts_file": summary.get("artifacts_file"),
        "missing_artifacts": summary.get("missing_artifacts"),
        "artifact_integrity_status": summary.get("artifact_integrity_status"),
        "event_log_status": summary.get("event_log_status"),
        "event_log_issues": summary.get("event_log_issues"),
        "failed_event_count": summary.get("failed_event_count"),
        "recovered_probe_event_count": summary.get("recovered_probe_event_count"),
        "recovered_export_event_count": summary.get("recovered_export_event_count"),
        "recovered_preflight_event_count": summary.get("recovered_preflight_event_count"),
        "delivery_manifest_file": summary.get("delivery_manifest_file"),
        "delivery_manifest_status": summary.get("delivery_manifest_status"),
        "delivery_manifest_issues": summary.get("delivery_manifest_issues"),
        "delivery_handoff_summary": _compact_handoff_summary(summary.get("delivery_handoff_summary")),
        "environment_status": summary.get("environment_status"),
        "environment_issues": summary.get("environment_issues"),
        "acceptance_summary": summary.get("acceptance_summary"),
        "repro_command": summary.get("repro_command"),
    }


def _compact_handoff_summary(value: Any) -> dict[str, Any] | None:
    """Return compact handoff fields suitable for CLI/MCP diagnosis payloads."""

    if not isinstance(value, dict):
        return None
    artifact_counts = value.get("artifact_counts")
    artifact_counts = artifact_counts if isinstance(artifact_counts, dict) else {}
    key_statuses = value.get("key_statuses")
    key_statuses = key_statuses if isinstance(key_statuses, dict) else {}
    return {
        "delivery_status": value.get("delivery_status"),
        "delivery_ok": value.get("delivery_ok"),
        "production_failures": value.get("production_failures", []),
        "repair_actions": value.get("repair_actions", []),
        "run_id": value.get("run_id"),
        "plan_name": value.get("plan_name"),
        "adapter": value.get("adapter"),
        "key_statuses": key_statuses,
        "artifact_counts": artifact_counts,
        "diagnose_command": value.get("diagnose_command"),
        "repro_command": value.get("repro_command"),
    }


def _current_acceptance_recheck(report: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Verify a stored production verdict satisfies the current code-level gate set."""

    stored_acceptance = diagnostics.get("production_acceptance_result") if isinstance(diagnostics, dict) else None
    if not isinstance(stored_acceptance, dict) or not stored_acceptance:
        return {
            "status": "not_available",
            "source": "missing_acceptance",
            "acceptance": stored_acceptance,
            "failure_reason": "production_acceptance_result is missing.",
        }
    if not _acceptance_is_accepted(stored_acceptance):
        return {
            "acceptance": stored_acceptance,
            "current_failures": stored_acceptance.get("failures", []),
            "current_status": stored_acceptance.get("status"),
            "source": "stored_rejected",
            "status": "not_required",
            "stored_status": stored_acceptance.get("status"),
        }
    failures = _current_acceptance_gate_failures(stored_acceptance, diagnostics)
    if failures:
        current_acceptance = _current_acceptance_gate_failure(stored_acceptance, failures)
        return {
            "status": "failed",
            "source": "current_gate_set",
            "acceptance": current_acceptance,
            "stored_status": stored_acceptance.get("status"),
            "current_status": current_acceptance.get("status"),
            "current_failures": current_acceptance.get("failures", []),
        }
    return {
        "status": "verified",
        "source": "current_gate_set",
        "acceptance": stored_acceptance,
        "stored_status": stored_acceptance.get("status"),
        "current_status": stored_acceptance.get("status"),
        "current_failures": [],
    }


def _current_acceptance_gate_failure(
    stored_acceptance: Any,
    failures: list[str],
) -> dict[str, Any]:
    """Return a rejection when an old accepted run lacks current gates or evidence."""

    stored_summary = stored_acceptance.get("summary", {}) if isinstance(stored_acceptance, dict) else {}
    summary = dict(stored_summary) if isinstance(stored_summary, dict) else {}
    summary["current_acceptance_recheck_status"] = "failed_current_gate_set"
    acceptance = {
        "status": "rejected",
        "ok": False,
        "checks": {failure: False for failure in failures},
        "failures": failures,
        "repair_actions": _current_gate_repair_actions(failures, summary),
        "summary": summary,
    }
    return acceptance


def _current_acceptance_gate_failures(
    stored_acceptance: dict[str, Any],
    diagnostics: dict[str, Any],
) -> list[str]:
    """Return current production gate ids that are missing, false, or lack required evidence."""

    checks = stored_acceptance.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    failures = [
        gate_id
        for gate_id in _current_production_gate_ids(stored_acceptance)
        if checks.get(gate_id) is not True
    ]
    if "drawing_standard_views_created" not in failures:
        drawing_view_result = diagnostics.get("drawing_view_result") if isinstance(diagnostics, dict) else None
        if not _drawing_standard_view_evidence_ok(drawing_view_result):
            failures.append("drawing_standard_views_created")
    no_hole_workflow = _stored_acceptance_has_no_hole_callout_requirement(stored_acceptance)
    evidence_checks = {
        "hole_callouts_created": no_hole_workflow or _hole_callout_evidence_ok(diagnostics),
        "direct_hole_callouts_created": no_hole_workflow or _direct_hole_callout_evidence_ok(diagnostics),
        "basic_dimensions_created": _basic_dimension_evidence_ok(diagnostics),
        "trusted_basic_dimensions": _trusted_dimension_evidence_ok(diagnostics),
        "basic_dimensions_not_proxy": _basic_dimensions_not_proxy_evidence_ok(diagnostics),
        "atomic_dimensions_created": _atomic_dimension_evidence_ok(stored_acceptance, diagnostics),
        "assembly_structure_verified": _assembly_structure_evidence_ok(diagnostics),
        "bom_verified": _bom_evidence_ok(diagnostics),
        "sheet_metal_feature_verified": _sheet_metal_feature_evidence_ok(diagnostics),
        "flat_pattern_exported": _sheet_metal_flat_pattern_evidence_ok(diagnostics),
        "weldment_feature_verified": _weldment_feature_evidence_ok(diagnostics),
        "cut_list_verified": _cut_list_evidence_ok(diagnostics),
        "simulation_study_verified": _simulation_study_evidence_ok(diagnostics),
        "simulation_results_within_limits": _simulation_limits_evidence_ok(diagnostics),
        "simulation_report_verified": _simulation_report_evidence_ok(diagnostics),
        "model_geometry_verified": _model_geometry_evidence_ok(diagnostics),
        "mass_properties_verified": _mass_property_evidence_ok(diagnostics),
        "cleanup_completed": _cleanup_completed_evidence_ok(diagnostics),
        "cleanup_verified": _cleanup_verified_evidence_ok(diagnostics),
        "document_state_audit_verified": _document_state_audit_evidence_ok(diagnostics),
    }
    for gate_id, evidence_ok in evidence_checks.items():
        if gate_id not in failures and checks.get(gate_id) is True and not evidence_ok:
            failures.append(gate_id)
    return failures


def _current_production_gate_ids(stored_acceptance: dict[str, Any]) -> tuple[str, ...]:
    """Return the production acceptance gates required by the current MVP."""

    summary = stored_acceptance.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    if summary.get("trusted_workflow_status") == "controlled_bom_assembly":
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
    if summary.get("trusted_workflow_status") == "controlled_atomic_model":
        gate_ids = [
            "execution_ok",
            "trusted_controlled_workflow",
            "preflight_ready",
            "drawing_standard_views_created",
            "basic_dimensions_not_proxy",
            "atomic_dimensions_created",
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
        ]
        if not _stored_acceptance_has_no_hole_callout_requirement(stored_acceptance):
            gate_ids.insert(4, "hole_callouts_created")
            gate_ids.insert(5, "direct_hole_callouts_created")
        return tuple(gate_ids)
    if summary.get("trusted_workflow_status") == "controlled_sheet_metal_base_flange":
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
    if summary.get("trusted_workflow_status") == "controlled_weldment_frame":
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
    if summary.get("trusted_workflow_status") == "controlled_static_simulation":
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


def _stored_acceptance_has_no_hole_callout_requirement(stored_acceptance: dict[str, Any]) -> bool:
    """Return whether the accepted workflow intentionally has no holes to call out."""

    summary = stored_acceptance.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    return (
        summary.get("trusted_workflow_status")
        in {
            "controlled_shaft",
            "controlled_atomic_model",
            "controlled_bom_assembly",
            "controlled_sheet_metal_base_flange",
            "controlled_weldment_frame",
            "controlled_static_simulation",
        }
        and summary.get("drawing_annotation_status") == "not_requested"
    )

def _drawing_standard_view_evidence_ok(value: Any) -> bool:
    """Return whether diagnostics prove the four required standard drawing views."""

    if not isinstance(value, dict) or value.get("status") != "created":
        return False
    roles = {
        str(item.get("role"))
        for item in value.get("views", [])
        if isinstance(item, dict) and item.get("role")
    }
    return {"front", "top", "right", "isometric"}.issubset(roles)


def _hole_callout_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove at least one real hole callout."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("drawing_annotation_result"))
    return (
        diagnostics.get("drawing_annotation_status") == "hole_callout_created"
        and int(result.get("created_callout_count") or 0) >= 1
    )


def _direct_hole_callout_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove selected-edge AddHoleCallout2 usage."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("drawing_annotation_result"))
    return result.get("direct_hole_callout_created") is True


def _basic_dimension_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove trusted drawing dimensions were created."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("drawing_dimension_result"))
    created = [item for item in result.get("created_dimensions", []) or [] if isinstance(item, dict)]
    missing = [item for item in result.get("missing_dimensions", []) or [] if item]
    return (
        diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
        and int(result.get("created_dimension_count") or 0) >= 1
        and len(created) >= int(result.get("created_dimension_count") or 0)
        and not missing
    )


def _trusted_dimension_evidence_ok(diagnostics: Any) -> bool:
    """Return whether dimensions are real SolidWorks display dimensions, not proxy layout."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("drawing_dimension_result"))
    created = [item for item in result.get("created_dimensions", []) or [] if isinstance(item, dict)]
    proxy_dimensions = [item for item in created if item.get("proxy_dimension") is True]
    return (
        _basic_dimension_evidence_ok(diagnostics)
        and result.get("dimension_layout_status") == "trusted_dimensions_created"
        and not proxy_dimensions
    )


def _basic_dimensions_not_proxy_evidence_ok(diagnostics: Any) -> bool:
    """Return whether atomic dimensions were not generated as proxy layout fallbacks."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("drawing_dimension_result"))
    created = [item for item in result.get("created_dimensions", []) or [] if isinstance(item, dict)]
    proxy_dimensions = [item for item in created if item.get("proxy_dimension") is True]
    return result.get("dimension_layout_status") != "radius_proxy_used" and not proxy_dimensions


def _atomic_dimension_evidence_ok(stored_acceptance: dict[str, Any], diagnostics: Any) -> bool:
    """Return whether diagnostics still prove the stored atomic driving dimensions."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("drawing_dimension_result"))
    summary = stored_acceptance.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    required = {
        str(item)
        for item in summary.get("required_atomic_dimensions", []) or []
        if item
    }
    created = [
        item
        for item in result.get("created_dimensions", []) or []
        if isinstance(item, dict)
    ]
    created_ids = {
        str(item.get("id"))
        for item in created
        if item.get("id")
    }
    missing = set(str(item) for item in result.get("missing_dimensions", []) or [] if item)
    return (
        diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
        and int(result.get("created_dimension_count") or 0) >= len(required)
        and required.issubset(created_ids)
        and not missing
    )


def _assembly_structure_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove the controlled assembly structure."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("assembly_result"))
    try:
        component_count = int(result.get("component_instance_count") or 0)
    except (TypeError, ValueError):
        component_count = 0
    return result.get("status") == "assembly_verified" and component_count >= 2


def _bom_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove the controlled BOM."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("bom_result"))
    try:
        row_count = int(result.get("row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    return result.get("status") == "bom_verified" and row_count >= 2


def _sheet_metal_feature_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove a controlled sheet-metal feature."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("sheet_metal_result"))
    return (
        diagnostics.get("sheet_metal_status") == "sheet_metal_verified"
        and result.get("status") == "sheet_metal_verified"
        and result.get("base_flange_created") is True
    )


def _sheet_metal_flat_pattern_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove a flat-pattern DXF export."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("sheet_metal_result"))
    flat_pattern_result = _as_dict(result.get("flat_pattern_result"))
    return flat_pattern_result.get("status") == "flat_pattern_exported" and flat_pattern_result.get("ok") is True


def _weldment_feature_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove a controlled weldment feature."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("weldment_result"))
    try:
        body_count = int(result.get("body_count") or 0)
    except (TypeError, ValueError):
        body_count = 0
    return (
        diagnostics.get("weldment_status") == "weldment_verified"
        and result.get("status") == "weldment_verified"
        and result.get("structural_member_created") is True
        and body_count >= 4
    )


def _cut_list_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove a weldment cut list."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("cut_list_result"))
    try:
        row_count = int(result.get("row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    return (
        diagnostics.get("cut_list_status") == "cut_list_verified"
        and result.get("status") == "cut_list_verified"
        and row_count >= 2
    )


def _simulation_study_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove a controlled static simulation study."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("simulation_result"))
    return (
        diagnostics.get("simulation_status") == "simulation_verified"
        and result.get("status") == "simulation_verified"
        and result.get("study_type") == "static"
    )


def _simulation_limits_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove simulation results are inside limits."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("simulation_result"))
    checks = _as_dict(result.get("checks"))
    return bool(checks) and all(value is True for value in checks.values())


def _simulation_report_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove a simulation report was generated."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("simulation_result"))
    try:
        row_count = int(result.get("row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    return _simulation_study_evidence_ok(diagnostics) and row_count >= 3


def _model_geometry_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove SolidWorks geometry readback."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("model_geometry_result"))
    return (
        diagnostics.get("model_geometry_status") == "geometry_verified"
        and result.get("status") == "geometry_verified"
        and int(result.get("body_count") or 0) >= 1
    )


def _mass_property_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove positive mass and volume readback."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("mass_property_result"))
    return (
        diagnostics.get("mass_property_status") == "mass_properties_verified"
        and _positive_number(result.get("mass_kg"))
        and _positive_number(result.get("volume_m3"))
    )


def _cleanup_completed_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove cleanup ran under the accepted policy."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("cleanup_result"))
    return result.get("enabled") is True and result.get("status") in {"completed", "skipped_no_documents"}


def _cleanup_verified_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove cleanup verification."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("cleanup_result"))
    return result.get("status") == "skipped_no_documents" or result.get("cleanup_verification_status") == "verified"


def _document_state_audit_evidence_ok(diagnostics: Any) -> bool:
    """Return whether diagnostics still prove no run-created document remained open."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = _as_dict(diagnostics.get("document_state_audit_result"))
    return (
        result.get("status") == "verified_no_run_documents_open"
        and result.get("after_cleanup_run_created_open_count") == 0
    )


def _positive_number(value: Any) -> bool:
    """Return whether value can be interpreted as a positive number."""

    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _current_gate_repair_actions(failures: list[str], summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Build repair actions for current-gate recheck failures."""

    try:
        from solidworks_mcp.repair import build_repair_actions

        return build_repair_actions(failures, summary)
    except Exception:
        return [
            {
                "id": failure,
                "severity": "blocker",
                "title": "Regenerate the run with the current production acceptance gates.",
                "next_step": "Rerun the trusted smoke workflow and inspect current_acceptance_recheck before handoff.",
                "evidence_fields": ["diagnostics.production_acceptance_result", "execution_report.json"],
                "details": {},
            }
            for failure in failures
        ]


def _compact_acceptance_recheck(value: Any) -> dict[str, Any]:
    """Return compact current-recheck metadata for CLI/MCP consumers."""

    value = value if isinstance(value, dict) else {}
    acceptance = value.get("acceptance")
    acceptance = acceptance if isinstance(acceptance, dict) else {}
    return {
        "status": value.get("status"),
        "source": value.get("source"),
        "stored_status": value.get("stored_status"),
        "current_status": value.get("current_status") or acceptance.get("status"),
        "current_failures": value.get("current_failures") or acceptance.get("failures", []),
        "failure_reason": value.get("failure_reason"),
    }


def _acceptance_is_accepted(value: Any) -> bool:
    """Return whether a stored acceptance result claimed production success."""

    return isinstance(value, dict) and (value.get("status") == "accepted" or value.get("ok") is True)


def _normalise_scan_limit(max_runs: int | None) -> int | None:
    """Return a positive scan limit, or None for a complete collection audit."""

    if max_runs is None or max_runs <= 0:
        return None
    return max_runs


def _discover_run_directories(root_path: Path, max_runs: int | None) -> tuple[list[Path], bool]:
    """Return candidate run directories newest first."""

    if (root_path / "execution_report.json").is_file():
        return [root_path], False
    report_files = sorted(
        root_path.rglob("execution_report.json") if root_path.exists() else [],
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    seen: set[Path] = set()
    run_paths: list[Path] = []
    for report_file in report_files:
        run_path = report_file.parent.resolve()
        if run_path in seen:
            continue
        seen.add(run_path)
        if max_runs is not None and len(run_paths) >= max_runs:
            return run_paths, True
        run_paths.append(run_path)
    return run_paths, False


def _collection_summary(
    root_path: Path,
    results: list[dict[str, Any]],
    max_runs: int | None,
    truncated: bool,
) -> dict[str, Any]:
    """Return aggregate production-delivery audit fields for a run collection."""

    accepted = [result for result in results if result.get("ok") is True]
    rejected = [result for result in results if result.get("ok") is not True]
    status_counts: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("production_acceptance_status") or "not_evaluated")
        status_counts[status] = status_counts.get(status, 0) + 1
        _add_collection_issue_counts(issue_counts, result)

    return {
        "root_dir": str(root_path),
        "ok": bool(results) and not rejected and not truncated,
        "scan_status": "truncated" if truncated else "complete" if results else "empty",
        "run_count": len(results),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "max_runs": max_runs if max_runs is not None else 0,
        "scan_limit": max_runs,
        "scan_unbounded": max_runs is None,
        "truncated": truncated,
        "latest_run_dir": results[0].get("run_dir") if results else None,
        "status_counts": status_counts,
        "issue_counts": dict(sorted(issue_counts.items())),
        "failure_reason": "scan truncated before all runs were diagnosed"
        if truncated
        else None
        if results
        else "no execution_report.json files found",
    }


def _collection_result_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Return a compact per-run payload for batch production audits."""

    missing_artifacts = [item for item in result.get("missing_artifacts") or [] if isinstance(item, dict)]
    event_issues = [item for item in result.get("event_log_issues") or [] if isinstance(item, dict)]
    manifest_issues = [item for item in result.get("delivery_manifest_issues") or [] if isinstance(item, dict)]
    environment_issues = [item for item in result.get("environment_issues") or [] if isinstance(item, dict)]
    issue_counts = _result_issue_counts(missing_artifacts, event_issues, manifest_issues, environment_issues)
    return {
        "run_dir": result.get("run_dir"),
        "ok": result.get("ok"),
        "execution_ok": result.get("execution_ok"),
        "plan_name": result.get("plan_name"),
        "adapter": result.get("adapter"),
        "production_acceptance_status": result.get("production_acceptance_status"),
        "production_acceptance_failures": result.get("production_acceptance_failures") or [],
        "stored_production_acceptance_status": result.get("stored_production_acceptance_status"),
        "stored_production_acceptance_failures": result.get("stored_production_acceptance_failures") or [],
        "current_acceptance_recheck": result.get("current_acceptance_recheck"),
        "repair_actions": result.get("repair_actions", []),
        "artifact_integrity_status": result.get("artifact_integrity_status"),
        "event_log_status": result.get("event_log_status"),
        "delivery_manifest_status": result.get("delivery_manifest_status"),
        "environment_status": result.get("environment_status"),
        "report_file": result.get("report_file"),
        "artifacts_file": result.get("artifacts_file"),
        "delivery_manifest_file": result.get("delivery_manifest_file"),
        "delivery_handoff_summary": _compact_handoff_summary(result.get("delivery_handoff_summary")),
        "missing_artifact_count": len(missing_artifacts),
        "event_issue_count": len(event_issues),
        "delivery_manifest_issue_count": len(manifest_issues),
        "environment_issue_count": len(environment_issues),
        "issue_counts": issue_counts,
        "top_issue_statuses": list(issue_counts)[:6],
        "failure_class": result.get("failure_class"),
        "message": result.get("message"),
        "repro_command": result.get("repro_command"),
    }


def _result_issue_counts(
    missing_artifacts: list[dict[str, Any]],
    event_issues: list[dict[str, Any]],
    manifest_issues: list[dict[str, Any]],
    environment_issues: list[dict[str, Any]],
) -> dict[str, int]:
    """Return stable compact issue counts for one batch result."""

    issue_counts: dict[str, int] = {}
    for item in missing_artifacts:
        _increment(issue_counts, f"artifact:{item.get('status')}")
    for item in event_issues:
        _increment(issue_counts, f"event:{item.get('status')}")
    for item in manifest_issues:
        _increment(issue_counts, f"manifest:{item.get('field')}:{item.get('status')}")
    for item in environment_issues:
        _increment(issue_counts, f"environment:{item.get('field')}:{item.get('status')}")
    return dict(sorted(issue_counts.items()))


def _add_collection_issue_counts(issue_counts: dict[str, int], result: dict[str, Any]) -> None:
    """Collect stable issue keys from one run diagnosis."""

    for failure in result.get("production_acceptance_failures") or []:
        _increment(issue_counts, f"production:{failure}")
    for field in ("artifact_integrity_status", "event_log_status", "delivery_manifest_status", "environment_status"):
        status = result.get(field)
        if status not in {None, "verified"}:
            _increment(issue_counts, f"{field}:{status}")
    for item in result.get("missing_artifacts") or []:
        if isinstance(item, dict):
            _increment(issue_counts, f"artifact:{item.get('status')}")
    for item in result.get("event_log_issues") or []:
        if isinstance(item, dict):
            _increment(issue_counts, f"event:{item.get('status')}")
    for item in result.get("delivery_manifest_issues") or []:
        if isinstance(item, dict):
            _increment(issue_counts, f"manifest:{item.get('field')}:{item.get('status')}")
    for item in result.get("environment_issues") or []:
        if isinstance(item, dict):
            _increment(issue_counts, f"environment:{item.get('field')}:{item.get('status')}")


def _increment(counts: dict[str, int], key: str) -> None:
    """Increment one aggregate issue key."""

    counts[key] = counts.get(key, 0) + 1


def _acceptance_summary(
    diagnostics: dict[str, Any],
    acceptance: dict[str, Any],
) -> dict[str, Any]:
    """Return a compact top-level summary of production gate evidence."""

    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    acceptance = acceptance if isinstance(acceptance, dict) else {}
    artifact_content = _as_dict(diagnostics.get("artifact_content_result"))
    cad_content = _as_dict(artifact_content.get("cad_content_result"))
    pdf_semantic = _as_dict(artifact_content.get("pdf_semantic_content_result"))
    cleanup = _as_dict(diagnostics.get("cleanup_result"))
    drawing_annotation = _as_dict(diagnostics.get("drawing_annotation_result"))
    drawing_dimensions = _as_dict(diagnostics.get("drawing_dimension_result"))
    drawing_metadata = _as_dict(diagnostics.get("drawing_metadata_note_result"))
    geometry = _as_dict(diagnostics.get("model_geometry_result"))
    mass_properties = _as_dict(diagnostics.get("mass_property_result"))
    material = _as_dict(diagnostics.get("material_result"))
    custom_properties = _as_dict(diagnostics.get("custom_property_result"))
    assembly = _as_dict(diagnostics.get("assembly_result"))
    bom = _as_dict(diagnostics.get("bom_result"))
    weldment = _as_dict(diagnostics.get("weldment_result"))
    cut_list = _as_dict(diagnostics.get("cut_list_result"))
    simulation = _as_dict(diagnostics.get("simulation_result"))
    summary = _as_dict(acceptance.get("summary"))

    return {
        "status": acceptance.get("status"),
        "failures": acceptance.get("failures", []),
        "trusted_workflow_status": summary.get("trusted_workflow_status"),
        "trusted_workflow": summary.get("trusted_workflow"),
        "thread_model_status": summary.get("thread_model_status") or diagnostics.get("thread_model_status"),
        "hole_count": summary.get("hole_count"),
        "corner_radius_status": summary.get("corner_radius_status") or diagnostics.get("corner_radius_status"),
        "drawing_view_status": summary.get("drawing_view_status") or diagnostics.get("drawing_view_status"),
        "drawing_view_roles": summary.get("drawing_view_roles"),
        "missing_drawing_view_roles": summary.get("missing_drawing_view_roles"),
        "drawing_view_errors": summary.get("drawing_view_errors"),
        "drawing_annotation_status": diagnostics.get("drawing_annotation_status"),
        "callout_creation_method": drawing_annotation.get("callout_creation_method"),
        "direct_hole_callout_created": drawing_annotation.get("direct_hole_callout_created"),
        "drawing_dimension_status": diagnostics.get("drawing_dimension_status"),
        "dimension_count": drawing_dimensions.get("created_dimension_count"),
        "dimension_layout_status": summary.get("dimension_layout_status")
        or drawing_dimensions.get("dimension_layout_status"),
        "proxy_dimensions": summary.get("proxy_dimensions"),
        "non_radial_radius_dimensions": summary.get("non_radial_radius_dimensions"),
        "missing_dimensions": summary.get("missing_dimensions")
        or drawing_dimensions.get("missing_dimensions", []),
        "drawing_metadata_note_status": drawing_metadata.get("status"),
        "drawing_metadata_note_method": drawing_metadata.get("method"),
        "model_geometry_status": diagnostics.get("model_geometry_status"),
        "model_geometry_max_error_mm": geometry.get("max_error_mm"),
        "mass_property_status": diagnostics.get("mass_property_status"),
        "mass_kg": summary.get("mass_kg") or mass_properties.get("mass_kg"),
        "volume_m3": summary.get("volume_m3") or mass_properties.get("volume_m3"),
        "surface_area_m2": summary.get("surface_area_m2") or mass_properties.get("surface_area_m2"),
        "mass_property_failure_reason": mass_properties.get("failure_reason"),
        "assembly_status": summary.get("assembly_status") or assembly.get("status"),
        "component_instance_count": summary.get("component_instance_count")
        if "component_instance_count" in summary
        else assembly.get("component_instance_count"),
        "component_definitions": summary.get("component_definitions") or assembly.get("component_definitions"),
        "bom_status": summary.get("bom_status") or bom.get("status"),
        "bom_row_count": summary.get("bom_row_count") if "bom_row_count" in summary else bom.get("row_count"),
        "bom_columns": summary.get("bom_columns") or bom.get("columns"),
        "weldment_status": summary.get("weldment_status") or diagnostics.get("weldment_status") or weldment.get("status"),
        "structural_member_created": summary.get("structural_member_created")
        if "structural_member_created" in summary
        else weldment.get("structural_member_created"),
        "weldment_feature_type": summary.get("weldment_feature_type") or weldment.get("feature_type"),
        "weldment_body_count": summary.get("weldment_body_count")
        if "weldment_body_count" in summary
        else weldment.get("body_count"),
        "cut_list_status": summary.get("cut_list_status") or diagnostics.get("cut_list_status") or cut_list.get("status"),
        "cut_list_row_count": summary.get("cut_list_row_count")
        if "cut_list_row_count" in summary
        else cut_list.get("row_count"),
        "cut_list_columns": summary.get("cut_list_columns") or cut_list.get("columns"),
        "simulation_status": summary.get("simulation_status")
        or diagnostics.get("simulation_status")
        or simulation.get("status"),
        "simulation_study_type": summary.get("simulation_study_type") or simulation.get("study_type"),
        "simulation_solver": summary.get("simulation_solver") or simulation.get("solver"),
        "simulation_report_row_count": summary.get("simulation_report_row_count")
        if "simulation_report_row_count" in summary
        else simulation.get("row_count"),
        "simulation_max_von_mises_mpa": summary.get("simulation_max_von_mises_mpa")
        or simulation.get("max_von_mises_mpa"),
        "simulation_min_factor_of_safety": summary.get("simulation_min_factor_of_safety")
        or simulation.get("min_factor_of_safety"),
        "simulation_max_displacement_mm": summary.get("simulation_max_displacement_mm")
        or simulation.get("max_displacement_mm"),
        "material_status": diagnostics.get("material_status"),
        "requested_material": summary.get("requested_material") or material.get("requested_material"),
        "effective_material": material.get("effective_material"),
        "current_material": summary.get("current_material") or material.get("current_material"),
        "material_failure_reason": material.get("failure_reason"),
        "custom_property_status": diagnostics.get("custom_property_status"),
        "requested_custom_properties": summary.get("requested_custom_properties")
        or custom_properties.get("requested_properties"),
        "current_custom_properties": summary.get("current_custom_properties")
        or custom_properties.get("current_properties"),
        "custom_property_failure_reason": custom_properties.get("failure_reason"),
        "artifact_content_status": artifact_content.get("status"),
        "cad_content_status": cad_content.get("status"),
        "cad_content_failed": cad_content.get("failed", []),
        "cad_content_failure_reason": cad_content.get("failure_reason"),
        "pdf_semantic_content_status": pdf_semantic.get("status"),
        "pdf_semantic_content_missing": pdf_semantic.get("missing", []),
        "cleanup_status": cleanup.get("status"),
        "cleanup_verification_status": cleanup.get("cleanup_verification_status"),
        "cleanup_failure_reason": cleanup.get("failure_reason"),
    }


def _classify_failed_events(
    events: list[dict[str, Any]],
    acceptance_ok: bool,
    report: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate hard failed events from recovered diagnostic failures."""

    failed_events: list[dict[str, Any]] = []
    recovered_probe_events: list[dict[str, Any]] = []
    recovered_export_events: list[dict[str, Any]] = []
    recovered_preflight_events: list[dict[str, Any]] = []
    for event in events:
        if event.get("status") != "failed":
            continue
        if acceptance_ok and _is_recovered_probe_event(event):
            recovered_probe_events.append(event)
        elif _is_recovered_export_event(event, report):
            recovered_export_events.append(event)
        elif _is_recovered_preflight_event(event, report):
            recovered_preflight_events.append(event)
        else:
            failed_events.append(event)
    return failed_events, recovered_probe_events, recovered_export_events, recovered_preflight_events


def _event_log_integrity_issues(events: list[dict[str, Any]], report: dict[str, Any]) -> list[dict[str, Any]]:
    """Return semantic event-log issues that hashes alone cannot prove."""

    if not report:
        return []
    if not events:
        return [{"status": "missing_events", "event": "events.jsonl"}]

    issues: list[dict[str, Any]] = []
    expected_run_id = report.get("run_id")
    if expected_run_id:
        for index, event in enumerate(events):
            event_run_id = event.get("run_id")
            if event_run_id is None:
                issues.append(
                    {
                        "status": "missing_event_run_id",
                        "event": event.get("event"),
                        "index": index,
                        "expected": expected_run_id,
                        "actual": None,
                    }
                )
            elif event_run_id != expected_run_id:
                issues.append(
                    {
                        "status": "event_run_id_mismatch",
                        "event": event.get("event"),
                        "index": index,
                        "expected": expected_run_id,
                        "actual": event_run_id,
                    }
                )

    terminal_events = [
        event
        for event in events
        if event.get("event") == "plan.execution" and event.get("status") in {"completed", "failed"}
    ]
    if not terminal_events:
        issues.append({"status": "missing_terminal_event", "event": "plan.execution"})
        return issues

    terminal_event = terminal_events[-1]
    expected_status = "completed" if report.get("ok") else "failed"
    actual_status = terminal_event.get("status")
    if actual_status != expected_status:
        issues.append(
            {
                "status": "terminal_status_mismatch",
                "event": "plan.execution",
                "expected": expected_status,
                "actual": actual_status,
            }
        )
    details = terminal_event.get("details", {})
    if isinstance(details, dict) and "ok" in details and bool(details.get("ok")) != bool(report.get("ok")):
        issues.append(
            {
                "status": "terminal_ok_mismatch",
                "event": "plan.execution",
                "expected": bool(report.get("ok")),
                "actual": bool(details.get("ok")),
            }
        )
    if not isinstance(details, dict):
        issues.append({"status": "terminal_details_missing", "event": "plan.execution"})
        return issues

    expected_output_count = len(report.get("output_files", {}) or {})
    expected_preview_count = len(report.get("preview_files", {}) or {})
    for field, expected, status in (
        ("output_count", expected_output_count, "terminal_output_count_mismatch"),
        ("preview_count", expected_preview_count, "terminal_preview_count_mismatch"),
    ):
        actual = details.get(field)
        if actual != expected:
            issues.append(
                {
                    "status": status,
                    "event": "plan.execution",
                    "field": field,
                    "expected": expected,
                    "actual": actual,
                }
            )
    return issues


def _is_recovered_probe_event(event: dict[str, Any]) -> bool:
    """Return whether a failed event is an expected exploratory COM probe."""

    details = event.get("details", {})
    if not isinstance(details, dict):
        return False
    parameters = details.get("parameters", {})
    if not isinstance(parameters, dict):
        return False
    return parameters.get("purpose") in {"hole_edge_probe", "hole_edge_polyline_probe"}


def _is_recovered_preflight_event(event: dict[str, Any], report: dict[str, Any]) -> bool:
    """Return whether a failed event is the expected record of a preflight block."""

    if not report or report.get("failure_class") != "preflight" or report.get("ok") is not False:
        return False
    if event.get("event") == "environment.preflight":
        details = event.get("details", {})
        return isinstance(details, dict) and details.get("ok") is False
    if event.get("event") == "plan.execution":
        details = event.get("details", {})
        return isinstance(details, dict) and details.get("failure_class") == "preflight"
    return False


def _is_recovered_export_event(event: dict[str, Any], report: dict[str, Any]) -> bool:
    """Return whether a failed per-format export event is recorded in the report."""

    if event.get("event") != "outputs.export_format":
        return False
    if not report or report.get("ok") is not True:
        return False
    details = event.get("details", {})
    if not isinstance(details, dict):
        return False
    event_format = details.get("format")
    event_path = details.get("path")
    diagnostics = report.get("diagnostics", {})
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    export_result = diagnostics.get("export_result", {})
    export_result = export_result if isinstance(export_result, dict) else {}
    for failure in export_result.get("failed", []) or []:
        if not isinstance(failure, dict):
            continue
        if failure.get("format") != event_format:
            continue
        if event_path and failure.get("path") != event_path:
            continue
        return True
    return False


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a dict when a diagnostic section has the expected shape."""

    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning an empty object when it is unavailable."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL events while skipping malformed lines."""

    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"event": "diagnose.invalid_jsonl", "status": "failed", "raw": line[:200]})
    except Exception:
        return []
    return events


def _validate_environment_snapshot(
    environment_path: Path,
    environment: dict[str, Any],
    report: dict[str, Any],
    artifacts: dict[str, Any],
    acceptance: dict[str, Any],
) -> dict[str, Any]:
    """Verify environment.json belongs to the run and records production safety flags."""

    if not environment_path.exists():
        return {"status": "missing", "issues": [{"field": "environment_file", "status": "missing"}]}
    if not environment:
        return {"status": "invalid", "issues": [{"field": "environment_file", "status": "invalid_json"}]}

    issues: list[dict[str, Any]] = []
    for field in ("run_id", "adapter", "debug_level", "paths", "env"):
        if field not in environment:
            issues.append({"field": field, "status": "missing"})

    expected_run_id = report.get("run_id") or artifacts.get("run_id")
    if expected_run_id is not None and environment.get("run_id") != expected_run_id:
        issues.append(
            {
                "field": "run_id",
                "status": "mismatch",
                "expected": expected_run_id,
                "actual": environment.get("run_id"),
            }
        )

    expected_adapter = report.get("adapter")
    if expected_adapter is not None and environment.get("adapter") != expected_adapter:
        issues.append(
            {
                "field": "adapter",
                "status": "mismatch",
                "expected": expected_adapter,
                "actual": environment.get("adapter"),
            }
        )

    paths = environment.get("paths", {})
    if not isinstance(paths, dict):
        issues.append({"field": "paths", "status": "invalid"})
        paths = {}
    env_vars = environment.get("env", {})
    if not isinstance(env_vars, dict):
        issues.append({"field": "env", "status": "invalid"})
        env_vars = {}

    run_dir = paths.get("run_dir")
    expected_run_dir = report.get("run_dir") or artifacts.get("run_dir")
    if expected_run_dir is not None and not _same_run_dir_reference(run_dir, expected_run_dir):
        issues.append(
            {
                "field": "paths.run_dir",
                "status": "mismatch",
                "expected": expected_run_dir,
                "actual": run_dir,
            }
        )

    env_adapter = env_vars.get("SOLIDWORKS_MCP_ADAPTER")
    if expected_adapter is not None and env_adapter != expected_adapter:
        issues.append(
            {
                "field": "env.SOLIDWORKS_MCP_ADAPTER",
                "status": "mismatch",
                "expected": expected_adapter,
                "actual": env_adapter,
            }
        )

    debug_level = environment.get("debug_level")
    env_debug_level = env_vars.get("SOLIDWORKS_MCP_DEBUG_LEVEL")
    if debug_level is not None and env_debug_level is not None and env_debug_level != debug_level:
        issues.append(
            {
                "field": "env.SOLIDWORKS_MCP_DEBUG_LEVEL",
                "status": "mismatch",
                "expected": debug_level,
                "actual": env_debug_level,
            }
        )

    if _requires_real_production_safety_flags(expected_adapter, acceptance):
        for field in (
            "SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN",
            "SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT",
            "SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW",
        ):
            if not _truthy_env_value(env_vars.get(field)):
                issues.append(
                    {
                        "field": f"env.{field}",
                        "status": "required_true",
                        "expected": True,
                        "actual": env_vars.get(field),
                    }
                )

    return {"status": "verified" if not issues else "failed", "issues": issues}


def _same_run_dir_reference(actual: Any, expected: Any) -> bool:
    """Return whether two run-dir strings can refer to the same run directory."""

    if actual == expected:
        return True
    if not actual or not expected:
        return False
    actual_path = Path(str(actual))
    expected_path = Path(str(expected))
    try:
        if actual_path.resolve() == expected_path.resolve():
            return True
    except Exception:
        pass
    actual_parts = _normalized_path_parts(actual_path)
    expected_parts = _normalized_path_parts(expected_path)
    return bool(actual_parts) and len(actual_parts) <= len(expected_parts) and expected_parts[-len(actual_parts) :] == actual_parts


def _normalized_path_parts(path: Path) -> tuple[str, ...]:
    """Return case-insensitive path parts, ignoring drive and root anchors."""

    return tuple(part.lower() for part in path.parts if part not in {path.drive, path.root, ""})


def _requires_real_production_safety_flags(adapter: Any, acceptance: Any) -> bool:
    """Return whether a run must prove real-SW cleanup and direct callout settings."""

    if adapter != "solidworks" or not isinstance(acceptance, dict):
        return False
    return acceptance.get("status") == "accepted" or acceptance.get("ok") is True


def _truthy_env_value(value: Any) -> bool:
    """Return whether an environment snapshot value represents an enabled flag."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _validate_delivery_manifest(
    manifest_path: Path,
    manifest: dict[str, Any],
    report: dict[str, Any],
    artifacts: dict[str, Any],
    run_path: Path,
) -> dict[str, Any]:
    """Verify the compact delivery manifest matches the run report and artifact index."""

    if not manifest_path.exists():
        return {"status": "missing", "issues": [{"field": "delivery_manifest_file", "status": "missing"}]}
    if not manifest:
        return {"status": "invalid", "issues": [{"field": "delivery_manifest_file", "status": "invalid_json"}]}

    issues: list[dict[str, Any]] = []
    required_fields = (
        "schema_version",
        "run_id",
        "run_dir",
        "plan_name",
        "adapter",
        "ok",
        "production_verdict",
        "report_file",
        "artifacts_file",
        "delivery_manifest_file",
        "events_file",
        "environment_file",
        "output_files",
        "preview_files",
        "diagnose_command",
    )
    for field in required_fields:
        if field not in manifest:
            issues.append({"field": field, "status": "missing"})
    if _manifest_requires_handoff_summary(manifest.get("schema_version")) and "handoff_summary" not in manifest:
        issues.append({"field": "handoff_summary", "status": "missing"})

    _compare_manifest_value(issues, manifest, "run_id", report.get("run_id") or artifacts.get("run_id"))
    _compare_manifest_value(issues, manifest, "plan_name", report.get("plan_name"))
    _compare_manifest_value(issues, manifest, "adapter", report.get("adapter"))
    _compare_manifest_value(issues, manifest, "ok", report.get("ok"))
    report_verdict = report.get("production_verdict")
    if isinstance(report_verdict, dict) and manifest.get("production_verdict") != report_verdict:
        issues.append({"field": "production_verdict", "status": "mismatch"})

    manifest_run_dir = Path(str(manifest.get("run_dir"))) if manifest.get("run_dir") else None
    expected_paths = {
        "report_file": run_path / "execution_report.json",
        "artifacts_file": run_path / "artifacts.json",
        "delivery_manifest_file": manifest_path,
        "events_file": run_path / "events.jsonl",
        "environment_file": run_path / "environment.json",
    }
    for field, expected_path in expected_paths.items():
        current_path = _resolve_manifest_path(manifest.get(field), run_path, manifest_run_dir)
        if current_path != expected_path.resolve():
            issues.append(
                {
                    "field": field,
                    "status": "path_mismatch",
                    "expected": str(expected_path),
                    "actual": str(current_path) if current_path else None,
                }
            )

    for group_name in ("output_files", "preview_files"):
        manifest_group = manifest.get(group_name, {})
        artifact_group = artifacts.get(group_name, {}) if artifacts else {}
        _compare_manifest_artifact_group(issues, group_name, manifest_group, artifact_group)

    if "handoff_summary" in manifest:
        _validate_manifest_handoff_summary(issues, manifest, report)

    return {"status": "verified" if not issues else "failed", "issues": issues}


def _manifest_requires_handoff_summary(schema_version: Any) -> bool:
    """Return whether this delivery manifest schema requires a handoff summary."""

    return str(schema_version or "") >= "2026-06-06.2"


def _validate_manifest_handoff_summary(
    issues: list[dict[str, Any]],
    manifest: dict[str, Any],
    report: dict[str, Any],
) -> None:
    """Verify the one-screen handoff summary agrees with the manifest."""

    handoff = manifest.get("handoff_summary")
    if not isinstance(handoff, dict):
        issues.append({"field": "handoff_summary", "status": "invalid"})
        return
    verdict = manifest.get("production_verdict")
    verdict = verdict if isinstance(verdict, dict) else {}
    expected_values = {
        "delivery_status": verdict.get("status"),
        "delivery_ok": verdict.get("ok"),
        "production_failures": verdict.get("failures", []),
        "run_id": manifest.get("run_id"),
        "plan_name": manifest.get("plan_name"),
        "adapter": manifest.get("adapter"),
        "diagnose_command": manifest.get("diagnose_command"),
        "repro_command": report.get("repro_command"),
    }
    for field, expected in expected_values.items():
        if expected is not None and handoff.get(field) != expected:
            issues.append(
                {
                    "field": f"handoff_summary.{field}",
                    "status": "mismatch",
                    "expected": expected,
                    "actual": handoff.get(field),
                }
            )
    if "repair_actions" in verdict or "repair_actions" in handoff:
        expected_actions = verdict.get("repair_actions", [])
        if handoff.get("repair_actions", []) != expected_actions:
            issues.append(
                {
                    "field": "handoff_summary.repair_actions",
                    "status": "mismatch",
                    "expected": expected_actions,
                    "actual": handoff.get("repair_actions", []),
                }
            )
    key_statuses = handoff.get("key_statuses")
    if not isinstance(key_statuses, dict):
        issues.append({"field": "handoff_summary.key_statuses", "status": "invalid"})
    else:
        verdict_summary = verdict.get("summary")
        verdict_summary = verdict_summary if isinstance(verdict_summary, dict) else {}
        for field in (
            "trusted_workflow_status",
            "thread_model_status",
            "corner_radius_status",
            "drawing_view_status",
            "drawing_annotation_status",
            "dimension_layout_status",
            "model_geometry_status",
            "mass_property_status",
            "artifact_validation_status",
            "artifact_content_status",
            "cleanup_status",
            "cleanup_verification_status",
            "document_state_audit_status",
            "document_state_after_cleanup_run_created_open_count",
        ):
            if key_statuses.get(field) != verdict_summary.get(field):
                issues.append(
                    {
                        "field": f"handoff_summary.key_statuses.{field}",
                        "status": "mismatch",
                        "expected": verdict_summary.get(field),
                        "actual": key_statuses.get(field),
                    }
                )
    artifact_counts = handoff.get("artifact_counts")
    if not isinstance(artifact_counts, dict):
        issues.append({"field": "handoff_summary.artifact_counts", "status": "invalid"})
    else:
        for manifest_group_name, count_name in (("output_files", "outputs"), ("preview_files", "previews")):
            manifest_group = manifest.get(manifest_group_name, {})
            manifest_group = manifest_group if isinstance(manifest_group, dict) else {}
            expected_count = len(manifest_group)
            if artifact_counts.get(count_name) != expected_count:
                issues.append(
                    {
                        "field": f"handoff_summary.artifact_counts.{count_name}",
                        "status": "mismatch",
                        "expected": expected_count,
                        "actual": artifact_counts.get(count_name),
                    }
                )
    _validate_manifest_handoff_files(issues, "outputs", handoff.get("outputs"), manifest.get("output_files", {}))
    _validate_manifest_handoff_files(issues, "previews", handoff.get("previews"), manifest.get("preview_files", {}))


def _validate_manifest_handoff_files(
    issues: list[dict[str, Any]],
    summary_field: str,
    summary_files: Any,
    manifest_files: Any,
) -> None:
    """Verify handoff file lists mirror manifest artifact maps."""

    if not isinstance(summary_files, list):
        issues.append({"field": f"handoff_summary.{summary_field}", "status": "invalid"})
        return
    manifest_files = manifest_files if isinstance(manifest_files, dict) else {}
    summary_by_id = {
        item.get("id"): item
        for item in summary_files
        if isinstance(item, dict) and item.get("id") is not None
    }
    if set(summary_by_id) != set(manifest_files):
        issues.append(
            {
                "field": f"handoff_summary.{summary_field}",
                "status": "keys_mismatch",
                "expected": sorted(manifest_files),
                "actual": sorted(summary_by_id),
            }
        )
        return
    for name, manifest_item in manifest_files.items():
        if not isinstance(manifest_item, dict):
            continue
        summary_item = summary_by_id.get(name)
        summary_item = summary_item if isinstance(summary_item, dict) else {}
        for key in ("path", "relative_path", "sha256", "size_bytes", "ok"):
            if manifest_item.get(key) != summary_item.get(key):
                issues.append(
                    {
                        "field": f"handoff_summary.{summary_field}.{name}.{key}",
                        "status": "mismatch",
                        "expected": manifest_item.get(key),
                        "actual": summary_item.get(key),
                    }
                )


def _compare_manifest_value(
    issues: list[dict[str, Any]],
    manifest: dict[str, Any],
    field: str,
    expected: Any,
) -> None:
    """Append a manifest mismatch issue when an expected value is available."""

    if expected is not None and manifest.get(field) != expected:
        issues.append({"field": field, "status": "mismatch", "expected": expected, "actual": manifest.get(field)})


def _resolve_manifest_path(raw_path: Any, run_path: Path, manifest_run_dir: Path | None) -> Path | None:
    """Resolve manifest paths while allowing copied run directories."""

    if not raw_path:
        return None
    path = Path(str(raw_path))
    if manifest_run_dir is not None:
        try:
            relative = path.resolve().relative_to(manifest_run_dir.resolve())
        except Exception:
            pass
        else:
            return (run_path / relative).resolve()
    return path.resolve()


def _compare_manifest_artifact_group(
    issues: list[dict[str, Any]],
    group_name: str,
    manifest_group: Any,
    artifact_group: Any,
) -> None:
    """Verify manifest output/preview entries are copied from artifacts.json."""

    if not isinstance(manifest_group, dict):
        issues.append({"field": group_name, "status": "invalid"})
        return
    if not isinstance(artifact_group, dict):
        artifact_group = {}
    manifest_keys = set(manifest_group)
    artifact_keys = set(artifact_group)
    if manifest_keys != artifact_keys:
        issues.append(
            {
                "field": group_name,
                "status": "keys_mismatch",
                "expected": sorted(artifact_keys),
                "actual": sorted(manifest_keys),
            }
        )
        return
    for name in sorted(artifact_keys):
        manifest_item = manifest_group.get(name)
        artifact_item = artifact_group.get(name)
        if not isinstance(manifest_item, dict) or not isinstance(artifact_item, dict):
            issues.append({"field": f"{group_name}.{name}", "status": "invalid"})
            continue
        if not manifest_item.get("sha256"):
            issues.append({"field": f"{group_name}.{name}.sha256", "status": "missing"})
        for key in ("path", "relative_path", "sha256"):
            if artifact_item.get(key) and manifest_item.get(key) != artifact_item.get(key):
                issues.append(
                    {
                        "field": f"{group_name}.{name}.{key}",
                        "status": "mismatch",
                        "expected": artifact_item.get(key),
                        "actual": manifest_item.get(key),
                    }
                )


def _missing_artifacts(artifacts: dict[str, Any], run_path: Path) -> list[dict[str, Any]]:
    """Return artifact entries whose indexed files are missing, empty or changed."""

    missing: list[dict[str, Any]] = []
    indexed_run_dir = Path(str(artifacts.get("run_dir"))) if artifacts.get("run_dir") else None
    for group_name in ("fixed_files", "output_files", "preview_files", "directories"):
        group = artifacts.get(group_name, {}) if artifacts else {}
        if not isinstance(group, dict):
            continue
        for name, item in group.items():
            if not isinstance(item, dict):
                item = {}
            current = _current_artifact_state(item, group_name, str(name), run_path, indexed_run_dir)
            ok = current["ok"]
            if not ok:
                missing.append(
                    {
                        "group": group_name,
                        "name": name,
                        "path": item.get("path"),
                        "resolved_path": current.get("path"),
                        "exists": current["exists"],
                        "size_bytes": current["size_bytes"],
                        "status": current["status"],
                        "expected_sha256": current.get("expected_sha256"),
                        "actual_sha256": current.get("actual_sha256"),
                    }
                )
    return missing


def _artifact_index_structure_issues(
    artifacts: dict[str, Any],
    report: dict[str, Any],
    run_path: Path,
) -> list[dict[str, Any]]:
    """Return structural issues that would make an artifact index incomplete."""

    if not artifacts:
        return []

    issues: list[dict[str, Any]] = []
    for field in ("run_id", "run_dir"):
        if field not in artifacts:
            issues.append(
                {
                    "group": "artifacts",
                    "name": field,
                    "path": None,
                    "resolved_path": None,
                    "exists": False,
                    "size_bytes": None,
                    "status": "missing_field",
                }
            )
    if report:
        _append_artifact_metadata_issues(issues, artifacts, report)
    indexed_run_dir = Path(str(artifacts.get("run_dir"))) if artifacts.get("run_dir") else None
    requires_relative_paths = _artifact_index_requires_relative_paths(artifacts.get("schema_version"))
    for group_name in ("fixed_files", "output_files", "preview_files", "directories"):
        if group_name not in artifacts:
            issues.append(
                {
                    "group": group_name,
                    "name": None,
                    "path": None,
                    "resolved_path": None,
                    "exists": False,
                    "size_bytes": None,
                    "status": "missing_group",
                }
            )
            continue
        if not isinstance(artifacts.get(group_name), dict):
            issues.append(
                {
                    "group": group_name,
                    "name": None,
                    "path": None,
                    "resolved_path": None,
                    "exists": False,
                    "size_bytes": None,
                    "status": "invalid_group",
                }
            )
        elif requires_relative_paths:
            _append_relative_path_issues(
                issues,
                group_name,
                artifacts.get(group_name),
                run_path,
                indexed_run_dir,
            )
    fixed_files = artifacts.get("fixed_files")
    if isinstance(fixed_files, dict):
        for name in ("plan", "report", "events", "environment", "artifacts", "delivery_manifest"):
            if name not in fixed_files:
                issues.append(
                    {
                        "group": "fixed_files",
                        "name": name,
                        "path": None,
                        "resolved_path": None,
                        "exists": False,
                        "size_bytes": None,
                        "status": "missing_fixed_file_entry",
                    }
                )

    report_run_dir = Path(str(report.get("run_dir"))) if report.get("run_dir") else None
    for group_name in ("output_files", "preview_files"):
        _append_report_artifact_group_issues(
            issues,
            group_name,
            report.get(group_name, {}) if report else {},
            artifacts.get(group_name, {}) if artifacts else {},
            run_path,
            report_run_dir,
            indexed_run_dir,
        )
    return issues


def _append_artifact_metadata_issues(
    issues: list[dict[str, Any]],
    artifacts: dict[str, Any],
    report: dict[str, Any],
) -> None:
    """Append issues when artifacts.json does not belong to the execution report."""

    for field, status in (
        ("run_id", "artifact_run_id_mismatch"),
        ("run_dir", "artifact_run_dir_mismatch"),
    ):
        expected = report.get(field)
        actual = artifacts.get(field)
        if expected is not None and actual is not None and actual != expected:
            issues.append(
                {
                    "group": "artifacts",
                    "name": field,
                    "path": None,
                    "resolved_path": None,
                    "exists": False,
                    "size_bytes": None,
                    "status": status,
                    "expected": expected,
                    "actual": actual,
                }
            )


def _artifact_index_requires_relative_paths(schema_version: Any) -> bool:
    """Return whether this artifact index schema requires portable relative paths."""

    return str(schema_version or "") >= "2026-06-06.2"


def _append_relative_path_issues(
    issues: list[dict[str, Any]],
    group_name: str,
    group: Any,
    run_path: Path,
    indexed_run_dir: Path | None,
) -> None:
    """Append issues when portable relative paths are missing or inconsistent."""

    if not isinstance(group, dict):
        return
    for name, item in group.items():
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path")
        if not raw_path:
            continue
        resolved_path = _resolve_indexed_artifact_path(str(raw_path), run_path, indexed_run_dir).resolve()
        try:
            expected_relative = resolved_path.relative_to(run_path.resolve()).as_posix()
        except Exception:
            continue
        relative_path = item.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            issues.append(
                {
                    "group": group_name,
                    "name": str(name),
                    "path": item.get("path"),
                    "resolved_path": str(resolved_path),
                    "exists": resolved_path.exists(),
                    "size_bytes": resolved_path.stat().st_size if resolved_path.is_file() else None,
                    "status": "missing_relative_path",
                    "expected_relative_path": expected_relative,
                }
            )
            continue
        relative_candidate = Path(relative_path)
        if relative_candidate.is_absolute() or ".." in relative_candidate.parts:
            issues.append(
                {
                    "group": group_name,
                    "name": str(name),
                    "path": item.get("path"),
                    "resolved_path": str(resolved_path),
                    "exists": resolved_path.exists(),
                    "size_bytes": resolved_path.stat().st_size if resolved_path.is_file() else None,
                    "status": "invalid_relative_path",
                    "relative_path": relative_path,
                }
            )
            continue
        if relative_candidate.as_posix() != expected_relative:
            issues.append(
                {
                    "group": group_name,
                    "name": str(name),
                    "path": item.get("path"),
                    "resolved_path": str(resolved_path),
                    "exists": resolved_path.exists(),
                    "size_bytes": resolved_path.stat().st_size if resolved_path.is_file() else None,
                    "status": "relative_path_mismatch",
                    "expected_relative_path": expected_relative,
                    "actual_relative_path": relative_path,
                }
            )


def _append_report_artifact_group_issues(
    issues: list[dict[str, Any]],
    group_name: str,
    report_group: Any,
    artifact_group: Any,
    run_path: Path,
    report_run_dir: Path | None,
    indexed_run_dir: Path | None,
) -> None:
    """Append issues when report output/preview paths are not covered by artifacts.json."""

    if not isinstance(report_group, dict) or not report_group:
        return
    if not isinstance(artifact_group, dict):
        return

    report_keys = set(report_group)
    artifact_keys = set(artifact_group)
    if report_keys != artifact_keys:
        issues.append(
            {
                "group": group_name,
                "name": None,
                "path": None,
                "resolved_path": None,
                "exists": False,
                "size_bytes": None,
                "status": "report_keys_mismatch",
                "expected": sorted(report_keys),
                "actual": sorted(artifact_keys),
            }
        )

    for name in sorted(report_keys & artifact_keys):
        artifact_item = artifact_group.get(name)
        if not isinstance(artifact_item, dict):
            continue
        report_path = _resolve_manifest_path(report_group.get(name), run_path, report_run_dir)
        artifact_path_raw = artifact_item.get("path")
        if not report_path or not artifact_path_raw:
            continue
        artifact_path = _resolve_indexed_artifact_path(str(artifact_path_raw), run_path, indexed_run_dir).resolve()
        if artifact_path != report_path:
            issues.append(
                {
                    "group": group_name,
                    "name": name,
                    "path": artifact_item.get("path"),
                    "resolved_path": str(artifact_path),
                    "exists": artifact_path.exists(),
                    "size_bytes": artifact_path.stat().st_size if artifact_path.is_file() else None,
                    "status": "report_path_mismatch",
                    "expected": str(report_path),
                    "actual": str(artifact_path),
                }
            )


def _current_artifact_state(
    item: dict[str, Any],
    group_name: str,
    artifact_name: str,
    run_path: Path,
    indexed_run_dir: Path | None,
) -> dict[str, Any]:
    """Inspect the current filesystem state instead of trusting stale metadata."""

    raw_path = item.get("path")
    if not raw_path:
        return {"path": None, "exists": False, "size_bytes": None, "ok": False, "status": "missing_path"}
    path = _resolve_indexed_artifact_path(str(raw_path), run_path, indexed_run_dir)
    exists = path.exists()
    is_file = path.is_file() if exists else False
    is_dir = path.is_dir() if exists else False
    size_bytes = path.stat().st_size if is_file else None
    if group_name == "directories":
        ok = exists and is_dir
        status = "ready" if ok else "missing"
    else:
        ok = exists and is_file and size_bytes is not None and size_bytes > 0
        status = "ready" if ok else "missing_or_empty"
        expected_sha = item.get("sha256")
        if ok and _requires_file_sha256(group_name, artifact_name) and not (
            isinstance(expected_sha, str) and expected_sha
        ):
            return {
                "exists": exists,
                "path": str(path),
                "size_bytes": size_bytes,
                "ok": False,
                "status": "missing_sha256",
            }
        if ok and isinstance(expected_sha, str) and expected_sha:
            actual_sha = _sha256_file(path)
            if actual_sha != expected_sha:
                return {
                    "exists": exists,
                    "path": str(path),
                    "size_bytes": size_bytes,
                    "ok": False,
                    "status": "sha256_mismatch",
                    "expected_sha256": expected_sha,
                    "actual_sha256": actual_sha,
                }
            return {
                "exists": exists,
                "path": str(path),
                "size_bytes": size_bytes,
                "ok": True,
                "status": "ready",
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
            }
    return {"path": str(path), "exists": exists, "size_bytes": size_bytes, "ok": ok, "status": status}


def _requires_file_sha256(group_name: str, artifact_name: str) -> bool:
    """Return whether an artifact index file entry must carry a SHA-256 digest."""

    return not (group_name == "fixed_files" and artifact_name == "artifacts")


def _resolve_indexed_artifact_path(raw_path: str, run_path: Path, indexed_run_dir: Path | None) -> Path:
    """Resolve artifact paths relative to the run directory being diagnosed."""

    path = Path(raw_path)
    if indexed_run_dir is None:
        return path
    try:
        relative = path.resolve().relative_to(indexed_run_dir.resolve())
    except Exception:
        return path
    return run_path / relative


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for one artifact file."""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
