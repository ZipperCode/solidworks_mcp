"""FastMCP server exposing high-level SolidWorks automation tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from solidworks_mcp.adapters import create_adapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.executor import ModelPlanExecutor


def build_executor() -> ModelPlanExecutor:
    """Build the executor from environment configuration."""

    config = SolidWorksMCPConfig.from_env()
    return ModelPlanExecutor(create_adapter(config), config)


executor = build_executor()
mcp = FastMCP("solidworks-mcp")


@mcp.tool()
def connect_solidworks() -> dict[str, Any]:
    """Connect to SolidWorks or report the active mock adapter."""

    return executor.connect()


@mcp.tool()
def validate_model_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a restricted JSON model plan before user confirmation."""

    return executor.validate_plan(plan).to_dict()


@mcp.tool()
def execute_model_plan(plan: dict[str, Any], confirmed: bool = False) -> dict[str, Any]:
    """Execute a confirmed model plan in an isolated document transaction."""

    return executor.execute_plan(plan, confirmed=confirmed).to_dict()


@mcp.tool()
def generate_drawing(plan: dict[str, Any]) -> dict[str, Any]:
    """Generate an engineering drawing for the active model."""

    return executor.generate_drawing(plan).to_dict()


@mcp.tool()
def export_outputs(plan: dict[str, Any], formats: list[str] | None = None) -> dict[str, Any]:
    """Export the active model or drawing to requested formats."""

    return executor.export_outputs(plan, formats=formats).to_dict()


@mcp.tool()
def inspect_active_model() -> dict[str, Any]:
    """Inspect the active model and return an AI-readable summary."""

    return executor.inspect_active_model().to_dict()


def main() -> None:
    """Run the MCP server over stdio."""

    mcp.run()


if __name__ == "__main__":
    main()
