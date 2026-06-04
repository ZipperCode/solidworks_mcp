"""Mock adapter for development without a Windows SolidWorks installation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters.base import CADAdapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.schemas import DrawingProfile, ModelOperation, ModelPlan, StepResult, path_to_string, safe_output_name


class MockCADAdapter(CADAdapter):
    """Deterministic adapter that records intended CAD actions as artifacts."""

    name = "mock"

    def __init__(self, config: SolidWorksMCPConfig) -> None:
        self._config = config
        self._active_plan: ModelPlan | None = None
        self._workspace: Path | None = None
        self._features: list[dict[str, Any]] = []
        self._thread_model_status = "not_requested"
        self._drawing_view_status = "not_requested"
        self._drawing_annotation_status = "not_requested"
        self._fallbacks: list[dict[str, Any]] = []
        self._warnings: list[str] = []

    def connect(self) -> dict[str, Any]:
        """Return mock runtime details used by clients during local development."""

        return {
            "adapter": self.name,
            "connected": True,
            "message": "Mock adapter active. Real SolidWorks COM execution requires Windows.",
            "output_root": path_to_string(self._config.output_root),
        }

    def begin_transaction(self, plan: ModelPlan) -> dict[str, Any]:
        """Create an isolated artifact directory for the current plan."""

        self._active_plan = plan
        self._features = []
        self._thread_model_status = "not_requested"
        self._drawing_view_status = "not_requested"
        self._drawing_annotation_status = "not_requested"
        self._fallbacks = []
        self._warnings = []
        self._workspace = getattr(self, "_run_workspace", None) or (
            self._config.output_root / safe_output_name(plan.name)
        )
        self._workspace.mkdir(parents=True, exist_ok=True)
        self.record_event("adapter.transaction", "started", {"workspace": self._workspace})
        return {
            "workspace": path_to_string(self._workspace),
            "document": f"{plan.name}.SLDPRT",
        }

    def execute_operation(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record a simulated feature for traceable dry-runs."""

        if operation.op == "create_mounting_plate":
            return self._execute_mounting_plate(operation, index, plan)

        feature = {
            "index": index,
            "id": operation.id or f"{operation.op}_{index}",
            "op": operation.op,
            "description": operation.description,
            "parameters": operation.parameters,
        }
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message=f"Mock executed {operation.op}",
            details=feature,
        )

    def _execute_mounting_plate(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Record the high-level mounting plate template and expected outputs."""

        params = operation.parameters
        feature = {
            "index": index,
            "id": operation.id or "mounting_plate",
            "op": operation.op,
            "description": operation.description,
            "parameters": params,
            "semantic_selectors": ["top_face", "outer_edges"],
            "thread_model_status": "mock_threaded_hole",
            "drawing_annotation_status": "mock_callout",
        }
        self._thread_model_status = "mock_threaded_hole"
        self._drawing_view_status = "mock_views"
        self._drawing_annotation_status = "mock_callout"
        self._features.append(feature)
        self.record_event("adapter.operation", "completed", feature)
        return StepResult(
            index=index,
            id=operation.id,
            op=operation.op,
            ok=True,
            message="Mock executed create_mounting_plate with semantic selectors.",
            details=feature,
        )

    def generate_drawing(self, plan: ModelPlan, profile: DrawingProfile) -> dict[str, str]:
        """Write a drawing manifest that mirrors the requested drawing profile."""

        workspace = self._require_workspace()
        drawing_path = workspace / "exports" / f"{safe_output_name(plan.name)}.drawing.json"
        drawing_path.parent.mkdir(parents=True, exist_ok=True)
        drawing_path.write_text(
            json.dumps(
                {
                    "plan": plan.name,
                    "units": plan.units,
                    "profile": profile.to_dict(),
                    "views": ["front", "top", "right"] + (["isometric"] if profile.include_isometric else []),
                    "annotation_status": self._drawing_annotation_status,
                    "note": "Mock drawing manifest. Generate the real drawing on Windows with SolidWorks.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return {"drawing_manifest": path_to_string(drawing_path)}

    def export_outputs(self, plan: ModelPlan, formats: tuple[str, ...]) -> dict[str, str]:
        """Create placeholder files so downstream agents can verify paths."""

        workspace = self._require_workspace() / "exports"
        workspace.mkdir(parents=True, exist_ok=True)
        outputs: dict[str, str] = {}
        base_name = safe_output_name(plan.name)
        for file_format in formats:
            output_path = workspace / f"{base_name}.{file_format.lower()}"
            output_path.write_text(
                f"Mock {file_format.upper()} export for {plan.name}\n",
                encoding="utf-8",
            )
            outputs[file_format] = path_to_string(output_path)
        return outputs

    def inspect_active_model(self) -> dict[str, Any]:
        """Return the recorded feature list for self-review."""

        return {
            "adapter": self.name,
            "active_document": self._active_plan.name if self._active_plan else None,
            "feature_count": len(self._features),
            "features": list(self._features),
            "thread_model_status": self._thread_model_status,
            "drawing_view_status": self._drawing_view_status,
            "drawing_annotation_status": self._drawing_annotation_status,
            "fallbacks": list(self._fallbacks),
            "warnings": list(self._warnings),
        }

    def capture_previews(self, plan: ModelPlan) -> dict[str, str]:
        """Create text placeholders for standard view previews."""

        workspace = self._require_workspace() / "previews"
        workspace.mkdir(parents=True, exist_ok=True)
        previews: dict[str, str] = {}
        for view_name in ("front", "top", "right", "isometric"):
            preview_path = workspace / f"{safe_output_name(plan.name)}_{view_name}_preview.txt"
            preview_path.write_text(
                f"Mock {view_name} preview for {plan.name}\n",
                encoding="utf-8",
            )
            previews[view_name] = path_to_string(preview_path)
        return previews

    def _require_workspace(self) -> Path:
        """Return the active transaction directory or fail with a useful error."""

        if self._workspace is None:
            raise RuntimeError("No active mock transaction. Call begin_transaction first.")
        return self._workspace
