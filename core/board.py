"""
core/board.py
--------------
Multi-panel "board" composition: arrange several already-exported figures,
each with its own title, into a row-based layout, so more than one plot can
sit on the same page/screen -- then hand back the individual vector files
and the LaTeX code that reproduces that same layout in a report.

Layout model
------------
A board is a list of *rows*; each row is a list of `BoardPanel`. Panels in
the same row sit side by side, their relative width given by `weight`
(equal weights by default, so an N-panel row splits evenly); rows stack top
to bottom. This covers every layout a report figure normally needs -- one
wide panel, two or three side by side, an uneven split, a big panel on top
of two smaller ones below, a 2x3 grid as three rows of two, etc. -- without
the overlap bookkeeping a full row/col-span grid would need, and it maps
directly onto the `subfigure` idiom LaTeX itself uses for the same kind of
layout (see `core.latex.board_block`).

Each panel keeps two paths:

- `vector_path`: the real exported figure (PDF/SVG/PGF). This is what the
  generated LaTeX embeds and what `export_individual_pdfs` copies out --
  it is never rasterized or re-encoded here.
- `preview_path`: a lightweight PNG snapshot used only to draw the
  on-screen board preview. Matplotlib cannot rasterize a vector PDF back
  into an array without an extra dependency (poppler/PyMuPDF), so the
  caller (the GUI) is expected to save this alongside the vector export,
  from the very same figure, at "add to board" time.

`core/` still does not import anything from `gui/`: this module only knows
about file paths and Matplotlib, exactly like `core/export.py`.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from typing import Sequence

import matplotlib.image as mpimg
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpecFromSubplotSpec

_FILENAME_CLEAN = re.compile(r"[^A-Za-z0-9_-]+")


def slugify_filename(text: str, fallback: str = "panel") -> str:
    """
    Turn a panel title into a filesystem-safe basename (no extension),
    e.g. "Respuesta en frecuencia" -> "Respuesta_en_frecuencia". Used so a
    panel renamed in the board window is exported under that same name,
    instead of the (frozen) name it happened to get when first added.
    """
    slug = _FILENAME_CLEAN.sub("_", (text or "").strip()).strip("_")
    return slug or fallback


@dataclass
class BoardPanel:
    """One figure placed on the board."""

    title: str
    vector_path: str            # exported PDF/SVG/PGF -- what LaTeX embeds
    preview_path: str = ""      # PNG snapshot, board preview only
    weight: float = 1.0         # relative width within its own row


BoardRow = list  # list[BoardPanel], kept as a plain alias for readability


def new_row() -> "list[BoardPanel]":
    return []


def total_panels(rows: Sequence[Sequence[BoardPanel]]) -> int:
    return sum(len(row) for row in rows)


def validate_board(rows: Sequence[Sequence[BoardPanel]]) -> list[str]:
    """Human-readable problems with the board; empty means it is exportable."""
    errors: list[str] = []
    if not rows or total_panels(rows) == 0:
        errors.append("El tablero no tiene ningún panel todavía.")
        return errors
    for i, row in enumerate(rows, start=1):
        if not row:
            errors.append(f"La fila {i} está vacía: agregá un panel o eliminala.")
            continue
        for panel in row:
            label = panel.title or "(sin título)"
            if panel.weight <= 0:
                errors.append(f"«{label}» tiene un ancho relativo inválido.")
            if not panel.vector_path or not os.path.isfile(panel.vector_path):
                errors.append(f"«{label}» no tiene una figura exportada válida en disco.")
    return errors


def compose_preview_figure(
    rows: Sequence[Sequence[BoardPanel]],
    row_height: float = 2.4,
    fig_width: float = 12.0,
    title_fontsize: int = 10,
) -> Figure:
    """
    Raster preview of the whole board: one row per board row, each panel
    drawn from its PNG snapshot with its title above it.

    This is an on-screen arrangement aid, not a publication asset -- the
    real figures stay vector and are exported/embedded untouched by this
    function.
    """
    n_rows = max(len(rows), 1)
    fig = Figure(figsize=(fig_width, row_height * n_rows))
    outer = fig.add_gridspec(n_rows, 1, hspace=0.55)

    for r, row in enumerate(rows):
        n_cols = max(len(row), 1)
        weights = [max(p.weight, 1e-6) for p in row] or [1.0]
        inner = GridSpecFromSubplotSpec(
            1, n_cols, subplot_spec=outer[r], width_ratios=weights, wspace=0.08)
        for c, panel in enumerate(row):
            ax = fig.add_subplot(inner[0, c])
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)
            if panel.preview_path and os.path.isfile(panel.preview_path):
                try:
                    ax.imshow(mpimg.imread(panel.preview_path))
                except Exception:
                    ax.text(0.5, 0.5, "(vista previa inválida)", ha="center",
                            va="center", transform=ax.transAxes,
                            fontsize=title_fontsize - 1, color="0.5")
            else:
                ax.text(0.5, 0.5, "(sin vista previa)", ha="center", va="center",
                        transform=ax.transAxes, fontsize=title_fontsize - 1,
                        color="0.5")
            if panel.title:
                ax.set_title(panel.title, fontsize=title_fontsize)
    return fig


def export_individual_pdfs(
    rows: Sequence[Sequence[BoardPanel]],
    out_dir: str,
) -> list[str]:
    """
    Copy every panel's already-exported vector file into `out_dir`, renaming
    it to match the panel's *current* title -- so renaming a panel in the
    board window (after it was first added to the board) changes the
    exported PDF's filename too, instead of it staying frozen at whatever
    slug it got at "add to board" time. Names are de-duplicated when two
    panels share a title; order is row-major, matching the order used in
    `core.latex.board_block`.
    """
    os.makedirs(out_dir, exist_ok=True)
    written: list[str] = []
    # Tracks actual FILENAMES already handed out, not base slugs: keying the
    # de-dup counter by the base alone let two different bases collide once
    # a suffix was appended -- e.g. a panel titled "Foo 1" (slug "Foo_1",
    # written as-is) and a later panel titled "Foo" (slug "Foo") would both
    # want the name "Foo_1.pdf" the second time "Foo" repeated, so the
    # second panel's copy silently clobbered the first panel's file on disk.
    used_names: set[str] = set()
    for row in rows:
        for panel in row:
            ext = os.path.splitext(panel.vector_path)[1]
            fallback = os.path.splitext(os.path.basename(panel.vector_path))[0]
            base = slugify_filename(panel.title, fallback)
            name = f"{base}{ext}"
            n = 1
            while name in used_names:
                name = f"{base}_{n}{ext}"
                n += 1
            used_names.add(name)
            dest = os.path.join(out_dir, name)
            if os.path.normcase(os.path.abspath(panel.vector_path)) != \
                    os.path.normcase(os.path.abspath(dest)):
                shutil.copyfile(panel.vector_path, dest)
            written.append(dest)
    return written
