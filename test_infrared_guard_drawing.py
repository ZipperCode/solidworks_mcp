"""实际零件测试：红外发热管防护罩-2 工程图生成"""
import json
import sys
from pathlib import Path

# 添加 src 目录到 Python 路径
project_root = Path(__file__).parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

from solidworks_mcp.adapters import create_adapter
from solidworks_mcp.config import SolidWorksMCPConfig
from solidworks_mcp.executor import ModelPlanExecutor


def test_infrared_guard_drawing():
    """测试红外发热管防护罩-2的工程图生成"""
    
    # 实际零件文件路径
    model_path = Path(r"C:\Users\Zipper\Downloads\解密3D\红外发热管防护罩-2(解密).SLDPRT")
    
    if not model_path.exists():
        print(f"❌ 模型文件不存在: {model_path}")
        return False
    
    print(f"✅ 找到模型文件: {model_path.name}")
    print(f"   文件大小: {model_path.stat().st_size / 1024:.1f} KB")
    
    # 创建测试计划
    plan_data = {
        "name": "infrared_guard_2_test",
        "operations": [
            {
                "op": "import_existing_model",
                "parameters": {
                    "path": str(model_path),
                    "model_type": "part"
                }
            },
            {
                "op": "make_drawing",
                "parameters": {
                    "drawing_profile": "A3",
                    "include_dimensions": True,
                    "include_center_marks": True
                }
            }
        ],
        "metadata": {
            "test_type": "actual_part",
            "part_name": "红外发热管防护罩-2",
            "geometry_type": "prismatic"  # 棱柱件/板料
        }
    }
    
    # 执行测试
    print("\n" + "="*80)
    print("正在生成工程图...")
    print("="*80)
    
    import os
    # 禁用工作流约束以便测试尺寸生成
    os.environ["SOLIDWORKS_MCP_ENFORCE_TRUSTED_WORKFLOW"] = "0"
    os.environ["SOLIDWORKS_MCP_REQUIRE_DIRECT_HOLE_CALLOUT"] = "1"
    
    config = SolidWorksMCPConfig.from_env()
    adapter = create_adapter(config)
    executor = ModelPlanExecutor(adapter, config)
    
    try:
        report = executor.execute_plan(plan_data, confirmed=True)
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # 提取诊断信息
    diagnostics = report.diagnostics if hasattr(report, 'diagnostics') else {}
    
    # 输出报告关键字段
    print("\n" + "="*80)
    print("执行报告")
    print("="*80)
    
    print(f"✅ 计划执行成功: {report.ok}")
    print(f"📊 消息: {report.message}")
    
    # 工程图相关信息
    print(f"\n📐 工程图状态:")
    print(f"   视图创建: {diagnostics.get('drawing_view_status', 'N/A')}")
    print(f"   标注状态: {diagnostics.get('drawing_annotation_status', 'N/A')}")
    print(f"   尺寸状态: {diagnostics.get('drawing_dimension_status', 'N/A')}")
    print(f"   尺寸布局: {diagnostics.get('dimension_layout_status', 'N/A')}")
    
    # 详细尺寸信息 - 来自 drawing_dimension_result
    dim_result = diagnostics.get('drawing_dimension_result', {})
    print(f"\n📏 尺寸详情:")
    required_dims = dim_result.get('required_dimensions', [])
    created_dims = dim_result.get('created_dimensions', [])
    missing_dims = dim_result.get('missing_dimensions', [])
    
    print(f"   需要尺寸: {len(required_dims)} 个")
    print(f"   创建尺寸: {len(created_dims)} 个")
    print(f"   缺失尺寸: {len(missing_dims)} 个")
    
    if created_dims:
        print("\n   ✅ 已创建的尺寸:")
        for dim in created_dims:
            dim_id = dim.get('id', 'unknown') if isinstance(dim, dict) else dim
            method = dim.get('method', 'unknown') if isinstance(dim, dict) else 'N/A'
            print(f"      - {dim_id} ({method})")
    
    if missing_dims:
        print("\n   ❌ 缺失的尺寸:")
        for dim in missing_dims:
            print(f"      - {dim}")
    
    # 中心标记 - 来自 drawing_annotation_result
    anno_result = diagnostics.get('drawing_annotation_result', {})
    center_count = anno_result.get('center_mark_count', 0)
    print(f"\n⭕ 中心标记: {center_count} 个")
    
    # 模型几何体信息
    print(f"\n📦 模型几何体:")
    print(f"   几何体验证: {diagnostics.get('model_geometry_status', 'N/A')}")
    print(f"   质量属性验证: {diagnostics.get('mass_property_status', 'N/A')}")
    
    model_geometry_result = diagnostics.get('model_geometry_result', {})
    if model_geometry_result:
        dimensions = model_geometry_result.get('dimensions_mm', {})
        if dimensions:
            print(f"   尺寸 (mm): {dimensions}")
    
    # 生产接受度结果
    print(f"\n🏆 生产接受度:")
    prod_result = diagnostics.get('production_acceptance_result', {})
    prod_status = prod_result.get('status', 'unknown')
    print(f"   状态: {prod_status}")
    
    if prod_status == 'accepted':
        print("   ✅ 所有生产门控通过!")
    else:
        print("   ⚠️ 存在生产门控失败:")
        checks = prod_result.get('checks', {})
        for check_name, check_value in checks.items():
            status = "✅" if check_value else "❌"
            print(f"      {status} {check_name}")
    
    # 输出报告文件位置
    if report.report_file:
        print(f"\n📁 完整报告: {report.report_file}")
    
    return prod_status == 'accepted'


if __name__ == "__main__":
    success = test_infrared_guard_drawing()
    
    print("\n" + "="*80)
    if success:
        print("✅ 测试通过！工程图生成符合生产标准")
    else:
        print("❌ 测试失败！请检查详细报告")
    print("="*80)
    
    sys.exit(0 if success else 1)
