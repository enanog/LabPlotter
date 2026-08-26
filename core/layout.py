"""
core/layout.py
--------------
Figure layout concerns: legend placement geometry and the margin reservation
needed when the legend is anchored outside the axes area.

This module deliberately lives outside `core/export.py`: exporting data and
placing a legend are different responsibilities, and keeping the geometry here
means the export path stays untouched. An older, narrower `legend_kwargs()`
used to live in `core/export.py` too (no "outside left/bottom/top" support,
no `frameon`); it was unused dead code -- the GUI has only ever imported the
richer version from here -- and was removed rather than kept in sync by hand.

All positions are resolved to plain Matplotlib `Axes.legend()` keyword
arguments, so nothing here depends on the GUI toolkit and the same placement
is reproducible from a script.
"""

from __future__ import annotations

from typing import Optional, Sequence

# Label shown in the GUI for the free-coordinate mode.
CUSTOM_POSITION = "personalizada (x, y)"

# Anchor corner of the legend box itself when using custom coordinates: the
# given (x, y) point is attached to this corner of the legend.
CUSTOM_ANCHOR_CORNERS: list[str] = [
    "upper left", "upper center", "upper right",
    "center left", "center", "center right",
    "lower left", "lower center", "lower right",
]

# Positions offered in the GUI. Anything not present in _OUTSIDE_SPECS and not
# equal to CUSTOM_POSITION is passed through to Matplotlib verbatim as `loc`.
LEGEND_POSITIONS: list[str] = [
    "best",
    "upper right", "upper left", "lower right", "lower left",
    "center right", "center left", "upper center", "lower center",
    "outside right", "outside right top", "outside left",
    "outside top", "outside bottom",
    "outside center right",          # legacy label, kept for compatibility
    CUSTOM_POSITION,
]

# position label -> (loc, bbox_to_anchor) in axes-fraction coordinates.
_OUTSIDE_SPECS: dict[str, tuple[str, tuple[float, float]]] = {
    "outside right":        ("center left",  (1.02, 0.50)),
    "outside right top":    ("upper left",   (1.02, 1.00)),
    "outside center right": ("center left",  (1.02, 0.50)),   # legacy alias
    "outside left":         ("center right", (-0.02, 0.50)),
    "outside top":          ("lower center", (0.50, 1.02)),
    "outside bottom":       ("upper center", (0.50, -0.14)),
}

# Subplot margins that guarantee the outside legend is visible on screen.
# `tight_layout()` ignores legends anchored outside the axes, so the caller
# applies these right after it.
_OUTSIDE_MARGINS: dict[str, dict[str, float]] = {
    "outside right":        {"right": 0.80},
    "outside right top":    {"right": 0.80},
    "outside center right": {"right": 0.80},
    "outside left":         {"left": 0.26},
    "outside top":          {"top": 0.86},
    "outside bottom":       {"bottom": 0.24},
}

_OUTSIDE_PREFIX = "outside "


def is_outside(position: str) -> bool:
    """True when the position anchors the legend beyond the axes area."""
    return str(position).startswith(_OUTSIDE_PREFIX)


def legend_kwargs(
    position: str,
    anchor: Optional[Sequence[float]] = None,
    corner: str = "upper left",
    ncol: int = 1,
    frameon: bool = True,
) -> dict:
    """
    Translate a GUI legend position into `Axes.legend()` keyword arguments.

    position : one of LEGEND_POSITIONS.
    anchor   : (x, y) in axes-fraction coordinates, used only when
               `position == CUSTOM_POSITION`. Values outside [0, 1] place the
               legend outside the axes, which is exactly the point.
    corner   : which corner of the legend box is pinned to `anchor`.
    ncol     : number of legend columns (useful for a legend below the axes).
    """
    ncol = max(1, int(ncol or 1))
    base = {"ncol": ncol, "frameon": bool(frameon)}

    if position == CUSTOM_POSITION:
        if anchor is None:
            anchor = (1.02, 1.0)
        loc = corner if corner in CUSTOM_ANCHOR_CORNERS else "upper left"
        return {**base, "loc": loc,
                "bbox_to_anchor": (float(anchor[0]), float(anchor[1])),
                "borderaxespad": 0.0}

    if position in _OUTSIDE_SPECS:
        loc, bbox = _OUTSIDE_SPECS[position]
        return {**base, "loc": loc, "bbox_to_anchor": bbox, "borderaxespad": 0.0}

    return {**base, "loc": position}


def reserve_legend_space(
    fig,
    position: str,
    anchor: Optional[Sequence[float]] = None,
) -> None:
    """
    Shrink the subplot area so an outside legend is not clipped on screen.

    Must be called *after* `fig.tight_layout()`, which recomputes the margins
    from the axes decorations only and would otherwise undo this. Export uses
    `bbox_inches="tight"`, so the saved file is safe either way; this only
    keeps the interactive preview faithful to the export.
    """
    margins: dict[str, float] = {}

    if position in _OUTSIDE_MARGINS:
        margins = dict(_OUTSIDE_MARGINS[position])
    elif position == CUSTOM_POSITION and anchor is not None:
        try:
            ax_x, ax_y = float(anchor[0]), float(anchor[1])
        except (TypeError, ValueError, IndexError):
            return
        # Reserve room only on the side the legend actually overflows.
        if ax_x > 1.0:
            margins["right"] = max(0.55, min(0.92, 1.0 - 0.18 * (ax_x - 0.9)))
        if ax_x < 0.0:
            margins["left"] = min(0.45, max(0.12, 0.14 - 0.20 * ax_x))
        if ax_y > 1.0:
            margins["top"] = max(0.70, min(0.95, 1.0 - 0.16 * (ax_y - 0.9)))
        if ax_y < 0.0:
            margins["bottom"] = min(0.40, max(0.10, 0.12 - 0.22 * ax_y))

    if not margins:
        return
    try:
        fig.subplots_adjust(**margins)
    except (ValueError, AttributeError):
        pass   # inconsistent margins (very small figure): keep tight_layout
