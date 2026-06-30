from __future__ import annotations

from solidworks_mcp.adapters.solidworks import (
    SolidWorksCOMAdapter,
    _existing_model_geometry_profile,
    _existing_model_overall_dimension_specs,
)


class FakeDrawingWithModelAnnotations:
    def InsertModelAnnotations3(self, *_args) -> None:
        return None


def test_existing_model_geometry_profile_treats_thin_near_square_plate_as_prismatic() -> None:
    profile = _existing_model_geometry_profile({"x": 0.016005, "y": 0.757005, "z": 0.819005})

    assert profile["kind"] == "prismatic"
    assert profile["draft_classification"] == "imported_prismatic_machining_draft"
    assert profile["reason"] == "thin_sheet_like_bbox"


def test_existing_model_geometry_profile_keeps_long_cylinder_rotational() -> None:
    profile = _existing_model_geometry_profile({"x": 0.150, "y": 0.150, "z": 0.700})

    assert profile["kind"] == "rotational"
    assert profile["draft_classification"] == "imported_rotational_machining_draft"


def test_insert_model_annotations_does_not_satisfy_required_prismatic_dimensions() -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    adapter._drawing_view_handles = {}
    adapter.record_com_call = lambda *_args, **_kwargs: None
    adapter.record_event = lambda *_args, **_kwargs: None
    view_result = {
        "layout": {
            "layout_style": "manufacturing_rotational",
            "existing_model_geometry_profile": {"kind": "prismatic"},
        }
    }
    result = {"attempts": []}

    final = adapter._try_insert_existing_model_overall_dimensions(
        FakeDrawingWithModelAnnotations(),
        None,
        view_result,
        ["overall_length", "overall_width"],
        result,
    )

    assert final["import_model_dimensions_result"]["status"] == "completed_unverified"
    assert final["import_model_dimensions_result"]["trusted_for_required_dimensions"] is False
    assert final["status"] == "dimension_creation_failed"
    assert final["missing_dimensions"] == ["overall_length", "overall_width"]


def test_prismatic_overall_length_uses_section_horizontal_extent() -> None:
    view_result = {
        "layout": {
            "existing_model_geometry_profile": {"kind": "prismatic"},
            "model_dimensions_m": {"x": 0.016, "y": 0.757, "z": 0.819},
        },
        "views": [
            {"role": "section", "outline": [0.04, 0.12, 0.19, 0.26]},
            {"role": "end", "outline": [0.29, 0.12, 0.31, 0.27]},
        ],
    }

    specs = _existing_model_overall_dimension_specs({}, view_result)
    overall_length = next(spec for spec in specs if spec["id"] == "overall_length")

    assert overall_length["view_role"] == "section"
    assert overall_length["method"] == "AddHorizontalDimension2"
    assert overall_length["edge_selector_data"]["axis"] == "x"


def test_drawing_view_fit_rechecks_live_outline_after_scale() -> None:
    adapter = SolidWorksCOMAdapter.__new__(SolidWorksCOMAdapter)
    view = object()
    adapter._drawing_view_handles = {"section": view}
    outlines = [
        [0.0, 0.0, 10.5, 10.5],
        [0.001, 0.001, 9.999, 9.999],
        [0.001, 0.001, 9.999, 9.999],
    ]
    scale_targets = []
    align_targets = []

    def fake_outline(_view):
        return outlines.pop(0)

    def fake_scale(_view, scale):
        scale_targets.append(scale)
        return {"status": "scale_set", "target_scale": scale}

    def fake_rebuild(_purpose):
        return {"status": "rebuilt"}

    def fake_align(_view, x, y):
        align_targets.append((x, y))
        return {"status": "outline_center_aligned"}

    adapter._drawing_view_outline = fake_outline
    adapter._set_drawing_view_scale = fake_scale
    adapter._rebuild_drawing = fake_rebuild
    adapter._align_drawing_view_outline_center = fake_align
    layout = {"safe_rect_m": {"left": 0.0, "bottom": 0.0, "right": 10.0, "top": 10.0}}
    views = [{"role": "section", "scale": 1.0, "outline": [0.0, 0.0, 20.0, 20.0]}]

    result = adapter._fit_drawing_views_to_safe_rect(layout, views)

    post_fit = result["adjustments"][0]["post_fit_result"]
    assert result["adjusted_view_count"] == 1
    assert post_fit["changed"] is True
    assert len(scale_targets) == 2
    assert len(align_targets) == 2
    assert views[0]["outline"] == [0.001, 0.001, 9.999, 9.999]
