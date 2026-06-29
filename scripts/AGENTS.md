# MODULE KNOWLEDGE BASE

## OVERVIEW

Operational gate scripts for schema checks, smoke runs, production release gates, offline diagnosis, and run-scoped cleanup. These scripts currently substitute for CI.

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| Fast schema/regression gate | `check_mounting_plate_schema.py` | Pure Python checks for schema, workflow policy, cleanup, diagnosis fixtures. |
| Main mock/real smoke | `smoke_mounting_plate.py` | Single scenarios, matrix, production suite, emergency cleanup. |
| Stable production suite wrapper | `smoke_production_workflows.py` | Entrypoint wrapper around smoke suite. |
| Release-level gate | `release_production_gate.py` | Batch trusted workflows plus batch diagnosis and release report. |
| Existing model drawing gate | `check_existing_model_manufacturing_drawing_gate.py` | Imported model + manufacturing drawing contract. |
| Offline run diagnosis | `diagnose_run.py`, `diagnose_runs.py` | Read-only artifact rechecks. |
| Release report diagnosis | `diagnose_release_gate.py` | Rechecks saved release report against current artifacts. |
| Post-run cleanup | `cleanup_run_documents.py` | Run-scoped remediation for still-open native docs. |

## CONVENTIONS

- Prefer mock gates while iterating. Real gates are for SolidWorks COM behavior and must be labeled as real evidence.
- `--summary-only` is the low-noise mode for release/smoke/diagnosis commands.
- Emergency cleanup after smoke failure only targets runs created by the current command and only completed run dirs with `execution_report.json`.
- Diagnosis scripts are offline: they should not connect to SolidWorks or modify CAD artifacts.
- Cleanup script prints structured JSON and exits non-zero when cleanup status or verification is not acceptable.

## ANTI-PATTERNS

- Do not edit generated output files to make these gates pass.
- Do not broaden a gate by deleting assertions; update the implementation and keep the evidence contract.
- Do not make release scripts depend on GitHub Actions state; no workflow files currently exist.
- Do not run a full real production suite as a first step for a small change. Use the smallest relevant mock gate first, then escalate.
- Do not hide interrupted/exceptional smoke failures; failure reports and emergency cleanup evidence are part of the contract.

## COMMANDS

```powershell
python scripts\check_mounting_plate_schema.py
python scripts\smoke_mounting_plate.py --mock
python scripts\smoke_mounting_plate.py --mock --matrix
python scripts\smoke_production_workflows.py --mock --production-suite --summary-only
python scripts\release_production_gate.py --mock --summary-only
python scripts\check_existing_model_manufacturing_drawing_gate.py
python scripts\diagnose_run.py <run_dir> --summary-only
python scripts\diagnose_runs.py outputs --summary-only
python scripts\diagnose_release_gate.py <release_gate_report.json> --summary-only
python scripts\cleanup_run_documents.py <run_dir>
```
