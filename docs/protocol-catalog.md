# SolidWorks MCP Protocol Catalog

本文档说明当前 SolidWorks MCP 的“能力目录”。它用于规划、提示词编排和后续 adapter 设计，不等同于可执行操作白名单。

可执行入口仍只有 6 个 MCP tools：

- `connect_solidworks`
- `validate_model_plan`
- `execute_model_plan`
- `generate_drawing`
- `export_outputs`
- `inspect_active_model`

未标记为 `available` 的能力只能用于方案讨论，不能写入 `execute_model_plan` 的 `ModelPlan.operations`。未来某项能力真正落地时，需要同时完成 schema 白名单、adapter 实现、调试报告字段和文档更新。

## MCP Discovery Interfaces

只读 resources：

- `solidworks://capabilities`：完整能力目录 JSON。
- `solidworks://capabilities/{category}`：单个分类目录，例如 `part_modeling`、`drawing`、`assembly`。

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
- `session.validate_model_plan`：校验 `ModelPlan` 的单位、导出格式、必填字段和操作白名单。

计划中：

- `session.template_preflight`：在正式执行前检查零件模板、工程图模板和图纸格式路径。

未来入口：

- `SolidWorksCOMAdapter.connect`
- `SolidWorksMCPConfig.from_env`
- 后续 config preflight helper

### `sketch`

用途：平面选择、基础草图实体、约束和尺寸。

当前可用：

- `sketch.basic_entities`：通过 `create_sketch` 创建基础草图实体。adapter 必须显式选择平面，不能依赖 SolidWorks 当前选择状态。

计划中：

- `sketch.dimensions_constraints`：草图尺寸和几何约束。

未来入口：

- `SolidWorksCOMAdapter._op_create_sketch`
- 后续 sketch constraint helper

### `part_modeling`

用途：单零件机械建模。

当前可用：

- `part.create_mounting_plate`：安装板模板，覆盖 `120 x 80 x 10 mm`、R5 四角、四个 M6 ISO 公制粗牙贯穿孔和 fallback 报告。
- `part.basic_features`：基础拉伸、切除、孔、圆角、倒角、线性阵列、圆周阵列。
- `part.semantic_selectors`：`top_face`、`outer_edges`、`feature:<id>`、`sketch:<id>` 等语义选择器。

计划中：

- `part.revolve_sweep_loft`：旋转、扫掠、放样。

未来入口：

- `SolidWorksCOMAdapter.execute_operation`
- 新增 `_op_*` handler
- `schemas.py` 中的 `SUPPORTED_OPERATIONS`

### `drawing`

用途：工程图、视图、尺寸、孔标注和图纸导出。

当前可用：

- `drawing.standard_views`：创建前/上/右/等轴测视图。
- `drawing.hole_callouts`：尽力尝试孔或螺纹标注；失败时只写报告，不阻断主流程。

计划中：

- `drawing.basic_dimensions`：基础尺寸和注释。
- `drawing.bom_tables`：BOM 表和装配图纸。

未来入口：

- `SolidWorksCOMAdapter.generate_drawing`
- drawing annotation helper
- drawing table helper

### `export`

用途：输出模型、图纸、中性 CAD、网格和文档文件。

当前可用：

- `export.mvp_formats`：`SLDPRT`、`SLDDRW`、`STEP`、`STL`、`PDF`、`DWG`、`DXF`。

计划中：

- `export.iges_parasolid`：`IGES`、Parasolid `X_T/X_B`。

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

- `properties.assign_material`：基础材料意图。

计划中：

- `appearance.color_texture`
- `properties.custom_properties`

未来入口：

- material/property helpers on active document
- appearance selector helper
- `CustomPropertyManager`

### `templates_macros`

用途：模板路径、VBA fallback、宏生成和宏安全策略。

当前可用：

- `templates.configure_paths`：从环境变量读取 part/drawing/output/debug 配置，并写入 `environment.json`。
- `macros.holewizard_fallback`：HoleWizard 失败时的受控宏 fallback 框架。

Blocked：

- `macros.general_generation`：AI 生成并执行任意宏。当前没有宏沙箱和审批策略，不能开放。

未来入口：

- `config.py`
- `SolidWorksCOMAdapter._try_holewizard_macro_fallback`
- 后续宏安全策略

### `diagnostics_review`

用途：run 目录、事件日志、预览、自审查和离线诊断。

当前可用：

- `diagnostics.run_artifacts`：每次确认执行固定生成五件套。
- `diagnostics.inspect_active_model`：返回特征摘要、fallback、warning、图纸标注状态。
- `diagnostics.visual_previews`：生成或 mock 多视角预览。

未来入口：

- `debug.py`
- `scripts/diagnose_run.py`
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
4. 在 `execution_report.json`、`events.jsonl` 和 `artifacts.json` 中记录足够的调试信息。
5. 更新 `src/solidworks_mcp/capabilities.py` 和本文档状态。
6. 确认 MCP tool 数量是否仍应保持不变；不要为了原子 API 扩散新增大量 tools。
