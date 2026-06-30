from __future__ import annotations

from solidworks_mcp.drawing_layout_fit import (
    DrawingOutline,
    DrawingSafeRect,
    fit_outline_inside_safe_rect,
)


def test_fit_outline_inside_safe_rect_moves_low_isometric_view_up() -> None:
    safe_rect = DrawingSafeRect(left=0.018, bottom=0.060, right=0.402, top=0.279)
    outline = DrawingOutline(
        left=0.2709501787120965,
        bottom=0.05746648885995054,
        right=0.33490582128790336,
        top=0.13699351114004946,
    )

    fit = fit_outline_inside_safe_rect(outline, safe_rect, current_scale=0.6137173333333332)

    assert fit.status == "position_adjusted"
    assert fit.target_center.y > outline.center.y
    assert fit.scale == 0.6137173333333332


def test_fit_outline_inside_safe_rect_moves_tall_end_view_down() -> None:
    safe_rect = DrawingSafeRect(left=0.018, bottom=0.060, right=0.402, top=0.279)
    outline = DrawingOutline(
        left=0.2735250142372863,
        bottom=0.1339367999999998,
        right=0.3323309857627135,
        top=0.2839032000000002,
    )

    fit = fit_outline_inside_safe_rect(outline, safe_rect, current_scale=1.1702237288135588)

    assert fit.status == "position_adjusted"
    assert fit.target_center.y < outline.center.y
    assert fit.scale == 1.1702237288135588


def test_fit_outline_inside_safe_rect_scales_view_that_cannot_fit() -> None:
    safe_rect = DrawingSafeRect(left=0.0, bottom=0.0, right=0.10, top=0.10)
    outline = DrawingOutline(left=0.0, bottom=0.0, right=0.20, top=0.10)

    fit = fit_outline_inside_safe_rect(outline, safe_rect, current_scale=1.0)

    assert fit.status == "scale_adjusted"
    assert fit.scale < 1.0
    assert fit.target_center.x == 0.05
