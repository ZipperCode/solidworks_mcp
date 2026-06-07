# SolidWorks MCP

A minimal Python MCP server for AI-assisted SolidWorks modeling workflows.

The MVP exposes a small set of high-level tools that let an external MCP client
validate a restricted JSON modeling plan, execute it after user confirmation,
generate drawing outputs and return a compact self-review report.
Completed run directories can also be diagnosed through the `diagnose_run` and
`diagnose_runs` MCP tools, which read debug artifacts from disk without
reconnecting to SolidWorks.  If a real run leaves native documents open,
`cleanup_run_documents` or `scripts/cleanup_run_documents.py` can close only the
run-created `SLDPRT`/`SLDDRW` documents after path-guarding them against the run
directory.  This remediation is attach-only by default, so it will not start a
new SolidWorks process just to clean up old documents.

## MVP scope

- Python MCP server over stdio.
- Windows SolidWorks COM execution through a dedicated adapter.
- macOS/Linux development through a deterministic mock adapter.
- Controlled mechanical part, drawing, assembly/BOM, sheet-metal and weldment plans.
- Confirmation required before model execution.
- Basic drawing/export/self-review reports after execution.

The project intentionally does not include an embedded LLM, chat UI, production
simulation validation or 2D drawing-to-3D reconstruction in the current release.
The static-simulation scenario is explicit-only until a real SolidWorks
Simulation/CosmosWorks API is available and accepted by the production gate.

## 中文生产说明

当前主分支的定位是“受控件库 + 原子建模会话 + 生产级诊断门禁”的
SolidWorks MCP。AI agent 可以先做规划、预检、诊断和修复建议；真正创建
SolidWorks 文档前，仍必须由调用方提交 `confirmed=true`。

当前默认生产门禁覆盖 25 个场景：安装板、法兰、中心孔板、支架、端盖、
安装块、轴、垫片、套筒、开槽/孔阵列板、装配/BOM、钣金基体法兰、
焊件框架，以及原子建模的拉伸、切除、孔、倒圆、倒角、线性/圆周阵列、
旋转、扫描、放样等能力。仿真场景仍为显式运行，不进入默认生产验收。

导入已有 `SLDPRT` 后生成生产加工图时，使用 `import_existing_model` +
`make_drawing`，并在 `drawing_profile` 中设置：

```json
{
  "enabled": true,
  "sheet_format": "A3",
  "projection": "first_angle",
  "view_style": "manufacturing_rotational",
  "include_isometric": true,
  "include_basic_dimensions": true,
  "export_formats": ["pdf", "dwg"]
}
```

该路径会把源模型复制到隔离的 `run_dir`，只生成新的零件和工程图文件，不会
原地修改用户文件。生产验收要求真实 SolidWorks 剖视图
`CreateSectionViewAt5` 证据、主剖视图、端视图、等轴测参考图、OD/ID/L
三类几何可验证 display dimensions、中心线/中心标记、A3 第一角法布局、
PDF/DWG/SLDDRW 导出，以及“导入模型尺寸/材料/表面处理需人工确认”的可见
技术说明。导入模型不会伪造完整加工尺寸；未能从模型或用户输入确认的信息会
明确标记为 `<未指定>` 或需人工确认。

常用中文验收命令：

```powershell
python scripts\check_existing_model_manufacturing_drawing_gate.py
python scripts\smoke_mounting_plate.py --plan <existing-model-plan.json> --summary-only
python scripts\diagnose_run.py <run_dir> --summary-only
python scripts\release_production_gate.py --mock --summary-only
python scripts\diagnose_release_gate.py <release_gate_report.json> --summary-only
```

真实 SolidWorks 生产执行建议开启：

```powershell
$env:SOLIDWORKS_MCP_ADAPTER = "solidworks"
$env:SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN = "1"
$env:SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY = "1"
$env:SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW = "1"
$env:SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT = "1"
```

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
- `create_center_hole_flange`
- `create_center_hole_plate`
- `create_bracket`
- `create_end_cap`
- `create_mounting_block`
- `create_shaft`
- `create_sheet_metal_base_flange`
- `create_washer`
- `create_sleeve`
- `create_slotted_array_plate`
- `create_weldment_frame`
- `create_bom_assembly`
- `create_plane`
- `create_sketch`
- `extrude`
- `cut`
- `hole`
- `fillet`
- `chamfer`
- `import_existing_model`
- `linear_pattern`
- `circular_pattern`
- `revolve`
- `sweep`
- `loft`
- `assign_material`
- `set_custom_properties`
- `make_drawing`
- `run_static_simulation`

Some operations are schema-supported before full Windows COM implementation so
the plan contract can stay stable while adapter coverage grows.
`create_mounting_plate`, `create_center_hole_flange`,
`create_center_hole_plate`, `create_bracket`, `create_end_cap`,
`create_mounting_block`, `create_shaft`, `create_sheet_metal_base_flange`,
`create_weldment_frame`, `create_washer`, `create_sleeve`,
`create_slotted_array_plate`, `create_bom_assembly`, `import_existing_model`,
and the gated atomic modeling scenarios are the current controlled production
workflows. `run_static_simulation` remains explicit-only and is excluded from the
default release gate.

## Mounting plate smoke workflow

The first real SolidWorks smoke workflow is a high-level mounting plate family:

- `120 x 80 x 10 mm`
- four rounded corners, `R5`
- four `M3/M4/M5/M6/M8` ISO metric coarse threaded through holes
- hole centers offset `15 mm` from the plate edges
- drawing export with front/top/right/isometric views and verified SolidWorks hole callouts

Run the local mock smoke flow:

```bash
python3 scripts/smoke_mounting_plate.py --mock
```

Run the controlled mock matrix before trusting a change that touches mounting
plate/flange/center-hole plate schema, holes, drawings, artifacts, cleanup or
production acceptance:

```bash
python3 scripts/check_mounting_plate_schema.py
python3 scripts/smoke_mounting_plate.py --mock --matrix
python3 scripts/smoke_production_workflows.py --mock --production-suite
python3 scripts/smoke_production_workflows.py --mock --production-suite --summary-only
```

The production suite runs independent trusted scenarios for the baseline plate,
localized material alias verification, custom properties/PDF metadata, a
combined metadata gate, optional DXF drawing exchange export, optional
IGES/Parasolid neutral exports, a wide controlled-size combined gate, the
baseline center-hole flange workflow, center-hole plate workflow, bracket,
end-cap, mounting-block, shaft, sheet-metal base flange, weldment frame, washer,
sleeve, slotted-array plate, BOM assembly, and gated atomic modeling workflows.
Each smoke run also calls the offline
`diagnose_run` verifier against the generated `run_dir`; suite acceptance
requires both the execution production verdict and offline artifact/manifest
integrity to pass.  Limit it to one scenario when iterating against a real
SolidWorks session:

```bash
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario combined
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario combined --summary-only
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario drawing_exchange --summary-only
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario neutral_exports --summary-only
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario flange_baseline --summary-only
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario center_hole_plate_baseline --summary-only
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario bracket_baseline --summary-only
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario mounting_block_baseline --summary-only
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario shaft_baseline --summary-only
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario washer_baseline --summary-only
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario sleeve_baseline --summary-only
python3 scripts/smoke_production_workflows.py --mock --production-suite --production-scenario slotted_array_plate_baseline --summary-only
```

For a pre-release handoff gate, run the isolated release command.  It creates a
dedicated output root, executes the full trusted production scenario set
(`baseline`, `material_alias`, `custom_properties`, `combined`,
`drawing_exchange`, `neutral_exports`, `wide_combined`, `flange_baseline`,
`center_hole_plate_baseline`, `bracket_baseline`, `end_cap_baseline`,
`mounting_block_baseline`, `shaft_baseline`,
`sheet_metal_base_flange_baseline`, `weldment_frame_baseline`,
`washer_baseline`, `sleeve_baseline`, `slotted_array_plate_baseline`,
`bom_assembly_baseline`, `atomic_baseline`, `atomic_cut_baseline`,
`atomic_pattern_baseline`, `atomic_revolve_baseline`,
`atomic_sweep_baseline`, and `atomic_loft_baseline`) and then batch diagnoses only that root with an
unbounded scan.  The gate writes
`release_gate_report.json` at the gate output root so the batch verdict can be
archived without relying on terminal scrollback.  The report includes top-level
evidence counts for cleanup, document-state audit, direct hole callouts,
trusted dimensions, artifact content and required outputs/previews.  If the
gate is interrupted or crashes after creating one or more run directories, it
still writes a rejected `release_gate_report.json`, batch-diagnoses the partial
root, and records a best-effort `emergency_cleanup_result` for run-created
documents:

```bash
python3 scripts/release_production_gate.py --mock --summary-only
```

The smoke CLI has the same operator-safety contract for direct iteration:
unexpected exceptions or `Ctrl+C` produce a rejected `smoke_failure_report.json`
in `SOLIDWORKS_MCP_OUTPUT_DIR` and attempt run-scoped cleanup for completed run
directories touched after the smoke command started.  The diagnostic scope is
intentionally conservative: cleanup requires an `execution_report.json`, so a
hard interruption before report creation is recorded as
`emergency_cleanup_result.status=skipped_no_recent_runs` rather than risking
unrelated open SolidWorks documents.  Set
`SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION=1` with `--mock --summary-only` to test the
failure-report path without starting SolidWorks.  Set
`SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION_AFTER_RUN=1` with a mock smoke command to
test the same report path after a completed run exists, including
`emergency_cleanup_result.attempted_count`.

For a single controlled variant:

```bash
python3 scripts/smoke_mounting_plate.py --mock --thread-spec M8
python3 scripts/smoke_mounting_plate.py --mock --thread-spec M8 --size-variant wide
```

`--summary-only` includes compact `repair_actions` on rejected runs, so CI or an
AI client can route the next repair pass from the smoke output without loading
the full `execution_report.json` first.

Run on Windows with SolidWorks:

```powershell
$env:SOLIDWORKS_MCP_ADAPTER = "solidworks"
$env:SOLIDWORKS_MCP_OUTPUT_DIR = "C:\SolidWorksMCP\outputs"
$env:SOLIDWORKS_MCP_PART_TEMPLATE = "C:\path\to\Part.prtdot"
$env:SOLIDWORKS_MCP_DRAWING_TEMPLATE = "C:\path\to\Drawing.drwdot"
$env:SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN = "1"
$env:SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY = "1"
$env:SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW = "1"
$env:SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT = "1"
python scripts\smoke_mounting_plate.py
python scripts\smoke_production_workflows.py --production-suite --production-scenario combined
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
```

The execution writes `execution_report.json` into the output directory.  A
trusted smoke run first requires
`trusted_workflow_status=controlled_mounting_plate` or
`trusted_workflow_status=controlled_center_hole_flange` or
`trusted_workflow_status=controlled_center_hole_plate` or
`trusted_workflow_status=controlled_bracket` or
`trusted_workflow_status=controlled_end_cap` or
`trusted_workflow_status=controlled_mounting_block` or
`trusted_workflow_status=controlled_shaft` or
`trusted_workflow_status=controlled_washer` or
`trusted_workflow_status=controlled_sleeve` or
`trusted_workflow_status=controlled_slotted_array_plate` and
`preflight_status=ready`.  Schema-valid lower-level operations such as sketch,
extrude, cut, hole, fillet, chamfer and patterns remain useful for development
and diagnostics, but they are not accepted as the current production workflow
until each workflow has its own verified gates.  By default,
`SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW=1` blocks those unsupported workflows
at preflight before any SolidWorks document is created; set it to `0` only for
non-production experiments.  The same preflight
is available as the `preflight_environment` MCP tool and the
`solidworks://preflight/environment` resource; if it fails, `execute_model_plan`
stops before `adapter.transaction` and records `failure_class=preflight` with a
full `preflight_result`.  Use `SOLIDWORKS_MCP_FORCE_PREFLIGHT_FAILURE=1` to
exercise that hard-block regression path.

The controlled center-hole flange plan in `examples/flange_plan.json` is the
second trusted workflow.  It covers a cylindrical flange with a concentric
through hole, verified material, model geometry readback, mass-property
readback, a real drawing Hole Callout, and trusted display dimensions for outer
diameter, hole diameter and thickness.  Accepted runs report
`trusted_workflow_status=controlled_center_hole_flange`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created` and
`dimension_layout_status=trusted_dimensions_created`.

The controlled center-hole plate plan in `examples/center_hole_plate_plan.json`
is the third trusted workflow.  It covers a rectangular plate with one
concentric through hole, verified material, model geometry readback,
mass-property readback, a real drawing Hole Callout, and trusted display
dimensions for length, width, thickness and hole diameter.  Accepted runs report
`trusted_workflow_status=controlled_center_hole_plate`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created` and
`dimension_layout_status=trusted_dimensions_created`.

The controlled bracket plan in `examples/bracket_plan.json` is the fourth trusted
workflow.  It covers an L bracket with one base hole and one upright hole,
verified geometry and mass-property readback, direct drawing Hole Callouts, and
trusted base length, base width, base thickness, upright height, upright
thickness and hole diameter display dimensions. Production acceptance requires
`trusted_workflow_status=controlled_bracket`,
`thread_model_status=not_requested`, `corner_radius_status=not_requested`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.

The controlled end cap plan in `examples/end_cap_plan.json` is the fifth trusted
workflow.  It covers a circular cap with center bore and six bolt holes,
verified geometry and mass-property readback, direct drawing Hole Callout, and
trusted outer diameter, center-hole diameter, bolt-hole diameter and thickness
display dimensions. Production acceptance requires
`trusted_workflow_status=controlled_end_cap`,
`thread_model_status=not_requested`, `corner_radius_status=not_requested`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.

The controlled mounting block plan in `examples/mounting_block_plan.json` is the sixth trusted
workflow.  It covers a rectangular block with one center through hole, verified
geometry and mass-property readback, direct drawing Hole Callout, and trusted
length, width, height and hole diameter display dimensions. Production acceptance
requires
`trusted_workflow_status=controlled_mounting_block`,
`thread_model_status=not_requested`, `corner_radius_status=not_requested`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.

The controlled shaft plan in `examples/shaft_plan.json` is the seventh trusted
workflow.  It covers a plain cylindrical shaft with verified geometry and
mass-property readback, trusted diameter and length display dimensions, and no
Hole Callout requirement because no hole is requested. Production acceptance
requires
`trusted_workflow_status=controlled_shaft`,
`thread_model_status=not_requested`, `corner_radius_status=not_requested`,
`drawing_annotation_status=not_requested`,
`model_geometry_status=geometry_verified`,
`mass_property_status=mass_properties_verified`,
`drawing_dimension_status=basic_dimensions_created`, and
`dimension_layout_status=trusted_dimensions_created`.

The controlled washer plan in `examples/washer_plan.json` is the eighth trusted
workflow.  It covers a thin circular washer with one concentric through hole,
verified material, model geometry readback, mass-property readback, a real
drawing Hole Callout, and trusted display dimensions for outer diameter, inner
diameter and thickness.  Accepted runs report
`trusted_workflow_status=controlled_washer`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created` and
`dimension_layout_status=trusted_dimensions_created`.

The controlled sleeve plan in `examples/sleeve_plan.json` is the ninth trusted
workflow.  It covers a cylindrical sleeve with one concentric bore, verified
material, model geometry readback, mass-property readback, a real drawing Hole
Callout, and trusted display dimensions for outer diameter, inner diameter and
length.  Accepted runs report
`trusted_workflow_status=controlled_sleeve`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created` and
`dimension_layout_status=trusted_dimensions_created`.

The controlled slotted-array plate plan in `examples/slotted_array_plate_plan.json`
is the tenth trusted workflow.  It covers a rectangular plate with a center slot
and 2 x 2 hole array, verified material, model geometry readback, mass-property
readback, direct drawing Hole Callouts, and trusted display dimensions for
length, width, thickness, slot length, slot width, hole diameter, and X/Y array
spacing.  Accepted runs report
`trusted_workflow_status=controlled_slotted_array_plate`,
`drawing_annotation_status=hole_callout_created`,
`drawing_dimension_status=basic_dimensions_created` and
`dimension_layout_status=trusted_dimensions_created`.

Trusted model acceptance then requires
`thread_model_status=holewizard_threaded_hole`,
`thread_model_status=macro_threaded_hole`, or `thread_model_status=not_requested`
for workflows without threaded holes; final `degraded_geometry_only` means the
holes are only tap-drill geometry.  It also requires
`corner_radius_status=fillet_feature` or `corner_radius_status=not_requested`
for workflows without rounded corners, and
`model_geometry_status=geometry_verified`, which reads the active SolidWorks
part bounding box and compares the sorted length/width/thickness dimensions
against the controlled plan.  `geometry_readback_failed` and
`geometry_mismatch` are hard production-acceptance failures even when export
artifacts are present, because they indicate missing solid bodies or unit/scale
drift.  The trusted gate also requires
`mass_property_status=mass_properties_verified`, proving SolidWorks reports
positive `mass_kg` and `volume_m3`; this is a manufacturing sanity check, not a
simulation result.
Trusted acceptance also requires
`drawing_view_status=created` with front, top, right, and isometric roles in
`drawing_view_result.views`, plus
`drawing_annotation_status=hole_callout_created`; hole tables are diagnostic
only and do not satisfy the drawing annotation acceptance check.  The annotation
result records `callout_creation_method` and `direct_hole_callout_created`.
Set `SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT=1` to require the direct
selected-edge `AddHoleCallout2` path.  The real SolidWorks adapter treats this
as a preflight blocker for confirmed execution, so a non-strict callout policy
cannot create new SolidWorks documents.  Mock runs remain usable for local
development, but accepted real production runs must prove this flag in
`environment.json`.  The drawing must also report
`drawing_dimension_status=basic_dimensions_created`, covering the MVP `120`
length, `80` width, `10` thickness, `R5` corner radius, and `15` hole
edge-offset display dimensions.  The corner-radius dimension must be a real
selected-edge radial display dimension created through `AddRadialDimension2`;
accepted runs report `dimension_layout_status=trusted_dimensions_created`.
`radius_proxy_used` or any `proxy_dimension=true` entry is rejected by
`production_acceptance_result` and should be treated as a repair target.  If a
plan requests `assign_material`, production acceptance also requires
`material_status=material_verified` with `material_result.current_material`
matching the requested material or a verified controlled alias recorded as
`material_result.effective_material`.  On the primary SW2022 Chinese validation
machine, `Plain Carbon Steel` is resolved to the installed `普通碳钢` material
only when SolidWorks readback confirms `current_material=普通碳钢`; unverified
translation is never accepted.  `material_set_unverified` and
`material_assignment_failed` are explicit repair diagnostics.  Use
`SOLIDWORKS_MCP_FORCE_MATERIAL_FAILURE=1` to verify the material rejection path;
artifacts are still generated and cleanup still runs, but trusted acceptance is
rejected with `material_verified`.  If a plan requests `set_custom_properties`,
production acceptance requires `custom_property_status=custom_properties_verified`
and `custom_property_result.current_properties` must match the requested values;
`custom_property_unverified` and `custom_property_failed` are explicit repair
diagnostics.  On SW2022 the adapter reports whether verification used
`CustomPropertyManager` or the legacy `custom_info_legacy` ModelDoc2 API.  The
drawing stage also inserts those values as a visible metadata note, and PDF
semantic validation requires the requested values to appear in the exported PDF.
Use
`SOLIDWORKS_MCP_FORCE_DRAWING_DIMENSION_FAILURE=1` to verify the dimension
failure regression path; CAD/PDF/DWG/PNG artifacts are still generated, but
smoke acceptance fails.  Production smoke also requires
`artifact_validation_result.status=artifacts_ready`, with non-empty `SLDPRT`,
`STEP`, `STL`, `SLDDRW`, `PDF`, `DWG`, and front/top/right/isometric preview
artifacts.  `artifacts.json` records required SHA-256 hashes for output and
preview files so later `diagnose_run` calls can detect post-run file drift;
missing hashes are treated as integrity failures.  Real
SolidWorks smoke also records `artifact_content_result`: STEP
must contain a readable ISO-10303 structure, STL must parse as ASCII or binary
facet data, DWG must expose an AutoCAD `AC....` signature, requested DXF must
contain recognizable drawing-exchange sections/entities, requested IGES and
Parasolid `X_T/X_B` outputs must expose recognizable exchange-file structure,
native `SLDPRT/SLDDRW` files must be plausible non-placeholder SolidWorks binaries,
the PDF must have a valid PDF header, EOF marker and at least one page, and PNG
previews must parse with nonblank pixel variation.  Use
`SOLIDWORKS_MCP_FORCE_CAD_CONTENT_FAILURE=1` to verify that CAD files still
export but `production_acceptance_result` is rejected with
`cad_artifact_content`.  Per-format export failures are recorded in
`export_result`; the adapter continues with later formats and previews where
possible.  Requested formats that fail or go missing reject production
acceptance with `requested_output_files`, while offline diagnosis reports the
matching `outputs.export_format` failed event as recovered when it is recorded
in `export_result.failed`.  It also requires
`cleanup_result.status=completed` or `cleanup_result.status=skipped_no_documents`;
completed cleanup must also report `cleanup_verification_status=verified`.
By default the SolidWorks adapter closes only the part and drawing documents
created by the current run after exports/previews are captured, leaving any
pre-existing user documents alone.  Native `SaveAs` can rename the active
document to the exported `SLDPRT`/`SLDDRW`, so cleanup also closes the run
export title; file-name and file-stem candidates are closed only after
`GetOpenDocumentByName` resolves them to a path inside the current run
workspace.  `SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN=0` is treated as a
preflight blocker for confirmed execution; the run stops before
`adapter.transaction` so a risky cleanup configuration cannot create new
SolidWorks documents.  Cleanup diagnostics include
`cleanup_verification_status`; when SolidWorks exposes
`GetOpenDocumentByName`, run-created documents are verified as closed after
`CloseDoc`, otherwise successful cleanup is reported as `unverified` and
production acceptance is rejected.
Each confirmed execution also records document-state snapshots before the
transaction, after the transaction, before cleanup, and after cleanup.  The
`document_state_audit_result` diagnostic summarizes whether any run-created
SolidWorks document is still open after cleanup; trusted production acceptance
now requires `document_state_audit_result.status=verified_no_run_documents_open`
and `after_cleanup_run_created_open_count=0`.
Use `SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE=1` for cleanup regression tests: the
run still writes CAD/PDF/DWG/preview artifacts and `execute_plan.ok=true`, but
`cleanup_result.status=forced_failure`, `cleanup_verification_status=failed`,
and trusted acceptance is rejected with `cleanup_completed` and
`cleanup_verified`.  Prefer this switch with the mock adapter; on a real
SolidWorks session it intentionally skips cleanup and may leave run-created
documents open for manual closure.  When a real run reports cleanup failure or
the operator suspects run-created documents are still open, run
`python scripts\cleanup_run_documents.py <run_dir>` with the SolidWorks adapter
environment, or call the MCP `cleanup_run_documents` tool.  The remediation tool
reads only the completed run artifacts, resolves candidate native documents
through `GetOpenDocumentByName`, and calls `CloseDoc` only when the resolved
document path is inside `<run_dir>`.  By default
`SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY=1`, so this post-run remediation attaches to
an already-running SolidWorks session and returns
`failure_reason=solidworks_not_running_attach_only` if none is available.  Set
it to `0` only when the operator explicitly wants the remediation command to use
`Dispatch("SldWorks.Application")`.
All of these hard gates are also summarized in
`production_acceptance_result`; clients should treat
the top-level `production_verdict.status=accepted` or
`production_acceptance_result.status=accepted` as the MVP trusted delivery
verdict and inspect `failures`, `repair_actions`, and `summary` fields when it
is rejected.  `repair_actions` gives stable action ids, severity, next-step text
and evidence fields so an AI client can plan a concrete repair pass instead of
only displaying raw gate names.  The
summary includes `dimension_layout_status=trusted_dimensions_created`,
`proxy_dimensions`, and `non_radial_radius_dimensions` so clients can
distinguish trusted dimensions from rejected fallback dimensions.  It also
includes model geometry expected/measured dimensions and requested/current
material, plus `effective_material` when a controlled material alias was used.
It also includes requested/current custom properties when metadata is part of
the plan, and mass/volume readback for the generated part.

The mounting-plate plan validator now rejects unsafe controlled-family inputs
before SolidWorks is touched.  It accepts only ISO metric coarse `M3/M4/M5/M6/M8`
four-corner through-hole variants and checks positive dimensions, corner radius
fit, hole-center containment, tap-drill edge wall clearance, hole-to-fillet
clearance, row/column spacing and minimum plate thickness for the requested
thread.  Required drawing dimension ids are derived from the actual plan
parameters, so non-default controlled sizes such as `140 x 90 x 12 mm`, `R6`,
`18 mm` edge offset are accepted only when the matching dimensions
(`length_140`, `width_90`, `thickness_12`, `corner_radius_r6`,
`hole_edge_offset_18`) are created.  This validation is intentionally scoped to
the trusted mounting-plate family; broader automated design rules should be
added as new controlled workflows earn real SolidWorks acceptance.

`SOLIDWORKS_MCP_MACRO_FALLBACK` defaults to enabled.  If the direct
`HoleWizard5` path fails, the adapter writes a controlled VBA macro template
under `macros/` and attempts to run it.  On the current SW2022 validation
machine, generated text `.swb` files are not accepted by `RunMacro2`; the
report records that as an explicit macro blocker before falling back to
geometry cuts.

## Debug artifacts

Every confirmed execution creates an isolated run directory:

```text
outputs/<plan_name>/run_<timestamp>_<id>/
```

The run directory contains:

- `plan.normalized.json`
- `execution_report.json`
- `delivery_manifest.json`
- `events.jsonl`
- `environment.json`
- `artifacts.json`
- `exports/`
- `previews/`
- `macros/`

`delivery_manifest.json` is the compact handoff file for downstream clients: it
contains the production verdict, report/artifacts paths, output/preview file
entries, portable `relative_path` values, and their required SHA-256 hashes.
Manifest schema `2026-06-06.2` also includes `handoff_summary`, a one-screen
delivery summary with the accepted verdict, key trusted statuses, output/preview
counts, compact file lists, relative paths, hashes, and diagnose/repro commands.
Offline diagnosis verifies that this summary matches the manifest and report,
and new `artifacts.json` indexes must include matching `relative_path` fields;
older `2026-06-06.1` manifests and pre-schema artifact indexes remain readable.
`diagnose_run` and `diagnose_runs` expose this as
`delivery_handoff_summary` in their summary payloads, trimmed to verdict, key
statuses, artifact counts and commands so clients can route accepted deliveries
without loading the full manifest.  `artifacts.json` also records hashes for stable fixed debug
files such as the normalized plan, report, events, environment snapshot and
delivery manifest, so copied run directories can be checked for post-run drift;
the self-referential `artifacts.json` entry is the only fixed-file hash
exception.

Basic structured events are enabled by default.  To include SolidWorks COM call
summaries, set:

```bash
SOLIDWORKS_MCP_DEBUG_LEVEL=verbose
```

To diagnose a copied run directory without connecting to SolidWorks:

```bash
python3 scripts/diagnose_run.py outputs/m6_mounting_plate/run_<timestamp>_<id>
python3 scripts/diagnose_runs.py outputs/m6_mounting_plate --summary-only
python3 scripts/diagnose_release_gate.py outputs/release_gate_<timestamp>/release_gate_report.json
```

The diagnose command exits non-zero when `production_acceptance_result` is
present and rejected, even if `execute_plan.ok=true` and all CAD/PDF/DWG/PNG
artifacts exist.  Offline diagnosis also rechecks stored accepted verdicts
against the current production gate set, reports
`stored_production_acceptance_status` separately, and rejects older handoffs that
do not prove newly trusted gates such as `drawing_standard_views_created`.  It
also rechecks the saved diagnostics evidence behind accepted gates, including
direct hole callouts, trusted dimensions, geometry and mass readback, cleanup,
and document-state audit, so a stale `checks=true` payload cannot replace proof.  It
also emits a compact `acceptance_summary` with the thread, drawing view/callout,
dimensions, geometry, CAD/PDF content, and cleanup verdicts plus
`repair_actions` before the full diagnostics payload.  Use `--summary-only`
when a CI job needs only the trusted verdict and repair-routing fields.  MCP clients can call
`diagnose_run` with the returned `run_dir` to get the same verdict without
touching SolidWorks; this also rechecks indexed output/preview SHA-256 hashes
and rejects changed artifacts, incomplete artifact indexes, or file entries
without hashes.  The fixed debug files must all be indexed, and the
output/preview entries in `artifacts.json` must match `execution_report.json`.
`artifacts.json` run metadata and event `run_id` values must belong to the same
run as `execution_report.json`, so mixed-run evidence is rejected.
It also rejects unrecovered `events.jsonl`
entries with `status=failed` and requires a terminal `plan.execution` event
whose `completed/failed` status, `output_count` and `preview_count` match
`execution_report`; known exploratory COM probes are reported as
`recovered_probe_event_count` instead of blocking a run after production
acceptance succeeds.  Event-log semantic issues, including missing event
`run_id`, are returned in `event_log_issues`.  The same offline diagnosis verifies
`delivery_manifest.json` itself and reports `delivery_manifest_status=verified`
only when the handoff file exists, parses, and matches `execution_report.json`
plus `artifacts.json`.  It also verifies `environment.json` and reports
`environment_status=verified` only when run id, adapter, run directory, and the
captured adapter env agree with the report; accepted real SolidWorks runs must
also prove `SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN` and
`SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT` were enabled.
For release handoff review, `diagnose_runs` and `scripts/diagnose_runs.py`
batch-audit all discovered run directories below a root, returning accepted and
rejected counts plus `issue_counts` grouped by production, artifact, event,
manifest, and environment failure keys.  The production default is
`max_runs=0`, which means a complete unbounded scan; pass a positive `max_runs`
only when you intentionally want an exploratory sample.  The CLI exits non-zero
when any diagnosed run is rejected or when a positive `max_runs` truncates the
scan.
`scripts/diagnose_release_gate.py` verifies an archived
`release_gate_report.json` by re-running the batch diagnosis for its output root
and checking the report's scenario list, schema version, batch counts, and
accepted scenario set against the current files on disk.  It also recomputes
release evidence from the current run artifacts and reports
`current_evidence_summary` plus `current_evidence_checks`; archived reports are
rejected when the current files no longer prove direct Hole Callouts, trusted
dimensions, cleanup/document-state safety, required outputs/previews, or
CAD/PDF semantic content, even if the original `release_gate_report.json`
claimed acceptance.
