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
from typing import Optional, Sequence

import matplotlib as mpl
import numpy as np
from matplotlib import font_manager

from .data_io import x_units_for_domain, y_units_for_kind

# Font presets exposed in the GUI. Each maps to a concrete font stack plus
# the Matplotlib *generic* family it must be registered under ("serif",
# "sans-serif" or "monospace" -- the only values Matplotlib's `font.family`
# accepts as a bucket key), and the matching mathtext fontset so inline math
# ($...$) visually matches the surrounding text.
#
# "LaTeX (Computer Modern)" targets reports written with `\usepackage{lmodern}`
# (Latin Modern, the standard drop-in replacement/extension of Computer
# Modern). Its mathtext fontset is "custom" rather than Matplotlib's built-in
# "cm": "cm" always renders math -- including `\text{...}` inside it -- with
# Matplotlib's own BUNDLED cmr10/cmmi10 files, which only cover the original
# 1980s 8-bit Computer Modern glyph set. That set is missing ordinary
# lowercase accented Latin letters (á é í ó ú ñ and their uppercase forms
# except Á), so ANY Spanish word inside `\text{...}`, or in plain (non-math)
# text if this preset's regular font also resolved to that same bundled
# cmr10, silently drew as a missing-glyph box -- e.g. "Medición" came out as
# "Medici[]n". A previous version of this preset put "cmr10" first in
# `fonts` specifically to make plain text and `$...$` text pixel-identical;
# that traded away Spanish rendering entirely to fix a font-matching nuance,
# which is the wrong trade for reports written in Spanish. "custom" instead
# points mathtext at `mathtext.rm`/`it`/`bf`, set in `set_publication_style`
# below to whichever font in `fonts` Matplotlib actually resolves on this
# machine -- typically "Latin Modern Roman" from a MiKTeX/TeX Live install,
# which (unlike cmr10) has full accented-Latin coverage and still reads as
# Computer Modern. Math and plain text end up using the exact same font file
# again, but one that can actually render "Medición", "según", "año", etc.
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
        # "DejaVu Serif" last: it ships with Matplotlib itself, so it is
        # always resolvable and this preset can never fail outright. It is
        # not Computer-Modern-styled, but it does have full accent coverage.
        "fonts": ["Latin Modern Roman", "CMU Serif", "DejaVu Serif"],
        "mathtext": "custom",
    },
}


def _resolve_font(candidates: list[str]) -> str:
    """
    First font NAME in `candidates` Matplotlib can actually find installed
    on this machine, falling back to the last entry (by convention always a
    font bundled with Matplotlib, e.g. "DejaVu Serif" -- guaranteed present)
    if none of the earlier, more specific choices are available.
    """
    for name in candidates[:-1]:
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except Exception:
            continue
    return candidates[-1]

# Backward-compatible alias: other modules only need the preset names to
# populate the GUI dropdown (`FONT_FAMILIES.keys()`).
FONT_FAMILIES = FONT_PRESETS


def set_publication_style(font_family: str = "sans-serif", base_fontsize: int = 10,
                          legend_fontsize: Optional[float] = None) -> None:
    """
    Apply a clean publication-oriented Matplotlib style.

    `text.usetex` is hard-disabled by design: mathtext renders `$...$`
    expressions internally, avoiding a LaTeX subprocess on every draw and on
    every PDF save. Both the interactive preview and the exported figure go
    through the exact same rcParams, so what is shown is what is exported.

    `legend_fontsize` is independent from `base_fontsize` so the legend can
    be enlarged (e.g. for a projector, or a dense legend with many entries)
    without also blowing up the axis labels and ticks; `None` keeps the
    previous behaviour of one point smaller than the base size.
    """
    family = font_family if font_family in FONT_PRESETS else "sans-serif"
    preset = FONT_PRESETS[family]
    generic = preset["generic"]           # always a valid font.family bucket
    legend_size = base_fontsize - 1 if legend_fontsize is None else legend_fontsize

    mathtext_rc = {"mathtext.fontset": preset["mathtext"]}
    if preset["mathtext"] == "custom":
        # Point mathtext at whichever font in `fonts` is actually installed
        # (see the FONT_PRESETS comment above), instead of Matplotlib's
        # built-in "cm" fontset -- so `$...$` math keeps using the exact
        # same font file as the surrounding plain text, one that can render
        # accented Spanish letters.
        resolved = _resolve_font(preset["fonts"])
        mathtext_rc.update({
            "mathtext.rm": resolved,
            "mathtext.it": f"{resolved}:italic",
            "mathtext.bf": f"{resolved}:bold",
        })

    mpl.rcParams.update({
        "font.family": generic,
        f"font.{generic}": preset["fonts"],
        **mathtext_rc,
        "mathtext.default": "regular",
        "text.usetex": False,          # never invoke an external TeX engine
        "axes.labelsize": base_fontsize,
        "axes.titlesize": base_fontsize + 1,
        "xtick.labelsize": base_fontsize - 1,
        "ytick.labelsize": base_fontsize - 1,
        "legend.fontsize": legend_size,
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
