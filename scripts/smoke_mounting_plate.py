"""Manual Windows smoke test for controlled SolidWorks production workflows.

This script is intentionally not a unit test.  It gives a Windows operator one
repeatable command that validates the MCP execution stack, runs confirmed
controlled production plans and prints the generated artifact paths.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import os
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solidworks_mcp.adapters import create_adapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.executor import ModelPlanExecutor
from solidworks_mcp.repair import build_repair_actions
from solidworks_mcp.run_diagnostics import diagnose_run_directory
from solidworks_mcp.sessions import AtomicSessionManager


EXPLICIT_ONLY_PRODUCTION_SCENARIOS = {
    "simulation_cantilever_baseline",
}


def parse_args() -> argparse.Namespace:
    """Parse smoke-test command line arguments."""

    parser = argparse.ArgumentParser(description="Run SolidWorks MCP controlled production smoke workflows.")
    parser.add_argument(
        "--plan",
        default=str(ROOT / "examples" / "mounting_plate_plan.json"),
        help="Path to the controlled model plan JSON file.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Force the mock adapter for local dry-runs outside Windows.",
    )
    parser.add_argument(
        "--thread-spec",
        choices=("M3", "M4", "M5", "M6", "M8"),
        help="Override the mounting plate thread spec for a single controlled variant.",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Run the controlled M3/M4/M5/M6/M8 mounting plate matrix.",
    )
    parser.add_argument(
        "--production-suite",
        action="store_true",
        help="Run the trusted production acceptance suite for controlled part-family scenarios.",
    )
    parser.add_argument(
        "--production-scenario",
        choices=(
            "all",
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
            "simulation_cantilever_baseline",
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
        ),
        default="all",
        help="Limit --production-suite to one scenario. Use 'all' to run the trusted production suite.",
    )
    parser.add_argument(
        "--size-variant",
        choices=("default", "wide"),
        default="default",
        help="Run a controlled size variant. 'wide' uses 140 x 90 x 12 mm, R6, 18 mm edge offset.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print a compact smoke verdict instead of the full execution diagnostics.",
    )
    return parser.parse_args()


def main() -> int:
    """Run connection, validation and confirmed execution for the smoke plan."""

    args = parse_args()
    smoke_started_at = time.time()
    plan_path = Path(args.plan).expanduser().resolve()
    base_plan = json.loads(plan_path.read_text(encoding="utf-8"))

    config = SolidWorksMCPConfig.from_env()
    if args.mock:
        config = SolidWorksMCPConfig(
            adapter="mock",
            output_root=config.output_root,
            part_template=config.part_template,
            drawing_template=config.drawing_template,
            visible=config.visible,
            macro_fallback_enabled=config.macro_fallback_enabled,
            macro_execution_disabled=config.macro_execution_disabled,
            force_holewizard_failure=config.force_holewizard_failure,
            force_drawing_callout_failure=config.force_drawing_callout_failure,
            force_drawing_dimension_failure=config.force_drawing_dimension_failure,
            force_cad_content_failure=config.force_cad_content_failure,
            force_cleanup_failure=config.force_cleanup_failure,
            force_export_failure=config.force_export_failure,
            force_model_geometry_failure=config.force_model_geometry_failure,
            force_material_failure=config.force_material_failure,
            force_preflight_failure=config.force_preflight_failure,
            enforce_trusted_workflow=config.enforce_trusted_workflow,
            require_direct_hole_callout=config.require_direct_hole_callout,
            close_documents_after_run=config.close_documents_after_run,
            cleanup_attach_only=config.cleanup_attach_only,
            debug_level=config.debug_level,
            run_id=config.run_id,
        )

    output_root = config.output_root.expanduser().resolve()
    if args.matrix and args.production_suite:
        print(json.dumps({
            "ok": False,
            "error": "--matrix and --production-suite are mutually exclusive",
        }, indent=2))
        return 2

    try:
        if os.getenv("SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION", "0").strip() in {"1", "true", "True"}:
            raise RuntimeError("SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION is enabled")

        if args.matrix:
            matrix = _run_matrix(base_plan, config, args.size_variant)
            _raise_forced_smoke_exception_after_run()
            print(json.dumps(_compact_suite_result(matrix) if args.summary_only else matrix, indent=2))
            return 0 if matrix["ok"] else 1

        if args.production_suite:
            suite = _run_production_suite(base_plan, config, args.production_scenario)
            _raise_forced_smoke_exception_after_run()
            print(json.dumps(_compact_suite_result(suite) if args.summary_only else suite, indent=2))
            return 0 if suite["ok"] else 1

        plan = _variant_plan(base_plan, args.thread_spec) if args.thread_spec else base_plan
        plan = _size_variant_plan(plan, args.size_variant)
        execution_bundle = _run_single_plan(plan, config)
        output = {
            "connection": execution_bundle["connection"],
            "validation": execution_bundle["validation"],
            "execution": execution_bundle["execution"],
        }
        _raise_forced_smoke_exception_after_run()
        print(json.dumps(_compact_single_result(output) if args.summary_only else output, indent=2))
        if not execution_bundle["validation"].get("ok"):
            return 2
        return 0 if execution_bundle["execution"]["acceptance"]["ok"] else 1
    except KeyboardInterrupt as exc:
        payload = _smoke_exception_payload(
            config=config,
            output_root=output_root,
            started_at=smoke_started_at,
            exc=exc,
            failure_class="smoke_interrupted",
        )
        report_file = _write_smoke_exception_report(output_root, payload)
        payload["report_file"] = str(report_file)
        print(
            json.dumps(
                _compact_smoke_exception_payload(payload) if args.summary_only else payload,
                indent=2,
                ensure_ascii=False,
            )
        )
        return 130
    except Exception as exc:
        payload = _smoke_exception_payload(
            config=config,
            output_root=output_root,
            started_at=smoke_started_at,
            exc=exc,
            failure_class="smoke_exception",
        )
        report_file = _write_smoke_exception_report(output_root, payload)
        payload["report_file"] = str(report_file)
        print(
            json.dumps(
                _compact_smoke_exception_payload(payload) if args.summary_only else payload,
                indent=2,
                ensure_ascii=False,
            )
        )
        return 1


def _raise_forced_smoke_exception_after_run() -> None:
    """Raise after at least one smoke execution for cleanup-path regression tests."""

    if os.getenv("SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION_AFTER_RUN", "0").strip() in {"1", "true", "True"}:
        raise RuntimeError("SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION_AFTER_RUN is enabled")


def _smoke_exception_payload(
    *,
    config: SolidWorksMCPConfig,
    output_root: Path,
    started_at: float,
    exc: BaseException,
    failure_class: str,
) -> dict[str, object]:
    """Return a rejected smoke payload with post-failure cleanup evidence."""

    cleanup_result = _emergency_cleanup_recent_runs(output_root, config, started_at)
    checks = {
        failure_class: False,
        "emergency_cleanup_attempted": cleanup_result.get("status")
        in {"completed", "skipped_no_recent_runs", "partial", "failed"},
    }
    return {
        "ok": False,
        "status": "rejected",
        "adapter": config.adapter,
        "output_root": str(output_root),
        "failure_class": failure_class,
        "failure_reason": str(exc),
        "traceback": traceback.format_exception_only(type(exc), exc),
        "checks": checks,
        "failures": [name for name, ok in checks.items() if not ok],
        "emergency_cleanup_result": cleanup_result,
    }


def _emergency_cleanup_recent_runs(
    output_root: Path,
    config: SolidWorksMCPConfig,
    started_at: float,
) -> dict[str, object]:
    """Best-effort close completed run documents created during this smoke invocation."""

    run_dirs = _recent_completed_run_dirs(output_root, started_at)
    result: dict[str, object] = {
        "status": "skipped_no_recent_runs",
        "run_count": len(run_dirs),
        "attempted_count": 0,
        "results": [],
        "failure_reason": None,
        "scope": {
            "output_root": str(output_root),
            "started_at_unix": started_at,
            "requires_execution_report": True,
        },
    }
    if not run_dirs:
        result["message"] = (
            "No completed run reports were found in the smoke output root after this invocation started; "
            "there was no safe run-scoped cleanup target."
        )
        return result

    try:
        executor = ModelPlanExecutor(create_adapter(config), config)
    except Exception as exc:
        result.update(
            {
                "status": "failed",
                "failure_reason": f"cleanup_executor_init_failed: {exc}",
                "message": "Could not initialize a cleanup executor after smoke failure.",
            }
        )
        return result

    cleanup_results: list[dict[str, object]] = []
    for run_dir in run_dirs:
        try:
            cleanup = executor.cleanup_run_documents(str(run_dir))
        except Exception as exc:
            cleanup = {
                "status": "failed",
                "run_dir": str(run_dir),
                "cleanup_verification_status": "not_attempted",
                "failure_reason": str(exc),
            }
        cleanup_results.append(cleanup)

    failed = [
        item
        for item in cleanup_results
        if item.get("status") not in {"completed", "skipped_no_documents"}
        or item.get("cleanup_verification_status") not in {"verified", "not_applicable"}
    ]
    result.update(
        {
            "status": "completed" if not failed else "partial" if len(failed) < len(cleanup_results) else "failed",
            "attempted_count": len(cleanup_results),
            "results": cleanup_results,
            "failure_reason": None if not failed else "One or more smoke emergency cleanup attempts failed.",
            "message": "Attempted run-scoped cleanup after smoke failure.",
        }
    )
    return result


def _recent_completed_run_dirs(output_root: Path, started_at: float) -> list[Path]:
    """Return completed run directories below output_root touched during this process."""

    if not output_root.exists():
        return []
    run_dirs: set[Path] = set()
    for report_file in output_root.rglob("execution_report.json"):
        try:
            if report_file.stat().st_mtime < started_at:
                continue
            run_dirs.add(report_file.parent.resolve())
        except OSError:
            continue
    return sorted(run_dirs)


def _write_smoke_exception_report(output_root: Path, payload: dict[str, object]) -> Path:
    """Persist a smoke failure report for operator handoff."""

    output_root.mkdir(parents=True, exist_ok=True)
    report_file = output_root / "smoke_failure_report.json"
    report_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return report_file


def _compact_smoke_exception_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return compact smoke failure fields for CI and operator review."""

    cleanup = payload.get("emergency_cleanup_result")
    cleanup = cleanup if isinstance(cleanup, dict) else {}
    return {
        "ok": payload.get("ok"),
        "status": payload.get("status"),
        "adapter": payload.get("adapter"),
        "output_root": payload.get("output_root"),
        "report_file": payload.get("report_file"),
        "failure_class": payload.get("failure_class"),
        "failure_reason": payload.get("failure_reason"),
        "failures": payload.get("failures"),
        "emergency_cleanup_result": {
            "status": cleanup.get("status"),
            "run_count": cleanup.get("run_count"),
            "attempted_count": cleanup.get("attempted_count"),
            "failure_reason": cleanup.get("failure_reason"),
        },
    }


def _run_single_plan(plan: dict[str, object], config: SolidWorksMCPConfig) -> dict[str, object]:
    """Execute one mounting-plate plan and return the smoke bundle."""

    executor = ModelPlanExecutor(create_adapter(config), config)
    connection = executor.connect()
    validation = executor.validate_plan(plan).to_dict()
    if not validation["ok"]:
        return {
            "connection": connection,
            "validation": validation,
            "execution": {"ok": False, "acceptance": {"ok": False, "failures": ["validation"]}},
        }

    execution = executor.execute_plan(plan, confirmed=True).to_dict()
    offline_diagnosis = _offline_diagnosis_for_execution(execution)
    acceptance = _smoke_acceptance(execution)
    acceptance = _merge_offline_diagnosis_acceptance(acceptance, offline_diagnosis)
    return {
        "connection": connection,
        "validation": validation,
        "execution": {
            "ok": execution["ok"],
            "message": execution["message"],
            "report_file": execution["report_file"],
            "delivery_manifest_file": execution.get("delivery_manifest_file"),
            "run_id": execution["run_id"],
            "run_dir": execution["run_dir"],
            "diagnostics": execution["diagnostics"],
            "offline_diagnosis": offline_diagnosis,
            "output_files": execution["output_files"],
            "preview_files": execution["preview_files"],
            "acceptance": acceptance,
            "diagnose_command": f"python scripts/diagnose_run.py {execution['run_dir']} --summary-only",
        },
    }


def _run_matrix(base_plan: dict[str, object], config: SolidWorksMCPConfig, size_variant: str) -> dict[str, object]:
    """Run every supported controlled thread variant as independent smoke executions."""

    variants = ["M3", "M4", "M5", "M6", "M8"]
    results = []
    for thread_spec in variants:
        variant_plan = _variant_plan(base_plan, thread_spec)
        variant_plan = _size_variant_plan(variant_plan, size_variant)
        variant_config = _config_for_variant(config, thread_spec)
        bundle = _run_single_plan(variant_plan, variant_config)
        execution = bundle["execution"]
        acceptance = execution.get("acceptance", {})
        diagnostics = execution.get("diagnostics", {})
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        results.append({
            "thread_spec": thread_spec,
            "ok": bool(acceptance.get("ok")),
            "validation_ok": bool(bundle["validation"].get("ok")),
            "execution_ok": bool(execution.get("ok")),
            "report_file": execution.get("report_file"),
            "delivery_manifest_file": execution.get("delivery_manifest_file"),
            "run_id": execution.get("run_id"),
            "run_dir": execution.get("run_dir"),
            "offline_diagnosis": execution.get("offline_diagnosis"),
            "acceptance": acceptance,
            "summary": diagnostics.get("production_acceptance_result", {}).get("summary")
            if isinstance(diagnostics.get("production_acceptance_result"), dict)
            else None,
        })
    failures = [result["thread_spec"] for result in results if not result["ok"]]
    return {
        "ok": not failures,
        "variants": variants,
        "size_variant": size_variant,
        "failures": failures,
        "results": results,
    }


def _run_production_suite(
    base_plan: dict[str, object],
    config: SolidWorksMCPConfig,
    scenario_filter: str,
) -> dict[str, object]:
    """Run trusted production scenarios as independent smoke executions."""

    scenarios = _production_scenarios(base_plan)
    if scenario_filter == "all":
        scenarios = [
            scenario
            for scenario in scenarios
            if str(scenario["name"]) not in EXPLICIT_ONLY_PRODUCTION_SCENARIOS
        ]
    else:
        scenarios = [scenario for scenario in scenarios if scenario["name"] == scenario_filter]
    results = []
    for scenario in scenarios:
        scenario_name = str(scenario["name"])
        scenario_config = _config_for_run_suffix(config, scenario_name)
        bundle = _run_single_plan(scenario["plan"], scenario_config)
        execution = bundle["execution"]
        acceptance = execution.get("acceptance", {})
        diagnostics = execution.get("diagnostics", {})
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        production_summary = None
        production_result = diagnostics.get("production_acceptance_result", {})
        if isinstance(production_result, dict):
            production_summary = production_result.get("summary")
        results.append({
            "scenario": scenario_name,
            "description": scenario.get("description"),
            "ok": bool(acceptance.get("ok")),
            "validation_ok": bool(bundle["validation"].get("ok")),
            "execution_ok": bool(execution.get("ok")),
            "report_file": execution.get("report_file"),
            "delivery_manifest_file": execution.get("delivery_manifest_file"),
            "run_id": execution.get("run_id"),
            "run_dir": execution.get("run_dir"),
            "offline_diagnosis": execution.get("offline_diagnosis"),
            "acceptance": acceptance,
            "summary": production_summary,
        })
    failures = [result["scenario"] for result in results if not result["ok"]]
    return {
        "ok": bool(results) and not failures,
        "suite": "production_acceptance",
        "scenario_filter": scenario_filter,
        "scenarios": [str(scenario["name"]) for scenario in scenarios],
        "failures": failures,
        "results": results,
    }


def _compact_suite_result(result: dict[str, object]) -> dict[str, object]:
    """Return a compact suite verdict for CI and quick operator review."""

    compact_results = []
    results = result.get("results", [])
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                compact_results.append(_compact_result_item(item))
    return {
        key: result.get(key)
        for key in ("ok", "suite", "scenario_filter", "variants", "size_variant", "failures")
        if key in result
    } | {"results": compact_results}


def _compact_single_result(result: dict[str, object]) -> dict[str, object]:
    """Return a compact single-run verdict for CI and quick operator review."""

    execution = result.get("execution", {})
    execution = execution if isinstance(execution, dict) else {}
    return {
        "ok": execution.get("acceptance", {}).get("ok")
        if isinstance(execution.get("acceptance"), dict)
        else execution.get("ok"),
        "connection": result.get("connection"),
        "validation_ok": result.get("validation", {}).get("ok")
        if isinstance(result.get("validation"), dict)
        else None,
        "execution": _compact_result_item({"execution_ok": execution.get("ok"), **execution}),
    }


def _compact_result_item(item: dict[str, object]) -> dict[str, object]:
    """Return the compact verdict fields for one smoke run result."""

    summary = item.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    acceptance = item.get("acceptance")
    acceptance = acceptance if isinstance(acceptance, dict) else {}
    offline_diagnosis = item.get("offline_diagnosis")
    offline_diagnosis = offline_diagnosis if isinstance(offline_diagnosis, dict) else {}
    diagnostics = item.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    dimension_result = diagnostics.get("drawing_dimension_result")
    dimension_result = dimension_result if isinstance(dimension_result, dict) else {}
    if not summary:
        acceptance_summary = acceptance.get("summary")
        summary = acceptance_summary if isinstance(acceptance_summary, dict) else {}
    drawing_dimension_status = summary.get("drawing_dimension_status")
    if drawing_dimension_status is None:
        drawing_dimension_status = dimension_result.get("status")
    if drawing_dimension_status is None and summary.get("dimension_count") is not None:
        try:
            dimension_count = int(summary.get("dimension_count") or 0)
        except (TypeError, ValueError):
            dimension_count = 0
        if dimension_count > 0 and not summary.get("missing_dimensions"):
            drawing_dimension_status = "basic_dimensions_created"
    return {
        "scenario": item.get("scenario", item.get("thread_spec")),
        "ok": item.get("ok", acceptance.get("ok")),
        "status": acceptance.get("status"),
        "failures": acceptance.get("failures", []),
        "repair_actions": acceptance.get("repair_actions")
        or offline_diagnosis.get("repair_actions")
        or build_repair_actions(acceptance.get("failures", []), summary),
        "validation_ok": item.get("validation_ok"),
        "execution_ok": item.get("execution_ok"),
        "report_file": item.get("report_file"),
        "delivery_manifest_file": item.get("delivery_manifest_file"),
        "run_id": item.get("run_id"),
        "run_dir": item.get("run_dir"),
        "offline_diagnosis_ok": offline_diagnosis.get("ok"),
        "artifact_integrity_status": offline_diagnosis.get("artifact_integrity_status"),
        "event_log_status": offline_diagnosis.get("event_log_status"),
        "failed_event_count": offline_diagnosis.get("failed_event_count"),
        "recovered_probe_event_count": offline_diagnosis.get("recovered_probe_event_count"),
        "recovered_export_event_count": offline_diagnosis.get("recovered_export_event_count"),
        "recovered_preflight_event_count": offline_diagnosis.get("recovered_preflight_event_count"),
        "delivery_manifest_status": offline_diagnosis.get("delivery_manifest_status"),
        "environment_status": offline_diagnosis.get("environment_status"),
        "environment_issues": offline_diagnosis.get("environment_issues"),
        "trusted_workflow_status": summary.get("trusted_workflow_status"),
        "thread_model_status": summary.get("thread_model_status"),
        "corner_radius_status": summary.get("corner_radius_status"),
        "drawing_view_status": summary.get("drawing_view_status"),
        "missing_drawing_view_roles": summary.get("missing_drawing_view_roles"),
        "drawing_annotation_status": summary.get("drawing_annotation_status"),
        "callout_creation_method": summary.get("callout_creation_method"),
        "direct_hole_callout_created": summary.get("direct_hole_callout_created"),
        "drawing_dimension_status": drawing_dimension_status,
        "dimension_layout_status": summary.get("dimension_layout_status"),
        "proxy_dimensions": summary.get("proxy_dimensions"),
        "non_radial_radius_dimensions": summary.get("non_radial_radius_dimensions"),
        "missing_dimensions": summary.get("missing_dimensions"),
        "material_status": summary.get("material_status"),
        "custom_property_status": summary.get("custom_property_status"),
        "model_geometry_status": summary.get("model_geometry_status"),
        "mass_property_status": summary.get("mass_property_status"),
        "sheet_metal_status": summary.get("sheet_metal_status"),
        "flat_pattern_status": summary.get("flat_pattern_status"),
        "flat_pattern_dxf_path": summary.get("flat_pattern_dxf_path"),
        "weldment_status": summary.get("weldment_status"),
        "structural_member_created": summary.get("structural_member_created"),
        "weldment_feature_type": summary.get("weldment_feature_type"),
        "weldment_body_count": summary.get("weldment_body_count"),
        "cut_list_status": summary.get("cut_list_status"),
        "cut_list_row_count": summary.get("cut_list_row_count"),
        "cut_list_columns": summary.get("cut_list_columns"),
        "simulation_status": summary.get("simulation_status"),
        "simulation_study_type": summary.get("simulation_study_type"),
        "simulation_solver": summary.get("simulation_solver"),
        "simulation_report_row_count": summary.get("simulation_report_row_count"),
        "simulation_max_von_mises_mpa": summary.get("simulation_max_von_mises_mpa"),
        "simulation_min_factor_of_safety": summary.get("simulation_min_factor_of_safety"),
        "simulation_max_displacement_mm": summary.get("simulation_max_displacement_mm"),
        "artifact_content_status": summary.get("artifact_content_status"),
        "cad_content_status": summary.get("cad_content_status"),
        "export_status": summary.get("export_status"),
        "export_failed": summary.get("export_failed"),
        "missing_requested_output_files": summary.get("missing_requested_output_files"),
        "output_files": summary.get("output_files"),
        "preview_files": summary.get("preview_files"),
        "assembly_status": summary.get("assembly_status"),
        "component_instance_count": summary.get("component_instance_count"),
        "component_definitions": summary.get("component_definitions"),
        "bom_status": summary.get("bom_status"),
        "bom_row_count": summary.get("bom_row_count"),
        "bom_columns": summary.get("bom_columns"),
        "pdf_semantic_content_status": summary.get("pdf_semantic_content_status"),
        "cleanup_status": summary.get("cleanup_status"),
        "cleanup_verification_status": summary.get("cleanup_verification_status"),
        "document_state_audit_status": summary.get("document_state_audit_status"),
        "document_state_after_cleanup_run_created_open_count": summary.get(
            "document_state_after_cleanup_run_created_open_count"
        ),
    }


def _production_scenarios(base_plan: dict[str, object]) -> list[dict[str, object]]:
    """Build deterministic production acceptance scenario plans."""

    flange_plan = _named_plan(_load_example_plan("flange_plan.json"), "center_hole_flange_prod_baseline")
    center_hole_plate_plan = _named_plan(
        _load_example_plan("center_hole_plate_plan.json"),
        "center_hole_plate_prod_baseline",
    )
    bracket_plan = _named_plan(_load_example_plan("bracket_plan.json"), "bracket_prod_baseline")
    end_cap_plan = _named_plan(_load_example_plan("end_cap_plan.json"), "end_cap_prod_baseline")
    mounting_block_plan = _named_plan(_load_example_plan("mounting_block_plan.json"), "mounting_block_prod_baseline")
    shaft_plan = _named_plan(_load_example_plan("shaft_plan.json"), "shaft_prod_baseline")
    sheet_metal_base_flange_plan = _named_plan(
        _load_example_plan("sheet_metal_base_flange_plan.json"),
        "sheet_metal_base_flange_prod_baseline",
    )
    weldment_frame_plan = _named_plan(_load_example_plan("weldment_frame_plan.json"), "weldment_frame_prod_baseline")
    simulation_cantilever_plan = _named_plan(
        _load_example_plan("simulation_cantilever_plan.json"),
        "simulation_cantilever_prod_baseline",
    )
    washer_plan = _named_plan(_load_example_plan("washer_plan.json"), "washer_prod_baseline")
    sleeve_plan = _named_plan(_load_example_plan("sleeve_plan.json"), "sleeve_prod_baseline")
    slotted_array_plate_plan = _named_plan(
        _load_example_plan("slotted_array_plate_plan.json"),
        "slotted_array_plate_prod_baseline",
    )
    bom_assembly_plan = _named_plan(_load_example_plan("bom_assembly_plan.json"), "bom_assembly_prod_baseline")
    atomic_baseline_plan = _atomic_baseline_plan()
    atomic_cut_baseline_plan = _atomic_cut_baseline_plan()
    atomic_pattern_baseline_plan = _atomic_pattern_baseline_plan()
    atomic_revolve_baseline_plan = _atomic_revolve_baseline_plan()
    atomic_sweep_baseline_plan = _atomic_sweep_baseline_plan()
    atomic_loft_baseline_plan = _atomic_loft_baseline_plan()
    return [
        {
            "name": "baseline",
            "description": "Baseline M6 threaded mounting plate with trusted geometry, drawing and cleanup gates.",
            "plan": _named_plan(base_plan, "m6_mounting_plate_prod_baseline"),
        },
        {
            "name": "material_alias",
            "description": "Material alias verification for Plain Carbon Steel on localized SW2022 installs.",
            "plan": _with_material_plan(
                _named_plan(base_plan, "m6_mounting_plate_prod_material_alias"),
                "Plain Carbon Steel",
            ),
        },
        {
            "name": "custom_properties",
            "description": "Custom property readback plus drawing metadata note and PDF semantic validation.",
            "plan": _with_custom_properties_plan(
                _named_plan(base_plan, "m6_mounting_plate_prod_custom_properties"),
            ),
        },
        {
            "name": "combined",
            "description": "Combined material, metadata, drawing and cleanup trusted production gate.",
            "plan": _with_custom_properties_plan(
                _with_material_plan(
                    _named_plan(base_plan, "m6_mounting_plate_prod_combined"),
                    "Plain Carbon Steel",
                ),
            ),
        },
        {
            "name": "drawing_exchange",
            "description": "Optional DXF drawing exchange export with artifact-content verification.",
            "plan": _with_output_formats_plan(
                _with_custom_properties_plan(
                    _with_material_plan(
                        _named_plan(base_plan, "m6_mounting_plate_prod_drawing_exchange"),
                        "Plain Carbon Steel",
                    ),
                ),
                ["sldprt", "step", "stl", "dxf"],
            ),
        },
        {
            "name": "neutral_exports",
            "description": "Optional IGES and Parasolid exchange exports with artifact-content verification.",
            "plan": _with_output_formats_plan(
                _with_custom_properties_plan(
                    _with_material_plan(
                        _named_plan(base_plan, "m6_mounting_plate_prod_neutral_exports"),
                        "Plain Carbon Steel",
                    ),
                ),
                ["sldprt", "step", "stl", "iges", "x_t", "x_b"],
            ),
        },
        {
            "name": "wide_combined",
            "description": "Wide controlled size variant with combined production metadata gates.",
            "plan": _with_custom_properties_plan(
                _with_material_plan(
                    _size_variant_plan(_named_plan(base_plan, "m6_mounting_plate_prod_wide_combined"), "wide"),
                    "Plain Carbon Steel",
                ),
            ),
        },
        {
            "name": "flange_baseline",
            "description": "Baseline controlled center-hole flange with trusted geometry, drawing dimensions, callout and cleanup gates.",
            "plan": flange_plan,
        },
        {
            "name": "center_hole_plate_baseline",
            "description": "Baseline controlled rectangular center-hole plate with trusted geometry, drawing dimensions, callout and cleanup gates.",
            "plan": center_hole_plate_plan,
        },
        {
            "name": "bracket_baseline",
            "description": "Baseline controlled L bracket with trusted geometry, drawing dimensions, hole callouts and cleanup gates.",
            "plan": bracket_plan,
        },
        {
            "name": "end_cap_baseline",
            "description": "Baseline controlled circular end cap with trusted geometry, drawing dimensions, hole callouts and cleanup gates.",
            "plan": end_cap_plan,
        },
        {
            "name": "mounting_block_baseline",
            "description": "Baseline controlled mounting block with trusted geometry, drawing dimensions, callout and cleanup gates.",
            "plan": mounting_block_plan,
        },
        {
            "name": "shaft_baseline",
            "description": "Baseline controlled plain shaft with trusted geometry, drawing dimensions and cleanup gates.",
            "plan": shaft_plan,
        },
        {
            "name": "sheet_metal_base_flange_baseline",
            "description": "Baseline controlled sheet-metal base flange with flat-pattern DXF, drawing dimensions and cleanup gates.",
            "plan": sheet_metal_base_flange_plan,
        },
        {
            "name": "weldment_frame_baseline",
            "description": "Baseline controlled structural-member weldment frame with cut-list CSV, drawing dimensions and cleanup gates.",
            "plan": weldment_frame_plan,
        },
        {
            "name": "simulation_cantilever_baseline",
            "description": "Baseline controlled cantilever static study with simulation CSV, drawing dimensions and cleanup gates.",
            "plan": simulation_cantilever_plan,
        },
        {
            "name": "washer_baseline",
            "description": "Baseline controlled washer with trusted geometry, drawing dimensions, callout and cleanup gates.",
            "plan": washer_plan,
        },
        {
            "name": "sleeve_baseline",
            "description": "Baseline controlled sleeve with trusted geometry, drawing dimensions, callout and cleanup gates.",
            "plan": sleeve_plan,
        },
        {
            "name": "slotted_array_plate_baseline",
            "description": "Baseline controlled slotted hole-array plate with trusted geometry, drawing dimensions, hole callouts and cleanup gates.",
            "plan": slotted_array_plate_plan,
        },
        {
            "name": "bom_assembly_baseline",
            "description": "Baseline controlled generated assembly with structure readback, BOM CSV, drawing, exports and cleanup gates.",
            "plan": bom_assembly_plan,
        },
        {
            "name": "atomic_baseline",
            "description": "Baseline staged atomic session with feature-graph ids, driven sketch dimension, hole callout, drawing and cleanup gates.",
            "plan": atomic_baseline_plan,
        },
        {
            "name": "atomic_cut_baseline",
            "description": "Focused staged atomic session proving named sketch replay for a second cut sketch before drawing and cleanup gates.",
            "plan": atomic_cut_baseline_plan,
        },
        {
            "name": "atomic_pattern_baseline",
            "description": "Focused staged atomic session proving named feature references through a production linear pattern before drawing and cleanup gates.",
            "plan": atomic_pattern_baseline_plan,
        },
        {
            "name": "atomic_revolve_baseline",
            "description": "Focused staged atomic session proving named sketch and axis references through a revolved feature before drawing and cleanup gates.",
            "plan": atomic_revolve_baseline_plan,
        },
        {
            "name": "atomic_sweep_baseline",
            "description": "Focused staged atomic session proving named profile/path references through a swept feature before drawing and cleanup gates.",
            "plan": atomic_sweep_baseline_plan,
        },
        {
            "name": "atomic_loft_baseline",
            "description": "Focused staged atomic session proving ordered named profile references through a lofted feature before drawing and cleanup gates.",
            "plan": atomic_loft_baseline_plan,
        },
    ]


def _load_example_plan(filename: str) -> dict[str, object]:
    """Load an example plan from the repository examples directory."""

    return json.loads((ROOT / "examples" / filename).read_text(encoding="utf-8"))


def _atomic_baseline_plan() -> dict[str, object]:
    """Build the deterministic release-gate plan through the atomic session protocol."""

    operations = [
        {
            "id": "sketch_base",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [
                    {"id": "base_rect", "type": "center_rectangle", "center": [0, 0], "width": 80, "height": 40}
                ],
                "dimensions": [{"id": "dim_width", "entity_id": "base_rect", "type": "width", "value": 80}],
                "constraints": [{"type": "horizontal", "entity_id": "base_rect"}],
            },
        },
        {"id": "boss_base", "op": "extrude", "parameters": {"sketch_id": "sketch_base", "depth": 8}},
        {"id": "hole_a", "op": "hole", "parameters": {"target_face": "front", "position": [0, 0], "diameter": 8, "depth": 8}},
        {"op": "make_drawing", "parameters": {}},
    ]
    return _atomic_session_plan(
        name="atomic_model_prod_baseline",
        session_id="atomic_release_baseline",
        operations=operations,
    )


def _atomic_cut_baseline_plan() -> dict[str, object]:
    """Build a release-gate atomic plan that proves named cut-sketch replay."""

    operations = [
        {
            "id": "sketch_base",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [
                    {"id": "base_rect", "type": "center_rectangle", "center": [0, 0], "width": 80, "height": 40}
                ],
                "dimensions": [{"id": "dim_width", "entity_id": "base_rect", "type": "width", "value": 80}],
                "constraints": [{"type": "horizontal", "entity_id": "base_rect"}],
            },
        },
        {"id": "boss_base", "op": "extrude", "parameters": {"sketch_id": "sketch_base", "depth": 8}},
        {
            "id": "sketch_cut",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [{"id": "cut_circle", "type": "circle", "center": [0, 0], "diameter": 18}],
            },
        },
        {"id": "center_cut", "op": "cut", "parameters": {"sketch_id": "sketch_cut", "depth": 8}},
        {"op": "make_drawing", "parameters": {}},
    ]
    return _atomic_session_plan(
        name="atomic_model_prod_cut_baseline",
        session_id="atomic_release_cut_baseline",
        operations=operations,
    )


def _atomic_pattern_baseline_plan() -> dict[str, object]:
    """Build a release-gate atomic plan that proves named feature pattern replay."""

    operations = [
        {
            "id": "sketch_base",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [
                    {"id": "pattern_base_rect", "type": "center_rectangle", "center": [0, 0], "width": 96, "height": 48}
                ],
                "dimensions": [
                    {
                        "id": "dim_pattern_width",
                        "entity_id": "pattern_base_rect",
                        "type": "width",
                        "value": 96,
                    }
                ],
                "constraints": [{"type": "horizontal", "entity_id": "pattern_base_rect"}],
            },
        },
        {"id": "boss_base", "op": "extrude", "parameters": {"sketch_id": "sketch_base", "depth": 8}},
        {
            "id": "hole_seed",
            "op": "hole",
            "parameters": {"target_face": "front", "position": [-24, 0], "diameter": 6, "depth": 8},
        },
        {
            "id": "linear_hole_pattern",
            "op": "linear_pattern",
            "parameters": {"seed_id": "hole_seed", "direction": "x_axis", "spacing": 24, "count": 3},
        },
        {"op": "make_drawing", "parameters": {}},
    ]
    return _atomic_session_plan(
        name="atomic_model_prod_pattern_baseline",
        session_id="atomic_release_pattern_baseline",
        operations=operations,
    )


def _atomic_revolve_baseline_plan() -> dict[str, object]:
    """Build a release-gate atomic plan that proves named revolve-axis references."""

    operations = [
        {
            "id": "sketch_profile",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [
                    {
                        "id": "revolve_axis_line",
                        "type": "line",
                        "start": [0, -20],
                        "end": [0, 20],
                        "construction": True,
                    },
                    {
                        "id": "revolve_profile_rect",
                        "type": "center_rectangle",
                        "center": [14, 0],
                        "width": 12,
                        "height": 28,
                    }
                ],
                "dimensions": [
                    {
                        "id": "dim_revolve_outer_diameter",
                        "entity_id": "revolve_profile_rect",
                        "type": "outer_diameter",
                        "value": 40,
                    }
                ],
                "constraints": [{"type": "vertical", "entity_id": "revolve_profile_rect"}],
            },
        },
        {
            "id": "revolve_body",
            "op": "revolve",
            "parameters": {"sketch_id": "sketch_profile", "axis": "revolve_axis_line", "angle": 360},
        },
        {"op": "make_drawing", "parameters": {}},
    ]
    return _atomic_session_plan(
        name="atomic_model_prod_revolve_baseline",
        session_id="atomic_release_revolve_baseline",
        operations=operations,
    )


def _atomic_sweep_baseline_plan() -> dict[str, object]:
    """Build a release-gate atomic plan that proves named sweep profile/path references."""

    operations = [
        {
            "id": "sketch_sweep_profile",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [
                    {"id": "sweep_profile_circle", "type": "circle", "center": [0, 0], "diameter": 16}
                ],
                "dimensions": [
                    {
                        "id": "dim_sweep_profile_diameter",
                        "entity_id": "sweep_profile_circle",
                        "type": "diameter",
                        "value": 16,
                    }
                ],
            },
        },
        {
            "id": "sketch_sweep_path",
            "op": "create_sketch",
            "parameters": {
                "plane": "top",
                "entities": [{"id": "sweep_path_line", "type": "line", "start": [0, 0], "end": [60, 0]}],
            },
        },
        {
            "id": "sweep_body",
            "op": "sweep",
            "parameters": {
                "profile_sketch_id": "sketch_sweep_profile",
                "profile_id": "sweep_profile_circle",
                "profile_diameter": 16,
                "path_sketch_id": "sweep_path_line",
            },
        },
        {"op": "make_drawing", "parameters": {}},
    ]
    return _atomic_session_plan(
        name="atomic_model_prod_sweep_baseline",
        session_id="atomic_release_sweep_baseline",
        operations=operations,
    )


def _atomic_loft_baseline_plan() -> dict[str, object]:
    """Build a release-gate atomic plan that proves ordered loft profile references."""

    operations = [
        {
            "id": "sketch_loft_profile_a",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [{"id": "loft_profile_a_circle", "type": "circle", "center": [0, 0], "diameter": 24}],
                "dimensions": [
                    {
                        "id": "dim_loft_primary_diameter",
                        "entity_id": "loft_profile_a_circle",
                        "type": "diameter",
                        "value": 24,
                    }
                ],
            },
        },
        {
            "id": "loft_offset_plane",
            "op": "create_plane",
            "parameters": {"base_plane": "front", "distance": 36},
        },
        {
            "id": "sketch_loft_profile_b",
            "op": "create_sketch",
            "parameters": {
                "plane": "loft_offset_plane",
                "entities": [{"id": "loft_profile_b_circle", "type": "circle", "center": [0, 0], "diameter": 10}],
            },
        },
        {
            "id": "loft_body",
            "op": "loft",
            "parameters": {"profile_sketch_ids": ["sketch_loft_profile_a", "sketch_loft_profile_b"]},
        },
        {"op": "make_drawing", "parameters": {}},
    ]
    return _atomic_session_plan(
        name="atomic_model_prod_loft_baseline",
        session_id="atomic_release_loft_baseline",
        operations=operations,
    )


def _atomic_session_plan(
    *,
    name: str,
    session_id: str,
    operations: list[dict[str, object]],
) -> dict[str, object]:
    """Build a deterministic release-gate plan through the atomic session protocol."""

    sessions = AtomicSessionManager(None)
    start = sessions.start_model_session(
        name,
        metadata={"production_scenario": name},
        output_formats=["sldprt", "step", "stl", "slddrw", "pdf", "dwg"],
    )
    if start.get("ok") is not True:
        raise RuntimeError(f"Could not start atomic release plan {name}: {start}")
    generated_session_id = str(start["session_id"])
    session = sessions._sessions.pop(generated_session_id)
    session.session_id = session_id
    sessions._sessions[session.session_id] = session
    for operation in operations:
        result = sessions.apply_model_operation(session_id, operation)
        if result.get("ok") is not True:
            raise RuntimeError(f"Could not stage atomic release operation {operation.get('op')}: {result}")
    return sessions._sessions[session_id].to_plan().to_dict()


def _variant_plan(base_plan: dict[str, object], thread_spec: str) -> dict[str, object]:
    """Create one deterministic mounting-plate thread variant from the base plan."""

    plan = copy.deepcopy(base_plan)
    base_name = str(plan.get("name", "mounting_plate")).lower()
    plan["name"] = base_name.replace("m6", thread_spec.lower()) if "m6" in base_name else f"{base_name}_{thread_spec.lower()}"
    metadata = plan.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["thread_spec"] = thread_spec
        metadata["description"] = (
            f"Smoke-test mounting plate variant: 120 x 80 x 10 mm, R5 corners, "
            f"four {thread_spec} ISO metric coarse threaded through holes."
        )
    for operation in plan.get("operations", []):
        if not isinstance(operation, dict) or operation.get("op") != "create_mounting_plate":
            continue
        operation["description"] = f"Create the controlled {thread_spec} mounting plate smoke-test part."
        params = operation.get("parameters", {})
        if isinstance(params, dict):
            params["thread_spec"] = thread_spec
    return plan


def _named_plan(base_plan: dict[str, object], name: str) -> dict[str, object]:
    """Return a deep-copied plan with a deterministic scenario name."""

    plan = copy.deepcopy(base_plan)
    plan["name"] = name
    metadata = plan.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["production_scenario"] = name
    return plan


def _with_output_formats_plan(base_plan: dict[str, object], output_formats: list[str]) -> dict[str, object]:
    """Return a plan that requests a deterministic model export format set."""

    plan = copy.deepcopy(base_plan)
    plan["output_formats"] = list(output_formats)
    return plan


def _with_material_plan(base_plan: dict[str, object], material: str) -> dict[str, object]:
    """Add or replace the controlled material operation before drawing creation."""

    plan = copy.deepcopy(base_plan)
    _upsert_operation_before_drawing(
        plan,
        "assign_material",
        {
            "id": "material",
            "op": "assign_material",
            "description": "Assign a production material for material-gate regression.",
            "parameters": {"material": material},
        },
    )
    return plan


def _with_custom_properties_plan(base_plan: dict[str, object]) -> dict[str, object]:
    """Add or replace manufacturing metadata before drawing creation."""

    plan = copy.deepcopy(base_plan)
    params = _mounting_plate_parameters(plan)
    length = _property_dimension_token(params.get("length", 120))
    width = _property_dimension_token(params.get("width", 80))
    thread_spec = str(params.get("thread_spec", "M6")).upper()
    _upsert_operation_before_drawing(
        plan,
        "set_custom_properties",
        {
            "id": "custom_properties",
            "op": "set_custom_properties",
            "description": "Assign trusted manufacturing metadata.",
            "parameters": {
                "properties": {
                    "PartNo": f"MP-{length}-{width}-{thread_spec}",
                    "Revision": "A",
                    "Description": f"Mounting plate smoke fixture {length}x{width}",
                },
            },
        },
    )
    return plan


def _mounting_plate_parameters(plan: dict[str, object]) -> dict[str, object]:
    """Return mounting plate operation parameters from a plain dict plan."""

    operations = plan.get("operations", [])
    if not isinstance(operations, list):
        return {}
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("op") != "create_mounting_plate":
            continue
        params = operation.get("parameters", {})
        return params if isinstance(params, dict) else {}
    return {}


def _property_dimension_token(value: object) -> str:
    """Format a dimension token for deterministic custom properties."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):03d}"
    return str(number).replace(".", "p")


def _upsert_operation_before_drawing(
    plan: dict[str, object],
    op_name: str,
    operation: dict[str, object],
) -> None:
    """Replace a matching operation or insert it before make_drawing."""

    operations = plan.setdefault("operations", [])
    if not isinstance(operations, list):
        plan["operations"] = [operation]
        return
    for index, existing in enumerate(operations):
        if isinstance(existing, dict) and existing.get("op") == op_name:
            operations[index] = operation
            return
    drawing_index = next(
        (
            index
            for index, existing in enumerate(operations)
            if isinstance(existing, dict) and existing.get("op") == "make_drawing"
        ),
        len(operations),
    )
    operations.insert(drawing_index, operation)


def _size_variant_plan(base_plan: dict[str, object], size_variant: str) -> dict[str, object]:
    """Apply a controlled mounting-plate size variant."""

    if size_variant == "default":
        return base_plan
    plan = copy.deepcopy(base_plan)
    plan["name"] = f"{str(plan.get('name', 'mounting_plate'))}_{size_variant}"
    metadata = plan.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["size_variant"] = size_variant
        metadata["description"] = (
            "Smoke-test mounting plate size variant: 140 x 90 x 12 mm, "
            "R6 corners, 18 mm edge offset."
        )
    for operation in plan.get("operations", []):
        if not isinstance(operation, dict) or operation.get("op") != "create_mounting_plate":
            continue
        operation["description"] = "Create the controlled wide mounting plate smoke-test part."
        params = operation.get("parameters", {})
        if isinstance(params, dict):
            params.update({
                "length": 140,
                "width": 90,
                "thickness": 12,
                "corner_radius": 6,
                "edge_offset": 18,
            })
    return plan


def _config_for_variant(config: SolidWorksMCPConfig, thread_spec: str) -> SolidWorksMCPConfig:
    """Set a stable run id per matrix variant unless the caller already supplied one."""

    return _config_for_run_suffix(config, thread_spec.lower(), default_prefix="matrix")


def _config_for_run_suffix(
    config: SolidWorksMCPConfig,
    suffix: str,
    default_prefix: str = "suite",
) -> SolidWorksMCPConfig:
    """Set a stable run id suffix for independent smoke executions."""

    safe_suffix = "".join(char if char.isalnum() else "_" for char in suffix.lower()).strip("_")
    run_id = (
        f"{config.run_id}_{safe_suffix}"
        if config.run_id
        else f"{default_prefix}_{safe_suffix}_{os.getpid()}"
    )
    return SolidWorksMCPConfig(
        adapter=config.adapter,
        output_root=config.output_root,
        part_template=config.part_template,
        drawing_template=config.drawing_template,
        visible=config.visible,
        macro_fallback_enabled=config.macro_fallback_enabled,
        macro_execution_disabled=config.macro_execution_disabled,
        force_holewizard_failure=config.force_holewizard_failure,
        force_drawing_callout_failure=config.force_drawing_callout_failure,
        force_drawing_dimension_failure=config.force_drawing_dimension_failure,
        force_cad_content_failure=config.force_cad_content_failure,
        force_cleanup_failure=config.force_cleanup_failure,
        force_export_failure=config.force_export_failure,
        force_model_geometry_failure=config.force_model_geometry_failure,
        force_material_failure=config.force_material_failure,
        force_preflight_failure=config.force_preflight_failure,
        enforce_trusted_workflow=config.enforce_trusted_workflow,
        require_direct_hole_callout=config.require_direct_hole_callout,
        close_documents_after_run=config.close_documents_after_run,
        cleanup_attach_only=config.cleanup_attach_only,
        debug_level=config.debug_level,
        run_id=run_id,
    )


def _offline_diagnosis_for_execution(execution: dict[str, object]) -> dict[str, object]:
    """Run the client-facing offline diagnosis for a completed smoke run."""

    run_dir = execution.get("run_dir")
    if not run_dir:
        return {
            "ok": False,
            "failure_reason": "execution did not return run_dir",
            "artifact_integrity_status": None,
            "delivery_manifest_status": None,
            "environment_status": None,
        }
    try:
        return diagnose_run_directory(str(run_dir), summary_only=True)
    except Exception as exc:
        return {
            "ok": False,
            "failure_reason": str(exc),
            "artifact_integrity_status": None,
            "delivery_manifest_status": None,
            "environment_status": None,
        }


def _merge_offline_diagnosis_acceptance(
    acceptance: dict[str, object],
    offline_diagnosis: dict[str, object],
) -> dict[str, object]:
    """Require the offline handoff diagnosis in addition to execution acceptance."""

    merged = dict(acceptance)
    checks = dict(merged.get("checks", {})) if isinstance(merged.get("checks"), dict) else {}
    checks.update(
        {
            "offline_run_diagnosis": offline_diagnosis.get("ok") is True,
            "offline_artifact_integrity": offline_diagnosis.get("artifact_integrity_status") == "verified",
            "offline_event_log": offline_diagnosis.get("event_log_status") == "verified",
            "offline_delivery_manifest": offline_diagnosis.get("delivery_manifest_status") == "verified",
            "offline_environment": offline_diagnosis.get("environment_status") == "verified",
        }
    )
    failures = list(merged.get("failures", [])) if isinstance(merged.get("failures"), list) else []
    for name, ok in checks.items():
        if not ok and name not in failures:
            failures.append(name)

    merged["checks"] = checks
    merged["failures"] = failures
    merged["repair_actions"] = build_repair_actions(
        failures,
        merged.get("summary") if isinstance(merged.get("summary"), dict) else {},
    )
    merged["ok"] = not failures
    if "status" in merged:
        merged["status"] = "accepted" if merged["ok"] else "rejected"
    merged["offline_diagnosis"] = {
        "ok": offline_diagnosis.get("ok"),
        "artifact_integrity_status": offline_diagnosis.get("artifact_integrity_status"),
        "event_log_status": offline_diagnosis.get("event_log_status"),
        "failed_event_count": offline_diagnosis.get("failed_event_count"),
        "recovered_probe_event_count": offline_diagnosis.get("recovered_probe_event_count"),
        "recovered_export_event_count": offline_diagnosis.get("recovered_export_event_count"),
        "recovered_preflight_event_count": offline_diagnosis.get("recovered_preflight_event_count"),
        "delivery_manifest_status": offline_diagnosis.get("delivery_manifest_status"),
        "delivery_manifest_issues": offline_diagnosis.get("delivery_manifest_issues"),
        "environment_status": offline_diagnosis.get("environment_status"),
        "environment_issues": offline_diagnosis.get("environment_issues"),
        "missing_artifacts": offline_diagnosis.get("missing_artifacts"),
    }
    return merged


def _smoke_acceptance(execution: dict[str, object]) -> dict[str, object]:
    """Evaluate the mounting-plate trusted smoke criteria."""

    diagnostics = execution.get("diagnostics", {})
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    production_result = diagnostics.get("production_acceptance_result", {})
    if isinstance(production_result, dict) and production_result:
        return production_result
    preflight_result = diagnostics.get("preflight_result", {})
    preflight_result = preflight_result if isinstance(preflight_result, dict) else {}
    if execution.get("failure_class") == "preflight" or diagnostics.get("preflight_status") == "failed":
        failures = [
            str(item)
            for item in preflight_result.get("failures", [])
            if item
        ] or ["preflight_ready"]
        summary = {
            "preflight_status": diagnostics.get("preflight_status"),
            "preflight_failures": failures,
        }
        return {
            "status": "rejected",
            "ok": False,
            "checks": {failure: False for failure in failures},
            "failures": failures,
            "repair_actions": build_repair_actions(failures, summary),
            "summary": summary,
            "expected": {"preflight_status": "ready"},
        }

    cleanup_result = diagnostics.get("cleanup_result", {})
    cleanup_result = cleanup_result if isinstance(cleanup_result, dict) else {}
    artifact_result = diagnostics.get("artifact_validation_result", {})
    artifact_result = artifact_result if isinstance(artifact_result, dict) else {}
    content_result = diagnostics.get("artifact_content_result", {})
    content_result = content_result if isinstance(content_result, dict) else {}
    output_files = execution.get("output_files", {})
    output_files = output_files if isinstance(output_files, dict) else {}
    preview_files = execution.get("preview_files", {})
    preview_files = preview_files if isinstance(preview_files, dict) else {}
    checks = {
        "execution_ok": bool(execution.get("ok")),
        "preflight_status": diagnostics.get("preflight_status") == "ready",
        "thread_model_status": diagnostics.get("thread_model_status")
        in {"holewizard_threaded_hole", "macro_threaded_hole"},
        "corner_radius_status": diagnostics.get("corner_radius_status") == "fillet_feature",
        "model_geometry_status": diagnostics.get("model_geometry_status") == "geometry_verified",
        "drawing_annotation_status": diagnostics.get("drawing_annotation_status") == "hole_callout_created",
        "drawing_dimension_status": diagnostics.get("drawing_dimension_status") == "basic_dimensions_created",
        "artifact_validation_status": artifact_result.get("ok") is True
        and artifact_result.get("status") == "artifacts_ready",
        "artifact_content_status": content_result.get("ok") is True
        and content_result.get("status") in {
            "content_ready",
            "mock_preview_placeholders",
            "mock_output_placeholders",
        },
        "required_output_files": {"sldprt", "step", "stl", "slddrw", "pdf", "dwg"}.issubset(set(output_files)),
        "required_preview_files": {"front", "top", "right", "isometric"}.issubset(set(preview_files)),
        "cleanup_enabled": cleanup_result.get("enabled") is True,
        "cleanup_status": cleanup_result.get("status") in {"completed", "skipped_no_documents"},
        "cleanup_verified": cleanup_result.get("status") == "skipped_no_documents"
        or cleanup_result.get("cleanup_verification_status") == "verified",
    }
    failures = [name for name, ok in checks.items() if not ok]
    return {
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "repair_actions": build_repair_actions(failures, {}),
        "expected": {
            "preflight_status": "ready",
            "thread_model_status": ["holewizard_threaded_hole", "macro_threaded_hole"],
            "corner_radius_status": "fillet_feature",
            "model_geometry_status": "geometry_verified",
            "drawing_annotation_status": "hole_callout_created",
            "drawing_dimension_status": "basic_dimensions_created",
            "artifact_validation_status": "artifacts_ready",
            "artifact_content_status": ["content_ready", "mock_preview_placeholders", "mock_output_placeholders"],
            "required_output_files": ["dwg", "pdf", "slddrw", "sldprt", "step", "stl"],
            "required_preview_files": ["front", "isometric", "right", "top"],
            "cleanup_enabled": True,
            "cleanup_status": ["completed", "skipped_no_documents"],
            "cleanup_verification_status": "verified unless no documents were created",
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
