# SolidWorks MCP Protocol Catalog

本文档说明当前 SolidWorks MCP 的“能力目录”。它用于规划、提示词编排和后续 adapter 设计，不等同于可执行操作白名单。

可执行入口当前是 15 个 MCP tools：

- `connect_solidworks`
- `validate_model_plan`
- `preflight_environment`
- `execute_model_plan`
- `start_model_session`
- `apply_model_operation`
- `finalize_model_session`
- `abort_model_session`
- `generate_drawing`
- `export_outputs`
- `inspect_active_model`
- `diagnose_run`
- `diagnose_runs`
- `diagnose_release_gate`
- `cleanup_run_documents`

未标记为 `available` 的能力只能用于方案讨论，不能写入 `execute_model_plan` 的 `ModelPlan.operations`。未来某项能力真正落地时，需要同时完成 schema 白名单、adapter 实现、调试报告字段和文档更新。

## MCP Discovery Interfaces

只读 resources：

- `solidworks://capabilities`：完整能力目录 JSON。
- `solidworks://capabilities/{category}`：单个分类目录，例如 `part_modeling`、`drawing`、`assembly`。
- `solidworks://preflight/environment`：当前环境预检 JSON，不创建零件或工程图文档。

Prompt：

- `plan_solidworks_operation`：指导 AI 先查能力目录，再生成当前可执行的 `ModelPlan`。它会明确提醒 planned/research/blocked 只能作为设计讨论内容。

## Status Meanings

- `available`：当前 schema、MCP tool 或 adapter 已能使用。
- `planned`：协议方向明确，但还不能执行。
- `research`：需要进一步 SolidWorks API 或产品边界调研。
- `blocked`：当前 MVP 因安全、依赖或范围限制不能实现。

## Reference Projects

这些项目只作为能力分类和协议设计参考，不复制实现：

- [vespo92/SolidworksMCP-TS](https://github.com/vespo92/SolidworksMCP-TS)：MCP 工具布局、COM 参数限制、VBA fallback 经验。
- [wzyn20051216/solidworks-automation-skill](https://github.com/wzyn20051216/solidworks-automation-skill)：Python 自动化流程、自审查、预览和工程验收习惯。
- [painezeng/CSharpAndSolidWorks](https://github.com/painezeng/CSharpAndSolidWorks)：工程图、尺寸、模板、BOM、选择和导出类 API 例子。
- [angelsix/solidworks-api](https://github.com/angelsix/solidworks-api)：C# wrapper、属性、注解、材料、应用状态和长期扩展参考。

## Categories

### `session_connection`

用途：连接、版本、模板、环境预检。

当前可用：

- `session.connect`：连接 mock 或 SolidWorks COM adapter，返回环境摘要。
- `session.validate_model_plan`：校验 `ModelPlan` 的单位、导出格式、必填字段和操作白名单；返回 `diagnostics.schema_status`、`diagnostics.production_readiness_status` 与 `diagnostics.trusted_workflow_policy_check`。`ok=true` 只代表 schema/白名单通过，生产客户端仍必须检查 `production_readiness_status=trusted_workflow_ready` 并继续执行 `preflight_environment`。
- `session.template_preflight`：正式建模前硬检查 SolidWorks COM、零件模板、工程图模板和输出目录。失败时 `execute_model_plan` 在进入 `adapter.transaction` 前停止，并返回 `failure_class=preflight` 与 `preflight_result`。
- `session.atomic_model_session`：通过 `start_model_session`、`apply_model_operation`、`finalize_model_session`、`abort_model_session` 暴露原子建模会话，而不是开放几十个零散工具。会话自带命名 feature graph，内置 `front/top/right` 平面和 `x_axis/y_axis/z_axis` 轴；后续草图、实体、尺寸、特征只能通过稳定 id 引用。`finalize_model_session` 仍必须 `confirmed=true` 才会创建 CAD 文档，并且 executor 会在 preflight 阶段重放 feature graph，缺引用或伪造 session evidence 会在 `adapter.transaction` 前失败。

计划中：

- 暂无。

未来入口：

- `SolidWorksCOMAdapter.connect`
- `SolidWorksMCPConfig.from_env`
- `SolidWorksCOMAdapter.preflight_environment`

### `sketch`

用途：平面选择、基础草图实体、约束和尺寸。

当前可用：

- `sketch.basic_entities`：通过 `create_sketch` 创建基础草图实体。adapter 必须显式选择平面，不能依赖 SolidWorks 当前选择状态。
- `sketch.dimensions_constraints`：在原子会话中记录草图驱动尺寸 id，并验证约束引用的实体 id。当前这层已经进入 feature graph 和 preflight replay；真实 SolidWorks driving dimension/constraint COM 回放仍是 adapter 硬化项。

计划中：

- 暂无。

未来入口：

- `SolidWorksCOMAdapter._op_create_sketch`
- 后续 sketch constraint helper

### `part_modeling`

用途：单零件机械建模。

当前可用：

- `part.create_mounting_plate`：受控安装板模板族，覆盖 `120 x 80 x 10 mm`、R5 四角、`15 mm` 边距、四个 `M3/M4/M5/M6/M8` ISO 公制粗牙贯穿孔和 fallback 报告。计划校验会在进入 SolidWorks 前检查正尺寸、圆角适配、孔中心位于板内、攻丝底孔边壁余量、孔到圆角余量、孔列/孔排间距和板厚。
- `part.create_center_hole_flange`：受控中心孔法兰 workflow，覆盖外径、厚度、中心通孔、材料、几何包围盒读回和质量属性读回。生产验收还要求真实工程图 Hole Callout，以及外径、内孔径、厚度三条可信 SolidWorks display dimensions。
- `part.create_center_hole_plate`：受控中心孔板 workflow，覆盖长、宽、厚、中心通孔、材料、几何包围盒读回和质量属性读回。生产验收还要求真实工程图 Hole Callout，以及长、宽、厚、孔径四条可信 SolidWorks display dimensions。
- `part.create_bracket`：受控 L 支架 workflow，覆盖底板长、底板宽、底板厚、立板高、立板厚、底板孔、立板孔、材料、几何包围盒读回和质量属性读回。生产验收还要求真实工程图 Hole Callout，以及底板长/宽/厚、立板高/厚、孔径六条可信 SolidWorks display dimensions。
- `part.create_end_cap`：受控端盖 workflow，覆盖外径、厚度、中心孔、螺栓孔 PCD、螺栓孔径、孔数、材料、几何包围盒读回和质量属性读回。生产验收还要求真实工程图 Hole Callout，以及外径、中心孔径、螺栓孔径、厚度四条可信 SolidWorks display dimensions；PCD 和孔数当前写入几何 evidence。
- `part.create_mounting_block`：受控安装块 workflow，覆盖长、宽、高、中心通孔、材料、几何包围盒读回和质量属性读回。生产验收还要求真实工程图 Hole Callout，以及长、宽、高、孔径四条可信 SolidWorks display dimensions。
- `part.create_shaft`：受控轴 workflow，覆盖直径、长度、材料、几何包围盒读回和质量属性读回。生产验收要求直径、长度两条可信 SolidWorks display dimensions；因该 plain shaft 无孔，Hole Callout 状态必须为 `not_requested`。
- `part.create_washer`：受控垫片 workflow，覆盖外径、内孔径、厚度、材料、几何包围盒读回和质量属性读回。生产验收还要求真实工程图 Hole Callout，以及外径、内孔径、厚度三条可信 SolidWorks display dimensions。
- `part.create_sleeve`：受控套筒 workflow，覆盖外径、内孔径、长度、材料、几何包围盒读回和质量属性读回。生产验收还要求真实工程图 Hole Callout，以及外径、内孔径、长度三条可信 SolidWorks display dimensions。
- `part.create_slotted_array_plate`：受控开槽/孔阵列板 workflow，覆盖长、宽、厚、中心长圆槽、2 x 2 孔阵列、材料、几何包围盒读回和质量属性读回。生产验收还要求真实工程图 Hole Callout，以及长、宽、厚、槽长、槽宽、孔径、X/Y 阵列间距八条可信 SolidWorks display dimensions。
- `part.basic_features`：基础拉伸、切除、孔、圆角、倒角、线性阵列、圆周阵列；直接写入 `ModelPlan` 的自由建模仍不属于生产验收 workflow，不能因为导出成功就声明为 trusted production output。生产路径是原子会话协议：先通过 feature graph 校验命名引用并持久化 session evidence，再由 executor 在 confirmed execution 的 preflight 阶段重放 graph。真实 SolidWorks face/edge/axis 选择回放仍是后续 adapter 硬化重点。
- `part.semantic_selectors`：`top_face`、`outer_edges`、`feature:<id>`、`sketch:<id>` 等语义选择器。
- `part.revolve_sweep_loft`：旋转、扫掠、放样已进入 `SUPPORTED_OPERATIONS` 和原子会话协议。SolidWorks adapter 已有 guarded COM 尝试和失败证据记录，但复杂 profile/path/axis 选择仍需要真实 full gate 验证后才能扩大承诺范围。
- `diagnostics.model_geometry_readback`：读取 SolidWorks 实体/文档包围盒并按受控 workflow 参数比对；`geometry_verified` 是 production acceptance 硬门槛，`geometry_readback_failed` 和 `geometry_mismatch` 只作为失败诊断。
- `diagnostics.mass_properties`：读取 SolidWorks 质量属性并要求正质量、正体积；`mass_properties_verified` 是受控 trusted smoke 的生产验收项，但不等同于仿真或强度校核。

计划中：

- 暂无新的基础单零件特征；下一阶段重点是真实 SolidWorks 命名引用回放和更多受控件族。

未来入口：

- `SolidWorksCOMAdapter.execute_operation`
- 新增 `_op_*` handler
- `schemas.py` 中的 `SUPPORTED_OPERATIONS`

### `drawing`

用途：工程图、视图、尺寸、孔标注和图纸导出。

当前可用：

- `drawing.standard_views`：创建前/上/右/等轴测视图；生产验收要求 `drawing_view_status=created` 且 `drawing_view_result.views` 覆盖 `front/top/right/isometric` 四个角色。
- `drawing.basic_dimensions`：创建真实 SolidWorks display dimensions，覆盖 MVP 安装板 `120` 长、`80` 宽、`10` 厚、真实选边径向 `R5/R6` 圆角和 `15/18` 孔边距定位；也覆盖中心孔法兰外径、内孔径和厚度，以及中心孔板长、宽、厚、孔径。`radius_proxy_used` 或任何 `proxy_dimension=true` 只保留为失败诊断，不满足 production acceptance。
- `drawing.metadata_note`：当计划请求 `set_custom_properties` 时，把已验证属性值插入图纸可见 note；PDF 语义验证要求这些值出现在导出的 PDF 文本中。
- `drawing.hole_callouts`：从孔面正投影视图选择可见孔边并调用 `AddHoleCallout2` 创建真实 Hole Callout。Hole Table 只能作为诊断 fallback，不满足 smoke acceptance。

计划中：

- `drawing.bom_tables`：BOM 表和装配图纸。

未来入口：

- `SolidWorksCOMAdapter.generate_drawing`
- drawing annotation helper
- drawing table helper

### `export`

用途：输出模型、图纸、中性 CAD、网格和文档文件。

当前可用：

- `export.mvp_formats`：`SLDPRT`、`SLDDRW`、`STEP`、`STL`、`PDF`、`DWG`、`DXF`、`IGES`、Parasolid `X_T/X_B`；当前 mounting-plate trusted smoke 的硬必需交付仍是 `SLDPRT/STEP/STL/SLDDRW/PDF/DWG` 和四张 PNG，DXF、IGES、Parasolid 是请求即导出和检查的可选生产交付项。`drawing_exchange` 场景覆盖 DXF 图纸交换导出，`neutral_exports` 场景覆盖 IGES/Parasolid 中性 CAD 导出。计划格式名保持 `iges`，SolidWorks 文件后缀使用 `.igs`。单个格式 `SaveAs` 失败会记录到 `export_result.failed` 并继续后续格式/预览导出，请求格式缺失会让生产验收以 `requested_output_files` 拒绝。

计划中：

- 暂无新的中性格式；后续可扩展 ACIS/SAT，但必须先进入 schema 白名单和 artifact content 检查。

未来入口：

- `SolidWorksCOMAdapter.export_outputs`
- `SUPPORTED_EXPORT_FORMATS`
- `_solidworks_suffix`

### `assembly`

用途：装配体、组件插入、配合、干涉检查和爆炸视图。

计划中：

- `assembly.create_insert_mate`
- `assembly.interference_exploded_view`

未来入口：

- future assembly adapter operations
- assembly-specific drawing and export helpers

首版仍是单零件 MVP，装配不能提交给 `execute_model_plan`。

### `properties_appearance`

用途：材料、颜色、外观、自定义属性和配置。

当前可用：

- `properties.assign_material`：材料写入并读回验证；当计划请求材料时，`material_status=material_verified` 才满足生产验收。支持受控材料别名诊断，例如 SW2022 中文环境下 `Plain Carbon Steel` 可解析为已安装并读回验证的 `普通碳钢`，报告会记录 `effective_material`。`SOLIDWORKS_MCP_FORCE_MATERIAL_FAILURE=1` 用于回归测试材料门禁，产物继续生成但 trusted acceptance 因 `material_verified` 拒绝。
- `properties.custom_properties`：通过 `set_custom_properties` 写入零件自定义属性并读回验证；当计划请求该操作时，`custom_property_status=custom_properties_verified` 才满足生产验收。SW2022 实测可走 `custom_info_legacy` fallback，图纸/PDF 还会验证属性值可见。

计划中：

- `appearance.color_texture`

未来入口：

- material/property helpers on active document
- appearance selector helper
- `CustomPropertyManager`

### `templates_macros`

用途：模板路径、VBA fallback、宏生成和宏安全策略。

当前可用：

- `templates.configure_paths`：从环境变量读取 part/drawing/output/debug 配置，并写入 `environment.json`。
- `templates.preflight_gate`：通过 `preflight_environment` tool/resource 或 `execute_model_plan` 内部门禁检查模板、输出目录和生产安全策略；失败时不进入建模事务。
- `runtime.trusted_workflow_policy`：默认启用 `SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW=1`，只允许受控 workflow 通过 confirmed execution preflight。当前 accepted production workflows 为 `controlled_mounting_plate`、`controlled_center_hole_flange`、`controlled_center_hole_plate`、`controlled_bracket`、`controlled_end_cap`、`controlled_mounting_block`、`controlled_shaft`、`controlled_washer`、`controlled_sleeve`、`controlled_slotted_array_plate`，以及由原子会话生成并带 feature graph evidence 的 `controlled_atomic_model`。夹带直接自由建模操作、伪造 atomic metadata 或其他未验收零件族会以 `trusted_workflow_policy` 阻止执行，不进入 `adapter.transaction`。
- `runtime.close_run_documents`：默认启用 `SOLIDWORKS_MCP_CLOSE_DOCUMENTS_AFTER_RUN=1`，在导出和预览完成后只关闭本次 run 创建的零件/图纸文档；文件名和 stem fallback 必须先解析到当前 run workspace，避免误关用户已有文档。`execute_model_plan` 会记录 `before_transaction/after_transaction/before_cleanup/after_cleanup` 文档状态快照和 `document_state_audit_result`，用于证明 cleanup 后没有 run-created 文档仍打开。若该开关被设为 `0`，`execute_model_plan` 会在 preflight 阶段以 `cleanup_policy` 阻止执行，不进入 `adapter.transaction`。
- `runtime.cleanup_completed_run_documents`：通过 MCP tool `cleanup_run_documents` 或 `scripts/cleanup_run_documents.py <run_dir>` 对已完成/中断 run 做补救清理；工具只读取 run 目录里声明的 `SLDPRT/SLDDRW`，并且必须先通过 `GetOpenDocumentByName` 解析到 `run_dir` 内的真实路径，才会调用 `CloseDoc`，不会新建文档或关闭用户其他文件。默认 `SOLIDWORKS_MCP_CLEANUP_ATTACH_ONLY=1`，因此补救清理只附着已有 SolidWorks 会话；未找到运行中的 SolidWorks 时返回 `solidworks_not_running_attach_only`，不会为了清理而启动新会话。只有操作员明确允许时才设置为 `0`。`SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE=1` 可用 mock adapter 验证该补救清理失败路径。
- `runtime.direct_hole_callout_policy`：真实 SolidWorks confirmed execution 要求 `SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT=1`；若未启用，`execute_model_plan` 会在 preflight 阶段以 `direct_hole_callout_policy` 阻止执行，避免非严格孔标注 fallback 进入生产 run。
- `macros.holewizard_fallback`：HoleWizard 失败时写出受控 `.swb` 宏模板、尝试 `RunMacro2`，并把宏路径、执行结果和失败原因写入报告。

Blocked：

- `macros.generated_swb_execution`：当前 SW2022 验证机上，生成的文本 `.swb` 不能被 `RunMacro2` 作为可运行宏项目接受；需要后续引入受信任 `.swp` 项目或人工安装宏项目。
- `macros.general_generation`：AI 生成并执行任意宏。当前没有宏沙箱和审批策略，不能开放。

未来入口：

- `config.py`
- `SolidWorksCOMAdapter._try_holewizard_macro_fallback`
- 后续宏安全策略

### `diagnostics_review`

用途：run 目录、事件日志、预览、自审查和离线诊断。

当前可用：

- `diagnostics.run_artifacts`：每次确认执行固定生成 `plan.normalized.json`、`execution_report.json`、`delivery_manifest.json`、`events.jsonl`、`environment.json`、`artifacts.json`；新 `artifacts.json` 和 `delivery_manifest.json` 文件条目同时记录绝对路径、可移植 `relative_path` 和 SHA-256；`delivery_manifest.json` schema `2026-06-06.2` 包含 `handoff_summary`，把 accepted/rejected verdict、关键可信状态、输出/预览计数、紧凑文件列表、相对路径、SHA-256、诊断命令和复现命令放到一页式交付摘要中。
- `diagnostics.inspect_active_model`：返回特征摘要、fallback、warning、图纸标注状态。
- `diagnostics.run_diagnosis`：通过 MCP tool `diagnose_run` 离线读取 run 目录，并用当前 production gate set 复核已保存的 accepted verdict；若旧 run 缺少当前可信门槛证据，会返回 `stored_production_acceptance_status=accepted` 但 `production_acceptance_status=rejected` 和 `current_acceptance_recheck.status=failed`。当前复核不仅检查保存的 gate 布尔值，还会重验 `drawing_annotation_result`、`drawing_dimension_result`、`model_geometry_result`、`mass_property_result`、`cleanup_result` 和 `document_state_audit_result` 等证据对象，防止 stale `checks=true` 掩盖缺失诊断。同时复核 artifacts 路径、导出/预览 SHA-256、`events.jsonl` 未恢复失败、`delivery_manifest.json` 契约和 `environment.json` 运行快照，并返回 `production_acceptance_status`、`production_acceptance_failures`、`repair_actions`、`acceptance_summary`、`artifact_integrity_status`、`event_log_status`、`delivery_manifest_status`、`delivery_handoff_summary`、`environment_status`、缺失或变更 artifact 和失败事件；文件条目缺少 SHA-256 会以 `missing_sha256` 失败，新 artifact index schema 缺少或写错 `relative_path` 会以 `missing_relative_path`、`invalid_relative_path` 或 `relative_path_mismatch` 失败，索引缺少字段或分组会以 `missing_field`、`missing_group`、`invalid_group` 失败，固定调试文件缺索引会以 `missing_fixed_file_entry` 失败，`execution_report.json` 与 `artifacts.json` 的 output/preview 不一致会以 `report_keys_mismatch` 或 `report_path_mismatch` 失败，`artifacts.json` 与报告 run 元数据不一致会以 `artifact_run_id_mismatch` 或 `artifact_run_dir_mismatch` 失败，新 schema 的 `handoff_summary` 缺失或与 manifest/report/artifacts 相对路径不一致会让 `delivery_manifest_status=failed`，旧 schema 仍可读取且 `delivery_handoff_summary=null`；`events.jsonl` 缺少事件 run_id、缺少终止 `plan.execution`、终止状态或 output/preview count 与 `execution_report` 不一致、或事件 run_id 混入其他 run，会写入 `event_log_issues` 并让 `event_log_status=failed`；`environment.json` 的 run id、adapter、run_dir 或 adapter env 不一致会写入 `environment_issues` 并让 `environment_status=failed`，accepted 真实 SolidWorks run 还必须证明开启文档关闭、直接孔标注强制开关和 trusted workflow enforcement；不会连接 SolidWorks 或修改 CAD 文件。
- `diagnostics.run_collection_diagnosis`：通过 MCP tool `diagnose_runs` 或 `scripts/diagnose_runs.py` 批量扫描根目录下包含 `execution_report.json` 的 run 目录，逐个复用 `diagnostics.run_diagnosis`，并返回 `scan_status`、`accepted_count`、`rejected_count`、`status_counts`、`issue_counts` 和每个 run 的 compact verdict；用于生产交付批量审计和修复分流，不连接 SolidWorks。生产默认 `max_runs=0` 表示完整不限量扫描；只有明确抽样时才传正数。`scan_status=truncated` 会让 batch `ok=false`，因为目录状态没有被完整证明。
- `diagnostics.release_gate_report`：通过 MCP tool `diagnose_release_gate` 或 `scripts/diagnose_release_gate.py` 复核 `release_gate_report.json`；它重新批量诊断报告中的 output root，并检查 schema、场景列表、批量计数和 accepted 场景集合是否仍与当前磁盘 run artifacts 一致；同时从当前 run artifacts 重新计算 `current_evidence_summary` 和 `current_evidence_checks`，确认 direct Hole Callout、可信尺寸、cleanup/document-state、必需输出/预览、CAD/PDF 语义内容等 release 证据仍然成立；`scripts/release_production_gate.py` 中断或异常时也会写 rejected report 和 `emergency_cleanup_result`；不连接 SolidWorks、不修改 CAD 文件。
- `diagnostics.smoke_failure_report`：`scripts/smoke_mounting_plate.py` 或 `scripts/smoke_production_workflows.py` 中断/异常时会在当前 output root 写 `smoke_failure_report.json`，包含 `failure_class`、`failure_reason`、`traceback` 和 `emergency_cleanup_result`；应急清理只处理本次命令开始后写出 `execution_report.json` 的 completed run 目录，避免误关非本次 run 的 SolidWorks 文档。`SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION=1` 可在 mock 模式验证建模前失败路径；`SOLIDWORKS_MCP_FORCE_SMOKE_EXCEPTION_AFTER_RUN=1` 可验证 completed run 已存在时会尝试 run-scoped cleanup。
- `diagnostics.cleanup_completed_run_documents`：补救清理工具返回 `attach_only`、`candidate_documents`、`attempts`、`closed_documents`、`cleanup_verification_status` 和失败原因；默认只附着已有 SolidWorks 以关闭仍然打开的 run-created native 文档，不修改 run artifacts，不代替离线 `diagnose_run` 验收。
- `diagnostics.hole_result`：报告 `holewizard_threaded_hole`、`macro_threaded_hole` 或 `degraded_geometry_only`，并包含宏路径和失败原因。
- `diagnostics.drawing_annotation_result`：报告 `hole_callout_created` 或具体失败阶段；Hole Table 不作为 MVP 成功标准。
- `diagnostics.mass_property_result`：报告 `mass_property_status`、`mass_kg`、`volume_m3`、`surface_area_m2` 和 API attempts；生产验收要求 `mass_properties_verified`。
- `diagnostics.artifact_content_result`：报告 CAD/PDF/PNG 内容验证；真实 run 要求 STEP/STL/DWG 格式检查，请求 DXF/IGES/Parasolid 时也要求交换格式结构可识别，`SLDPRT/SLDDRW` 非占位二进制检查、PDF 语义内容和 PNG 非空渲染通过。`diagnostics.export_result` 记录每个请求格式的成功/失败，`partial_export_failure` 不等于交付成功，必须结合 `requested_output_files` 门禁判断。`artifacts.json` 对 output/previews 和稳定固定调试文件记录必需 SHA-256，用于后续离线完整性复核；自引用 `artifacts.json` 固定文件条目是唯一哈希例外。
- `diagnostics.cleanup_result`：报告 `cleanup_result.status`、关闭尝试和 `cleanup_verification_status`；生产验收要求 `completed/verified`，或 mock/no-documents 场景的 `skipped_no_documents`。`SOLIDWORKS_MCP_FORCE_CLEANUP_FAILURE=1` 会生成 `forced_failure/failed` 诊断，并让 trusted acceptance 因 `cleanup_completed`、`cleanup_verified` 拒绝。
- `diagnostics.document_state_audit`：报告 `document_state_before_transaction`、`document_state_after_transaction`、`document_state_before_cleanup`、`document_state_after_cleanup` 和 `document_state_audit_result`；快照采集失败会记录诊断而不打断产物生成，但生产验收要求 `verified_no_run_documents_open` 且 `after_cleanup_run_created_open_count=0`，以确认本次 run 创建的 SolidWorks 文档没有在 cleanup 后继续打开。
- `diagnostics.production_verdict`：`ExecutionReport.to_dict()` 顶层返回 `production_verdict`，镜像生产验收的 `status/ok/failures/repair_actions/summary`，让 MCP 客户端不用深挖 diagnostics 即可做交付判断。当前 accepted verdict 支持 `controlled_mounting_plate`、`controlled_center_hole_flange`、`controlled_center_hole_plate`、`controlled_bracket`、`controlled_end_cap`、`controlled_mounting_block`、`controlled_shaft`、`controlled_washer`、`controlled_sleeve` 和 `controlled_slotted_array_plate`；夹带自由建模操作会以 `trusted_controlled_workflow` 拒绝，并给出对应 repair action。
- `diagnostics.visual_previews`：生成或 mock 多视角预览。

未来入口：

- `debug.py`
- `scripts/diagnose_run.py`
- `scripts/diagnose_runs.py`
- `scripts/cleanup_run_documents.py`
- `solidworks_mcp.run_diagnostics.diagnose_run_directory`
- `solidworks_mcp.run_diagnostics.diagnose_run_collection`
- adapter preview helpers

### `advanced_manufacturing`

用途：钣金、焊件和仿真等非 MVP 制造域。

Research：

- `manufacturing.sheet_metal`
- `manufacturing.weldments`
- `manufacturing.simulation`

这些能力需要单独的 API 调研、schema 设计和工程验收标准，不能混入当前单零件 MVP。

## Promotion Checklist

当 planned/research 能力准备变成 available 时，至少完成：

1. 在 `schemas.py` 中添加明确的输入字段、必填校验和导出格式校验。
2. 在 mock adapter 中生成可读 dry-run 报告。
3. 在 SolidWorks adapter 中实现显式选择、COM 调用、fallback 和 failure_class。
4. 在 `execution_report.json`、`delivery_manifest.json`、`events.jsonl` 和 `artifacts.json` 中记录足够的调试信息。
5. 更新 `src/solidworks_mcp/capabilities.py` 和本文档状态。
6. 确认 MCP tool 数量是否仍应保持不变；不要为了原子 API 扩散新增大量 tools。
