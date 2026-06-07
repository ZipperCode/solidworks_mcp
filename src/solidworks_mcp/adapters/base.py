"""Abstract CAD adapter used by the execution engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from solidworks_mcp.schemas import DrawingProfile, ModelOperation, ModelPlan, StepResult


class CADAdapter(ABC):
    """Backend boundary between MCP tools and a CAD runtime."""

    name: str

    def set_run_workspace(self, run_dir: Path) -> None:
        """Receive the isolated run directory selected by the executor."""

        self._run_workspace = run_dir

    def set_debug_recorder(self, recorder: Any) -> None:
        """Receive the optional event recorder used for debug artifacts."""

        self._debug_recorder = recorder

    def record_event(
        self,
        name: str,
        status: str,
        details: dict[str, Any] | None = None,
        *,
        level: str = "basic",
        started_at: float | None = None,
    ) -> None:
        """Write an adapter event when a debug recorder is attached."""

        recorder = getattr(self, "_debug_recorder", None)
        if recorder is not None:
            recorder.event(name, status, details, level=level, started_at=started_at)

    def record_com_call(
        self,
        method: str,
        parameters: dict[str, Any] | None,
        *,
        result: Any = None,
        error: Exception | str | None = None,
        started_at: float | None = None,
    ) -> None:
        """Write a COM call summary when verbose debug logging is enabled."""

        recorder = getattr(self, "_debug_recorder", None)
        if recorder is not None:
            recorder.com_call(method, parameters, result=result, error=error, started_at=started_at)

    @abstractmethod
    def connect(self) -> dict[str, Any]:
        """Connect to the CAD application and return environment information."""

    @abstractmethod
    def preflight_environment(self, plan: ModelPlan | None = None) -> dict[str, Any]:
        """Check runtime prerequisites before starting a modeling transaction."""

    @abstractmethod
    def begin_transaction(self, plan: ModelPlan) -> dict[str, Any]:
        """Create or clone a document so execution does not mutate user files."""

    @abstractmethod
    def execute_operation(self, operation: ModelOperation, index: int, plan: ModelPlan) -> StepResult:
        """Execute one whitelisted modeling operation."""

    @abstractmethod
    def generate_drawing(self, plan: ModelPlan, profile: DrawingProfile) -> dict[str, str]:
        """Generate an engineering drawing for the active model."""

    @abstractmethod
    def export_outputs(self, plan: ModelPlan, formats: tuple[str, ...]) -> dict[str, str]:
        """Export the active model and drawing to requested file formats."""

    @abstractmethod
    def inspect_active_model(self) -> dict[str, Any]:
        """Return a compact model summary for AI self-review."""

    @abstractmethod
    def document_state_snapshot(self, phase: str) -> dict[str, Any]:
        """Return a best-effort snapshot of open CAD documents for cleanup auditing."""

    @abstractmethod
    def capture_previews(self, plan: ModelPlan) -> dict[str, str]:
        """Capture multiple preview images or mock placeholders."""

    @abstractmethod
    def cleanup_after_run(self, plan: ModelPlan | None = None) -> dict[str, Any]:
        """Release documents created by the current execution run."""

    @abstractmethod
    def cleanup_run_documents(self, run_dir: str | Path) -> dict[str, Any]:
        """Best-effort close open documents that belong to a completed run directory."""
