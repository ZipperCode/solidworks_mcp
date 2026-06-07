"""FastMCP server exposing high-level SolidWorks automation protocol surfaces."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from solidworks_mcp.adapters import create_adapter
from solidworks_mcp.capabilities import capability_catalog_json, capability_category_json
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.executor import ModelPlanExecutor
from solidworks_mcp.release_diagnostics import diagnose_release_gate_report
from solidworks_mcp.run_diagnostics import diagnose_run_collection, diagnose_run_directory
from solidworks_mcp.sessions import AtomicSessionManager


def build_executor() -> ModelPlanExecutor:
    """Build the executor from environment configuration."""

    config = SolidWorksMCPConfig.from_env()
    return ModelPlanExecutor(create_adapter(config), config)


executor = build_executor()
atomic_sessions = AtomicSessionManager(executor)
mcp = FastMCP("solidworks-mcp")


@mcp.tool()
def connect_solidworks() -> dict[str, Any]:
    """Connect to SolidWorks or report the active mock adapter.

    Use this before planning a real Windows run so the MCP client can confirm
    whether the server is using the mock adapter or SolidWorks COM.  The result
    is a small environment summary, not a modeling report, and it does not
    create a run directory.
    """

    return executor.connect()


@mcp.tool()
def validate_model_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a restricted JSON model plan before user confirmation.

    This is the schema gate for AI-generated ``ModelPlan`` payloads.  It checks
    units, export formats, required fields, and the current executable operation
    whitelist.  Schema-valid freeform operations are development capabilities,
    not trusted production workflows.  Keep planned capabilities from the
    capability catalog out of this plan until they are promoted into
    ``SUPPORTED_OPERATIONS``.
    """

    return executor.validate_plan(plan).to_dict()


@mcp.tool()
def preflight_environment(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check SolidWorks MCP runtime prerequisites before confirmed execution.

    This performs the same hard-gate checks that ``execute_model_plan`` runs
    internally: SolidWorks COM availability, template paths or discoverable
    defaults, pywin32 on Windows, and output-directory writability.  It does not
    create a part or drawing document.
    """

    return executor.preflight_environment(plan).to_dict()


@mcp.tool()
def execute_model_plan(plan: dict[str, Any], confirmed: bool = False) -> dict[str, Any]:
    """Execute a confirmed model plan in an isolated document transaction.

    Call this only after the user has reviewed a validated plan and the client
    passes ``confirmed=true``.  Every confirmed run writes a dedicated run
    directory containing ``plan.normalized.json``, ``execution_report.json``,
    ``delivery_manifest.json``, ``events.jsonl``, ``environment.json``, and
    ``artifacts.json`` for later failure diagnosis.
    """

    return executor.execute_plan(plan, confirmed=confirmed).to_dict()


@mcp.tool()
def start_model_session(
    name: str,
    units: str = "mm",
    metadata: dict[str, Any] | None = None,
    output_formats: list[str] | None = None,
    drawing_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a staged atomic modeling session without creating CAD documents.

    The session returns a named feature graph containing built-in reference ids
    such as ``front``, ``top``, ``right``, ``x_axis``, ``y_axis`` and ``z_axis``.
    Later ``apply_model_operation`` calls may create or reference graph ids, but
    no SolidWorks document is created until ``finalize_model_session`` is called
    with ``confirmed=true``.
    """

    return atomic_sessions.start_model_session(
        name=name,
        units=units,
        metadata=metadata,
        output_formats=output_formats,
        drawing_profile=drawing_profile,
    )


@mcp.tool()
def apply_model_operation(session_id: str, operation: dict[str, Any]) -> dict[str, Any]:
    """Validate and stage one production atomic operation in a model session.

    This is the safe planning surface for sketch/extrude/cut/hole/fillet/
    chamfer/pattern/revolve/sweep/loft workflows.  It validates required fields
    and named feature-graph references before the operation can be finalized.
    """

    return atomic_sessions.apply_model_operation(session_id, operation)


@mcp.tool()
def finalize_model_session(session_id: str, confirmed: bool = False) -> dict[str, Any]:
    """Execute a staged atomic session through the normal confirmed run path.

    ``confirmed=false`` preserves the same safety contract as
    ``execute_model_plan`` and returns a missing-confirmation report.  Confirmed
    execution writes the standard run directory, manifest, event log, artifacts
    index and production verdict.
    """

    return atomic_sessions.finalize_model_session(session_id, confirmed=confirmed)


@mcp.tool()
def abort_model_session(session_id: str) -> dict[str, Any]:
    """Discard a staged atomic modeling session without touching CAD state."""

    return atomic_sessions.abort_model_session(session_id)


@mcp.tool()
def generate_drawing(plan: dict[str, Any]) -> dict[str, Any]:
    """Generate an engineering drawing for the active model.

    Use this when a model already exists in the active adapter session and the
    client wants to retry or refresh drawing creation from the plan's
    ``DrawingProfile``.  Drawing annotation failures are reported as diagnostics
    instead of hiding the model or export status.
    """

    return executor.generate_drawing(plan).to_dict()


@mcp.tool()
def export_outputs(plan: dict[str, Any], formats: list[str] | None = None) -> dict[str, Any]:
    """Export the active model or drawing to requested formats.

    Use this to retry file exports from the active adapter session.  The optional
    ``formats`` argument overrides the plan's output format list for this call;
    unsupported formats still fail validation through the plan/export schema.
    """

    return executor.export_outputs(plan, formats=formats).to_dict()


@mcp.tool()
def inspect_active_model() -> dict[str, Any]:
    """Inspect the active model and return an AI-readable summary.

    Use this after execution, drawing, or export to collect feature summaries,
    fallback states, warnings, and annotation status.  The response is intended
    for self-review and repair planning rather than as certified CAD validation.
    """

    return executor.inspect_active_model().to_dict()


@mcp.tool()
def diagnose_run(run_dir: str, summary_only: bool = True, tail: int = 12) -> dict[str, Any]:
    """Diagnose a completed run directory without touching SolidWorks.

    Use this after ``execute_model_plan`` returns a ``run_dir`` or when a copied
    run directory needs review.  The tool reads ``execution_report.json``,
    ``artifacts.json``, ``delivery_manifest.json``, ``environment.json`` and
    ``events.jsonl`` from disk, rechecks artifact paths, and returns the same
    trusted production verdict as the CLI ``scripts/diagnose_run.py`` helper.  It does not connect to
    SolidWorks, create documents, export files, or mutate the run directory.
    """

    return diagnose_run_directory(run_dir, tail=tail, summary_only=summary_only)


@mcp.tool()
def diagnose_runs(
    root_dir: str,
    summary_only: bool = True,
    tail: int = 12,
    max_runs: int = 0,
) -> dict[str, Any]:
    """Audit completed run directories below a root without touching SolidWorks.

    This is the batch companion to ``diagnose_run``.  It recursively finds run
    directories containing ``execution_report.json``, applies the same trusted
    single-run diagnosis, and returns aggregate accepted/rejected counts plus
    issue keys for repair routing.  ``max_runs=0`` is the production default and
    means a complete unbounded scan; set a positive value only for an explicit
    exploratory sample.
    """

    return diagnose_run_collection(root_dir, tail=tail, summary_only=summary_only, max_runs=max_runs)


@mcp.tool()
def diagnose_release_gate(report_file: str, summary_only: bool = True) -> dict[str, Any]:
    """Verify an archived release_gate_report.json without touching SolidWorks.

    Use this for release handoff review after ``scripts/release_production_gate.py``
    creates a batch report.  The tool re-runs the offline batch diagnosis for
    the report's output root and checks that the archived scenario/count verdict
    still matches the current files on disk.
    """

    return diagnose_release_gate_report(report_file, summary_only=summary_only)


@mcp.tool()
def cleanup_run_documents(run_dir: str) -> dict[str, Any]:
    """Close open SolidWorks documents that belong to a completed run directory.

    This is a post-run cleanup remediation tool for real SolidWorks sessions.
    It reads completed-run artifacts, resolves candidate ``SLDPRT`` and
    ``SLDDRW`` documents through ``GetOpenDocumentByName``, and calls
    ``CloseDoc`` only after the open document path is verified inside
    ``run_dir``.  It does not create documents, export files, or close
    unrelated user files.
    """

    return executor.cleanup_run_documents(run_dir)


@mcp.resource(
    "solidworks://capabilities",
    title="SolidWorks MCP Capability Catalog",
    description="Read-only JSON catalog of available, planned, research, and blocked SolidWorks MCP protocol abilities.",
    mime_type="application/json",
)
def solidworks_capabilities() -> str:
    """Return the complete read-only protocol capability catalog as JSON."""

    return capability_catalog_json()


@mcp.resource(
    "solidworks://capabilities/{category}",
    title="SolidWorks MCP Capability Category",
    description="Read-only JSON catalog entry for a single SolidWorks MCP capability category.",
    mime_type="application/json",
)
def solidworks_capability_category(category: str) -> str:
    """Return one capability category as JSON, including an error payload for unknown names."""

    return capability_category_json(category)


@mcp.resource(
    "solidworks://preflight/environment",
    title="SolidWorks MCP Environment Preflight",
    description="Dynamic current-environment preflight result without starting a modeling transaction.",
    mime_type="application/json",
)
def solidworks_preflight_environment() -> str:
    """Return current runtime preflight diagnostics without creating documents."""

    return json.dumps(executor.preflight_environment().to_dict(), ensure_ascii=False, indent=2)


@mcp.prompt(
    name="plan_solidworks_operation",
    title="Plan SolidWorks Operation",
    description="Guide an AI client to draft a safe ModelPlan using the capability catalog.",
)
def plan_solidworks_operation(user_request: str, capability_category: str | None = None) -> str:
    """Return prompt guidance for converting a user request into a safe ModelPlan."""

    category_hint = (
        f"\nFocus capability category: {capability_category}."
        if capability_category
        else "\nUse solidworks://capabilities to inspect the full catalog before planning."
    )
    return f"""You are planning a SolidWorks MCP operation.

User request:
{user_request}
{category_hint}

Workflow:
1. Read solidworks://capabilities or solidworks://capabilities/{{category}} to separate available, planned, research, and blocked abilities.
2. For production output, draft a controlled create_mounting_plate, create_center_hole_flange, create_center_hole_plate, create_bracket, create_end_cap, create_mounting_block, create_shaft, create_washer, create_sleeve, or create_slotted_array_plate workflow only when the request matches one of the current trusted workflows; schema-valid freeform operations are non-production experiments unless the user explicitly disables trusted workflow enforcement.
3. Draft a ModelPlan only with operations currently accepted by validate_model_plan, then run preflight_environment with that candidate plan before asking for confirmed execution.
4. Treat planned, research, blocked, and schema-valid-but-untrusted capabilities as design-discussion notes only; never submit them to execute_model_plan for a trusted production claim.
5. Prefer high-level operations such as create_mounting_plate, create_center_hole_flange, create_center_hole_plate, create_bracket, create_end_cap, create_mounting_block, create_shaft, create_washer, create_sleeve, or create_slotted_array_plate when they match the request, but only declare production success when diagnose_run returns an accepted verdict; keep SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW=1, SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN=1, and SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT=1 for real SolidWorks production runs.
6. Explain any fallback risks, drawing annotation limits, preflight blockers, and export expectations before asking the user to confirm execution.
7. After confirmed execution, call diagnose_run with the returned run_dir and require production_acceptance_status=accepted, artifact_integrity_status=verified, event_log_status=verified, delivery_manifest_status=verified, and environment_status=verified before declaring success. For a directory containing multiple completed runs, call diagnose_runs with max_runs=0 and require scan_status=complete and rejected_count=0 before treating the batch as production handoff ready. For archived release gates, call diagnose_release_gate on release_gate_report.json and require status=verified.

Return a concise plan summary first, then the candidate ModelPlan JSON if it is executable today."""


def main() -> None:
    """Run the MCP server over stdio."""

    mcp.run()


if __name__ == "__main__":
    main()
