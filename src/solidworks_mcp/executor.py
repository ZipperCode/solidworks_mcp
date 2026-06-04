"""Execution orchestration for validated SolidWorks model plans."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from solidworks_mcp.adapters.base import CADAdapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.debug import (
    DebugRunContext,
    EventRecorder,
    classify_failure,
    create_debug_run_context,
    repro_command_for_plan,
    write_artifacts_index,
    write_environment_snapshot,
)
from solidworks_mcp.schemas import ExecutionReport, ModelPlan, PlanValidationError, StepResult, path_to_string, write_json_file


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
                diagnostics={"failure": str(exc)},
            )

        return ExecutionReport(
            ok=True,
            adapter=self.adapter_name,
            message="Plan is valid and ready for user confirmation.",
            plan_name=plan.name,
            feature_summary=[operation.to_dict() for operation in plan.operations],
            failure_class=None,
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

        try:
            transaction_started_at = perf_counter()
            transaction = self._adapter.begin_transaction(plan)
            active_document = transaction.get("document")
            recorder.event("adapter.transaction", "completed", transaction, started_at=transaction_started_at)

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
                    return _finalize_report(report, context, recorder)

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
            return _finalize_report(report, context, recorder)
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
            return _finalize_report(report, context, recorder)

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
        "thread_model_status": inspection.get("thread_model_status"),
        "drawing_view_status": inspection.get("drawing_view_status"),
        "drawing_annotation_status": inspection.get("drawing_annotation_status"),
        "fallbacks": inspection.get("fallbacks", []),
        "warnings": inspection.get("warnings", []),
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
        recorder.event(
            "report.write",
            "completed",
            {"report_file": report_file, "artifacts_file": artifacts_file},
        )
        recorder.event(
            "plan.execution",
            "completed" if report.ok else "failed",
            {
                "ok": enriched_report.ok,
                "report_file": enriched_report.report_file,
                "artifacts_file": enriched_report.artifacts_file,
                "failure_class": enriched_report.failure_class,
                "output_count": len(enriched_report.output_files),
                "preview_count": len(enriched_report.preview_files),
            },
        )
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
