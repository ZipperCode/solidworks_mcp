"""Run a production release gate over trusted SolidWorks MCP workflows."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import traceback
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPT_DIR = Path(__file__).resolve().parent
for path in (SRC, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from solidworks_mcp.adapters import create_adapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.executor import ModelPlanExecutor
from solidworks_mcp.run_diagnostics import diagnose_run_collection
from smoke_mounting_plate import _compact_result_item, _load_example_plan, _run_production_suite


DEFAULT_SCENARIOS = (
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

OPTIONAL_SCENARIOS = (
    "simulation_cantilever_baseline",
)


def parse_args() -> argparse.Namespace:
    """Parse release-gate command line arguments."""

    parser = argparse.ArgumentParser(
        description="Run trusted workflow smoke scenarios and batch-diagnose the generated run set.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Force the mock adapter for local dry-runs.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Dedicated gate output root. Defaults to <configured output>/release_gate_<timestamp>.",
    )
    parser.add_argument(
        "--scenario",
        dest="scenarios",
        action="append",
        choices=DEFAULT_SCENARIOS + OPTIONAL_SCENARIOS + ("all",),
        help=(
            "Scenario to include. Repeat for multiple scenarios; default/all is the trusted production set; "
            "simulation_cantilever_baseline is explicit-only."
        ),
    )
    parser.add_argument(
        "--run-id-prefix",
        default=None,
        help="Stable run id prefix. Defaults to release_gate_<timestamp>.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only compact release-gate fields.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the release gate and return a process status suitable for CI."""

    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    config = SolidWorksMCPConfig.from_env()
    adapter = "mock" if args.mock else config.adapter
    run_id_prefix = args.run_id_prefix or f"release_gate_{timestamp}"
    output_root = (args.output_root or (config.output_root / run_id_prefix)).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    gate_config = _config_for_release_gate(config, adapter, output_root, run_id_prefix)
    base_plan = _load_example_plan("mounting_plate_plan.json")
    scenario_names = _scenario_names(args.scenarios)

    smoke_results = []
    try:
        for scenario_name in scenario_names:
            smoke_results.append(_run_production_suite(base_plan, gate_config, scenario_name))

        batch_diagnosis = diagnose_run_collection(output_root, summary_only=True, max_runs=0)
        payload = _release_gate_payload(
            adapter=adapter,
            output_root=output_root,
            scenario_names=scenario_names,
            smoke_results=smoke_results,
            batch_diagnosis=batch_diagnosis,
        )
    except KeyboardInterrupt as exc:
        payload = _release_gate_exception_payload(
            adapter=adapter,
            output_root=output_root,
            scenario_names=scenario_names,
            smoke_results=smoke_results,
            config=gate_config,
            exc=exc,
            failure_class="release_gate_interrupted",
        )
        report_file = _write_release_gate_report(output_root, payload)
        payload["report_file"] = str(report_file)
        print(json.dumps(_compact_release_payload(payload) if args.summary_only else payload, indent=2, ensure_ascii=False))
        return 130
    except Exception as exc:
        payload = _release_gate_exception_payload(
            adapter=adapter,
            output_root=output_root,
            scenario_names=scenario_names,
            smoke_results=smoke_results,
            config=gate_config,
            exc=exc,
            failure_class="release_gate_exception",
        )
        report_file = _write_release_gate_report(output_root, payload)
        payload["report_file"] = str(report_file)
        print(json.dumps(_compact_release_payload(payload) if args.summary_only else payload, indent=2, ensure_ascii=False))
        return 1

    report_file = _write_release_gate_report(output_root, payload)
    payload["report_file"] = str(report_file)
    print(json.dumps(_compact_release_payload(payload) if args.summary_only else payload, indent=2, ensure_ascii=False))
    return 0 if payload["ok"] else 1


def _scenario_names(raw: list[str] | None) -> list[str]:
    """Return the requested release-gate scenarios in execution order."""

    if not raw:
        return list(DEFAULT_SCENARIOS)
    if "all" in raw:
        return list(DEFAULT_SCENARIOS)
    seen: set[str] = set()
    names: list[str] = []
    for item in raw:
        if item not in seen:
            seen.add(item)
            names.append(item)
    return names


def _config_for_release_gate(
    config: SolidWorksMCPConfig,
    adapter: str,
    output_root: Path,
    run_id_prefix: str,
) -> SolidWorksMCPConfig:
    """Return a config scoped to one release-gate output root."""

    return SolidWorksMCPConfig(
        adapter=adapter,
        output_root=output_root,
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
        run_id=run_id_prefix,
    )


def _release_gate_payload(
    *,
    adapter: str,
    output_root: Path,
    scenario_names: list[str],
    smoke_results: list[dict[str, object]],
    batch_diagnosis: dict[str, object],
) -> dict[str, object]:
    """Return the full release-gate verdict."""

    scenario_results = []
    for suite_result in smoke_results:
        for item in suite_result.get("results", []) if isinstance(suite_result.get("results"), list) else []:
            if isinstance(item, dict):
                scenario_results.append(_compact_result_item(item))
    failed_smoke_scenarios = [
        str(item.get("scenario"))
        for item in scenario_results
        if item.get("ok") is not True
    ]
    missing_scenarios = sorted(set(scenario_names) - {str(item.get("scenario")) for item in scenario_results})
    evidence_summary = _release_evidence_summary(scenario_results)
    evidence_checks = _release_evidence_checks(evidence_summary)
    checks = {
        "scenario_smoke": not failed_smoke_scenarios and not missing_scenarios,
        "batch_diagnosis": batch_diagnosis.get("ok") is True,
        "batch_complete": batch_diagnosis.get("scan_status") == "complete",
        "batch_no_rejections": batch_diagnosis.get("rejected_count") == 0,
        "batch_count_matches": batch_diagnosis.get("run_count") == len(scenario_names),
        **evidence_checks,
    }
    failures = [name for name, ok in checks.items() if not ok]
    return {
        "ok": not failures,
        "status": "accepted" if not failures else "rejected",
        "schema_version": "2026-06-06.1",
        "adapter": adapter,
        "output_root": str(output_root),
        "scenarios": scenario_names,
        "checks": checks,
        "failures": failures,
        "failed_smoke_scenarios": failed_smoke_scenarios,
        "missing_scenarios": missing_scenarios,
        "evidence_summary": evidence_summary,
        "scenario_results": scenario_results,
        "batch_diagnosis": batch_diagnosis,
    }


def _release_gate_exception_payload(
    *,
    adapter: str,
    output_root: Path,
    scenario_names: list[str],
    smoke_results: list[dict[str, object]],
    config: SolidWorksMCPConfig,
    exc: BaseException,
    failure_class: str,
) -> dict[str, object]:
    """Return and persist as much release-gate evidence as possible after a crash."""

    try:
        batch_diagnosis = diagnose_run_collection(output_root, summary_only=True, max_runs=0)
    except Exception as diagnosis_exc:
        batch_diagnosis = {
            "ok": False,
            "scan_status": "diagnosis_failed",
            "run_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "issue_counts": {"release_gate:diagnosis_failed": 1},
            "failure_reason": str(diagnosis_exc),
        }
    payload = _release_gate_payload(
        adapter=adapter,
        output_root=output_root,
        scenario_names=scenario_names,
        smoke_results=smoke_results,
        batch_diagnosis=batch_diagnosis,
    )
    cleanup_result = _emergency_cleanup_completed_runs(output_root, config)
    failures = list(payload.get("failures", [])) if isinstance(payload.get("failures"), list) else []
    if failure_class not in failures:
        failures.append(failure_class)
    payload.update(
        {
            "ok": False,
            "status": "rejected",
            "failure_class": failure_class,
            "failure_reason": str(exc),
            "traceback": traceback.format_exception_only(type(exc), exc),
            "failures": failures,
            "emergency_cleanup_result": cleanup_result,
        }
    )
    checks = dict(payload.get("checks", {})) if isinstance(payload.get("checks"), dict) else {}
    checks[failure_class] = False
    checks["emergency_cleanup_attempted"] = cleanup_result.get("status") in {
        "completed",
        "skipped_no_runs",
        "partial",
        "failed",
    }
    payload["checks"] = checks
    return payload


def _emergency_cleanup_completed_runs(output_root: Path, config: SolidWorksMCPConfig) -> dict[str, object]:
    """Best-effort close run-created documents after an interrupted release gate."""

    run_dirs = _completed_run_dirs(output_root)
    result: dict[str, object] = {
        "status": "skipped_no_runs",
        "run_count": len(run_dirs),
        "attempted_count": 0,
        "results": [],
        "failure_reason": None,
    }
    if not run_dirs:
        return result

    try:
        executor = ModelPlanExecutor(create_adapter(config), config)
    except Exception as exc:
        result.update(
            {
                "status": "failed",
                "failure_reason": f"cleanup_executor_init_failed: {exc}",
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
            "failure_reason": None if not failed else "One or more post-interruption cleanup attempts failed.",
        }
    )
    return result


def _completed_run_dirs(output_root: Path) -> list[Path]:
    """Return run directories with reports below a release-gate output root."""

    if not output_root.exists():
        return []
    return sorted({path.parent.resolve() for path in output_root.rglob("execution_report.json")})


def _write_release_gate_report(output_root: Path, payload: dict[str, object]) -> Path:
    """Persist the release-gate verdict for archive and handoff review."""

    report_file = output_root / "release_gate_report.json"
    report_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return report_file


def _compact_release_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return compact release-gate fields for CI and operator review."""

    batch = payload.get("batch_diagnosis")
    batch = batch if isinstance(batch, dict) else {}
    return {
        "ok": payload.get("ok"),
        "status": payload.get("status"),
        "adapter": payload.get("adapter"),
        "output_root": payload.get("output_root"),
        "report_file": payload.get("report_file"),
        "scenarios": payload.get("scenarios"),
        "failures": payload.get("failures"),
        "failed_smoke_scenarios": payload.get("failed_smoke_scenarios"),
        "missing_scenarios": payload.get("missing_scenarios"),
        "batch": {
            "ok": batch.get("ok"),
            "scan_status": batch.get("scan_status"),
            "run_count": batch.get("run_count"),
            "accepted_count": batch.get("accepted_count"),
            "rejected_count": batch.get("rejected_count"),
            "issue_counts": batch.get("issue_counts"),
        },
        "evidence_summary": payload.get("evidence_summary"),
        "failure_class": payload.get("failure_class"),
        "failure_reason": payload.get("failure_reason"),
        "emergency_cleanup_result": _compact_emergency_cleanup(payload.get("emergency_cleanup_result")),
        "scenario_results": payload.get("scenario_results"),
    }


def _compact_emergency_cleanup(value: object) -> dict[str, object] | None:
    """Return compact post-interruption cleanup fields for operator summaries."""

    if not isinstance(value, dict):
        return None
    return {
        "status": value.get("status"),
        "run_count": value.get("run_count"),
        "attempted_count": value.get("attempted_count"),
        "failure_reason": value.get("failure_reason"),
    }


def _release_evidence_summary(scenario_results: list[dict[str, object]]) -> dict[str, object]:
    """Return top-level production evidence aggregated from scenario results."""

    scenario_count = len(scenario_results)
    return {
        "scenario_count": scenario_count,
        "accepted_scenarios": sorted(str(item.get("scenario")) for item in scenario_results if item.get("ok") is True),
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
            lambda item: item.get("thread_model_status") in {"holewizard_threaded_hole", "macro_threaded_hole", "not_requested"},
        ),
        "fillet_feature_count": _count_results(
            scenario_results,
            lambda item: item.get("corner_radius_status") in {"fillet_feature", "not_requested"},
        ),
    }


def _release_evidence_checks(evidence_summary: dict[str, object]) -> dict[str, bool]:
    """Return production evidence checks for the release gate."""

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


def _count_results(scenario_results: list[dict[str, object]], predicate: Any) -> int:
    """Count scenarios that satisfy a predicate."""

    return sum(1 for item in scenario_results if callable(predicate) and predicate(item))


def _is_assembly_workflow(item: dict[str, object]) -> bool:
    """Return whether a compact result belongs to the controlled assembly+BOM workflow."""

    return item.get("trusted_workflow_status") == "controlled_bom_assembly"


def _is_sheet_metal_workflow(item: dict[str, object]) -> bool:
    """Return whether a compact result belongs to the controlled sheet-metal workflow."""

    return item.get("trusted_workflow_status") == "controlled_sheet_metal_base_flange"


def _is_weldment_workflow(item: dict[str, object]) -> bool:
    """Return whether a compact result belongs to the controlled weldment workflow."""

    return item.get("trusted_workflow_status") == "controlled_weldment_frame"


def _is_simulation_workflow(item: dict[str, object]) -> bool:
    """Return whether a compact result belongs to the controlled static simulation workflow."""

    return item.get("trusted_workflow_status") == "controlled_static_simulation"


def _assembly_structure_ok(item: dict[str, object]) -> bool:
    """Return whether compact evidence proves a controlled assembly structure."""

    try:
        component_count = int(item.get("component_instance_count") or 0)
    except (TypeError, ValueError):
        component_count = 0
    return _is_assembly_workflow(item) and item.get("assembly_status") == "assembly_verified" and component_count >= 2


def _bom_ok(item: dict[str, object]) -> bool:
    """Return whether compact evidence proves a controlled BOM."""

    try:
        row_count = int(item.get("bom_row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    return _is_assembly_workflow(item) and item.get("bom_status") == "bom_verified" and row_count >= 2


def _sheet_metal_ok(item: dict[str, object]) -> bool:
    """Return whether compact evidence proves a controlled sheet-metal feature."""

    return _is_sheet_metal_workflow(item) and item.get("sheet_metal_status") == "sheet_metal_verified"


def _sheet_metal_flat_pattern_ok(item: dict[str, object]) -> bool:
    """Return whether compact evidence proves a flat-pattern DXF export."""

    return _is_sheet_metal_workflow(item) and item.get("flat_pattern_status") == "flat_pattern_exported"


def _weldment_ok(item: dict[str, object]) -> bool:
    """Return whether compact evidence proves a controlled weldment feature."""

    try:
        body_count = int(item.get("weldment_body_count") or 0)
    except (TypeError, ValueError):
        body_count = 0
    return _is_weldment_workflow(item) and item.get("weldment_status") == "weldment_verified" and body_count >= 4


def _cut_list_ok(item: dict[str, object]) -> bool:
    """Return whether compact evidence proves a weldment cut list."""

    try:
        row_count = int(item.get("cut_list_row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    return _is_weldment_workflow(item) and item.get("cut_list_status") == "cut_list_verified" and row_count >= 2


def _simulation_ok(item: dict[str, object]) -> bool:
    """Return whether compact evidence proves a controlled static simulation study."""

    return _is_simulation_workflow(item) and item.get("simulation_status") == "simulation_verified"


def _simulation_report_ok(item: dict[str, object]) -> bool:
    """Return whether compact evidence proves a simulation report CSV."""

    try:
        row_count = int(item.get("simulation_report_row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    return _is_simulation_workflow(item) and row_count >= 3


def _count_sheet_metal_scenarios(evidence_summary: dict[str, object]) -> int:
    """Return the current number of sheet-metal scenarios represented in a release summary."""

    accepted = evidence_summary.get("accepted_scenarios")
    if not isinstance(accepted, list):
        return 0
    return sum(1 for scenario in accepted if str(scenario) == "sheet_metal_base_flange_baseline")


def _count_weldment_scenarios(evidence_summary: dict[str, object]) -> int:
    """Return the current number of weldment scenarios represented in a release summary."""

    accepted = evidence_summary.get("accepted_scenarios")
    if not isinstance(accepted, list):
        return 0
    return sum(1 for scenario in accepted if str(scenario) == "weldment_frame_baseline")


def _count_simulation_scenarios(evidence_summary: dict[str, object]) -> int:
    """Return the current number of simulation scenarios represented in a release summary."""

    accepted = evidence_summary.get("accepted_scenarios")
    if not isinstance(accepted, list):
        return 0
    return sum(1 for scenario in accepted if str(scenario) == "simulation_cantilever_baseline")


def _trusted_dimension_or_assembly_ok(item: dict[str, object]) -> bool:
    """Return whether a scenario has trusted dimensions or intentionally assembly-only drawing evidence."""

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


def _has_required_outputs(item: dict[str, object]) -> bool:
    """Return whether a compact scenario result lists the required output formats."""

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


def _has_required_previews(item: dict[str, object]) -> bool:
    """Return whether a compact scenario result lists the required preview views."""

    previews = item.get("preview_files")
    if not isinstance(previews, list):
        return False
    return {"front", "top", "right", "isometric"}.issubset({str(value).lower() for value in previews})


if __name__ == "__main__":
    raise SystemExit(main())
