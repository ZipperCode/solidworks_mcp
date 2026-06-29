# MODULE KNOWLEDGE BASE

## OVERVIEW

Project documentation for MCP client setup, protocol/catalog policy, and SolidWorks API expansion analysis. Docs are operator contracts; they must match executable behavior.

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| MCP client setup and operator flow | `mcp-client-config.md` | Environment variables, client behavior, diagnosis usage. |
| Capability and gate policy | `protocol-catalog.md` | Tool/resource catalog, maturity states, trusted workflow rules. |
| API expansion research | `analysis/solidworks-api-expansion-report.md` | Research/roadmap context, not proof of current availability. |

## CONVENTIONS

- Keep capability status words exact: `available`, `planned`, `research`, `blocked`.
- When docs say a workflow is trusted/accepted, verify matching code/gate behavior in `src/solidworks_mcp` and `scripts`.
- Preserve hard product terms: trusted workflow policy, preflight gate, cleanup gate, direct hole callout policy, production verdict, offline diagnosis.
- Use Windows/PowerShell examples for real SolidWorks execution paths.
- For imported existing models, document the isolated `run_dir` copy behavior and avoid implying source files are modified in place.

## ANTI-PATTERNS

- Do not promote roadmap/research notes to executable capability claims.
- Do not document mock evidence as real SolidWorks evidence.
- Do not remove warnings about `confirmed=true`, cleanup, or trusted workflow enforcement to make the workflow look simpler.
- Do not say old accepted run artifacts remain accepted without current `diagnose_run` recheck.
- Do not treat `docs/analysis/` as the source of truth for current gates.

## VERIFICATION

```powershell
python scripts\release_production_gate.py --mock --summary-only
python scripts\diagnose_release_gate.py <release_gate_report.json> --summary-only
```

For docs-only edits, validate that commands, environment variables, capability names, and workflow status strings still exist in code or README. Avoid adding user-facing promises that do not have a gate.
