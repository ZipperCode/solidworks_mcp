# SolidWorks MCP

A minimal Python MCP server for AI-assisted SolidWorks modeling workflows.

The MVP exposes a small set of high-level tools that let an external MCP client
validate a restricted JSON modeling plan, execute it after user confirmation,
generate drawing outputs and return a compact self-review report.

## MVP scope

- Python MCP server over stdio.
- Windows SolidWorks COM execution through a dedicated adapter.
- macOS/Linux development through a deterministic mock adapter.
- Single-part mechanical modeling plans only.
- Confirmation required before model execution.
- Basic drawing/export/self-review reports after execution.

The project intentionally does not include an embedded LLM, chat UI, assembly
automation, sheet metal, weldments, simulation or 2D drawing-to-3D reconstruction
in the first version.

## Install

```bash
uv sync
```

For real SolidWorks execution on Windows:

```bash
uv sync --extra windows
```

## Run

Mock mode is the default outside Windows:

```bash
SOLIDWORKS_MCP_ADAPTER=mock uv run solidworks-mcp
```

Windows SolidWorks mode:

```powershell
$env:SOLIDWORKS_MCP_ADAPTER = "solidworks"
uv run solidworks-mcp
```

See `docs/mcp-client-config.md` for MCP client examples.

## Protocol catalog

The executable MCP tool surface intentionally stays small.  Future SolidWorks
abilities are documented through read-only discovery interfaces instead of
placeholder tools:

- `solidworks://capabilities`
- `solidworks://capabilities/{category}`
- prompt `plan_solidworks_operation`

See `docs/protocol-catalog.md` for the human-readable catalog covering sketch,
part modeling, drawing, export, assembly, properties, templates/macros and
diagnostics.  Capabilities marked `planned`, `research` or `blocked` are for
design discussion only and must not be submitted to `execute_model_plan`.

## Model plan

Modeling input is a restricted JSON operation list.  See
`examples/flange_plan.json` for a complete starter plan.

Supported MVP operation names:

- `create_mounting_plate`
- `create_sketch`
- `extrude`
- `cut`
- `hole`
- `fillet`
- `chamfer`
- `linear_pattern`
- `circular_pattern`
- `assign_material`
- `make_drawing`

Some operations are schema-supported before full Windows COM implementation so
the plan contract can stay stable while adapter coverage grows.

## Mounting plate smoke workflow

The first real SolidWorks smoke workflow is a high-level mounting plate:

- `120 x 80 x 10 mm`
- four rounded corners, `R5`
- four `M6` ISO metric coarse threaded through holes
- hole centers offset `15 mm` from the plate edges
- drawing export with front/top/right/isometric views and best-effort hole callouts

Run the local mock smoke flow:

```bash
python3 scripts/smoke_mounting_plate.py --mock
```

Run on Windows with SolidWorks:

```powershell
$env:SOLIDWORKS_MCP_ADAPTER = "solidworks"
$env:SOLIDWORKS_MCP_OUTPUT_DIR = "C:\SolidWorksMCP\outputs"
python scripts\smoke_mounting_plate.py
```

The execution writes `execution_report.json` into the output directory.  If
HoleWizard cannot create a threaded hole, the adapter falls back to tap-drill
geometry cuts and records `thread_model_status=degraded_geometry_only`.

## Debug artifacts

Every confirmed execution creates an isolated run directory:

```text
outputs/<plan_name>/run_<timestamp>_<id>/
```

The run directory contains:

- `plan.normalized.json`
- `execution_report.json`
- `events.jsonl`
- `environment.json`
- `artifacts.json`
- `exports/`
- `previews/`
- `macros/`

Basic structured events are enabled by default.  To include SolidWorks COM call
summaries, set:

```bash
SOLIDWORKS_MCP_DEBUG_LEVEL=verbose
```

To diagnose a copied run directory without connecting to SolidWorks:

```bash
python3 scripts/diagnose_run.py outputs/m6_mounting_plate/run_<timestamp>_<id>
```
