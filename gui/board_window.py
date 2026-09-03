"""
gui/board_window.py
--------------------
"Tablero" (board) stage: arma varias figuras ya exportadas -- cada una
agregada desde el canvas principal con su propio título -- en filas de
paneles lado a lado, previsualiza el layout completo y al exportar copia
cada archivo vectorial individual y genera el LaTeX (`core.latex.board_block`)
que reproduce la misma disposición en el informe.

Hasta una sesión anterior esto era un `ctk.CTkToplevel` flotante.
`BoardEditor` pasa a ser un controlador que construye el editor de
filas/paneles en el navigator del stage "board" (`App.navigators["board"]`)
y la vista previa en un frame del workspace (`App._board_plot_frame`),
intercambiado por `App._show_plot_frame` -- ya no hay una ventana aparte que
abrir ni perder de vista.

Edita `app.board_rows` en el lugar (una lista de `core.board.BoardRow`, es
decir list[BoardPanel]), así que los paneles agregados desde el canvas
principal mientras este stage no está activo, o agregados de nuevo al volver
a entrar, siempre son el estado vivo -- no hay una copia separada que
mantener sincronizada.
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

from .theme import col, font
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


class BoardEditor:
    """
    Arma, previsualiza y exporta el tablero multi-figura.

    No es un `ctk.CTkFrame` -- su contenido vive en dos parents distintos
    (navigator + workspace). `nav_frame`/`plot_frame` quedan expuestos para
    que `App._rebuild_ui` los destruya junto con el resto antes de
    reconstruir todo con un `BoardEditor` nuevo.
    """

    def __init__(self, app, nav_parent, plot_parent) -> None:
        self.app = app
        self._canvas: Optional[FigureCanvasTkAgg] = None

        self.nav_frame = self._build_controls(nav_parent)
        self.plot_frame = self._build_canvas(plot_parent)

        self._refresh()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _build_controls(self, parent) -> ctk.CTkFrame:
        nav = ctk.CTkFrame(parent, fg_color="transparent", width=1, height=1)
        nav.pack(fill="both", expand=True)

        # Adding a panel used to mean leaving this stage entirely (only the
        # "Exportar" stage's navigator had the button) -- go there, click,
        # come back to see it. This is the same action
        # (`App._add_current_to_board`), reachable from right here instead:
        # it grabs whatever the tab strip's ACTIVE tab is currently showing
        # (any plot mode, Histograma included), so composing a board is
        # switch tab -> click -> repeat, without ever leaving "Tablero".
        primary_button(nav, t("+ Agregar gráfico actual"),
                       self.app._add_current_to_board, height=32
                       ).pack(fill="x", pady=(0, 10))

        header = ctk.CTkFrame(nav, fg_color="transparent")
        header.pack(fill="x")
        ghost_button(header, t("+ Nueva fila"), self._add_row, width=110
                     ).pack(side="right")
        hint(nav, t("← → reordena dentro de la fila · ↑ ↓ pasa el panel a la "
                    "fila de arriba/abajo · ▲ ▼ (junto a cada fila) reordena "
                    "filas."), wraplength=270).pack(fill="x", pady=(4, 8))

        self.rows_container = ctk.CTkScrollableFrame(
            nav, fg_color="transparent", corner_radius=0)
        self.rows_container.pack(fill="both", expand=True, pady=(0, 10))

        Rule(nav).pack(fill="x", pady=(0, 10))

        self.caption_var = ctk.StringVar(value="")
        self.label_var = ctk.StringVar(value="")
        entry_field(nav, t("Epígrafe general del tablero"), self.caption_var,
                    rule=False)
        entry_field(nav, "Label", self.label_var, rule=False)
        hint(nav, t("El label se autocompleta a partir del epígrafe si se "
                    "deja vacío. El epígrafe de cada figura se edita arriba, "
                    "panel por panel."), wraplength=270).pack(fill="x", pady=(0, 8))
        primary_button(nav, t("Exportar tablero..."), self._export,
                       height=32).pack(fill="x")
        return nav

    def _build_canvas(self, parent) -> ctk.CTkFrame:
        self.preview_container = ctk.CTkScrollableFrame(
            parent, fg_color=col("surface"), corner_radius=0)
        self.preview_container.pack(fill="both", expand=True)
        return self.preview_container

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
                        width=90, height=24).pack(side="right")
            ghost_button(head, "▼", lambda i=r: self._move_row(i, 1),
                        width=26, height=24).pack(side="right", padx=(2, 4))
            ghost_button(head, "▲", lambda i=r: self._move_row(i, -1),
                        width=26, height=24).pack(side="right")

            if not row:
                hint(block, t("Vacía -- agregá un gráfico desde la ventana principal."),
                     wraplength=270).pack(fill="x", padx=10, pady=(0, 10))
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

                weight_entry = ctk.CTkEntry(strip, width=40, height=28,
                                            font=font("mono"), justify="right")
                weight_entry.insert(0, f"{panel.weight:g}")
                weight_entry.pack(side="left", padx=(4, 0))
                weight_entry.bind(
                    "<Return>", lambda _e, pn=panel, w=weight_entry: self._set_weight(pn, w))
                weight_entry.bind(
                    "<FocusOut>", lambda _e, pn=panel, w=weight_entry: self._set_weight(pn, w))

                # Reorder within the same row (left/right neighbour)...
                ghost_button(strip, "←", lambda i=r, j=p: self._move_panel_within_row(i, j, -1),
                            width=24, height=28).pack(side="left", padx=(4, 0))
                ghost_button(strip, "→", lambda i=r, j=p: self._move_panel_within_row(i, j, 1),
                            width=24, height=28).pack(side="left", padx=(2, 0))
                # ...or hand it off to the row above/below entirely.
                ghost_button(strip, "↑", lambda i=r, j=p: self._move_panel(i, j, -1),
                            width=24, height=28).pack(side="left", padx=(4, 0))
                ghost_button(strip, "↓", lambda i=r, j=p: self._move_panel(i, j, 1),
                            width=24, height=28).pack(side="left", padx=(2, 0))
                ghost_button(strip, "✕", lambda i=r, j=p: self._remove_panel(i, j),
                            width=24, height=28).pack(side="left", padx=(2, 0))

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
            self.app, t("Incluir tablero en LaTeX"), initial, note=note,
            fields=[(title_key, self.caption_var), ("Label", self.label_var)],
            rebuild=build,
            extra_toggle=(t("Escapar caracteres especiales de los títulos "
                            "(desactivalo si escribís $matemática$)"), False))
