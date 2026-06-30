from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

DEFAULT_LAYOUT_CLEARANCE_M: Final = 0.001
FLOAT_TOLERANCE_M: Final = 1e-9

FitStatus = Literal["already_inside", "position_adjusted", "scale_adjusted", "cannot_fit"]


@dataclass(frozen=True, slots=True)
class DrawingPoint:
    x: float
    y: float

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True, slots=True)
class DrawingOutline:
    left: float
    bottom: float
    right: float
    top: float

    @property
    def width(self) -> float:
        return max(self.right - self.left, 0.0)

    @property
    def height(self) -> float:
        return max(self.top - self.bottom, 0.0)

    @property
    def center(self) -> DrawingPoint:
        return DrawingPoint(x=(self.left + self.right) / 2.0, y=(self.bottom + self.top) / 2.0)

    def to_dict(self) -> dict[str, float]:
        return {"left": self.left, "bottom": self.bottom, "right": self.right, "top": self.top}


@dataclass(frozen=True, slots=True)
class DrawingSafeRect:
    left: float
    bottom: float
    right: float
    top: float

    @property
    def width(self) -> float:
        return max(self.right - self.left, 0.0)

    @property
    def height(self) -> float:
        return max(self.top - self.bottom, 0.0)

    def to_dict(self) -> dict[str, float]:
        return {"left": self.left, "bottom": self.bottom, "right": self.right, "top": self.top}


@dataclass(frozen=True, slots=True)
class DrawingViewFit:
    status: FitStatus
    scale: float
    scale_multiplier: float
    target_center: DrawingPoint
    needs_position: bool
    needs_scale: bool

    def to_dict(self) -> dict[str, float | str | bool | dict[str, float]]:
        return {
            "status": self.status,
            "scale": self.scale,
            "scale_multiplier": self.scale_multiplier,
            "target_center": self.target_center.to_dict(),
            "needs_position": self.needs_position,
            "needs_scale": self.needs_scale,
        }


def fit_outline_inside_safe_rect(
    outline: DrawingOutline,
    safe_rect: DrawingSafeRect,
    current_scale: float,
    *,
    clearance_m: float = DEFAULT_LAYOUT_CLEARANCE_M,
) -> DrawingViewFit:
    center = outline.center
    available_width = safe_rect.width - (clearance_m * 2.0)
    available_height = safe_rect.height - (clearance_m * 2.0)
    if outline.width <= 0.0 or outline.height <= 0.0 or available_width <= 0.0 or available_height <= 0.0:
        return DrawingViewFit(
            status="cannot_fit",
            scale=current_scale,
            scale_multiplier=1.0,
            target_center=center,
            needs_position=False,
            needs_scale=False,
        )

    scale_multiplier = min(1.0, available_width / outline.width, available_height / outline.height)
    if scale_multiplier <= 0.0:
        return DrawingViewFit(
            status="cannot_fit",
            scale=current_scale,
            scale_multiplier=1.0,
            target_center=center,
            needs_position=False,
            needs_scale=False,
        )

    fitted_width = outline.width * scale_multiplier
    fitted_height = outline.height * scale_multiplier
    target_center = DrawingPoint(
        x=_clamp(center.x, safe_rect.left + clearance_m + fitted_width / 2.0, safe_rect.right - clearance_m - fitted_width / 2.0),
        y=_clamp(center.y, safe_rect.bottom + clearance_m + fitted_height / 2.0, safe_rect.top - clearance_m - fitted_height / 2.0),
    )
    needs_scale = scale_multiplier < 1.0 - FLOAT_TOLERANCE_M
    needs_position = (
        abs(target_center.x - center.x) > FLOAT_TOLERANCE_M
        or abs(target_center.y - center.y) > FLOAT_TOLERANCE_M
    )
    status = _fit_status(needs_scale=needs_scale, needs_position=needs_position)
    return DrawingViewFit(
        status=status,
        scale=current_scale * scale_multiplier if needs_scale else current_scale,
        scale_multiplier=scale_multiplier,
        target_center=target_center,
        needs_position=needs_position,
        needs_scale=needs_scale,
    )


def _fit_status(*, needs_scale: bool, needs_position: bool) -> FitStatus:
    if needs_scale:
        return "scale_adjusted"
    if needs_position:
        return "position_adjusted"
    return "already_inside"


def _clamp(value: float, lower: float, upper: float) -> float:
    if lower > upper:
        midpoint = (lower + upper) / 2.0
        return midpoint
    return min(max(value, lower), upper)
