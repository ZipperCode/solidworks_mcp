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
    "schema_version": "2026-06-06.1",
    "purpose": (
        "Describe current and future SolidWorks MCP protocol capabilities for planning. "
        "This catalog is not an execution whitelist."
    ),
    "execution_policy": {
        "tool_count": 15,
        "available_tools": [
            "connect_solidworks",
            "validate_model_plan",
            "preflight_environment",
            "execute_model_plan",
            "start_model_session",
            "apply_model_operation",
            "finalize_model_session",
            "abort_model_session",
            "generate_drawing",
            "export_outputs",
            "inspect_active_model",
            "diagnose_run",
            "diagnose_runs",
            "diagnose_release_gate",
            "cleanup_run_documents",
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
                    "purpose": "Validate ModelPlan shape, units, export formats, operation whitelist, and early production workflow readiness.",
                    "suggested_inputs": ["ModelPlan JSON"],
                    "expected_outputs": [
                        "ExecutionReport with ok/message/failure_class",
                        "diagnostics.schema_status",
                        "diagnostics.production_readiness_status",
                        "diagnostics.trusted_workflow_policy_check",
                    ],
                    "dependencies": ["ModelPlan.from_dict", "SUPPORTED_OPERATIONS", "trusted workflow policy"],
                    "references": ["solidworks-automation-skill"],
                    "notes": "This is the safe schema gate before asking the user for execution confirmation. ok=true means the JSON schema and operation whitelist passed; clients must also inspect production_readiness_status and still run preflight_environment before confirmed production execution.",
                },
                {
                    "id": "session.template_preflight",
                    "status": "available",
                    "purpose": "Hard-gate SolidWorks COM, part template, drawing template, and output-directory readiness before modeling starts.",
                    "suggested_inputs": ["optional ModelPlan JSON", "SOLIDWORKS_MCP_PART_TEMPLATE", "SOLIDWORKS_MCP_DRAWING_TEMPLATE"],
                    "expected_outputs": ["preflight_status", "preflight_result.checks", "failure_class=preflight on blocked execution"],
                    "dependencies": ["MCP tool: preflight_environment", "SolidWorksCOMAdapter.preflight_environment", "execute_model_plan preflight gate"],
                    "future_adapter_entry": "SolidWorksCOMAdapter._template_preflight_check and common template discovery",
                    "references": ["CSharpAndSolidWorks", "SolidworksMCP-TS"],
                    "notes": "execute_model_plan runs the same preflight internally and stops before adapter.transaction when any check fails.",
                },
                {
                    "id": "session.atomic_model_session",
                    "status": "available",
                    "purpose": "Stage production atomic modeling operations behind one named feature graph instead of exposing dozens of separate MCP tools.",
                    "suggested_inputs": [
                        "start_model_session name/units/output_formats/drawing_profile",
                        "apply_model_operation operations with stable ids",
                        "finalize_model_session confirmed=true",
                    ],
                    "expected_outputs": [
                        "session_id",
                        "feature_graph nodes for built-in planes/axes and created sketches/entities/features/dimensions",
                        "production_verdict for confirmed finalized runs",
                    ],
                    "dependencies": [
                        "MCP tools: start_model_session/apply_model_operation/finalize_model_session/abort_model_session",
                        "FeatureGraph",
                        "AtomicSessionManager",
                        "ModelPlanExecutor trusted atomic workflow replay",
                    ],
                    "references": ["solidworks-automation-skill", "SolidworksMCP-TS"],
                    "notes": "No CAD document is created until finalize_model_session receives confirmed=true. Production acceptance requires persisted atomic_session_id, operation count, and feature-graph evidence; executor preflight replays the graph before adapter.transaction so missing or spoofed references fail before document creation.",
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
                    "status": "available",
                    "purpose": "Name sketch driving dimensions and geometric constraints inside staged atomic sessions.",
                    "suggested_inputs": ["entity ids", "dimension names", "constraint types"],
                    "expected_outputs": ["feature graph dimension nodes", "reference validation before preflight"],
                    "dependencies": ["stable entity ids", "FeatureGraph.validate_and_record"],
                    "future_adapter_entry": "sketch constraint helper under adapter selection layer",
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "The session layer now records dimension ids and validates constraint entity references. Real SolidWorks driving-dimension and constraint COM replay remains a focused adapter-hardening item before claiming full sketch-definition parity.",
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
                    "dependencies": [
                        "operation: create_mounting_plate",
                        "semantic selectors",
                        "mounting plate geometry validation",
                        "model geometry readback",
                        "HoleWizard fallback",
                    ],
                    "references": ["solidworks-automation-skill", "SolidworksMCP-TS"],
                    "notes": (
                        "The trusted mounting-plate family is 120 x 80 x 10 mm with R5 corners, "
                        "15 mm edge offsets and ISO metric coarse M3/M4/M5/M6/M8 four-corner holes. "
                        "Schema validation rejects unsafe edge clearance, fillet clearance, spacing and thickness."
                    ),
                },
                {
                    "id": "part.create_center_hole_flange",
                    "status": "available",
                    "purpose": "Create the controlled center-hole flange trusted workflow.",
                    "suggested_inputs": [
                        "outer_diameter",
                        "thickness",
                        "hole_diameter",
                        "optional material",
                        "drawing_profile",
                    ],
                    "expected_outputs": [
                        "part body",
                        "model_geometry_status=geometry_verified",
                        "mass_property_status=mass_properties_verified",
                        "drawing_annotation_status=hole_callout_created",
                        "drawing_dimension_status=basic_dimensions_created",
                        "production verdict accepted when artifact and cleanup gates also pass",
                    ],
                    "dependencies": [
                        "operation: create_center_hole_flange",
                        "center-hole flange geometry validation",
                        "model geometry readback",
                        "mass-property readback",
                        "AddHoleCallout2 drawing callout",
                        "outer/hole diameter and thickness display dimensions",
                    ],
                    "references": ["solidworks-automation-skill", "CSharpAndSolidWorks"],
                    "notes": (
                        "Trusted smoke covers outer diameter, center through-hole diameter, thickness, material, "
                        "geometry/mass readback, direct drawing hole callout and trusted display dimensions."
                    ),
                },
                {
                    "id": "part.create_center_hole_plate",
                    "status": "available",
                    "purpose": "Create the controlled rectangular center-hole plate trusted workflow.",
                    "suggested_inputs": [
                        "length",
                        "width",
                        "thickness",
                        "hole_diameter",
                        "optional material",
                        "drawing_profile",
                    ],
                    "expected_outputs": [
                        "part body",
                        "model_geometry_status=geometry_verified",
                        "mass_property_status=mass_properties_verified",
                        "drawing_annotation_status=hole_callout_created",
                        "drawing_dimension_status=basic_dimensions_created",
                        "production verdict accepted when artifact and cleanup gates also pass",
                    ],
                    "dependencies": [
                        "operation: create_center_hole_plate",
                        "center-hole plate geometry validation",
                        "model geometry readback",
                        "mass-property readback",
                        "AddHoleCallout2 drawing callout",
                        "length/width/thickness and hole diameter display dimensions",
                    ],
                    "references": ["solidworks-automation-skill", "CSharpAndSolidWorks"],
                    "notes": (
                        "Trusted smoke covers rectangular plate length, width, thickness, center through-hole "
                        "diameter, material, geometry/mass readback, direct drawing hole callout and trusted "
                        "display dimensions. Thread and corner-radius statuses are not_requested for this workflow."
                    ),
                },
                {
                    "id": "part.create_bracket",
                    "status": "available",
                    "purpose": "Create the controlled L-bracket trusted workflow.",
                    "suggested_inputs": [
                        "base_length",
                        "base_width",
                        "base_thickness",
                        "upright_height",
                        "upright_thickness",
                        "hole_diameter",
                        "optional material",
                        "drawing_profile",
                    ],
                    "expected_outputs": [
                        "part body",
                        "model_geometry_status=geometry_verified",
                        "mass_property_status=mass_properties_verified",
                        "drawing_annotation_status=hole_callout_created",
                        "drawing_dimension_status=basic_dimensions_created",
                        "production verdict accepted when artifact and cleanup gates also pass",
                    ],
                    "dependencies": [
                        "operation: create_bracket",
                        "bracket geometry validation",
                        "model geometry readback",
                        "mass-property readback",
                        "AddHoleCallout2 drawing callout",
                        "base length/width/thickness, upright height/thickness and hole diameter display dimensions",
                    ],
                    "references": ["solidworks-automation-skill", "CSharpAndSolidWorks"],
                    "notes": (
                        "Trusted smoke covers an L bracket with one base hole and one upright hole, material, "
                        "geometry/mass readback, direct drawing hole callouts and trusted display dimensions. "
                        "Thread and corner-radius statuses are not_requested for this workflow."
                    ),
                },
                {
                    "id": "part.create_end_cap",
                    "status": "available",
                    "purpose": "Create the controlled circular end-cap trusted workflow.",
                    "suggested_inputs": [
                        "outer_diameter",
                        "thickness",
                        "center_hole_diameter",
                        "bolt_circle_diameter",
                        "bolt_hole_diameter",
                        "bolt_hole_count",
                        "optional material",
                        "drawing_profile",
                    ],
                    "expected_outputs": [
                        "part body",
                        "model_geometry_status=geometry_verified",
                        "mass_property_status=mass_properties_verified",
                        "drawing_annotation_status=hole_callout_created",
                        "drawing_dimension_status=basic_dimensions_created",
                        "production verdict accepted when artifact and cleanup gates also pass",
                    ],
                    "dependencies": [
                        "operation: create_end_cap",
                        "end-cap geometry validation",
                        "model geometry readback",
                        "mass-property readback",
                        "AddHoleCallout2 drawing callout",
                        "outer diameter, center-hole diameter, bolt-hole diameter and thickness display dimensions",
                    ],
                    "references": ["solidworks-automation-skill", "CSharpAndSolidWorks"],
                    "notes": (
                        "Trusted smoke covers circular end-cap outer diameter, center bore, bolt-hole pattern, "
                        "material, geometry/mass readback, direct drawing hole callout and trusted display "
                        "dimensions. Bolt circle diameter and bolt-hole count are recorded in geometry evidence; "
                        "dedicated PCD pattern annotations are planned follow-up coverage."
                    ),
                },
                {
                    "id": "part.create_mounting_block",
                    "status": "available",
                    "purpose": "Create the controlled mounting-block trusted workflow.",
                    "suggested_inputs": [
                        "length",
                        "width",
                        "height",
                        "hole_diameter",
                        "optional material",
                        "drawing_profile",
                    ],
                    "expected_outputs": [
                        "part body",
                        "model_geometry_status=geometry_verified",
                        "mass_property_status=mass_properties_verified",
                        "drawing_annotation_status=hole_callout_created",
                        "drawing_dimension_status=basic_dimensions_created",
                        "production verdict accepted when artifact and cleanup gates also pass",
                    ],
                    "dependencies": [
                        "operation: create_mounting_block",
                        "mounting block geometry validation",
                        "model geometry readback",
                        "mass-property readback",
                        "AddHoleCallout2 drawing callout",
                        "length/width/height and hole diameter display dimensions",
                    ],
                    "references": ["solidworks-automation-skill", "CSharpAndSolidWorks"],
                    "notes": (
                        "Trusted smoke covers mounting-block length, width, height, center through-hole "
                        "diameter, material, geometry/mass readback, direct drawing hole callout and trusted "
                        "display dimensions. Thread and corner-radius statuses are not_requested for this workflow."
                    ),
                },
                {
                    "id": "part.create_shaft",
                    "status": "available",
                    "purpose": "Create the controlled plain-shaft trusted workflow.",
                    "suggested_inputs": [
                        "diameter",
                        "length",
                        "optional material",
                        "drawing_profile",
                    ],
                    "expected_outputs": [
                        "part body",
                        "model_geometry_status=geometry_verified",
                        "mass_property_status=mass_properties_verified",
                        "drawing_annotation_status=not_requested",
                        "drawing_dimension_status=basic_dimensions_created",
                        "production verdict accepted when artifact and cleanup gates also pass",
                    ],
                    "dependencies": [
                        "operation: create_shaft",
                        "shaft geometry validation",
                        "model geometry readback",
                        "mass-property readback",
                        "diameter and length display dimensions",
                    ],
                    "references": ["solidworks-automation-skill", "CSharpAndSolidWorks"],
                    "notes": (
                        "Trusted smoke covers shaft diameter, length, material, geometry/mass readback and trusted "
                        "display dimensions. Hole Callout is explicitly not_requested because the controlled plain "
                        "shaft has no holes. Thread and corner-radius statuses are also not_requested."
                    ),
                },
                {
                    "id": "part.create_washer",
                    "status": "available",
                    "purpose": "Create the controlled washer trusted workflow.",
                    "suggested_inputs": [
                        "outer_diameter",
                        "inner_diameter",
                        "thickness",
                        "optional material",
                        "drawing_profile",
                    ],
                    "expected_outputs": [
                        "part body",
                        "model_geometry_status=geometry_verified",
                        "mass_property_status=mass_properties_verified",
                        "drawing_annotation_status=hole_callout_created",
                        "drawing_dimension_status=basic_dimensions_created",
                        "production verdict accepted when artifact and cleanup gates also pass",
                    ],
                    "dependencies": [
                        "operation: create_washer",
                        "washer geometry validation",
                        "model geometry readback",
                        "mass-property readback",
                        "AddHoleCallout2 drawing callout",
                        "outer/inner diameter and thickness display dimensions",
                    ],
                    "references": ["solidworks-automation-skill", "CSharpAndSolidWorks"],
                    "notes": (
                        "Trusted smoke covers washer outer diameter, inner through-hole diameter, thickness, "
                        "material, geometry/mass readback, direct drawing hole callout and trusted display "
                        "dimensions. Thread and corner-radius statuses are not_requested for this workflow."
                    ),
                },
                {
                    "id": "part.create_sleeve",
                    "status": "available",
                    "purpose": "Create the controlled sleeve trusted workflow.",
                    "suggested_inputs": [
                        "outer_diameter",
                        "inner_diameter",
                        "length",
                        "optional material",
                        "drawing_profile",
                    ],
                    "expected_outputs": [
                        "part body",
                        "model_geometry_status=geometry_verified",
                        "mass_property_status=mass_properties_verified",
                        "drawing_annotation_status=hole_callout_created",
                        "drawing_dimension_status=basic_dimensions_created",
                        "production verdict accepted when artifact and cleanup gates also pass",
                    ],
                    "dependencies": [
                        "operation: create_sleeve",
                        "sleeve geometry validation",
                        "model geometry readback",
                        "mass-property readback",
                        "AddHoleCallout2 drawing callout",
                        "outer/inner diameter and length display dimensions",
                    ],
                    "references": ["solidworks-automation-skill", "CSharpAndSolidWorks"],
                    "notes": (
                        "Trusted smoke covers sleeve outer diameter, inner bore diameter, length, material, "
                        "geometry/mass readback, direct drawing hole callout and trusted display dimensions. "
                        "Thread and corner-radius statuses are not_requested for this workflow."
                    ),
                },
                {
                    "id": "part.create_slotted_array_plate",
                    "status": "available",
                    "purpose": "Create the controlled slotted hole-array plate trusted workflow.",
                    "suggested_inputs": [
                        "length",
                        "width",
                        "thickness",
                        "slot_length",
                        "slot_width",
                        "hole_diameter",
                        "hole_rows",
                        "hole_columns",
                        "hole_spacing_x",
                        "hole_spacing_y",
                        "optional material",
                        "drawing_profile",
                    ],
                    "expected_outputs": [
                        "part body",
                        "model_geometry_status=geometry_verified",
                        "mass_property_status=mass_properties_verified",
                        "drawing_annotation_status=hole_callout_created",
                        "drawing_dimension_status=basic_dimensions_created",
                        "production verdict accepted when artifact and cleanup gates also pass",
                    ],
                    "dependencies": [
                        "operation: create_slotted_array_plate",
                        "slotted-array plate geometry validation",
                        "model geometry readback",
                        "mass-property readback",
                        "AddHoleCallout2 drawing callout",
                        "length/width/thickness, slot length/width, hole diameter and array spacing display dimensions",
                    ],
                    "references": ["solidworks-automation-skill", "CSharpAndSolidWorks"],
                    "notes": (
                        "Trusted smoke covers a rectangular plate with a center slot and hole array, material, "
                        "geometry/mass readback, direct drawing hole callouts and trusted display dimensions. "
                        "Thread and corner-radius statuses are not_requested for this workflow."
                    ),
                },
                {
                    "id": "part.basic_features",
                    "status": "available",
                    "purpose": "Route basic extrude, cut, hole, fillet, chamfer, and pattern operations through direct ModelPlan execution or staged atomic sessions.",
                    "suggested_inputs": ["feature-specific parameters", "feature or sketch ids"],
                    "expected_outputs": ["feature tree step result", "adapter diagnostics"],
                    "dependencies": [
                        "operations: extrude/cut/hole/fillet/chamfer/linear_pattern/circular_pattern",
                    ],
                    "references": ["SolidworksMCP-TS", "CSharpAndSolidWorks"],
                    "notes": "Direct freeform ModelPlan use remains non-production under the default trusted workflow policy. The production path is the atomic session protocol, which validates named sketch/feature/axis references, persists feature-graph evidence, and replays the graph in preflight before confirmed execution. Real SolidWorks COM reference replay for selected faces/edges/axes is still the main adapter-hardening frontier.",
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
                    "status": "available",
                    "purpose": "Support rotational, sweep, and loft features for non-prismatic parts.",
                    "suggested_inputs": ["profile sketch", "axis/path/guide curves"],
                    "expected_outputs": ["feature id", "feature status"],
                    "dependencies": ["multi-sketch references", "stable selection ids", "atomic feature graph replay"],
                    "future_adapter_entry": "expand SolidWorksCOMAdapter named reference replay for profile/path/axis selections",
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "These operations are now in SUPPORTED_OPERATIONS and accepted by staged atomic sessions. The SolidWorks adapter records guarded COM attempts and failure evidence; real full-gate validation is still required before treating complex selections as broadly proven.",
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
                    "purpose": "Create front, top, right, and isometric views from the active part.",
                    "suggested_inputs": ["DrawingProfile", "saved part path"],
                    "expected_outputs": ["SLDDRW path", "drawing_view_result", "front/top/right/isometric roles"],
                    "dependencies": ["CreateDrawViewFromModelView3", "drawing template"],
                    "references": ["SolidworksMCP-TS", "CSharpAndSolidWorks"],
                    "notes": "Partial view creation is reported with missing_roles/errors and rejects trusted production acceptance through drawing_standard_views_created.",
                },
                {
                    "id": "drawing.basic_dimensions",
                    "status": "available",
                    "purpose": "Create verified SolidWorks display dimensions for the controlled MVP drawings.",
                    "suggested_inputs": [
                        "front/top drawing views",
                        "controlled workflow parameters",
                        "include_basic_dimensions",
                    ],
                    "expected_outputs": ["drawing_dimension_status=basic_dimensions_created", "drawing_dimension_result"],
                    "dependencies": ["DrawingDoc.AddHorizontalDimension2", "DrawingDoc.AddVerticalDimension2", "DrawingDoc.AddRadialDimension2", "DrawingDoc.AddDimension2"],
                    "future_adapter_entry": "SolidWorksCOMAdapter._try_insert_basic_dimensions",
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "Trusted smoke requires workflow-specific basic dimensions: mounting-plate length, width, thickness, real selected-edge R5/R6 radial dimensions and hole edge offsets; flange outer diameter, hole diameter and thickness; or center-hole plate length, width, thickness and hole diameter. Successful runs report dimension_layout_status=trusted_dimensions_created; radius_proxy_used remains diagnostic and is rejected by production acceptance.",
                },
                {
                    "id": "drawing.hole_callouts",
                    "status": "available",
                    "purpose": "Create verified SolidWorks hole callouts from selected hole-face drawing view edges.",
                    "suggested_inputs": ["top drawing view", "visible circular hole edges", "hole points"],
                    "expected_outputs": ["drawing_annotation_status=hole_callout_created", "drawing_annotation_result"],
                    "dependencies": ["IView.GetVisibleComponents", "IView.GetVisibleEntities2", "IView.SelectEntity", "IDrawingDoc.AddHoleCallout2"],
                    "references": ["CSharpAndSolidWorks", "SolidworksMCP-TS"],
                    "notes": "Hole tables are diagnostic fallback only and do not satisfy trusted smoke acceptance. Real SolidWorks confirmed execution requires SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT=1 so production runs cannot rely on InsertModelAnnotations3 fallback policy.",
                },
                {
                    "id": "drawing.metadata_note",
                    "status": "available",
                    "purpose": "Insert visible drawing metadata notes from verified custom properties.",
                    "suggested_inputs": ["set_custom_properties operation"],
                    "expected_outputs": ["drawing_metadata_note_result.status=metadata_note_created", "PDF semantic matches for custom property values"],
                    "dependencies": ["DrawingDoc.InsertNote", "properties.custom_properties", "PDF semantic validation"],
                    "references": ["solidworks-api"],
                    "notes": "When a plan requests custom properties, exported PDF semantic validation requires those values to appear in the drawing text.",
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
                    "purpose": "Export SLDPRT, SLDDRW, STEP, STL, PDF, DWG, DXF, IGES, and Parasolid where the active document supports them.",
                    "suggested_inputs": ["output_formats", "drawing_profile.export_formats"],
                    "expected_outputs": ["format to absolute path map"],
                    "dependencies": ["ModelDocExtension.SaveAs", "active model/drawing"],
                    "references": ["SolidworksMCP-TS", "CSharpAndSolidWorks"],
                    "notes": "The report and artifacts index record all generated paths. Trusted production smoke requires the MVP set SLDPRT, STEP, STL, SLDDRW, PDF, DWG plus previews; optional DXF, IGES and Parasolid exports are validated when requested. The production suite keeps DXF covered by drawing_exchange and IGES/Parasolid covered by neutral_exports. A single SaveAs failure is recorded in export_result.failed and later formats continue where possible, but missing requested formats reject production acceptance with requested_output_files.",
                },
                {
                    "id": "export.dxf_drawing_exchange",
                    "status": "available",
                    "purpose": "Export an optional DXF drawing exchange file from the generated drawing document.",
                    "suggested_inputs": ["format=dxf", "drawing_profile", "trusted production workflow"],
                    "expected_outputs": ["DXF path", "artifact content check when requested"],
                    "dependencies": ["generated drawing document", "SaveAs options and extension mapping"],
                    "references": ["solidworks-api"],
                    "notes": "Schema accepts output format dxf. The SolidWorks adapter routes DXF through the active drawing when available, and artifact-content validation requires recognizable DXF sections/entities/EOF markers instead of only checking file size. The production smoke scenario drawing_exchange requests DXF so offline diagnosis keeps this path trusted.",
                },
                {
                    "id": "export.iges_parasolid",
                    "status": "available",
                    "purpose": "Export optional IGES and Parasolid X_T/X_B files for downstream CAD exchange.",
                    "suggested_inputs": ["format", "export options"],
                    "expected_outputs": ["IGES/X_T/X_B path", "artifact content check when requested"],
                    "dependencies": ["SaveAs options and extension mapping"],
                    "references": ["CSharpAndSolidWorks", "solidworks-api"],
                    "notes": "Schema accepts output formats iges, x_t, and x_b. The SolidWorks adapter writes IGES with the SW-friendly .igs suffix while keeping the report key as iges. Real artifact-content validation checks requested IGES and Parasolid outputs for recognizable structure and rejects wrong or placeholder files through cad_artifact_content. SW2022 SaveAs failures are surfaced as partial_export_failure plus requested_output_files rather than a silent or whole-run crash.",
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
                    "purpose": "Assign and verify material intent on the active part.",
                    "suggested_inputs": ["material"],
                    "expected_outputs": [
                        "material_status=material_verified",
                        "material_result.current_material",
                        "material_result.effective_material when a controlled alias is used",
                    ],
                    "dependencies": ["operation: assign_material"],
                    "references": ["solidworks-api", "CSharpAndSolidWorks"],
                    "notes": "When a plan requests assign_material, production acceptance requires readback verification. Controlled aliases are allowed only when the effective material is applied and read back; the SW2022 Chinese validation path maps Plain Carbon Steel to 普通碳钢. material_set_unverified, material_assignment_failed, and forced_failure are rejected diagnostics, not trusted material states. SOLIDWORKS_MCP_FORCE_MATERIAL_FAILURE=1 exercises this rejection path while allowing artifacts and cleanup to continue.",
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
                    "status": "available",
                    "purpose": "Write custom properties such as part number, revision, designer, and description.",
                    "suggested_inputs": ["property key/value map", "configuration scope"],
                    "expected_outputs": [
                        "custom_property_status=custom_properties_verified",
                        "custom_property_result.current_properties",
                    ],
                    "dependencies": ["operation: set_custom_properties", "CustomPropertyManager"],
                    "references": ["solidworks-api", "CSharpAndSolidWorks"],
                    "notes": "Production acceptance requires readback verification when a plan requests set_custom_properties. SW2022 may use the verified custom_info_legacy fallback. The drawing stage inserts verified values as a visible metadata note and PDF semantic validation requires them to be present.",
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
                    "purpose": "Read part, drawing, output, debug, and run-cleanup settings from environment variables.",
                    "suggested_inputs": [
                        "SOLIDWORKS_MCP_PART_TEMPLATE",
                        "SOLIDWORKS_MCP_DRAWING_TEMPLATE",
                        "SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN",
                    ],
                    "expected_outputs": ["environment snapshot", "config summary"],
                    "dependencies": ["SolidWorksMCPConfig.from_env"],
                    "references": ["SolidworksMCP-TS", "CSharpAndSolidWorks"],
                    "notes": "environment.json records safe configuration facts for each execution run.",
                },
                {
                    "id": "runtime.close_run_documents",
                    "status": "available",
                    "purpose": "Close only the SolidWorks part and drawing documents created by the current run after exports and previews complete.",
                    "suggested_inputs": ["SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN"],
                    "expected_outputs": [
                        "cleanup_result",
                        "cleanup_verification_status=verified",
                        "document_state_audit_result",
                        "adapter.document_state event",
                        "adapter.cleanup event",
                        "SldWorks.CloseDoc verbose COM calls",
                    ],
                    "dependencies": [
                        "tracked document titles",
                        "saved run output paths",
                        "GetOpenDocumentByName path guard",
                    ],
                    "references": ["solidworks-api"],
                    "notes": "Enabled by default for production safety. File-name and file-stem fallbacks are closed only after resolving to the current run workspace. execute_model_plan records document_state snapshots before transaction, after transaction, before cleanup, and after cleanup; trusted production acceptance now requires document_state_audit_result.status=verified_no_run_documents_open and after_cleanup_run_created_open_count=0. If disabled, execute_model_plan fails preflight with cleanup_policy before adapter.transaction so no new run documents are created. SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE=1 is available for mock-first cleanup regression tests and rejects trusted acceptance with cleanup_completed and cleanup_verified.",
                },
                {
                    "id": "runtime.cleanup_completed_run_documents",
                    "status": "available",
                    "purpose": "Post-run remediation tool that closes open SolidWorks native documents declared by a completed run directory.",
                    "suggested_inputs": ["run_dir", "SOLIDWORKS_MCP_ADAPTER=solidworks"],
                    "expected_outputs": [
                        "cleanup_run_documents.status",
                        "attach_only",
                        "candidate_documents",
                        "closed_documents",
                        "cleanup_verification_status",
                    ],
                    "dependencies": [
                        "MCP tool: cleanup_run_documents",
                        "scripts/cleanup_run_documents.py",
                        "execution_report.json or artifacts.json output_files",
                        "GetOpenDocumentByName path guard",
                    ],
                    "references": ["solidworks-api"],
                    "notes": "Use when a completed/interrupted real run reports cleanup failure or the operator suspects run-created documents are still open. The tool does not create documents or export files; it closes only SLDPRT/SLDDRW candidates that resolve to paths inside the supplied run_dir. SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY=1 is the default, so this remediation attaches to an already-running SolidWorks session and reports solidworks_not_running_attach_only instead of starting SolidWorks. Set it to 0 only when the operator explicitly wants Dispatch-based remediation. SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE=1 also exercises this post-run cleanup failure path with the mock adapter.",
                },
                {
                    "id": "runtime.direct_hole_callout_policy",
                    "status": "available",
                    "purpose": "Require direct selected-edge SolidWorks Hole Callout enforcement before confirmed real SolidWorks execution.",
                    "suggested_inputs": ["SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT"],
                    "expected_outputs": ["preflight direct_hole_callout_policy"],
                    "dependencies": ["SolidWorksCOMAdapter.preflight_environment", "drawing.hole_callouts"],
                    "references": ["solidworks-api"],
                    "notes": "For the real SolidWorks adapter this flag is a preflight gate, not only a post-run acceptance preference. If disabled, execute_model_plan stops before adapter.transaction so no production run documents are created under a non-strict callout policy.",
                },
                {
                    "id": "runtime.trusted_workflow_policy",
                    "status": "available",
                    "purpose": "Block schema-valid but untrusted workflows before confirmed production execution creates SolidWorks documents.",
                    "suggested_inputs": ["SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW", "ModelPlan.operations"],
                    "expected_outputs": ["preflight trusted_workflow_policy"],
                    "dependencies": ["ModelPlanExecutor._preflight_for_plan", "controlled workflow checks"],
                    "references": ["solidworks-api"],
                    "notes": "Enabled by default. Plans outside controlled workflows fail preflight with trusted_workflow_policy before adapter.transaction. Accepted production workflows are controlled_mounting_plate, controlled_center_hole_flange, controlled_center_hole_plate, controlled_bracket, controlled_end_cap, controlled_mounting_block, controlled_shaft, controlled_washer, controlled_sleeve, controlled_slotted_array_plate, and controlled_atomic_model when the plan was produced by an atomic session with persisted feature-graph evidence. Set SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW=0 only for non-production experiments.",
                },
                {
                    "id": "macros.holewizard_fallback",
                    "status": "available",
                    "purpose": "Write and attempt a guarded VBA macro fallback path when HoleWizard COM calls fail.",
                    "suggested_inputs": ["thread spec", "hole points", "macro fallback flag"],
                    "expected_outputs": ["macro path", "result path", "fallback status", "failure reason"],
                    "dependencies": ["SOLIDWORKS_MCP_MACRO_FALLBACK", "macros/ run directory"],
                    "references": ["SolidworksMCP-TS", "solidworks-automation-skill"],
                    "notes": "Narrow ISO metric coarse M3/M4/M5/M6/M8 four-corner through-hole path. Generated .swb execution is explicitly reported if SolidWorks rejects it.",
                },
                {
                    "id": "macros.generated_swb_execution",
                    "status": "blocked",
                    "purpose": "Execute newly generated text .swb macros as runnable SolidWorks macro projects.",
                    "suggested_inputs": ["controlled .swb path", "module name", "procedure name"],
                    "expected_outputs": ["RunMacro2 success", "macro result file"],
                    "dependencies": ["trusted .swp project or SolidWorks macro security configuration"],
                    "future_adapter_entry": "replace generated .swb execution with a trusted macro project handoff",
                    "references": ["solidworks-api"],
                    "notes": "SW2022 validation returned RunMacro2=False and error_code=0 for generated .swb probes; keep this distinct from arbitrary macro generation.",
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
            "future_adapter_entry": "debug.py, scripts/diagnose_run.py, and solidworks_mcp.run_diagnostics",
            "capabilities": [
                {
                    "id": "diagnostics.run_artifacts",
                    "status": "available",
                    "purpose": "Create a run directory with plan, report, delivery manifest, events, environment, and artifacts index.",
                    "suggested_inputs": ["confirmed execution"],
                    "expected_outputs": [
                        "plan.normalized.json",
                        "execution_report.json",
                        "delivery_manifest.json",
                        "delivery_manifest.handoff_summary",
                        "events.jsonl",
                        "environment.json",
                        "artifacts.json",
                        "artifact entries with relative_path and sha256",
                    ],
                    "dependencies": ["DebugRunContext", "EventRecorder"],
                    "references": ["solidworks-automation-skill"],
                    "notes": "This is the primary bug-location substrate for untested Windows COM runs. Manifest schema 2026-06-06.2 includes handoff_summary, a verified one-screen delivery summary with verdict, key statuses, output/preview counts, portable relative_path values, file hashes, diagnose command, and repro command. New artifact indexes include relative_path for output, preview, and fixed debug files while old historical indexes remain diagnosable.",
                },
                {
                    "id": "diagnostics.model_geometry_readback",
                    "status": "available",
                    "purpose": "Verify generated controlled solid-body dimensions from SolidWorks bounding-box readback before trusted acceptance.",
                    "suggested_inputs": ["active SolidWorks part", "controlled workflow plan parameters"],
                    "expected_outputs": ["model_geometry_status=geometry_verified", "body_count", "measured_dimensions_mm", "max_error_mm"],
                    "dependencies": ["SolidWorks GetBodies2/GetBodyBox or document bounding-box fallback", "ModelPlanExecutor production acceptance"],
                    "references": ["solidworks-api"],
                    "notes": "Production acceptance rejects geometry_readback_failed and geometry_mismatch so unit, scale, orientation, or missing-solid errors cannot pass only because artifacts exported.",
                },
                {
                    "id": "diagnostics.mass_properties",
                    "status": "available",
                    "purpose": "Verify that the generated controlled part reports positive mass and volume before trusted acceptance.",
                    "suggested_inputs": ["active SolidWorks part", "controlled production workflow"],
                    "expected_outputs": ["mass_property_status=mass_properties_verified", "mass_kg", "volume_m3", "surface_area_m2"],
                    "dependencies": ["SolidWorks CreateMassProperty or GetMassProperties fallback", "ModelPlanExecutor production acceptance"],
                    "references": ["solidworks-api"],
                    "notes": "This is a manufacturing sanity gate, not simulation proof. Production acceptance rejects missing, zero, or invalid mass properties for controlled production workflows.",
                },
                {
                    "id": "diagnostics.artifact_validation",
                    "status": "available",
                    "purpose": "Validate that reported CAD, drawing, and preview artifacts exist and are non-empty before trusted smoke passes.",
                    "suggested_inputs": ["ExecutionReport.output_files", "ExecutionReport.preview_files"],
                    "expected_outputs": ["artifact_validation_result.status=artifacts_ready", "per-artifact size checks", "required artifacts.json sha256 for outputs/previews and fixed debug files"],
                    "dependencies": ["ModelPlanExecutor artifact validation", "artifacts.json size and hash metadata"],
                    "references": ["solidworks-api"],
                    "notes": "Trusted smoke requires SLDPRT, STEP, STL, SLDDRW, PDF, DWG, and front/top/right/isometric previews. Stable fixed debug files such as plan.normalized.json, execution_report.json, events.jsonl, environment.json, and delivery_manifest.json are also hashed so diagnose_run can detect copied-run drift; missing hashes are integrity failures except for the self-referential artifacts.json fixed-file entry.",
                },
                {
                    "id": "diagnostics.artifact_content",
                    "status": "available",
                    "purpose": "Validate CAD, drawing, and preview readability beyond non-empty file existence.",
                    "suggested_inputs": ["CAD exports", "PDF drawing export", "front/top/right/isometric preview artifacts"],
                    "expected_outputs": ["artifact_content_result.status=content_ready", "cad_content_result", "PDF page count", "PNG dimensions and nonblank check"],
                    "dependencies": ["ModelPlanExecutor content validation"],
                    "references": ["solidworks-api"],
                    "notes": "Mock text exports are reported as placeholders. Real STEP/STL/DWG exports must pass format checks; requested DXF, IGES and Parasolid X_T/X_B outputs must expose recognizable exchange-file structure; native SLDPRT/SLDDRW exports must be plausible non-placeholder binaries; PNG previews must parse and contain pixel variation. SOLIDWORKS_MCP_FORCE_CAD_CONTENT_FAILURE exercises the rejected-artifacts-still-exported path.",
                },
                {
                    "id": "diagnostics.production_acceptance",
                    "status": "available",
                    "purpose": "Summarize the MVP trusted delivery gates into one accepted/rejected verdict.",
                    "suggested_inputs": ["ExecutionReport.diagnostics", "output_files", "preview_files"],
                    "expected_outputs": ["production_verdict.status=accepted", "production_acceptance_result.status=accepted", "failures", "repair_actions", "summary"],
                    "dependencies": ["preflight", "HoleWizard/thread fallback status", "model geometry readback", "mass property readback", "standard drawing views", "drawing callouts", "basic dimensions", "material verification when requested", "artifact validation", "artifact content", "cleanup result"],
                    "references": ["solidworks-api"],
                    "notes": "ExecutionReport.to_dict exposes this as top-level production_verdict and keeps the full production_acceptance_result under diagnostics. Rejected verdicts include repair_actions with stable ids, next_step text, and evidence_fields so clients can route an automatic repair pass instead of only displaying raw failure ids. The summary includes trusted_workflow_status, model geometry and mass-property readback, drawing_view_status, dimension_layout_status, proxy_dimensions, non_radial_radius_dimensions, callout_creation_method, and requested/current material so clients can distinguish trusted outputs from rejected diagnostic fallbacks. Current accepted workflows are controlled_mounting_plate, controlled_center_hole_flange, controlled_center_hole_plate, controlled_bracket, controlled_end_cap, controlled_mounting_block, controlled_shaft, controlled_washer, controlled_sleeve, controlled_slotted_array_plate, and session-produced controlled_atomic_model. Extra direct freeform modeling operations are rejected with trusted_controlled_workflow and, under the default trusted workflow policy, are blocked during preflight before execution.",
                },
                {
                    "id": "diagnostics.document_state_audit",
                    "status": "available",
                    "purpose": "Record run-scoped SolidWorks open-document state before and after cleanup.",
                    "suggested_inputs": ["active SolidWorks session", "run workspace"],
                    "expected_outputs": [
                        "diagnostics.document_state_before_transaction",
                        "diagnostics.document_state_after_transaction",
                        "diagnostics.document_state_before_cleanup",
                        "diagnostics.document_state_after_cleanup",
                        "diagnostics.document_state_audit_result",
                    ],
                    "dependencies": ["adapter.document_state events", "GetDocuments or GetFirstDocument/GetNext", "GetOpenDocumentByName fallback"],
                    "references": ["solidworks-api"],
                    "notes": "Snapshot capture records failures as diagnostics so artifacts can still be produced, but trusted production acceptance requires document_state_audit_verified in addition to cleanup_completed and cleanup_verified.",
                },
                {
                    "id": "diagnostics.run_diagnosis",
                    "status": "available",
                    "purpose": "Read a completed run directory and return the trusted production verdict without touching SolidWorks.",
                    "suggested_inputs": ["run_dir", "summary_only", "tail"],
                    "expected_outputs": ["ok", "production_acceptance_status", "production_acceptance_failures", "stored_production_acceptance_status", "current_acceptance_recheck", "repair_actions", "acceptance_summary", "artifact_integrity_status", "event_log_status", "event_log_issues", "delivery_manifest_status", "delivery_handoff_summary", "environment_status", "environment_issues", "missing_artifacts", "failed_events"],
                    "dependencies": ["MCP tool: diagnose_run", "execution_report.json", "artifacts.json", "delivery_manifest.json", "environment.json", "events.jsonl"],
                    "references": ["solidworks-automation-skill"],
                    "notes": "This is the MCP equivalent of scripts/diagnose_run.py. It rechecks stored accepted verdicts against the current production gate set and reports stored_production_acceptance_status/current_acceptance_recheck so older runs lacking newly trusted gates are rejected. It rechecks artifact paths, portable relative_path values, and required SHA-256 hashes for indexed output/preview/fixed files; reports missing_sha256 when a file exists without digest coverage; rejects new-schema missing, invalid, or mismatched relative paths with missing_relative_path/invalid_relative_path/relative_path_mismatch; rejects structurally incomplete artifact indexes with missing_field/missing_group/invalid_group; rejects missing fixed debug entries with missing_fixed_file_entry; rejects execution_report.json versus artifacts.json output/preview drift with report_keys_mismatch/report_path_mismatch; rejects mixed-run artifacts/events with artifact_run_id_mismatch/artifact_run_dir_mismatch/event_run_id_mismatch/missing_event_run_id; rejects unrecovered failed events while reporting recovered COM probes separately; requires a terminal plan.execution event whose status and output/preview counts agree with execution_report; verifies delivery_manifest.json against the report and artifact index; verifies schema 2026-06-06.2 handoff_summary, including relative paths, while keeping older manifests readable; returns compact delivery_handoff_summary for client routing; verifies environment.json run id/adapter/run_dir/env consistency; requires accepted real SolidWorks runs to prove document cleanup and direct hole-callout enforcement flags; and is safe to call after cleanup because it never connects to SolidWorks or mutates CAD files. smoke_production_workflows.py requires this offline diagnosis to pass before a production-suite smoke run is accepted; smoke_mounting_plate.py keeps the backward-compatible single-plan and mounting-plate matrix entrypoint.",
                },
                {
                    "id": "diagnostics.run_collection_diagnosis",
                    "status": "available",
                    "purpose": "Batch-audit completed run directories below a root and summarize trusted production delivery status.",
                    "suggested_inputs": ["root_dir", "summary_only", "tail", "max_runs"],
                    "expected_outputs": ["ok", "scan_status", "run_count", "accepted_count", "rejected_count", "issue_counts", "results[].delivery_manifest_file", "results[].issue_counts"],
                    "dependencies": ["MCP tool: diagnose_runs", "scripts/diagnose_runs.py", "solidworks_mcp.run_diagnostics.diagnose_run_collection"],
                    "references": ["solidworks-automation-skill"],
                    "notes": "This recursively finds run directories containing execution_report.json, applies the same single-run diagnose_run verifier, and aggregates stable issue keys such as production, artifact, event, manifest, and environment failures. It is intended for release handoff audits, copied-run review, and repair routing; it never connects to SolidWorks. max_runs=0 is the production default and performs a complete unbounded scan. Set a positive max_runs only for an explicit exploratory sample; scan_status=truncated makes ok=false because max_runs prevented a complete root audit.",
                },
                {
                    "id": "diagnostics.release_gate_report",
                    "status": "available",
                    "purpose": "Verify an archived release_gate_report.json against current run files.",
                    "suggested_inputs": ["release_gate_report.json", "summary_only"],
                    "expected_outputs": [
                        "status=verified",
                        "issues",
                        "batch.run_count",
                        "batch.accepted_count",
                        "current_evidence_summary",
                        "current_evidence_checks",
                    ],
                    "dependencies": ["MCP tool: diagnose_release_gate", "scripts/diagnose_release_gate.py", "solidworks_mcp.release_diagnostics"],
                    "references": ["solidworks-automation-skill"],
                    "notes": "This re-runs offline batch diagnosis for the report output root and checks the archived scenario list, schema version, output root, batch counts, and accepted scenario set. It also recomputes release evidence from current run artifacts and rejects stale reports when direct Hole Callouts, trusted dimensions, cleanup/document-state, required outputs/previews, CAD content, or PDF semantic content can no longer be proven. The release_production_gate.py producer writes a rejected report with emergency_cleanup_result if a batch run is interrupted or crashes after partial run creation. The smoke_mounting_plate.py and smoke_production_workflows.py producers write smoke_failure_report.json on interruption or unexpected exception and attempt conservative run-scoped cleanup for completed runs touched after the smoke command started. SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION_AFTER_RUN=1 verifies that path after at least one completed smoke run exists. Diagnosis itself never connects to SolidWorks or mutates CAD files.",
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
