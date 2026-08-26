"""
gui/board_window.py
--------------------
"Tablero" (board) window: arranges several already-exported figures --
each added from the main canvas with its own title -- into rows of
side-by-side panels, previews the whole layout, and on export copies every
individual vector file plus generates the LaTeX (`core.latex.board_block`)
that reproduces the same arrangement in a report.

The window edits `app.board_rows` in place (a list of
`core.board.BoardRow`, i.e. list[BoardPanel]) so panels added from the main
window while this is closed, or added again after reopening it, are always
the live state -- there is no separate copy to keep in sync.
"""

from __future__ import annotations

import os
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from core import board, latex
from core.export import export_figure
from core.i18n import t

from .theme import col, font, spaced
from .widgets import (
    CodeDialog, Rule, entry_field, ghost_button, hint, primary_button,
    stacked_label,
)


def _parse_weight(text: str, fallback: float = 1.0) -> float:
    try:
        value = float(str(text).strip().replace(",", "."))
        return value if value > 0 else fallback
    except (TypeError, ValueError):
        return fallback


class BoardWindow(ctk.CTkToplevel):
    """Arrange, preview and export the multi-figure board."""

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app
        self.title(t("Tablero de figuras"))
        self.geometry("1040x700")
        self.minsize(820, 560)

        self._canvas: Optional[FigureCanvasTkAgg] = None

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(header, text=spaced(t("Tablero de figuras")), font=font("header"),
                     text_color=col("fg_muted")).pack(side="left")
        ghost_button(header, t("+ Nueva fila"), self._add_row, width=110
                     ).pack(side="right")
        Rule(self).pack(fill="x", padx=20)
        hint(self, t("← → reordena dentro de la fila · ↑ ↓ pasa el panel a la "
                    "fila de arriba/abajo · ▲ ▼ (junto a cada fila) reordena filas."),
             wraplength=980).pack(fill="x", padx=20, pady=(2, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(12, 8))
        body.grid_columnconfigure(0, weight=1, uniform="board")
        body.grid_columnconfigure(1, weight=1, uniform="board")
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(body, fg_color="transparent", corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.rows_container = left

        # A plain CTkFrame here would let the embedded preview canvas force
        # this whole Toplevel to grow taller every time a row is added --
        # `compose_preview_figure` sizes the figure at `row_height * n_rows`
        # inches, so a 4th or 5th board row can easily need more vertical
        # pixels than the screen has, pushing "Exportar tablero..." (packed
        # at the bottom of the window) past the bottom edge of the screen.
        # A CTkScrollableFrame clips its content to the viewport and shows
        # its own scrollbar instead of ever growing its parent, exactly like
        # `rows_container` already does on the left for the row editor.
        self.preview_container = ctk.CTkScrollableFrame(body, fg_color=col("surface"),
                                                        corner_radius=0)
        self.preview_container.grid(row=0, column=1, sticky="nsew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(0, 18))
        self.caption_var = ctk.StringVar(value="")
        self.label_var = ctk.StringVar(value="")
        entry_field(footer, t("Epígrafe general del tablero"), self.caption_var, rule=False,
                    label_width=170, width=220)
        entry_field(footer, "Label", self.label_var, rule=False,
                    label_width=170, width=220)
        hint(footer, t("El label se autocompleta a partir del epígrafe si se deja vacío. "
                      "El epígrafe de cada figura se edita arriba, panel por panel."),
             wraplength=420).pack(fill="x", pady=(0, 8))
        primary_button(footer, t("Exportar tablero..."), self._export,
                       height=32).pack(fill="x")

        self._refresh()
        self.transient(master)

    # ------------------------------------------------------------------ #
    # Row / panel editor
    # ------------------------------------------------------------------ #
    def _add_row(self) -> None:
        self.app.board_rows.append(board.new_row())
        self._refresh()

    def _remove_row(self, row_index: int) -> None:
        if 0 <= row_index < len(self.app.board_rows):
            del self.app.board_rows[row_index]
        if not self.app.board_rows:
            self.app.board_rows.append(board.new_row())
        self._refresh()

    def _remove_panel(self, row_index: int, panel_index: int) -> None:
        row = self.app.board_rows[row_index]
        del row[panel_index]
        self._refresh()

    def _move_panel(self, row_index: int, panel_index: int, delta: int) -> None:
        """Move a panel to the row above/below, appended at that row's end."""
        target = row_index + delta
        if not (0 <= target < len(self.app.board_rows)):
            return
        panel = self.app.board_rows[row_index].pop(panel_index)
        self.app.board_rows[target].append(panel)
        self._refresh()

    def _move_panel_within_row(self, row_index: int, panel_index: int, delta: int) -> None:
        """Reorder a panel against its left/right neighbour in the same row."""
        row = self.app.board_rows[row_index]
        target = panel_index + delta
        if not (0 <= target < len(row)):
            return
        row[panel_index], row[target] = row[target], row[panel_index]
        self._refresh()

    def _move_row(self, row_index: int, delta: int) -> None:
        """Reorder a whole row against the row above/below it."""
        rows = self.app.board_rows
        target = row_index + delta
        if not (0 <= target < len(rows)):
            return
        rows[row_index], rows[target] = rows[target], rows[row_index]
        self._refresh()

    def _set_title(self, panel, value: str) -> None:
        panel.title = value
        self._refresh_preview()

    def _set_weight(self, panel, entry: ctk.CTkEntry) -> None:
        panel.weight = _parse_weight(entry.get(), fallback=panel.weight or 1.0)
        entry.delete(0, "end")
        entry.insert(0, f"{panel.weight:g}")
        self._refresh_preview()

    def _refresh(self) -> None:
        for child in self.rows_container.winfo_children():
            child.destroy()

        rows = self.app.board_rows
        for r, row in enumerate(rows):
            block = ctk.CTkFrame(self.rows_container, fg_color=col("surface"),
                                 corner_radius=0)
            block.pack(fill="x", pady=(0, 10))

            head = ctk.CTkFrame(block, fg_color="transparent")
            head.pack(fill="x", padx=10, pady=(8, 4))
            ctk.CTkLabel(head, text=f"{t('Fila')} {r + 1}", font=font("label"),
                        text_color=col("fg_muted")).pack(side="left")
            ghost_button(head, t("Eliminar fila"), lambda i=r: self._remove_row(i),
                        width=100, height=24).pack(side="right")
            ghost_button(head, "▼", lambda i=r: self._move_row(i, 1),
                        width=28, height=24).pack(side="right", padx=(2, 4))
            ghost_button(head, "▲", lambda i=r: self._move_row(i, -1),
                        width=28, height=24).pack(side="right")

            if not row:
                hint(block, t("Vacía -- agregá un gráfico desde la ventana principal."),
                     wraplength=380).pack(fill="x", padx=10, pady=(0, 10))
                continue

            for p, panel in enumerate(row):
                stacked_label(block, t("Epígrafe")).pack(
                    fill="x", padx=10, pady=(0, 0))
                strip = ctk.CTkFrame(block, fg_color="transparent")
                strip.pack(fill="x", padx=10, pady=(0, 8))

                title_var = ctk.StringVar(value=panel.title)
                title_entry = ctk.CTkEntry(strip, textvariable=title_var,
                                           height=28, font=font("body"))
                title_entry.pack(side="left", fill="x", expand=True)
                title_var.trace_add(
                    "write", lambda *_a, pv=title_var, pn=panel: self._set_title(pn, pv.get()))

                weight_entry = ctk.CTkEntry(strip, width=48, height=28,
                                            font=font("mono"), justify="right")
                weight_entry.insert(0, f"{panel.weight:g}")
                weight_entry.pack(side="left", padx=(6, 0))
                weight_entry.bind(
                    "<Return>", lambda _e, pn=panel, w=weight_entry: self._set_weight(pn, w))
                weight_entry.bind(
                    "<FocusOut>", lambda _e, pn=panel, w=weight_entry: self._set_weight(pn, w))

                # Reorder within the same row (left/right neighbour)...
                ghost_button(strip, "←", lambda i=r, j=p: self._move_panel_within_row(i, j, -1),
                            width=28, height=28).pack(side="left", padx=(6, 0))
                ghost_button(strip, "→", lambda i=r, j=p: self._move_panel_within_row(i, j, 1),
                            width=28, height=28).pack(side="left", padx=(2, 0))
                # ...or hand it off to the row above/below entirely.
                ghost_button(strip, "↑", lambda i=r, j=p: self._move_panel(i, j, -1),
                            width=28, height=28).pack(side="left", padx=(6, 0))
                ghost_button(strip, "↓", lambda i=r, j=p: self._move_panel(i, j, 1),
                            width=28, height=28).pack(side="left", padx=(2, 0))
                ghost_button(strip, "✕", lambda i=r, j=p: self._remove_panel(i, j),
                            width=28, height=28).pack(side="left", padx=(2, 0))

        if not rows:
            stacked_label(self.rows_container, t("El tablero está vacío."))

        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if self._canvas is not None:
            self._canvas.get_tk_widget().destroy()
            self._canvas = None
        fig = board.compose_preview_figure(self.app.board_rows)
        self._canvas = FigureCanvasTkAgg(fig, master=self.preview_container)
        self._canvas.draw()
        self._canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=8)

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def _export(self) -> None:
        rows = self.app.board_rows
        errors = board.validate_board(rows)
        if errors:
            messagebox.showerror(t("Tablero incompleto"), "\n".join(errors))
            return

        out_dir = filedialog.askdirectory(
            title=t("Carpeta de destino para los PDFs del tablero"))
        if not out_dir:
            return

        try:
            copied = board.export_individual_pdfs(rows, out_dir)
        except OSError as exc:
            messagebox.showerror(t("Error al exportar"), str(exc))
            return

        # Panels re-pointed at the copies just written, in the same
        # row/column order, so the LaTeX below matches what actually landed
        # in `out_dir` rather than the original (possibly scratch) paths.
        dest_iter = iter(copied)
        dest_rows = []
        for row in rows:
            dest_row = []
            for panel in row:
                dest_row.append(board.BoardPanel(
                    title=panel.title, vector_path=next(dest_iter),
                    preview_path=panel.preview_path, weight=panel.weight))
            dest_rows.append(dest_row)

        try:
            preview_path = os.path.join(out_dir, "tablero_overview.png")
            export_figure(board.compose_preview_figure(rows), preview_path, dpi=150)
        except Exception:
            preview_path = ""   # purely a convenience thumbnail: never fatal

        relative_to = os.path.dirname(out_dir) or out_dir
        default_caption = self.caption_var.get().strip() or t("Figuras del ensayo")
        default_label = self.label_var.get().strip() or latex.sanitize_label(default_caption)
        self.caption_var.set(default_caption)
        self.label_var.set(default_label)

        title_key = t("Epígrafe general del tablero")

        def build(values: dict) -> str:
            return latex.board_block(
                dest_rows, caption=values.get(title_key, ""),
                label=values.get("Label", ""), relative_to=relative_to,
                escape_titles=not bool(values.get("__toggle__")))

        initial = build({title_key: default_caption, "Label": default_label,
                         "__toggle__": False})
        note = (f"{latex.board_requirements()}    ·    "
                f"{len(copied)} {t('archivo(s) copiados a')}: {out_dir}")
        if preview_path:
            note += f"    ·    {t('Vista previa (no vectorial)')}: {preview_path}"

        CodeDialog(
            self, t("Incluir tablero en LaTeX"), initial, note=note,
            fields=[(title_key, self.caption_var), ("Label", self.label_var)],
            rebuild=build,
            extra_toggle=(t("Escapar caracteres especiales de los títulos "
                            "(desactivalo si escribís $matemática$)"), False))
