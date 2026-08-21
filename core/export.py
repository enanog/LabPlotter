"""
core/export.py
-----------------
Global Matplotlib style configuration and export routines.

Data export: PGFPlots-ready CSV (individual or combined on a common grid).
Figure export: vector PDF / PGF and 300 DPI raster PNG.

The external TeX engine is never used: `text.usetex` is forced to False and
all math is rendered with Matplotlib's built-in mathtext engine. This keeps
PDF export fast, lightweight and free of any system LaTeX installation,
while still producing true vector output.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Optional, Sequence

import matplotlib as mpl
import numpy as np

from .data_io import x_units_for_domain, y_units_for_kind

# Font presets exposed in the GUI. Each maps to a concrete font stack plus
# the Matplotlib *generic* family it must be registered under ("serif",
# "sans-serif" or "monospace" -- the only values Matplotlib's `font.family`
# accepts as a bucket key), and the matching mathtext fontset so inline math
# ($...$) visually matches the surrounding text.
#
# "LaTeX (Computer Modern)" targets reports written with `\usepackage{lmodern}`
# (Latin Modern, the standard drop-in replacement/extension of Computer
# Modern): it tries "Latin Modern Roman" first (present on most systems that
# have a TeX distribution installed, e.g. MiKTeX/TeX Live on Windows), then
# "CMU Serif" (another common Computer Modern Unicode port), and finally
# "cmr10" -- a Computer Modern font Matplotlib ships internally, so this
# preset always renders correctly even with no TeX/system fonts installed.
FONT_PRESETS: dict[str, dict] = {
    "sans-serif": {
        "generic": "sans-serif",
        "fonts": ["DejaVu Sans", "Arial", "Helvetica"],
        "mathtext": "dejavusans",
    },
    "serif": {
        "generic": "serif",
        "fonts": ["DejaVu Serif", "Times New Roman"],
        "mathtext": "dejavuserif",
    },
    "monospace": {
        "generic": "monospace",
        "fonts": ["DejaVu Sans Mono", "Courier New"],
        "mathtext": "dejavusans",
    },
    "LaTeX (Computer Modern)": {
        "generic": "serif",
        "fonts": ["Latin Modern Roman", "CMU Serif", "cmr10", "DejaVu Serif"],
        "mathtext": "cm",
    },
}

# Backward-compatible alias: other modules only need the preset names to
# populate the GUI dropdown (`FONT_FAMILIES.keys()`).
FONT_FAMILIES = FONT_PRESETS

# Legend position labels shown in the GUI. "outside center right" is not a
# native Matplotlib loc; it is resolved via bbox_to_anchor in legend_kwargs().
LEGEND_POSITIONS: list[str] = [
    "upper right", "upper left", "lower right", "lower left",
    "center right", "outside center right",
]

_OUTSIDE_PREFIX = "outside "


def set_publication_style(font_family: str = "sans-serif", base_fontsize: int = 10) -> None:
    """
    Apply a clean publication-oriented Matplotlib style.

    `text.usetex` is hard-disabled by design: mathtext renders `$...$`
    expressions internally, avoiding a LaTeX subprocess on every draw and on
    every PDF save. Both the interactive preview and the exported figure go
    through the exact same rcParams, so what is shown is what is exported.
    """
    family = font_family if font_family in FONT_PRESETS else "sans-serif"
    preset = FONT_PRESETS[family]
    generic = preset["generic"]           # always a valid font.family bucket
    mpl.rcParams.update({
        "font.family": generic,
        f"font.{generic}": preset["fonts"],
        "mathtext.fontset": preset["mathtext"],
        "mathtext.default": "regular",
        "text.usetex": False,          # never invoke an external TeX engine
        "axes.labelsize": base_fontsize,
        "axes.titlesize": base_fontsize + 1,
        "xtick.labelsize": base_fontsize - 1,
        "ytick.labelsize": base_fontsize - 1,
        "legend.fontsize": base_fontsize - 1,
        "legend.framealpha": 0.9,
        "legend.edgecolor": "0.6",
        "lines.linewidth": 1.2,
        "axes.linewidth": 0.8,
        "axes.formatter.use_mathtext": True,
        "axes.unicode_minus": False,   # avoid missing U+2212 glyph warnings
        "grid.linewidth": 0.4,
        "grid.alpha": 0.5,
        "figure.dpi": 100,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "pdf.fonttype": 42,            # embed TrueType, keep text selectable
        "ps.fonttype": 42,
    })


def legend_kwargs(position: str) -> dict:
    """
    Translate a GUI legend position into Matplotlib legend keyword arguments.

    Positions prefixed with "outside " are placed beyond the axes area using
    bbox_to_anchor, which requires the caller to leave room (tight bbox on
    save, or a constrained layout on screen).
    """
    if position.startswith(_OUTSIDE_PREFIX):
        base = position[len(_OUTSIDE_PREFIX):]
        if base == "center right":
            return {"loc": "center left", "bbox_to_anchor": (1.02, 0.5),
                    "borderaxespad": 0.0}
        return {"loc": base, "bbox_to_anchor": (1.02, 1.0), "borderaxespad": 0.0}
    return {"loc": position}


def _sanitize_filename(name: str) -> str:
    """Sanitize a signal name for safe use as a filename."""
    name = re.sub(r"[^\w\-.]", "_", name.strip())
    return name or "signal"


def _y_column_name(y_kind: str, y_unit: str) -> str:
    """
    Build the Y column header. dB and degree traces carry their unit in the
    kind itself, so appending a voltage unit there would be misleading.
    """
    if y_kind == "dB":
        return "dB"
    if y_kind == "deg":
        return "deg"
    return f"V_{y_unit}"


def _scale_pair(x: np.ndarray, y: np.ndarray, domain: str, y_kind: str,
                x_unit: str, y_unit: str) -> tuple[np.ndarray, np.ndarray]:
    """Convert base-unit arrays into the requested display units."""
    x_factor = x_units_for_domain(domain).get(x_unit, 1.0)
    y_factor = y_units_for_kind(y_kind).get(y_unit, 1.0)
    return x / x_factor, y / y_factor


def export_csv_individual(
    signals_data: Sequence[tuple[str, np.ndarray, np.ndarray, str, str]],
    out_dir: str,
    x_unit: str,
    y_unit: str,
    fmt: str = "%.6e",
    sep: str = ",",
) -> list[str]:
    """
    Write one CSV per signal with two columns ready for
    `\\addplot table[x=..., y=..., col sep=comma]`.

    signals_data : sequence of (name, x, y, domain, y_kind) already cropped
                   and decimated, with x/y in base units.
    """
    os.makedirs(out_dir, exist_ok=True)
    paths: list[str] = []
    for name, x, y, domain, y_kind in signals_data:
        if len(x) == 0:
            continue
        x_disp, y_disp = _scale_pair(x, y, domain, y_kind, x_unit, y_unit)
        x_col = ("f_" + x_unit) if domain == "freq" else ("t_" + x_unit)
        y_col = _y_column_name(y_kind, y_unit)
        path = os.path.join(out_dir, f"{_sanitize_filename(name)}.csv")
        np.savetxt(path, np.column_stack([x_disp, y_disp]), delimiter=sep,
                   header=f"{x_col}{sep}{y_col}", comments="", fmt=fmt)
        paths.append(path)
    if not paths:
        raise ValueError("No hay señales con datos para exportar.")
    return paths


def export_csv_combined(
    signals_data: Sequence[tuple[str, np.ndarray, np.ndarray, str, str]],
    out_path: str,
    x_unit: str,
    y_unit: str,
    n_points: int = 500,
    fmt: str = "%.6e",
    sep: str = ",",
) -> str:
    """
    Write a single CSV sharing one interpolated X grid, with one Y column per
    signal. Requires the signals to overlap on the X axis; signals of mixed
    domains (time vs frequency) are rejected because a common grid would be
    physically meaningless.
    """
    usable = [s for s in signals_data if len(s[1]) >= 2]
    if not usable:
        raise ValueError("Se requieren al menos 2 puntos por señal para el modo combinado.")

    domains = {s[3] for s in usable}
    if len(domains) > 1:
        raise ValueError(
            "No se pueden combinar señales de dominios distintos (tiempo y "
            "frecuencia) en una grilla común. Usá la exportación individual.")
    domain = usable[0][3]

    x_min = max(float(s[1][0]) for s in usable)
    x_max = min(float(s[1][-1]) for s in usable)
    if not np.isfinite(x_min) or not np.isfinite(x_max) or x_min >= x_max:
        raise ValueError(
            "Las señales seleccionadas no comparten un rango común en X; "
            "usá la exportación individual en su lugar.")

    # Log-spaced grid for frequency sweeps preserves decade resolution.
    if domain == "freq" and x_min > 0:
        x_grid = np.logspace(np.log10(x_min), np.log10(x_max), n_points)
    else:
        x_grid = np.linspace(x_min, x_max, n_points)

    x_factor = x_units_for_domain(domain).get(x_unit, 1.0)
    columns = [x_grid / x_factor]
    headers = [("f_" + x_unit) if domain == "freq" else ("t_" + x_unit)]

    for name, x, y, _domain, y_kind in usable:
        y_grid = np.interp(x_grid, x, y)
        y_factor = y_units_for_kind(y_kind).get(y_unit, 1.0)
        columns.append(y_grid / y_factor)
        headers.append(f"{_y_column_name(y_kind, y_unit)}_{_sanitize_filename(name)}")

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    np.savetxt(out_path, np.column_stack(columns), delimiter=sep,
               header=sep.join(headers), comments="", fmt=fmt)
    return out_path


def export_xy_csv(
    curves: Sequence[tuple[str, np.ndarray, np.ndarray]],
    out_dir: str,
    fmt: str = "%.6e",
    sep: str = ",",
) -> list[str]:
    """Write parametric X/Y curves (Lissajous / transfer curves), one CSV each."""
    os.makedirs(out_dir, exist_ok=True)
    paths: list[str] = []
    for name, x, y in curves:
        if len(x) == 0:
            continue
        path = os.path.join(out_dir, f"{_sanitize_filename(name)}_xy.csv")
        np.savetxt(path, np.column_stack([x, y]), delimiter=sep,
                   header=f"x{sep}y", comments="", fmt=fmt)
        paths.append(path)
    if not paths:
        raise ValueError("No hay curvas X/Y con datos para exportar.")
    return paths


def export_figure(fig, out_path: str, dpi: int = 300,
                  transparent: bool = False) -> str:
    """
    Save the figure to PDF / PGF (vector) or PNG (raster at `dpi`).

    The figure carries the styling already applied in the GUI, so colors,
    fonts and legend placement are preserved verbatim. `bbox_inches="tight"`
    guarantees that an outside-anchored legend is not clipped.
    """
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight",
                transparent=transparent, facecolor=fig.get_facecolor())
    return out_path
