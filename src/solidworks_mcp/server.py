"""FastMCP server exposing high-level SolidWorks automation protocol surfaces."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from solidworks_mcp.adapters import create_adapter
from solidworks_mcp.capabilities import capability_catalog_json, capability_category_json
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
    """Connect to SolidWorks or report the active mock adapter.

    Use this before planning a real Windows run so the MCP client can confirm
    whether the server is using the mock adapter or SolidWorks COM.  The result
    is a small environment summary, not a modeling report, and it does not
    create a run directory.
    """

    return executor.connect()


@mcp.tool()
def validate_model_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a restricted JSON model plan before user confirmation.

    This is the schema gate for AI-generated ``ModelPlan`` payloads.  It checks
    units, export formats, required fields, and the current executable operation
    whitelist.  Planned capabilities from the capability catalog must be kept
    out of this plan until they are promoted into ``SUPPORTED_OPERATIONS``.
    """

    return executor.validate_plan(plan).to_dict()


@mcp.tool()
def execute_model_plan(plan: dict[str, Any], confirmed: bool = False) -> dict[str, Any]:
    """Execute a confirmed model plan in an isolated document transaction.

    Call this only after the user has reviewed a validated plan and the client
    passes ``confirmed=true``.  Every confirmed run writes a dedicated run
    directory containing ``plan.normalized.json``, ``execution_report.json``,
    ``events.jsonl``, ``environment.json``, and ``artifacts.json`` for later
    failure diagnosis.
    """

    return executor.execute_plan(plan, confirmed=confirmed).to_dict()


@mcp.tool()
def generate_drawing(plan: dict[str, Any]) -> dict[str, Any]:
    """Generate an engineering drawing for the active model.

    Use this when a model already exists in the active adapter session and the
    client wants to retry or refresh drawing creation from the plan's
    ``DrawingProfile``.  Drawing annotation failures are reported as diagnostics
    instead of hiding the model or export status.
    """

    return executor.generate_drawing(plan).to_dict()


@mcp.tool()
def export_outputs(plan: dict[str, Any], formats: list[str] | None = None) -> dict[str, Any]:
    """Export the active model or drawing to requested formats.

    Use this to retry file exports from the active adapter session.  The optional
    ``formats`` argument overrides the plan's output format list for this call;
    unsupported formats still fail validation through the plan/export schema.
    """

    return executor.export_outputs(plan, formats=formats).to_dict()


@mcp.tool()
def inspect_active_model() -> dict[str, Any]:
    """Inspect the active model and return an AI-readable summary.

    Use this after execution, drawing, or export to collect feature summaries,
    fallback states, warnings, and annotation status.  The response is intended
    for self-review and repair planning rather than as certified CAD validation.
    """

    return executor.inspect_active_model().to_dict()


@mcp.resource(
    "solidworks://capabilities",
    title="SolidWorks MCP Capability Catalog",
    description="Read-only JSON catalog of available, planned, research, and blocked SolidWorks MCP protocol abilities.",
    mime_type="application/json",
)
def solidworks_capabilities() -> str:
    """Return the complete read-only protocol capability catalog as JSON."""

    return capability_catalog_json()


@mcp.resource(
    "solidworks://capabilities/{category}",
    title="SolidWorks MCP Capability Category",
    description="Read-only JSON catalog entry for a single SolidWorks MCP capability category.",
    mime_type="application/json",
)
def solidworks_capability_category(category: str) -> str:
    """Return one capability category as JSON, including an error payload for unknown names."""

    return capability_category_json(category)


@mcp.prompt(
    name="plan_solidworks_operation",
    title="Plan SolidWorks Operation",
    description="Guide an AI client to draft a safe ModelPlan using the capability catalog.",
)
def plan_solidworks_operation(user_request: str, capability_category: str | None = None) -> str:
    """Return prompt guidance for converting a user request into a safe ModelPlan."""

    category_hint = (
        f"\nFocus capability category: {capability_category}."
        if capability_category
        else "\nUse solidworks://capabilities to inspect the full catalog before planning."
    )
    return f"""You are planning a SolidWorks MCP operation.

User request:
{user_request}
{category_hint}

Workflow:
1. Read solidworks://capabilities or solidworks://capabilities/{{category}} to separate available, planned, research, and blocked abilities.
2. Draft a ModelPlan only with operations currently accepted by validate_model_plan.
3. Treat planned, research, and blocked capabilities as design-discussion notes only; never submit them to execute_model_plan.
4. Prefer high-level operations such as create_mounting_plate when they match the request.
5. Explain any fallback risks, drawing annotation limits, and export expectations before asking the user to confirm execution.
6. After confirmed execution, review execution_report.json, events.jsonl, artifacts.json, previews, and diagnostics before declaring success.

Return a concise plan summary first, then the candidate ModelPlan JSON if it is executable today."""


def main() -> None:
    """Run the MCP server over stdio."""

    mcp.run()


if __name__ == "__main__":
    main()
