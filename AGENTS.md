# PROJECT KNOWLEDGE BASE

**Generated:** 2026-06-29
**Commit:** 015fda0
**Branch:** main

## OVERVIEW

SolidWorks MCP is a Python 3.11+ MCP server for controlled SolidWorks automation. The product boundary is not open-ended CAD scripting: production claims require trusted workflows, explicit confirmation, preflight, run artifacts, cleanup evidence, and offline diagnosis.

## STRUCTURE

```text
solidworks_mcp/
├── src/solidworks_mcp/          # MCP tools, schemas, executor, diagnostics, capability catalog
│   └── adapters/                # CADAdapter contract, mock backend, real SolidWorks COM backend
├── scripts/                     # project-specific smoke, release gate, diagnosis, cleanup commands
├── docs/                        # protocol/client/operator rules; not generated API reference
├── examples/                    # JSON ModelPlan input contracts for controlled workflows
├── outputs/                     # generated run artifacts; evidence area, not source
├── test_outputs*/               # historical/mock/real validation artifacts; do not treat as source
├── test_dimension_generation.py # root-level focused tests, no tests/ package yet
├── test_e2e_prismatic_dimension.py
└── test_infrared_guard_drawing.py
```

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| MCP tool surface | `src/solidworks_mcp/server.py` | `solidworks-mcp = solidworks_mcp.server:main`; tools funnel into executor/diagnostics. |
| Plan schema and safety contract | `src/solidworks_mcp/schemas.py` | Operation whitelist, geometry bounds, validation errors. |
| Execution and production verdict | `src/solidworks_mcp/executor.py` | Validate -> preflight -> transaction -> operations -> drawing/export/preview -> cleanup -> acceptance. |
| Capability maturity | `src/solidworks_mcp/capabilities.py`, `docs/protocol-catalog.md` | Separate `available`, `planned`, `research`, and `blocked`; do not infer from README alone. |
| Real SolidWorks behavior | `src/solidworks_mcp/adapters/solidworks.py` | COM, templates, drawing, export, cleanup, path guards. |
| Offline/dev behavior | `src/solidworks_mcp/adapters/mock.py` | Deterministic mock outputs and forced-failure switches. |
| Run/release diagnosis | `src/solidworks_mcp/run_diagnostics.py`, `src/solidworks_mcp/release_diagnostics.py` | Recheck saved evidence using current gates. |
| Fast local gates | `scripts/check_mounting_plate_schema.py`, `scripts/release_production_gate.py` | Scripts are the current CI substitute. |
| Existing imported model drawing gate | `scripts/check_existing_model_manufacturing_drawing_gate.py` | Use for `import_existing_model` + manufacturing drawing contracts. |

## CODE MAP

| Symbol | Type | Location | Refs | Role |
| --- | --- | --- | ---: | --- |
| `main` | function | `src/solidworks_mcp/server.py:632` | high | Starts MCP stdio server and exposes tools/resources/prompts. |
| `build_executor` | function | `src/solidworks_mcp/server.py:19` | high | Runtime config + adapter factory + atomic session registry. |
| `ModelPlanExecutor` | class | `src/solidworks_mcp/executor.py:173` | 18 | Central validation, execution, cleanup, artifact, and verdict pipeline. |
| `execute_plan` | method | `src/solidworks_mcp/executor.py:254` | high | Only confirmed execution path; writes run artifacts. |
| `ModelPlan` | schema | `src/solidworks_mcp/schemas.py` | high | Executable plan contract consumed by executor/scripts/adapters. |
| `create_adapter` | function | `src/solidworks_mcp/adapters/__init__.py` | 17 | Selects `mock` vs `solidworks` backend. |
| `CADAdapter` | base class | `src/solidworks_mcp/adapters/base.py` | 4 | Adapter interface; changes affect executor and both backends. |
| `MockCADAdapter` | class | `src/solidworks_mcp/adapters/mock.py` | 5 | Offline/CI-compatible backend and forced failure diagnostics. |
| `SolidWorksCOMAdapter` | class | `src/solidworks_mcp/adapters/solidworks.py:332` | 4 | Real Windows COM backend; largest blast radius. |
| `diagnose_run_directory` | function | `src/solidworks_mcp/run_diagnostics.py` | high | Offline run recheck; does not connect to SolidWorks. |
| `release_production_gate.main` | function | `scripts/release_production_gate.py` | gate | Batch trusted workflow gate plus batch diagnosis. |
| `smoke_mounting_plate.main` | function | `scripts/smoke_mounting_plate.py` | gate | Mock/real smoke, matrix, production suite, emergency cleanup. |

## CONVENTIONS

- Use `uv sync` for base setup and `uv sync --extra windows` before real SolidWorks COM work.
- Default development path is mock adapter. Real SolidWorks runs require `SOLIDWORKS_MCP_ADAPTER=solidworks` and Windows COM availability.
- `execute_model_plan` still requires `confirmed=true`; schema-valid is not execution-ready.
- Every confirmed run writes a run directory with `execution_report.json`, `delivery_manifest.json`, `events.jsonl`, `environment.json`, `artifacts.json`, `exports/`, `previews/`, and sometimes `macros/`.
- Treat `scripts/*.py` as project gates, not miscellaneous helpers. They encode current release criteria.
- `outputs/`, `test_outputs/`, `test_outputs_real/`, and `test_outputs_real_no_stl/` are generated evidence/history. Do not edit them to make a gate pass.

## ANTI-PATTERNS (THIS PROJECT)

- Do not treat `ok=true` from validation as permission to execute or deliver; check `production_readiness_status`, run preflight, then verify the production verdict.
- Do not claim planned/research/blocked capabilities are available. Capability status must come from the catalog/code, not wishful README wording.
- Do not submit freeform `ModelPlan` operations or spoofed atomic metadata as trusted production workflows.
- Do not claim export success is production success. The gate also needs geometry, mass, direct hole callouts, trusted dimensions, artifact content, cleanup, and document-state evidence.
- Do not accept `proxy_dimension=true`, `radius_proxy_used`, or hole tables as trusted dimensions. They are diagnostic fallback/failure evidence.
- Do not use `OpenDoc6` alone to validate STEP/DWG exchange files; this project expects import-data based validation (`GetImportFileData` + `LoadFile4`) for those paths.
- Do not close arbitrary user SolidWorks documents. Cleanup is run-scoped and path-guarded.

## UNIQUE STYLES

- The repository is code plus real CAD evidence, not a pure package. Large generated artifacts may be useful as evidence but are not implementation sources.
- Production vocabulary is stable: trusted workflow policy, preflight gate, cleanup gate, direct hole callout policy, production verdict, run diagnosis.
- Mock and real SolidWorks evidence must be labeled separately. Passing mock gates does not prove real COM behavior.

## COMMANDS

```powershell
uv sync
uv sync --extra windows
$env:SOLIDWORKS_MCP_ADAPTER = "mock"; uv run solidworks-mcp
python scripts\check_mounting_plate_schema.py
python scripts\smoke_mounting_plate.py --mock
python scripts\smoke_mounting_plate.py --mock --matrix
python scripts\smoke_production_workflows.py --mock --production-suite --summary-only
python scripts\release_production_gate.py --mock --summary-only
python scripts\diagnose_run.py <run_dir> --summary-only
python scripts\diagnose_runs.py outputs --summary-only
python scripts\diagnose_release_gate.py <release_gate_report.json> --summary-only
```

## NOTES

- No `.github/workflows` is currently present; release confidence is script-driven.
- LSP status may show no active client until a Python file is opened; CodeGraph has indexed the main architecture.
- Real SolidWorks production runs commonly need `SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN=1`, `SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY=1`, `SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW=1`, and `SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT=1`.
- For imported existing models, the production path is `import_existing_model` followed by `make_drawing`; source files are copied into an isolated `run_dir`, not modified in place.
