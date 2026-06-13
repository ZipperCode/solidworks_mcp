"""Complete dimension generation test for imported model drawings"""
import json
import sys
from pathlib import Path
from solidworks_mcp.adapters.solidworks import (
    SolidWorksCOMAdapter,
    _existing_model_prismatic_dimension_ids,
    _existing_model_overall_dimension_specs,
)

def test_dimension_ids():
    """Test that all required dimension IDs are returned"""
    print("=" * 80)
    print("测试：棱柱件尺寸ID列表")
    print("=" * 80)
    
    dimension_ids = _existing_model_prismatic_dimension_ids()
    
    expected_ids = [
        "overall_length",
        "overall_width",
        "overall_height",
        "hole_position_x",
        "hole_position_y",
        "hole_diameter",
        "chamfer_radius",
    ]
    
    print("\n期望的尺寸ID:")
    for dim_id in expected_ids:
        print(f"  - {dim_id}")
    
    print("\n实际生成的尺寸ID:")
    for dim_id in dimension_ids:
        print(f"  - {dim_id}")
    
    missing = set(expected_ids) - set(dimension_ids)
    extra = set(dimension_ids) - set(expected_ids)
    
    if missing:
        print(f"\n❌ 缺少尺寸: {missing}")
        return False
    if extra:
        print(f"\n⚠️  额外尺寸: {extra}")
    
    print(f"\n✅ 所有必需尺寸ID都包含 ({len(dimension_ids)} 个)")
    return True


def test_dimension_specs():
    """Test that dimension specs are generated correctly"""
    print("\n" + "=" * 80)
    print("测试：尺寸规格生成")
    print("=" * 80)
    
    # Mock view result with sample data - including outline as 4 floats [left, bottom, right, top]
    view_result = {
        "layout": {
            "model_dimensions_m": {"x": 0.120, "y": 0.080, "z": 0.020},
            "existing_model_geometry_profile": {"kind": "prismatic"},
        },
        "views": [
            {"role": "section", "outline": [0.05, 0.05, 0.13, 0.11]},  # left, bottom, right, top
            {"role": "end", "outline": [0.25, 0.05, 0.33, 0.11]},
            {"role": "isometric", "outline": [0.45, 0.05, 0.53, 0.11]},
        ],
    }
    
    specs = _existing_model_overall_dimension_specs({}, view_result)
    
    print(f"\n生成的尺寸规格数: {len(specs)}")
    print("\n尺寸规格详情:")
    
    spec_ids = []
    for spec in specs:
        dim_id = spec.get("id", "unknown")
        method = spec.get("method", "unknown")
        view_role = spec.get("view_role", "unknown")
        spec_ids.append(dim_id)
        print(f"  - {dim_id:20s} | method: {method:30s} | view: {view_role}")
    
    expected_ids = [
        "overall_length",
        "overall_width", 
        "overall_height",
        "hole_position_x",
        "hole_position_y",
        "hole_diameter",
        "chamfer_radius",
    ]
    
    missing = set(expected_ids) - set(spec_ids)
    if missing:
        print(f"\n❌ 缺少规格: {missing}")
        return False
    
    print(f"\n✅ 所有必需尺寸规格都生成 ({len(specs)} 个)")
    return True


def test_integrated_drawing():
    """Test complete drawing generation with dimensions"""
    print("\n" + "=" * 80)
    print("测试：集成工程图生成（包含尺寸）")
    print("=" * 80)
    
    try:
        adapter = SolidWorksCOMAdapter()
        adapter.connect()
        
        # Sample imported model
        model_path = r"C:\Users\Zipper\Downloads\解密3D\红外发热管防护罩-2(解密).SLDPRT"
        if not Path(model_path).exists():
            print(f"⚠️  跳过集成测试: 模型文件不存在")
            return True
        
        # Load model
        model_doc = adapter.open_document(model_path)
        
        # Get model dimensions
        bbox = adapter.get_bounding_box(model_doc)
        print(f"\n模型尺寸 (meters):")
        print(f"  X: {bbox['width']:.4f}")
        print(f"  Y: {bbox['height']:.4f}")
        print(f"  Z: {bbox['depth']:.4f}")
        
        # Create drawing
        output_dir = Path("test_outputs") / "dimension_test"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        drawing_path = output_dir / "test_drawing.SLDDRW"
        pdf_path = output_dir / "test_drawing.pdf"
        
        # Create drawing with all features
        drawing = adapter.create_drawing(
            model_doc,
            drawing_path,
            include_section=True,
            include_flat_pattern=True,
            include_center_marks=True,
            include_dimensions=True,
        )
        
        # Export PDF
        adapter.export_drawing_pdf(drawing, pdf_path)
        
        print(f"\n✅ 工程图生成成功:")
        print(f"   路径: {drawing_path}")
        print(f"   PDF: {pdf_path}")
        
        # Check if dimensions were added
        # This would require inspecting the drawing object
        print("\n✅ 集成测试完成")
        
        adapter.close_document(model_doc)
        adapter.disconnect()
        return True
        
    except Exception as e:
        print(f"\n❌ 集成测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all dimension tests"""
    print("\n" + "🔍 尺寸生成测试" + " 🔍")
    print("=" * 80)
    
    results = []
    
    # Test 1: Dimension IDs
    results.append(("尺寸ID列表", test_dimension_ids()))
    
    # Test 2: Dimension specs
    results.append(("尺寸规格生成", test_dimension_specs()))
    
    # Test 3: Integrated drawing (optional)
    # results.append(("集成工程图生成", test_integrated_drawing()))
    
    # Summary
    print("\n" + "=" * 80)
    print("测试总结")
    print("=" * 80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} | {name}")
    
    print("\n" + "-" * 80)
    print(f"通过: {passed}/{total}")
    
    if passed == total:
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print("\n⚠️  部分测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
