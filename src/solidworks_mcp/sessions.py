"""Atomic modeling session orchestration for MCP clients.

Sessions are a staging layer: they validate operation order and named
references without creating SolidWorks documents.  Only ``finalize`` calls the
normal confirmed execution path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import uuid

from solidworks_mcp.executor import ModelPlanExecutor
from solidworks_mcp.feature_graph import FeatureGraph
from solidworks_mcp.schemas import (
    DrawingProfile,
    ModelOperation,
    ModelPlan,
    PlanValidationError,
)


@dataclass
class AtomicModelSession:
    """Staged operations and feature graph for one atomic model build."""

    session_id: str
    name: str
    units: str = "mm"
    metadata: dict[str, Any] = field(default_factory=dict)
    output_formats: tuple[str, ...] = ("sldprt", "step", "stl")
    drawing_profile: DrawingProfile = field(default_factory=DrawingProfile)
    graph: FeatureGraph = field(default_factory=FeatureGraph)
    operations: list[ModelOperation] = field(default_factory=list)
    finalized: bool = False

    def to_plan(self) -> ModelPlan:
        """Build a validated ModelPlan from the staged operations."""

        raw_plan = {
            "name": self.name,
            "units": self.units,
            "metadata": {
                **self.metadata,
                "solidworks_mcp_workflow": "atomic_model_session",
                "atomic_session_id": self.session_id,
                "atomic_operation_count": len(self.operations),
                "atomic_feature_graph": self.graph.to_dict(),
            },
            "output_formats": list(self.output_formats),
            "drawing_profile": self.drawing_profile.to_dict(),
            "operations": [operation.to_dict() for operation in self.operations],
        }
        return ModelPlan.from_dict(raw_plan)

    def summary(self) -> dict[str, Any]:
        """Return an MCP-friendly session summary."""

        return {
            "session_id": self.session_id,
            "name": self.name,
            "units": self.units,
            "operation_count": len(self.operations),
            "finalized": self.finalized,
            "output_formats": list(self.output_formats),
            "drawing_profile": self.drawing_profile.to_dict(),
            "feature_graph": self.graph.to_dict(),
        }


class AtomicSessionManager:
    """Manage atomic modeling sessions for one MCP server process."""

    def __init__(self, executor: ModelPlanExecutor) -> None:
        self._executor = executor
        self._sessions: dict[str, AtomicModelSession] = {}

    def start_model_session(
        self,
        name: str,
        units: str = "mm",
        metadata: dict[str, Any] | None = None,
        output_formats: list[str] | tuple[str, ...] | None = None,
        drawing_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a staged atomic modeling session without touching CAD state."""

        try:
            if not isinstance(name, str) or not name.strip():
                raise PlanValidationError("name must be a non-empty string")
            if metadata is not None and not isinstance(metadata, dict):
                raise PlanValidationError("metadata must be an object when provided")
            raw_output_formats = list(output_formats) if output_formats is not None else ["sldprt", "step", "stl"]
            drawing = DrawingProfile.from_dict(drawing_profile)
            probe_plan = {
                "name": name,
                "units": units,
                "metadata": metadata or {},
                "output_formats": raw_output_formats,
                "drawing_profile": drawing.to_dict(),
                "operations": [{"op": "make_drawing", "parameters": {}}],
            }
            ModelPlan.from_dict(probe_plan)
            session_id = f"atomic_{uuid.uuid4().hex[:12]}"
            session = AtomicModelSession(
                session_id=session_id,
                name=name.strip(),
                units=units,
                metadata=dict(metadata or {}),
                output_formats=tuple(str(item).lower() for item in raw_output_formats),
                drawing_profile=drawing,
            )
            self._sessions[session_id] = session
            return {"ok": True, "message": "Atomic model session started.", **session.summary()}
        except Exception as exc:
            return {"ok": False, "message": str(exc), "failure_class": "schema"}

    def apply_model_operation(self, session_id: str, operation: dict[str, Any]) -> dict[str, Any]:
        """Validate and stage one atomic operation."""

        session = self._sessions.get(session_id)
        if session is None:
            return {"ok": False, "message": f"Unknown atomic session: {session_id}", "failure_class": "session"}
        if session.finalized:
            return {"ok": False, "message": "Atomic session is already finalized.", "failure_class": "session"}

        try:
            model_operation = ModelOperation.from_dict(operation, len(session.operations))
            graph_result = session.graph.validate_and_record(model_operation)
            session.operations.append(model_operation)
            return {
                "ok": True,
                "message": f"Staged {model_operation.op}.",
                "operation": model_operation.to_dict(),
                **graph_result,
                "session": session.summary(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": str(exc),
                "failure_class": "schema",
                "session": session.summary(),
            }

    def finalize_model_session(self, session_id: str, confirmed: bool = False) -> dict[str, Any]:
        """Execute a staged session through the normal confirmed ModelPlan path."""

        session = self._sessions.get(session_id)
        if session is None:
            return {"ok": False, "message": f"Unknown atomic session: {session_id}", "failure_class": "session"}
        if session.finalized:
            return {"ok": False, "message": "Atomic session is already finalized.", "failure_class": "session"}
        if not session.operations:
            return {"ok": False, "message": "Atomic session has no staged operations.", "failure_class": "schema"}

        try:
            plan = session.to_plan()
            report = self._executor.execute_plan(plan.to_dict(), confirmed=confirmed).to_dict()
            session.finalized = bool(confirmed and report.get("ok") and report.get("run_dir"))
            report.setdefault("diagnostics", {})
            if isinstance(report["diagnostics"], dict):
                report["diagnostics"]["atomic_session"] = session.summary()
            return report
        except Exception as exc:
            return {"ok": False, "message": str(exc), "failure_class": "schema", "session": session.summary()}

    def abort_model_session(self, session_id: str) -> dict[str, Any]:
        """Drop a staged session without touching CAD state."""

        session = self._sessions.pop(session_id, None)
        if session is None:
            return {"ok": False, "message": f"Unknown atomic session: {session_id}", "failure_class": "session"}
        return {
            "ok": True,
            "message": "Atomic model session aborted.",
            "session": session.summary(),
        }
