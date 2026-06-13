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
    def run_command(self, command_id: int, command_string: str = "") -> dict[str, Any]:
        """Execute a SolidWorks command by numeric command id."""

    @abstractmethod
    def list_commands(self, category_filter: str | None = None) -> dict[str, Any]:
        """List available SolidWorks commands, optionally filtered by category."""

    @abstractmethod
    def list_open_documents(self) -> dict[str, Any]:
        """List all currently open CAD documents."""

    @abstractmethod
    def get_document_info(self, title: str | None = None) -> dict[str, Any]:
        """Get information about a named document or the active document."""

    @abstractmethod
    def activate_document(self, title: str) -> dict[str, Any]:
        """Switch the CAD application to a specific open document."""

    @abstractmethod
    def close_document(self, title: str) -> dict[str, Any]:
        """Close a specific open document when it is safe to do so."""

    @abstractmethod
    def get_feature_tree(self, max_depth: int = 5) -> dict[str, Any]:
        """Traverse the active document feature tree up to the requested depth."""

    @abstractmethod
    def select_by_id(
        self,
        name: str,
        type: str,
        mark: int = 2,
        x: float = 0,
        y: float = 0,
        z: float = 0,
        append: bool = False,
        mark_option: int = 1,
    ) -> dict[str, Any]:
        """Select an entity by SolidWorks selection id and type."""

    @abstractmethod
    def get_selected_objects(self) -> dict[str, Any]:
        """Return the current CAD selection set."""

    @abstractmethod
    def get_mass_properties(self) -> dict[str, Any]:
        """Get mass properties for the active model."""

    @abstractmethod
    def setup_simulation_study(self, study_name: str = "Static 1", study_type: str = "static") -> dict[str, Any]:
        """Create/setup a simulation study on the active model."""

    @abstractmethod
    def apply_simulation_material(self, material_name: str) -> dict[str, Any]:
        """Apply a material to the active model for simulation."""

    @abstractmethod
    def add_simulation_fixture(self, fixture_type: str, entity_name: str, entity_type: str) -> dict[str, Any]:
        """Add a fixture (constraint) to the simulation study."""

    @abstractmethod
    def add_simulation_load(
        self,
        load_type: str,
        entity_name: str,
        entity_type: str,
        magnitude: float,
        direction: list[float] | None = None,
    ) -> dict[str, Any]:
        """Add a load to the simulation study."""

    @abstractmethod
    def run_simulation_mesh_and_solve(self) -> dict[str, Any]:
        """Mesh and solve the active simulation study."""

    @abstractmethod
    def get_simulation_results(self) -> dict[str, Any]:
        """Get results from the completed simulation study."""

    @abstractmethod
    def check_interference(self, component_selectors: list[str] | None = None) -> dict[str, Any]:
        """Run interference detection on the active assembly."""

    @abstractmethod
    def create_exploded_view(self, name: str = "ExplodedView1") -> dict[str, Any]:
        """Create an exploded view of the active assembly."""

    @abstractmethod
    def get_assembly_component_tree(self) -> dict[str, Any]:
        """Return the assembly component hierarchy with mate information."""

    @abstractmethod
    def add_dimxpert_dimension(
        self,
        entity_name: str,
        entity_type: str,
        dimension_type: str,
        x: float = 0,
        y: float = 0,
        z: float = 0,
    ) -> dict[str, Any]:
        """Add a DimXpert dimension to the active part."""

    @abstractmethod
    def add_dimxpert_tolerance(self, dimension_name: str, tolerance_type: str, upper: float, lower: float) -> dict[str, Any]:
        """Add a tolerance to a DimXpert dimension."""

    @abstractmethod
    def list_dimxpert_dimensions(self) -> dict[str, Any]:
        """List all DimXpert dimensions in the active part."""

    @abstractmethod
    def list_configurations(self) -> dict[str, Any]:
        """List all configurations in the active model."""

    @abstractmethod
    def activate_configuration(self, config_name: str) -> dict[str, Any]:
        """Activate a specific configuration."""

    @abstractmethod
    def add_configuration(self, config_name: str, comment: str = "", options: int = 0) -> dict[str, Any]:
        """Add a new configuration to the active model."""

    @abstractmethod
    def list_equations(self) -> dict[str, Any]:
        """List all equations and global variables in the active model."""

    @abstractmethod
    def set_equation(self, equation_str: str) -> dict[str, Any]:
        """Add or modify an equation/global variable in the active model."""

    @abstractmethod
    def subscribe_events(self, event_types: list[str]) -> dict[str, Any]:
        """Subscribe to SolidWorks COM events. Returns event listener status."""

    @abstractmethod
    def unsubscribe_events(self) -> dict[str, Any]:
        """Unsubscribe from all SolidWorks COM events."""

    @abstractmethod
    def get_event_log(self, max_events: int = 50) -> dict[str, Any]:
        """Return recent SolidWorks events that were captured."""

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
    def insert_drawing_bom_table(self, view_name: str | None = None, template_path: str | None = None) -> dict[str, Any]:
        """Insert a BOM table into the active drawing."""

    @abstractmethod
    def insert_drawing_center_mark(self, entity_type: str, x: float, y: float, z: float = 0.0) -> dict[str, Any]:
        """Insert a center mark on a circular edge in the active drawing."""

    @abstractmethod
    def insert_drawing_centerline(
        self,
        entity_type: str,
        x1: float,
        y1: float,
        z1: float,
        x2: float,
        y2: float,
        z2: float,
    ) -> dict[str, Any]:
        """Insert a centerline between two entities in the active drawing."""

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
    def read_document_properties_offline(self, file_path: str, configuration: str | None = None) -> dict[str, Any]:
        """Read custom properties from a SolidWorks document without opening SolidWorks."""

    @abstractmethod
    def write_document_properties_offline(
        self,
        file_path: str,
        properties: dict[str, str],
        configuration: str | None = None,
    ) -> dict[str, Any]:
        """Write custom properties to a SolidWorks document without opening SolidWorks."""

    @abstractmethod
    def read_document_configurations_offline(self, file_path: str) -> dict[str, Any]:
        """List configurations in a SolidWorks document without opening SolidWorks."""

    @abstractmethod
    def read_document_bom_offline(self, file_path: str) -> dict[str, Any]:
        """Read BOM components from a SolidWorks assembly document without opening SolidWorks."""

    @abstractmethod
    def cleanup_run_documents(self, run_dir: str | Path) -> dict[str, Any]:
        """Best-effort close open documents that belong to a completed run directory."""
