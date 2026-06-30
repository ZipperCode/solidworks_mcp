from __future__ import annotations

from pathlib import Path
import json
import sys
from typing import TypeAlias


SchemaValue: TypeAlias = str | int | float | bool | None | list["SchemaValue"] | dict[str, "SchemaValue"]
Schema: TypeAlias = dict[str, SchemaValue]


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solidworks_mcp.server import mcp  # noqa: E402


def main() -> None:
    tools = mcp._tool_manager._tools  # noqa: SLF001
    for tool_name, tool in sorted(tools.items()):
        _assert_top_level_descriptions(tool_name, tool.parameters)
        _assert_model_field_descriptions(tool_name, tool.parameters)
        _assert_no_unconstrained_object_slots(tool_name, tool.parameters)

    _assert_structured_ref(tools["validate_model_plan"].parameters, "plan", "ModelPlanInput")
    _assert_structured_ref(tools["preflight_environment"].parameters, "plan", "ModelPlanInput")
    _assert_structured_ref(tools["execute_model_plan"].parameters, "plan", "ModelPlanInput")
    _assert_structured_ref(tools["generate_drawing"].parameters, "plan", "ModelPlanInput")
    _assert_structured_ref(tools["export_outputs"].parameters, "plan", "ModelPlanInput")
    _assert_structured_ref(tools["apply_model_operation"].parameters, "operation", "OperationInput")

    start_schema = tools["start_model_session"].parameters
    _assert_structured_ref(start_schema, "drawing_profile", "DrawingProfileInput")
    metadata = start_schema["properties"]["metadata"]
    _assert(
        _schema_has_object(metadata),
        f"Expected start_model_session.metadata to remain an explicit object slot: {metadata}",
    )
    _assert(
        metadata != {"additionalProperties": True, "title": "Metadata", "type": "object"},
        f"Expected metadata schema to be more specific than an unconstrained object: {metadata}",
    )

    operation_schema = tools["apply_model_operation"].parameters["$defs"]["OperationInput"]
    model_plan_schema = tools["validate_model_plan"].parameters["$defs"]["ModelPlanInput"]
    drawing_profile_schema = tools["start_model_session"].parameters["$defs"]["DrawingProfileInput"]
    _assert_examples(operation_schema, "OperationInput")
    _assert_examples(model_plan_schema, "ModelPlanInput")
    _assert_examples(drawing_profile_schema, "DrawingProfileInput")
    _assert(
        operation_schema.get("additionalProperties") is False,
        f"Expected OperationInput to reject unknown top-level fields: {operation_schema}",
    )
    required_fields = set(operation_schema.get("required", []))
    _assert("op" in required_fields, f"Expected OperationInput.op to be required: {operation_schema}")
    _assert("parameters" in operation_schema.get("properties", {}), operation_schema)
    for flat_field in ("plane", "entities", "sketch_id", "depth", "position", "diameter", "target_refs"):
        _assert(flat_field in operation_schema.get("properties", {}), f"Missing flat field: {flat_field}")
    _assert_description_contains(operation_schema, "op", ("create_sketch", "extrude", "hole", "chamfer"))
    _assert_description_contains(operation_schema, "parameters", ("canonical", "flat", "parameters"))
    _assert_description_contains(operation_schema, "plane", ("create_sketch", "front", "top", "right"))
    _assert_description_contains(operation_schema, "entities", ("create_sketch", "rectangle", "circle"))
    _assert_description_contains(model_plan_schema, "operations", ("ordered", "validate_model_plan"))
    _assert_description_contains(drawing_profile_schema, "projection", ("first_angle", "third_angle"))

    examples_text = json.dumps(operation_schema.get("examples", []), ensure_ascii=False)
    for keyword in ("create_sketch", "plane", "entities", "extrude", "sketch_id", "hole", "diameter"):
        _assert(keyword in examples_text, f"OperationInput examples must mention {keyword}: {examples_text}")


def _assert_top_level_descriptions(tool_name: str, schema: Schema) -> None:
    properties = schema.get("properties", {})
    _assert(isinstance(properties, dict), f"{tool_name} parameters schema has no properties object: {schema}")
    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue
        description = field_schema.get("description")
        _assert(
            isinstance(description, str) and description.strip(),
            f"Expected description for {tool_name}.{field_name}: {field_schema}",
        )


def _assert_model_field_descriptions(tool_name: str, schema: Schema) -> None:
    definitions = schema.get("$defs", {})
    if not isinstance(definitions, dict):
        return
    for definition_name, definition_schema in definitions.items():
        if definition_name not in {"DrawingProfileInput", "ModelPlanInput", "OperationInput"}:
            continue
        _assert(isinstance(definition_schema, dict), f"{tool_name} $defs.{definition_name} is invalid: {schema}")
        properties = definition_schema.get("properties", {})
        _assert(isinstance(properties, dict), f"{tool_name} $defs.{definition_name} has no properties")
        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                continue
            description = field_schema.get("description")
            _assert(
                isinstance(description, str) and description.strip(),
                f"Expected description for {tool_name}.$defs.{definition_name}.{field_name}: {field_schema}",
            )


def _assert_no_unconstrained_object_slots(tool_name: str, schema: SchemaValue) -> None:
    if isinstance(schema, dict):
        is_unconstrained = schema.get("type") == "object" and schema.get("additionalProperties") is True
        _assert(not is_unconstrained, f"{tool_name} exposes an unconstrained object schema: {schema}")
        for nested_schema in schema.values():
            _assert_no_unconstrained_object_slots(tool_name, nested_schema)
        return
    if isinstance(schema, list):
        for nested_schema in schema:
            _assert_no_unconstrained_object_slots(tool_name, nested_schema)


def _assert_examples(schema: Schema, definition_name: str) -> None:
    examples = schema.get("examples")
    _assert(
        isinstance(examples, list) and bool(examples),
        f"Expected $defs.{definition_name} to include JSON schema examples: {schema}",
    )


def _assert_description_contains(
    schema: Schema,
    field_name: str,
    keywords: tuple[str, ...],
) -> None:
    properties = schema.get("properties", {})
    _assert(isinstance(properties, dict), f"Schema has no properties object: {schema}")
    field_schema = properties.get(field_name)
    _assert(isinstance(field_schema, dict), f"Missing property {field_name}: {schema}")
    description = field_schema.get("description")
    _assert(isinstance(description, str), f"Missing description for {field_name}: {field_schema}")
    description_lower = description.lower()
    for keyword in keywords:
        _assert(
            keyword.lower() in description_lower,
            f"Expected {field_name} description to mention {keyword}: {description}",
        )


def _assert_structured_ref(schema: Schema, field_name: str, definition_name: str) -> None:
    properties = schema.get("properties", {})
    _assert(isinstance(properties, dict), f"Schema has no properties object: {schema}")
    field_schema = properties[field_name]
    ref = _find_ref(field_schema)
    expected_ref = f"#/$defs/{definition_name}"
    _assert(ref == expected_ref, f"Expected {field_name} to reference {expected_ref}, got {field_schema}")
    definitions = schema.get("$defs", {})
    _assert(isinstance(definitions, dict) and definition_name in definitions, f"Missing $defs.{definition_name}")


def _find_ref(schema: SchemaValue) -> str | None:
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str):
            return ref
        variants = schema.get("anyOf", [])
        if isinstance(variants, list):
            for variant in variants:
                ref = _find_ref(variant)
                if ref is not None:
                    return ref
    return None


def _schema_has_object(schema: SchemaValue) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get("type") == "object":
        return True
    variants = schema.get("anyOf", [])
    return isinstance(variants, list) and any(_schema_has_object(variant) for variant in variants)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    main()
