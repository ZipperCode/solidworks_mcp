# MODULE KNOWLEDGE BASE

## OVERVIEW

Core package for the MCP server, executable plan contract, executor, session state, capability catalog, diagnostics, repair routing, and adapter boundary.

## STRUCTURE

```text
src/solidworks_mcp/
├── server.py              # MCP tools/resources/prompts and runtime entry
├── schemas.py             # ModelPlan, operations, validation errors
├── executor.py            # confirmed execution, production verdicts, artifact gates
├── sessions.py            # atomic modeling session state
├── feature_graph.py       # named feature graph replay/evidence
├── capabilities.py        # machine-readable capability maturity catalog
├── run_diagnostics.py     # offline run artifact recheck
├── release_diagnostics.py # release gate report recheck
├── repair.py              # repair action summaries
├── debug.py               # run workspace, event, manifest helpers
├── config.py              # env-only runtime config
└── adapters/              # adapter contract and backends
```

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| Add/change MCP tool | `server.py` | Keep server thin; delegate execution and diagnosis logic. |
| Add/change operation schema | `schemas.py` | Update validation, trusted dimension IDs, examples, and gates together. |
| Change production acceptance | `executor.py` | Recheck `run_diagnostics.py` and `release_diagnostics.py`; saved runs are re-evaluated by current rules. |
| Change atomic modeling | `sessions.py`, `feature_graph.py`, `executor.py` | Feature graph evidence is part of trusted workflow proof. |
| Change capability maturity | `capabilities.py`, `docs/protocol-catalog.md` | Keep `available/planned/research/blocked` aligned. |
| Change artifacts/manifest/events | `debug.py`, `run_diagnostics.py` | Diagnosis validates hashes, relative paths, event termination, and environment snapshot. |

## CONVENTIONS

- `SolidWorksMCPConfig.from_env()` is the runtime config source; do not add hidden alternate config paths casually.
- `ModelPlanExecutor.execute_plan()` is the confirmed path. Preserve `confirmed=true` requirement and preflight-before-transaction ordering.
- `ExecutionReport.to_dict()` exposes `production_verdict`; clients should not need to dig through raw diagnostics for the top-level verdict.
- When adding a production gate field, update both live acceptance and offline diagnosis, or old accepted artifacts can mask missing evidence.
- `available` means executable; `planned`, `research`, and `blocked` are design/catalog states only.

## ANTI-PATTERNS

- Do not put real COM calls in `server.py`; keep COM behind adapters.
- Do not make `validate_plan()` mutate SolidWorks state.
- Do not broaden trusted workflow acceptance by only changing schema validation. Trusted workflow policy is separate from schema validity.
- Do not remove or downgrade cleanup/document-state evidence from reports; it is part of production acceptance.
- Do not make offline diagnosis connect to SolidWorks or modify CAD files.

## VERIFICATION

```powershell
python scripts\check_mounting_plate_schema.py
python scripts\release_production_gate.py --mock --summary-only
python scripts\diagnose_run.py <run_dir> --summary-only
```

For schema or executor changes, run the smallest relevant gate first, then the mock release gate. For real SolidWorks changes, label mock evidence as mock and run an explicit real smoke/gate before claiming COM behavior.
