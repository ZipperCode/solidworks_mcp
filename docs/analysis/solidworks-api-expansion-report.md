# SolidWorks MCP 扩展能力分析报告

> 生成日期: 2026-06-12  
> 分析范围: D:\Program Files\SOLIDWORKS Corp 安装 + 当前 solidworks_mcp 代码库  
> SW 版本: 2022 SP5 (推断)

---

## 一、当前 MCP 现状总览

### 1.1 MCP 工具（15 个）

| # | 工具名 | 用途 |
|---|--------|------|
| 1 | `connect_solidworks` | 连接 SolidWorks COM / 报告 mock 状态 |
| 2 | `validate_model_plan` | 校验 JSON 模型计划 schema |
| 3 | `preflight_environment` | 硬性检查：COM、模板、输出目录、cleanup 策略 |
| 4 | `execute_model_plan` | 在隔离 run 目录中执行已确认的计划 |
| 5 | `start_model_session` | 开启原子建模会话（仅 staging，不创建 CAD） |
| 6 | `apply_model_operation` | 在原子会话中 stage 一个操作 |
| 7 | `finalize_model_session` | 执行原子会话（confirmed=true） |
| 8 | `abort_model_session` | 丢弃原子会话 |
| 9 | `generate_drawing` | 从活动模型生成工程图 |
| 10 | `export_outputs` | 导出模型/工程图到指定格式 |
| 11 | `inspect_active_model` | 返回特征/工程图/诊断摘要 |
| 12 | `diagnose_run` | 离线诊断已完成的 run 目录 |
| 13 | `diagnose_runs` | 批量诊断 |
| 14 | `diagnose_release_gate` | 验证发布门禁报告 |
| 15 | `cleanup_run_documents` | 关闭 run 创建的仍打开的 SolidWorks 文档 |

### 1.2 资源（3 个）
- `solidworks://capabilities` — 完整能力目录 JSON
- `solidworks://capabilities/{category}` — 单个类别
- `solidworks://preflight/environment` — 动态环境预检

### 1.3 提示（1 个）
- `plan_solidworks_operation` — 引导 AI 生成安全的 ModelPlan

### 1.4 支持的操作（30 个）

**受控零件模板（13）**: mounting_plate, flange, center_hole_plate, bracket, end_cap, mounting_block, shaft, washer, sleeve, slotted_array_plate, sheet_metal_base_flange, weldment_frame, bom_assembly

**原子建模（12）**: create_plane, create_sketch, extrude, cut, hole, fillet, chamfer, linear_pattern, circular_pattern, revolve, sweep, loft

**工具类（5）**: import_existing_model, assign_material, set_custom_properties, make_drawing, run_static_simulation

### 1.5 架构

```
FastMCP (stdio) → ModelPlanExecutor → CADAdapter → SolidWorksCOMAdapter (win32com)
                                   ↓            → MockCADAdapter
                              capability catalog / run diagnostics
```

---

## 二、SolidWorks 安装中发现的 API 资产

### 2.1 类型库（.tlb）— 20 个已发现

| 类型库 | 大小 | 用途 | 当前使用 |
|--------|------|------|----------|
| **sldworks.tlb** | 1.9 MB | 核心 API (ISldWorks, IModelDoc2, IPartDoc, IAssemblyDoc, IDrawingDoc) | ✅ 已使用 |
| **swconst.tlb** | 739 KB | 枚举/常量 (955+ 枚举) | ✅ 已使用 |
| **swcommands.tlb** | 269 KB | 命令 ID 枚举 (1000+ 内置命令) | ❌ **未使用** |
| **swpublished.tlb** | 44 KB | 插件接口 (ISwAddin, ISwAddinExtension) | ❌ **未使用** |
| **swdimxpert.tlb** | 69 KB | DimXpert GD&T 自动化 | ❌ **未使用** |
| **cosworks.tlb** | 495 KB | Simulation/FEA API | ❌ **未使用** |
| **floworks.tlb** | 79 KB | Flow Simulation API | ❌ **未使用** |
| **swmotionstudy.tlb** | 24 KB | 运动分析 API | ❌ **未使用** |
| **swinspectionAddIn.tlb** | 30 KB | Inspection 自动化 | ❌ **未使用** |
| **sldcostingapi.tlb** | 51 KB | 成本估算 API | ❌ **未使用** |
| **SWRoutingLib.tlb** | 43 KB | 布线 API（管道/电缆） | ❌ **未使用** |
| **sustainability.tlb** | 19 KB | 可持续性分析 | ❌ **未使用** |
| **swvba.tlb** | 5 KB | VBA 宏接口 | ❌ **未使用** |
| **SolidWorks.MacroBuilder.tlb** | 7 KB | 宏构建器 API | ❌ **未使用** |
| **swfeedback.tlb** | 7 KB | 反馈接口 | ❌ **未使用** |
| **gabiswengine.tlb** | 5 KB | PhotoView 360 渲染引擎 | ❌ **未使用** |
| **sldVisuConverter.tlb** | 2 KB | Visualize 转换器 | ❌ **未使用** |
| **SldPresMgr.tlb** | 4 KB | 演示管理器 | ❌ **未使用** |
| **gtswUtilities.tlb** | 51 KB | 工具箱/实用程序 | ❌ **未使用** |
| **cmotionswapi.tlb** | 97 KB | Motion API | ❌ **未使用** |

**关键发现**: 20 个类型库中仅 2 个被使用（sldworks, swconst），18 个完全未触及。

### 2.2 互操作 DLL（18 个已发现）

| DLL | 大小 | 用途 |
|-----|------|------|
| SolidWorks.Interop.sldworks.dll | 2.7 MB | 核心 API |
| SolidWorks.Interop.swconst.dll | 455 KB | 枚举 |
| SolidWorks.Interop.swcommands.dll | 188 KB | 命令接口 |
| SolidWorks.Interop.swpublished.dll | 46 KB | 插件接口 |
| **SolidWorks.Interop.swdocumentmgr.dll** | **345 KB** | **⚡ 离线文档管理（无需 SW！）** |
| SolidWorks.Interop.swdimxpert.dll | 68 KB | GD&T |
| SolidWorks.Interop.cosworks.dll | 505 KB | Simulation |
| SolidWorks.Interop.swmotionstudy.dll | 43 KB | 运动分析 |
| SolidWorks.Interop.SWRoutingLib.dll | 42 KB | 布线 |
| SolidWorks.Interop.gtswutilities.dll | 47 KB | 工具箱 |
| SolidWorks.Interop.sldcostingapi.dll | 57 KB | 成本估算 |
| SolidWorks.Interop.sustainability.dll | 29 KB | 可持续性 |
| SolidWorks.Interop.swbrowser.dll | 28 KB | 浏览器集成 |
| SolidWorks.Interop.sw3dprinter.dll | 20 KB | 3D 打印 |
| SolidWorks.Interop.dsgnchk.dll | 20 KB | 设计检查 |
| SolidWorks.Interop.fworks.dll | 16 KB | FeatureWorks |
| SolidWorks.Interop.sldtoolboxconfigureaddin.dll | 14 KB | 工具箱配置 |
| Interop.SWEdmLib.dll | 267 KB | EDM/PDM 库 |

### 2.3 API 帮助文件
- `api/HelpViewer/sldworksapi/apihelpviewer.cab` (33 MB) — 完整 API 参考
- `api/HelpViewer/swconst/apienumshelpviewer.cab` (2.8 MB) — 枚举参考

### 2.4 当前使用的 ISldWorks 方法（vs 可用方法）

| 当前使用的方法 | 状态 |
|---------------|------|
| `Dispatch("SldWorks.Application")` | 连接 |
| `GetActiveObject("SldWorks.Application")` | 附加 |
| `NewDocument(template, ...)` | 创建文档 |
| `OpenDoc6(path, type, ...)` | 打开文档 |
| `CloseDoc(title)` | 关闭文档 |
| `ActivateDoc3(title, ...)` | 激活文档 |
| `GetOpenDocumentByName(path)` | 解析文档 |
| `RevisionNumber` | 版本信息 |
| `Visible` | 可见性 |
| `GetUserPreferenceStringValue(id)` | 读取偏好设置 |
| `RunMacro2(path, module, proc)` | 运行宏 |

| ⚡ 可用但未使用的方法 | 重要性 |
|----------------------|--------|
| **`RunCommand(cmdId, ...)`** | 🔴 极高 — 执行任何 SW 命令 |
| **`GetRunningCommandInfo(...)`** | 🟠 高 — 读取当前命令状态 |
| **`GetDocumentCount()`** | 🟠 高 — 获取打开文档数 |
| **`GetDocuments()`** | 🟠 高 — 枚举打开文档 |
| **`GetFirstDocument()/GetNext()`** | 🟠 高 — 遍历文档 |
| **`QuitDoc(title)`** | 🟠 高 — 退出文档时不保存 |
| **`CreatePropertyManagerPage(...)`** | 🟡 中 — 创建 PMP |
| **`CreateTaskpaneView(...)`** | 🟡 中 — 创建任务窗格 |
| **`GetAddInObject(progID)`** | 🟡 中 — 获取插件对象 |
| **`SetUserPreferenceToggle(...)`** | 🟡 中 — 设置偏好 |

---

## 三、与 SolidworksMCP-TS 的差距对比

| 能力域 | 当前 Python MCP | SolidworksMCP-TS (TypeScript) |
|--------|----------------|-------------------------------|
| **MCP 工具数** | 15 | 100+ |
| **建模操作** | 30 个受控操作 | 25+ 建模工具 |
| **VBA 宏生成** | 受限（swb 被 SW2022 拒绝） | 100+ 宏模板 (handlebars) |
| **数据库集成** | 无 | mssql + pg |
| **PDM 操作** | 无 | 15+ PDM 工具 |
| **宏录制** | 无 | 5+ 录制工具 |
| **事件处理** | 无 | 事件订阅系统 |
| **知识库** | 无 | ChromaDB 向量数据库 |
| **资源管理** | 基本 | LRU 缓存 + 持久化 |

---

## 四、扩展建议（优先级排序）

### 🔴 第一阶段：基础增强（低风险，极高价值）

这些利用已有但未使用的 ISldWorks 方法，无需额外 DLL。

| 新增工具 | COM 接口 | 价值 |
|---------|---------|------|
| `sw_run_command` | `ISldWorks.RunCommand(swCommands_e)` | **执行任何 SolidWorks 命令** — 这是 1000+ 操作的网关 |
| `sw_list_commands` | swcommands.tlb 枚举解析 | 枚举所有可用命令 ID 供 AI 发现 |
| `sw_list_open_documents` | `GetDocumentCount()` + `GetDocuments()` | 列出所有打开的文档 |
| `sw_get_document_info` | `IModelDoc2.GetTitle()` + `GetPathName()` | 增强文档信息 |
| `sw_activate_document` | `ActivateDoc3()` | 切换活动文档 |
| `sw_close_document` | `CloseDoc()` + 路径守卫 | 安全关闭文档 |
| `sw_get_running_command` | `GetRunningCommandInfo()` | 检查当前正在运行的命令 |
| `sw_subscribe_events` | COM ConnectionPoint 事件 | 文档生命周期事件监听 |

### 🟠 第二阶段：文档管理增强

| 新增工具 | COM 接口 | 价值 |
|---------|---------|------|
| `sw_read_properties_offline` | **swdocumentmgr.dll** (SwDMApplication) | **无需 SolidWorks 读取属性！** |
| `sw_write_properties_offline` | SwDMDocument 第三方存储 | 无头批量属性写入 |
| `sw_read_configurations_offline` | SwDMConfiguration | 枚举配置 |
| `sw_read_bom_offline` | SwDMDocument 组件遍历 | 离线 BOM 提取 |
| `sw_get_thumbnail` | `IModelDoc2::GetPreviewBitmap` | 提取缩略图 |
| `sw_read_3rd_party_storage` | IStream + SwDM | 读写嵌入式自定义数据 |
| `sw_write_3rd_party_storage` | IStream + SwDM | 将 AI 元数据存入 SW 文件 |

### 🟡 第三阶段：交互增强

| 新增工具 | COM 接口 | 价值 |
|---------|---------|------|
| `sw_select_by_id` | `ModelDocExtension.SelectByID2` | 按 ID 选择实体 |
| `sw_get_selected_objects` | `ISelectionMgr` | 读取用户选择 |
| `sw_get_feature_tree` | `IFeatureManager` 遍历 | 导出特征树 |
| `sw_get_all_materials` | `IMaterialManager` | 列出可用材料 |
| `sw_get_appearances` | IMaterialManager 外观 | 颜色/外观管理 |
| `sw_create_ref_plane` | `FeatureManager.InsertRefPlane` | 创建参考平面 |
| `sw_create_ref_axis` | `InsertAxis2` | 创建参考轴 |
| `sw_get_equations` | `IEquationMgr` | 读取/写入方程 |
| `sw_set_equation` | `IEquationMgr` | 添加全局变量/方程 |

### 🔵 第四阶段：高级域

| 新增工具 | COM 接口 | 价值 |
|---------|---------|------|
| `sw_run_interference_check` | `IAssemblyDoc::ToolsCheckInterference2` | 干涉分析 |
| `sw_create_exploded_view` | IAssemblyDoc 爆炸视图 | 爆炸图生成 |
| `sw_add_dimxpert` | swdimxpert.tlb | GD&T 标注 |
| `sw_setup_simulation` | cosworks.tlb | FEA 仿真设置 |
| `sw_run_simulation` | cosworks.tlb | 运行仿真 |
| `sw_get_simulation_results` | cosworks.tlb | 读取仿真结果 |
| `sw_create_route` | SWRoutingLib.tlb | 管道/线缆布线 |
| `sw_estimate_cost` | sldcostingapi.tlb | 成本估算 |
| `sw_run_sustainability` | sustainability.tlb | 可持续性分析 |
| `sw_run_flow_simulation` | floworks.tlb | CFD 分析 |
| `sw_create_motion_study` | swmotionstudy.tlb | 运动分析 |
| `sw_render_photoview` | gabiswengine.tlb | PhotoView 渲染 |

---

## 五、swcommands.tlb 的巨大潜力

`swcommands.tlb` 包含列出 **所有 SolidWorks 内置命令** 的 `swCommands_e` 枚举。  
每个工具栏按钮、菜单项、右键菜单操作都对应一个命令 ID。

### 关键命令示例

```
swCommands_Save              — 保存文档
swCommands_Open              — 打开文件
swCommands_New               — 新建
swCommands_Print             — 打印
swCommands_Rebuild           — 重建模型
swCommands_ZoomToFit         — 适合窗口
swCommands_PreviousView      — 上一个视图
swCommands_Measure           — 测量工具
swCommands_MassProperties    — 质量属性
swCommands_SectionView       — 剖面视图
swCommands_Loft              — 放样
swCommands_Sweep             — 扫描
swCommands_HoleWizard        — 异形孔向导
swCommands_Fillet            — 圆角
swCommands_Chamfer           — 倒角
swCommands_DimXpert          — DimXpert
swCommands_SelectAll         — 全选
swCommands_Copy              — 复制
swCommands_Paste             — 粘贴
swCommands_Undo              — 撤销
swCommands_Redo              — 重做
swCommands_InsertBOMTable    — 插入 BOM 表
swCommands_InsertCenterMark  — 插入中心标记
swCommands_InsertCenterline  — 插入中心线
```

### 实现建议

```python
# 注册 swcommands 类型库
import comtypes.client
comtypes.client.GetModule(r"D:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\swcommands.tlb")

# 列出所有可用命令
# 在 Python 中可以用 dir() 枚举，或者解析 IDL

# 执行命令
sw.RunCommand(cmd_id_swCommands_Save, "")
```

---

## 六、事件系统的缺失

当前 MCP **完全没有事件处理**。SolidWorks 支持通过 COM ConnectionPoint 的事件。

### 可用事件

| 接口 | 事件 |
|------|------|
| `ISldWorks` | `ActiveModelDocChange`, `FileOpenNotify`, `FileCloseNotify`, `FileNewNotify`, `FileSaveAsNotify`, `DocumentLoadNotify`, `CommandOpenPreNotify`, `CommandCloseNotify`, `RebuildNotify` |
| `IPartDoc` | `NewSelectionNotify`, `ModifyNotify`, `FileSaveNotify` |
| `IAssemblyDoc` | `NewSelectionNotify`, `AddItemNotify`, `DeleteItemNotify` |
| `IDrawingDoc` | `NewSelectionNotify`, `AddItemNotify` |

### Python 中的事件实现

```python
import win32com.client

class SolidWorksEvents:
    def OnActiveModelDocChange(self):
        print("Active document changed!")
        return 0
    
    def OnFileSaveAsNotify(self, file_name):
        print(f"File saved: {file_name}")
        return 0

sw = win32com.client.Dispatch("SldWorks.Application")
# 使用 win32com WithEvents 或 comtypes events
```

---

## 七、swdocumentmgr.dll — 游戏规则改变者

SolidWorks 文档管理器 API **不需要运行 SolidWorks** 即可读写文件！

### 能力

- 读取/写入自定义属性（所有配置）
- 读取 BOM 信息（装配体）
- 读取配置列表
- 读取质量属性
- 第三方存储（在 SW 文件中嵌入任意数据）
- 文件依赖追踪
- 重命名/文件参考维护

### 实现关键

```python
# 通过 comtypes 访问 SwDM
import comtypes.client
comtypes.client.GetModule(r"D:\PROGRA~1\SOLIDWO~1\SOLIDWO~1\api\redist\SolidWorks.Interop.swdocumentmgr.dll")
# 需要许可证密钥！
```

**注意**: Document Manager 需要有效的 SolidWorks 订阅许可密钥。

---

## 八、实施路线图

### 第 1 周：基础增强（第 1 阶段）
- [ ] 注册 `swcommands.tlb` 类型库
- [ ] 实现 `sw_run_command` — 执行任意 SolidWorks 命令
- [ ] 实现 `sw_list_commands` — 枚举可用命令
- [ ] 实现 `sw_list_open_documents` — 列出打开的文档
- [ ] 实现 `sw_get_document_info` — 增强文档信息
- [ ] 实现 `sw_activate_document` — 切换活动文档
- [ ] 实现 `sw_close_document` — 安全关闭

### 第 2 周：事件与增强选择
- [ ] 实现 COM 事件订阅机制
- [ ] 添加 `sw_subscribe_document_events`
- [ ] 添加 `sw_get_selected_objects`
- [ ] 添加 `sw_select_by_id`（完全公开）
- [ ] 添加 `sw_get_feature_tree`

### 第 3 周：文档管理器（离线）
- [ ] 集成 swdocumentmgr.dll
- [ ] 实现离线属性读取/写入
- [ ] 实现离线配置枚举
- [ ] 实现第三方存储功能

### 第 4 周：材料/属性/方程
- [ ] `sw_get_all_materials` — 材料数据库
- [ ] `sw_get_equations` / `sw_set_equation`
- [ ] `sw_create_ref_plane` / `sw_create_ref_axis`

### 后续：高级域（按需）
- [ ] 仿真集成（cosworks.tlb）
- [ ] 布线集成（SWRoutingLib.tlb）
- [ ] 成本估算（sldcostingapi.tlb）
- [ ] GD&T 标注（swdimxpert.tlb）
- [ ] 渲染（gabiswengine.tlb）

---

## 九、技术注意事项

### 9.1 COM 线程模型
- SolidWorks 是单线程套间（STA）应用程序
- Python COM 调用需要适当线程化
- `pythoncom.CoInitialize()` 是必要的

### 9.2 类型库注册
```python
# 方式 1: comtypes（推荐用于类型安全）
import comtypes.client
comtypes.client.GetModule("path/to/sldworks.tlb")

# 方式 2: win32com（更简单但无类型安全）
import win32com.client
sw = win32com.client.Dispatch("SldWorks.Application")
```

### 9.3 安全考虑
- `RunCommand` 可以执行破坏性操作
- 需要确认/预检门禁
- 建议 `disabled_commands` 可配置列表
- 不应允许通过 MCP 执行宏

### 9.4 Document Manager 许可证
- 需要 SolidWorks 订阅有效的许可证密钥
- 密钥应来自环境变量，不硬编码
- 读取操作不需要许可证（仅写入需要）

### 9.5 向后兼容
- 所有新工具应是现有工具的补充，非替代
- 应保持受控生产工作流不变
- 新工具应标记 `experimental` 直到验证

---

## 十、参考资料

| 资源 | URL |
|------|-----|
| SolidWorks API 帮助 (2026) | https://help.solidworks.com/2026/english/api/sldworksapiprogguide/Welcome.htm |
| 离线 API 文档（LLM 友好） | https://github.com/pedropaulovc/offline-solidworks-api-docs |
| SolidworksMCP-TS（参考实现） | https://github.com/vespo92/SolidworksMCP-TS |
| xCAD.NET 框架 | https://github.com/xarial/xcad |
| CodeStack SW API 教程 | https://www.codestack.net/solidworks-api/ |
| CADSharp 事件处理 | https://www.cadsharp.com/blog/event-handlers-in-solidworks-addins/ |
| Adze CAD (AI 原生 SW 插件) | https://github.com/Kadenvh/adze-cad |
| 安装类型库目录 | `D:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\*.tlb` |
| 安装 API 重分发 | `D:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\redist\` |
