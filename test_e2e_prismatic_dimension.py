"""端到端集成测试：导入件工程图完整尺寸生成"""
import json
from pathlib import Path
from solidworks_mcp.solidworks_adapter import SolidWorksAdapter


def test_end_to_end_dimension_generation():
    """测试导入件工程图完整尺寸生成的端到端流程"""
    print("=" * 80)
    print("端到端测试：导入件工程图完整尺寸生成")
    print("=" * 80)
    
    # 测试用的模型文件
    model_path = Path("C:/Users/Zipper/Downloads/解密3D/红外发热管防护罩-2(解密).SLDPRT")
    if not model_path.exists():
        print(f"警告: 模型文件不存在: {model_path}")
        print("跳过实际生成测试，仅验证规格生成逻辑")
        return False
    
    output_dir = Path("D:/Project/GitHub/solidworks_mcp/test_outputs/prismatic_dimension_test")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "prismatic_dimension_test.SLDDRW"
    
    try:
        # 初始化适配器
        adapter = SolidWorksAdapter()
        
        print("\n连接到 SolidWorks...")
        adapter.connect_to_solidworks()
        
        print(f"\n导入模型: {model_path.name}")
        adapter.import_model(str(model_path))
        
        print(f"\n生成工程图: {output_file.name}")
        drawing_config = {
            "views": [
                {
                    "type": "standard",
                    "view_names": ["*Front", "*Top", "*Right", "*Isometric"],
                    "layout": "A3",
                }
            ],
            "sheet_format": "A3",
            "scale": 1.0,
            "include_section_view": True,
            "include_flat_pattern_view": False,  # 对非钣金件
            "include_center_marks": True,
            "include_dimensions": True,  # 启用尺寸生成
        }
        
        adapter.create_drawing_from_model(str(output_file), drawing_config)
        
        # 读取生成的工程图进行验证
        print("\n验证生成的工程图...")
        
        # 打开工程图
        adapter.open_drawing(str(output_file))
        
        # 读取尺寸信息
        drawing_info = adapter.get_drawing_info()
        
        print(f"\n工程图信息:")
        print(f"  - 视图数: {drawing_info.get('view_count', 0)}")
        print(f"  - 尺寸数: {drawing_info.get('dimension_count', 0)}")
        print(f"  - 中心标记数: {drawing_info.get('center_mark_count', 0)}")
        
        # 读取具体的尺寸详情
        dimensions = drawing_info.get("dimensions", [])
        print(f"\n尺寸详情 ({len(dimensions)} 个):")
        
        dimension_types = {}
        for dim in dimensions:
            dim_id = dim.get("id", "unknown")
            value = dim.get("value", 0.0)
            unit = dim.get("unit", "mm")
            dimension_types[dim_id] = dimension_types.get(dim_id, 0) + 1
            print(f"  - {dim_id}: {value:.3f} {unit}")
        
        # 验证所有期望的尺寸类型
        expected_types = [
            "overall_length",
            "overall_width",
            "overall_height",
            "hole_position_x",
            "hole_position_y",
            "hole_diameter",
            "chamfer_radius",
        ]
        
        print(f"\n尺寸类型验证:")
        all_present = True
        for expected_type in expected_types:
            count = dimension_types.get(expected_type, 0)
            status = "✅" if count > 0 else "❌"
            print(f"  {status} {expected_type}: {count} 个")
            if count == 0:
                all_present = False
        
        print(f"\n中心标记验证:")
        center_mark_count = drawing_info.get("center_mark_count", 0)
        print(f"  {'✅' if center_mark_count > 0 else '❌'} 中心标记: {center_mark_count} 个")
        
        # 关闭工程图
        adapter.close_drawing()
        
        # 关闭 SolidWorks
        adapter.disconnect()
        
        if all_present and center_mark_count > 0:
            print("\n✅ 端到端测试通过：所有尺寸类型都已生成")
            return True
        else:
            print("\n❌ 端到端测试失败：部分尺寸类型缺失")
            return False
            
    except Exception as e:
        print(f"\n❌ 端到端测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_end_to_end_dimension_generation()
    exit(0 if success else 1)
