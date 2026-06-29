# MODULE KNOWLEDGE BASE

## OVERVIEW

JSON `ModelPlan` examples for controlled workflows. These files are input contracts and smoke fixtures, not implementation logic.

## WHERE TO LOOK

| Workflow | File |
| --- | --- |
| Mounting plate | `mounting_plate_plan.json` |
| Center-hole flange | `flange_plan.json` |
| Center-hole plate | `center_hole_plate_plan.json` |
| Bracket | `bracket_plan.json` |
| End cap | `end_cap_plan.json` |
| Mounting block | `mounting_block_plan.json` |
| Shaft | `shaft_plan.json` |
| Washer | `washer_plan.json` |
| Sleeve | `sleeve_plan.json` |
| Slotted array plate | `slotted_array_plate_plan.json` |
| Sheet metal base flange | `sheet_metal_base_flange_plan.json` |
| Weldment frame | `weldment_frame_plan.json` |
| BOM assembly | `bom_assembly_plan.json` |
| Explicit simulation | `simulation_cantilever_plan.json` |

## CONVENTIONS

- Keep JSON valid and minimal; examples should be easy to pass to validation/smoke commands.
- Match operation names and parameter bounds from `src/solidworks_mcp/schemas.py`.
- Keep descriptions aligned with trusted workflow terms used in `README.md` and `docs/protocol-catalog.md`.
- `simulation_cantilever_plan.json` is explicit-only unless the current gates say otherwise.
- Examples should not include absolute user machine paths unless the workflow specifically requires an imported existing model fixture.

## ANTI-PATTERNS

- Do not use examples to bypass schema or trusted workflow policy.
- Do not add freeform operations to a production example and still call it trusted.
- Do not make examples depend on generated files under `outputs/` or `test_outputs*/`.
- Do not mark optional exports as required unless the gate expects them.

## VERIFICATION

```powershell
python -m json.tool examples\mounting_plate_plan.json > $null
python -m json.tool examples\flange_plan.json > $null
python scripts\check_mounting_plate_schema.py
python scripts\smoke_mounting_plate.py --mock --production-suite --production-scenario combined --summary-only
```
