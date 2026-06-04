"""Read-only capability catalog for SolidWorks MCP planning.

The catalog is intentionally descriptive.  It helps MCP clients and prompt
authors discover the intended protocol surface without expanding the executable
operation whitelist.  A capability marked ``planned`` or ``research`` must stay
out of ``ModelPlan.operations`` until the schema and adapter both support it.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Literal


CapabilityStatus = Literal["available", "planned", "research", "blocked"]


STATUS_DESCRIPTIONS: dict[CapabilityStatus, str] = {
    "available": "Usable through the current schema, MCP tool surface, or adapter implementation.",
    "planned": "Protocol shape is expected, but execution support is not ready yet.",
    "research": "Capability area needs API research or product decisions before protocol design.",
    "blocked": "Known dependency or safety issue prevents implementation in the current MVP.",
}


REFERENCE_PROJECTS: dict[str, dict[str, str]] = {
    "SolidworksMCP-TS": {
        "url": "https://github.com/vespo92/SolidworksMCP-TS",
        "usage": "MCP tool layout, COM argument limits, and macro fallback patterns.",
    },
    "solidworks-automation-skill": {
        "url": "https://github.com/wzyn20051216/solidworks-automation-skill",
        "usage": "Python automation workflow, review artifacts, and engineering acceptance habits.",
    },
    "CSharpAndSolidWorks": {
        "url": "https://github.com/painezeng/CSharpAndSolidWorks",
        "usage": "SolidWorks API examples for drawings, templates, dimensions, BOM, and selection.",
    },
    "solidworks-api": {
        "url": "https://github.com/angelsix/solidworks-api",
        "usage": "Long-term C# wrapper concepts for properties, annotations, add-ins, and app state.",
    },
}


CAPABILITY_CATALOG: dict[str, Any] = {
    "schema_version": "2026-06-04.1",
    "purpose": (
        "Describe current and future SolidWorks MCP protocol capabilities for planning. "
        "This catalog is not an execution whitelist."
    ),
    "execution_policy": {
        "tool_count": 6,
        "available_tools": [
            "connect_solidworks",
            "validate_model_plan",
            "execute_model_plan",
            "generate_drawing",
            "export_outputs",
            "inspect_active_model",
        ],
        "planned_capabilities_rule": (
            "planned, research, and blocked capabilities may be used in design discussion only. "
            "They must not be submitted to execute_model_plan unless they are promoted into "
            "SUPPORTED_OPERATIONS and implemented by the active adapter."
        ),
    },
    "statuses": STATUS_DESCRIPTIONS,
    "references": REFERENCE_PROJECTS,
    "categories": {
        "session_connection": {
            "name": "Session connection and preflight",
            "purpose": "Find and describe the active CAD runtime before an AI plan mutates SolidWorks state.",
            "future_adapter_entry": "SolidWorksCOMAdapter.connect / config preflight helpers",
            "capabilities": [
                {
                    "id": "session.connect",
                    "status": "available",
                    "purpose": "Connect to the configured adapter and return runtime or mock environment details.",
                    "suggested_inputs": ["SOLIDWORKS_MCP_ADAPTER", "template path environment variables"],
                    "expected_outputs": ["adapter", "connected", "version or mock notice", "output_root"],
                    "dependencies": ["MCP tool: connect_solidworks"],
                    "references": ["SolidworksMCP-TS", "solidworks-api"],
                    "notes": "Use before execution to confirm whether the client is in mock or Windows COM mode.",
                },
                {
                    "id": "session.validate_model_plan",
                    "status": "available",
                    "purpose": "Validate ModelPlan shape, units, export formats, and operation whitelist.",
                    "suggested_inputs": ["ModelPlan JSON"],
                    "expected_outputs": ["ExecutionReport with ok/message/failure_class"],
                    "dependencies": ["ModelPlan.from_dict", "SUPPORTED_OPERATIONS"],
                    "references": ["solidworks-automation-skill"],
                    "notes": "This is the safe gate before asking the user for execution confirmation.",
                },
                {
                    "id": "session.template_preflight",
                    "status": "planned",
                    "purpose": "Check part, drawing, and sheet-format templates before a long execution run.",
                    "suggested_inputs": ["part_template", "drawing_template", "sheet_format"],
                    "expected_outputs": ["template availability", "missing path diagnostics"],
                    "dependencies": ["SolidWorks template filesystem access"],
                    "future_adapter_entry": "config preflight helper before begin_transaction",
                    "references": ["CSharpAndSolidWorks", "SolidworksMCP-TS"],
                    "notes": "Keep as preflight metadata until a dedicated validator exists.",
                },
            ],
        },
        "sketch": {
            "name": "Sketch creation and constraints",
            "purpose": "Represent 2D construction intent without exposing raw sketch COM calls to the AI.",
            "future_adapter_entry": "SolidWorksCOMAdapter._op_create_sketch and selector helpers",
            "capabilities": [
                {
                    "id": "sketch.basic_entities",
                    "status": "available",
                    "purpose": "Create a sketch on a named plane with supported line, circle, rectangle, and arc-like entities.",
                    "suggested_inputs": ["plane", "entities[]", "units"],
                    "expected_outputs": ["sketch feature id", "step result"],
                    "dependencies": ["operation: create_sketch"],
                    "references": ["SolidworksMCP-TS", "CSharpAndSolidWorks"],
                    "notes": "Use explicit plane names or semantic selectors; do not rely on the current SolidWorks selection state.",
                },
                {
                    "id": "sketch.dimensions_constraints",
                    "status": "planned",
                    "purpose": "Attach driving dimensions and geometric constraints to sketch entities.",
                    "suggested_inputs": ["entity ids", "dimension names", "constraint types"],
                    "expected_outputs": ["fully/under-defined state", "dimension map"],
                    "dependencies": ["stable entity ids", "selection manager routing"],
                    "future_adapter_entry": "sketch constraint helper under adapter selection layer",
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "Planned because robust entity naming is required before exposing this safely.",
                },
            ],
        },
        "part_modeling": {
            "name": "Part modeling",
            "purpose": "Create single-part mechanical geometry from a restricted operation plan.",
            "future_adapter_entry": "SolidWorksCOMAdapter.execute_operation",
            "capabilities": [
                {
                    "id": "part.create_mounting_plate",
                    "status": "available",
                    "purpose": "Create the MVP rectangular mounting plate template with rounded corners and four threaded holes.",
                    "suggested_inputs": [
                        "length",
                        "width",
                        "thickness",
                        "corner_radius",
                        "hole_pattern",
                        "thread_spec",
                        "thread_standard",
                        "edge_offset",
                    ],
                    "expected_outputs": ["part body", "hole/thread status", "fallback markers"],
                    "dependencies": ["operation: create_mounting_plate", "semantic selectors", "HoleWizard fallback"],
                    "references": ["solidworks-automation-skill", "SolidworksMCP-TS"],
                    "notes": "The current Windows smoke target is 120 x 80 x 10 mm with M6 ISO metric coarse holes.",
                },
                {
                    "id": "part.basic_features",
                    "status": "available",
                    "purpose": "Route basic extrude, cut, hole, fillet, chamfer, and pattern operations.",
                    "suggested_inputs": ["feature-specific parameters", "feature or sketch ids"],
                    "expected_outputs": ["feature tree step result", "adapter diagnostics"],
                    "dependencies": [
                        "operations: extrude/cut/hole/fillet/chamfer/linear_pattern/circular_pattern",
                    ],
                    "references": ["SolidworksMCP-TS", "CSharpAndSolidWorks"],
                    "notes": "Coverage is intentionally narrow; prefer create_mounting_plate for the first real acceptance loop.",
                },
                {
                    "id": "part.semantic_selectors",
                    "status": "available",
                    "purpose": "Resolve plan selectors such as top_face, outer_edges, feature:<id>, and sketch:<id> before each feature.",
                    "suggested_inputs": ["selector string"],
                    "expected_outputs": ["adapter-selected face, edge, feature, or sketch"],
                    "dependencies": ["selector validation in schemas.py", "adapter selection helpers"],
                    "references": ["SolidworksMCP-TS", "CSharpAndSolidWorks"],
                    "notes": "Adapters must make selections explicitly for each operation.",
                },
                {
                    "id": "part.revolve_sweep_loft",
                    "status": "planned",
                    "purpose": "Support rotational, sweep, and loft features for non-prismatic parts.",
                    "suggested_inputs": ["profile sketch", "axis/path/guide curves"],
                    "expected_outputs": ["feature id", "feature status"],
                    "dependencies": ["multi-sketch references", "stable selection ids"],
                    "future_adapter_entry": "new operation handlers under SolidWorksCOMAdapter",
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "Do not add to SUPPORTED_OPERATIONS until selection and sketch references are stable.",
                },
            ],
        },
        "drawing": {
            "name": "Engineering drawing",
            "purpose": "Create drawing documents and export reviewable engineering sheets.",
            "future_adapter_entry": "SolidWorksCOMAdapter.generate_drawing",
            "capabilities": [
                {
                    "id": "drawing.standard_views",
                    "status": "available",
                    "purpose": "Create front, top, right, and optional isometric views from the active part.",
                    "suggested_inputs": ["DrawingProfile", "saved part path"],
                    "expected_outputs": ["SLDDRW path", "view status", "warnings"],
                    "dependencies": ["CreateDrawViewFromModelView3", "drawing template"],
                    "references": ["SolidworksMCP-TS", "CSharpAndSolidWorks"],
                    "notes": "View creation is best-effort and records warnings instead of hiding partial failures.",
                },
                {
                    "id": "drawing.basic_dimensions",
                    "status": "planned",
                    "purpose": "Insert baseline dimensions and annotations driven by plan intent.",
                    "suggested_inputs": ["dimension profile", "view names", "feature references"],
                    "expected_outputs": ["dimension ids", "annotation status"],
                    "dependencies": ["drawing view handles", "stable model dimensions"],
                    "future_adapter_entry": "drawing annotation helper",
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "Current drawing generation records annotation status but does not guarantee full drafting quality.",
                },
                {
                    "id": "drawing.hole_callouts",
                    "status": "available",
                    "purpose": "Attempt threaded hole callouts without blocking model or export success.",
                    "suggested_inputs": ["active drawing", "hole features"],
                    "expected_outputs": ["drawing_annotation_status", "warnings"],
                    "dependencies": ["SolidWorks drawing annotation APIs"],
                    "references": ["CSharpAndSolidWorks", "SolidworksMCP-TS"],
                    "notes": "Failures are reported in diagnostics and should not stop PDF/DWG export.",
                },
                {
                    "id": "drawing.bom_tables",
                    "status": "planned",
                    "purpose": "Insert BOM tables for assemblies or multi-body parts.",
                    "suggested_inputs": ["assembly drawing", "BOM template"],
                    "expected_outputs": ["BOM table id", "export status"],
                    "dependencies": ["assembly support", "drawing table APIs"],
                    "future_adapter_entry": "drawing table helper",
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "Blocked by the MVP single-part scope for now, but useful for future assembly workflows.",
                },
            ],
        },
        "export": {
            "name": "Export and file outputs",
            "purpose": "Save generated part, drawing, neutral CAD, mesh, and document outputs for review.",
            "future_adapter_entry": "SolidWorksCOMAdapter.export_outputs",
            "capabilities": [
                {
                    "id": "export.mvp_formats",
                    "status": "available",
                    "purpose": "Export SLDPRT, SLDDRW, STEP, STL, PDF, DWG, and DXF where the active document supports them.",
                    "suggested_inputs": ["output_formats", "drawing_profile.export_formats"],
                    "expected_outputs": ["format to absolute path map"],
                    "dependencies": ["ModelDocExtension.SaveAs", "active model/drawing"],
                    "references": ["SolidworksMCP-TS", "CSharpAndSolidWorks"],
                    "notes": "The report and artifacts index record all generated paths.",
                },
                {
                    "id": "export.iges_parasolid",
                    "status": "planned",
                    "purpose": "Add IGES and Parasolid exports for downstream CAD exchange.",
                    "suggested_inputs": ["format", "export options"],
                    "expected_outputs": ["IGES/X_T/X_B path", "export status"],
                    "dependencies": ["SaveAs options and extension mapping"],
                    "future_adapter_entry": "schema format whitelist and _solidworks_suffix mapping",
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "Must be added to SUPPORTED_EXPORT_FORMATS before clients can request it.",
                },
            ],
        },
        "assembly": {
            "name": "Assembly workflows",
            "purpose": "Create and inspect assemblies once the single-part loop is proven.",
            "future_adapter_entry": "future assembly adapter operations",
            "capabilities": [
                {
                    "id": "assembly.create_insert_mate",
                    "status": "planned",
                    "purpose": "Create assemblies, insert components, and apply mate relationships.",
                    "suggested_inputs": ["component paths", "mate definitions", "reference geometry"],
                    "expected_outputs": ["SLDASM path", "mate status", "component tree"],
                    "dependencies": ["assembly templates", "component file management"],
                    "references": ["SolidworksMCP-TS", "solidworks-api"],
                    "notes": "Keep outside the MVP tool execution protocol until single-part debugging is stable.",
                },
                {
                    "id": "assembly.interference_exploded_view",
                    "status": "planned",
                    "purpose": "Run interference checks and generate exploded views for review.",
                    "suggested_inputs": ["assembly document", "component groups"],
                    "expected_outputs": ["interference report", "exploded view status"],
                    "dependencies": ["assembly model support"],
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "Useful future self-review capability after assembly creation lands.",
                },
            ],
        },
        "properties_appearance": {
            "name": "Properties, material, and appearance",
            "purpose": "Attach engineering metadata and visual state to generated CAD outputs.",
            "future_adapter_entry": "material/property helpers on active document",
            "capabilities": [
                {
                    "id": "properties.assign_material",
                    "status": "available",
                    "purpose": "Assign basic material intent to the active part.",
                    "suggested_inputs": ["material"],
                    "expected_outputs": ["material assignment status"],
                    "dependencies": ["operation: assign_material"],
                    "references": ["solidworks-api", "CSharpAndSolidWorks"],
                    "notes": "Keep material names explicit so reports can show mismatches later.",
                },
                {
                    "id": "appearance.color_texture",
                    "status": "planned",
                    "purpose": "Apply colors, appearances, and simple visual grouping for review images.",
                    "suggested_inputs": ["RGB/appearance name", "target selector"],
                    "expected_outputs": ["appearance assignment status"],
                    "dependencies": ["selection layer", "appearance API mapping"],
                    "future_adapter_entry": "appearance helper after selector stabilization",
                    "references": ["solidworks-api", "solidworks-automation-skill"],
                    "notes": "Useful for AI-generated previews but not required for mechanical acceptance.",
                },
                {
                    "id": "properties.custom_properties",
                    "status": "planned",
                    "purpose": "Write custom properties such as part number, revision, designer, and description.",
                    "suggested_inputs": ["property key/value map", "configuration scope"],
                    "expected_outputs": ["property write status", "property summary"],
                    "dependencies": ["CustomPropertyManager"],
                    "future_adapter_entry": "document property helper",
                    "references": ["solidworks-api", "CSharpAndSolidWorks"],
                    "notes": "Should be available before BOM workflows become executable.",
                },
            ],
        },
        "templates_macros": {
            "name": "Templates and macro fallback",
            "purpose": "Manage SolidWorks template paths and controlled fallback macro execution.",
            "future_adapter_entry": "config.py and SolidWorksCOMAdapter macro helpers",
            "capabilities": [
                {
                    "id": "templates.configure_paths",
                    "status": "available",
                    "purpose": "Read part, drawing, output, and debug settings from environment variables.",
                    "suggested_inputs": ["SOLIDWORKS_MCP_PART_TEMPLATE", "SOLIDWORKS_MCP_DRAWING_TEMPLATE"],
                    "expected_outputs": ["environment snapshot", "config summary"],
                    "dependencies": ["SolidWorksMCPConfig.from_env"],
                    "references": ["SolidworksMCP-TS", "CSharpAndSolidWorks"],
                    "notes": "environment.json records safe configuration facts for each execution run.",
                },
                {
                    "id": "macros.holewizard_fallback",
                    "status": "available",
                    "purpose": "Provide a guarded VBA macro fallback path when HoleWizard COM calls fail.",
                    "suggested_inputs": ["thread spec", "hole points", "macro fallback flag"],
                    "expected_outputs": ["macro path", "fallback status"],
                    "dependencies": ["SOLIDWORKS_MCP_MACRO_FALLBACK", "macros/ run directory"],
                    "references": ["SolidworksMCP-TS", "solidworks-automation-skill"],
                    "notes": "The fallback is narrow and records degradation instead of pretending threaded geometry succeeded.",
                },
                {
                    "id": "macros.general_generation",
                    "status": "blocked",
                    "purpose": "Generate and execute arbitrary SolidWorks macros from AI text.",
                    "suggested_inputs": ["macro source", "execution policy"],
                    "expected_outputs": ["macro run status", "audit log"],
                    "dependencies": ["macro sandboxing", "user approval", "security policy"],
                    "future_adapter_entry": "not available until macro safety policy exists",
                    "references": ["SolidworksMCP-TS"],
                    "notes": "Blocked because arbitrary macro execution is too risky for the MVP.",
                },
            ],
        },
        "diagnostics_review": {
            "name": "Diagnostics and self-review",
            "purpose": "Make failed or partial SolidWorks runs debuggable after the user copies back artifacts.",
            "future_adapter_entry": "debug.py and scripts/diagnose_run.py",
            "capabilities": [
                {
                    "id": "diagnostics.run_artifacts",
                    "status": "available",
                    "purpose": "Create a run directory with plan, report, events, environment, and artifacts index.",
                    "suggested_inputs": ["confirmed execution"],
                    "expected_outputs": [
                        "plan.normalized.json",
                        "execution_report.json",
                        "events.jsonl",
                        "environment.json",
                        "artifacts.json",
                    ],
                    "dependencies": ["DebugRunContext", "EventRecorder"],
                    "references": ["solidworks-automation-skill"],
                    "notes": "This is the primary bug-location substrate for untested Windows COM runs.",
                },
                {
                    "id": "diagnostics.inspect_active_model",
                    "status": "available",
                    "purpose": "Return feature summary, fallback state, warnings, and active document data for AI review.",
                    "suggested_inputs": ["active adapter state"],
                    "expected_outputs": ["ExecutionReport.diagnostics", "feature_summary"],
                    "dependencies": ["MCP tool: inspect_active_model"],
                    "references": ["solidworks-automation-skill", "SolidworksMCP-TS"],
                    "notes": "Use after execution or export to decide whether repair planning is needed.",
                },
                {
                    "id": "diagnostics.visual_previews",
                    "status": "available",
                    "purpose": "Capture or mock front, top, right, and isometric preview artifacts.",
                    "suggested_inputs": ["active model", "run directory"],
                    "expected_outputs": ["preview_files map"],
                    "dependencies": ["adapter.capture_previews"],
                    "references": ["solidworks-automation-skill"],
                    "notes": "Preview capture is part of execute_model_plan finalization.",
                },
            ],
        },
        "advanced_manufacturing": {
            "name": "Advanced manufacturing",
            "purpose": "Track non-MVP manufacturing domains that need separate schema and adapter research.",
            "future_adapter_entry": "future domain-specific adapters or operation groups",
            "capabilities": [
                {
                    "id": "manufacturing.sheet_metal",
                    "status": "research",
                    "purpose": "Create sheet-metal base flanges, bends, unfold/fold states, and flat patterns.",
                    "suggested_inputs": ["gauge", "bend radius", "k-factor", "flange definitions"],
                    "expected_outputs": ["sheet metal part", "flat pattern export"],
                    "dependencies": ["SolidWorks sheet-metal feature APIs"],
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "Research only; not part of the single-part mechanical MVP.",
                },
                {
                    "id": "manufacturing.weldments",
                    "status": "research",
                    "purpose": "Create structural member weldments and cut lists.",
                    "suggested_inputs": ["profile library", "path sketches", "cut-list metadata"],
                    "expected_outputs": ["weldment part", "cut list"],
                    "dependencies": ["weldment profiles", "cut-list API"],
                    "references": ["solidworks-api"],
                    "notes": "Requires a separate metadata and library strategy.",
                },
                {
                    "id": "manufacturing.simulation",
                    "status": "research",
                    "purpose": "Run simulation or mass/property checks as engineering review signals.",
                    "suggested_inputs": ["loads", "fixtures", "material", "mesh settings"],
                    "expected_outputs": ["simulation report", "risk summary"],
                    "dependencies": ["SolidWorks Simulation licensing and API"],
                    "references": ["solidworks-api"],
                    "notes": "Research only; avoid presenting simulation output as validated engineering proof.",
                },
            ],
        },
    },
}


def get_capability_catalog() -> dict[str, Any]:
    """Return a deep copy of the complete capability catalog.

    Callers receive an isolated copy so MCP resource rendering cannot mutate the
    module-level registry shared by the server process.
    """

    return deepcopy(CAPABILITY_CATALOG)


def get_capability_category(category: str) -> dict[str, Any]:
    """Return one category from the catalog with registry metadata.

    The result is always JSON serializable.  Unknown categories return an error
    object instead of raising so MCP resource clients can display diagnostics.
    """

    catalog = get_capability_catalog()
    category_payload = catalog["categories"].get(category)
    if category_payload is None:
        return {
            "ok": False,
            "error": f"Unknown capability category: {category}",
            "available_categories": sorted(catalog["categories"]),
        }
    return {
        "ok": True,
        "schema_version": catalog["schema_version"],
        "category": category,
        "status_descriptions": catalog["statuses"],
        "references": catalog["references"],
        "data": category_payload,
    }


def capability_catalog_json() -> str:
    """Render the complete catalog as stable JSON for MCP resources."""

    return json.dumps(get_capability_catalog(), indent=2, sort_keys=True)


def capability_category_json(category: str) -> str:
    """Render one category as stable JSON for MCP resources."""

    return json.dumps(get_capability_category(category), indent=2, sort_keys=True)
