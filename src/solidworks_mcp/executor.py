"""Execution orchestration for validated SolidWorks model plans."""

from __future__ import annotations

from pathlib import Path
import re
import struct
from time import perf_counter
from typing import Any
import zlib

from solidworks_mcp.adapters.base import CADAdapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.debug import (
    DebugRunContext,
    EventRecorder,
    classify_failure,
    create_debug_run_context,
    repro_command_for_plan,
    write_artifacts_index,
    write_delivery_manifest,
    write_environment_snapshot,
)
from solidworks_mcp.feature_graph import FeatureGraph, atomic_dimension_ids_from_metadata
from solidworks_mcp.repair import build_repair_actions
from solidworks_mcp.schemas import (
    bom_assembly_parameters_from_plan,
    bracket_basic_dimension_ids_from_plan,
    center_hole_flange_basic_dimension_ids_from_plan,
    center_hole_flange_parameters_from_plan,
    center_hole_plate_basic_dimension_ids_from_plan,
    end_cap_basic_dimension_ids_from_plan,
    ExecutionReport,
    ModelPlan,
    PlanValidationError,
    StepResult,
    mounting_block_basic_dimension_ids_from_plan,
    mounting_plate_basic_dimension_ids_from_plan,
    mounting_plate_parameters_from_plan,
    path_to_string,
    shaft_basic_dimension_ids_from_plan,
    shaft_parameters_from_plan,
    sheet_metal_base_flange_basic_dimension_ids_from_plan,
    sheet_metal_base_flange_parameters_from_plan,
    sleeve_basic_dimension_ids_from_plan,
    slotted_array_plate_basic_dimension_ids_from_plan,
    static_simulation_basic_dimension_ids_from_plan,
    static_simulation_parameters_from_plan,
    washer_basic_dimension_ids_from_plan,
    weldment_frame_basic_dimension_ids_from_plan,
    weldment_frame_parameters_from_plan,
    write_json_file,
)


TRUSTED_THREAD_STATUSES = {"holewizard_threaded_hole", "macro_threaded_hole"}
REQUIRED_OUTPUT_KEYS = {"sldprt", "step", "stl", "slddrw", "pdf", "dwg"}
REQUIRED_SHEET_METAL_OUTPUT_KEYS = {"sldprt", "step", "stl", "slddrw", "pdf", "dwg", "dxf"}
REQUIRED_WELDMENT_OUTPUT_KEYS = {"sldprt", "step", "stl", "slddrw", "pdf", "dwg", "csv"}
REQUIRED_SIMULATION_OUTPUT_KEYS = {"sldprt", "step", "stl", "slddrw", "pdf", "dwg", "csv"}
REQUIRED_PREVIEW_KEYS = {"front", "top", "right", "isometric"}
REQUIRED_DRAWING_VIEW_ROLES = {"front", "top", "right", "isometric"}
OPTIONAL_CAD_CONTENT_KEYS = {"dxf", "iges", "x_t", "x_b"}
TRUSTED_BRACKET_OPERATIONS = {
    "create_bracket",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_MOUNTING_PLATE_OPERATIONS = {
    "create_mounting_plate",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_CENTER_HOLE_FLANGE_OPERATIONS = {
    "create_center_hole_flange",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_CENTER_HOLE_PLATE_OPERATIONS = {
    "create_center_hole_plate",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_END_CAP_OPERATIONS = {
    "create_end_cap",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_MOUNTING_BLOCK_OPERATIONS = {
    "create_mounting_block",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_SHAFT_OPERATIONS = {
    "create_shaft",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_SHEET_METAL_BASE_FLANGE_OPERATIONS = {
    "create_sheet_metal_base_flange",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_WELDMENT_FRAME_OPERATIONS = {
    "create_weldment_frame",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_STATIC_SIMULATION_OPERATIONS = {
    "run_static_simulation",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_WASHER_OPERATIONS = {
    "create_washer",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_SLEEVE_OPERATIONS = {
    "create_sleeve",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_SLOTTED_ARRAY_PLATE_OPERATIONS = {
    "create_slotted_array_plate",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_ATOMIC_OPERATIONS = {
    "create_plane",
    "create_sketch",
    "extrude",
    "cut",
    "hole",
    "fillet",
    "chamfer",
    "linear_pattern",
    "circular_pattern",
    "revolve",
    "sweep",
    "loft",
    "assign_material",
    "set_custom_properties",
    "make_drawing",
}
TRUSTED_BOM_ASSEMBLY_OPERATIONS = {
    "create_bom_assembly",
    "set_custom_properties",
    "make_drawing",
}


class ModelPlanExecutor:
    """Validate, execute and inspect model plans through a CAD adapter."""

    def __init__(self, adapter: CADAdapter, config: SolidWorksMCPConfig | None = None) -> None:
        self._adapter = adapter
        self._config = config or SolidWorksMCPConfig.from_env()

    @property
    def adapter_name(self) -> str:
        """Return the active adapter name for tool responses."""

        return self._adapter.name

    def connect(self) -> dict[str, Any]:
        """Connect to the configured CAD backend."""

        return self._adapter.connect()

    def validate_plan(self, raw_plan: dict[str, Any]) -> ExecutionReport:
        """Validate a plan without touching SolidWorks state."""

        try:
            plan = ModelPlan.from_dict(raw_plan)
        except PlanValidationError as exc:
            return ExecutionReport(
                ok=False,
                adapter=self.adapter_name,
                message=str(exc),
                failure_class="schema",
                diagnostics={"schema_status": "invalid", "failure": str(exc)},
            )

        workflow_check = _trusted_workflow_policy_check(plan, self._config.enforce_trusted_workflow)
        production_readiness_status = _production_readiness_status_from_workflow_check(workflow_check)

        return ExecutionReport(
            ok=True,
            adapter=self.adapter_name,
            message="Plan schema is valid. Run preflight_environment before confirmed execution.",
            plan_name=plan.name,
            feature_summary=[operation.to_dict() for operation in plan.operations],
            failure_class=None,
            diagnostics={
                "schema_status": "valid",
                "production_readiness_status": production_readiness_status,
                "trusted_workflow_policy_check": workflow_check,
            },
        )

    def preflight_environment(self, raw_plan: dict[str, Any] | None = None) -> ExecutionReport:
        """Check adapter prerequisites without starting a modeling transaction."""

        plan: ModelPlan | None = None
        if raw_plan is not None:
            try:
                plan = ModelPlan.from_dict(raw_plan)
            except PlanValidationError as exc:
                return ExecutionReport(
                    ok=False,
                    adapter=self.adapter_name,
                    message=str(exc),
                    failure_class="schema",
                    diagnostics={"failure": str(exc)},
                )

        preflight = self._preflight_for_plan(plan)
        return ExecutionReport(
            ok=bool(preflight.get("ok")),
            adapter=self.adapter_name,
            message="Preflight passed." if preflight.get("ok") else "Preflight failed.",
            plan_name=plan.name if plan else None,
            failure_class=None if preflight.get("ok") else "preflight",
            diagnostics={"preflight_status": preflight.get("status"), "preflight_result": preflight},
        )

    def execute_plan(self, raw_plan: dict[str, Any], confirmed: bool = False) -> ExecutionReport:
        """Execute a plan only after explicit user/client confirmation."""

        if not confirmed:
            return ExecutionReport(
                ok=False,
                adapter=self.adapter_name,
                message="Execution requires confirmed=true after the user reviews the plan.",
                failure_class="schema",
                diagnostics={"failure": "missing_confirmation"},
            )

        try:
            plan = ModelPlan.from_dict(raw_plan)
        except PlanValidationError as exc:
            return ExecutionReport(
                ok=False,
                adapter=self.adapter_name,
                message=str(exc),
                failure_class="schema",
                diagnostics={"failure": str(exc)},
            )

        context = create_debug_run_context(self._config, plan)
        recorder = EventRecorder(context)
        self._adapter.set_run_workspace(context.run_dir)
        self._adapter.set_debug_recorder(recorder)
        write_json_file(context.plan_file, plan.to_dict())
        write_environment_snapshot(context, self._config, self.adapter_name)
        recorder.event(
            "plan.execution",
            "started",
            {
                "plan_name": plan.name,
                "operation_count": len(plan.operations),
                "debug_level": context.debug_level,
            },
        )

        step_results: list[StepResult] = []
        output_files: dict[str, str] = {}
        preview_files: dict[str, str] = {}
        active_document: str | None = None
        transaction_started = False
        document_state_snapshots: dict[str, dict[str, Any]] = {}

        def capture_document_state(phase: str) -> dict[str, Any]:
            """Capture a non-blocking document-state snapshot for cleanup auditing."""

            started_at = perf_counter()
            try:
                snapshot = self._adapter.document_state_snapshot(phase)
            except Exception as exc:
                snapshot = {
                    "status": "failed",
                    "adapter": self.adapter_name,
                    "phase": phase,
                    "failure_reason": str(exc),
                    "run_created_open_count": None,
                }
            document_state_snapshots[phase] = snapshot
            recorder.event(
                "adapter.document_state",
                "failed" if snapshot.get("status") == "failed" else "completed",
                snapshot,
                started_at=started_at,
            )
            return snapshot

        def finalize_execution(report: ExecutionReport) -> ExecutionReport:
            """Cleanup run-created CAD documents before writing the final report."""

            if transaction_started:
                report.diagnostics["document_state_before_cleanup"] = capture_document_state("before_cleanup")
                cleanup_started_at = perf_counter()
                try:
                    cleanup_result = self._adapter.cleanup_after_run(plan)
                    report.diagnostics["cleanup_result"] = cleanup_result
                    recorder.event(
                        "adapter.cleanup",
                        "completed"
                        if cleanup_result.get("status") in {"completed", "skipped_no_documents"}
                        else "failed",
                        cleanup_result,
                        started_at=cleanup_started_at,
                    )
                except Exception as cleanup_exc:
                    cleanup_result = {
                        "status": "failed",
                        "enabled": self._config.close_documents_after_run,
                        "failure_reason": str(cleanup_exc),
                    }
                    report.diagnostics["cleanup_result"] = cleanup_result
                    recorder.event("adapter.cleanup", "failed", cleanup_result, started_at=cleanup_started_at)
                report.diagnostics["document_state_after_cleanup"] = capture_document_state("after_cleanup")
                report.diagnostics["document_state_audit_result"] = _document_state_audit_result(
                    document_state_snapshots
                )
                report.diagnostics["require_direct_hole_callout"] = self._config.require_direct_hole_callout
                report.diagnostics["production_acceptance_result"] = _build_production_acceptance_result(
                    plan,
                    report.ok,
                    report.diagnostics,
                    report.output_files,
                    report.preview_files,
                )
            return _finalize_report(report, context, recorder)

        try:
            preflight_started_at = perf_counter()
            preflight = self._preflight_for_plan(plan)
            recorder.event(
                "environment.preflight",
                "completed" if preflight.get("ok") else "failed",
                preflight,
                started_at=preflight_started_at,
            )
            if not preflight.get("ok"):
                report = ExecutionReport(
                    ok=False,
                    adapter=self.adapter_name,
                    message="Preflight failed; execution was not started.",
                    plan=plan.to_dict(),
                    plan_name=plan.name,
                    run_id=context.run_id,
                    run_dir=path_to_string(context.run_dir),
                    events_file=path_to_string(context.events_file),
                    environment_file=path_to_string(context.environment_file),
                    artifacts_file=path_to_string(context.artifacts_file),
                    failure_class="preflight",
                    repro_command=repro_command_for_plan(context.plan_file),
                    diagnostics={"preflight_status": preflight.get("status"), "preflight_result": preflight},
                )
                return _finalize_report(report, context, recorder)

            transaction_started_at = perf_counter()
            capture_document_state("before_transaction")
            transaction_started = True
            transaction = self._adapter.begin_transaction(plan)
            active_document = transaction.get("document")
            recorder.event("adapter.transaction", "completed", transaction, started_at=transaction_started_at)
            capture_document_state("after_transaction")

            for index, operation in enumerate(plan.operations):
                step_started_at = perf_counter()
                recorder.event(
                    "plan.step",
                    "started",
                    {"index": index, "id": operation.id, "op": operation.op},
                )
                result = self._adapter.execute_operation(operation, index, plan)
                step_results.append(result)
                recorder.event(
                    "plan.step",
                    "completed" if result.ok else "failed",
                    result.to_dict(),
                    started_at=step_started_at,
                )
                if not result.ok:
                    report = self._failure_report(
                        plan,
                        context,
                        step_results,
                        result.message,
                        index,
                        active_document,
                    )
                    return finalize_execution(report)

            if plan.drawing_profile.enabled:
                drawing_started_at = perf_counter()
                recorder.event("drawing.generate", "started", plan.drawing_profile.to_dict())
                output_files.update(self._adapter.generate_drawing(plan, plan.drawing_profile))
                recorder.event("drawing.generate", "completed", output_files, started_at=drawing_started_at)

            export_formats = _combined_export_formats(plan)
            export_started_at = perf_counter()
            recorder.event("outputs.export", "started", {"formats": list(export_formats)})
            output_files.update(self._adapter.export_outputs(plan, export_formats))
            recorder.event("outputs.export", "completed", output_files, started_at=export_started_at)

            preview_started_at = perf_counter()
            recorder.event("previews.capture", "started", {})
            preview_files.update(self._adapter.capture_previews(plan))
            recorder.event("previews.capture", "completed", preview_files, started_at=preview_started_at)

            inspection = self._adapter.inspect_active_model()
            diagnostics = _diagnostics_from_inspection(inspection)
            diagnostics["document_state_before_transaction"] = document_state_snapshots.get("before_transaction")
            diagnostics["document_state_after_transaction"] = document_state_snapshots.get("after_transaction")
            diagnostics["preflight_status"] = preflight.get("status")
            diagnostics["preflight_result"] = preflight
            diagnostics["artifact_validation_result"] = _validate_generated_artifacts(output_files, preview_files)
            diagnostics["artifact_content_result"] = _validate_artifact_content(
                plan,
                output_files,
                preview_files,
                force_cad_content_failure=self._config.force_cad_content_failure,
            )
            report = ExecutionReport(
                ok=True,
                adapter=self.adapter_name,
                message="Plan executed. Review generated previews and outputs before using files for engineering decisions.",
                plan=plan.to_dict(),
                plan_name=plan.name,
                run_id=context.run_id,
                run_dir=path_to_string(context.run_dir),
                events_file=path_to_string(context.events_file),
                environment_file=path_to_string(context.environment_file),
                artifacts_file=path_to_string(context.artifacts_file),
                step_results=tuple(step_results),
                output_files=output_files,
                preview_files=preview_files,
                feature_summary=inspection.get("features", []),
                active_document=inspection.get("active_document") or active_document,
                failure_class=None,
                repro_command=repro_command_for_plan(context.plan_file),
                diagnostics=diagnostics,
            )
            return finalize_execution(report)
        except Exception as exc:
            recorder.event("plan.execution", "failed", {"error": str(exc)})
            report = self._failure_report(
                plan,
                context,
                step_results,
                str(exc),
                len(step_results),
                active_document,
            )
            return finalize_execution(report)

    def generate_drawing(self, raw_plan: dict[str, Any]) -> ExecutionReport:
        """Generate a drawing for the adapter's active model using plan settings."""

        try:
            plan = ModelPlan.from_dict(raw_plan)
            output_files = self._adapter.generate_drawing(plan, plan.drawing_profile)
        except Exception as exc:
            return ExecutionReport(
                ok=False,
                adapter=self.adapter_name,
                message=str(exc),
                failure_class=classify_failure(str(exc)),
            )

        return ExecutionReport(
            ok=True,
            adapter=self.adapter_name,
            message="Drawing generated for the active model.",
            plan_name=plan.name,
            output_files=output_files,
        )

    def export_outputs(self, raw_plan: dict[str, Any], formats: list[str] | None = None) -> ExecutionReport:
        """Export the adapter's active model to requested formats."""

        try:
            plan = ModelPlan.from_dict(raw_plan)
            requested_formats = tuple(formats) if formats else plan.output_formats
            output_files = self._adapter.export_outputs(plan, requested_formats)
        except Exception as exc:
            return ExecutionReport(
                ok=False,
                adapter=self.adapter_name,
                message=str(exc),
                failure_class=classify_failure(str(exc)),
            )

        return ExecutionReport(
            ok=True,
            adapter=self.adapter_name,
            message="Outputs exported for the active model.",
            plan_name=plan.name,
            output_files=output_files,
        )

    def inspect_active_model(self) -> ExecutionReport:
        """Inspect the active model for AI self-review."""

        try:
            inspection = self._adapter.inspect_active_model()
        except Exception as exc:
            return ExecutionReport(
                ok=False,
                adapter=self.adapter_name,
                message=str(exc),
                failure_class=classify_failure(str(exc)),
            )

        return ExecutionReport(
            ok=True,
            adapter=self.adapter_name,
            message="Active model inspected.",
            plan_name=inspection.get("active_document"),
            feature_summary=inspection.get("features", []),
            active_document=inspection.get("active_document"),
            diagnostics=_diagnostics_from_inspection(inspection),
        )

    def cleanup_run_documents(self, run_dir: str) -> dict[str, Any]:
        """Close open CAD documents that belong to a completed run directory."""

        return self._adapter.cleanup_run_documents(run_dir)

    def _preflight_for_plan(self, plan: ModelPlan | None) -> dict[str, Any]:
        """Run production policy checks before adapter preflight can create documents."""

        workflow_check = _trusted_workflow_policy_check(plan, self._config.enforce_trusted_workflow)
        if workflow_check is not None and not workflow_check.get("ok"):
            return {
                "ok": False,
                "status": "failed",
                "adapter": self.adapter_name,
                "plan_name": plan.name if plan else None,
                "checks": [workflow_check],
                "failures": [workflow_check["id"]],
            }

        preflight = dict(self._adapter.preflight_environment(plan))
        if workflow_check is not None:
            checks = list(preflight.get("checks") or [])
            checks.append(workflow_check)
            failures = [str(check.get("id")) for check in checks if not check.get("ok")]
            preflight["checks"] = checks
            preflight["failures"] = failures
            preflight["ok"] = not failures
            preflight["status"] = "failed" if failures else "ready"
        return preflight

    def _failure_report(
        self,
        plan: ModelPlan,
        context: DebugRunContext,
        step_results: list[StepResult],
        message: str,
        error_step: int,
        active_document: str | None,
    ) -> ExecutionReport:
        """Build a consistent failure report with enough context for repair."""

        failure_class = classify_failure(message, step_results)
        return ExecutionReport(
            ok=False,
            adapter=self.adapter_name,
            message=message,
            plan=plan.to_dict(),
            plan_name=plan.name,
            run_id=context.run_id,
            run_dir=path_to_string(context.run_dir),
            events_file=path_to_string(context.events_file),
            environment_file=path_to_string(context.environment_file),
            artifacts_file=path_to_string(context.artifacts_file),
            step_results=tuple(step_results),
            active_document=active_document,
            error_step=error_step,
            failure_class=failure_class,
            repro_command=repro_command_for_plan(context.plan_file),
            diagnostics={"failure": message, "failure_class": failure_class},
        )


def _diagnostics_from_inspection(inspection: dict[str, Any]) -> dict[str, Any]:
    """Extract report diagnostics from adapter inspection data."""

    return {
        "preflight_status": inspection.get("preflight_status"),
        "preflight_result": inspection.get("preflight_result"),
        "thread_model_status": inspection.get("thread_model_status"),
        "corner_radius_status": inspection.get("corner_radius_status"),
        "drawing_view_status": inspection.get("drawing_view_status"),
        "drawing_view_result": inspection.get("drawing_view_result"),
        "drawing_annotation_status": inspection.get("drawing_annotation_status"),
        "drawing_annotation_result": inspection.get("drawing_annotation_result"),
        "drawing_dimension_status": inspection.get("drawing_dimension_status"),
        "drawing_dimension_result": inspection.get("drawing_dimension_result"),
        "drawing_metadata_note_result": inspection.get("drawing_metadata_note_result"),
        "material_status": inspection.get("material_status"),
        "material_result": inspection.get("material_result"),
        "custom_property_status": inspection.get("custom_property_status"),
        "custom_property_result": inspection.get("custom_property_result"),
        "model_geometry_status": inspection.get("model_geometry_status"),
        "model_geometry_result": inspection.get("model_geometry_result"),
        "mass_property_status": inspection.get("mass_property_status"),
        "mass_property_result": inspection.get("mass_property_result"),
        "export_result": inspection.get("export_result"),
        "assembly_result": inspection.get("assembly_result"),
        "bom_result": inspection.get("bom_result"),
        "sheet_metal_status": inspection.get("sheet_metal_status"),
        "sheet_metal_result": inspection.get("sheet_metal_result"),
        "weldment_status": inspection.get("weldment_status"),
        "weldment_result": inspection.get("weldment_result"),
        "cut_list_status": inspection.get("cut_list_status"),
        "cut_list_result": inspection.get("cut_list_result"),
        "simulation_status": inspection.get("simulation_status"),
        "simulation_result": inspection.get("simulation_result"),
        "artifact_validation_result": inspection.get("artifact_validation_result"),
        "artifact_content_result": inspection.get("artifact_content_result"),
        "hole_result": inspection.get("hole_result"),
        "fallbacks": inspection.get("fallbacks", []),
        "warnings": inspection.get("warnings", []),
    }


def _document_state_audit_result(snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Summarize document-state snapshots into one cleanup audit status."""

    after_cleanup = snapshots.get("after_cleanup") or {}
    before_cleanup = snapshots.get("before_cleanup") or {}
    after_count = after_cleanup.get("run_created_open_count")
    if after_count == 0:
        status = "verified_no_run_documents_open"
        ok = True
        failure_reason = None
    elif isinstance(after_count, int) and after_count > 0:
        status = "run_documents_still_open"
        ok = False
        failure_reason = "One or more run-created SolidWorks documents are still open after cleanup."
    else:
        status = "not_verified"
        ok = False
        failure_reason = "Document-state audit did not verify the post-cleanup open document count."
    return {
        "status": status,
        "ok": ok,
        "failure_reason": failure_reason,
        "before_cleanup_run_created_open_count": before_cleanup.get("run_created_open_count"),
        "after_cleanup_run_created_open_count": after_count,
        "after_cleanup_snapshot_status": after_cleanup.get("status"),
        "phases": sorted(snapshots),
    }


def _combined_export_formats(plan: ModelPlan) -> tuple[str, ...]:
    """Merge model and drawing export formats while preserving request order."""

    formats: list[str] = []
    for file_format in plan.output_formats:
        if file_format not in formats:
            formats.append(file_format)
    if plan.drawing_profile.enabled:
        for file_format in plan.drawing_profile.export_formats:
            if file_format not in formats:
                formats.append(file_format)
    return tuple(formats)


def _trusted_workflow_policy_check(plan: ModelPlan | None, enforced: bool) -> dict[str, Any] | None:
    """Return a preflight check for the current trusted-workflow policy."""

    if plan is None:
        return None
    workflow = _trusted_workflow_result(plan)
    if not enforced:
        return {
            "id": "trusted_workflow_policy",
            "ok": True,
            "message": "Trusted workflow enforcement is disabled for this run.",
            "enforced": False,
            "trusted_workflow": workflow,
            "remediation": "Set SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW=1 for production execution.",
        }
    return {
        "id": "trusted_workflow_policy",
        "ok": workflow["ok"],
        "message": "Plan matches a controlled production workflow."
        if workflow["ok"]
        else "Plan is outside the controlled production workflows.",
        "enforced": True,
        "trusted_workflow": workflow,
        "remediation": None
        if workflow["ok"]
        else (
            "Use create_bracket, create_mounting_plate, create_center_hole_flange, create_center_hole_plate, create_end_cap, create_mounting_block, create_shaft, create_sheet_metal_base_flange, create_weldment_frame, run_static_simulation, create_washer, create_sleeve, or create_slotted_array_plate "
            "for controlled production, or set SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW=0 "
            "only for non-production experiments."
        ),
    }


def _production_readiness_status_from_workflow_check(workflow_check: dict[str, Any] | None) -> str:
    """Return a compact client-facing production-readiness status for validation."""

    if workflow_check is None:
        return "not_evaluated"
    if workflow_check.get("enforced") is False:
        return "trusted_workflow_enforcement_disabled"
    return "trusted_workflow_ready" if workflow_check.get("ok") else "blocked_by_trusted_workflow_policy"


def _validate_generated_artifacts(output_files: dict[str, str], preview_files: dict[str, str]) -> dict[str, Any]:
    """Validate that generated artifact paths exist and are non-empty files."""

    checks: list[dict[str, Any]] = []
    for group_name, files in (("output_files", output_files), ("preview_files", preview_files)):
        for artifact_id, raw_path in files.items():
            path = Path(raw_path)
            exists = path.exists()
            is_file = path.is_file() if exists else False
            size_bytes = path.stat().st_size if is_file else 0
            checks.append(
                {
                    "group": group_name,
                    "id": artifact_id,
                    "path": path_to_string(path),
                    "exists": exists,
                    "is_file": is_file,
                    "size_bytes": size_bytes,
                    "ok": exists and is_file and size_bytes > 0,
                }
            )

    missing_or_empty = [
        {"group": check["group"], "id": check["id"], "path": check["path"], "size_bytes": check["size_bytes"]}
        for check in checks
        if not check["ok"]
    ]
    return {
        "status": "artifacts_ready" if not missing_or_empty else "missing_or_empty_artifacts",
        "ok": not missing_or_empty,
        "checked_count": len(checks),
        "missing_or_empty": missing_or_empty,
        "checks": checks,
    }


def _validate_artifact_content(
    plan: ModelPlan,
    output_files: dict[str, str],
    preview_files: dict[str, str],
    *,
    force_cad_content_failure: bool = False,
) -> dict[str, Any]:
    """Validate basic readability and nonblank content for CAD, drawing and preview artifacts."""

    cad_content_result = _validate_cad_artifact_content(
        output_files,
        forced_failure=force_cad_content_failure,
    )
    pdf_checks = []
    if "pdf" in output_files:
        pdf_checks.append(_inspect_pdf(Path(output_files["pdf"])))
    pdf_semantic_result = _validate_pdf_semantic_content(plan, pdf_checks)

    preview_checks = [
        _inspect_preview_artifact(preview_id, Path(path))
        for preview_id, path in preview_files.items()
    ]
    failed = [
        {"group": "pdf", "id": check["id"], "status": check["status"], "path": check["path"]}
        for check in pdf_checks
        if not check.get("ok")
    ]
    if not pdf_semantic_result.get("ok"):
        failed.append(
            {
                "group": "pdf",
                "id": "semantic_content",
                "status": pdf_semantic_result.get("status"),
                "path": pdf_semantic_result.get("path"),
            }
        )
    if not cad_content_result.get("ok"):
        failed.append(
            {
                "group": "cad",
                "id": "cad_artifact_content",
                "status": cad_content_result.get("status"),
                "path": None,
            }
        )
    failed.extend(
        {"group": "preview", "id": check["id"], "status": check["status"], "path": check["path"]}
        for check in preview_checks
        if not check.get("ok")
    )
    mock_placeholders = [
        check for check in preview_checks if check.get("status") == "mock_placeholder"
    ]
    mock_pdf_placeholders = [
        check for check in pdf_checks if check.get("status") == "mock_placeholder"
    ]
    if failed:
        status = "content_failed"
    elif (
        mock_placeholders
        and len(mock_placeholders) == len(preview_checks)
        and mock_pdf_placeholders
        and len(mock_pdf_placeholders) == len(pdf_checks)
    ):
        failed = []
        status = "mock_output_placeholders"
    elif mock_placeholders and len(mock_placeholders) == len(preview_checks):
        status = "mock_preview_placeholders"
    else:
        status = "content_ready"
    return {
        "status": status,
        "ok": not failed,
        "cad_content_result": cad_content_result,
        "pdf_checks": pdf_checks,
        "pdf_semantic_content_result": pdf_semantic_result,
        "preview_checks": preview_checks,
        "failed": failed,
    }


def _build_production_acceptance_result(
    plan: ModelPlan,
    execution_ok: bool,
    diagnostics: dict[str, Any],
    output_files: dict[str, str],
    preview_files: dict[str, str],
) -> dict[str, Any]:
    """Build the single trusted production acceptance verdict for the MVP workflow."""

    annotation_result = _as_dict(diagnostics.get("drawing_annotation_result"))
    drawing_view_result = _as_dict(diagnostics.get("drawing_view_result"))
    dimension_result = _as_dict(diagnostics.get("drawing_dimension_result"))
    artifact_result = _as_dict(diagnostics.get("artifact_validation_result"))
    content_result = _as_dict(diagnostics.get("artifact_content_result"))
    cad_content_result = _as_dict(content_result.get("cad_content_result"))
    pdf_semantic_result = _as_dict(content_result.get("pdf_semantic_content_result"))
    cleanup_result = _as_dict(diagnostics.get("cleanup_result"))
    hole_result = _as_dict(diagnostics.get("hole_result"))
    material_result = _as_dict(diagnostics.get("material_result"))
    custom_property_result = _as_dict(diagnostics.get("custom_property_result"))
    geometry_result = _as_dict(diagnostics.get("model_geometry_result"))
    mass_property_result = _as_dict(diagnostics.get("mass_property_result"))
    export_result = _as_dict(diagnostics.get("export_result"))
    document_state_audit = _as_dict(diagnostics.get("document_state_audit_result"))
    trusted_workflow = _trusted_workflow_result(plan)
    if trusted_workflow.get("workflow") == "bom_assembly":
        return _build_assembly_production_acceptance_result(
            plan,
            execution_ok,
            diagnostics,
            output_files,
            preview_files,
            trusted_workflow,
        )
    if trusted_workflow.get("workflow") == "sheet_metal_base_flange":
        return _build_sheet_metal_production_acceptance_result(
            plan,
            execution_ok,
            diagnostics,
            output_files,
            preview_files,
            trusted_workflow,
        )
    if trusted_workflow.get("workflow") == "weldment_frame":
        return _build_weldment_production_acceptance_result(
            plan,
            execution_ok,
            diagnostics,
            output_files,
            preview_files,
            trusted_workflow,
        )
    if trusted_workflow.get("workflow") == "static_simulation":
        return _build_static_simulation_production_acceptance_result(
            plan,
            execution_ok,
            diagnostics,
            output_files,
            preview_files,
            trusted_workflow,
        )
    if trusted_workflow.get("workflow") == "atomic_model":
        return _build_atomic_production_acceptance_result(
            plan,
            execution_ok,
            diagnostics,
            output_files,
            preview_files,
            trusted_workflow,
        )

    required_basic_dimensions = set(_trusted_basic_dimension_ids_from_plan(plan))
    required_material = _required_material_from_plan(plan)
    required_custom_properties = _required_custom_properties_from_plan(plan)
    requires_controlled_geometry = _requires_controlled_model_geometry(plan)
    is_mounting_plate_workflow = trusted_workflow.get("workflow") == "mounting_plate"
    hole_callout_required = _requires_hole_callout(plan)

    created_dimensions = {
        str(item.get("id"))
        for item in dimension_result.get("created_dimensions", [])
        if isinstance(item, dict) and item.get("id")
    }
    dimension_methods = {
        str(item.get("id")): str(item.get("method"))
        for item in dimension_result.get("created_dimensions", [])
        if isinstance(item, dict) and item.get("id")
    }
    radius_dimensions = sorted(
        dimension_id
        for dimension_id in required_basic_dimensions
        if dimension_id.startswith("corner_radius_")
    )
    non_radial_radius_dimensions = sorted(
        dimension_id
        for dimension_id in radius_dimensions
        if dimension_methods.get(dimension_id) != "AddRadialDimension2"
    )
    proxy_dimensions = sorted(
        str(item.get("id"))
        for item in dimension_result.get("created_dimensions", [])
        if isinstance(item, dict) and item.get("id") and item.get("proxy_dimension") is True
    )
    missing_dimensions = sorted(required_basic_dimensions - created_dimensions)
    drawing_view_roles = _drawing_view_roles(drawing_view_result)
    missing_drawing_view_roles = sorted(REQUIRED_DRAWING_VIEW_ROLES - drawing_view_roles)
    reported_missing = [
        str(item)
        for item in dimension_result.get("missing_dimensions", [])
    ]
    output_keys = set(output_files)
    preview_keys = set(preview_files)
    requested_output_keys = set(_combined_export_formats(plan))
    missing_requested_output_keys = sorted(requested_output_keys - output_keys)
    failed_exports = [
        item for item in export_result.get("failed", [])
        if isinstance(item, dict)
    ]
    require_direct_hole_callout = bool(diagnostics.get("require_direct_hole_callout"))
    checks = {
        "execution_ok": bool(execution_ok),
        "trusted_controlled_workflow": trusted_workflow["ok"],
        "preflight_ready": diagnostics.get("preflight_status") == "ready",
        "trusted_thread_model": not is_mounting_plate_workflow
        or (
            diagnostics.get("thread_model_status") in TRUSTED_THREAD_STATUSES
            and hole_result.get("hole_count") == 4
        ),
        "corner_radius_feature": not is_mounting_plate_workflow
        or diagnostics.get("corner_radius_status") == "fillet_feature",
        "drawing_standard_views_created": diagnostics.get("drawing_view_status") == "created"
        and drawing_view_result.get("status") == "created"
        and not missing_drawing_view_roles,
        "hole_callouts_created": not hole_callout_required
        or (
            diagnostics.get("drawing_annotation_status") == "hole_callout_created"
            and int(annotation_result.get("created_callout_count") or 0) >= 1
        ),
        "direct_hole_callouts_created": not hole_callout_required
        or not require_direct_hole_callout
        or annotation_result.get("direct_hole_callout_created") is True,
        "basic_dimensions_created": diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
        and bool(required_basic_dimensions)
        and int(dimension_result.get("created_dimension_count") or 0) >= len(required_basic_dimensions)
        and not missing_dimensions
        and not reported_missing,
        "trusted_basic_dimensions": diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
        and not proxy_dimensions
        and not non_radial_radius_dimensions
        and dimension_result.get("dimension_layout_status") == "trusted_dimensions_created",
        "material_verified": required_material is None
        or (
            diagnostics.get("material_status") == "material_verified"
            and _material_result_matches(material_result, required_material)
        ),
        "custom_properties_verified": not required_custom_properties
        or (
            diagnostics.get("custom_property_status") == "custom_properties_verified"
            and _custom_property_result_matches(custom_property_result, required_custom_properties)
        ),
        "model_geometry_verified": not requires_controlled_geometry
        or (
            diagnostics.get("model_geometry_status") == "geometry_verified"
            and geometry_result.get("status") == "geometry_verified"
            and int(geometry_result.get("body_count") or 0) >= 1
        ),
        "mass_properties_verified": not requires_controlled_geometry
        or (
            diagnostics.get("mass_property_status") == "mass_properties_verified"
            and _positive_number(mass_property_result.get("mass_kg"))
            and _positive_number(mass_property_result.get("volume_m3"))
        ),
        "artifacts_ready": artifact_result.get("ok") is True
        and artifact_result.get("status") == "artifacts_ready",
        "artifact_content_ready": content_result.get("ok") is True
        and content_result.get("status") in {"content_ready", "mock_preview_placeholders", "mock_output_placeholders"},
        "cad_artifact_content": cad_content_result.get("ok") is True
        and cad_content_result.get("status") in {"cad_artifacts_verified", "mock_placeholder"},
        "drawing_pdf_semantic_content": pdf_semantic_result.get("ok") is True
        and pdf_semantic_result.get("status") in {"pdf_semantic_content_verified", "mock_placeholder"},
        "required_output_files": REQUIRED_OUTPUT_KEYS.issubset(output_keys),
        "requested_output_files": not missing_requested_output_keys and not failed_exports,
        "required_preview_files": REQUIRED_PREVIEW_KEYS.issubset(preview_keys),
        "cleanup_completed": cleanup_result.get("enabled") is True
        and cleanup_result.get("status") in {"completed", "skipped_no_documents"},
        "cleanup_verified": cleanup_result.get("status") == "skipped_no_documents"
        or cleanup_result.get("cleanup_verification_status") == "verified",
        "document_state_audit_verified": document_state_audit.get("status")
        == "verified_no_run_documents_open"
        and document_state_audit.get("after_cleanup_run_created_open_count") == 0,
    }
    failures = [name for name, ok in checks.items() if not ok]
    summary = {
        "thread_model_status": diagnostics.get("thread_model_status"),
        "trusted_workflow_status": trusted_workflow["status"],
        "trusted_workflow": trusted_workflow,
        "hole_count": hole_result.get("hole_count"),
        "corner_radius_status": diagnostics.get("corner_radius_status"),
        "drawing_view_status": diagnostics.get("drawing_view_status"),
        "drawing_view_roles": sorted(drawing_view_roles),
        "missing_drawing_view_roles": missing_drawing_view_roles,
        "drawing_view_errors": drawing_view_result.get("errors"),
        "drawing_annotation_status": diagnostics.get("drawing_annotation_status"),
        "callout_count": annotation_result.get("created_callout_count"),
        "callout_creation_method": annotation_result.get("callout_creation_method"),
        "direct_hole_callout_created": annotation_result.get("direct_hole_callout_created"),
        "dimension_count": dimension_result.get("created_dimension_count"),
        "dimension_layout_status": dimension_result.get("dimension_layout_status"),
        "proxy_dimensions": proxy_dimensions,
        "non_radial_radius_dimensions": non_radial_radius_dimensions,
        "missing_dimensions": missing_dimensions or reported_missing,
        "material_status": diagnostics.get("material_status"),
        "requested_material": required_material,
        "effective_material": material_result.get("effective_material"),
        "current_material": material_result.get("current_material"),
        "custom_property_status": diagnostics.get("custom_property_status"),
        "requested_custom_properties": required_custom_properties,
        "current_custom_properties": custom_property_result.get("current_properties"),
        "model_geometry_status": diagnostics.get("model_geometry_status"),
        "model_geometry_max_error_mm": geometry_result.get("max_error_mm"),
        "model_geometry_measured_dimensions_mm": geometry_result.get("measured_dimensions_mm"),
        "model_geometry_expected_dimensions_mm": geometry_result.get("expected_dimensions_mm"),
        "model_geometry_body_count": geometry_result.get("body_count"),
        "mass_property_status": diagnostics.get("mass_property_status"),
        "mass_kg": mass_property_result.get("mass_kg"),
        "volume_m3": mass_property_result.get("volume_m3"),
        "surface_area_m2": mass_property_result.get("surface_area_m2"),
        "artifact_validation_status": artifact_result.get("status"),
        "artifact_content_status": content_result.get("status"),
        "cad_content_status": cad_content_result.get("status"),
        "cad_content_failed": cad_content_result.get("failed"),
        "export_status": export_result.get("status"),
        "export_failed": failed_exports,
        "missing_requested_output_files": missing_requested_output_keys,
        "pdf_semantic_content_status": pdf_semantic_result.get("status"),
        "pdf_semantic_content_matches": pdf_semantic_result.get("matches"),
        "pdf_semantic_content_missing": pdf_semantic_result.get("missing"),
        "cleanup_status": cleanup_result.get("status"),
        "cleanup_verification_status": cleanup_result.get("cleanup_verification_status"),
        "document_state_audit_status": document_state_audit.get("status"),
        "document_state_after_cleanup_run_created_open_count": document_state_audit.get(
            "after_cleanup_run_created_open_count"
        ),
        "output_files": sorted(output_keys),
        "preview_files": sorted(preview_keys),
    }
    return {
        "status": "accepted" if not failures else "rejected",
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "repair_actions": build_repair_actions(failures, summary),
        "summary": summary,
        "expected": {
            "thread_model_status": sorted(TRUSTED_THREAD_STATUSES),
            "trusted_workflow": trusted_workflow.get("status"),
            "hole_count": 4 if is_mounting_plate_workflow else "not required",
            "corner_radius_status": "fillet_feature" if is_mounting_plate_workflow else "not required",
            "drawing_view_status": "created",
            "required_drawing_view_roles": sorted(REQUIRED_DRAWING_VIEW_ROLES),
            "drawing_annotation_status": "hole_callout_created" if hole_callout_required else "not_required",
            "minimum_callout_count": 1 if hole_callout_required else 0,
            "direct_hole_callout_required": require_direct_hole_callout,
            "drawing_dimension_status": "basic_dimensions_created",
            "required_dimensions": sorted(required_basic_dimensions),
            "proxy_dimensions": [],
            "corner_radius_dimension_method": "AddRadialDimension2",
            "dimension_layout_status": "trusted_dimensions_created",
            "material_status": "material_verified" if required_material else "not required",
            "requested_material": required_material,
            "custom_property_status": "custom_properties_verified" if required_custom_properties else "not required",
            "requested_custom_properties": required_custom_properties,
            "model_geometry_status": "geometry_verified" if requires_controlled_geometry else "not required",
            "mass_property_status": "mass_properties_verified" if requires_controlled_geometry else "not required",
            "artifact_validation_status": "artifacts_ready",
            "artifact_content_status": ["content_ready", "mock_preview_placeholders", "mock_output_placeholders"],
            "cad_content_status": ["cad_artifacts_verified", "mock_placeholder"],
            "pdf_semantic_content_status": ["pdf_semantic_content_verified", "mock_placeholder"],
            "required_output_files": sorted(REQUIRED_OUTPUT_KEYS),
            "requested_output_files": sorted(requested_output_keys),
            "required_preview_files": sorted(REQUIRED_PREVIEW_KEYS),
            "cleanup_status": ["completed", "skipped_no_documents"],
            "cleanup_verification_status": "verified unless no documents were created",
        },
    }


def _build_atomic_production_acceptance_result(
    plan: ModelPlan,
    execution_ok: bool,
    diagnostics: dict[str, Any],
    output_files: dict[str, str],
    preview_files: dict[str, str],
    trusted_workflow: dict[str, Any],
) -> dict[str, Any]:
    """Build a conservative production verdict for staged atomic model sessions."""

    annotation_result = _as_dict(diagnostics.get("drawing_annotation_result"))
    drawing_view_result = _as_dict(diagnostics.get("drawing_view_result"))
    dimension_result = _as_dict(diagnostics.get("drawing_dimension_result"))
    artifact_result = _as_dict(diagnostics.get("artifact_validation_result"))
    content_result = _as_dict(diagnostics.get("artifact_content_result"))
    cad_content_result = _as_dict(content_result.get("cad_content_result"))
    pdf_semantic_result = _as_dict(content_result.get("pdf_semantic_content_result"))
    cleanup_result = _as_dict(diagnostics.get("cleanup_result"))
    material_result = _as_dict(diagnostics.get("material_result"))
    custom_property_result = _as_dict(diagnostics.get("custom_property_result"))
    export_result = _as_dict(diagnostics.get("export_result"))
    geometry_result = _as_dict(diagnostics.get("model_geometry_result"))
    mass_property_result = _as_dict(diagnostics.get("mass_property_result"))
    document_state_audit = _as_dict(diagnostics.get("document_state_audit_result"))
    required_material = _required_material_from_plan(plan)
    required_custom_properties = _required_custom_properties_from_plan(plan)
    output_keys = set(output_files)
    preview_keys = set(preview_files)
    requested_output_keys = set(_combined_export_formats(plan))
    missing_requested_output_keys = sorted(requested_output_keys - output_keys)
    failed_exports = [
        item for item in export_result.get("failed", [])
        if isinstance(item, dict)
    ]
    drawing_view_roles = _drawing_view_roles(drawing_view_result)
    missing_drawing_view_roles = sorted(REQUIRED_DRAWING_VIEW_ROLES - drawing_view_roles)
    hole_requested = any(operation.op == "hole" for operation in plan.operations)
    created_dimensions = [
        item for item in dimension_result.get("created_dimensions", [])
        if isinstance(item, dict)
    ]
    required_atomic_dimensions = set(atomic_dimension_ids_from_metadata(plan.metadata))
    created_dimension_ids = {
        str(item.get("id"))
        for item in created_dimensions
        if item.get("id")
    }
    missing_atomic_dimensions = sorted(required_atomic_dimensions - created_dimension_ids)
    proxy_dimensions = sorted(
        str(item.get("id"))
        for item in created_dimensions
        if item.get("id") and item.get("proxy_dimension") is True
    )
    require_direct_hole_callout = bool(diagnostics.get("require_direct_hole_callout"))
    checks = {
        "execution_ok": bool(execution_ok),
        "trusted_controlled_workflow": trusted_workflow["ok"],
        "preflight_ready": diagnostics.get("preflight_status") == "ready",
        "drawing_standard_views_created": diagnostics.get("drawing_view_status") == "created"
        and drawing_view_result.get("status") == "created"
        and not missing_drawing_view_roles,
        "hole_callouts_created": not hole_requested
        or (
            diagnostics.get("drawing_annotation_status") == "hole_callout_created"
            and int(annotation_result.get("created_callout_count") or 0) >= 1
        ),
        "direct_hole_callouts_created": not hole_requested
        or not require_direct_hole_callout
        or annotation_result.get("direct_hole_callout_created") is True,
        "basic_dimensions_not_proxy": not proxy_dimensions
        and dimension_result.get("dimension_layout_status") != "radius_proxy_used",
        "atomic_dimensions_created": not required_atomic_dimensions
        or (
            diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
            and int(dimension_result.get("created_dimension_count") or 0) >= len(required_atomic_dimensions)
            and not missing_atomic_dimensions
            and not dimension_result.get("missing_dimensions")
        ),
        "material_verified": required_material is None
        or (
            diagnostics.get("material_status") == "material_verified"
            and _material_result_matches(material_result, required_material)
        ),
        "custom_properties_verified": not required_custom_properties
        or (
            diagnostics.get("custom_property_status") == "custom_properties_verified"
            and _custom_property_result_matches(custom_property_result, required_custom_properties)
        ),
        "model_geometry_verified": diagnostics.get("model_geometry_status") == "geometry_verified"
        and geometry_result.get("status") == "geometry_verified"
        and int(geometry_result.get("body_count") or 0) >= 1,
        "mass_properties_verified": diagnostics.get("mass_property_status") == "mass_properties_verified"
        and _positive_number(mass_property_result.get("mass_kg"))
        and _positive_number(mass_property_result.get("volume_m3")),
        "artifacts_ready": artifact_result.get("ok") is True
        and artifact_result.get("status") == "artifacts_ready",
        "artifact_content_ready": content_result.get("ok") is True
        and content_result.get("status") in {"content_ready", "mock_preview_placeholders", "mock_output_placeholders"},
        "cad_artifact_content": cad_content_result.get("ok") is True
        and cad_content_result.get("status") in {"cad_artifacts_verified", "mock_placeholder"},
        "drawing_pdf_semantic_content": pdf_semantic_result.get("ok") is True
        and pdf_semantic_result.get("status") in {"pdf_semantic_content_verified", "mock_placeholder"},
        "required_output_files": REQUIRED_OUTPUT_KEYS.issubset(output_keys),
        "requested_output_files": not missing_requested_output_keys and not failed_exports,
        "required_preview_files": REQUIRED_PREVIEW_KEYS.issubset(preview_keys),
        "cleanup_completed": cleanup_result.get("enabled") is True
        and cleanup_result.get("status") in {"completed", "skipped_no_documents"},
        "cleanup_verified": cleanup_result.get("status") == "skipped_no_documents"
        or cleanup_result.get("cleanup_verification_status") == "verified",
        "document_state_audit_verified": document_state_audit.get("status")
        == "verified_no_run_documents_open"
        and document_state_audit.get("after_cleanup_run_created_open_count") == 0,
    }
    failures = [name for name, ok in checks.items() if not ok]
    summary = {
        "trusted_workflow_status": trusted_workflow["status"],
        "trusted_workflow": trusted_workflow,
        "operation_sequence": trusted_workflow.get("operation_sequence"),
        "thread_model_status": "not_requested",
        "corner_radius_status": "not_requested",
        "drawing_view_status": diagnostics.get("drawing_view_status"),
        "drawing_view_roles": sorted(drawing_view_roles),
        "missing_drawing_view_roles": missing_drawing_view_roles,
        "drawing_annotation_status": diagnostics.get("drawing_annotation_status"),
        "callout_creation_method": annotation_result.get("callout_creation_method"),
        "direct_hole_callout_created": annotation_result.get("direct_hole_callout_created"),
        "drawing_dimension_status": diagnostics.get("drawing_dimension_status"),
        "dimension_layout_status": dimension_result.get("dimension_layout_status"),
        "required_atomic_dimensions": sorted(required_atomic_dimensions),
        "missing_atomic_dimensions": missing_atomic_dimensions,
        "proxy_dimensions": proxy_dimensions,
        "non_radial_radius_dimensions": [],
        "missing_dimensions": dimension_result.get("missing_dimensions") or [],
        "material_status": diagnostics.get("material_status"),
        "custom_property_status": diagnostics.get("custom_property_status"),
        "model_geometry_status": diagnostics.get("model_geometry_status"),
        "model_geometry_body_count": geometry_result.get("body_count"),
        "model_geometry_failure_reason": geometry_result.get("failure_reason"),
        "mass_property_status": diagnostics.get("mass_property_status"),
        "mass_kg": mass_property_result.get("mass_kg"),
        "volume_m3": mass_property_result.get("volume_m3"),
        "artifact_validation_status": artifact_result.get("status"),
        "artifact_content_status": content_result.get("status"),
        "cad_content_status": cad_content_result.get("status"),
        "export_status": export_result.get("status"),
        "export_failed": failed_exports,
        "missing_requested_output_files": missing_requested_output_keys,
        "pdf_semantic_content_status": pdf_semantic_result.get("status"),
        "cleanup_status": cleanup_result.get("status"),
        "cleanup_verification_status": cleanup_result.get("cleanup_verification_status"),
        "document_state_audit_status": document_state_audit.get("status"),
        "document_state_after_cleanup_run_created_open_count": document_state_audit.get(
            "after_cleanup_run_created_open_count"
        ),
        "output_files": sorted(output_keys),
        "preview_files": sorted(preview_keys),
    }
    return {
        "status": "accepted" if not failures else "rejected",
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "repair_actions": build_repair_actions(failures, summary),
        "summary": summary,
        "expected": {
            "trusted_workflow": "controlled_atomic_model",
            "drawing_view_status": "created",
            "required_drawing_view_roles": sorted(REQUIRED_DRAWING_VIEW_ROLES),
            "hole_callouts": "required only when the atomic plan includes hole operations",
            "proxy_dimensions": [],
            "required_atomic_dimensions": sorted(required_atomic_dimensions),
            "model_geometry_status": "geometry_verified",
            "mass_property_status": "mass_properties_verified",
            "artifact_validation_status": "artifacts_ready",
            "required_output_files": sorted(REQUIRED_OUTPUT_KEYS),
            "requested_output_files": sorted(requested_output_keys),
            "required_preview_files": sorted(REQUIRED_PREVIEW_KEYS),
            "cleanup_status": ["completed", "skipped_no_documents"],
        },
    }


def _build_sheet_metal_production_acceptance_result(
    plan: ModelPlan,
    execution_ok: bool,
    diagnostics: dict[str, Any],
    output_files: dict[str, str],
    preview_files: dict[str, str],
    trusted_workflow: dict[str, Any],
) -> dict[str, Any]:
    """Build a production verdict for controlled sheet-metal base flanges."""

    drawing_view_result = _as_dict(diagnostics.get("drawing_view_result"))
    dimension_result = _as_dict(diagnostics.get("drawing_dimension_result"))
    artifact_result = _as_dict(diagnostics.get("artifact_validation_result"))
    content_result = _as_dict(diagnostics.get("artifact_content_result"))
    cad_content_result = _as_dict(content_result.get("cad_content_result"))
    pdf_semantic_result = _as_dict(content_result.get("pdf_semantic_content_result"))
    cleanup_result = _as_dict(diagnostics.get("cleanup_result"))
    material_result = _as_dict(diagnostics.get("material_result"))
    custom_property_result = _as_dict(diagnostics.get("custom_property_result"))
    geometry_result = _as_dict(diagnostics.get("model_geometry_result"))
    mass_property_result = _as_dict(diagnostics.get("mass_property_result"))
    export_result = _as_dict(diagnostics.get("export_result"))
    document_state_audit = _as_dict(diagnostics.get("document_state_audit_result"))
    sheet_metal_result = _as_dict(diagnostics.get("sheet_metal_result"))
    flat_pattern_result = _as_dict(sheet_metal_result.get("flat_pattern_result"))
    required_basic_dimensions = set(sheet_metal_base_flange_basic_dimension_ids_from_plan(plan))
    required_material = _required_material_from_plan(plan)
    required_custom_properties = _required_custom_properties_from_plan(plan)
    output_keys = set(output_files)
    preview_keys = set(preview_files)
    requested_output_keys = set(_combined_export_formats(plan))
    missing_requested_output_keys = sorted(requested_output_keys - output_keys)
    failed_exports = [
        item for item in export_result.get("failed", [])
        if isinstance(item, dict)
    ]
    created_dimensions = {
        str(item.get("id"))
        for item in dimension_result.get("created_dimensions", [])
        if isinstance(item, dict) and item.get("id")
    }
    proxy_dimensions = sorted(
        str(item.get("id"))
        for item in dimension_result.get("created_dimensions", [])
        if isinstance(item, dict) and item.get("id") and item.get("proxy_dimension") is True
    )
    missing_dimensions = sorted(required_basic_dimensions - created_dimensions)
    drawing_view_roles = _drawing_view_roles(drawing_view_result)
    missing_drawing_view_roles = sorted(REQUIRED_DRAWING_VIEW_ROLES - drawing_view_roles)
    checks = {
        "execution_ok": bool(execution_ok),
        "trusted_controlled_workflow": trusted_workflow["ok"],
        "preflight_ready": diagnostics.get("preflight_status") == "ready",
        "sheet_metal_feature_verified": diagnostics.get("sheet_metal_status") == "sheet_metal_verified"
        and sheet_metal_result.get("status") == "sheet_metal_verified"
        and sheet_metal_result.get("base_flange_created") is True,
        "flat_pattern_exported": flat_pattern_result.get("status") == "flat_pattern_exported"
        and flat_pattern_result.get("ok") is True
        and "dxf" in output_keys,
        "drawing_standard_views_created": diagnostics.get("drawing_view_status") == "created"
        and drawing_view_result.get("status") == "created"
        and not missing_drawing_view_roles,
        "basic_dimensions_created": diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
        and bool(required_basic_dimensions)
        and int(dimension_result.get("created_dimension_count") or 0) >= len(required_basic_dimensions)
        and not missing_dimensions
        and not dimension_result.get("missing_dimensions"),
        "trusted_basic_dimensions": diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
        and dimension_result.get("dimension_layout_status") == "trusted_dimensions_created"
        and not proxy_dimensions,
        "material_verified": required_material is None
        or (
            diagnostics.get("material_status") == "material_verified"
            and _material_result_matches(material_result, required_material)
        ),
        "custom_properties_verified": not required_custom_properties
        or (
            diagnostics.get("custom_property_status") == "custom_properties_verified"
            and _custom_property_result_matches(custom_property_result, required_custom_properties)
        ),
        "model_geometry_verified": diagnostics.get("model_geometry_status") == "geometry_verified"
        and geometry_result.get("status") == "geometry_verified"
        and int(geometry_result.get("body_count") or 0) >= 1,
        "mass_properties_verified": diagnostics.get("mass_property_status") == "mass_properties_verified"
        and _positive_number(mass_property_result.get("mass_kg"))
        and _positive_number(mass_property_result.get("volume_m3")),
        "artifacts_ready": artifact_result.get("ok") is True
        and artifact_result.get("status") == "artifacts_ready",
        "artifact_content_ready": content_result.get("ok") is True
        and content_result.get("status") in {"content_ready", "mock_preview_placeholders", "mock_output_placeholders"},
        "cad_artifact_content": cad_content_result.get("ok") is True
        and cad_content_result.get("status") in {"cad_artifacts_verified", "mock_placeholder"},
        "drawing_pdf_semantic_content": pdf_semantic_result.get("ok") is True
        and pdf_semantic_result.get("status") in {"pdf_semantic_content_verified", "mock_placeholder"},
        "required_output_files": REQUIRED_SHEET_METAL_OUTPUT_KEYS.issubset(output_keys),
        "requested_output_files": not missing_requested_output_keys and not failed_exports,
        "required_preview_files": REQUIRED_PREVIEW_KEYS.issubset(preview_keys),
        "cleanup_completed": cleanup_result.get("enabled") is True
        and cleanup_result.get("status") in {"completed", "skipped_no_documents"},
        "cleanup_verified": cleanup_result.get("status") == "skipped_no_documents"
        or cleanup_result.get("cleanup_verification_status") == "verified",
        "document_state_audit_verified": document_state_audit.get("status")
        == "verified_no_run_documents_open"
        and document_state_audit.get("after_cleanup_run_created_open_count") == 0,
    }
    failures = [name for name, ok in checks.items() if not ok]
    summary = {
        "trusted_workflow_status": trusted_workflow["status"],
        "trusted_workflow": trusted_workflow,
        "thread_model_status": "not_requested",
        "corner_radius_status": "not_requested",
        "drawing_annotation_status": "not_requested",
        "drawing_view_status": diagnostics.get("drawing_view_status"),
        "drawing_view_roles": sorted(drawing_view_roles),
        "missing_drawing_view_roles": missing_drawing_view_roles,
        "drawing_dimension_status": diagnostics.get("drawing_dimension_status"),
        "dimension_layout_status": dimension_result.get("dimension_layout_status"),
        "proxy_dimensions": proxy_dimensions,
        "non_radial_radius_dimensions": [],
        "missing_dimensions": missing_dimensions or dimension_result.get("missing_dimensions") or [],
        "sheet_metal_status": diagnostics.get("sheet_metal_status"),
        "sheet_metal_feature_name": sheet_metal_result.get("feature_name"),
        "base_flange_created": sheet_metal_result.get("base_flange_created"),
        "flat_pattern_status": flat_pattern_result.get("status"),
        "flat_pattern_dxf_path": flat_pattern_result.get("path"),
        "material_status": diagnostics.get("material_status"),
        "custom_property_status": diagnostics.get("custom_property_status"),
        "model_geometry_status": diagnostics.get("model_geometry_status"),
        "model_geometry_max_error_mm": geometry_result.get("max_error_mm"),
        "model_geometry_measured_dimensions_mm": geometry_result.get("measured_dimensions_mm"),
        "model_geometry_expected_dimensions_mm": geometry_result.get("expected_dimensions_mm"),
        "model_geometry_body_count": geometry_result.get("body_count"),
        "mass_property_status": diagnostics.get("mass_property_status"),
        "mass_kg": mass_property_result.get("mass_kg"),
        "volume_m3": mass_property_result.get("volume_m3"),
        "artifact_validation_status": artifact_result.get("status"),
        "artifact_content_status": content_result.get("status"),
        "cad_content_status": cad_content_result.get("status"),
        "export_status": export_result.get("status"),
        "export_failed": failed_exports,
        "missing_requested_output_files": missing_requested_output_keys,
        "pdf_semantic_content_status": pdf_semantic_result.get("status"),
        "cleanup_status": cleanup_result.get("status"),
        "cleanup_verification_status": cleanup_result.get("cleanup_verification_status"),
        "document_state_audit_status": document_state_audit.get("status"),
        "document_state_after_cleanup_run_created_open_count": document_state_audit.get(
            "after_cleanup_run_created_open_count"
        ),
        "output_files": sorted(output_keys),
        "preview_files": sorted(preview_keys),
    }
    return {
        "status": "accepted" if not failures else "rejected",
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "repair_actions": build_repair_actions(failures, summary),
        "summary": summary,
        "expected": {
            "trusted_workflow": "controlled_sheet_metal_base_flange",
            "sheet_metal_status": "sheet_metal_verified",
            "flat_pattern_status": "flat_pattern_exported",
            "drawing_view_status": "created",
            "required_drawing_view_roles": sorted(REQUIRED_DRAWING_VIEW_ROLES),
            "drawing_annotation_status": "not_requested",
            "required_dimensions": sorted(required_basic_dimensions),
            "dimension_layout_status": "trusted_dimensions_created",
            "required_output_files": sorted(REQUIRED_SHEET_METAL_OUTPUT_KEYS),
            "requested_output_files": sorted(requested_output_keys),
            "required_preview_files": sorted(REQUIRED_PREVIEW_KEYS),
            "cleanup_status": ["completed", "skipped_no_documents"],
        },
    }


def _build_assembly_production_acceptance_result(
    plan: ModelPlan,
    execution_ok: bool,
    diagnostics: dict[str, Any],
    output_files: dict[str, str],
    preview_files: dict[str, str],
    trusted_workflow: dict[str, Any],
) -> dict[str, Any]:
    """Build a production verdict for controlled assembly and BOM workflows."""

    drawing_view_result = _as_dict(diagnostics.get("drawing_view_result"))
    artifact_result = _as_dict(diagnostics.get("artifact_validation_result"))
    content_result = _as_dict(diagnostics.get("artifact_content_result"))
    cad_content_result = _as_dict(content_result.get("cad_content_result"))
    pdf_semantic_result = _as_dict(content_result.get("pdf_semantic_content_result"))
    cleanup_result = _as_dict(diagnostics.get("cleanup_result"))
    custom_property_result = _as_dict(diagnostics.get("custom_property_result"))
    export_result = _as_dict(diagnostics.get("export_result"))
    document_state_audit = _as_dict(diagnostics.get("document_state_audit_result"))
    assembly_result = _as_dict(diagnostics.get("assembly_result"))
    bom_result = _as_dict(diagnostics.get("bom_result"))
    required_custom_properties = _required_custom_properties_from_plan(plan)
    output_keys = set(output_files)
    preview_keys = set(preview_files)
    requested_output_keys = set(_combined_export_formats(plan))
    missing_requested_output_keys = sorted(requested_output_keys - output_keys)
    failed_exports = [
        item for item in export_result.get("failed", [])
        if isinstance(item, dict)
    ]
    drawing_view_roles = _drawing_view_roles(drawing_view_result)
    required_outputs = {"sldasm", "step", "pdf", "dwg", "csv"}
    checks = {
        "execution_ok": bool(execution_ok),
        "trusted_controlled_workflow": trusted_workflow["ok"],
        "preflight_ready": diagnostics.get("preflight_status") == "ready",
        "assembly_structure_verified": assembly_result.get("status") == "assembly_verified"
        and int(assembly_result.get("component_instance_count") or 0) >= 2,
        "bom_verified": bom_result.get("status") == "bom_verified"
        and int(bom_result.get("row_count") or 0) >= 2,
        "drawing_standard_views_created": diagnostics.get("drawing_view_status") == "created"
        and drawing_view_result.get("status") == "created",
        "custom_properties_verified": not required_custom_properties
        or (
            diagnostics.get("custom_property_status") == "custom_properties_verified"
            and _custom_property_result_matches(custom_property_result, required_custom_properties)
        ),
        "artifacts_ready": artifact_result.get("ok") is True
        and artifact_result.get("status") == "artifacts_ready",
        "artifact_content_ready": content_result.get("ok") is True
        and content_result.get("status") in {"content_ready", "mock_preview_placeholders", "mock_output_placeholders"},
        "cad_artifact_content": cad_content_result.get("ok") is True
        and cad_content_result.get("status") in {"cad_artifacts_verified", "mock_placeholder"},
        "drawing_pdf_semantic_content": pdf_semantic_result.get("ok") is True
        and pdf_semantic_result.get("status") in {"pdf_semantic_content_verified", "mock_placeholder"},
        "required_output_files": required_outputs.issubset(output_keys),
        "requested_output_files": not missing_requested_output_keys and not failed_exports,
        "required_preview_files": REQUIRED_PREVIEW_KEYS.issubset(preview_keys),
        "cleanup_completed": cleanup_result.get("enabled") is True
        and cleanup_result.get("status") in {"completed", "skipped_no_documents"},
        "cleanup_verified": cleanup_result.get("status") == "skipped_no_documents"
        or cleanup_result.get("cleanup_verification_status") == "verified",
        "document_state_audit_verified": document_state_audit.get("status")
        == "verified_no_run_documents_open"
        and document_state_audit.get("after_cleanup_run_created_open_count") == 0,
    }
    failures = [name for name, ok in checks.items() if not ok]
    summary = {
        "trusted_workflow_status": trusted_workflow["status"],
        "trusted_workflow": trusted_workflow,
        "thread_model_status": "not_requested",
        "corner_radius_status": "not_requested",
        "drawing_view_status": diagnostics.get("drawing_view_status"),
        "drawing_view_roles": sorted(drawing_view_roles),
        "drawing_annotation_status": "not_requested",
        "dimension_layout_status": "not_requested",
        "proxy_dimensions": [],
        "non_radial_radius_dimensions": [],
        "missing_dimensions": [],
        "assembly_status": assembly_result.get("status"),
        "component_instance_count": assembly_result.get("component_instance_count"),
        "component_definitions": assembly_result.get("component_definitions"),
        "bom_status": bom_result.get("status"),
        "bom_row_count": bom_result.get("row_count"),
        "bom_columns": bom_result.get("columns"),
        "custom_property_status": diagnostics.get("custom_property_status"),
        "artifact_validation_status": artifact_result.get("status"),
        "artifact_content_status": content_result.get("status"),
        "cad_content_status": cad_content_result.get("status"),
        "export_status": export_result.get("status"),
        "export_failed": failed_exports,
        "missing_requested_output_files": missing_requested_output_keys,
        "pdf_semantic_content_status": pdf_semantic_result.get("status"),
        "cleanup_status": cleanup_result.get("status"),
        "cleanup_verification_status": cleanup_result.get("cleanup_verification_status"),
        "document_state_audit_status": document_state_audit.get("status"),
        "document_state_after_cleanup_run_created_open_count": document_state_audit.get(
            "after_cleanup_run_created_open_count"
        ),
        "output_files": sorted(output_keys),
        "preview_files": sorted(preview_keys),
    }
    return {
        "status": "accepted" if not failures else "rejected",
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "repair_actions": build_repair_actions(failures, summary),
        "summary": summary,
        "expected": {
            "trusted_workflow": "controlled_bom_assembly",
            "assembly_status": "assembly_verified",
            "bom_status": "bom_verified",
            "drawing_view_status": "created",
            "required_output_files": sorted(required_outputs),
            "requested_output_files": sorted(requested_output_keys),
            "required_preview_files": sorted(REQUIRED_PREVIEW_KEYS),
            "cleanup_status": ["completed", "skipped_no_documents"],
        },
    }


def _build_weldment_production_acceptance_result(
    plan: ModelPlan,
    execution_ok: bool,
    diagnostics: dict[str, Any],
    output_files: dict[str, str],
    preview_files: dict[str, str],
    trusted_workflow: dict[str, Any],
) -> dict[str, Any]:
    """Build a production verdict for controlled structural-member weldment frames."""

    drawing_view_result = _as_dict(diagnostics.get("drawing_view_result"))
    dimension_result = _as_dict(diagnostics.get("drawing_dimension_result"))
    artifact_result = _as_dict(diagnostics.get("artifact_validation_result"))
    content_result = _as_dict(diagnostics.get("artifact_content_result"))
    cad_content_result = _as_dict(content_result.get("cad_content_result"))
    pdf_semantic_result = _as_dict(content_result.get("pdf_semantic_content_result"))
    cleanup_result = _as_dict(diagnostics.get("cleanup_result"))
    material_result = _as_dict(diagnostics.get("material_result"))
    custom_property_result = _as_dict(diagnostics.get("custom_property_result"))
    geometry_result = _as_dict(diagnostics.get("model_geometry_result"))
    mass_property_result = _as_dict(diagnostics.get("mass_property_result"))
    export_result = _as_dict(diagnostics.get("export_result"))
    document_state_audit = _as_dict(diagnostics.get("document_state_audit_result"))
    weldment_result = _as_dict(diagnostics.get("weldment_result"))
    cut_list_result = _as_dict(diagnostics.get("cut_list_result"))
    required_basic_dimensions = set(weldment_frame_basic_dimension_ids_from_plan(plan))
    required_material = _required_material_from_plan(plan)
    required_custom_properties = _required_custom_properties_from_plan(plan)
    output_keys = set(output_files)
    preview_keys = set(preview_files)
    requested_output_keys = set(_combined_export_formats(plan))
    missing_requested_output_keys = sorted(requested_output_keys - output_keys)
    failed_exports = [
        item for item in export_result.get("failed", [])
        if isinstance(item, dict)
    ]
    created_dimensions = {
        str(item.get("id"))
        for item in dimension_result.get("created_dimensions", [])
        if isinstance(item, dict) and item.get("id")
    }
    proxy_dimensions = sorted(
        str(item.get("id"))
        for item in dimension_result.get("created_dimensions", [])
        if isinstance(item, dict) and item.get("id") and item.get("proxy_dimension") is True
    )
    missing_dimensions = sorted(required_basic_dimensions - created_dimensions)
    drawing_view_roles = _drawing_view_roles(drawing_view_result)
    missing_drawing_view_roles = sorted(REQUIRED_DRAWING_VIEW_ROLES - drawing_view_roles)
    try:
        weldment_body_count = int(weldment_result.get("body_count") or 0)
    except (TypeError, ValueError):
        weldment_body_count = 0
    try:
        cut_list_row_count = int(cut_list_result.get("row_count") or 0)
    except (TypeError, ValueError):
        cut_list_row_count = 0
    checks = {
        "execution_ok": bool(execution_ok),
        "trusted_controlled_workflow": trusted_workflow["ok"],
        "preflight_ready": diagnostics.get("preflight_status") == "ready",
        "weldment_feature_verified": diagnostics.get("weldment_status") == "weldment_verified"
        and weldment_result.get("status") == "weldment_verified"
        and weldment_result.get("structural_member_created") is True
        and weldment_body_count >= 4,
        "cut_list_verified": diagnostics.get("cut_list_status") == "cut_list_verified"
        and cut_list_result.get("status") == "cut_list_verified"
        and cut_list_row_count >= 2
        and "csv" in output_keys,
        "drawing_standard_views_created": diagnostics.get("drawing_view_status") == "created"
        and drawing_view_result.get("status") == "created"
        and not missing_drawing_view_roles,
        "basic_dimensions_created": diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
        and bool(required_basic_dimensions)
        and int(dimension_result.get("created_dimension_count") or 0) >= len(required_basic_dimensions)
        and not missing_dimensions
        and not dimension_result.get("missing_dimensions"),
        "trusted_basic_dimensions": diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
        and dimension_result.get("dimension_layout_status") == "trusted_dimensions_created"
        and not proxy_dimensions,
        "material_verified": required_material is None
        or (
            diagnostics.get("material_status") == "material_verified"
            and _material_result_matches(material_result, required_material)
        ),
        "custom_properties_verified": not required_custom_properties
        or (
            diagnostics.get("custom_property_status") == "custom_properties_verified"
            and _custom_property_result_matches(custom_property_result, required_custom_properties)
        ),
        "model_geometry_verified": diagnostics.get("model_geometry_status") == "geometry_verified"
        and geometry_result.get("status") == "geometry_verified"
        and int(geometry_result.get("body_count") or 0) >= 4,
        "mass_properties_verified": diagnostics.get("mass_property_status") == "mass_properties_verified"
        and _positive_number(mass_property_result.get("mass_kg"))
        and _positive_number(mass_property_result.get("volume_m3")),
        "artifacts_ready": artifact_result.get("ok") is True
        and artifact_result.get("status") == "artifacts_ready",
        "artifact_content_ready": content_result.get("ok") is True
        and content_result.get("status") in {"content_ready", "mock_preview_placeholders", "mock_output_placeholders"},
        "cad_artifact_content": cad_content_result.get("ok") is True
        and cad_content_result.get("status") in {"cad_artifacts_verified", "mock_placeholder"},
        "drawing_pdf_semantic_content": pdf_semantic_result.get("ok") is True
        and pdf_semantic_result.get("status") in {"pdf_semantic_content_verified", "mock_placeholder"},
        "required_output_files": REQUIRED_WELDMENT_OUTPUT_KEYS.issubset(output_keys),
        "requested_output_files": not missing_requested_output_keys and not failed_exports,
        "required_preview_files": REQUIRED_PREVIEW_KEYS.issubset(preview_keys),
        "cleanup_completed": cleanup_result.get("enabled") is True
        and cleanup_result.get("status") in {"completed", "skipped_no_documents"},
        "cleanup_verified": cleanup_result.get("status") == "skipped_no_documents"
        or cleanup_result.get("cleanup_verification_status") == "verified",
        "document_state_audit_verified": document_state_audit.get("status")
        == "verified_no_run_documents_open"
        and document_state_audit.get("after_cleanup_run_created_open_count") == 0,
    }
    failures = [name for name, ok in checks.items() if not ok]
    summary = {
        "trusted_workflow_status": trusted_workflow["status"],
        "trusted_workflow": trusted_workflow,
        "thread_model_status": "not_requested",
        "corner_radius_status": "not_requested",
        "drawing_annotation_status": "not_requested",
        "drawing_view_status": diagnostics.get("drawing_view_status"),
        "drawing_view_roles": sorted(drawing_view_roles),
        "missing_drawing_view_roles": missing_drawing_view_roles,
        "drawing_dimension_status": diagnostics.get("drawing_dimension_status"),
        "dimension_layout_status": dimension_result.get("dimension_layout_status"),
        "proxy_dimensions": proxy_dimensions,
        "non_radial_radius_dimensions": [],
        "missing_dimensions": missing_dimensions or dimension_result.get("missing_dimensions") or [],
        "weldment_status": diagnostics.get("weldment_status"),
        "structural_member_created": weldment_result.get("structural_member_created"),
        "weldment_feature_type": weldment_result.get("feature_type"),
        "weldment_body_count": weldment_body_count,
        "cut_list_status": diagnostics.get("cut_list_status"),
        "cut_list_row_count": cut_list_row_count,
        "cut_list_columns": cut_list_result.get("columns"),
        "material_status": diagnostics.get("material_status"),
        "custom_property_status": diagnostics.get("custom_property_status"),
        "model_geometry_status": diagnostics.get("model_geometry_status"),
        "model_geometry_max_error_mm": geometry_result.get("max_error_mm"),
        "model_geometry_measured_dimensions_mm": geometry_result.get("measured_dimensions_mm"),
        "model_geometry_expected_dimensions_mm": geometry_result.get("expected_dimensions_mm"),
        "model_geometry_body_count": geometry_result.get("body_count"),
        "mass_property_status": diagnostics.get("mass_property_status"),
        "mass_kg": mass_property_result.get("mass_kg"),
        "volume_m3": mass_property_result.get("volume_m3"),
        "artifact_validation_status": artifact_result.get("status"),
        "artifact_content_status": content_result.get("status"),
        "cad_content_status": cad_content_result.get("status"),
        "export_status": export_result.get("status"),
        "export_failed": failed_exports,
        "missing_requested_output_files": missing_requested_output_keys,
        "pdf_semantic_content_status": pdf_semantic_result.get("status"),
        "cleanup_status": cleanup_result.get("status"),
        "cleanup_verification_status": cleanup_result.get("cleanup_verification_status"),
        "document_state_audit_status": document_state_audit.get("status"),
        "document_state_after_cleanup_run_created_open_count": document_state_audit.get(
            "after_cleanup_run_created_open_count"
        ),
        "output_files": sorted(output_keys),
        "preview_files": sorted(preview_keys),
    }
    return {
        "status": "accepted" if not failures else "rejected",
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "repair_actions": build_repair_actions(failures, summary),
        "summary": summary,
        "expected": {
            "trusted_workflow": "controlled_weldment_frame",
            "weldment_status": "weldment_verified",
            "cut_list_status": "cut_list_verified",
            "drawing_view_status": "created",
            "required_drawing_view_roles": sorted(REQUIRED_DRAWING_VIEW_ROLES),
            "drawing_annotation_status": "not_requested",
            "required_dimensions": sorted(required_basic_dimensions),
            "dimension_layout_status": "trusted_dimensions_created",
            "required_output_files": sorted(REQUIRED_WELDMENT_OUTPUT_KEYS),
            "requested_output_files": sorted(requested_output_keys),
            "required_preview_files": sorted(REQUIRED_PREVIEW_KEYS),
            "cleanup_status": ["completed", "skipped_no_documents"],
        },
    }


def _build_static_simulation_production_acceptance_result(
    plan: ModelPlan,
    execution_ok: bool,
    diagnostics: dict[str, Any],
    output_files: dict[str, str],
    preview_files: dict[str, str],
    trusted_workflow: dict[str, Any],
) -> dict[str, Any]:
    """Build a production verdict for controlled static simulation studies."""

    drawing_view_result = _as_dict(diagnostics.get("drawing_view_result"))
    dimension_result = _as_dict(diagnostics.get("drawing_dimension_result"))
    artifact_result = _as_dict(diagnostics.get("artifact_validation_result"))
    content_result = _as_dict(diagnostics.get("artifact_content_result"))
    cad_content_result = _as_dict(content_result.get("cad_content_result"))
    pdf_semantic_result = _as_dict(content_result.get("pdf_semantic_content_result"))
    cleanup_result = _as_dict(diagnostics.get("cleanup_result"))
    material_result = _as_dict(diagnostics.get("material_result"))
    custom_property_result = _as_dict(diagnostics.get("custom_property_result"))
    geometry_result = _as_dict(diagnostics.get("model_geometry_result"))
    mass_property_result = _as_dict(diagnostics.get("mass_property_result"))
    export_result = _as_dict(diagnostics.get("export_result"))
    document_state_audit = _as_dict(diagnostics.get("document_state_audit_result"))
    simulation_result = _as_dict(diagnostics.get("simulation_result"))
    required_basic_dimensions = set(static_simulation_basic_dimension_ids_from_plan(plan))
    required_material = _required_material_from_plan(plan)
    required_custom_properties = _required_custom_properties_from_plan(plan)
    output_keys = set(output_files)
    preview_keys = set(preview_files)
    requested_output_keys = set(_combined_export_formats(plan))
    missing_requested_output_keys = sorted(requested_output_keys - output_keys)
    failed_exports = [
        item for item in export_result.get("failed", [])
        if isinstance(item, dict)
    ]
    created_dimensions = {
        str(item.get("id"))
        for item in dimension_result.get("created_dimensions", [])
        if isinstance(item, dict) and item.get("id")
    }
    proxy_dimensions = sorted(
        str(item.get("id"))
        for item in dimension_result.get("created_dimensions", [])
        if isinstance(item, dict) and item.get("id") and item.get("proxy_dimension") is True
    )
    missing_dimensions = sorted(required_basic_dimensions - created_dimensions)
    drawing_view_roles = _drawing_view_roles(drawing_view_result)
    missing_drawing_view_roles = sorted(REQUIRED_DRAWING_VIEW_ROLES - drawing_view_roles)
    checks_payload = _as_dict(simulation_result.get("checks"))
    try:
        row_count = int(simulation_result.get("row_count") or 0)
    except (TypeError, ValueError):
        row_count = 0
    checks = {
        "execution_ok": bool(execution_ok),
        "trusted_controlled_workflow": trusted_workflow["ok"],
        "preflight_ready": diagnostics.get("preflight_status") == "ready",
        "simulation_study_verified": diagnostics.get("simulation_status") == "simulation_verified"
        and simulation_result.get("status") == "simulation_verified"
        and simulation_result.get("study_type") == "static",
        "simulation_results_within_limits": bool(checks_payload)
        and all(value is True for value in checks_payload.values()),
        "simulation_report_verified": row_count >= 3 and "csv" in output_keys,
        "drawing_standard_views_created": diagnostics.get("drawing_view_status") == "created"
        and drawing_view_result.get("status") == "created"
        and not missing_drawing_view_roles,
        "basic_dimensions_created": diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
        and bool(required_basic_dimensions)
        and int(dimension_result.get("created_dimension_count") or 0) >= len(required_basic_dimensions)
        and not missing_dimensions
        and not dimension_result.get("missing_dimensions"),
        "trusted_basic_dimensions": diagnostics.get("drawing_dimension_status") == "basic_dimensions_created"
        and dimension_result.get("dimension_layout_status") == "trusted_dimensions_created"
        and not proxy_dimensions,
        "material_verified": required_material is None
        or (
            diagnostics.get("material_status") == "material_verified"
            and _material_result_matches(material_result, required_material)
        ),
        "custom_properties_verified": not required_custom_properties
        or (
            diagnostics.get("custom_property_status") == "custom_properties_verified"
            and _custom_property_result_matches(custom_property_result, required_custom_properties)
        ),
        "model_geometry_verified": diagnostics.get("model_geometry_status") == "geometry_verified"
        and geometry_result.get("status") == "geometry_verified"
        and int(geometry_result.get("body_count") or 0) >= 1,
        "mass_properties_verified": diagnostics.get("mass_property_status") == "mass_properties_verified"
        and _positive_number(mass_property_result.get("mass_kg"))
        and _positive_number(mass_property_result.get("volume_m3")),
        "artifacts_ready": artifact_result.get("ok") is True
        and artifact_result.get("status") == "artifacts_ready",
        "artifact_content_ready": content_result.get("ok") is True
        and content_result.get("status") in {"content_ready", "mock_preview_placeholders", "mock_output_placeholders"},
        "cad_artifact_content": cad_content_result.get("ok") is True
        and cad_content_result.get("status") in {"cad_artifacts_verified", "mock_placeholder"},
        "drawing_pdf_semantic_content": pdf_semantic_result.get("ok") is True
        and pdf_semantic_result.get("status") in {"pdf_semantic_content_verified", "mock_placeholder"},
        "required_output_files": REQUIRED_SIMULATION_OUTPUT_KEYS.issubset(output_keys),
        "requested_output_files": not missing_requested_output_keys and not failed_exports,
        "required_preview_files": REQUIRED_PREVIEW_KEYS.issubset(preview_keys),
        "cleanup_completed": cleanup_result.get("enabled") is True
        and cleanup_result.get("status") in {"completed", "skipped_no_documents"},
        "cleanup_verified": cleanup_result.get("status") == "skipped_no_documents"
        or cleanup_result.get("cleanup_verification_status") == "verified",
        "document_state_audit_verified": document_state_audit.get("status")
        == "verified_no_run_documents_open"
        and document_state_audit.get("after_cleanup_run_created_open_count") == 0,
    }
    failures = [name for name, ok in checks.items() if not ok]
    summary = {
        "trusted_workflow_status": trusted_workflow["status"],
        "trusted_workflow": trusted_workflow,
        "thread_model_status": "not_requested",
        "corner_radius_status": "not_requested",
        "drawing_annotation_status": "not_requested",
        "drawing_view_status": diagnostics.get("drawing_view_status"),
        "drawing_view_roles": sorted(drawing_view_roles),
        "missing_drawing_view_roles": missing_drawing_view_roles,
        "drawing_dimension_status": diagnostics.get("drawing_dimension_status"),
        "dimension_layout_status": dimension_result.get("dimension_layout_status"),
        "proxy_dimensions": proxy_dimensions,
        "non_radial_radius_dimensions": [],
        "missing_dimensions": missing_dimensions or dimension_result.get("missing_dimensions") or [],
        "simulation_status": diagnostics.get("simulation_status"),
        "simulation_study_type": simulation_result.get("study_type"),
        "simulation_solver": simulation_result.get("solver"),
        "simulation_report_row_count": row_count,
        "simulation_max_von_mises_mpa": simulation_result.get("max_von_mises_mpa"),
        "simulation_min_factor_of_safety": simulation_result.get("min_factor_of_safety"),
        "simulation_max_displacement_mm": simulation_result.get("max_displacement_mm"),
        "simulation_checks": checks_payload,
        "material_status": diagnostics.get("material_status"),
        "custom_property_status": diagnostics.get("custom_property_status"),
        "model_geometry_status": diagnostics.get("model_geometry_status"),
        "model_geometry_max_error_mm": geometry_result.get("max_error_mm"),
        "model_geometry_measured_dimensions_mm": geometry_result.get("measured_dimensions_mm"),
        "model_geometry_expected_dimensions_mm": geometry_result.get("expected_dimensions_mm"),
        "model_geometry_body_count": geometry_result.get("body_count"),
        "mass_property_status": diagnostics.get("mass_property_status"),
        "mass_kg": mass_property_result.get("mass_kg"),
        "volume_m3": mass_property_result.get("volume_m3"),
        "artifact_validation_status": artifact_result.get("status"),
        "artifact_content_status": content_result.get("status"),
        "cad_content_status": cad_content_result.get("status"),
        "export_status": export_result.get("status"),
        "export_failed": failed_exports,
        "missing_requested_output_files": missing_requested_output_keys,
        "pdf_semantic_content_status": pdf_semantic_result.get("status"),
        "cleanup_status": cleanup_result.get("status"),
        "cleanup_verification_status": cleanup_result.get("cleanup_verification_status"),
        "document_state_audit_status": document_state_audit.get("status"),
        "document_state_after_cleanup_run_created_open_count": document_state_audit.get(
            "after_cleanup_run_created_open_count"
        ),
        "output_files": sorted(output_keys),
        "preview_files": sorted(preview_keys),
    }
    return {
        "status": "accepted" if not failures else "rejected",
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "repair_actions": build_repair_actions(failures, summary),
        "summary": summary,
        "expected": {
            "trusted_workflow": "controlled_static_simulation",
            "simulation_status": "simulation_verified",
            "simulation_report": "csv",
            "drawing_view_status": "created",
            "required_drawing_view_roles": sorted(REQUIRED_DRAWING_VIEW_ROLES),
            "drawing_annotation_status": "not_requested",
            "required_dimensions": sorted(required_basic_dimensions),
            "dimension_layout_status": "trusted_dimensions_created",
            "required_output_files": sorted(REQUIRED_SIMULATION_OUTPUT_KEYS),
            "requested_output_files": sorted(requested_output_keys),
            "required_preview_files": sorted(REQUIRED_PREVIEW_KEYS),
            "cleanup_status": ["completed", "skipped_no_documents"],
        },
    }


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a dict when diagnostics contain the expected object shape."""

    return value if isinstance(value, dict) else {}


def _trusted_basic_dimension_ids_from_plan(plan: ModelPlan) -> list[str]:
    """Return the required drawing dimensions for the controlled workflow."""

    mounting_plate_dimensions = mounting_plate_basic_dimension_ids_from_plan(plan)
    if mounting_plate_dimensions:
        return mounting_plate_dimensions
    bracket_dimensions = bracket_basic_dimension_ids_from_plan(plan)
    if bracket_dimensions:
        return bracket_dimensions
    flange_dimensions = center_hole_flange_basic_dimension_ids_from_plan(plan)
    if flange_dimensions:
        return flange_dimensions
    center_hole_plate_dimensions = center_hole_plate_basic_dimension_ids_from_plan(plan)
    if center_hole_plate_dimensions:
        return center_hole_plate_dimensions
    end_cap_dimensions = end_cap_basic_dimension_ids_from_plan(plan)
    if end_cap_dimensions:
        return end_cap_dimensions
    mounting_block_dimensions = mounting_block_basic_dimension_ids_from_plan(plan)
    if mounting_block_dimensions:
        return mounting_block_dimensions
    shaft_dimensions = shaft_basic_dimension_ids_from_plan(plan)
    if shaft_dimensions:
        return shaft_dimensions
    sheet_metal_dimensions = sheet_metal_base_flange_basic_dimension_ids_from_plan(plan)
    if sheet_metal_dimensions:
        return sheet_metal_dimensions
    weldment_dimensions = weldment_frame_basic_dimension_ids_from_plan(plan)
    if weldment_dimensions:
        return weldment_dimensions
    simulation_dimensions = static_simulation_basic_dimension_ids_from_plan(plan)
    if simulation_dimensions:
        return simulation_dimensions
    washer_dimensions = washer_basic_dimension_ids_from_plan(plan)
    if washer_dimensions:
        return washer_dimensions
    sleeve_dimensions = sleeve_basic_dimension_ids_from_plan(plan)
    if sleeve_dimensions:
        return sleeve_dimensions
    return slotted_array_plate_basic_dimension_ids_from_plan(plan)


def _drawing_view_roles(view_result: dict[str, Any]) -> set[str]:
    """Return the standard drawing-view roles reported by the adapter."""

    roles: set[str] = set()
    for item in view_result.get("views", []):
        if isinstance(item, dict) and item.get("role"):
            roles.add(str(item["role"]))
    return roles


def _validate_cad_artifact_content(
    output_files: dict[str, str],
    *,
    forced_failure: bool = False,
) -> dict[str, Any]:
    """Validate exported CAD files beyond existence and byte size."""

    required = ("sldasm", "step", "dwg", "csv") if "sldasm" in output_files else ("sldprt", "step", "stl", "slddrw", "dwg")
    requested_optional = tuple(
        artifact_id
        for artifact_id in sorted(OPTIONAL_CAD_CONTENT_KEYS)
        if artifact_id in output_files
    )
    checks = [
        _inspect_cad_artifact(artifact_id, Path(output_files[artifact_id]))
        for artifact_id in (*required, *requested_optional)
        if artifact_id in output_files
    ]
    missing = [artifact_id for artifact_id in required if artifact_id not in output_files]
    failed = [
        {"id": check["id"], "status": check["status"], "path": check["path"]}
        for check in checks
        if not check.get("ok")
    ]
    failed.extend({"id": artifact_id, "status": "missing", "path": None} for artifact_id in missing)
    if forced_failure:
        failed.append({"id": "forced_failure", "status": "forced_failure", "path": None})
    mock_placeholders = [
        check for check in checks if check.get("status") == "mock_placeholder"
    ]
    if mock_placeholders and len(mock_placeholders) == len(checks) and not missing and not forced_failure:
        return {
            "status": "mock_placeholder",
            "ok": True,
            "checks": checks,
            "failed": [],
            "missing": [],
        }
    return {
        "status": "cad_artifacts_verified"
        if not failed
        else "forced_failure"
        if forced_failure
        else "cad_artifact_content_failed",
        "ok": not failed,
        "checks": checks,
        "failed": failed,
        "missing": missing,
        "failure_reason": "SOLIDWORKS_MCP_FORCE_CAD_CONTENT_FAILURE is enabled"
        if forced_failure
        else None,
    }


def _inspect_cad_artifact(artifact_id: str, path: Path) -> dict[str, Any]:
    """Inspect one exported CAD artifact with format-specific conservative checks."""

    result: dict[str, Any] = {
        "id": artifact_id,
        "path": path_to_string(path),
        "status": "not_checked",
        "ok": False,
    }
    if not path.exists() or not path.is_file():
        result["status"] = "missing"
        return result

    data = path.read_bytes()
    result["size_bytes"] = len(data)
    if data.startswith(b"Mock "):
        result["status"] = "mock_placeholder"
        result["ok"] = True
        return result
    if artifact_id == "step":
        result.update(_inspect_step_content(data))
    elif artifact_id == "stl":
        result.update(_inspect_stl_content(data))
    elif artifact_id == "dwg":
        result.update(_inspect_dwg_content(data))
    elif artifact_id == "dxf":
        result.update(_inspect_dxf_content(data))
    elif artifact_id == "iges":
        result.update(_inspect_iges_content(data))
    elif artifact_id in {"x_t", "x_b"}:
        result.update(_inspect_parasolid_content(data, artifact_id))
    elif artifact_id in {"sldprt", "sldasm", "slddrw"}:
        result.update(_inspect_solidworks_native_content(data, artifact_id))
    elif artifact_id == "csv":
        result.update(_inspect_csv_content(data))
    else:
        result["status"] = "unsupported_cad_artifact"
    return result


def _inspect_csv_content(data: bytes) -> dict[str, Any]:
    """Inspect a BOM, weldment cut-list or simulation CSV export."""

    text = data.decode("utf-8", errors="ignore")
    lines = [line for line in text.splitlines() if line.strip()]
    header = lines[0].lower() if lines else ""
    tabular_manufacturing = "quantity" in header and ("part_number" in header or "component_id" in header)
    simulation_report = "metric" in header and "value" in header and "status" in header
    valid = len(lines) >= 2 and (tabular_manufacturing or simulation_report)
    return {
        "status": "csv_readable" if valid else "csv_invalid",
        "ok": valid,
        "line_count": len(lines),
        "csv_kind": "simulation_report" if simulation_report else "manufacturing_table" if tabular_manufacturing else None,
    }


def _inspect_step_content(data: bytes) -> dict[str, Any]:
    """Inspect a STEP AP203/AP214/AP242 text export."""

    text = data.decode("latin-1", errors="ignore").upper()
    head = data[:4096].decode("latin-1", errors="ignore").upper()
    tail = data[-4096:].decode("latin-1", errors="ignore").upper()
    has_header = "ISO-10303-21" in head and "HEADER" in head and "FILE_SCHEMA" in head
    has_data = "DATA" in head or "\nDATA" in text
    has_end = "END-ISO-10303-21" in tail
    return {
        "status": "step_readable" if has_header and has_data and has_end else "step_invalid",
        "ok": has_header and has_data and has_end,
        "has_header": has_header,
        "has_data": has_data,
        "has_end": has_end,
    }


def _inspect_stl_content(data: bytes) -> dict[str, Any]:
    """Inspect binary or ASCII STL structure."""

    if len(data) < 84:
        return {"status": "stl_too_small", "ok": False}
    header = data[:80]
    triangle_count = struct.unpack("<I", data[80:84])[0]
    expected_binary_size = 84 + triangle_count * 50
    is_binary = expected_binary_size == len(data) and triangle_count > 0
    if is_binary:
        return {
            "status": "stl_binary_readable",
            "ok": True,
            "triangle_count": triangle_count,
            "expected_size_bytes": expected_binary_size,
        }

    text_start = data[:4096].decode("latin-1", errors="ignore").lower()
    text_tail = data[-4096:].decode("latin-1", errors="ignore").lower()
    has_ascii_shape = text_start.lstrip().startswith("solid") and "facet normal" in text_start and "endsolid" in text_tail
    return {
        "status": "stl_ascii_readable" if has_ascii_shape else "stl_invalid",
        "ok": has_ascii_shape,
        "triangle_count": triangle_count if triangle_count > 0 else None,
        "binary_header": header.decode("latin-1", errors="ignore").strip(),
    }


def _inspect_dwg_content(data: bytes) -> dict[str, Any]:
    """Inspect a DWG header signature."""

    signature = data[:6].decode("ascii", errors="ignore")
    valid = signature.startswith("AC") and len(data) > 1024
    return {
        "status": "dwg_header_verified" if valid else "dwg_invalid",
        "ok": valid,
        "signature": signature,
    }


def _inspect_dxf_content(data: bytes) -> dict[str, Any]:
    """Inspect an AutoCAD DXF drawing export enough to reject placeholders."""

    head = data[:4096].decode("latin-1", errors="ignore").upper()
    tail = data[-4096:].decode("latin-1", errors="ignore").upper()
    binary_signature = data.startswith(b"AutoCAD Binary DXF")
    has_section = "SECTION" in head
    has_header = "HEADER" in head
    has_entities = "ENTITIES" in data.decode("latin-1", errors="ignore").upper()
    has_eof = "EOF" in tail
    valid = len(data) > 1024 and (
        binary_signature or (has_section and has_header and has_entities and has_eof)
    )
    return {
        "status": "dxf_readable" if valid else "dxf_invalid",
        "ok": valid,
        "binary_signature": binary_signature,
        "has_section": has_section,
        "has_header": has_header,
        "has_entities": has_entities,
        "has_eof": has_eof,
    }


def _inspect_iges_content(data: bytes) -> dict[str, Any]:
    """Inspect an IGES text export enough to catch placeholders or wrong files."""

    text = data.decode("latin-1", errors="ignore")
    tail = text[-2048:].upper()
    section_marks = {
        "start": "S" in tail,
        "global": "G" in tail,
        "directory": "D" in tail,
        "parameter": "P" in tail,
        "terminate": "T" in tail,
    }
    has_iges_signature = "S      1" in text[:4096] or "1H," in text[:4096].upper() or "IGES" in text[:4096].upper()
    has_terminate = "T      1" in tail
    valid = len(data) > 1024 and has_iges_signature and has_terminate
    return {
        "status": "iges_readable" if valid else "iges_invalid",
        "ok": valid,
        "has_iges_signature": has_iges_signature,
        "has_terminate": has_terminate,
        "section_markers": section_marks,
    }


def _inspect_parasolid_content(data: bytes, artifact_id: str) -> dict[str, Any]:
    """Inspect Parasolid text or binary exports with conservative header checks."""

    head = data[:4096]
    head_text = head.decode("latin-1", errors="ignore").lower()
    is_text = b"\x00" not in head[:512]
    has_text_signature = "parasolid" in head_text or "schema" in head_text or "transmit" in head_text
    has_binary_signature = data.startswith(b"\x00\x00") or b"parasolid" in head.lower()
    valid_text = artifact_id == "x_t" and is_text and len(data) > 1024 and has_text_signature
    valid_binary = artifact_id == "x_b" and len(data) > 1024 and (not is_text or has_binary_signature)
    valid = valid_text or valid_binary
    return {
        "status": f"{artifact_id}_readable" if valid else f"{artifact_id}_invalid",
        "ok": valid,
        "is_text": is_text,
        "has_text_signature": has_text_signature,
        "has_binary_signature": has_binary_signature,
    }


def _inspect_solidworks_native_content(data: bytes, artifact_id: str) -> dict[str, Any]:
    """Reject placeholder or implausibly small SolidWorks native exports."""

    min_size = 4096
    printable_head = data[:64].decode("latin-1", errors="ignore")
    valid = len(data) >= min_size and not printable_head.startswith("Mock ")
    return {
        "status": f"{artifact_id}_binary_present" if valid else f"{artifact_id}_invalid",
        "ok": valid,
        "minimum_size_bytes": min_size,
    }


def _required_material_from_plan(plan: ModelPlan) -> str | None:
    """Return the final material requested by the plan, if any."""

    material: str | None = None
    for operation in plan.operations:
        if operation.op == "assign_material":
            material = str(operation.parameters.get("material", "")).strip() or None
    return material


def _required_custom_properties_from_plan(plan: ModelPlan) -> dict[str, str]:
    """Return the final custom-property set requested by the plan."""

    properties: dict[str, str] = {}
    for operation in plan.operations:
        if operation.op == "set_custom_properties":
            properties = {
                str(key).strip(): str(value)
                for key, value in operation.parameters.get("properties", {}).items()
            }
    return properties


def _requires_controlled_model_geometry(plan: ModelPlan) -> bool:
    """Return whether trusted acceptance must verify model geometry readback."""

    return any(
        operation.op in {
            "create_mounting_plate",
            "create_bracket",
            "create_center_hole_flange",
            "create_center_hole_plate",
            "create_end_cap",
            "create_mounting_block",
            "create_shaft",
            "create_sheet_metal_base_flange",
            "create_weldment_frame",
            "run_static_simulation",
            "create_washer",
            "create_sleeve",
            "create_slotted_array_plate",
        }
        for operation in plan.operations
    )

def _requires_hole_callout(plan: ModelPlan) -> bool:
    """Return whether the controlled workflow contains holes that need Hole Callout evidence."""

    return (
        shaft_parameters_from_plan(plan) is None
        and sheet_metal_base_flange_parameters_from_plan(plan) is None
        and weldment_frame_parameters_from_plan(plan) is None
        and static_simulation_parameters_from_plan(plan) is None
    )


def _trusted_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to a controlled production workflow."""

    atomic_workflow = _trusted_atomic_workflow_result(plan)
    for workflow_result in (
        _trusted_bracket_workflow_result(plan),
        _trusted_mounting_plate_workflow_result(plan),
        _trusted_center_hole_flange_workflow_result(plan),
        _trusted_center_hole_plate_workflow_result(plan),
        _trusted_end_cap_workflow_result(plan),
        _trusted_mounting_block_workflow_result(plan),
        _trusted_shaft_workflow_result(plan),
        _trusted_sheet_metal_base_flange_workflow_result(plan),
        _trusted_weldment_frame_workflow_result(plan),
        _trusted_static_simulation_workflow_result(plan),
        _trusted_washer_workflow_result(plan),
        _trusted_sleeve_workflow_result(plan),
        _trusted_slotted_array_plate_workflow_result(plan),
        _trusted_bom_assembly_workflow_result(plan),
        atomic_workflow,
    ):
        if workflow_result["ok"]:
            return workflow_result
    if str(plan.metadata.get("solidworks_mcp_workflow") or "") == "atomic_model_session":
        return atomic_workflow

    operation_names = [operation.op for operation in plan.operations]
    return {
        "ok": False,
        "status": "unsupported_workflow",
        "workflow": "unsupported",
        "allowed_workflows": ["bracket", "mounting_plate", "center_hole_flange", "center_hole_plate", "end_cap", "mounting_block", "shaft", "sheet_metal_base_flange", "weldment_frame", "static_simulation", "washer", "sleeve", "slotted_array_plate", "bom_assembly", "atomic_model"],
        "operation_sequence": operation_names,
        "untrusted_operations": sorted(set(operation_names)),
        "failure_reason": (
            "Trusted production acceptance requires exactly one controlled geometry operation: "
            "create_bracket, create_mounting_plate, create_center_hole_flange, create_center_hole_plate, create_end_cap, create_mounting_block, create_shaft, create_sheet_metal_base_flange, create_weldment_frame, run_static_simulation, create_washer, create_sleeve, create_slotted_array_plate, create_bom_assembly, "
            "or an atomic_model_session metadata marker with only production atomic operations."
        ),
    }


def _trusted_mounting_plate_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the current trusted production workflow."""

    operation_names = [operation.op for operation in plan.operations]
    mounting_plate_count = operation_names.count("create_mounting_plate")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_MOUNTING_PLATE_OPERATIONS)
    ok = mounting_plate_count == 1 and not untrusted_operations
    status = "controlled_mounting_plate" if ok else "unsupported_workflow"
    failure_reason = None
    if mounting_plate_count != 1:
        failure_reason = "Trusted production acceptance requires exactly one create_mounting_plate operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted production acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    return {
        "ok": ok,
        "status": status,
        "workflow": "mounting_plate",
        "mounting_plate_operation_count": mounting_plate_count,
        "allowed_operations": sorted(TRUSTED_MOUNTING_PLATE_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_bracket_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled bracket workflow."""

    operation_names = [operation.op for operation in plan.operations]
    bracket_count = operation_names.count("create_bracket")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_BRACKET_OPERATIONS)
    ok = bracket_count == 1 and not untrusted_operations
    status = "controlled_bracket" if ok else "unsupported_workflow"
    failure_reason = None
    if bracket_count != 1:
        failure_reason = "Trusted bracket acceptance requires exactly one create_bracket operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted bracket acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    return {
        "ok": ok,
        "status": status,
        "workflow": "bracket",
        "bracket_operation_count": bracket_count,
        "allowed_operations": sorted(TRUSTED_BRACKET_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_center_hole_flange_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled center-hole flange workflow."""

    operation_names = [operation.op for operation in plan.operations]
    flange_count = operation_names.count("create_center_hole_flange")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_CENTER_HOLE_FLANGE_OPERATIONS)
    ok = flange_count == 1 and not untrusted_operations
    status = "controlled_center_hole_flange" if ok else "unsupported_workflow"
    failure_reason = None
    if flange_count != 1:
        failure_reason = "Trusted flange acceptance requires exactly one create_center_hole_flange operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted flange acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    return {
        "ok": ok,
        "status": status,
        "workflow": "center_hole_flange",
        "center_hole_flange_operation_count": flange_count,
        "allowed_operations": sorted(TRUSTED_CENTER_HOLE_FLANGE_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_center_hole_plate_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled center-hole plate workflow."""

    operation_names = [operation.op for operation in plan.operations]
    plate_count = operation_names.count("create_center_hole_plate")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_CENTER_HOLE_PLATE_OPERATIONS)
    ok = plate_count == 1 and not untrusted_operations
    status = "controlled_center_hole_plate" if ok else "unsupported_workflow"
    failure_reason = None
    if plate_count != 1:
        failure_reason = "Trusted center-hole plate acceptance requires exactly one create_center_hole_plate operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted center-hole plate acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    return {
        "ok": ok,
        "status": status,
        "workflow": "center_hole_plate",
        "center_hole_plate_operation_count": plate_count,
        "allowed_operations": sorted(TRUSTED_CENTER_HOLE_PLATE_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_slotted_array_plate_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled slotted-array plate workflow."""

    operation_names = [operation.op for operation in plan.operations]
    plate_count = operation_names.count("create_slotted_array_plate")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_SLOTTED_ARRAY_PLATE_OPERATIONS)
    ok = plate_count == 1 and not untrusted_operations
    status = "controlled_slotted_array_plate" if ok else "unsupported_workflow"
    failure_reason = None
    if plate_count != 1:
        failure_reason = "Trusted slotted-array plate acceptance requires exactly one create_slotted_array_plate operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted slotted-array plate acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    return {
        "ok": ok,
        "status": status,
        "workflow": "slotted_array_plate",
        "slotted_array_plate_operation_count": plate_count,
        "allowed_operations": sorted(TRUSTED_SLOTTED_ARRAY_PLATE_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_washer_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled washer workflow."""

    operation_names = [operation.op for operation in plan.operations]
    washer_count = operation_names.count("create_washer")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_WASHER_OPERATIONS)
    ok = washer_count == 1 and not untrusted_operations
    status = "controlled_washer" if ok else "unsupported_workflow"
    failure_reason = None
    if washer_count != 1:
        failure_reason = "Trusted washer acceptance requires exactly one create_washer operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted washer acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    return {
        "ok": ok,
        "status": status,
        "workflow": "washer",
        "washer_operation_count": washer_count,
        "allowed_operations": sorted(TRUSTED_WASHER_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_end_cap_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled end-cap workflow."""

    operation_names = [operation.op for operation in plan.operations]
    end_cap_count = operation_names.count("create_end_cap")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_END_CAP_OPERATIONS)
    ok = end_cap_count == 1 and not untrusted_operations
    status = "controlled_end_cap" if ok else "unsupported_workflow"
    failure_reason = None
    if end_cap_count != 1:
        failure_reason = "Trusted end-cap acceptance requires exactly one create_end_cap operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted end-cap acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    return {
        "ok": ok,
        "status": status,
        "workflow": "end_cap",
        "end_cap_operation_count": end_cap_count,
        "allowed_operations": sorted(TRUSTED_END_CAP_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_mounting_block_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled mounting-block workflow."""

    operation_names = [operation.op for operation in plan.operations]
    mounting_block_count = operation_names.count("create_mounting_block")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_MOUNTING_BLOCK_OPERATIONS)
    ok = mounting_block_count == 1 and not untrusted_operations
    status = "controlled_mounting_block" if ok else "unsupported_workflow"
    failure_reason = None
    if mounting_block_count != 1:
        failure_reason = "Trusted mounting block acceptance requires exactly one create_mounting_block operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted mounting block acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    return {
        "ok": ok,
        "status": status,
        "workflow": "mounting_block",
        "mounting_block_operation_count": mounting_block_count,
        "allowed_operations": sorted(TRUSTED_MOUNTING_BLOCK_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_shaft_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled shaft workflow."""

    operation_names = [operation.op for operation in plan.operations]
    shaft_count = operation_names.count("create_shaft")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_SHAFT_OPERATIONS)
    ok = shaft_count == 1 and not untrusted_operations
    status = "controlled_shaft" if ok else "unsupported_workflow"
    failure_reason = None
    if shaft_count != 1:
        failure_reason = "Trusted shaft acceptance requires exactly one create_shaft operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted shaft acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    return {
        "ok": ok,
        "status": status,
        "workflow": "shaft",
        "shaft_operation_count": shaft_count,
        "allowed_operations": sorted(TRUSTED_SHAFT_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_sheet_metal_base_flange_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled sheet-metal base-flange workflow."""

    operation_names = [operation.op for operation in plan.operations]
    sheet_metal_count = operation_names.count("create_sheet_metal_base_flange")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_SHEET_METAL_BASE_FLANGE_OPERATIONS)
    params = sheet_metal_base_flange_parameters_from_plan(plan)
    dxf_requested = "dxf" in _combined_export_formats(plan)
    ok = sheet_metal_count == 1 and not untrusted_operations and params is not None and dxf_requested
    status = "controlled_sheet_metal_base_flange" if ok else "unsupported_workflow"
    failure_reason = None
    if sheet_metal_count != 1:
        failure_reason = "Trusted sheet-metal acceptance requires exactly one create_sheet_metal_base_flange operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted sheet-metal acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    elif params is None:
        failure_reason = "Trusted sheet-metal acceptance requires valid base-flange parameters."
    elif not dxf_requested:
        failure_reason = "Trusted sheet-metal acceptance requires dxf in output_formats for flat-pattern export."
    return {
        "ok": ok,
        "status": status,
        "workflow": "sheet_metal_base_flange",
        "sheet_metal_operation_count": sheet_metal_count,
        "flat_pattern_dxf_requested": dxf_requested,
        "allowed_operations": sorted(TRUSTED_SHEET_METAL_BASE_FLANGE_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_weldment_frame_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled weldment-frame workflow."""

    operation_names = [operation.op for operation in plan.operations]
    weldment_count = operation_names.count("create_weldment_frame")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_WELDMENT_FRAME_OPERATIONS)
    params = weldment_frame_parameters_from_plan(plan)
    csv_requested = "csv" in _combined_export_formats(plan)
    ok = weldment_count == 1 and not untrusted_operations and params is not None and csv_requested
    status = "controlled_weldment_frame" if ok else "unsupported_workflow"
    failure_reason = None
    if weldment_count != 1:
        failure_reason = "Trusted weldment acceptance requires exactly one create_weldment_frame operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted weldment acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    elif params is None:
        failure_reason = "Trusted weldment acceptance requires valid frame/profile/cut-list parameters."
    elif not csv_requested:
        failure_reason = "Trusted weldment acceptance requires csv in output_formats for cut-list export."
    return {
        "ok": ok,
        "status": status,
        "workflow": "weldment_frame",
        "weldment_operation_count": weldment_count,
        "cut_list_csv_requested": csv_requested,
        "allowed_operations": sorted(TRUSTED_WELDMENT_FRAME_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_static_simulation_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled static simulation workflow."""

    operation_names = [operation.op for operation in plan.operations]
    simulation_count = operation_names.count("run_static_simulation")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_STATIC_SIMULATION_OPERATIONS)
    params = static_simulation_parameters_from_plan(plan)
    csv_requested = "csv" in _combined_export_formats(plan)
    ok = simulation_count == 1 and not untrusted_operations and params is not None and csv_requested
    status = "controlled_static_simulation" if ok else "unsupported_workflow"
    failure_reason = None
    if simulation_count != 1:
        failure_reason = "Trusted simulation acceptance requires exactly one run_static_simulation operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted simulation acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    elif params is None:
        failure_reason = "Trusted simulation acceptance requires valid cantilever static-study parameters."
    elif not csv_requested:
        failure_reason = "Trusted simulation acceptance requires csv in output_formats for simulation report export."
    return {
        "ok": ok,
        "status": status,
        "workflow": "static_simulation",
        "simulation_operation_count": simulation_count,
        "simulation_report_csv_requested": csv_requested,
        "allowed_operations": sorted(TRUSTED_STATIC_SIMULATION_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_sleeve_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled sleeve workflow."""

    operation_names = [operation.op for operation in plan.operations]
    sleeve_count = operation_names.count("create_sleeve")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_SLEEVE_OPERATIONS)
    ok = sleeve_count == 1 and not untrusted_operations
    status = "controlled_sleeve" if ok else "unsupported_workflow"
    failure_reason = None
    if sleeve_count != 1:
        failure_reason = "Trusted sleeve acceptance requires exactly one create_sleeve operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted sleeve acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    return {
        "ok": ok,
        "status": status,
        "workflow": "sleeve",
        "sleeve_operation_count": sleeve_count,
        "allowed_operations": sorted(TRUSTED_SLEEVE_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_bom_assembly_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to the controlled assembly+BOM workflow."""

    operation_names = [operation.op for operation in plan.operations]
    assembly_count = operation_names.count("create_bom_assembly")
    untrusted_operations = sorted(set(operation_names) - TRUSTED_BOM_ASSEMBLY_OPERATIONS)
    params = bom_assembly_parameters_from_plan(plan)
    component_count = len(params["components"]) if params else 0
    ok = assembly_count == 1 and not untrusted_operations and component_count >= 2
    status = "controlled_bom_assembly" if ok else "unsupported_workflow"
    failure_reason = None
    if assembly_count != 1:
        failure_reason = "Trusted assembly acceptance requires exactly one create_bom_assembly operation."
    elif untrusted_operations:
        failure_reason = (
            "Trusted assembly acceptance does not allow extra freeform modeling operations: "
            + ", ".join(untrusted_operations)
        )
    elif component_count < 2:
        failure_reason = "Trusted assembly acceptance requires at least two components."
    return {
        "ok": ok,
        "status": status,
        "workflow": "bom_assembly",
        "assembly_operation_count": assembly_count,
        "component_definition_count": component_count,
        "allowed_operations": sorted(TRUSTED_BOM_ASSEMBLY_OPERATIONS),
        "operation_sequence": operation_names,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _trusted_atomic_workflow_result(plan: ModelPlan) -> dict[str, Any]:
    """Return whether the plan belongs to a staged production atomic session."""

    operation_names = [operation.op for operation in plan.operations]
    marker = str(plan.metadata.get("solidworks_mcp_workflow") or "")
    atomic_session_id = plan.metadata.get("atomic_session_id")
    atomic_operation_count = plan.metadata.get("atomic_operation_count")
    atomic_feature_graph = plan.metadata.get("atomic_feature_graph")
    graph_node_count = (
        atomic_feature_graph.get("node_count")
        if isinstance(atomic_feature_graph, dict)
        else None
    )
    graph_replay = _validate_atomic_feature_graph_replay(plan)
    untrusted_operations = sorted(set(operation_names) - TRUSTED_ATOMIC_OPERATIONS)
    geometry_operation_count = sum(
        1
        for operation_name in operation_names
        if operation_name not in {"assign_material", "set_custom_properties", "make_drawing"}
    )
    has_session_evidence = (
        isinstance(atomic_session_id, str)
        and atomic_session_id.startswith("atomic_")
        and atomic_operation_count == len(plan.operations)
        and isinstance(graph_node_count, int)
        and graph_node_count >= 6
        and graph_replay["ok"]
    )
    ok = (
        marker == "atomic_model_session"
        and has_session_evidence
        and not untrusted_operations
        and geometry_operation_count > 0
    )
    failure_reason = None
    if marker != "atomic_model_session":
        failure_reason = "Atomic production acceptance requires metadata.solidworks_mcp_workflow=atomic_model_session."
    elif not has_session_evidence:
        failure_reason = graph_replay.get("failure_reason") or (
            "Atomic production acceptance requires persisted session feature graph evidence."
        )
    elif untrusted_operations:
        failure_reason = "Atomic production acceptance does not allow operations: " + ", ".join(untrusted_operations)
    elif geometry_operation_count <= 0:
        failure_reason = "Atomic production acceptance requires at least one geometry operation."
    return {
        "ok": ok,
        "status": "controlled_atomic_model" if ok else "unsupported_workflow",
        "workflow": "atomic_model",
        "atomic_session_id": atomic_session_id,
        "atomic_operation_count": atomic_operation_count,
        "feature_graph_node_count": graph_node_count,
        "feature_graph_replay": graph_replay,
        "allowed_operations": sorted(TRUSTED_ATOMIC_OPERATIONS),
        "operation_sequence": operation_names,
        "geometry_operation_count": geometry_operation_count,
        "untrusted_operations": untrusted_operations,
        "failure_reason": failure_reason,
    }


def _validate_atomic_feature_graph_replay(plan: ModelPlan) -> dict[str, Any]:
    """Replay atomic operations through the named feature graph validator."""

    graph = FeatureGraph()
    try:
        for operation in plan.operations:
            graph.validate_and_record(operation)
    except PlanValidationError as exc:
        return {
            "ok": False,
            "failure_reason": f"Atomic feature graph replay failed before execution: {exc}",
            "feature_graph": graph.to_dict(),
        }
    return {"ok": True, "failure_reason": None, "feature_graph": graph.to_dict()}


def _material_result_matches(material_result: dict[str, Any], required_material: str) -> bool:
    """Return whether material diagnostics prove the requested material is active."""

    current = str(material_result.get("current_material") or "").strip().lower()
    requested = required_material.strip().lower()
    effective = str(material_result.get("effective_material") or "").strip().lower()
    material_names = [requested]
    if effective and effective != requested:
        material_names.append(effective)
    for material_name in material_names:
        if current == material_name or current.endswith(f"\\{material_name}") or current.endswith(f"/{material_name}"):
            return True
    return False


def _custom_property_result_matches(
    custom_property_result: dict[str, Any],
    required_properties: dict[str, str],
) -> bool:
    """Return whether custom-property diagnostics prove requested metadata is active."""

    current = custom_property_result.get("current_properties")
    if not isinstance(current, dict):
        return False
    return all(str(current.get(key) or "") == value for key, value in required_properties.items())


def _positive_number(value: Any) -> bool:
    """Return whether a diagnostic value is a positive finite number."""

    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _validate_pdf_semantic_content(plan: ModelPlan, pdf_checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify that a real drawing PDF contains the MVP's semantic manufacturing text."""

    if not pdf_checks:
        return {
            "status": "missing_pdf",
            "ok": False,
            "failure_reason": "No PDF output was available for semantic content validation.",
        }
    check = pdf_checks[0]
    if check.get("status") == "mock_placeholder":
        return {
            "status": "mock_placeholder",
            "ok": True,
            "path": check.get("path"),
            "failure_reason": None,
        }
    if check.get("status") != "pdf_readable":
        return {
            "status": "pdf_not_readable",
            "ok": False,
            "path": check.get("path"),
            "failure_reason": f"PDF readability status was {check.get('status')}.",
        }
    path = Path(str(check.get("path")))
    text_result = _extract_pdf_text(path)
    requirements = _pdf_semantic_requirements_for_plan(plan)
    normalized_text = _normalize_pdf_text(str(text_result.get("text") or ""))
    matches = {
        item["id"]: _pdf_requirement_matches(normalized_text, item["tokens"])
        for item in requirements
    }
    missing = sorted(requirement_id for requirement_id, matched in matches.items() if not matched)
    return {
        "status": "pdf_semantic_content_verified" if not missing else "pdf_semantic_content_missing",
        "ok": not missing,
        "path": check.get("path"),
        "text_extract_status": text_result.get("status"),
        "text_length": len(str(text_result.get("text") or "")),
        "requirements": requirements,
        "matches": matches,
        "missing": missing,
        "sample_text": normalized_text[:800],
        "failure_reason": None if not missing else "PDF text did not contain all required manufacturing callouts.",
    }


def _pdf_semantic_requirements_for_plan(plan: ModelPlan) -> list[dict[str, Any]]:
    """Build minimum trusted PDF text requirements for the controlled mounting plate."""

    params = mounting_plate_parameters_from_plan(plan)
    if params is None:
        return []
    thread_spec = _thread_spec_from_plan(plan) or "M6"
    requirements = [
        {"id": "thread_spec", "tokens": [thread_spec]},
        {"id": "length_dimension", "tokens": [_dimension_text_token(params["length"])]},
        {"id": "width_dimension", "tokens": [_dimension_text_token(params["width"])]},
        {"id": "thickness_dimension", "tokens": [_dimension_text_token(params["thickness"])]},
        {"id": "corner_radius", "tokens": [f"R{_dimension_text_token(params['corner_radius'])}"]},
        {"id": "hole_edge_offset", "tokens": [_dimension_text_token(params["edge_offset"])]},
        {"id": "hole_or_thread_callout", "tokens": ["THRU", "THROUGH", "贯穿", "通孔", "螺纹", "TAP", "TAPPED"]},
    ]
    for key, value in _required_custom_properties_from_plan(plan).items():
        if value:
            requirements.append({"id": f"custom_property_{key}", "tokens": [value]})
    return requirements


def _thread_spec_from_plan(plan: ModelPlan) -> str | None:
    """Return the mounting-plate thread spec from a plan."""

    for operation in plan.operations:
        if operation.op == "create_mounting_plate":
            thread_spec = operation.parameters.get("thread_spec")
            return str(thread_spec).upper() if thread_spec else None
    return None


def _dimension_text_token(value: Any) -> str:
    """Format a plan dimension the way it usually appears in a drawing PDF."""

    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _pdf_requirement_matches(normalized_text: str, tokens: list[str]) -> bool:
    """Return whether any candidate token appears in normalized PDF text."""

    for token in tokens:
        normalized = _normalize_pdf_text(str(token))
        if normalized and normalized in normalized_text:
            return True
    return False


def _normalize_pdf_text(value: str) -> str:
    """Normalize extracted PDF text for language- and font-tolerant matching."""

    return re.sub(r"\s+", " ", value).upper()


def _inspect_pdf(path: Path) -> dict[str, Any]:
    """Inspect a PDF enough to catch corrupt or empty drawing exports."""

    result: dict[str, Any] = {
        "id": "pdf",
        "path": path_to_string(path),
        "status": "not_checked",
        "ok": False,
        "page_count": 0,
    }
    if not path.exists() or not path.is_file():
        result["status"] = "missing"
        return result
    data = path.read_bytes()
    result["size_bytes"] = len(data)
    if data.startswith(b"Mock PDF export"):
        result["status"] = "mock_placeholder"
        result["ok"] = True
        return result
    has_header = data.startswith(b"%PDF-")
    has_eof = b"%%EOF" in data[-2048:]
    page_count = len(re.findall(rb"/Type\s*/Page\b", data))
    result.update(
        {
            "has_header": has_header,
            "has_eof": has_eof,
            "page_count": page_count,
        }
    )
    if has_header and has_eof and page_count >= 1:
        result["status"] = "pdf_readable"
        result["ok"] = True
    else:
        result["status"] = "pdf_invalid"
    return result


def _extract_pdf_text(path: Path) -> dict[str, Any]:
    """Extract simple text operands from a PDF without optional dependencies."""

    try:
        data = path.read_bytes()
        decoded_streams: list[bytes] = []
        stream_texts: list[str] = []
        stream_count = 0
        decoded_stream_count = 0
        for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, flags=re.DOTALL):
            stream_count += 1
            raw_stream = match.group(1)
            header = data[max(0, match.start() - 500):match.start()]
            decoded = _decode_pdf_stream(raw_stream, header)
            if decoded is not None:
                decoded_stream_count += 1
                decoded_streams.append(decoded)
        cmap = _extract_pdf_to_unicode_map(decoded_streams)
        for decoded in decoded_streams:
            stream_texts.append(_extract_pdf_text_operands(decoded, cmap))
        direct_text = _extract_pdf_text_operands(data, cmap)
        text = "\n".join(item for item in [direct_text, *stream_texts] if item)
        return {
            "status": "text_extracted" if text else "no_text_found",
            "ok": bool(text),
            "stream_count": stream_count,
            "decoded_stream_count": decoded_stream_count,
            "to_unicode_map_size": len(cmap),
            "text": text,
        }
    except Exception as exc:
        return {"status": "text_extract_failed", "ok": False, "text": "", "failure_reason": str(exc)}


def _decode_pdf_stream(raw_stream: bytes, header: bytes) -> bytes | None:
    """Decode one PDF content stream when its filter is supported."""

    if b"/FlateDecode" in header or b"/Fl" in header:
        try:
            return zlib.decompress(raw_stream)
        except zlib.error:
            return None
    if b"/Filter" not in header:
        return raw_stream
    return None


def _extract_pdf_text_operands(data: bytes, cmap: dict[int, str] | None = None) -> str:
    """Extract PDF literal and hex strings that are used as text operands."""

    texts: list[str] = []
    for match in re.finditer(rb"\((?:\\.|[^\\()])*\)\s*(?:Tj|'|\"|\])", data, flags=re.DOTALL):
        literal = match.group(0)
        literal = literal[:literal.rfind(b")") + 1]
        texts.append(_decode_pdf_literal_string(literal[1:-1], cmap))
    for match in re.finditer(rb"<([0-9A-Fa-f\s]+)>\s*Tj", data):
        decoded = _decode_pdf_hex_string(match.group(1), cmap)
        if decoded:
            texts.append(decoded)
    for array_match in re.finditer(rb"\[(.*?)\]\s*TJ", data, flags=re.DOTALL):
        array = array_match.group(1)
        for literal in re.findall(rb"\((?:\\.|[^\\()])*\)", array, flags=re.DOTALL):
            texts.append(_decode_pdf_literal_string(literal[1:-1], cmap))
        for hex_value in re.findall(rb"<([0-9A-Fa-f\s]+)>", array):
            decoded = _decode_pdf_hex_string(hex_value, cmap)
            if decoded:
                texts.append(decoded)
    return " ".join(text for text in texts if text)


def _decode_pdf_literal_string(value: bytes, cmap: dict[int, str] | None = None) -> str:
    """Decode a PDF literal string with common escape sequences."""

    output = bytearray()
    index = 0
    while index < len(value):
        char = value[index]
        if char != 0x5C:
            output.append(char)
            index += 1
            continue
        index += 1
        if index >= len(value):
            break
        escaped = value[index]
        index += 1
        mapping = {
            ord("n"): 0x0A,
            ord("r"): 0x0D,
            ord("t"): 0x09,
            ord("b"): 0x08,
            ord("f"): 0x0C,
            ord("("): ord("("),
            ord(")"): ord(")"),
            ord("\\"): ord("\\"),
        }
        if escaped in mapping:
            output.append(mapping[escaped])
            continue
        if 48 <= escaped <= 55:
            octal_digits = bytes([escaped])
            while index < len(value) and len(octal_digits) < 3 and 48 <= value[index] <= 55:
                octal_digits += bytes([value[index]])
                index += 1
            output.append(int(octal_digits, 8) & 0xFF)
            continue
        output.append(escaped)
    return _decode_pdf_text_bytes(bytes(output), cmap)


def _decode_pdf_hex_string(value: bytes, cmap: dict[int, str] | None = None) -> str:
    """Decode a PDF hex string as UTF-16BE or latin text."""

    hex_text = re.sub(rb"\s+", b"", value)
    if len(hex_text) % 2:
        hex_text += b"0"
    try:
        return _decode_pdf_text_bytes(bytes.fromhex(hex_text.decode("ascii")), cmap)
    except ValueError:
        return ""


def _decode_pdf_text_bytes(value: bytes, cmap: dict[int, str] | None = None) -> str:
    """Decode text bytes extracted from PDF content streams."""

    if not value:
        return ""
    if cmap:
        mapped = _decode_pdf_text_with_cmap(value, cmap)
        if _use_pdf_cmap_decoding(mapped):
            return mapped
    if value.startswith(b"\xfe\xff"):
        return value[2:].decode("utf-16-be", errors="ignore")
    if value.startswith(b"\xff\xfe"):
        return value[2:].decode("utf-16-le", errors="ignore")
    decoded = value.decode("utf-8", errors="ignore")
    if decoded.strip():
        return decoded
    return value.decode("latin-1", errors="ignore")


def _extract_pdf_to_unicode_map(decoded_streams: list[bytes]) -> dict[int, str]:
    """Parse ToUnicode CMap streams from decoded PDF streams."""

    cmap: dict[int, str] = {}
    for stream in decoded_streams:
        if b"beginbfchar" not in stream and b"beginbfrange" not in stream:
            continue
        for block in re.findall(rb"beginbfchar(.*?)endbfchar", stream, flags=re.DOTALL):
            for source, target in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
                source_code = _pdf_hex_to_int(source)
                target_text = _pdf_hex_to_unicode(target)
                if source_code is not None and target_text:
                    cmap[source_code] = target_text
        for block in re.findall(rb"beginbfrange(.*?)endbfrange", stream, flags=re.DOTALL):
            _parse_pdf_bfrange_block(block, cmap)
    return cmap


def _parse_pdf_bfrange_block(block: bytes, cmap: dict[int, str]) -> None:
    """Parse one CMap bfrange block into a mutable map."""

    for source_start, source_end, target_start in re.findall(
        rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
        block,
    ):
        start = _pdf_hex_to_int(source_start)
        end = _pdf_hex_to_int(source_end)
        target = _pdf_hex_to_int(target_start)
        if start is None or end is None or target is None:
            continue
        for offset, source_code in enumerate(range(start, end + 1)):
            cmap[source_code] = chr(target + offset)
    for source_start, source_end, targets in re.findall(
        rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]",
        block,
        flags=re.DOTALL,
    ):
        start = _pdf_hex_to_int(source_start)
        end = _pdf_hex_to_int(source_end)
        if start is None or end is None:
            continue
        target_values = re.findall(rb"<([0-9A-Fa-f]+)>", targets)
        for source_code, target_hex in zip(range(start, end + 1), target_values, strict=False):
            target_text = _pdf_hex_to_unicode(target_hex)
            if target_text:
                cmap[source_code] = target_text


def _decode_pdf_text_with_cmap(value: bytes, cmap: dict[int, str]) -> str:
    """Decode PDF text bytes by applying a ToUnicode CMap."""

    output: list[str] = []
    index = 0
    while index < len(value):
        two_byte = int.from_bytes(value[index:index + 2], "big") if index + 1 < len(value) else None
        if two_byte is not None and two_byte in cmap:
            output.append(cmap[two_byte])
            index += 2
            continue
        one_byte = value[index]
        output.append(cmap.get(one_byte, chr(one_byte)))
        index += 1
    return "".join(output)


def _use_pdf_cmap_decoding(value: str) -> bool:
    """Return whether CMap decoding produced meaningful printable text."""

    if not value:
        return False
    printable = sum(1 for char in value if char.isprintable() and not char.isspace())
    controls = sum(1 for char in value if ord(char) < 32 and char not in "\r\n\t")
    return printable > 0 and controls <= max(2, printable // 10)


def _pdf_hex_to_int(value: bytes) -> int | None:
    """Convert a PDF hex token to an integer code."""

    try:
        return int(value.decode("ascii"), 16)
    except ValueError:
        return None


def _pdf_hex_to_unicode(value: bytes) -> str:
    """Convert a CMap target hex value to Unicode text."""

    hex_text = value.decode("ascii")
    if len(hex_text) % 4 == 0:
        try:
            return bytes.fromhex(hex_text).decode("utf-16-be", errors="ignore")
        except ValueError:
            pass
    try:
        return chr(int(hex_text, 16))
    except ValueError:
        return ""


def _inspect_preview_artifact(preview_id: str, path: Path) -> dict[str, Any]:
    """Inspect one preview artifact, with strict PNG checks for real previews."""

    result: dict[str, Any] = {
        "id": preview_id,
        "path": path_to_string(path),
        "status": "not_checked",
        "ok": False,
    }
    if not path.exists() or not path.is_file():
        result["status"] = "missing"
        return result

    suffix = path.suffix.lower()
    if suffix == ".png":
        result.update(_inspect_png(path))
        return result

    if suffix == ".txt":
        size_bytes = path.stat().st_size
        result.update(
            {
                "status": "mock_placeholder" if size_bytes > 0 else "empty_placeholder",
                "ok": size_bytes > 0,
                "size_bytes": size_bytes,
            }
        )
        return result

    size_bytes = path.stat().st_size
    result.update(
        {
            "status": "unsupported_preview_type",
            "ok": False,
            "size_bytes": size_bytes,
        }
    )
    return result


def _inspect_png(path: Path) -> dict[str, Any]:
    """Inspect PNG structure and detect all-one-color preview captures."""

    data = path.read_bytes()
    details: dict[str, Any] = {
        "size_bytes": len(data),
        "png_signature": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "status": "png_invalid",
        "ok": False,
    }
    if not details["png_signature"]:
        return details

    try:
        png = _parse_png(data)
        details.update(png)
        nonblank = _png_has_pixel_variation(png)
        details.pop("idat_data", None)
        details["nonblank"] = nonblank
        details["status"] = "png_rendered" if nonblank else "png_blank"
        details["ok"] = bool(nonblank and png.get("width", 0) > 0 and png.get("height", 0) > 0)
    except Exception as exc:
        details["failure_reason"] = str(exc)
    return details


def _parse_png(data: bytes) -> dict[str, Any]:
    """Parse enough PNG chunks to support content-quality checks."""

    offset = 8
    width = 0
    height = 0
    bit_depth = 0
    color_type = 0
    idat_chunks: list[bytes] = []
    saw_iend = False
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(data):
            raise ValueError(f"PNG chunk {chunk_type!r} exceeds file length")
        chunk_data = data[chunk_start:chunk_end]
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            saw_iend = True
            break
        offset = chunk_end + 4
    return {
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "idat_chunk_count": len(idat_chunks),
        "has_iend": saw_iend,
        "idat_data": b"".join(idat_chunks),
    }


def _png_has_pixel_variation(png: dict[str, Any]) -> bool:
    """Return whether a PNG has more than one sampled reconstructed pixel value."""

    width = int(png.get("width") or 0)
    height = int(png.get("height") or 0)
    bit_depth = int(png.get("bit_depth") or 0)
    color_type = int(png.get("color_type") or 0)
    idat_data = png.get("idat_data") or b""
    if width <= 0 or height <= 0 or not idat_data:
        return False
    if bit_depth != 8 or color_type not in {0, 2, 3, 4, 6}:
        return True

    samples_per_pixel = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_size = width * samples_per_pixel
    raw = zlib.decompress(idat_data)
    expected_min = (row_size + 1) * height
    if len(raw) < expected_min:
        raise ValueError("PNG IDAT data is shorter than expected for IHDR dimensions")

    previous = bytearray(row_size)
    first_pixel: bytes | None = None
    max_rows_to_check = min(height, 200)
    row_stride = max(1, height // max_rows_to_check)
    for row_index in range(height):
        start = row_index * (row_size + 1)
        filter_type = raw[start]
        scanline = bytearray(raw[start + 1:start + 1 + row_size])
        reconstructed = _unfilter_png_scanline(filter_type, scanline, previous, samples_per_pixel)
        previous = reconstructed
        if row_index % row_stride != 0 and row_index != height - 1:
            continue
        for pixel_offset in range(0, row_size, samples_per_pixel):
            pixel = bytes(reconstructed[pixel_offset:pixel_offset + samples_per_pixel])
            if first_pixel is None:
                first_pixel = pixel
            elif pixel != first_pixel:
                return True
    return False


def _unfilter_png_scanline(
    filter_type: int,
    scanline: bytearray,
    previous: bytearray,
    bytes_per_pixel: int,
) -> bytearray:
    """Reverse one PNG filter scanline."""

    result = bytearray(scanline)
    for index, value in enumerate(scanline):
        left = result[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        up = previous[index] if index < len(previous) else 0
        upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel and index < len(previous) else 0
        if filter_type == 0:
            result[index] = value
        elif filter_type == 1:
            result[index] = (value + left) & 0xFF
        elif filter_type == 2:
            result[index] = (value + up) & 0xFF
        elif filter_type == 3:
            result[index] = (value + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            result[index] = (value + _paeth_predictor(left, up, upper_left)) & 0xFF
        else:
            raise ValueError(f"Unsupported PNG filter type {filter_type}")
    return result


def _paeth_predictor(left: int, up: int, upper_left: int) -> int:
    """Return the PNG Paeth predictor."""

    prediction = left + up - upper_left
    distance_left = abs(prediction - left)
    distance_up = abs(prediction - up)
    distance_upper_left = abs(prediction - upper_left)
    if distance_left <= distance_up and distance_left <= distance_upper_left:
        return left
    if distance_up <= distance_upper_left:
        return up
    return upper_left


def _finalize_report(
    report: ExecutionReport,
    context: DebugRunContext,
    recorder: EventRecorder,
) -> ExecutionReport:
    """Persist report and artifact index while preserving execution status."""

    recorder.event(
        "report.write",
        "started",
        {"report_file": context.report_file, "artifacts_file": context.artifacts_file},
    )
    try:
        report_file = write_json_file(context.report_file, report.to_dict())
        enriched_report = _copy_report_with_paths(report, context, report_file)
        write_json_file(context.report_file, enriched_report.to_dict())
        artifacts_file = write_artifacts_index(context, enriched_report.to_dict())
        enriched_report = _copy_report_with_paths(enriched_report, context, report_file, artifacts_file)
        write_json_file(context.report_file, enriched_report.to_dict())
        delivery_manifest_file = write_delivery_manifest(context, enriched_report.to_dict())
        enriched_report = _copy_report_with_paths(
            enriched_report,
            context,
            report_file,
            artifacts_file,
            delivery_manifest_file,
        )
        write_json_file(context.report_file, enriched_report.to_dict())
        recorder.event(
            "report.write",
            "completed",
            {
                "report_file": report_file,
                "artifacts_file": artifacts_file,
                "delivery_manifest_file": delivery_manifest_file,
            },
        )
        recorder.event(
            "plan.execution",
            "completed" if report.ok else "failed",
            {
                "ok": enriched_report.ok,
                "report_file": enriched_report.report_file,
                "artifacts_file": enriched_report.artifacts_file,
                "delivery_manifest_file": enriched_report.delivery_manifest_file,
                "failure_class": enriched_report.failure_class,
                "output_count": len(enriched_report.output_files),
                "preview_count": len(enriched_report.preview_files),
            },
        )
        artifacts_file = write_artifacts_index(context, enriched_report.to_dict())
        return enriched_report
    except Exception as exc:
        diagnostics = dict(report.diagnostics)
        diagnostics["report_write_error"] = str(exc)
        failed_report = _copy_report_with_paths(
            ExecutionReport(
                ok=report.ok,
                adapter=report.adapter,
                message=report.message,
                plan=report.plan,
                plan_name=report.plan_name,
                run_id=report.run_id,
                run_dir=report.run_dir,
                events_file=report.events_file,
                environment_file=report.environment_file,
                artifacts_file=report.artifacts_file,
                delivery_manifest_file=report.delivery_manifest_file,
                step_results=report.step_results,
                output_files=report.output_files,
                preview_files=report.preview_files,
                feature_summary=report.feature_summary,
                active_document=report.active_document,
                error_step=report.error_step,
                failure_class=report.failure_class,
                repro_command=report.repro_command,
                diagnostics=diagnostics,
                report_file=report.report_file,
            ),
            context,
            report.report_file,
        )
        recorder.event("report.write", "failed", {"error": str(exc)})
        return failed_report


def _copy_report_with_paths(
    report: ExecutionReport,
    context: DebugRunContext,
    report_file: str | None,
    artifacts_file: str | None = None,
    delivery_manifest_file: str | None = None,
) -> ExecutionReport:
    """Return a report copy with run artifact path fields filled consistently."""

    return ExecutionReport(
        ok=report.ok,
        adapter=report.adapter,
        message=report.message,
        plan=report.plan,
        plan_name=report.plan_name,
        run_id=report.run_id or context.run_id,
        run_dir=report.run_dir or path_to_string(context.run_dir),
        events_file=report.events_file or path_to_string(context.events_file),
        environment_file=report.environment_file or path_to_string(context.environment_file),
        artifacts_file=artifacts_file or report.artifacts_file or path_to_string(context.artifacts_file),
        delivery_manifest_file=delivery_manifest_file
        or report.delivery_manifest_file
        or path_to_string(context.delivery_manifest_file),
        step_results=report.step_results,
        output_files=report.output_files,
        preview_files=report.preview_files,
        feature_summary=report.feature_summary,
        active_document=report.active_document,
        error_step=report.error_step,
        failure_class=report.failure_class,
        repro_command=report.repro_command or repro_command_for_plan(context.plan_file),
        diagnostics=report.diagnostics,
        report_file=report_file,
    )
