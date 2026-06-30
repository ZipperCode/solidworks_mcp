from __future__ import annotations

from solidworks_mcp.capabilities import get_capability_catalog
from solidworks_mcp.schemas import SUPPORTED_OPERATIONS


def test_external_mcp_adoption_entries_are_non_executable_backlog() -> None:
    catalog = get_capability_catalog()
    entries = {
        capability["id"]: capability
        for capability in catalog["categories"]["external_reference_adoption"]["capabilities"]
    }
    expected_ids = {
        "knowledge.solidworks_api_lookup",
        "diagnostics.com_strategy_trace",
        "macros.controlled_run_diagnostics",
        "drawing.template_diagnostics",
        "workflow.controlled_industry_templates",
        "exchange.neutral_file_inspection",
    }

    assert expected_ids <= set(entries)
    for capability_id in expected_ids:
        assert entries[capability_id]["status"] in {"planned", "research"}
        assert capability_id not in SUPPORTED_OPERATIONS


def test_external_reference_projects_capture_adoption_sources() -> None:
    catalog = get_capability_catalog()
    references = catalog["references"]

    assert "solidworks-api-mcp" in references
    assert "solidworks-macro-diagnostics-mcp" in references
    assert "SolidworksMCP-python" in references
    assert "solidworks-mcp-pro" in references
