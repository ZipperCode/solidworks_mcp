"""FastMCP server exposing high-level SolidWorks automation protocol surfaces."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import JsonValue

from solidworks_mcp.adapters import create_adapter
from solidworks_mcp.capabilities import capability_catalog_json, capability_category_json
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.executor import ModelPlanExecutor
from solidworks_mcp.release_diagnostics import diagnose_release_gate_report
from solidworks_mcp.run_diagnostics import diagnose_run_collection, diagnose_run_directory
from solidworks_mcp.sessions import AtomicSessionManager
from solidworks_mcp.tool_schema_guidance import apply_tool_schema_guidance
from solidworks_mcp.tool_inputs import DrawingProfileInput, ModelPlanInput, OperationInput


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
def validate_model_plan(plan: ModelPlanInput) -> dict[str, Any]:
    """Validate a restricted JSON model plan before user confirmation.

    This is the schema gate for AI-generated ``ModelPlan`` payloads.  It checks
    units, export formats, required fields, and the current executable operation
    whitelist.  Schema-valid freeform operations are development capabilities,
    not trusted production workflows.  Keep planned capabilities from the
    capability catalog out of this plan until they are promoted into
    ``SUPPORTED_OPERATIONS``.
    """

    return executor.validate_plan(plan.to_plan_dict()).to_dict()


@mcp.tool()
def preflight_environment(plan: ModelPlanInput | None = None) -> dict[str, Any]:
    """Check SolidWorks MCP runtime prerequisites before confirmed execution.

    This performs the same hard-gate checks that ``execute_model_plan`` runs
    internally: SolidWorks COM availability, template paths or discoverable
    defaults, pywin32 on Windows, and output-directory writability.  It does not
    create a part or drawing document.
    """

    return executor.preflight_environment(plan.to_plan_dict() if plan is not None else None).to_dict()


@mcp.tool()
def execute_model_plan(plan: ModelPlanInput, confirmed: bool = False) -> dict[str, Any]:
    """Execute a confirmed model plan in an isolated document transaction.

    Call this only after the user has reviewed a validated plan and the client
    passes ``confirmed=true``.  Every confirmed run writes a dedicated run
    directory containing ``plan.normalized.json``, ``execution_report.json``,
    ``delivery_manifest.json``, ``events.jsonl``, ``environment.json``, and
    ``artifacts.json`` for later failure diagnosis.
    """

    return executor.execute_plan(plan.to_plan_dict(), confirmed=confirmed).to_dict()


@mcp.tool()
def start_model_session(
    name: str,
    units: str = "mm",
    metadata: dict[str, JsonValue] | None = None,
    output_formats: list[str] | None = None,
    drawing_profile: DrawingProfileInput | None = None,
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
        drawing_profile=drawing_profile.model_dump(mode="json") if drawing_profile is not None else None,
    )


@mcp.tool()
def apply_model_operation(session_id: str, operation: OperationInput) -> dict[str, Any]:
    """Validate and stage one production atomic operation in a model session.

    This is the safe planning surface for sketch/extrude/cut/hole/fillet/
    chamfer/pattern/revolve/sweep/loft workflows.  It validates required fields
    and named feature-graph references before the operation can be finalized.
    """

    return atomic_sessions.apply_model_operation(session_id, operation.to_operation_dict())


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
def generate_drawing(plan: ModelPlanInput) -> dict[str, Any]:
    """Generate an engineering drawing for the active model.

    Use this when a model already exists in the active adapter session and the
    client wants to retry or refresh drawing creation from the plan's
    ``DrawingProfile``.  Drawing annotation failures are reported as diagnostics
    instead of hiding the model or export status.
    """

    return executor.generate_drawing(plan.to_plan_dict()).to_dict()


@mcp.tool()
def export_outputs(plan: ModelPlanInput, formats: list[str] | None = None) -> dict[str, Any]:
    """Export the active model or drawing to requested formats.

    Use this to retry file exports from the active adapter session.  The optional
    ``formats`` argument overrides the plan's output format list for this call;
    unsupported formats still fail validation through the plan/export schema.
    """

    return executor.export_outputs(plan.to_plan_dict(), formats=formats).to_dict()


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


@mcp.tool()
def sw_subscribe_events(event_types: list[str]) -> dict[str, Any]:
    """Subscribe to SolidWorks application-level COM events.

    Supported event names include ActiveModelDocChange, FileOpenNotify,
    FileSaveAsNotify, FileCloseNotify, and RebuildNotify. Subscription is
    explicit and remains active only while the adapter instance is alive.
    """

    return executor.adapter.subscribe_events(event_types)


@mcp.tool()
def sw_unsubscribe_events() -> dict[str, Any]:
    """Unsubscribe from all active SolidWorks COM event listeners."""

    return executor.adapter.unsubscribe_events()


@mcp.tool()
def sw_get_event_log(max_events: int = 50) -> dict[str, Any]:
    """Return recent SolidWorks application events captured by the adapter."""

    return executor.adapter.get_event_log(max_events)


@mcp.tool()
def sw_run_command(command_id: int, command_string: str = "") -> dict[str, Any]:
    """Execute a SolidWorks command by its command ID.

    Use sw_list_commands to discover available command IDs.  This is the primary
    gateway to programmatic SolidWorks operation - every toolbar button and menu
    item maps to a command ID in swCommands_e.  Sensitive or destructive commands
    should be reviewed before execution.
    """
    return executor.adapter.run_command(command_id, command_string)


@mcp.tool()
def sw_list_commands(category_filter: str | None = None) -> dict[str, Any]:
    """List available SolidWorks command IDs for use with sw_run_command.

    Returns common SolidWorks commands with their IDs and categories.  Use the
    optional category_filter to narrow results (e.g. "Sketch", "Features",
    "Drawing", "Assembly").
    """
    return executor.adapter.list_commands(category_filter)


@mcp.tool()
def sw_list_open_documents() -> dict[str, Any]:
    """List all currently open SolidWorks documents.

    Returns title, path, type (part/assembly/drawing), and configuration for
    each open document.  Use this before sw_activate_document or
    sw_close_document.
    """
    return executor.adapter.list_open_documents()


@mcp.tool()
def sw_get_document_info(title: str | None = None) -> dict[str, Any]:
    """Get detailed information about a SolidWorks document.

    If title is omitted, returns info for the currently active document.
    Includes path, type, configuration, read-only status, and save state.
    """
    return executor.adapter.get_document_info(title)


@mcp.tool()
def sw_activate_document(title: str) -> dict[str, Any]:
    """Switch the active SolidWorks document to the named document.

    The document must already be open.  Use sw_list_open_documents to see
    available document titles.
    """
    return executor.adapter.activate_document(title)


@mcp.tool()
def sw_close_document(title: str) -> dict[str, Any]:
    """Close a specific SolidWorks document by title.

    The document must be open.  This will NOT close documents that are part
    of an active modeling transaction.  Use with caution - unsaved changes
    may be lost.
    """
    return executor.adapter.close_document(title)


@mcp.tool()
def sw_get_feature_tree(max_depth: int = 5) -> dict[str, Any]:
    """Traverse the feature tree of the active SolidWorks model.

    Returns a nested tree of features (extrudes, cuts, fillets, sketches, etc.)
    with their types and child features.  Use max_depth to limit traversal depth.
    """
    return executor.adapter.get_feature_tree(max_depth)


@mcp.tool()
def sw_select_by_id(
    name: str,
    type: str,
    mark: int = 2,
    x: float = 0,
    y: float = 0,
    z: float = 0,
    append: bool = False,
    mark_option: int = 1,
) -> dict[str, Any]:
    """Select a SolidWorks entity by its identifier string.

    This is the primary selection mechanism.  Common type values: "FACE",
    "EDGE", "VERTEX", "PLANE", "SKETCH", "BODY".  The name format
    depends on the entity type (e.g. face names from feature tree).
    Set append=True to add to existing selection instead of replacing it.
    """
    return executor.adapter.select_by_id(name, type, mark, x, y, z, append, mark_option)


@mcp.tool()
def sw_get_selected_objects() -> dict[str, Any]:
    """Get the currently selected objects in SolidWorks.

    Returns a list of selected entities with their types, names, and
    selection coordinates.  Useful for inspecting what the user has selected
    or verifying that a sw_select_by_id call succeeded.
    """
    return executor.adapter.get_selected_objects()


@mcp.tool()
def sw_get_mass_properties() -> dict[str, Any]:
    """Get mass properties of the active SolidWorks model.

    Returns mass (kg), volume (m3), surface area (m2), and center of mass
    coordinates.  This is a manufacturing sanity check, not a simulation result.
    Requires an active part or assembly document.
    """
    return executor.adapter.get_mass_properties()


@mcp.tool()
def sw_setup_simulation_study(study_name: str = "Static 1", study_type: str = "static") -> dict[str, Any]:
    """Create or activate a SolidWorks Simulation study on the active model."""
    return executor.adapter.setup_simulation_study(study_name, study_type)


@mcp.tool()
def sw_apply_simulation_material(material_name: str) -> dict[str, Any]:
    """Apply a material to the active model for SolidWorks Simulation."""
    return executor.adapter.apply_simulation_material(material_name)


@mcp.tool()
def sw_add_simulation_fixture(fixture_type: str, entity_name: str, entity_type: str) -> dict[str, Any]:
    """Add a SolidWorks Simulation fixture to a named active-model entity."""
    return executor.adapter.add_simulation_fixture(fixture_type, entity_name, entity_type)


@mcp.tool()
def sw_add_simulation_load(
    load_type: str,
    entity_name: str,
    entity_type: str,
    magnitude: float,
    direction: list[float] | None = None,
) -> dict[str, Any]:
    """Add a SolidWorks Simulation load to a named active-model entity."""
    return executor.adapter.add_simulation_load(load_type, entity_name, entity_type, magnitude, direction)


@mcp.tool()
def sw_run_simulation_mesh_and_solve() -> dict[str, Any]:
    """Mesh and solve the active SolidWorks Simulation study."""
    return executor.adapter.run_simulation_mesh_and_solve()


@mcp.tool()
def sw_get_simulation_results() -> dict[str, Any]:
    """Read best-effort result summaries from the active SolidWorks Simulation study."""
    return executor.adapter.get_simulation_results()


@mcp.tool()
def sw_add_dimxpert_dimension(
    entity_name: str,
    entity_type: str,
    dimension_type: str,
    x: float = 0,
    y: float = 0,
    z: float = 0,
) -> dict[str, Any]:
    """Add a DimXpert GD&T dimension to the active part by selecting an entity."""
    return executor.adapter.add_dimxpert_dimension(entity_name, entity_type, dimension_type, x, y, z)


@mcp.tool()
def sw_add_dimxpert_tolerance(dimension_name: str, tolerance_type: str, upper: float, lower: float) -> dict[str, Any]:
    """Add a tolerance specification to an existing DimXpert dimension."""
    return executor.adapter.add_dimxpert_tolerance(dimension_name, tolerance_type, upper, lower)


@mcp.tool()
def sw_list_dimxpert_dimensions() -> dict[str, Any]:
    """List DimXpert dimensions available in the active part."""
    return executor.adapter.list_dimxpert_dimensions()


@mcp.tool()
def sw_check_interference(component_selectors: list[str] | None = None) -> dict[str, Any]:
    """Run interference detection on the active SolidWorks assembly.

    Returns interfering component pairs with overlap volume.
    """
    return executor.adapter.check_interference(component_selectors)


@mcp.tool()
def sw_create_exploded_view(name: str = "ExplodedView1") -> dict[str, Any]:
    """Create an exploded view of the active SolidWorks assembly."""
    return executor.adapter.create_exploded_view(name)


@mcp.tool()
def sw_get_assembly_component_tree() -> dict[str, Any]:
    """Get component hierarchy and mate information for the active assembly."""
    return executor.adapter.get_assembly_component_tree()


@mcp.tool()
def sw_list_configurations() -> dict[str, Any]:
    """List all configurations in the active SolidWorks model."""
    return executor.adapter.list_configurations()


@mcp.tool()
def sw_activate_configuration(config_name: str) -> dict[str, Any]:
    """Activate a specific configuration in the active SolidWorks model."""
    return executor.adapter.activate_configuration(config_name)


@mcp.tool()
def sw_add_configuration(config_name: str, comment: str = "", options: int = 0) -> dict[str, Any]:
    """Add a new configuration to the active SolidWorks model."""
    return executor.adapter.add_configuration(config_name, comment, options)


@mcp.tool()
def sw_list_equations() -> dict[str, Any]:
    """List equations and global variables in the active SolidWorks model."""
    return executor.adapter.list_equations()


@mcp.tool()
def sw_set_equation(equation_str: str) -> dict[str, Any]:
    """Add or modify an equation/global variable in the active SolidWorks model."""
    return executor.adapter.set_equation(equation_str)


@mcp.tool()
def sw_read_properties_offline(file_path: str, configuration: str | None = None) -> dict[str, Any]:
    """Read custom properties from a SolidWorks document without starting SolidWorks.

    Uses the SolidWorks Document Manager API when available.  Real offline access
    requires swdocumentmgr.dll registration and the optional
    SOLIDWORKS_MCP_DOCMGR_LICENSE environment variable for licensed operations.
    """
    return executor.adapter.read_document_properties_offline(file_path, configuration)


@mcp.tool()
def sw_write_properties_offline(
    file_path: str,
    properties: dict[str, str],
    configuration: str | None = None,
) -> dict[str, Any]:
    """Write custom properties to a SolidWorks document without starting SolidWorks."""
    return executor.adapter.write_document_properties_offline(file_path, properties, configuration)


@mcp.tool()
def sw_read_configurations_offline(file_path: str) -> dict[str, Any]:
    """List configurations in a SolidWorks document without starting SolidWorks."""
    return executor.adapter.read_document_configurations_offline(file_path)


@mcp.tool()
def sw_read_bom_offline(file_path: str) -> dict[str, Any]:
    """Read BOM components from a SolidWorks assembly document without starting SolidWorks."""
    return executor.adapter.read_document_bom_offline(file_path)


@mcp.tool()
def sw_insert_bom_table(view_name: str | None = None, template_path: str | None = None) -> dict[str, Any]:
    """Insert a BOM table into the active SolidWorks drawing."""
    return executor.adapter.insert_drawing_bom_table(view_name, template_path)


@mcp.tool()
def sw_insert_center_mark(entity_type: str, x: float, y: float, z: float = 0.0) -> dict[str, Any]:
    """Insert a center mark on a circular drawing entity near the supplied sheet point."""
    return executor.adapter.insert_drawing_center_mark(entity_type, x, y, z)


@mcp.tool()
def sw_insert_centerline(
    entity_type: str,
    x1: float,
    y1: float,
    z1: float,
    x2: float,
    y2: float,
    z2: float,
) -> dict[str, Any]:
    """Insert a centerline between two drawing entities near the supplied sheet points."""
    return executor.adapter.insert_drawing_centerline(entity_type, x1, y1, z1, x2, y2, z2)


apply_tool_schema_guidance(mcp)


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
