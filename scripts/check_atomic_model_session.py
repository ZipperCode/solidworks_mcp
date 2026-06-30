"""Regression checks for staged atomic model sessions."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solidworks_mcp.adapters.mock import MockCADAdapter  # noqa: E402
from solidworks_mcp.config import SolidWorksMCPConfig  # noqa: E402
from solidworks_mcp.executor import ModelPlanExecutor  # noqa: E402
from solidworks_mcp.sessions import AtomicSessionManager  # noqa: E402


def main() -> None:
    """Run focused atomic-session checks without a SolidWorks install."""

    with tempfile.TemporaryDirectory(prefix="solidworks_mcp_atomic_") as tmp:
        _set_mock_env(tmp)
        config = SolidWorksMCPConfig.from_env()
        executor = ModelPlanExecutor(MockCADAdapter(config), config)
        sessions = AtomicSessionManager(executor)

        accepted = _run_valid_hole_session(sessions)
        _assert(accepted.get("ok") is True, f"Expected valid atomic session to execute: {accepted}")
        verdict = accepted.get("production_verdict") or {}
        _assert(verdict.get("status") == "accepted", f"Expected accepted verdict: {verdict}")
        summary = verdict.get("summary") or {}
        _assert(summary.get("drawing_annotation_status") == "hole_callout_created", f"Missing callout: {summary}")
        _assert(summary.get("direct_hole_callout_created") is True, f"Missing direct callout: {summary}")
        _assert(summary.get("required_atomic_dimensions") == ["dim_width"], f"Missing atomic dimension gate: {summary}")
        _assert(summary.get("missing_atomic_dimensions") == [], f"Unexpected missing atomic dimensions: {summary}")
        _assert(summary.get("model_geometry_status") == "geometry_verified", f"Missing atomic geometry evidence: {summary}")
        _assert(summary.get("mass_property_status") == "mass_properties_verified", f"Missing atomic mass evidence: {summary}")
        metadata = (accepted.get("plan") or {}).get("metadata") or {}
        _assert(metadata.get("atomic_operation_count") == 4, f"Missing operation-count evidence: {metadata}")
        graph = metadata.get("atomic_feature_graph") or {}
        _assert(graph.get("node_count", 0) >= 9, f"Missing feature-graph evidence: {graph}")

        full_protocol = _stage_full_atomic_protocol_session(sessions)
        _assert(full_protocol.get("ok") is True, f"Expected full atomic protocol coverage: {full_protocol}")

        flat_operation = _stage_flat_atomic_operation_session(sessions)
        _assert(flat_operation.get("ok") is True, f"Expected flat atomic operation compatibility: {flat_operation}")

        missing_confirmation = _run_valid_hole_session(sessions, confirmed=False)
        _assert(missing_confirmation.get("ok") is False, f"Expected missing confirmation: {missing_confirmation}")
        _assert(missing_confirmation.get("failure_class") == "schema", f"Wrong failure class: {missing_confirmation}")

        bad_ref = _stage_bad_reference(sessions)
        _assert(bad_ref.get("ok") is False, f"Expected bad reference rejection: {bad_ref}")
        _assert("unknown feature graph id" in str(bad_ref.get("message")), f"Wrong bad-ref message: {bad_ref}")

        bad_ref_cases = _stage_atomic_reference_rejections(sessions)
        _assert(
            all(case.get("ok") is False for case in bad_ref_cases),
            f"Expected all atomic reference cases to fail: {bad_ref_cases}",
        )
        _assert(
            all("unknown feature graph id" in str(case.get("message")) for case in bad_ref_cases),
            f"Wrong atomic reference failure messages: {bad_ref_cases}",
        )

        bad_parameter_cases = _stage_atomic_parameter_rejections(sessions)
        _assert(
            all(case.get("ok") is False for case in bad_parameter_cases),
            f"Expected all atomic parameter cases to fail: {bad_parameter_cases}",
        )
        _assert(
            all(case.get("failure_class") == "schema" for case in bad_parameter_cases),
            f"Wrong atomic parameter failure classes: {bad_parameter_cases}",
        )

        spoofed = _preflight_spoofed_atomic_plan(executor)
        _assert(spoofed.get("ok") is False, f"Expected spoofed atomic plan to fail preflight: {spoofed}")
        _assert(spoofed.get("failure_class") == "preflight", f"Wrong spoof failure class: {spoofed}")
        failure_reason = (
            ((spoofed.get("diagnostics") or {}).get("preflight_result") or {})
            .get("checks", [{}])[0]
            .get("trusted_workflow", {})
            .get("failure_reason", "")
        )
        _assert("feature graph replay failed" in failure_reason, f"Wrong spoof failure reason: {failure_reason}")

    with tempfile.TemporaryDirectory(prefix="solidworks_mcp_atomic_callout_") as tmp:
        _set_mock_env(tmp)
        os.environ["SOLIDWORKS_MCP_FORCE_DRAWING_CALLOUT_FAILURE"] = "1"
        config = SolidWorksMCPConfig.from_env()
        failing_sessions = AtomicSessionManager(ModelPlanExecutor(MockCADAdapter(config), config))
        rejected = _run_valid_hole_session(failing_sessions)
        verdict = rejected.get("production_verdict") or {}
        _assert(verdict.get("status") == "rejected", f"Expected forced callout rejection: {verdict}")
        _assert("hole_callouts_created" in verdict.get("failures", []), f"Missing callout failure: {verdict}")
        _assert("direct_hole_callouts_created" in verdict.get("failures", []), f"Missing direct-callout failure: {verdict}")

    forced_cases = [
        (
            "SOLIDWORKS_MCP_FORCE_MODEL_GEOMETRY_FAILURE",
            ["model_geometry_verified", "mass_properties_verified"],
            "forced_model_geometry_failure_rejected",
        ),
        (
            "SOLIDWORKS_MCP_FORCE_DRAWING_DIMENSION_FAILURE",
            ["atomic_dimensions_created"],
            "forced_drawing_dimension_failure_rejected",
        ),
        (
            "SOLIDWORKS_MCP_FORCE_CAD_CONTENT_FAILURE",
            ["artifact_content_ready", "cad_artifact_content"],
            "forced_cad_content_failure_rejected",
        ),
        (
            "SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE",
            ["cleanup_completed", "cleanup_verified"],
            "forced_cleanup_failure_rejected",
        ),
        (
            "SOLIDWORKS_MCP_FORCE_EXPORT_FAILURE",
            ["requested_output_files"],
            "forced_export_failure_rejected",
        ),
    ]
    forced_checks: list[str] = []
    for env_var, expected_failures, check_name in forced_cases:
        rejected = _run_forced_atomic_rejection(env_var)
        verdict = rejected.get("production_verdict") or {}
        _assert(verdict.get("status") == "rejected", f"Expected {env_var} rejection: {verdict}")
        failures = verdict.get("failures", [])
        for expected_failure in expected_failures:
            _assert(expected_failure in failures, f"Missing {expected_failure} for {env_var}: {verdict}")
        forced_checks.append(check_name)

    print(
        {
            "ok": True,
            "checks": [
                "atomic_session_accepted",
                "full_atomic_protocol_covered",
                "flat_atomic_operation_compatible",
                "missing_confirmation_rejected",
                "bad_reference_rejected",
                "atomic_bad_references_rejected",
                "atomic_bad_parameters_rejected",
                "spoofed_metadata_preflight_rejected",
                "forced_callout_failure_rejected",
                *forced_checks,
            ],
        }
    )


def _set_mock_env(output_dir: str) -> None:
    """Set deterministic mock settings for this script process."""

    os.environ["SOLIDWORKS_MCP_ADAPTER"] = "mock"
    os.environ["SOLIDWORKS_MCP_OUTPUT_DIR"] = output_dir
    os.environ["SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN"] = "1"
    os.environ["SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW"] = "1"
    os.environ["SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT"] = "1"
    os.environ.pop("SOLIDWORKS_MCP_FORCE_DRAWING_CALLOUT_FAILURE", None)
    os.environ.pop("SOLIDWORKS_MCP_FORCE_DRAWING_DIMENSION_FAILURE", None)
    os.environ.pop("SOLIDWORKS_MCP_FORCE_CAD_CONTENT_FAILURE", None)
    os.environ.pop("SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE", None)
    os.environ.pop("SOLIDWORKS_MCP_FORCE_EXPORT_FAILURE", None)
    os.environ.pop("SOLIDWORKS_MCP_FORCE_MODEL_GEOMETRY_FAILURE", None)


def _run_valid_hole_session(sessions: AtomicSessionManager, confirmed: bool = True) -> dict:
    start = sessions.start_model_session("Atomic regression hole")
    _assert(start.get("ok") is True, f"Could not start atomic session: {start}")
    session_id = start["session_id"]
    operations = [
        {
            "id": "sketch_base",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [
                    {"id": "base_rect", "type": "center_rectangle", "center": [0, 0], "width": 80, "height": 40}
                ],
                "dimensions": [{"id": "dim_width", "entity_id": "base_rect", "type": "width", "value": 80}],
                "constraints": [{"type": "horizontal", "entity_id": "base_rect"}],
            },
        },
        {"id": "boss_base", "op": "extrude", "parameters": {"sketch_id": "sketch_base", "depth": 8}},
        {"id": "hole_a", "op": "hole", "parameters": {"position": [0, 0], "diameter": 8, "depth": 8}},
        {"op": "make_drawing", "parameters": {}},
    ]
    for operation in operations:
        result = sessions.apply_model_operation(session_id, operation)
        _assert(result.get("ok") is True, f"Could not stage {operation['op']}: {result}")
    return sessions.finalize_model_session(session_id, confirmed=confirmed)


def _stage_flat_atomic_operation_session(sessions: AtomicSessionManager) -> dict:
    """Stage common agent-emitted flat operation fields through normalization."""

    start = sessions.start_model_session("Atomic flat operation compatibility")
    _assert(start.get("ok") is True, f"Could not start flat-operation session: {start}")
    session_id = start["session_id"]
    flat_sketch = {
        "id": "flat_sketch",
        "op": "create_sketch",
        "plane": "front",
        "entities": [{"id": "flat_rect", "type": "center_rectangle", "center": [0, 0], "width": 40, "height": 20}],
    }
    sketch_result = sessions.apply_model_operation(session_id, flat_sketch)
    _assert(sketch_result.get("ok") is True, f"Flat create_sketch was not normalized: {sketch_result}")
    normalized_sketch = sketch_result.get("operation") or {}
    _assert((normalized_sketch.get("parameters") or {}).get("plane") == "front", f"Wrong normalized sketch: {normalized_sketch}")
    _assert("plane" not in normalized_sketch, f"Flat sketch field leaked into normalized operation: {normalized_sketch}")

    flat_extrude = {"id": "flat_boss", "op": "extrude", "sketch_id": "flat_sketch", "depth": 8}
    extrude_result = sessions.apply_model_operation(session_id, flat_extrude)
    _assert(extrude_result.get("ok") is True, f"Flat extrude was not normalized: {extrude_result}")
    return extrude_result


def _stage_full_atomic_protocol_session(sessions: AtomicSessionManager) -> dict:
    """Stage every production atomic operation through the feature graph."""

    start = sessions.start_model_session("Atomic full protocol coverage")
    _assert(start.get("ok") is True, f"Could not start full-protocol session: {start}")
    session_id = start["session_id"]
    operations = [
        {
            "id": "profile_sketch",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [
                    {"id": "profile_rect", "type": "center_rectangle", "center": [0, 0], "width": 40, "height": 20}
                ],
                "dimensions": [{"id": "profile_width", "entity_id": "profile_rect", "type": "width", "value": 40}],
                "constraints": [{"type": "horizontal", "entity_id": "profile_rect"}],
            },
        },
        {
            "id": "cut_sketch",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [{"id": "cut_circle", "type": "circle", "center": [0, 0], "diameter": 8}],
            },
        },
        {
            "id": "path_sketch",
            "op": "create_sketch",
            "parameters": {
                "plane": "top",
                "entities": [{"id": "path_line", "type": "line", "start": [0, 0], "end": [30, 0]}],
            },
        },
        {
            "id": "loft_profile_a",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [{"id": "loft_circle_a", "type": "circle", "center": [0, 0], "diameter": 12}],
            },
        },
        {
            "id": "loft_profile_b",
            "op": "create_sketch",
            "parameters": {
                "plane": "right",
                "entities": [{"id": "loft_circle_b", "type": "circle", "center": [0, 0], "diameter": 6}],
            },
        },
        {"id": "boss_base", "op": "extrude", "parameters": {"sketch_id": "profile_sketch", "depth": 8}},
        {"id": "cut_a", "op": "cut", "parameters": {"sketch_id": "cut_sketch", "depth": 8}},
        {"id": "hole_a", "op": "hole", "parameters": {"target_face": "front", "position": [0, 0], "diameter": 6, "depth": 8}},
        {"id": "fillet_a", "op": "fillet", "parameters": {"target_refs": ["boss_base"], "radius": 1}},
        {"id": "chamfer_a", "op": "chamfer", "parameters": {"target_refs": ["boss_base"], "distance": 1}},
        {
            "id": "linear_pattern_a",
            "op": "linear_pattern",
            "parameters": {"seed_id": "hole_a", "direction": "x_axis", "spacing": 20, "count": 2},
        },
        {
            "id": "circular_pattern_a",
            "op": "circular_pattern",
            "parameters": {"seed_id": "hole_a", "axis": "z_axis", "count": 4},
        },
        {"id": "revolve_a", "op": "revolve", "parameters": {"sketch_id": "profile_sketch", "axis": "z_axis", "angle": 180}},
        {
            "id": "sweep_a",
            "op": "sweep",
            "parameters": {"profile_sketch_id": "profile_sketch", "path_sketch_id": "path_sketch"},
        },
        {
            "id": "loft_a",
            "op": "loft",
            "parameters": {"profile_sketch_ids": ["loft_profile_a", "loft_profile_b"]},
        },
        {"op": "assign_material", "parameters": {"material": "Plain Carbon Steel"}},
        {"op": "set_custom_properties", "parameters": {"properties": {"PartNo": "ATOMIC-COVERAGE"}}},
        {"op": "make_drawing", "parameters": {}},
    ]
    last_result: dict = {}
    for operation in operations:
        last_result = sessions.apply_model_operation(session_id, operation)
        _assert(last_result.get("ok") is True, f"Could not stage {operation['op']}: {last_result}")

    graph = ((last_result.get("session") or {}).get("feature_graph")) or {}
    node_ids = {
        node.get("id")
        for node in graph.get("nodes", [])
        if isinstance(node, dict)
    }
    expected_nodes = {
        "front",
        "top",
        "right",
        "x_axis",
        "z_axis",
        "profile_sketch",
        "profile_rect",
        "profile_width",
        "boss_base",
        "cut_a",
        "hole_a",
        "linear_pattern_a",
        "circular_pattern_a",
        "revolve_a",
        "sweep_a",
        "loft_a",
    }
    missing_nodes = sorted(expected_nodes - node_ids)
    _assert(not missing_nodes, f"Full atomic protocol graph missed nodes {missing_nodes}: {graph}")
    return last_result


def _stage_bad_reference(sessions: AtomicSessionManager) -> dict:
    start = sessions.start_model_session("Atomic bad reference")
    _assert(start.get("ok") is True, f"Could not start bad-reference session: {start}")
    return sessions.apply_model_operation(
        start["session_id"],
        {"id": "bad_boss", "op": "extrude", "parameters": {"sketch_id": "missing_sketch", "depth": 5}},
    )


def _stage_atomic_reference_rejections(sessions: AtomicSessionManager) -> list[dict]:
    """Return focused bad-reference results for each reference-heavy atomic op."""

    cases = [
        ("create_sketch_dimension", [], {
            "id": "bad_sketch",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [{"id": "line_a", "type": "line", "start": [0, 0], "end": [10, 0]}],
                "dimensions": [{"id": "bad_dim", "entity_id": "missing_entity", "type": "length", "value": 10}],
            },
        }),
        ("create_sketch_constraint", [], {
            "id": "bad_sketch",
            "op": "create_sketch",
            "parameters": {
                "plane": "front",
                "entities": [{"id": "line_a", "type": "line", "start": [0, 0], "end": [10, 0]}],
                "constraints": [{"type": "coincident", "entity_ids": ["line_a", "missing_entity"]}],
            },
        }),
        ("hole_target_face", [], {
            "id": "bad_hole",
            "op": "hole",
            "parameters": {"target_face": "missing_face", "position": [0, 0], "diameter": 6, "depth": 8},
        }),
        ("fillet_target", [], {
            "id": "bad_fillet",
            "op": "fillet",
            "parameters": {"target_refs": ["missing_edge"], "radius": 1},
        }),
        ("chamfer_target", [], {
            "id": "bad_chamfer",
            "op": "chamfer",
            "parameters": {"target_refs": ["missing_edge"], "distance": 1},
        }),
        ("linear_pattern_seed", [], {
            "id": "bad_linear",
            "op": "linear_pattern",
            "parameters": {"seed_id": "missing_feature", "direction": "x_axis", "spacing": 10, "count": 2},
        }),
        ("circular_pattern_axis", [{"id": "seed_sketch", "op": "create_sketch", "parameters": {"plane": "front", "entities": [{"id": "seed_line", "type": "line", "start": [0, 0], "end": [10, 0]}]}}, {"id": "seed_feature", "op": "extrude", "parameters": {"sketch_id": "seed_sketch", "depth": 5}}], {
            "id": "bad_circular",
            "op": "circular_pattern",
            "parameters": {"seed_id": "seed_feature", "axis": "missing_axis", "count": 2},
        }),
        ("revolve_axis", [{"id": "rev_sketch", "op": "create_sketch", "parameters": {"plane": "front", "entities": [{"id": "rev_line", "type": "line", "start": [0, 0], "end": [10, 0]}]}}], {
            "id": "bad_revolve",
            "op": "revolve",
            "parameters": {"sketch_id": "rev_sketch", "axis": "missing_axis", "angle": 90},
        }),
        ("sweep_path", [{"id": "profile_sketch", "op": "create_sketch", "parameters": {"plane": "front", "entities": [{"id": "profile_line", "type": "line", "start": [0, 0], "end": [10, 0]}]}}], {
            "id": "bad_sweep",
            "op": "sweep",
            "parameters": {"profile_sketch_id": "profile_sketch", "path_sketch_id": "missing_path"},
        }),
        ("loft_profile", [{"id": "profile_a", "op": "create_sketch", "parameters": {"plane": "front", "entities": [{"id": "profile_line_a", "type": "line", "start": [0, 0], "end": [10, 0]}]}}], {
            "id": "bad_loft",
            "op": "loft",
            "parameters": {"profile_sketch_ids": ["profile_a", "missing_profile"]},
        }),
    ]
    results: list[dict] = []
    for name, prerequisites, operation in cases:
        start = sessions.start_model_session(f"Atomic bad reference {name}")
        _assert(start.get("ok") is True, f"Could not start bad-reference case {name}: {start}")
        session_id = start["session_id"]
        for prerequisite in prerequisites:
            prereq_result = sessions.apply_model_operation(session_id, prerequisite)
            _assert(prereq_result.get("ok") is True, f"Could not stage prerequisite for {name}: {prereq_result}")
        result = sessions.apply_model_operation(session_id, operation)
        result["case"] = name
        results.append(result)
    return results


def _stage_atomic_parameter_rejections(sessions: AtomicSessionManager) -> list[dict]:
    """Return focused schema failures for unsafe atomic geometry parameters."""

    cases = [
        {
            "id": "empty_sketch",
            "op": "create_sketch",
            "parameters": {"plane": "front", "entities": []},
        },
        {
            "id": "bad_circle",
            "op": "create_sketch",
            "parameters": {"plane": "front", "entities": [{"id": "c1", "type": "circle", "center": [0, 0]}]},
        },
        {
            "id": "bad_line",
            "op": "create_sketch",
            "parameters": {"plane": "front", "entities": [{"id": "l1", "type": "line", "start": [0, 0], "end": [0, 0]}]},
        },
        {
            "id": "bad_extrude",
            "op": "extrude",
            "parameters": {"sketch_id": "missing_sketch", "depth": 0},
        },
        {
            "id": "bad_hole",
            "op": "hole",
            "parameters": {"position": [0, 0, 0], "diameter": 6, "depth": 8},
        },
        {
            "id": "bad_fillet",
            "op": "fillet",
            "parameters": {"target_refs": ["missing_edge"], "radius": -1},
        },
        {
            "id": "bad_linear_count",
            "op": "linear_pattern",
            "parameters": {"seed_id": "missing_feature", "direction": "x_axis", "spacing": 10, "count": 1},
        },
        {
            "id": "bad_revolve_angle",
            "op": "revolve",
            "parameters": {"sketch_id": "missing_sketch", "axis": "z_axis", "angle": 720},
        },
        {
            "id": "bad_circular_angle",
            "op": "circular_pattern",
            "parameters": {"seed_id": "missing_feature", "axis": "z_axis", "count": 4, "angle": 720},
        },
    ]
    results: list[dict] = []
    for operation in cases:
        start = sessions.start_model_session(f"Atomic bad parameter {operation['id']}")
        _assert(start.get("ok") is True, f"Could not start bad-parameter case {operation['id']}: {start}")
        result = sessions.apply_model_operation(start["session_id"], operation)
        result["case"] = operation["id"]
        results.append(result)
    return results


def _run_forced_atomic_rejection(env_var: str) -> dict:
    """Run the valid atomic session with one forced production-gate failure."""

    with tempfile.TemporaryDirectory(prefix="solidworks_mcp_atomic_forced_") as tmp:
        _set_mock_env(tmp)
        os.environ[env_var] = "1"
        config = SolidWorksMCPConfig.from_env()
        sessions = AtomicSessionManager(ModelPlanExecutor(MockCADAdapter(config), config))
        return _run_valid_hole_session(sessions)


def _preflight_spoofed_atomic_plan(executor: ModelPlanExecutor) -> dict:
    plan = {
        "name": "Spoofed atomic plan",
        "units": "mm",
        "metadata": {
            "solidworks_mcp_workflow": "atomic_model_session",
            "atomic_session_id": "atomic_spoofed",
            "atomic_operation_count": 2,
            "atomic_feature_graph": {"node_count": 6, "nodes": []},
        },
        "operations": [
            {"id": "bad_boss", "op": "extrude", "parameters": {"sketch_id": "missing_sketch", "depth": 5}},
            {"op": "make_drawing", "parameters": {}},
        ],
    }
    return executor.execute_plan(plan, confirmed=True).to_dict()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


if __name__ == "__main__":
    main()
