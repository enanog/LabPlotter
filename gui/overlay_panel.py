"""
gui/overlay_panel.py
--------------------
CustomTkinter front-end for `gui.overlays`: a floating palette holding the
cursor bench and the annotation editor.

It is built entirely from `gui.widgets`, so it cannot drift from the main
window's aesthetic: same square hairlines, same letterspaced small-caps
headers, same label-left / control-right rows, same progressive disclosure.

Two deliberate structural choices:

* a separate, **non-modal** `CTkToplevel` rather than a fourth column --
  placing a cursor or capturing a coordinate means clicking *on the canvas*
  while the palette is open, so it must never grab the event loop
  (`grab_set()` is never called);
* the annotation form is split across collapsibles, so the twenty-odd
  parameters an annotation can carry are never all on screen at once.
"""

from __future__ import annotations

import math
from tkinter import colorchooser, filedialog, messagebox
from typing import Callable, Optional

import customtkinter as ctk

from core.i18n import t
from core.units import parse_eng

from .overlays import (
    ANNOTATION_KINDS, ARROW_STYLES, AnnotationManager, AnnotationSpec,
    CursorManager, FONT_FAMILIES, FONT_STYLES, FONT_WEIGHTS, HA_CHOICES,
    KIND_DEFAULTS, LINESTYLES, STYLE_PRESETS, VA_CHOICES, format_eng,
    load_overlays, save_overlays,
)
from .theme import col, font, spaced
from .widgets import (
    ROW_HEIGHT, MeasurementsCard, Rule, SectionHeader, Segmented, StaticSection,
    check_field,
    combo_field, entry_field, ghost_button, hint, primary_button,
    stacked_entry, stacked_label,
)

# Internal pane ids; the visible text comes from `_pane_labels()`.
PANES = ["cursors", "annotations"]


def _pane_labels() -> dict:
    return {"cursors": t("Cursores"), "annotations": t("Anotaciones")}

# Fields each annotation kind actually uses. Keeping the layout fixed and
# greying out the rest avoids the dialog reflowing on every kind change,
# which is especially jarring at Windows DPI scaling.
_KIND_FIELDS: dict[str, set[str]] = {
    "point": {"x", "y", "text", "dx", "dy", "arrow", "fontsize", "boxed", "color"},
    "arrow": {"x", "y", "x2", "y2", "text", "dx", "dy", "arrow", "linewidth",
              "fontsize", "boxed", "color"},
    "vline": {"x", "text", "linestyle", "linewidth", "rotation", "label_pos",
              "fontsize", "boxed", "color"},
    "hline": {"y", "text", "linestyle", "linewidth", "rotation", "label_pos",
              "fontsize", "boxed", "color"},
    "text":  {"x", "y", "text", "rotation", "fontsize", "boxed", "color"},
    "vspan": {"x", "x2", "text", "alpha", "label_pos", "fontsize", "boxed", "color"},
    "hspan": {"y", "y2", "text", "alpha", "label_pos", "fontsize", "boxed", "color"},
}

# Sentinel for "inherit the rcParams family": an empty string cannot be shown
# in a CTkComboBox without looking like a rendering glitch.
FONT_DEFAULT = "(por defecto)"

# Every kind carries a text label, so the typography controls apply to all.
_TEXT_STYLE_FIELDS = {"fontfamily", "fontweight", "fontstyle", "ha", "va"}
for _kind in _KIND_FIELDS:
    _KIND_FIELDS[_kind] |= _TEXT_STYLE_FIELDS


def _parse_float(text: str, fallback: float = 0.0) -> float:
    """
    Defensive numeric parsing with engineering notation.

    Annotation coordinates are exactly where `9.61k` gets typed instead of
    `9610`, so these fields go through the same parser as the rest of the
    application -- see `core.units.parse_eng`.
    """
    value = parse_eng(text, None)
    return fallback if value is None else value


def _clean(label: str) -> str:
    """Readable version of a mathtext label for a plain-text list."""
    return (label or "").replace("$", "").replace("\\", "")


class OverlayPanel(ctk.CTkFrame):
    """Cursor bench + annotation editor. Embeddable in any CTk container."""

    def __init__(self, master, cursors: CursorManager,
                 annotations: AnnotationManager,
                 on_refresh: Callable[[], None],
                 unit_provider: Optional[Callable[[], tuple[str, str]]] = None,
                 initial_pane: str = "cursors", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.cursors = cursors
        self.annotations = annotations
        self.on_refresh = on_refresh
        self.unit_provider = unit_provider

        self._sel_cursor: Optional[int] = None
        self._sel_annotation: Optional[int] = None
        self._axes_index = 0

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 0))
        ctk.CTkLabel(header, text=spaced(t("Cursores y anotaciones")), font=font("header"),
                     text_color=col("fg_muted")).pack(side="left")
        Rule(self, strong=True).pack(fill="x", padx=18, pady=(8, 12))

        self.pane_var = ctk.StringVar(value=initial_pane if initial_pane in PANES
                                      else PANES[0])
        Segmented(self, PANES, self.pane_var, labels=_pane_labels(),
                  command=lambda _v: self._show_pane(),
                  width=132).pack(padx=18, anchor="w")

        self.panes: dict[str, ctk.CTkFrame] = {}
        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="both", expand=True, padx=18, pady=(14, 16))
        for name in PANES:
            # Scrollable: the annotation editor is taller than the palette,
            # and the cursor pane grows with every cursor placed.
            self.panes[name] = ctk.CTkScrollableFrame(
                holder, fg_color="transparent", corner_radius=0)
        self._build_cursor_pane(self.panes["cursors"])
        self._build_annotation_pane(self.panes["annotations"])
        self._show_pane()

        self.refresh_all()

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #
    def show_pane(self, name: str) -> None:
        if name in self.panes:
            self.pane_var.set(name)
            self._show_pane()

    def _show_pane(self) -> None:
        current = self.pane_var.get()
        for name, pane in self.panes.items():
            pane.pack_forget()
            if name == current:
                pane.pack(fill="both", expand=True)

    def detach(self) -> None:
        """Release any armed interaction before the panel is destroyed."""
        self.cursors.disarm()
        self.annotations.disarm()

    def _refresh_canvas(self) -> None:
        try:
            self.on_refresh()
        except Exception as exc:
            messagebox.showerror(t("Error al redibujar"), str(exc), parent=self)

    def _units(self) -> tuple[str, str]:
        if self.unit_provider is None:
            return "", ""
        try:
            return self.unit_provider()
        except Exception:
            return "", ""

    # ================================================================== #
    # Cursors
    # ================================================================== #
    def _build_cursor_pane(self, parent) -> None:
        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(fill="x")
        primary_button(actions, t("+ Vertical"), lambda: self._arm_cursor("v"),
                       height=28, width=104).pack(side="left")
        ghost_button(actions, t("+ Horizontal"), lambda: self._arm_cursor("h"),
                     width=112).pack(side="left", padx=6)
        ghost_button(actions, t("Quitar"), self._remove_cursor,
                     width=76).pack(side="left")

        self.cursor_hint = hint(parent,
                                t("Clic sobre el gráfico para colocarlo; "
                                  "arrastralos para medir."), wraplength=390)
        self.cursor_hint.pack(fill="x", pady=(8, 12))

        self.cursor_list = ctk.CTkFrame(parent, fg_color="transparent",
                                        width=1, height=1)
        self.cursor_list.pack(fill="x")

        Rule(parent).pack(fill="x", pady=12)

        self.cursor_readout = MeasurementsCard(parent, title=t("Lectura"))
        self.cursor_readout.bind_close(self._clear_cursors)
        self.cursor_readout.pack(fill="x")

        section = StaticSection(parent, t("Opciones de cursor"))
        section.pack(fill="x", pady=(12, 0))
        box = section.body

        self.snap_var = ctk.BooleanVar(value=self.cursors.snap_to_data)
        check_field(box, t("Pegar a las muestras"), self.snap_var,
                    command=self._apply_cursor_options)
        self.tags_var = ctk.BooleanVar(value=self.cursors.show_tags)
        check_field(box, t("Etiquetas en el gráfico"), self.tags_var,
                    command=self._apply_cursor_options)
        self.tag_value_var = ctk.BooleanVar(value=self.cursors.tag_with_value)
        check_field(box, t("Mostrar el valor"), self.tag_value_var,
                    command=self._apply_cursor_options)

        self.cursor_pos_var = ctk.StringVar(value="")
        entry_field(box, t("Posición exacta (X o Y)"), self.cursor_pos_var, width=110,
                    on_enter=self._apply_cursor_position, rule=False,
                    label_width=124)

        # ---- Live position slider ---------------------------------------- #
        # Normalised 0..1 travel: the mapping to data coordinates is rebuilt on
        # every selection and every replot (`_sync_cursor_slider`), so the same
        # widget serves a linear time axis and a log frequency axis.
        stacked_label(box, t("Mover cursor"))
        self.cursor_slider = ctk.CTkSlider(
            box, from_=0.0, to=1.0, number_of_steps=1000,
            command=self._on_cursor_slide, height=14)
        self.cursor_slider.pack(fill="x", pady=(0, 2))
        self.cursor_slider.set(0.5)
        self.cursor_slider.configure(state="disabled")
        self.slider_readout = hint(box, t("Seleccioná un cursor de la lista."),
                                   wraplength=390)
        self.slider_readout.pack(fill="x", pady=(0, 4))

        self._slider_range: tuple[float, float, bool] = (0.0, 1.0, False)
        self._slider_syncing = False   # guards set() -> command re-entrancy
        self._slider_job = None        # debounce handle for the heavy refresh

    def _arm_cursor(self, orientation: str) -> None:
        self.annotations.disarm()
        self.cursors.arm(orientation)
        self.cursor_hint.configure(
            text=t("Cursor armado: hacé clic sobre el gráfico para colocarlo."))

    def _apply_cursor_options(self) -> None:
        self.cursors.snap_to_data = bool(self.snap_var.get())
        self.cursors.show_tags = bool(self.tags_var.get())
        self.cursors.tag_with_value = bool(self.tag_value_var.get())
        self._refresh_canvas()
        self.refresh_cursor_ui()

    def _apply_cursor_position(self) -> None:
        if self._sel_cursor is None:
            return
        spec = self.cursors.get(self._sel_cursor)
        if spec is None:
            return
        spec.position = _parse_float(self.cursor_pos_var.get(), spec.position)
        self._refresh_canvas()
        self.refresh_cursor_ui()

    # --------------------------- live slider ----------------------------- #
    def _slider_to_data(self, fraction: float) -> float:
        """Map slider travel (0..1) to data coordinates, log-aware."""
        lo, hi, log = self._slider_range
        fraction = min(1.0, max(0.0, float(fraction)))
        if log and lo > 0.0:
            # Linear interpolation on a decade axis bunches every useful
            # position into the last 10 % of the travel; interpolate the
            # exponent instead so the drag feels uniform across decades.
            return math.exp(math.log(lo) + fraction * (math.log(hi) - math.log(lo)))
        return lo + fraction * (hi - lo)

    def _data_to_slider(self, value: float) -> float:
        """Inverse of `_slider_to_data`, clamped to the travel."""
        lo, hi, log = self._slider_range
        try:
            if log and lo > 0.0 and value > 0.0:
                span = math.log(hi) - math.log(lo)
                fraction = (math.log(value) - math.log(lo)) / span if span else 0.0
            else:
                span = hi - lo
                fraction = (value - lo) / span if span else 0.0
        except (ValueError, ZeroDivisionError):
            fraction = 0.0
        return min(1.0, max(0.0, fraction))

    def _on_cursor_slide(self, value: float) -> None:
        """
        Per-tick handler: moves ONE cursor's artists and nothing else.

        No `on_refresh()` here on purpose -- that re-renders every overlay and
        would make the drag stutter. The expensive part (readout table, cursor
        list rebuild) is debounced in `_schedule_slider_commit`.
        """
        if self._slider_syncing or self._sel_cursor is None:
            return
        position = self._slider_to_data(value)
        # snap=False: snapping fights a continuous drag. The exact-position
        # field and the canvas drag still honour the global snap setting.
        if not self.cursors.move(self._sel_cursor, position, snap=False):
            return
        self.cursor_pos_var.set(f"{position:.6g}")
        spec = self.cursors.get(self._sel_cursor)
        x_unit, y_unit = self._units()
        unit = x_unit if (spec is not None and spec.orientation == "v") else y_unit
        self.slider_readout.configure(text=format_eng(position, unit))
        self._schedule_slider_commit()

    def _schedule_slider_commit(self, delay_ms: int = 120) -> None:
        if self._slider_job is not None:
            try:
                self.after_cancel(self._slider_job)
            except Exception:
                pass
        self._slider_job = self.after(delay_ms, self._commit_slider)

    def _commit_slider(self) -> None:
        """Settled: now refresh the measurement table and the cursor list."""
        self._slider_job = None
        self.cursors.notify()   # -> App._on_cursor_change -> refresh_cursor_ui

    def _sync_cursor_slider(self) -> None:
        """Re-map the slider to the selected cursor's axis range and position."""
        rng = (None if self._sel_cursor is None
               else self.cursors.range_for(self._sel_cursor))
        spec = (None if self._sel_cursor is None
                else self.cursors.get(self._sel_cursor))
        if rng is None or spec is None:
            self.cursor_slider.configure(state="disabled")
            self.slider_readout.configure(
                text=t("Seleccioná un cursor de la lista."))
            return
        self._slider_range = rng
        self.cursor_slider.configure(state="normal")
        self._slider_syncing = True
        try:
            self.cursor_slider.set(self._data_to_slider(spec.position))
        finally:
            self._slider_syncing = False
        x_unit, y_unit = self._units()
        unit = x_unit if spec.orientation == "v" else y_unit
        self.slider_readout.configure(text=format_eng(spec.position, unit))

    def _remove_cursor(self) -> None:
        if self._sel_cursor is None:
            return
        self.cursors.remove(self._sel_cursor)
        self._sel_cursor = None
        self._refresh_canvas()
        self.refresh_cursor_ui()

    def _clear_cursors(self) -> None:
        self.cursors.clear()
        self._sel_cursor = None
        self._refresh_canvas()
        self.refresh_cursor_ui()

    def _select_cursor(self, cid: int) -> None:
        self._sel_cursor = cid
        spec = self.cursors.get(cid)
        if spec is not None:
            self.cursor_pos_var.set(f"{spec.position:.6g}")
        self.refresh_cursor_ui()

    def refresh_cursor_ui(self) -> None:
        self._render_cursor_list()
        self._render_readout()
        # Keep "Posición exacta" tracking the SELECTED cursor's actual
        # position. This used to only ever get set in `_select_cursor`, so
        # dragging the selected cursor directly on the canvas (which calls
        # this via `App._on_cursor_change`) moved it and refreshed the list
        # readout, but left the exact-position field showing the value from
        # before the drag -- pressing Enter there afterwards silently
        # snapped the cursor back to that stale position, undoing the drag.
        if self._sel_cursor is not None:
            spec = self.cursors.get(self._sel_cursor)
            if spec is not None:
                self.cursor_pos_var.set(f"{spec.position:.6g}")
        if not self.cursors.armed:
            self.cursor_hint.configure(
                text=t(t("Clic sobre el gráfico para colocarlo; arrastralos para medir.")))
        # Keep the slider tracking the selection and the current axis limits:
        # a zoom or a replot changes the travel range under it.
        self._sync_cursor_slider()

    def _render_cursor_list(self) -> None:
        for widget in self.cursor_list.winfo_children():
            widget.destroy()
        if not self.cursors.cursors:
            hint(self.cursor_list, t("Sin cursores.")).pack(fill="x")
            return
        x_unit, y_unit = self._units()
        for spec in self.cursors.cursors:
            unit = x_unit if spec.orientation == "v" else y_unit
            axis = "X" if spec.orientation == "v" else "Y"
            selected = spec.cid == self._sel_cursor
            row = ctk.CTkFrame(self.cursor_list, corner_radius=0,
                               height=ROW_HEIGHT,
                               fg_color=col("sel") if selected else "transparent")
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            # height=1: an empty CTkFrame otherwise holds a 200x200 request
            # and stretches the whole row to that height.
            marker = ctk.CTkFrame(row, width=3, height=1, corner_radius=0,
                                  fg_color=col("accent") if selected else "transparent")
            marker.pack(side="left", fill="y")
            name = ctk.CTkLabel(row, text=f"{self.cursors.name_of(spec)}  ·  {axis}",
                                font=font("small"), anchor="w", cursor="hand2")
            name.pack(side="left", padx=(8, 0))
            value = ctk.CTkLabel(row, text=format_eng(spec.position, unit),
                                 font=font("mono"), text_color=col("fg_muted"),
                                 cursor="hand2")
            value.pack(side="right", padx=8)
            for widget in (row, name, value):
                widget.bind("<Button-1>", lambda _e, c=spec.cid: self._select_cursor(c))

    def _render_readout(self) -> None:
        x_unit, y_unit = self._units()
        rows: list[tuple[str, str]] = []
        for entry in self.cursors.readout():
            axis = "X" if entry["orientation"] == "v" else "Y"
            unit = x_unit if entry["orientation"] == "v" else y_unit
            rows.append((f"{entry['name']}  ·  {axis}",
                         format_eng(entry["position"], unit)))
            for item in entry["values"]:
                label = _clean(item["label"])[:18]
                if entry["orientation"] == "v":
                    rows.append((f"   {label}", format_eng(item["value"], y_unit)))
                else:
                    crossings = item.get("crossings") or []
                    text = ", ".join(format_eng(c, x_unit) for c in crossings[:2])
                    rows.append((f"   {label}", text or t("sin cruce")))
        deltas = self.cursors.deltas()
        if deltas:
            rows.append(("--", ""))
            for item in deltas:
                unit = x_unit if item["orientation"] == "v" else y_unit
                rows.append((f"Δ {item['from']}→{item['to']}",
                             format_eng(item["delta"], unit)))
                if item["orientation"] == "v" and item["inverse"] is not None:
                    rows.append(("   1/Δ", format_eng(item["inverse"])))
                for curve in item["curves"][:3]:
                    rows.append((f"   Δ {_clean(curve['label'])[:14]}",
                                 format_eng(curve["delta"], y_unit)))
        self.cursor_readout.set_rows(rows[:22])

    # ================================================================== #
    # Annotations
    # ================================================================== #
    def _build_annotation_pane(self, parent) -> None:
        stacked_label(parent, t("Tipo"))
        self.kind_var = ctk.StringVar(value=t(list(ANNOTATION_KINDS)[0]))
        ctk.CTkComboBox(parent, values=[t(k) for k in ANNOTATION_KINDS],
                        variable=self.kind_var,
                        height=28, font=font("body"), dropdown_font=font("body"),
                        command=lambda _=None: self._on_kind_change()
                        ).pack(fill="x", pady=(0, 12))

        self.text_var = ctk.StringVar(value="")
        stacked_entry(parent, t("Texto"), self.text_var)
        hint(parent, t("Admite mathtext: $f_0 = 9{,}61\\,$kHz"),
             wraplength=390).pack(fill="x", pady=(0, 12))

        # Coordinates: the two things you always set, kept in the open.
        self.vars: dict[str, ctk.StringVar] = {
            name: ctk.StringVar(value=default) for name, default in (
                ("x", "0"), ("y", "0"), ("x2", "0"), ("y2", "0"),
                ("dx", "26"), ("dy", "20"), ("fontsize", "8"),
                ("linewidth", "0.9"), ("rotation", "0"), ("label_pos", "0.5"),
                ("alpha", "1.0"), ("color", "#2A2724"),
            )
        }
        self.linestyle_var = ctk.StringVar(value="--")
        self.arrow_var = ctk.StringVar(value="->")
        self.boxed_var = ctk.BooleanVar(value=True)
        self.widgets: dict[str, list] = {}

        coords = ctk.CTkFrame(parent, fg_color="transparent", width=1, height=1)
        coords.pack(fill="x")
        self.widgets["x"] = [entry_field(coords, "X", self.vars["x"], label_width=60)]
        self.widgets["y"] = [entry_field(coords, "Y", self.vars["y"], label_width=60)]
        self.widgets["x2"] = [entry_field(coords, "X₂", self.vars["x2"], label_width=60)]
        self.widgets["y2"] = [entry_field(coords, "Y₂", self.vars["y2"],
                                          label_width=60, rule=False)]

        capture = ctk.CTkFrame(parent, fg_color="transparent")
        capture.pack(fill="x", pady=(10, 4))
        ghost_button(capture, t("Capturar X/Y"), lambda: self._capture(False),
                     width=136).pack(side="left")
        ghost_button(capture, t("Capturar X₂/Y₂"), lambda: self._capture(True),
                     width=136).pack(side="left", padx=6)
        self.annotation_hint = hint(parent, "", wraplength=390)
        self.annotation_hint.pack(fill="x", pady=(2, 10))

        # Everything else is style, and style has a sensible default.
        appearance = StaticSection(parent, t("Estilo"))
        appearance.pack(fill="x", pady=(0, 8))
        box = appearance.body

        stacked_label(box, t("Preset"))
        self.preset_var = ctk.StringVar(value=t(list(STYLE_PRESETS)[0]))
        ctk.CTkComboBox(box, values=[t(k) for k in STYLE_PRESETS],
                        variable=self.preset_var,
                        height=28, font=font("body"), dropdown_font=font("body")
                        ).pack(fill="x", pady=(0, 6))
        ghost_button(box, t("Aplicar preset"), self._apply_preset).pack(fill="x", pady=(0, 12))

        color_row = ctk.CTkFrame(box, fg_color="transparent")
        color_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(color_row, text=t("Color"), font=font("label"),
                     text_color=col("fg_muted"), width=60, anchor="w").pack(side="left")
        color_button = ctk.CTkButton(color_row, text="", width=22, height=26,
                                      corner_radius=0, border_width=1,
                                      border_color=col("border_str"),
                                      fg_color=self.vars["color"].get(),
                                      hover_color=self.vars["color"].get(),
                                      command=self._pick_color)
        color_button.pack(side="right")
        color_entry = ctk.CTkEntry(color_row, textvariable=self.vars["color"],
                                    height=26, font=font("mono"), width=120)
        color_entry.pack(side="right", padx=(0, 6))
        self.widgets["color"] = [color_entry, color_button]

        def _sync_color(*_):
            value = self.vars["color"].get().strip()
            try:
                color_button.configure(fg_color=value, hover_color=value)
            except Exception:
                pass   # invalid hex while typing

        self.vars["color"].trace_add("write", _sync_color)

        self.widgets["linestyle"] = [combo_field(box, t("Línea"), self.linestyle_var,
                                                  LINESTYLES, width=90)]
        self.widgets["arrow"] = [combo_field(box, t("Flecha"), self.arrow_var,
                                              ARROW_STYLES, width=90)]
        self.widgets["linewidth"] = [entry_field(box, t("Grosor de línea"), self.vars["linewidth"],
                                                  width=64)]
        self.widgets["fontsize"] = [entry_field(box, t("Tamaño de texto"), self.vars["fontsize"],
                                                 width=64)]
        self.widgets["boxed"] = [check_field(box, t("Recuadro"), self.boxed_var)]

        placement = StaticSection(parent, t("Posición de la etiqueta"))
        placement.pack(fill="x", pady=(0, 12))
        box = placement.body
        self.widgets["dx"] = [entry_field(box, t("Offset X"), self.vars["dx"], suffix="pt")]
        self.widgets["dy"] = [entry_field(box, t("Offset Y"), self.vars["dy"], suffix="pt")]
        self.widgets["rotation"] = [entry_field(box, t("Rotación"), self.vars["rotation"],
                                                 suffix="°")]
        self.widgets["label_pos"] = [entry_field(box, t("Posición en la línea"),
                                                  self.vars["label_pos"])]
        self.widgets["alpha"] = [entry_field(box, t("Opacidad"), self.vars["alpha"],
                                              rule=False)]
        self.widgets["text"] = []

        # ---- Typography and label placement ------------------------------ #
        typography = StaticSection(parent, t("Tipografía y ubicación"))
        typography.pack(fill="x", pady=(0, 12))
        box = typography.body

        self.fontfamily_var = ctk.StringVar(value=FONT_DEFAULT)
        self.fontweight_var = ctk.StringVar(value="normal")
        self.fontstyle_var = ctk.StringVar(value="normal")
        self.ha_var = ctk.StringVar(value="center")
        self.va_var = ctk.StringVar(value="center")

        self.widgets["fontfamily"] = [combo_field(
            box, t("Fuente"), self.fontfamily_var,
            [FONT_DEFAULT] + FONT_FAMILIES, width=140)]
        self.widgets["fontweight"] = [combo_field(
            box, t("Peso"), self.fontweight_var, FONT_WEIGHTS, width=110)]
        self.widgets["fontstyle"] = [combo_field(
            box, t("Estilo"), self.fontstyle_var, FONT_STYLES, width=110)]
        self.widgets["ha"] = [combo_field(
            box, t("Alineación H"), self.ha_var, HA_CHOICES, width=110)]
        self.widgets["va"] = [combo_field(
            box, t("Alineación V"), self.va_var, VA_CHOICES, width=110,
            rule=False)]
        hint(box, t("La fuente aplica al texto plano; los tramos entre $...$ "
                    "siguen el set de mathtext."),
             wraplength=380).pack(fill="x", pady=(4, 0))

        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(fill="x")
        primary_button(actions, t("Agregar"), self._add_annotation,
                       height=28, width=96).pack(side="left")
        ghost_button(actions, t("Actualizar"), self._update_annotation,
                     width=100).pack(side="left", padx=6)
        ghost_button(actions, t("Quitar"), self._remove_annotation,
                     width=84).pack(side="left")

        Rule(parent).pack(fill="x", pady=12)
        SectionHeader(parent, t("Anotaciones"), action=t("Limpiar todo"),
                      command=self._clear_annotations).pack(fill="x", pady=(0, 6))
        # A plain frame, not a scroll region: the pane around it already
        # scrolls, and nesting two of them drives the configure/resize
        # feedback loop that had to be removed from the main window.
        self.annotation_list = ctk.CTkFrame(parent, fg_color="transparent",
                                            width=1, height=1)
        self.annotation_list.pack(fill="x")

        io_bar = ctk.CTkFrame(parent, fg_color="transparent")
        io_bar.pack(fill="x", pady=(10, 0))
        ghost_button(io_bar, t("Guardar..."), self._save_overlays,
                     width=132).pack(side="left")
        ghost_button(io_bar, t("Cargar..."), self._load_overlays,
                     width=132).pack(side="left", padx=6)

        self._on_kind_change()

    # ------------------------------ form --------------------------------- #
    def _kind(self) -> str:
        """Map the (possibly translated) visible label back to its kind id."""
        label = self.kind_var.get()
        if label in ANNOTATION_KINDS:
            return ANNOTATION_KINDS[label]
        for spanish, kind in ANNOTATION_KINDS.items():
            if t(spanish) == label:
                return kind
        return "point"

    def _set_field_states(self, kind: str) -> None:
        active = _KIND_FIELDS.get(kind, set())
        for name, widgets in self.widgets.items():
            state = "normal" if name in active else "disabled"
            for widget in widgets:
                try:
                    widget.configure(state=state)
                except (ValueError, AttributeError):
                    pass

    def _on_kind_change(self) -> None:
        kind = self._kind()
        self._set_field_states(kind)
        for key, value in KIND_DEFAULTS.get(kind, {}).items():
            if key == "boxed":
                self.boxed_var.set(bool(value))
            elif key == "arrow":
                self.arrow_var.set(str(value))
            elif key in self.vars:
                self.vars[key].set(f"{value:g}")

    def _preset_key(self) -> str:
        """Visible (possibly translated) preset label back to its key."""
        label = self.preset_var.get()
        if label in STYLE_PRESETS:
            return label
        return next((k for k in STYLE_PRESETS if t(k) == label),
                    list(STYLE_PRESETS)[0])

    def _apply_preset(self) -> None:
        for key, value in STYLE_PRESETS.get(self._preset_key(), {}).items():
            if key == "boxed":
                self.boxed_var.set(bool(value))
            elif key == "arrow":
                self.arrow_var.set(str(value))
            elif key == "linestyle":
                self.linestyle_var.set(str(value))
            elif key in self.vars:
                self.vars[key].set(f"{value:g}")

    def _pick_color(self) -> None:
        initial = self.vars["color"].get().strip() or "#2A2724"
        try:
            _rgb, hex_color = colorchooser.askcolor(color=initial, parent=self)
        except Exception:
            _rgb, hex_color = colorchooser.askcolor(parent=self)
        if hex_color:
            self.vars["color"].set(hex_color)

    def _capture(self, second_point: bool) -> None:
        self.cursors.disarm()

        def _done(axes_index: int, x: float, y: float) -> None:
            if second_point:
                self.vars["x2"].set(f"{x:.6g}")
                self.vars["y2"].set(f"{y:.6g}")
            else:
                self.vars["x"].set(f"{x:.6g}")
                self.vars["y"].set(f"{y:.6g}")
            self._axes_index = axes_index
            self.annotation_hint.configure(
                text=f"Capturado: X = {format_eng(x)}, Y = {format_eng(y)} "
                     f"(subgráfico {axes_index + 1}).")

        self.annotations.arm_pick(_done)
        self.annotation_hint.configure(
            text=t("Hacé clic sobre el punto deseado del gráfico."))

    def _form_values(self) -> dict:
        return {
            "kind": self._kind(),
            "x": _parse_float(self.vars["x"].get()),
            "y": _parse_float(self.vars["y"].get()),
            "x2": _parse_float(self.vars["x2"].get()),
            "y2": _parse_float(self.vars["y2"].get()),
            "text": self.text_var.get(),
            "axes_index": self._axes_index,
            "dx": _parse_float(self.vars["dx"].get(), 0.0),
            "dy": _parse_float(self.vars["dy"].get(), 0.0),
            "color": self.vars["color"].get().strip() or "#2A2724",
            "fontsize": _parse_float(self.vars["fontsize"].get(), 8.0),
            "linestyle": self.linestyle_var.get(),
            "linewidth": _parse_float(self.vars["linewidth"].get(), 0.9),
            "rotation": _parse_float(self.vars["rotation"].get(), 0.0),
            "boxed": bool(self.boxed_var.get()),
            "arrow": self.arrow_var.get(),
            "label_pos": _parse_float(self.vars["label_pos"].get(), 0.5),
            "alpha": _parse_float(self.vars["alpha"].get(), 1.0),
            # "" means "inherit": the renderer keeps the rcParams family and
            # the per-kind alignment default.
            "fontfamily": ("" if self.fontfamily_var.get() == FONT_DEFAULT
                           else self.fontfamily_var.get()),
            "fontweight": self.fontweight_var.get(),
            "fontstyle": self.fontstyle_var.get(),
            "ha": self.ha_var.get(),
            "va": self.va_var.get(),
        }

    def _load_form(self, spec: AnnotationSpec) -> None:
        label = next((k for k, v in ANNOTATION_KINDS.items() if v == spec.kind),
                     list(ANNOTATION_KINDS)[0])
        self.kind_var.set(t(label))
        self._set_field_states(spec.kind)
        for key in ("x", "y", "x2", "y2", "dx", "dy", "fontsize", "linewidth",
                    "rotation", "label_pos", "alpha"):
            self.vars[key].set(f"{getattr(spec, key):g}")
        self.vars["color"].set(spec.color)
        self.text_var.set(spec.text)
        self.linestyle_var.set(spec.linestyle)
        self.arrow_var.set(spec.arrow)
        self.boxed_var.set(spec.boxed)
        self.fontfamily_var.set(spec.fontfamily or FONT_DEFAULT)
        self.fontweight_var.set(spec.fontweight or "normal")
        self.fontstyle_var.set(spec.fontstyle or "normal")
        self.ha_var.set(spec.ha or "center")
        self.va_var.set(spec.va or "center")
        self._axes_index = spec.axes_index

    # ----------------------------- actions ------------------------------- #
    def _add_annotation(self) -> None:
        self.annotations.add(**self._form_values())
        self._refresh_canvas()
        self.refresh_annotation_list()

    def _update_annotation(self) -> None:
        if self._sel_annotation is None:
            messagebox.showinfo(t("Sin selección"),
                                t("Seleccioná una anotación de la lista."), parent=self)
            return
        self.annotations.update(self._sel_annotation, **self._form_values())
        self._refresh_canvas()
        self.refresh_annotation_list()

    def _remove_annotation(self) -> None:
        if self._sel_annotation is None:
            return
        self.annotations.remove(self._sel_annotation)
        self._sel_annotation = None
        self._refresh_canvas()
        self.refresh_annotation_list()

    def _clear_annotations(self) -> None:
        if not self.annotations.items:
            return
        if not messagebox.askyesno(t("Limpiar anotaciones"),
                                   f"¿Eliminar las {len(self.annotations.items)} "
                                   "anotaciones del gráfico?", parent=self):
            return
        self.annotations.clear()
        self._sel_annotation = None
        self._refresh_canvas()
        self.refresh_annotation_list()

    def _select_annotation(self, aid: int) -> None:
        spec = self.annotations.get(aid)
        if spec is None:
            return
        self._sel_annotation = aid
        self._load_form(spec)
        self.refresh_annotation_list()

    def refresh_annotation_list(self) -> None:
        for widget in self.annotation_list.winfo_children():
            widget.destroy()
        if not self.annotations.items:
            hint(self.annotation_list, t("Sin anotaciones.")).pack(fill="x")
            return
        for index, spec in enumerate(self.annotations.items, start=1):
            label = t(next((k for k, v in ANNOTATION_KINDS.items()
                            if v == spec.kind), spec.kind))
            caption = _clean(spec.text) or t("(sin texto)")
            selected = spec.aid == self._sel_annotation
            row = ctk.CTkFrame(self.annotation_list, corner_radius=0,
                               height=ROW_HEIGHT,
                               fg_color=col("sel") if selected else "transparent")
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            marker = ctk.CTkFrame(row, width=3, height=1, corner_radius=0,
                                  fg_color=col("accent") if selected else "transparent")
            marker.pack(side="left", fill="y")
            name = ctk.CTkLabel(row, text=f"A{index}  {caption[:24]}",
                                font=font("small"), anchor="w", cursor="hand2")
            name.pack(side="left", padx=(8, 0))
            kind = ctk.CTkLabel(row, text=label, font=font("mono", 9),
                                text_color=col("fg_faint"), cursor="hand2")
            kind.pack(side="right", padx=8)
            for widget in (row, name, kind):
                widget.bind("<Button-1>", lambda _e, a=spec.aid: self._select_annotation(a))

    # ----------------------------- overlay I/O --------------------------- #
    def _save_overlays(self) -> None:
        path = filedialog.asksaveasfilename(
            title=t("Guardar cursores y anotaciones"), defaultextension=".json",
            filetypes=[("JSON", "*.json")], parent=self)
        if not path:
            return
        try:
            save_overlays(path, self.cursors, self.annotations)
        except OSError as exc:
            messagebox.showerror(t("Error al guardar"), str(exc), parent=self)
            return
        messagebox.showinfo(t("Guardado"), f"{t('Overlays guardados en')}:\n{path}", parent=self)

    def _load_overlays(self) -> None:
        path = filedialog.askopenfilename(
            title=t("Cargar cursores y anotaciones"),
            filetypes=[("JSON", "*.json")], parent=self)
        if not path:
            return
        try:
            load_overlays(path, self.cursors, self.annotations)
        except (OSError, ValueError) as exc:
            messagebox.showerror(t("Error al cargar"), str(exc), parent=self)
            return
        except Exception as exc:
            messagebox.showerror(t("Archivo inválido"), str(exc), parent=self)
            return
        self._sel_cursor = None
        self._sel_annotation = None
        self._refresh_canvas()
        self.refresh_all()

    def refresh_all(self) -> None:
        self.refresh_cursor_ui()
        self.refresh_annotation_list()

    def clear_selection(self) -> None:
        """
        Drop any cursor/annotation selection before the underlying managers
        are repopulated with a different tab's content (`from_dict`) --
        otherwise a selected id from the OLD tab could point at nothing (or
        worse, at an unrelated cursor/annotation that happens to reuse the
        same id) in the new one.
        """
        self._sel_cursor = None
        self._sel_annotation = None


class OverlayWindow(ctk.CTkToplevel):
    """
    Floating palette hosting `OverlayPanel`.

    Non-modal on purpose: the canvas has to stay clickable while it is open,
    so `grab_set()` is never called.
    """

    def __init__(self, master, cursors: CursorManager,
                 annotations: AnnotationManager,
                 on_refresh: Callable[[], None],
                 unit_provider: Optional[Callable[[], tuple[str, str]]] = None,
                 on_close: Optional[Callable[[], None]] = None,
                 initial_pane: str = "cursors"):
        super().__init__(master)
        self.title(f'{t("Cursores")} / {t("Anotaciones")}')
        self.geometry("440x760")
        self.minsize(420, 600)
        self._on_close = on_close

        self.panel = OverlayPanel(self, cursors, annotations, on_refresh,
                                  unit_provider=unit_provider,
                                  initial_pane=initial_pane)
        self.panel.pack(fill="both", expand=True)

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def close(self) -> None:
        self.panel.detach()
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass
        self.destroy()
