"""Adapter factory for mock and real SolidWorks execution backends."""

from solidworks_mcp.adapters.base import CADAdapter
from solidworks_mcp.adapters.mock import MockCADAdapter
from solidworks_mcp.adapters.solidworks import SolidWorksCOMAdapter
from solidworks_mcp.config import SolidWorksMCPConfig


def create_adapter(config: SolidWorksMCPConfig) -> CADAdapter:
    """Create the configured CAD adapter.

    The factory keeps platform-specific imports behind the adapter boundary, so
    macOS development can still validate schemas and MCP wiring without COM.
    """

    if config.adapter == "mock":
        return MockCADAdapter(config)
    if config.adapter == "solidworks":
        return SolidWorksCOMAdapter(config)
    raise ValueError(f"Unsupported CAD adapter: {config.adapter}")


__all__ = ["CADAdapter", "MockCADAdapter", "SolidWorksCOMAdapter", "create_adapter"]

