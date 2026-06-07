"""Named feature graph for staged atomic modeling sessions.

The graph is intentionally lightweight.  It gives MCP clients stable ids for
planning, reference validation, and diagnostics before a confirmed execution
creates any SolidWorks documents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from solidworks_mcp.schemas import ModelOperation, PlanValidationError


REFERENCE_NODE_TYPES = {
    "axis",
    "dimension",
    "edge",
    "entity",
    "face",
    "feature",
    "plane",
    "sketch",
}


ATOMIC_PRODUCTION_OPERATIONS = {
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


@dataclass
class FeatureGraphNode:
    """One named planning artifact exposed to an MCP client."""

    id: str
    type: str
    source_operation: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable graph node."""

        return {
            "id": self.id,
            "type": self.type,
            "source_operation": self.source_operation,
            "metadata": self.metadata,
        }


class FeatureGraph:
    """Track stable ids for staged atomic modeling operations."""

    def __init__(self) -> None:
        self._nodes: dict[str, FeatureGraphNode] = {}
        self._operation_count = 0
        for plane_id in ("front", "top", "right"):
            self.add_node(
                plane_id,
                "plane",
                metadata={"builtin": True, "solidworks_plane": f"{plane_id}_plane"},
            )
        for axis_id in ("x_axis", "y_axis", "z_axis"):
            self.add_node(axis_id, "axis", metadata={"builtin": True})

    def add_node(
        self,
        node_id: str,
        node_type: str,
        *,
        source_operation: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FeatureGraphNode:
        """Add a graph node, rejecting duplicate or unknown node ids."""

        clean_id = _clean_id(node_id, "node id")
        if node_type not in REFERENCE_NODE_TYPES:
            raise PlanValidationError(f"Unsupported feature graph node type: {node_type}")
        if clean_id in self._nodes:
            raise PlanValidationError(f"Feature graph id already exists: {clean_id}")
        node = FeatureGraphNode(
            id=clean_id,
            type=node_type,
            source_operation=source_operation,
            metadata=dict(metadata or {}),
        )
        self._nodes[clean_id] = node
        return node

    def require(self, node_id: Any, allowed_types: set[str], field_name: str) -> FeatureGraphNode:
        """Return a referenced node or raise a validation error."""

        clean_id = _clean_id(node_id, field_name)
        node = self._nodes.get(clean_id)
        if node is None:
            raise PlanValidationError(f"{field_name} references unknown feature graph id: {clean_id}")
        if node.type not in allowed_types:
            raise PlanValidationError(
                f"{field_name} must reference one of {sorted(allowed_types)}, got {node.type}: {clean_id}"
            )
        return node

    def validate_and_record(self, operation: ModelOperation) -> dict[str, Any]:
        """Validate references for an atomic operation and record its outputs."""

        if operation.op not in ATOMIC_PRODUCTION_OPERATIONS:
            raise PlanValidationError(
                f"Atomic sessions support only production atomic operations: {sorted(ATOMIC_PRODUCTION_OPERATIONS)}"
            )
        self._operation_count += 1
        params = operation.parameters
        created: list[FeatureGraphNode] = []
        references: list[FeatureGraphNode] = []

        if operation.op == "create_plane":
            references.append(self.require(params["base_plane"], {"plane", "face"}, "parameters.base_plane"))
            created.append(
                self.add_node(
                    operation.id or f"plane_{self._operation_count}",
                    "plane",
                    source_operation=operation.op,
                    metadata=_operation_metadata(params),
                )
            )

        elif operation.op == "create_sketch":
            references.append(self.require(params["plane"], {"plane", "face"}, "parameters.plane"))
            sketch_id = operation.id or str(params.get("sketch_id") or f"sketch_{self._operation_count}")
            created.append(
                self.add_node(
                    sketch_id,
                    "sketch",
                    source_operation=operation.op,
                    metadata={"plane": params["plane"], "entity_count": len(params.get("entities", []))},
                )
            )
            for entity_index, entity in enumerate(params.get("entities", [])):
                if isinstance(entity, dict) and entity.get("id"):
                    created.append(
                        self.add_node(
                            str(entity["id"]),
                            "entity",
                            source_operation=operation.op,
                            metadata={"sketch_id": sketch_id, "entity_index": entity_index, "kind": entity.get("type")},
                        )
                    )
            for dimension_index, dimension in enumerate(params.get("dimensions", [])):
                if isinstance(dimension, dict) and dimension.get("id"):
                    entity_id = dimension.get("entity_id") or dimension.get("target_id")
                    if entity_id:
                        references.append(self.require(entity_id, {"entity"}, "parameters.dimensions.entity_id"))
                    created.append(
                        self.add_node(
                            str(dimension["id"]),
                            "dimension",
                            source_operation=operation.op,
                            metadata={
                                "sketch_id": sketch_id,
                                "dimension_index": dimension_index,
                                "kind": dimension.get("type"),
                                "driving": dimension.get("driving", True) is not False,
                            },
                        )
                    )
            for constraint_index, constraint in enumerate(params.get("constraints", [])):
                if isinstance(constraint, dict):
                    for field_name in ("entity_id", "target_id", "entity_ids"):
                        value = constraint.get(field_name)
                        if isinstance(value, list):
                            for item in value:
                                references.append(self.require(item, {"entity"}, f"parameters.constraints.{field_name}"))
                        elif value:
                            references.append(self.require(value, {"entity"}, f"parameters.constraints.{field_name}"))

        elif operation.op in {"extrude", "cut", "revolve"}:
            references.append(self.require(params["sketch_id"], {"sketch"}, "parameters.sketch_id"))
            if operation.op == "revolve":
                _record_optional_axis_reference(self, params, references)
            created.append(
                self.add_node(
                    operation.id or f"{operation.op}_{self._operation_count}",
                    "feature",
                    source_operation=operation.op,
                    metadata=_operation_metadata(params),
                )
            )

        elif operation.op == "sweep":
            references.append(self.require(params["profile_sketch_id"], {"sketch"}, "parameters.profile_sketch_id"))
            if params.get("profile_id"):
                references.append(
                    self.require(
                        params["profile_id"],
                        {"sketch", "entity"},
                        "parameters.profile_id",
                    )
                )
            references.append(
                self.require(
                    params.get("path_sketch_id") or params.get("path_id"),
                    {"sketch", "edge", "entity"},
                    "parameters.path_sketch_id",
                )
            )
            created.append(
                self.add_node(
                    operation.id or f"sweep_{self._operation_count}",
                    "feature",
                    source_operation=operation.op,
                    metadata=_operation_metadata(params),
                )
            )

        elif operation.op == "loft":
            profile_ids = params["profile_sketch_ids"]
            for profile_id in profile_ids:
                references.append(self.require(profile_id, {"sketch"}, "parameters.profile_sketch_ids"))
            created.append(
                self.add_node(
                    operation.id or f"loft_{self._operation_count}",
                    "feature",
                    source_operation=operation.op,
                    metadata=_operation_metadata(params),
                )
            )

        elif operation.op == "hole":
            target = params.get("target_face")
            if target:
                references.append(self.require(target, {"face", "plane"}, "parameters.target_face"))
            created.append(
                self.add_node(
                    operation.id or f"hole_{self._operation_count}",
                    "feature",
                    source_operation=operation.op,
                    metadata=_operation_metadata(params),
                )
            )

        elif operation.op in {"fillet", "chamfer"}:
            target_refs = params.get("target_refs") or params.get("targets")
            if not isinstance(target_refs, list) or not target_refs:
                raise PlanValidationError(f"parameters.target_refs is required for atomic {operation.op}")
            for target_ref in target_refs:
                references.append(self.require(target_ref, {"edge", "face", "feature"}, "parameters.target_refs"))
            created.append(
                self.add_node(
                    operation.id or f"{operation.op}_{self._operation_count}",
                    "feature",
                    source_operation=operation.op,
                    metadata=_operation_metadata(params),
                )
            )

        elif operation.op in {"linear_pattern", "circular_pattern"}:
            references.append(self.require(params["seed_id"], {"feature"}, "parameters.seed_id"))
            if operation.op == "linear_pattern":
                _record_optional_direction_reference(self, params, references)
            else:
                _record_optional_axis_reference(self, params, references)
            created.append(
                self.add_node(
                    operation.id or f"{operation.op}_{self._operation_count}",
                    "feature",
                    source_operation=operation.op,
                    metadata=_operation_metadata(params),
                )
            )

        return {
            "created_nodes": [node.to_dict() for node in created],
            "referenced_nodes": [node.to_dict() for node in references],
            "graph": self.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the graph state in stable id order."""

        nodes = [self._nodes[node_id].to_dict() for node_id in sorted(self._nodes)]
        return {"node_count": len(nodes), "nodes": nodes}


def _record_optional_axis_reference(
    graph: FeatureGraph,
    params: dict[str, Any],
    references: list[FeatureGraphNode],
) -> None:
    axis_id = params.get("axis_id")
    axis = params.get("axis")
    if axis_id:
        references.append(graph.require(axis_id, {"axis", "edge", "entity"}, "parameters.axis_id"))
    elif isinstance(axis, str):
        references.append(graph.require(axis, {"axis", "edge", "entity"}, "parameters.axis"))


def _record_optional_direction_reference(
    graph: FeatureGraph,
    params: dict[str, Any],
    references: list[FeatureGraphNode],
) -> None:
    direction_id = params.get("direction_id")
    direction = params.get("direction")
    if direction_id:
        references.append(graph.require(direction_id, {"axis", "edge", "entity"}, "parameters.direction_id"))
    elif isinstance(direction, str):
        references.append(graph.require(direction, {"axis", "edge", "entity"}, "parameters.direction"))


def _operation_metadata(params: dict[str, Any]) -> dict[str, Any]:
    """Keep graph metadata compact and JSON-safe."""

    metadata: dict[str, Any] = {}
    for key in (
        "sketch_id",
        "base_plane",
        "distance",
        "profile_sketch_id",
        "profile_id",
        "path_sketch_id",
        "profile_diameter",
        "seed_id",
        "depth",
        "angle",
        "count",
        "spacing",
        "radius",
        "distance",
    ):
        if key in params:
            metadata[key] = params[key]
    return metadata


def atomic_dimension_ids_from_metadata(metadata: dict[str, Any]) -> list[str]:
    """Return staged atomic dimension ids from persisted feature graph evidence."""

    if not isinstance(metadata, dict):
        return []
    graph = metadata.get("atomic_feature_graph")
    if not isinstance(graph, dict):
        return []
    dimension_ids = {
        str(node.get("id")).strip()
        for node in graph.get("nodes", [])
        if isinstance(node, dict)
        and node.get("type") == "dimension"
        and isinstance(node.get("id"), str)
        and str(node.get("id")).strip()
    }
    return sorted(dimension_ids)


def _clean_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanValidationError(f"{field_name} must be a non-empty string")
    return value.strip()
