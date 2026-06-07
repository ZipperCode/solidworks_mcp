# MCP Client Configuration

This server uses stdio transport through the official Python MCP SDK.

## Discovery surfaces

The server keeps the executable tool surface limited to high-level tools,
including the atomic session protocol, and also exposes read-only planning
helpers:

- resource `solidworks://capabilities`
- resource `solidworks://capabilities/{category}`
- resource `solidworks://preflight/environment`
- prompt `plan_solidworks_operation`

Use these discovery surfaces to decide whether a requested SolidWorks ability is
currently executable, planned, research-only or blocked.  Only executable
operations accepted by `validate_model_plan` should be submitted to
`execute_model_plan`, and production clients must treat
`diagnostics.production_readiness_status` as the early trust signal:
`trusted_workflow_ready` can continue to preflight, while
`blocked_by_trusted_workflow_policy` is schema-valid but non-production.

For atomic modeling, use `start_model_session`, `apply_model_operation`,
`finalize_model_session`, and `abort_model_session` instead of inventing a large
set of per-feature MCP tools. The session feature graph assigns stable ids for
built-in planes/axes and created sketches/entities/features/dimensions.
`finalize_model_session` preserves the same safety contract as
`execute_model_plan`: no CAD document is created unless the client passes
`confirmed=true`, and executor preflight replays the graph so missing or spoofed
references fail before `adapter.transaction`.

## Local mock mode

Use mock mode on macOS or Linux while developing prompts, schemas and client
wiring:

```json
{
  "mcpServers": {
    "solidworks-mcp": {
      "command": "uv",
      "args": ["run", "solidworks-mcp"],
      "env": {
        "SOLIDWORKS_MCP_ADAPTER": "mock",
        "SOLIDWORKS_MCP_OUTPUT_DIR": "outputs"
      }
    }
  }
}
```

## Windows SolidWorks mode

Run this on a Windows machine with SolidWorks installed:

```json
{
  "mcpServers": {
    "solidworks-mcp": {
      "command": "C:\\\\path\\\\to\\\\venv\\\\Scripts\\\\python.exe",
      "args": ["-m", "solidworks_mcp"],
      "env": {
        "SOLIDWORKS_MCP_ADAPTER": "solidworks",
        "SOLIDWORKS_MCP_OUTPUT_DIR": "C:\\\\SolidWorksMCP\\\\outputs",
        "SOLIDWORKS_MCP_PART_TEMPLATE": "C:\\\\ProgramData\\\\SOLIDWORKS\\\\SOLIDWORKS 2025\\\\templates\\\\Part.prtdot",
        "SOLIDWORKS_MCP_DRAWING_TEMPLATE": "C:\\\\ProgramData\\\\SOLIDWORKS\\\\SOLIDWORKS 2025\\\\templates\\\\Drawing.drwdot",
        "SOLIDWORKS_MCP_MACRO_FALLBACK": "1",
        "SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN": "1",
        "SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY": "1",
        "SOLIDWORKS_MCP_DEBUG_LEVEL": "basic"
      }
    }
  }
}
```

The real adapter requires `pip install "solidworks-mcp[windows]"` so pywin32
and comtypes are available.

Environment preflight is now a trusted gate.  Clients can call the
`preflight_environment` tool or read the `solidworks://preflight/environment`
resource before execution.  `execute_model_plan` also runs the same checks
internally and stops before the modeling transaction if SolidWorks COM,
templates, or the output directory are not ready.  Use
`SOLIDWORKS_MCP_FORCE_PREFLIGHT_FAILURE=1` to verify that hard-block path.
Trusted workflow enforcement is also enabled by default through
`SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW=1`: schema-valid freeform plans are
blocked at preflight before a SolidWorks document is created unless the variable
is set to `0` for a non-production experiment.
`create_center_hole_flange` is the second controlled production workflow,
`create_center_hole_plate` is the third, `create_bracket` is the fourth,
`create_end_cap` is the fifth, `create_mounting_block` is the sixth,
`create_shaft` is the seventh, `create_washer` is the eighth,
`create_sleeve` is the ninth, and `create_slotted_array_plate` is the tenth.
They validate model-side geometry/mass readback plus real drawing Hole Callout
and trusted basic display dimensions for their controlled geometry.
Session-produced atomic plans are the controlled atomic workflow. Direct
freeform `ModelPlan` feature operations remain non-production unless trusted
workflow enforcement is explicitly disabled for experiments.

Macro fallback is narrow and enabled by default.  It only writes and attempts a
controlled HoleWizard macro for ISO metric coarse `M3/M4/M5/M6/M8` four-corner
through holes.  Use `SOLIDWORKS_MCP_DISABLE_MACRO_EXECUTION=1` for degradation
regression tests, and `SOLIDWORKS_MCP_FORCE_HOLEWIZARD_FAILURE=1` to force the
macro/fallback branch during smoke validation.

Run cleanup is enabled by default with
`SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN=1`.  After exports and previews are
captured, the real adapter closes the part and drawing documents it created for
that run, first by the tracked SolidWorks title and then by saved output file
name/stem.  It does not enumerate or close unrelated user documents.  Set the
variable to `0` only outside confirmed execution experiments: `execute_model_plan`
now treats disabled cleanup as a preflight blocker and stops before
`adapter.transaction`, so no new SolidWorks part or drawing document is created
under that risky policy.

## Windows smoke test

After configuring templates and installing the `windows` extra, run:

```powershell
$env:SOLIDWORKS_MCP_ADAPTER = "solidworks"
$env:SOLIDWORKS_MCP_OUTPUT_DIR = "C:\\SolidWorksMCP\\outputs"
$env:SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN = "1"
$env:SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY = "1"
$env:SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW = "1"
$env:SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT = "1"
python scripts\smoke_mounting_plate.py
python scripts\smoke_production_workflows.py --production-suite --production-scenario flange_baseline
python scripts\smoke_production_workflows.py --production-suite --production-scenario center_hole_plate_baseline
python scripts\smoke_production_workflows.py --production-suite --production-scenario bracket_baseline
python scripts\smoke_production_workflows.py --production-suite --production-scenario end_cap_baseline
python scripts\smoke_production_workflows.py --production-suite --production-scenario mounting_block_baseline
python scripts\smoke_production_workflows.py --production-suite --production-scenario shaft_baseline
python scripts\smoke_production_workflows.py --production-suite --production-scenario washer_baseline
python scripts\smoke_production_workflows.py --production-suite --production-scenario sleeve_baseline
python scripts\smoke_production_workflows.py --production-suite --production-scenario slotted_array_plate_baseline
python scripts\release_production_gate.py --summary-only
python scripts\check_atomic_model_session.py
```

For a local dry-run without SolidWorks:

```bash
python3 scripts/smoke_mounting_plate.py --mock
python3 scripts/smoke_production_workflows.py --mock --production-suite --summary-only
python3 scripts/release_production_gate.py --mock --summary-only
python3 scripts/check_atomic_model_session.py
```

The smoke script executes a controlled plan with `confirmed=true`, prints the
generated run directory and writes the debug artifact set inside that run
directory.  By default it uses `examples/mounting_plate_plan.json`; pass
`--plan examples\flange_plan.json`,
`--plan examples\center_hole_plate_plan.json`,
`--plan examples\bracket_plan.json`,
`--plan examples\end_cap_plan.json`,
`--plan examples\mounting_block_plan.json`,
`--plan examples\shaft_plan.json`,
`--plan examples\washer_plan.json`,
`--plan examples\sleeve_plan.json`,
`--plan examples\slotted_array_plate_plan.json`,
`--production-suite --production-scenario flange_baseline`, or
`--production-suite --production-scenario center_hole_plate_baseline`, or
`--production-suite --production-scenario bracket_baseline`, or
`--production-suite --production-scenario end_cap_baseline`, or
`--production-suite --production-scenario mounting_block_baseline`, or
`--production-suite --production-scenario shaft_baseline`, or
`--production-suite --production-scenario washer_baseline`, or
`--production-suite --production-scenario sleeve_baseline`, or
`--production-suite --production-scenario slotted_array_plate_baseline` for the
trusted center-hole, bracket, end-cap, mounting-block, shaft, washer, sleeve and
slotted-array plate workflows.  `scripts/smoke_production_workflows.py` is the
workflow-neutral production-suite entrypoint; `scripts/smoke_mounting_plate.py`
remains available for backward-compatible single-plan and mounting-plate matrix
runs.  `scripts/release_production_gate.py` is the release handoff gate: it
creates a dedicated output root, runs the full trusted production scenario set
including isolated baseline/material/custom-property checks and the wide
controlled-size gate, then batch-diagnoses only that root with `max_runs=0`.  It writes
`release_gate_report.json` in that root with the scenario smoke results and
batch diagnosis summary for archive or release approval, including top-level
evidence counts for cleanup, document-state audit, direct hole callouts,
trusted dimensions and required outputs/previews.  If the gate is interrupted or
raises an unexpected exception after partial run creation, it still writes a
rejected report and records `emergency_cleanup_result` after attempting
post-run cleanup for discovered run directories.

The smoke CLI also writes a rejected `smoke_failure_report.json` in the active
output root when it is interrupted or raises an unexpected exception.  It then
attempts conservative post-run cleanup only for completed run directories whose
`execution_report.json` was touched after the smoke command started.  If the
failure happens before a run report exists, the report records
`emergency_cleanup_result.status=skipped_no_recent_runs` so operators know no
safe run-scoped cleanup target was available.  For regression testing without
SolidWorks, set `SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION=1` and run the smoke
command with `--mock --summary-only`.  To verify the post-run cleanup path
itself, set `SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION_AFTER_RUN=1`; the smoke run
first writes a normal completed run report, then raises and records a rejected
`smoke_failure_report.json` with cleanup attempts for that run.

- `plan.normalized.json`
- `execution_report.json`
- `delivery_manifest.json`
- `events.jsonl`
- `environment.json`
- `artifacts.json`

Use `delivery_manifest.json` as the compact handoff record for downstream
systems; it includes the production verdict plus output/preview artifact entries
with portable `relative_path` values and required SHA-256 hashes.  Schema `2026-06-06.2` adds
`handoff_summary`, which duplicates the trusted verdict, key production
statuses, output/preview counts, compact file lists, relative paths, hashes, and
diagnose/repro commands for dashboard or archive intake.  `artifacts.json` also hashes stable
fixed debug files including the normalized plan, report, events, environment
snapshot and delivery manifest; the self-referential `artifacts.json` entry is
the only fixed-file hash exception.  Offline `diagnose_run` now treats this
handoff file as a verified contract: `delivery_manifest_status=verified` means
the manifest exists, parses, and agrees with `execution_report.json` plus
`artifacts.json`; for schema `2026-06-06.2` it also means `handoff_summary`
matches the manifest and report, and new artifact indexes include matching
`relative_path` entries.  Older pre-`2026-06-06.2` manifests and artifact
indexes remain readable for historical runs.  Missing or mismatched manifests
make the diagnosis fail even when the CAD files still exist.
`diagnose_run` and `diagnose_runs` return the compact version as
`delivery_handoff_summary`, including verdict, key statuses, artifact counts and
commands without repeating every file entry in batch results.

Trusted real smoke acceptance requires
`trusted_workflow_status=controlled_mounting_plate` or
`trusted_workflow_status=controlled_center_hole_flange` or
`trusted_workflow_status=controlled_center_hole_plate`,
`trusted_workflow_status=controlled_end_cap`,
`trusted_workflow_status=controlled_mounting_block`,
`trusted_workflow_status=controlled_shaft`,
`preflight_status=ready`,
`thread_model_status` to be
`holewizard_threaded_hole`, `macro_threaded_hole`, or `not_requested` for
workflows without threaded holes.  If the status is `degraded_geometry_only`,
the exported files exist but the holes are only geometric tap-drill cuts.  It
also requires `corner_radius_status=fillet_feature` or `not_requested` for
workflows without rounded corners,
`drawing_view_status=created` with front, top, right, and isometric view roles,
and `drawing_annotation_status=hole_callout_created`; a hole table is diagnostic
only and is not accepted as the MVP hole callout success state.  The report
records `callout_creation_method` and `direct_hole_callout_created`.  Set
`SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT=1` for real SolidWorks production
runs.  The SolidWorks adapter fails preflight before document creation when this
flag is disabled, which prevents a non-strict `InsertModelAnnotations3` fallback
policy from being used for confirmed production execution.  It also
requires `drawing_dimension_status=basic_dimensions_created` for the controlled
workflow's MVP basic dimensions: mounting-plate length, width, thickness, R5,
and hole edge-offset dimensions; flange outer diameter, hole diameter, and
thickness; or center-hole plate length, width, thickness, and hole diameter.
The mounting-plate corner-radius dimension is part of that trusted gate: it
must be a real radial display dimension created from a selected drawing-view
edge.  Runs with
`dimension_layout_status=trusted_dimensions_created` are the trusted success
state; `dimension_layout_status=radius_proxy_used` or any
`proxy_dimension=true` dimension is rejected and remains useful only as failure
diagnostics.  Use
`SOLIDWORKS_MCP_FORCE_DRAWING_DIMENSION_FAILURE=1` to confirm that dimension
failure keeps artifacts but fails smoke acceptance.  If a plan includes
`assign_material`, the trusted gate also requires
`material_status=material_verified` and a readback material name matching the
request or a controlled `material_result.effective_material` alias.  The current
SW2022 Chinese smoke path verifies `Plain Carbon Steel` by applying and reading
back the installed `普通碳钢` material; if SolidWorks does not read back the
effective material, acceptance still fails.  `material_set_unverified` and
`material_assignment_failed` remain diagnostic failure states.  Use
`SOLIDWORKS_MCP_FORCE_MATERIAL_FAILURE=1` to
verify that artifacts still export and cleanup still runs while trusted
acceptance rejects the run with `material_verified`.  If a plan includes
`set_custom_properties`, the trusted gate requires
`custom_property_status=custom_properties_verified` and readback values matching
the requested properties; reports identify whether SolidWorks used
`CustomPropertyManager` or the verified `custom_info_legacy` fallback.  The
drawing stage inserts those values as a visible metadata note, and PDF semantic
validation requires the requested values to appear in the exported drawing PDF.
The trusted smoke also
requires `model_geometry_status=geometry_verified`; the adapter reads the
SolidWorks solid-body or document bounding box and compares sorted
principal dimensions to the controlled workflow plan.
`geometry_readback_failed` and `geometry_mismatch` keep artifacts available but
reject `production_acceptance_result`.  The trusted smoke also requires
`mass_property_status=mass_properties_verified`, with positive `mass_kg` and
`volume_m3` from SolidWorks mass-property readback.  This is a sanity gate for
manufacturing deliverables, not simulation validation.  The trusted smoke also requires
`artifact_validation_result.status=artifacts_ready` and validates that
`SLDPRT`, `STEP`, `STL`, `SLDDRW`, `PDF`, `DWG`, and the four standard preview
files exist and are non-empty.  `artifacts.json` records required SHA-256 hashes
for output and preview files so `diagnose_run` can detect post-run file drift;
missing hashes are integrity failures.  Real
SolidWorks smoke also records
`artifact_content_result`: STEP must contain a readable ISO-10303 structure, STL
must parse as ASCII or binary facet data, DWG must expose an AutoCAD `AC....`
signature, requested DXF must contain recognizable drawing-exchange
sections/entities, requested IGES and Parasolid `X_T/X_B` outputs must expose
recognizable exchange-file structure, native `SLDPRT/SLDDRW` files must be plausible non-placeholder
SolidWorks binaries, the PDF must be structurally readable with at least one
page, and PNG previews must parse and contain nonblank pixel variation.  The
trusted production suite includes `drawing_exchange` and `neutral_exports`
scenarios so optional drawing/CAD exchange-format paths stay under offline
diagnosis.  Per-format export failures
are reported as `export_result.status=partial_export_failure` with
`export_result.failed`; later exports and previews continue where possible.
Missing requested formats reject `production_acceptance_result` with
`requested_output_files`, while offline diagnosis treats matching
`outputs.export_format` failed events as recovered diagnostic events.  Use
`SOLIDWORKS_MCP_FORCE_CAD_CONTENT_FAILURE=1` to verify that CAD files still
export but `production_acceptance_result` is rejected with
`cad_artifact_content`.  It also requires `cleanup_result.status` to be
`completed` or `skipped_no_documents`.
For `examples/flange_plan.json`, a trusted run should create/export artifacts
and report `trusted_workflow_status=controlled_center_hole_flange`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.
For `examples/center_hole_plate_plan.json`, the same trusted artifact and
cleanup gates apply, with
`trusted_workflow_status=controlled_center_hole_plate`,
`thread_model_status=not_requested`,
`corner_radius_status=not_requested`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.
For `examples/washer_plan.json`, the same trusted artifact and cleanup gates
apply, with `trusted_workflow_status=controlled_washer`,
`thread_model_status=not_requested`, `corner_radius_status=not_requested`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.
For `examples/end_cap_plan.json`, the same trusted artifact and cleanup gates
apply, with `trusted_workflow_status=controlled_end_cap`,
`thread_model_status=not_requested`, `corner_radius_status=not_requested`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.
For `examples/mounting_block_plan.json`, the same trusted artifact and cleanup
gates apply, with `trusted_workflow_status=controlled_mounting_block`,
`thread_model_status=not_requested`, `corner_radius_status=not_requested`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.
For `examples/shaft_plan.json`, the same trusted artifact and cleanup gates
apply, with `trusted_workflow_status=controlled_shaft`,
`thread_model_status=not_requested`, `corner_radius_status=not_requested`,
`drawing_annotation_status=not_requested`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.
For `examples/sleeve_plan.json`, the same trusted artifact and cleanup gates
apply, with `trusted_workflow_status=controlled_sleeve`,
`thread_model_status=not_requested`, `corner_radius_status=not_requested`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.
completed cleanup must also report `cleanup_verification_status=verified`, so
automation cannot silently leave the run-created SolidWorks documents open.
Because SolidWorks native `SaveAs` can retitle the active part or drawing to the
exported `SLDPRT`/`SLDDRW`, cleanup closes the current run title and evaluates
exported file-name/file-stem candidates before reporting success.  File-name
and file-stem cleanup candidates are path-guarded and are closed only when
`GetOpenDocumentByName` resolves them to the current run workspace.  When supported by the
local SolidWorks COM server, cleanup also verifies closed documents with
`GetOpenDocumentByName` and reports
`cleanup_verification_status=verified`; unsupported verification is reported as
`unverified` instead of being silently treated as confirmed closure, and
production acceptance is rejected.
Every confirmed execution also records document-state snapshots as
`document_state_before_transaction`, `document_state_after_transaction`,
`document_state_before_cleanup`, and `document_state_after_cleanup`.
`document_state_audit_result.status=verified_no_run_documents_open` is the
required production acceptance signal that no run-created SolidWorks documents
remained open after cleanup; accepted runs also require
`after_cleanup_run_created_open_count=0`.
Use `SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE=1` to test the cleanup rejection path:
artifacts are still generated and `execute_plan.ok=true`, but
`cleanup_result.status=forced_failure`,
`cleanup_verification_status=failed`, and trusted acceptance fails with
`cleanup_completed` plus `cleanup_verified`.  Run this regression primarily with
the mock adapter; against a real SolidWorks process it deliberately skips
cleanup and can leave the current run documents open.  If a completed real run
reports cleanup failure, or an operator can still see the generated part or
drawing in SolidWorks, call MCP `cleanup_run_documents` with that `run_dir`, or
run `python scripts\cleanup_run_documents.py <run_dir>` in the same SolidWorks
adapter environment.  This remediation path does not create documents or export
files; it reads completed-run artifacts and closes only `SLDPRT`/`SLDDRW`
candidates whose open document path resolves inside the supplied run directory.
By default `SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY=1`, so the remediation command
attaches to an already-running SolidWorks session and returns
`failure_reason=solidworks_not_running_attach_only` if SolidWorks is not already
running.  Set it to `0` only for an explicit operator-approved remediation that
may use `Dispatch("SldWorks.Application")`.
The same gates are summarized as top-level `production_verdict` and full
`diagnostics.production_acceptance_result`; clients should prefer
`production_verdict.status=accepted` as the single trusted MVP delivery verdict
and inspect `production_verdict.failures` or
`production_acceptance_result.failures` for repair planning.  Rejected verdicts
also include `repair_actions`: stable action ids, severity, next-step text and
evidence fields that tell an AI client which diagnostic sections to inspect
before rerunning the trusted workflow.
After `execute_model_plan`, clients can call `diagnose_run` with the returned
`run_dir` to re-read the run artifacts, recheck file paths, and retrieve the
same compact production verdict without reconnecting to SolidWorks.  Require
`artifact_integrity_status=verified`, `event_log_status=verified`,
`delivery_manifest_status=verified`, and `environment_status=verified` before passing a run to downstream
manufacturing or archive systems.  `artifact_integrity_status=failed` with
`missing_artifacts[].status=missing_sha256` means the artifact existed but was
not cryptographically covered by the run index.  `missing_group`,
`invalid_group`, and `missing_field` mean the artifact index itself is
structurally incomplete and cannot be used as a production handoff contract.
`missing_fixed_file_entry` means one of the standard debug handoff files was not
indexed; `report_keys_mismatch` or `report_path_mismatch` means
`artifacts.json` no longer agrees with the output/preview paths declared in
`execution_report.json`.  `event_log_status=verified` also requires a terminal
`plan.execution` event whose `completed/failed` status and output/preview
counts match `execution_report`; failures such as `missing_event_run_id`,
`missing_terminal_event`, `terminal_status_mismatch`,
`terminal_ok_mismatch`, `terminal_output_count_mismatch`, and
`terminal_preview_count_mismatch` are returned in
`event_log_issues`.  Mixed-run evidence is also rejected:
`artifact_run_id_mismatch` means `artifacts.json` belongs to a different report,
and `event_run_id_mismatch` means at least one event line belongs to another
run.  `environment_status=failed` reports run snapshot problems in
`environment_issues`; accepted real SolidWorks runs are rejected there unless
the snapshot proves document cleanup, direct hole-callout enforcement, and
trusted-workflow enforcement were enabled for the run.
Schema-valid freeform modeling operations are intentionally outside this
trusted smoke workflow; clients must treat `trusted_controlled_workflow` failure
or `trusted_workflow_status=unsupported_workflow` as non-production output even
when files exported successfully.
The smoke script applies the same rule internally: every single, matrix, and
production-suite run calls offline `diagnose_run` after execution, and the smoke
exit code fails when artifact integrity, unrecovered failed events, or the
delivery manifest or environment snapshot contract fails.  `--summary-only`
includes compact `repair_actions`, so CI or an AI client can route the next
repair pass without loading the full report first.

For offline diagnosis after copying a run directory back:

```bash
python3 scripts/diagnose_run.py <run_dir>
```

The diagnose command exits non-zero when `production_acceptance_result` is
present and rejected, even if `execute_plan.ok=true` and all artifacts exist.
It also rechecks stored accepted verdicts against the current production gate
set and exposes `stored_production_acceptance_status` plus
`current_acceptance_recheck`, so older runs can be rejected when they lack
evidence for newly trusted gates.  The recheck validates the saved diagnostics
objects behind accepted gates, including direct hole callout, trusted dimension,
geometry/mass readback, cleanup, and document-state evidence, so a stale
`checks=true` payload cannot stand in for missing proof.  The command emits a compact
`acceptance_summary` and `repair_actions` before the full diagnostics payload so
clients can route repair work without parsing every COM probe detail.  Use
`--summary-only`, or the MCP `diagnose_run` tool with `summary_only=true`, when
the client only needs the trusted verdict and repair-routing fields.

For a batch delivery audit, use the MCP `diagnose_runs` tool or:

```bash
python3 scripts/diagnose_runs.py <outputs_or_plan_root> --summary-only
python3 scripts/diagnose_release_gate.py <outputs_or_plan_root>/release_gate_report.json
```

The batch diagnosis recursively finds run directories containing
`execution_report.json`, applies the same single-run verifier, and returns
`scan_status`, `accepted_count`, `rejected_count`, `status_counts`, `issue_counts`, and compact
per-run results.  It is intended for release handoff review and repair routing;
it never reconnects to SolidWorks.  The production default is a complete scan:
`max_runs=0` in the MCP tool or CLI means unbounded, and positive values should
only be used for explicit exploratory samples.  The CLI exits non-zero when any
diagnosed run is rejected or the scan is truncated by a positive `max_runs`,
which makes it suitable as a release gate.  With
`--summary-only`, each run result is compact and includes `report_file`,
`artifacts_file`, `delivery_manifest_file`, and per-run `issue_counts`
for dashboard, archiving, or CI use; omit it when a repair session needs the full
`acceptance_summary` and detailed artifact/event issue arrays.
For archived release gates, call MCP `diagnose_release_gate` or the
`diagnose_release_gate.py` CLI against `release_gate_report.json`; it re-runs
the batch diagnosis and verifies the report's schema, output root, scenario set,
and batch counts against current files without touching SolidWorks.  The release
diagnosis also recomputes `current_evidence_summary` and
`current_evidence_checks` from the current run artifacts, so a report is rejected
when copied or edited files no longer prove direct Hole Callouts, trusted
dimensions, cleanup/document-state safety, required outputs/previews, CAD
content, or PDF semantic content.
