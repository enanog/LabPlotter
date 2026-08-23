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

from tkinter import colorchooser, filedialog, messagebox
from typing import Callable, Optional

import customtkinter as ctk

from core.units import parse_eng

from .overlays import (
    ANNOTATION_KINDS, ARROW_STYLES, AnnotationManager, AnnotationSpec,
    CursorManager, KIND_DEFAULTS, LINESTYLES, STYLE_PRESETS, format_eng,
    load_overlays, save_overlays,
)
from .theme import col, font, spaced
from .widgets import (
    ROW_HEIGHT, MeasurementsCard, Rule, SectionHeader, Segmented, StaticSection,
    check_field,
    combo_field, entry_field, ghost_button, hint, primary_button,
    stacked_entry, stacked_label,
)

PANES = ["Cursores", "Anotaciones"]

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
                 initial_pane: str = "Cursores", **kwargs):
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
        ctk.CTkLabel(header, text=spaced("Superposiciones"), font=font("header"),
                     text_color=col("fg_muted")).pack(side="left")
        Rule(self, strong=True).pack(fill="x", padx=18, pady=(8, 12))

        self.pane_var = ctk.StringVar(value=initial_pane if initial_pane in PANES
                                      else PANES[0])
        Segmented(self, PANES, self.pane_var, command=lambda _v: self._show_pane(),
                  width=132).pack(padx=18, anchor="w")

        self.panes: dict[str, ctk.CTkFrame] = {}
        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="both", expand=True, padx=18, pady=(14, 16))
        for name in PANES:
            self.panes[name] = ctk.CTkFrame(holder, fg_color="transparent",
                                            width=1, height=1)
        self._build_cursor_pane(self.panes["Cursores"])
        self._build_annotation_pane(self.panes["Anotaciones"])
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
            messagebox.showerror("Error al redibujar", str(exc), parent=self)

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
        primary_button(actions, "+ Vertical", lambda: self._arm_cursor("v"),
                       height=28, width=104).pack(side="left")
        ghost_button(actions, "+ Horizontal", lambda: self._arm_cursor("h"),
                     width=112).pack(side="left", padx=6)
        ghost_button(actions, "Quitar", self._remove_cursor,
                     width=76).pack(side="left")

        self.cursor_hint = hint(parent, "Clic sobre el gráfico para colocarlo; "
                                        "arrastralos para medir.", wraplength=390)
        self.cursor_hint.pack(fill="x", pady=(8, 12))

        self.cursor_list = ctk.CTkFrame(parent, fg_color="transparent",
                                        width=1, height=1)
        self.cursor_list.pack(fill="x")

        Rule(parent).pack(fill="x", pady=12)

        self.cursor_readout = MeasurementsCard(parent, title="Lectura")
        self.cursor_readout.bind_close(self._clear_cursors)
        self.cursor_readout.pack(fill="both", expand=True)

        section = StaticSection(parent, "Opciones de cursor")
        section.pack(fill="x", pady=(12, 0))
        box = section.body

        self.snap_var = ctk.BooleanVar(value=self.cursors.snap_to_data)
        check_field(box, "Ajustar a muestras", self.snap_var,
                    command=self._apply_cursor_options)
        self.tags_var = ctk.BooleanVar(value=self.cursors.show_tags)
        check_field(box, "Etiquetas sobre el gráfico", self.tags_var,
                    command=self._apply_cursor_options)
        self.tag_value_var = ctk.BooleanVar(value=self.cursors.tag_with_value)
        check_field(box, "Valor en la etiqueta", self.tag_value_var,
                    command=self._apply_cursor_options)

        self.cursor_pos_var = ctk.StringVar(value="")
        entry_field(box, "Posición exacta", self.cursor_pos_var, width=110,
                    on_enter=self._apply_cursor_position, rule=False,
                    label_width=124)

    def _arm_cursor(self, orientation: str) -> None:
        self.annotations.disarm()
        self.cursors.arm(orientation)
        self.cursor_hint.configure(
            text="Cursor armado: hacé clic sobre el gráfico para colocarlo.")

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
        if not self.cursors.armed:
            self.cursor_hint.configure(
                text="Clic sobre el gráfico para colocarlo; arrastralos para medir.")

    def _render_cursor_list(self) -> None:
        for widget in self.cursor_list.winfo_children():
            widget.destroy()
        if not self.cursors.cursors:
            hint(self.cursor_list, "Sin cursores.").pack(fill="x")
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
                    rows.append((f"   {label}", text or "sin cruce"))
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
        stacked_label(parent, "Tipo")
        self.kind_var = ctk.StringVar(value=list(ANNOTATION_KINDS)[0])
        ctk.CTkComboBox(parent, values=list(ANNOTATION_KINDS), variable=self.kind_var,
                        height=28, font=font("body"), dropdown_font=font("body"),
                        command=lambda _=None: self._on_kind_change()
                        ).pack(fill="x", pady=(0, 12))

        self.text_var = ctk.StringVar(value="")
        stacked_entry(parent, "Texto", self.text_var)
        hint(parent, "Admite mathtext: $f_0 = 9{,}61\\,$kHz",
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
        ghost_button(capture, "Capturar X/Y", lambda: self._capture(False),
                     width=136).pack(side="left")
        ghost_button(capture, "Capturar X₂/Y₂", lambda: self._capture(True),
                     width=136).pack(side="left", padx=6)
        self.annotation_hint = hint(parent, "", wraplength=390)
        self.annotation_hint.pack(fill="x", pady=(2, 10))

        # Everything else is style, and style has a sensible default.
        appearance = StaticSection(parent, "Estilo")
        appearance.pack(fill="x", pady=(0, 8))
        box = appearance.body

        stacked_label(box, "Preset")
        self.preset_var = ctk.StringVar(value=list(STYLE_PRESETS)[0])
        ctk.CTkComboBox(box, values=list(STYLE_PRESETS), variable=self.preset_var,
                        height=28, font=font("body"), dropdown_font=font("body")
                        ).pack(fill="x", pady=(0, 6))
        ghost_button(box, "Aplicar preset", self._apply_preset).pack(fill="x", pady=(0, 12))

        color_row = ctk.CTkFrame(box, fg_color="transparent")
        color_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(color_row, text="Color", font=font("label"),
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

        self.widgets["linestyle"] = [combo_field(box, "Línea", self.linestyle_var,
                                                  LINESTYLES, width=90)]
        self.widgets["arrow"] = [combo_field(box, "Flecha", self.arrow_var,
                                              ARROW_STYLES, width=90)]
        self.widgets["linewidth"] = [entry_field(box, "Grosor", self.vars["linewidth"],
                                                  width=64)]
        self.widgets["fontsize"] = [entry_field(box, "Cuerpo", self.vars["fontsize"],
                                                 width=64)]
        self.widgets["boxed"] = [check_field(box, "Recuadro", self.boxed_var)]

        placement = StaticSection(parent, "Posición de la etiqueta")
        placement.pack(fill="x", pady=(0, 12))
        box = placement.body
        self.widgets["dx"] = [entry_field(box, "Offset X", self.vars["dx"], suffix="pt")]
        self.widgets["dy"] = [entry_field(box, "Offset Y", self.vars["dy"], suffix="pt")]
        self.widgets["rotation"] = [entry_field(box, "Rotación", self.vars["rotation"],
                                                 suffix="°")]
        self.widgets["label_pos"] = [entry_field(box, "Sobre la línea",
                                                  self.vars["label_pos"])]
        self.widgets["alpha"] = [entry_field(box, "Opacidad", self.vars["alpha"],
                                              rule=False)]
        self.widgets["text"] = []

        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(fill="x")
        primary_button(actions, "Agregar", self._add_annotation,
                       height=28, width=96).pack(side="left")
        ghost_button(actions, "Actualizar", self._update_annotation,
                     width=100).pack(side="left", padx=6)
        ghost_button(actions, "Quitar", self._remove_annotation,
                     width=84).pack(side="left")

        Rule(parent).pack(fill="x", pady=12)
        SectionHeader(parent, "Anotaciones", action="Limpiar todo",
                      command=self._clear_annotations).pack(fill="x", pady=(0, 6))
        self.annotation_list = ctk.CTkScrollableFrame(parent, height=130,
                                                       fg_color="transparent",
                                                       corner_radius=0)
        self.annotation_list.pack(fill="both", expand=True)

        io_bar = ctk.CTkFrame(parent, fg_color="transparent")
        io_bar.pack(fill="x", pady=(10, 0))
        ghost_button(io_bar, "Guardar...", self._save_overlays,
                     width=132).pack(side="left")
        ghost_button(io_bar, "Cargar...", self._load_overlays,
                     width=132).pack(side="left", padx=6)

        self._on_kind_change()

    # ------------------------------ form --------------------------------- #
    def _kind(self) -> str:
        return ANNOTATION_KINDS.get(self.kind_var.get(), "point")

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

    def _apply_preset(self) -> None:
        for key, value in STYLE_PRESETS.get(self.preset_var.get(), {}).items():
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
            text="Hacé clic sobre el punto deseado del gráfico.")

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
        }

    def _load_form(self, spec: AnnotationSpec) -> None:
        label = next((k for k, v in ANNOTATION_KINDS.items() if v == spec.kind),
                     list(ANNOTATION_KINDS)[0])
        self.kind_var.set(label)
        self._set_field_states(spec.kind)
        for key in ("x", "y", "x2", "y2", "dx", "dy", "fontsize", "linewidth",
                    "rotation", "label_pos", "alpha"):
            self.vars[key].set(f"{getattr(spec, key):g}")
        self.vars["color"].set(spec.color)
        self.text_var.set(spec.text)
        self.linestyle_var.set(spec.linestyle)
        self.arrow_var.set(spec.arrow)
        self.boxed_var.set(spec.boxed)
        self._axes_index = spec.axes_index

    # ----------------------------- actions ------------------------------- #
    def _add_annotation(self) -> None:
        self.annotations.add(**self._form_values())
        self._refresh_canvas()
        self.refresh_annotation_list()

    def _update_annotation(self) -> None:
        if self._sel_annotation is None:
            messagebox.showinfo("Sin selección",
                                "Seleccioná una anotación de la lista.", parent=self)
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
        if not messagebox.askyesno("Limpiar anotaciones",
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
            hint(self.annotation_list, "Sin anotaciones.").pack(fill="x")
            return
        for index, spec in enumerate(self.annotations.items, start=1):
            label = next((k for k, v in ANNOTATION_KINDS.items() if v == spec.kind),
                         spec.kind)
            caption = _clean(spec.text) or "(sin texto)"
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
            title="Guardar cursores y anotaciones", defaultextension=".json",
            filetypes=[("JSON", "*.json")], parent=self)
        if not path:
            return
        try:
            save_overlays(path, self.cursors, self.annotations)
        except OSError as exc:
            messagebox.showerror("Error al guardar", str(exc), parent=self)
            return
        messagebox.showinfo("Guardado", f"Overlays guardados en:\n{path}", parent=self)

    def _load_overlays(self) -> None:
        path = filedialog.askopenfilename(
            title="Cargar cursores y anotaciones",
            filetypes=[("JSON", "*.json")], parent=self)
        if not path:
            return
        try:
            load_overlays(path, self.cursors, self.annotations)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Error al cargar", str(exc), parent=self)
            return
        except Exception as exc:
            messagebox.showerror("Archivo inválido", str(exc), parent=self)
            return
        self._sel_cursor = None
        self._sel_annotation = None
        self._refresh_canvas()
        self.refresh_all()

    def refresh_all(self) -> None:
        self.refresh_cursor_ui()
        self.refresh_annotation_list()


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
                 initial_pane: str = "Cursores"):
        super().__init__(master)
        self.title("Cursores y anotaciones")
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
