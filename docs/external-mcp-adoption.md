# External MCP Adoption Roadmap

本文档把外部 SolidWorks/CAD MCP 调研结果转成本项目可执行的优化点。它不是能力承诺，也不是 `ModelPlan.operations` 白名单；所有条目在进入生产前仍必须走 schema、adapter、run artifacts、cleanup、offline diagnosis 和 release gate。

## Adoption Rules

- 只采纳机制，不整仓照搬工具面。当前项目保持受控 workflow、显式确认、preflight、run 目录证据和离线诊断为交付边界。
- 只读能力优先。能提升规划、诊断、模板检查和 API 查询的能力，可以先以 resource/tool 或文档索引落地。
- 宏能力默认不开放。任意宏生成/执行仍是 blocked；只有 allowlist、run-dir isolation、环境门禁和人工确认都具备时，才研究受控宏诊断。
- 不扩大工具爆炸面。新增能力优先沉淀到能力目录、诊断字段、已有 `execute_model_plan` 或离线诊断流程，避免把 SolidWorks API 逐个暴露成几十个工具。
- Mock 通过不等于真实可用。任何生产声明必须有真实 SolidWorks COM 运行证据或明确标为 mock/offline。

## Priority Backlog

| Priority | Candidate | Source Signal | Project Fit | Status |
| --- | --- | --- | --- | --- |
| P0 | SolidWorks API docs lookup | `kilwizac/solidworks-api-mcp` 提供 method/search/interface/enum/examples 这类只读查询形态 | 提升计划生成、COM 调试和 adapter 维修，不需要启动 SolidWorks | planned |
| P1 | COM strategy trace | `vespo92/SolidworksMCP-TS`、`andrewbartels1/SolidworksMCP-python` 都暴露了复杂 COM 参数和调用策略问题 | 把 `FeatureExtrusion3`、工程图、DimXpert、Simulation 等尝试过程结构化进事件和诊断 | planned |
| P1 | Drawing template diagnostics | 多个项目围绕模板、工程图、尺寸和导出工具分层 | 在创建工程图前诊断模板、sheet、projection、view anchor 和尺寸布局风险 | planned |
| P2 | Controlled macro diagnostics | `ladla90077-web/solidworks-mcp` 的 `RunMacro2`、日志解析、watchdog、STA worker 思路较成熟 | 只能作为 allowlisted installed macro project 的诊断实验，不能开放任意宏 | research |
| P2 | Controlled industry templates | `ANYLXB/solidworks-mcp-pro` 的法兰/管板类 JSON 规格可作为 recipe 灵感 | 可扩展新的受控 workflow family，但必须先有 typed schema 和 release gate | research |
| P3 | Neutral exchange-file inspection | MCPWorld 搜索更偏 CAD-adjacent，适合借鉴 STEP/DXF/IGES 离线检查方向 | 增强 `artifact_content_result`，不替代真实 SolidWorks open/import gate | planned |

## Implementation Contracts

### `knowledge.solidworks_api_lookup`

目标是新增只读 API 知识面，而不是 CAD 自动化面。第一版可以只做本地文档索引包装，返回 method signature、interface members、enum values、examples 和 source reference。验收条件：

- 不需要 SolidWorks COM session。
- 不写 CAD 文件、不修改 run 目录。
- 大索引文件的 license、来源和仓库体积需要单独确认。
- MCP 暴露形态优先考虑 read-only resource/tool，不进入 `SUPPORTED_OPERATIONS`。

### `diagnostics.com_strategy_trace`

目标是让复杂 COM 调用失败后能回答“试了哪些 API、为何 fallback、下一步修哪里”。验收条件：

- 每次复杂调用记录 method family、参数变体、HRESULT/返回值、异常摘要和 fallback reason。
- 诊断写入 `events.jsonl` 和 `execution_report.json`，并能被 `diagnose_run` 离线复核。
- 只影响诊断，不改变 trusted workflow policy。

### `drawing.template_diagnostics`

目标是在工程图生成前把模板/图纸问题显式化。验收条件：

- preflight 可以报告 drawing template 是否存在、可读、sheet size/projection 是否可解析。
- 失败时在进入 drawing creation 前给出稳定 failure key。
- 不把 template diagnostics 通过当作 drawing generation 成功。

### `macros.controlled_run_diagnostics`

目标是研究受控宏诊断，不是开放宏生成。验收条件：

- macro id 必须来自 allowlist，macro project 必须是本机已安装/受信任路径。
- 每次执行必须绑定 run directory、显式 confirmation 和环境开关。
- `RunMacro2` error/warning code、日志、watchdog 结果都写入 run artifacts。
- `macros.generated_swb_execution` 与 `macros.general_generation` 保持 blocked，直到有独立安全策略。

### `workflow.controlled_industry_templates`

目标是从外部 recipe 项目提炼新的受控零件族。验收条件：

- 每个 recipe 有 typed schema、边界校验、mock dry-run 和真实 SolidWorks release gate。
- 必须生成工程图、导出、预览、geometry/mass/material/dimension/callout/cleanup 证据。
- 未通过 release gate 前只能作为 research，不进入 accepted production workflows。

### `exchange.neutral_file_inspection`

目标是增强离线 artifact 内容验证。验收条件：

- 解析 STEP、IGES、DXF、Parasolid 的基础结构、单位线索和实体统计。
- 结果进入 `artifact_content_result`，并被 `diagnose_run` 复核。
- 对 native/openability 仍以 SolidWorks open/import validation 为准。

## Promotion Checklist

某个条目从 planned/research 晋升 available 前，至少完成：

1. 更新 `src/solidworks_mcp/capabilities.py` 状态和诊断字段说明。
2. 若涉及执行，更新 `schemas.py`、mock adapter、SolidWorks adapter 和 production acceptance。
3. 新增或更新最小测试、mock gate，以及必要的真实 SolidWorks gate。
4. 更新 `docs/protocol-catalog.md`、示例输入和 operator 说明。
5. 证明新能力不会绕过 confirmation、preflight、cleanup、artifact integrity 或 offline diagnosis。
