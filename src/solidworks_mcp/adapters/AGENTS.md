# MODULE KNOWLEDGE BASE

## OVERVIEW

Adapter boundary for CAD execution: `base.py` defines the contract, `mock.py` provides deterministic offline evidence, and `solidworks.py` is the Windows COM backend.

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| Adapter interface | `base.py` | Executor depends on these methods. Keep return payloads stable. |
| Offline behavior | `mock.py` | Must exercise gates without SolidWorks and support forced-failure env switches. |
| Real COM behavior | `solidworks.py` | Drawing, export, selection, material, cleanup, and path guarding live here. |
| Adapter selection | `__init__.py` | `create_adapter` maps config to backend. |

## CONVENTIONS

- Mock and real adapters should report comparable diagnostic keys when possible; gates depend on key names.
- Real adapter units are SolidWorks/COM-sensitive. Preserve explicit mm/m conversions and do not infer from display text.
- SolidWorks selection must be explicit and replayable. Prefer named plane/entity selectors and feature graph evidence over active UI selection state.
- Cleanup only closes run-created documents whose resolved path belongs to the current run workspace.
- Post-run cleanup is attach-only by default: `SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY=1` must not launch a new SolidWorks instance.
- Direct hole callouts are required for trusted hole evidence; hole tables are fallback diagnostics only.

## ANTI-PATTERNS

- Do not close documents by title/stem unless the candidate resolves inside `run_dir`.
- Do not treat generated `.swb` macros as runnable proof; SW2022 has known generated-text macro failures.
- Do not accept proxy dimensions or sketch fallback dimensions as trusted dimensions.
- Do not use mock success to claim real COM behavior.
- Do not add silent fallbacks that hide SolidWorks COM/RPC errors from diagnostics.
- Do not make cleanup failures non-fatal for production acceptance.

## VERIFICATION

```powershell
python scripts\check_mounting_plate_schema.py
python scripts\smoke_mounting_plate.py --mock
python scripts\smoke_production_workflows.py --mock --production-suite --summary-only
```

Real adapter changes need a real SolidWorks smoke/gate with Windows dependencies installed:

```powershell
uv sync --extra windows
$env:SOLIDWORKS_MCP_ADAPTER = "solidworks"
$env:SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN = "1"
$env:SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY = "1"
$env:SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW = "1"
$env:SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT = "1"
python scripts\smoke_mounting_plate.py --summary-only
```
