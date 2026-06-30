from __future__ import annotations

from typing import Protocol, TypeAlias


SchemaValue: TypeAlias = str | int | float | bool | None | list["SchemaValue"] | dict[str, "SchemaValue"]
Schema: TypeAlias = dict[str, SchemaValue]


class ToolLike(Protocol):
    parameters: Schema


class ToolManagerLike(Protocol):
    _tools: dict[str, ToolLike]


class FastMCPLike(Protocol):
    _tool_manager: ToolManagerLike


TOOL_PARAMETER_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "abort_model_session": {
        "session_id": "Session id returned by start_model_session; the staged session is discarded without CAD execution.",
    },
    "apply_model_operation": {
        "session_id": "Session id returned by start_model_session; the operation is staged into this feature graph.",
        "operation": (
            "One OperationInput object. It must include op and either a parameters object or supported flat fields "
            "such as plane/entities for create_sketch, sketch_id/depth for extrude, or position/diameter/depth for hole."
        ),
    },
    "cleanup_run_documents": {
        "run_dir": "Completed run directory returned by execute_model_plan or finalize_model_session.",
    },
    "diagnose_release_gate": {
        "report_file": "Path to an archived release_gate_report.json file produced by the release production gate script.",
        "summary_only": "True returns a compact verdict; false includes detailed per-run diagnostic data.",
    },
    "diagnose_run": {
        "run_dir": "Completed run directory returned by execute_model_plan or finalize_model_session.",
        "summary_only": "True returns the production verdict and issue keys; false includes detailed file and event checks.",
        "tail": "Number of trailing event-log entries to include when summary_only is false.",
    },
    "diagnose_runs": {
        "root_dir": "Directory containing one or more completed SolidWorks MCP run directories.",
        "summary_only": "True returns aggregate accepted/rejected counts; false includes each run diagnosis.",
        "tail": "Number of trailing event-log entries to include per run when summary_only is false.",
        "max_runs": "Maximum run directories to scan; use 0 for a complete unbounded scan.",
    },
    "execute_model_plan": {
        "plan": "Validated ModelPlanInput containing name, units, ordered operations, optional drawing_profile, and output_formats.",
        "confirmed": "Must be true after user approval; false keeps the execution safety gate closed.",
    },
    "export_outputs": {
        "plan": "ModelPlanInput that describes the active model/session and default requested output_formats.",
        "formats": "Optional export format override for this call, for example ['step', 'stl'] or ['pdf', 'dwg'].",
    },
    "finalize_model_session": {
        "session_id": "Session id returned by start_model_session; all staged operations in that session will be executed.",
        "confirmed": "Must be true after user approval; false returns the same missing-confirmation safety response.",
    },
    "generate_drawing": {
        "plan": "ModelPlanInput whose drawing_profile controls sheet format, projection, view style, dimensions, and exports.",
    },
    "preflight_environment": {
        "plan": "Optional ModelPlanInput used to check templates, output formats, trusted workflow settings, and run prerequisites.",
    },
    "start_model_session": {
        "name": "Short stable model/session name used in reports and generated run artifacts.",
        "units": "Model units for all numeric dimensions in staged operations, for example mm, cm, m, inch, or ft.",
        "metadata": "Optional JSON metadata copied into the eventual plan and run reports, such as source prompt or design notes.",
        "output_formats": "Requested model output formats for finalization, for example ['sldprt', 'step', 'stl'].",
        "drawing_profile": "Optional DrawingProfileInput controlling engineering drawing generation and drawing export formats.",
    },
    "sw_activate_configuration": {
        "config_name": "Name of an existing configuration to activate in the active SolidWorks model.",
    },
    "sw_activate_document": {
        "title": "Exact title of an already-open SolidWorks document, as returned by sw_list_open_documents.",
    },
    "sw_add_configuration": {
        "config_name": "Name for the new configuration to add to the active SolidWorks model.",
        "comment": "Optional SolidWorks configuration comment stored with the new configuration.",
        "options": "SolidWorks configuration option bitmask; use 0 unless a specific API option is required.",
    },
    "sw_add_dimxpert_dimension": {
        "entity_name": "Name of the selected part entity that should receive the DimXpert dimension.",
        "entity_type": "SolidWorks selection type for the entity, for example FACE, EDGE, VERTEX, PLANE, or SKETCH.",
        "dimension_type": "DimXpert/GD&T dimension kind to create, for example size, location, or datum-related type.",
        "x": "Selection point x coordinate in model space, used when the entity name alone is ambiguous.",
        "y": "Selection point y coordinate in model space, used when the entity name alone is ambiguous.",
        "z": "Selection point z coordinate in model space, used when the entity name alone is ambiguous.",
    },
    "sw_add_dimxpert_tolerance": {
        "dimension_name": "Name of an existing DimXpert dimension returned by sw_list_dimxpert_dimensions.",
        "tolerance_type": "Tolerance kind to apply, for example bilateral, limit, fit, or geometric tolerance style.",
        "upper": "Upper tolerance value in the active model units.",
        "lower": "Lower tolerance value in the active model units.",
    },
    "sw_add_simulation_fixture": {
        "fixture_type": "SolidWorks Simulation fixture type, for example fixed, roller, hinge, or symmetry.",
        "entity_name": "Name of the model entity receiving the fixture.",
        "entity_type": "SolidWorks selection type for the fixture entity, for example FACE, EDGE, or VERTEX.",
    },
    "sw_add_simulation_load": {
        "load_type": "SolidWorks Simulation load type, for example force, pressure, torque, gravity, or bearing_load.",
        "entity_name": "Name of the model entity receiving the load.",
        "entity_type": "SolidWorks selection type for the load entity, for example FACE, EDGE, or VERTEX.",
        "magnitude": "Load magnitude in the units expected by the active SolidWorks Simulation study.",
        "direction": "Optional load direction vector [x, y, z]; omit for scalar loads such as pressure.",
    },
    "sw_apply_simulation_material": {
        "material_name": "SolidWorks material display/library name to apply before meshing and solving the simulation.",
    },
    "sw_check_interference": {
        "component_selectors": "Optional assembly component names/selectors to limit interference checking; null checks all components.",
    },
    "sw_close_document": {
        "title": "Exact title of the open SolidWorks document to close, as returned by sw_list_open_documents.",
    },
    "sw_create_exploded_view": {
        "name": "Name for the exploded view configuration or feature created in the active assembly.",
    },
    "sw_get_document_info": {
        "title": "Optional open document title; omit to inspect the currently active SolidWorks document.",
    },
    "sw_get_event_log": {
        "max_events": "Maximum number of recent SolidWorks adapter events to return.",
    },
    "sw_get_feature_tree": {
        "max_depth": "Maximum feature-tree nesting depth to return; keep small for large models.",
    },
    "sw_insert_bom_table": {
        "view_name": "Optional drawing view name to attach the BOM table to; omit to use the active/default view.",
        "template_path": "Optional absolute BOM table template path; omit to use SolidWorks defaults.",
    },
    "sw_insert_center_mark": {
        "entity_type": "Drawing entity type near the supplied sheet point, usually circle or arc.",
        "x": "Drawing sheet x coordinate near the circular entity that should receive the center mark.",
        "y": "Drawing sheet y coordinate near the circular entity that should receive the center mark.",
        "z": "Drawing sheet z coordinate near the circular entity; usually 0 for drawing sheets.",
    },
    "sw_insert_centerline": {
        "entity_type": "Drawing entity type near the supplied sheet points, usually edge, circle, or arc.",
        "x1": "Drawing sheet x coordinate near the first entity.",
        "y1": "Drawing sheet y coordinate near the first entity.",
        "z1": "Drawing sheet z coordinate near the first entity; usually 0 for drawing sheets.",
        "x2": "Drawing sheet x coordinate near the second entity.",
        "y2": "Drawing sheet y coordinate near the second entity.",
        "z2": "Drawing sheet z coordinate near the second entity; usually 0 for drawing sheets.",
    },
    "sw_list_commands": {
        "category_filter": "Optional command category substring, for example Sketch, Features, Drawing, Assembly, or File.",
    },
    "sw_read_bom_offline": {
        "file_path": "Absolute path to a SolidWorks assembly document whose BOM should be read offline.",
    },
    "sw_read_configurations_offline": {
        "file_path": "Absolute path to a SolidWorks part or assembly document whose configurations should be listed offline.",
    },
    "sw_read_properties_offline": {
        "file_path": "Absolute path to a SolidWorks document whose custom properties should be read offline.",
        "configuration": "Optional configuration name; omit or null to read document-level custom properties.",
    },
    "sw_run_command": {
        "command_id": "SolidWorks command id from swCommands_e; discover candidates with sw_list_commands first.",
        "command_string": "Optional command string argument passed to SolidWorks RunCommand; usually empty.",
    },
    "sw_select_by_id": {
        "name": "SolidWorks entity identifier to select, such as a feature, face, edge, plane, sketch, or body name.",
        "type": "SolidWorks selection type, for example FACE, EDGE, VERTEX, PLANE, SKETCH, BODY, or COMPONENT.",
        "mark": "Selection mark used by downstream SolidWorks commands; keep 2 unless a command requires another mark.",
        "x": "Model-space x coordinate used to disambiguate the selected entity.",
        "y": "Model-space y coordinate used to disambiguate the selected entity.",
        "z": "Model-space z coordinate used to disambiguate the selected entity.",
        "append": "True appends to the current selection set; false clears existing selections first.",
        "mark_option": "SolidWorks selection option flag; keep 1 unless a specific API call requires another option.",
    },
    "sw_set_equation": {
        "equation_str": "Equation or global variable assignment string, for example 'D1@Sketch1 = 25mm' or 'Length = 100mm'.",
    },
    "sw_setup_simulation_study": {
        "study_name": "Name of the SolidWorks Simulation study to create or activate.",
        "study_type": "Simulation study type; currently static is the expected production value.",
    },
    "sw_subscribe_events": {
        "event_types": (
            "SolidWorks application event names to subscribe to, for example ActiveModelDocChange, FileOpenNotify, "
            "FileSaveAsNotify, FileCloseNotify, or RebuildNotify."
        ),
    },
    "sw_write_properties_offline": {
        "file_path": "Absolute path to a SolidWorks document whose custom properties should be written offline.",
        "properties": "String custom-property key/value map to write, for example {'PartNo': 'A-001', 'Revision': 'A'}.",
        "configuration": "Optional configuration name; omit or null to write document-level custom properties.",
    },
    "validate_model_plan": {
        "plan": (
            "Candidate ModelPlanInput to validate before any confirmed execution. Include name, units, ordered operations, "
            "optional drawing_profile, metadata, and output_formats."
        ),
    },
}


def apply_tool_schema_guidance(mcp: FastMCPLike) -> None:
    for tool_name, field_descriptions in TOOL_PARAMETER_DESCRIPTIONS.items():
        tool = mcp._tool_manager._tools.get(tool_name)
        if tool is None:
            continue
        _apply_field_descriptions(tool.parameters, field_descriptions)


def _apply_field_descriptions(parameters: Schema, field_descriptions: dict[str, str]) -> None:
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return
    for field_name, description in field_descriptions.items():
        field_schema = properties.get(field_name)
        if isinstance(field_schema, dict):
            field_schema.setdefault("description", description)
