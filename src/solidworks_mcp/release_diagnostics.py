"""Offline diagnosis for archived SolidWorks MCP release-gate reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from solidworks_mcp.run_diagnostics import diagnose_run_collection


RELEASE_GATE_SCHEMA_VERSION = "2026-06-06.1"


def diagnose_release_gate_report(
    report_file: str | Path,
    *,
    summary_only: bool = True,
) -> dict[str, Any]:
    """Verify a release_gate_report.json against the current run directory state."""

    report_path = Path(report_file).expanduser().resolve()
    report = _read_json(report_path)
    issues: list[dict[str, Any]] = []
    if not report_path.is_file():
        issues.append({"field": "report_file", "status": "missing", "path": str(report_path)})
        return _release_report_result(report_path, {}, {}, issues, summary_only=summary_only)
    if not isinstance(report, dict):
        issues.append({"field": "report_file", "status": "invalid_json", "path": str(report_path)})
        return _release_report_result(report_path, {}, {}, issues, summary_only=summary_only)

    output_root = Path(str(report.get("output_root") or report_path.parent)).expanduser().resolve()
    if output_root != report_path.parent.resolve():
        issues.append(
            {
                "field": "output_root",
                "status": "path_mismatch",
                "expected": str(report_path.parent.resolve()),
                "actual": str(output_root),
            }
        )
    if report.get("schema_version") != RELEASE_GATE_SCHEMA_VERSION:
        issues.append(
            {
                "field": "schema_version",
                "status": "unsupported",
                "expected": RELEASE_GATE_SCHEMA_VERSION,
                "actual": report.get("schema_version"),
            }
        )

    scenarios = _string_list(report.get("scenarios"))
    if not scenarios:
        issues.append({"field": "scenarios", "status": "missing_or_empty"})
    duplicate_scenarios = sorted({item for item in scenarios if scenarios.count(item) > 1})
    if duplicate_scenarios:
        issues.append({"field": "scenarios", "status": "duplicate", "values": duplicate_scenarios})

    batch = diagnose_run_collection(output_root, summary_only=True, max_runs=0)
    _validate_report_vs_batch(issues, report, batch, scenarios)
    current_evidence_summary = _current_release_evidence_summary(batch)
    current_evidence_checks = _release_evidence_checks(current_evidence_summary)
    _validate_current_release_evidence(
        issues,
        report,
        scenarios,
        current_evidence_summary,
        current_evidence_checks,
    )
    return _release_report_result(
        report_path,
        report,
        batch,
        issues,
        summary_only=summary_only,
        current_evidence_summary=current_evidence_summary,
        current_evidence_checks=current_evidence_checks,
    )


def _read_json(path: Path) -> Any:
    """Return parsed JSON, or None when missing/invalid."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _validate_report_vs_batch(
    issues: list[dict[str, Any]],
    report: dict[str, Any],
    batch: dict[str, Any],
    scenarios: list[str],
) -> None:
    """Append mismatches between archived report fields and fresh batch diagnosis."""

    if batch.get("scan_status") != "complete":
        issues.append({"field": "batch.scan_status", "status": "mismatch", "actual": batch.get("scan_status")})
    expected_counts = {
        "run_count": len(scenarios),
        "accepted_count": len(scenarios),
        "rejected_count": 0,
    }
    for field, expected in expected_counts.items():
        actual = batch.get(field)
        if actual != expected:
            issues.append({"field": f"batch.{field}", "status": "mismatch", "expected": expected, "actual": actual})

    archived_batch = report.get("batch_diagnosis")
    archived_batch = archived_batch if isinstance(archived_batch, dict) else {}
    for field in ("ok", "scan_status", "run_count", "accepted_count", "rejected_count"):
        archived_value = archived_batch.get(field)
        current_value = batch.get(field)
        if archived_value != current_value:
            issues.append(
                {
                    "field": f"batch_diagnosis.{field}",
                    "status": "stale",
                    "expected": archived_value,
                    "actual": current_value,
                }
            )

    result_scenarios = _accepted_scenarios_from_batch(batch) or _accepted_scenarios_from_report(report)
    missing = sorted(set(scenarios) - result_scenarios)
    if missing:
        issues.append({"field": "scenarios", "status": "missing_accepted_runs", "values": missing})

    extra = sorted(result_scenarios - set(scenarios))
    if extra:
        issues.append({"field": "scenarios", "status": "unexpected_accepted_runs", "values": extra})

    _validate_archived_release_checks(issues, report, scenarios)


def _validate_current_release_evidence(
    issues: list[dict[str, Any]],
    report: dict[str, Any],
    scenarios: list[str],
    current_evidence_summary: dict[str, Any],
    current_evidence_checks: dict[str, bool],
) -> None:
    """Append issues when current run artifacts no longer prove release-gate evidence."""

    scenario_count = len(scenarios)
    if current_evidence_summary.get("scenario_count") != scenario_count:
        issues.append(
            {
                "field": "current_evidence.scenario_count",
                "status": "mismatch",
                "expected": scenario_count,
                "actual": current_evidence_summary.get("scenario_count"),
            }
        )

    archived_evidence = report.get("evidence_summary")
    archived_evidence = archived_evidence if isinstance(archived_evidence, dict) else {}
    archived_checks = report.get("checks")
    archived_checks = archived_checks if isinstance(archived_checks, dict) else {}

    for check_name, ok in current_evidence_checks.items():
        if ok is not True:
            count_field = _release_check_count_field(check_name)
            issues.append(
                {
                    "field": f"current_evidence.{check_name}",
                    "status": "failed",
                    "expected": True,
                    "actual": ok,
                    "evidence_field": count_field,
                    "evidence_count": current_evidence_summary.get(count_field) if count_field else None,
                    "scenario_count": current_evidence_summary.get("scenario_count"),
                }
            )
        if archived_checks.get(check_name) is True and ok is not True:
            issues.append(
                {
                    "field": f"checks.{check_name}",
                    "status": "stale_current_evidence",
                    "expected": archived_checks.get(check_name),
                    "actual": ok,
                }
            )

    for field in _release_evidence_count_fields():
        if field in archived_evidence and archived_evidence.get(field) != current_evidence_summary.get(field):
            issues.append(
                {
                    "field": f"evidence_summary.{field}",
                    "status": "stale",
                    "expected": archived_evidence.get(field),
                    "actual": current_evidence_summary.get(field),
                }
            )


def _validate_archived_release_checks(
    issues: list[dict[str, Any]],
    report: dict[str, Any],
    scenarios: list[str],
) -> None:
    """Verify archived top-level release checks when the report provides them."""

    checks = report.get("checks")
    evidence = report.get("evidence_summary")
    if not isinstance(checks, dict) or not isinstance(evidence, dict):
        return
    scenario_count = len(scenarios)
    expected_counts = {
        "cleanup_acceptable": "cleanup_acceptable_count",
        "cleanup_verified": "cleanup_verified_count",
        "document_state_verified": "document_state_verified_count",
        "direct_hole_callouts": "direct_callout_count",
        "trusted_dimensions": "trusted_dimension_count",
        "geometry_verified": "geometry_verified_count",
        "mass_properties_verified": "mass_verified_count",
        "artifact_content_ready": "artifact_content_ready_count",
        "cad_content_verified": "cad_content_verified_count",
        "pdf_semantic_content_verified": "pdf_semantic_verified_count",
        "required_outputs": "required_output_count",
        "required_previews": "preview_count",
        "assembly_structure_or_not_requested": "assembly_or_not_requested_count",
        "bom_or_not_requested": "bom_or_not_requested_count",
        "thread_or_not_requested": "threaded_hole_count",
        "fillet_or_not_requested": "fillet_feature_count",
    }
    for check_name, count_field in expected_counts.items():
        count_ok = evidence.get(count_field) == scenario_count
        check_ok = checks.get(check_name) is True
        if not check_ok or not count_ok:
            issues.append(
                {
                    "field": f"checks.{check_name}",
                    "status": "failed" if not check_ok else "evidence_mismatch",
                    "expected": True,
                    "actual": checks.get(check_name),
                    "evidence_field": count_field,
                    "evidence_count": evidence.get(count_field),
                    "scenario_count": scenario_count,
                }
            )


def _current_release_evidence_summary(batch: dict[str, Any]) -> dict[str, Any]:
    """Recompute release evidence from current compact run diagnosis results."""

    scenario_results = [_scenario_evidence_from_batch_result(item) for item in _batch_results(batch)]
    scenario_count = len(scenario_results)
    return {
        "scenario_count": scenario_count,
        "accepted_scenarios": sorted(
            str(item.get("scenario")) for item in scenario_results if item.get("ok") is True and item.get("scenario")
        ),
        "cleanup_acceptable_count": _count_results(
            scenario_results,
            lambda item: item.get("cleanup_status") in {"completed", "skipped_no_documents"},
        ),
        "cleanup_verified_count": _count_results(
            scenario_results,
            lambda item: item.get("cleanup_verification_status") in {"verified", "not_applicable"},
        ),
        "document_state_verified_count": _count_results(
            scenario_results,
            lambda item: item.get("document_state_audit_status") == "verified_no_run_documents_open"
            and item.get("document_state_after_cleanup_run_created_open_count") == 0,
        ),
        "direct_callout_count": _count_results(
            scenario_results,
            lambda item: (
                item.get("direct_hole_callout_created") is True
                and item.get("callout_creation_method") == "add_hole_callout2"
            )
            or (
                item.get("trusted_workflow_status") == "controlled_shaft"
                and item.get("drawing_annotation_status") == "not_requested"
            )
            or (
                item.get("trusted_workflow_status") == "controlled_atomic_model"
                and item.get("drawing_annotation_status") == "not_requested"
            )
            or (
                _is_assembly_workflow(item)
                and item.get("drawing_annotation_status") == "not_requested"
            )
            or (
                _is_sheet_metal_workflow(item)
                and item.get("drawing_annotation_status") == "not_requested"
            )
            or (
                _is_weldment_workflow(item)
                and item.get("drawing_annotation_status") == "not_requested"
            )
            or (
                _is_simulation_workflow(item)
                and item.get("drawing_annotation_status") == "not_requested"
            ),
        ),
        "trusted_dimension_count": _count_results(
            scenario_results,
            lambda item: _trusted_dimension_or_assembly_ok(item),
        ),
        "geometry_verified_count": _count_results(
            scenario_results,
            lambda item: item.get("model_geometry_status") == "geometry_verified" or _assembly_structure_ok(item),
        ),
        "mass_verified_count": _count_results(
            scenario_results,
            lambda item: item.get("mass_property_status") == "mass_properties_verified" or _bom_ok(item),
        ),
        "assembly_verified_count": _count_results(scenario_results, _assembly_structure_ok),
        "bom_verified_count": _count_results(scenario_results, _bom_ok),
        "sheet_metal_verified_count": _count_results(scenario_results, _sheet_metal_ok),
        "flat_pattern_exported_count": _count_results(scenario_results, _sheet_metal_flat_pattern_ok),
        "weldment_verified_count": _count_results(scenario_results, _weldment_ok),
        "cut_list_verified_count": _count_results(scenario_results, _cut_list_ok),
        "simulation_verified_count": _count_results(scenario_results, _simulation_ok),
        "simulation_report_verified_count": _count_results(scenario_results, _simulation_report_ok),
        "assembly_or_not_requested_count": _count_results(
            scenario_results,
            lambda item: not _is_assembly_workflow(item) or _assembly_structure_ok(item),
        ),
        "bom_or_not_requested_count": _count_results(
            scenario_results,
            lambda item: not _is_assembly_workflow(item) or _bom_ok(item),
        ),
        "simulation_or_not_requested_count": _count_results(
            scenario_results,
            lambda item: not _is_simulation_workflow(item) or (_simulation_ok(item) and _simulation_report_ok(item)),
        ),
        "artifact_content_ready_count": _count_results(
            scenario_results,
            lambda item: item.get("artifact_content_status") in {"content_ready", "mock_output_placeholders"},
        ),
        "cad_content_verified_count": _count_results(
            scenario_results,
            lambda item: item.get("cad_content_status") in {"cad_artifacts_verified", "mock_placeholder"},
        ),
        "pdf_semantic_verified_count": _count_results(
            scenario_results,
            lambda item: item.get("pdf_semantic_content_status") in {"pdf_semantic_content_verified", "mock_placeholder"},
        ),
        "required_output_count": _count_results(scenario_results, _has_required_outputs),
        "preview_count": _count_results(scenario_results, _has_required_previews),
        "threaded_hole_count": _count_results(
            scenario_results,
            lambda item: item.get("thread_model_status")
            in {"holewizard_threaded_hole", "macro_threaded_hole", "not_requested"},
        ),
        "fillet_feature_count": _count_results(
            scenario_results,
            lambda item: item.get("corner_radius_status") in {"fillet_feature", "not_requested"},
        ),
    }


def _scenario_evidence_from_batch_result(item: Any) -> dict[str, Any]:
    """Return release evidence fields for one current batch result."""

    item = item if isinstance(item, dict) else {}
    report = _read_json(Path(str(item.get("report_file") or ""))) if item.get("report_file") else {}
    report = report if isinstance(report, dict) else {}
    diagnostics = report.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    acceptance = diagnostics.get("production_acceptance_result")
    acceptance = acceptance if isinstance(acceptance, dict) else {}
    summary = acceptance.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    annotation = diagnostics.get("drawing_annotation_result")
    annotation = annotation if isinstance(annotation, dict) else {}
    drawing_dimensions = diagnostics.get("drawing_dimension_result")
    drawing_dimensions = drawing_dimensions if isinstance(drawing_dimensions, dict) else {}
    content = diagnostics.get("artifact_content_result")
    content = content if isinstance(content, dict) else {}
    cad_content = content.get("cad_content_result")
    cad_content = cad_content if isinstance(cad_content, dict) else {}
    pdf_semantic = content.get("pdf_semantic_content_result")
    pdf_semantic = pdf_semantic if isinstance(pdf_semantic, dict) else {}
    cleanup = diagnostics.get("cleanup_result")
    cleanup = cleanup if isinstance(cleanup, dict) else {}
    document_state = diagnostics.get("document_state_audit_result")
    document_state = document_state if isinstance(document_state, dict) else {}
    scenario = _scenario_from_plan_name(str(item.get("plan_name") or report.get("plan_name") or ""))
    output_files = report.get("output_files")
    output_files = output_files if isinstance(output_files, dict) else {}
    preview_files = report.get("preview_files")
    preview_files = preview_files if isinstance(preview_files, dict) else {}
    assembly_result = diagnostics.get("assembly_result")
    assembly_result = assembly_result if isinstance(assembly_result, dict) else {}
    bom_result = diagnostics.get("bom_result")
    bom_result = bom_result if isinstance(bom_result, dict) else {}
    sheet_metal_result = diagnostics.get("sheet_metal_result")
    sheet_metal_result = sheet_metal_result if isinstance(sheet_metal_result, dict) else {}
    flat_pattern_result = sheet_metal_result.get("flat_pattern_result")
    flat_pattern_result = flat_pattern_result if isinstance(flat_pattern_result, dict) else {}
    weldment_result = diagnostics.get("weldment_result")
    weldment_result = weldment_result if isinstance(weldment_result, dict) else {}
    cut_list_result = diagnostics.get("cut_list_result")
    cut_list_result = cut_list_result if isinstance(cut_list_result, dict) else {}
    simulation_result = diagnostics.get("simulation_result")
    simulation_result = simulation_result if isinstance(simulation_result, dict) else {}

    return {
        "scenario": scenario,
        "ok": item.get("ok"),
        "trusted_workflow_status": summary.get("trusted_workflow_status"),
        "thread_model_status": summary.get("thread_model_status") or diagnostics.get("thread_model_status"),
        "corner_radius_status": summary.get("corner_radius_status") or diagnostics.get("corner_radius_status"),
        "drawing_annotation_status": summary.get("drawing_annotation_status")
        or diagnostics.get("drawing_annotation_status")
        or annotation.get("status"),
        "callout_creation_method": summary.get("callout_creation_method") or annotation.get("callout_creation_method"),
        "direct_hole_callout_created": summary.get("direct_hole_callout_created")
        if "direct_hole_callout_created" in summary
        else annotation.get("direct_hole_callout_created"),
        "drawing_dimension_status": summary.get("drawing_dimension_status")
        or diagnostics.get("drawing_dimension_status")
        or drawing_dimensions.get("status"),
        "dimension_layout_status": summary.get("dimension_layout_status")
        or drawing_dimensions.get("dimension_layout_status"),
        "proxy_dimensions": summary.get("proxy_dimensions") or [],
        "non_radial_radius_dimensions": summary.get("non_radial_radius_dimensions") or [],
        "missing_dimensions": summary.get("missing_dimensions") or drawing_dimensions.get("missing_dimensions") or [],
        "model_geometry_status": summary.get("model_geometry_status") or diagnostics.get("model_geometry_status"),
        "mass_property_status": summary.get("mass_property_status") or diagnostics.get("mass_property_status"),
        "sheet_metal_status": summary.get("sheet_metal_status")
        or diagnostics.get("sheet_metal_status")
        or sheet_metal_result.get("status"),
        "flat_pattern_status": summary.get("flat_pattern_status") or flat_pattern_result.get("status"),
        "weldment_status": summary.get("weldment_status")
        or diagnostics.get("weldment_status")
        or weldment_result.get("status"),
        "weldment_body_count": summary.get("weldment_body_count")
        if "weldment_body_count" in summary
        else weldment_result.get("body_count"),
        "cut_list_status": summary.get("cut_list_status")
        or diagnostics.get("cut_list_status")
        or cut_list_result.get("status"),
        "cut_list_row_count": summary.get("cut_list_row_count")
        if "cut_list_row_count" in summary
        else cut_list_result.get("row_count"),
        "simulation_status": summary.get("simulation_status")
        or diagnostics.get("simulation_status")
        or simulation_result.get("status"),
        "simulation_study_type": summary.get("simulation_study_type") or simulation_result.get("study_type"),
        "simulation_solver": summary.get("simulation_solver") or simulation_result.get("solver"),
        "simulation_report_row_count": summary.get("simulation_report_row_count")
        if "simulation_report_row_count" in summary
        else simulation_result.get("row_count"),
        "simulation_max_von_mises_mpa": summary.get("simulation_max_von_mises_mpa")
        or simulation_result.get("max_von_mises_mpa"),
        "simulation_min_factor_of_safety": summary.get("simulation_min_factor_of_safety")
        or simulation_result.get("min_factor_of_safety"),
        "simulation_max_displacement_mm": summary.get("simulation_max_displacement_mm")
        or simulation_result.get("max_displacement_mm"),
        "assembly_status": summary.get("assembly_status") or assembly_result.get("status"),
        "component_instance_count": summary.get("component_instance_count")
        if "component_instance_count" in summary
        else assembly_result.get("component_instance_count"),
        "bom_status": summary.get("bom_status") or bom_result.get("status"),
        "bom_row_count": summary.get("bom_row_count") if "bom_row_count" in summary else bom_result.get("row_count"),
        "artifact_content_status": summary.get("artifact_content_status") or content.get("status"),
        "cad_content_status": summary.get("cad_content_status") or cad_content.get("status"),
        "pdf_semantic_content_status": summary.get("pdf_semantic_content_status") or pdf_semantic.get("status"),
        "cleanup_status": summary.get("cleanup_status") or cleanup.get("status"),
        "cleanup_verification_status": summary.get("cleanup_verification_status")
        or cleanup.get("cleanup_verification_status"),
        "document_state_audit_status": summary.get("document_state_audit_status") or document_state.get("status"),
        "document_state_after_cleanup_run_created_open_count": summary.get(
            "document_state_after_cleanup_run_created_open_count"
        )
        if "document_state_after_cleanup_run_created_open_count" in summary
        else document_state.get("after_cleanup_run_created_open_count"),
        "output_files": sorted(str(key).lower() for key in output_files),
        "preview_files": sorted(str(key).lower() for key in preview_files),
    }


def _release_evidence_checks(evidence_summary: dict[str, Any]) -> dict[str, bool]:
    """Return production evidence checks for a recomputed release summary."""

    scenario_count = int(evidence_summary.get("scenario_count") or 0)
    return {
        "cleanup_acceptable": evidence_summary.get("cleanup_acceptable_count") == scenario_count,
        "cleanup_verified": evidence_summary.get("cleanup_verified_count") == scenario_count,
        "document_state_verified": evidence_summary.get("document_state_verified_count") == scenario_count,
        "direct_hole_callouts": evidence_summary.get("direct_callout_count") == scenario_count,
        "trusted_dimensions": evidence_summary.get("trusted_dimension_count") == scenario_count,
        "geometry_verified": evidence_summary.get("geometry_verified_count") == scenario_count,
        "mass_properties_verified": evidence_summary.get("mass_verified_count") == scenario_count,
        "artifact_content_ready": evidence_summary.get("artifact_content_ready_count") == scenario_count,
        "cad_content_verified": evidence_summary.get("cad_content_verified_count") == scenario_count,
        "pdf_semantic_content_verified": evidence_summary.get("pdf_semantic_verified_count") == scenario_count,
        "required_outputs": evidence_summary.get("required_output_count") == scenario_count,
        "required_previews": evidence_summary.get("preview_count") == scenario_count,
        "assembly_structure_or_not_requested": evidence_summary.get("assembly_or_not_requested_count") == scenario_count,
        "bom_or_not_requested": evidence_summary.get("bom_or_not_requested_count") == scenario_count,
        "sheet_metal_or_not_requested": (
            evidence_summary.get("sheet_metal_verified_count") == _count_sheet_metal_scenarios(evidence_summary)
            and evidence_summary.get("flat_pattern_exported_count") == _count_sheet_metal_scenarios(evidence_summary)
        ),
        "weldment_or_not_requested": (
            evidence_summary.get("weldment_verified_count") == _count_weldment_scenarios(evidence_summary)
            and evidence_summary.get("cut_list_verified_count") == _count_weldment_scenarios(evidence_summary)
        ),
        "simulation_or_not_requested": (
            evidence_summary.get("simulation_verified_count") == _count_simulation_scenarios(evidence_summary)
            and evidence_summary.get("simulation_report_verified_count") == _count_simulation_scenarios(evidence_summary)
        ),
        "thread_or_not_requested": evidence_summary.get("threaded_hole_count") == scenario_count,
        "fillet_or_not_requested": evidence_summary.get("fillet_feature_count") == scenario_count,
    }


def _release_check_count_field(check_name: str) -> str | None:
    """Return the evidence count field backing a release check."""

    return _release_check_count_fields().get(check_name)


def _release_check_count_fields() -> dict[str, str]:
    """Return release check to evidence count mappings."""

    return {
        "cleanup_acceptable": "cleanup_acceptable_count",
        "cleanup_verified": "cleanup_verified_count",
        "document_state_verified": "document_state_verified_count",
        "direct_hole_callouts": "direct_callout_count",
        "trusted_dimensions": "trusted_dimension_count",
        "geometry_verified": "geometry_verified_count",
        "mass_properties_verified": "mass_verified_count",
        "artifact_content_ready": "artifact_content_ready_count",
        "cad_content_verified": "cad_content_verified_count",
        "pdf_semantic_content_verified": "pdf_semantic_verified_count",
        "required_outputs": "required_output_count",
        "required_previews": "preview_count",
        "assembly_structure_or_not_requested": "assembly_or_not_requested_count",
        "bom_or_not_requested": "bom_or_not_requested_count",
        "sheet_metal_or_not_requested": "sheet_metal_verified_count",
        "weldment_or_not_requested": "weldment_verified_count",
        "simulation_or_not_requested": "simulation_verified_count",
        "thread_or_not_requested": "threaded_hole_count",
        "fillet_or_not_requested": "fillet_feature_count",
    }


def _release_evidence_count_fields() -> tuple[str, ...]:
    """Return stable release evidence fields with numeric counts."""

    return ("scenario_count", *_release_check_count_fields().values())


def _batch_results(batch: dict[str, Any]) -> list[dict[str, Any]]:
    """Return compact batch results from a diagnosis payload."""

    results = batch.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _count_results(scenario_results: list[dict[str, Any]], predicate: Any) -> int:
    """Count scenarios that satisfy a predicate."""

    return sum(1 for item in scenario_results if callable(predicate) and predicate(item))


def _is_assembly_workflow(item: dict[str, Any]) -> bool:
    """Return whether compact evidence belongs to the controlled assembly+BOM workflow."""

    return item.get("trusted_workflow_status") == "controlled_bom_assembly"


def _is_sheet_metal_workflow(item: dict[str, Any]) -> bool:
    """Return whether compact evidence belongs to the controlled sheet-metal workflow."""

    return item.get("trusted_workflow_status") == "controlled_sheet_metal_base_flange"


def _is_weldment_workflow(item: dict[str, Any]) -> bool:
    """Return whether compact evidence belongs to the controlled weldment workflow."""

    return item.get("trusted_workflow_status") == "controlled_weldment_frame"


def _is_simulation_workflow(item: dict[str, Any]) -> bool:
    """Return whether compact evidence belongs to the controlled static simulation workflow."""

    return item.get("trusted_workflow_status") == "controlled_static_simulation"


def _assembly_structure_ok(item: dict[str, Any]) -> bool:
    """Return whether compact evidence proves a controlled assembly structure."""

    try:
        component_count = int(item.get("component_instance_count") or 0)
    except (TypeError, ValueError):
        component_count = 0
    return _is_assembly_workflow(item) and item.get("assembly_status") == "assembly_verified" and component_count >= 2


def _bom_ok(item: dict[str, Any]) -> bool:
    """Return whether compact evidence proves a controlled BOM."""

    try:
        row_count = int(item.get("bom_row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    return _is_assembly_workflow(item) and item.get("bom_status") == "bom_verified" and row_count >= 2


def _sheet_metal_ok(item: dict[str, Any]) -> bool:
    """Return whether compact evidence proves a controlled sheet-metal feature."""

    return _is_sheet_metal_workflow(item) and item.get("sheet_metal_status") == "sheet_metal_verified"


def _sheet_metal_flat_pattern_ok(item: dict[str, Any]) -> bool:
    """Return whether compact evidence proves a flat-pattern DXF export."""

    return _is_sheet_metal_workflow(item) and item.get("flat_pattern_status") == "flat_pattern_exported"


def _weldment_ok(item: dict[str, Any]) -> bool:
    """Return whether compact evidence proves a controlled weldment feature."""

    try:
        body_count = int(item.get("weldment_body_count") or 0)
    except (TypeError, ValueError):
        body_count = 0
    return _is_weldment_workflow(item) and item.get("weldment_status") == "weldment_verified" and body_count >= 4


def _cut_list_ok(item: dict[str, Any]) -> bool:
    """Return whether compact evidence proves a weldment cut list."""

    try:
        row_count = int(item.get("cut_list_row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    return _is_weldment_workflow(item) and item.get("cut_list_status") == "cut_list_verified" and row_count >= 2


def _simulation_ok(item: dict[str, Any]) -> bool:
    """Return whether compact evidence proves a controlled simulation study."""

    return _is_simulation_workflow(item) and item.get("simulation_status") == "simulation_verified"


def _simulation_report_ok(item: dict[str, Any]) -> bool:
    """Return whether compact evidence proves a simulation report CSV."""

    try:
        row_count = int(item.get("simulation_report_row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    return _is_simulation_workflow(item) and row_count >= 3


def _count_sheet_metal_scenarios(evidence_summary: dict[str, Any]) -> int:
    """Return the current number of accepted sheet-metal scenarios in a release summary."""

    accepted = evidence_summary.get("accepted_scenarios")
    if not isinstance(accepted, list):
        return 0
    return sum(1 for scenario in accepted if str(scenario) == "sheet_metal_base_flange_baseline")


def _count_weldment_scenarios(evidence_summary: dict[str, Any]) -> int:
    """Return the current number of accepted weldment scenarios in a release summary."""

    accepted = evidence_summary.get("accepted_scenarios")
    if not isinstance(accepted, list):
        return 0
    return sum(1 for scenario in accepted if str(scenario) == "weldment_frame_baseline")


def _count_simulation_scenarios(evidence_summary: dict[str, Any]) -> int:
    """Return the current number of accepted simulation scenarios in a release summary."""

    accepted = evidence_summary.get("accepted_scenarios")
    if not isinstance(accepted, list):
        return 0
    return sum(1 for scenario in accepted if str(scenario) == "simulation_cantilever_baseline")


def _trusted_dimension_or_assembly_ok(item: dict[str, Any]) -> bool:
    """Return whether current evidence has trusted dimensions or intentional assembly-only evidence."""

    if _is_assembly_workflow(item):
        return (
            item.get("dimension_layout_status") == "not_requested"
            and not item.get("proxy_dimensions")
            and not item.get("non_radial_radius_dimensions")
            and not item.get("missing_dimensions")
        )
    return (
        item.get("drawing_dimension_status") == "basic_dimensions_created"
        and item.get("dimension_layout_status") == "trusted_dimensions_created"
        and not item.get("proxy_dimensions")
        and not item.get("non_radial_radius_dimensions")
        and not item.get("missing_dimensions")
    )


def _has_required_outputs(item: dict[str, Any]) -> bool:
    """Return whether a current scenario result lists required output formats."""

    outputs = item.get("output_files")
    if not isinstance(outputs, list):
        return False
    output_ids = {str(value).lower() for value in outputs}
    if _is_assembly_workflow(item):
        return {"sldasm", "step", "slddrw", "pdf", "dwg", "csv"}.issubset(output_ids)
    if _is_sheet_metal_workflow(item):
        return {"sldprt", "step", "stl", "slddrw", "pdf", "dwg", "dxf"}.issubset(output_ids)
    if _is_weldment_workflow(item):
        return {"sldprt", "step", "stl", "slddrw", "pdf", "dwg", "csv"}.issubset(output_ids)
    if _is_simulation_workflow(item):
        return {"sldprt", "step", "stl", "slddrw", "pdf", "dwg", "csv"}.issubset(output_ids)
    return {"sldprt", "step", "stl", "slddrw", "pdf", "dwg"}.issubset(output_ids)


def _has_required_previews(item: dict[str, Any]) -> bool:
    """Return whether a current scenario result lists required preview views."""

    previews = item.get("preview_files")
    if not isinstance(previews, list):
        return False
    return {"front", "top", "right", "isometric"}.issubset({str(value).lower() for value in previews})


def _accepted_scenarios_from_batch(batch: dict[str, Any]) -> set[str]:
    """Return accepted scenario names inferred from compact batch results."""

    accepted: set[str] = set()
    for item in batch.get("results", []) if isinstance(batch.get("results"), list) else []:
        if not isinstance(item, dict) or item.get("ok") is not True:
            continue
        plan_name = str(item.get("plan_name") or "")
        scenario = _scenario_from_plan_name(plan_name)
        if scenario:
            accepted.add(scenario)
    return accepted


def _accepted_scenarios_from_report(report: dict[str, Any]) -> set[str]:
    """Return accepted scenario names recorded in the archived release report."""

    accepted: set[str] = set()
    for item in report.get("scenario_results", []) if isinstance(report.get("scenario_results"), list) else []:
        if isinstance(item, dict) and item.get("ok") is True and item.get("scenario"):
            accepted.add(str(item.get("scenario")))
    return accepted


def _scenario_from_plan_name(plan_name: str) -> str | None:
    """Map deterministic production plan names back to release scenario ids."""

    mapping = {
        "m6_mounting_plate_prod_combined": "combined",
        "m6_mounting_plate_prod_drawing_exchange": "drawing_exchange",
        "m6_mounting_plate_prod_neutral_exports": "neutral_exports",
        "center_hole_flange_prod_baseline": "flange_baseline",
        "center_hole_plate_prod_baseline": "center_hole_plate_baseline",
        "bracket_prod_baseline": "bracket_baseline",
        "end_cap_prod_baseline": "end_cap_baseline",
        "mounting_block_prod_baseline": "mounting_block_baseline",
        "shaft_prod_baseline": "shaft_baseline",
        "sheet_metal_base_flange_prod_baseline": "sheet_metal_base_flange_baseline",
        "weldment_frame_prod_baseline": "weldment_frame_baseline",
        "simulation_cantilever_prod_baseline": "simulation_cantilever_baseline",
        "washer_prod_baseline": "washer_baseline",
        "sleeve_prod_baseline": "sleeve_baseline",
        "slotted_array_plate_prod_baseline": "slotted_array_plate_baseline",
        "bom_assembly_prod_baseline": "bom_assembly_baseline",
        "atomic_model_prod_baseline": "atomic_baseline",
        "atomic_model_prod_cut_baseline": "atomic_cut_baseline",
        "atomic_model_prod_pattern_baseline": "atomic_pattern_baseline",
        "atomic_model_prod_revolve_baseline": "atomic_revolve_baseline",
        "atomic_model_prod_sweep_baseline": "atomic_sweep_baseline",
        "atomic_model_prod_loft_baseline": "atomic_loft_baseline",
        "m6_mounting_plate_prod_baseline": "baseline",
        "m6_mounting_plate_prod_material_alias": "material_alias",
        "m6_mounting_plate_prod_custom_properties": "custom_properties",
        "m6_mounting_plate_prod_wide_combined": "wide_combined",
        "m6_mounting_plate_prod_wide_combined_wide": "wide_combined",
    }
    return mapping.get(plan_name)


def _release_report_result(
    report_path: Path,
    report: dict[str, Any],
    batch: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    summary_only: bool,
    current_evidence_summary: dict[str, Any] | None = None,
    current_evidence_checks: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Return a stable release-report diagnosis payload."""

    result = {
        "ok": not issues,
        "status": "verified" if not issues else "failed",
        "report_file": str(report_path),
        "schema_version": report.get("schema_version"),
        "adapter": report.get("adapter"),
        "output_root": report.get("output_root") or str(report_path.parent),
        "scenarios": _string_list(report.get("scenarios")),
        "issue_count": len(issues),
        "issues": issues,
        "batch": {
            "ok": batch.get("ok"),
            "scan_status": batch.get("scan_status"),
            "run_count": batch.get("run_count"),
            "accepted_count": batch.get("accepted_count"),
            "rejected_count": batch.get("rejected_count"),
            "issue_counts": batch.get("issue_counts"),
        },
        "current_evidence_summary": current_evidence_summary or {},
        "current_evidence_checks": current_evidence_checks or {},
    }
    if not summary_only:
        result["report"] = report
        result["batch_diagnosis"] = batch
    return result


def _string_list(value: Any) -> list[str]:
    """Return a list of non-empty strings from a JSON-ish value."""

    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]
