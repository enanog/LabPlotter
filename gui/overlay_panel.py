"""
gui/overlay_panel.py
--------------------
CustomTkinter front-end for `gui.overlays`: a floating instrument-style
palette holding the cursor bench and the annotation editor.

It is deliberately a separate, non-modal `CTkToplevel` instead of a fourth
column in the main window:

* the main layout (signals | canvas | plot settings) stays uncluttered, which
  is the point of the minimalist redesign;
* placing a cursor or capturing a coordinate requires clicking *on the canvas*
  while the palette is open, so the window must never grab the event loop
  (no `grab_set()`).

The panel owns no plotting logic: it edits the manager state and asks the
application to re-render the overlay layer through the `on_refresh` callback.
"""

from __future__ import annotations

from tkinter import colorchooser, filedialog, messagebox
from typing import Callable, Optional

import customtkinter as ctk

from .overlays import (
    ANNOTATION_KINDS, ARROW_STYLES, AnnotationManager, AnnotationSpec,
    CursorManager, KIND_DEFAULTS, LINESTYLES, STYLE_PRESETS, format_eng,
    load_overlays, save_overlays,
)

MONO_FONT = ("Consolas", "Courier New", "monospace")

# Fields that stay disabled for kinds that do not use them. Keeping the layout
# fixed (instead of repacking) avoids the dialog jumping around on Windows DPI.
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
    """Defensive float parsing, accepting comma as decimal separator."""
    try:
        return float(str(text).strip().replace(",", "."))
    except (ValueError, AttributeError, TypeError):
        return fallback


def _clean(label: str) -> str:
    """Readable version of a mathtext label for the plain-text readout."""
    return (label or "").replace("$", "").replace("\\", "")


class OverlayPanel(ctk.CTkFrame):
    """Cursor bench + annotation editor. Embeddable in any CTk container."""

    def __init__(self, master, cursors: CursorManager,
                 annotations: AnnotationManager,
                 on_refresh: Callable[[], None],
                 unit_provider: Optional[Callable[[], tuple[str, str]]] = None,
                 **kwargs):
        super().__init__(master, **kwargs)
        self.cursors = cursors
        self.annotations = annotations
        self.on_refresh = on_refresh
        self.unit_provider = unit_provider

        self._sel_cursor: Optional[int] = None
        self._sel_annotation: Optional[int] = None
        self._cursor_rows: dict[int, ctk.CTkButton] = {}
        self._annotation_rows: dict[int, ctk.CTkButton] = {}

        # Chain the manager callback so dragging a cursor updates the readout.
        self._prev_on_change = cursors.on_change
        cursors.on_change = self._on_cursor_change

        self.tabs = ctk.CTkTabview(self, height=520)
        self.tabs.pack(fill="both", expand=True, padx=6, pady=6)
        self.tabs.add("Cursores")
        self.tabs.add("Anotaciones")
        self._build_cursor_tab(self.tabs.tab("Cursores"))
        self._build_annotation_tab(self.tabs.tab("Anotaciones"))

        self.refresh_all()

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #
    def detach(self) -> None:
        """Restore the manager callback before the panel is destroyed."""
        self.cursors.on_change = self._prev_on_change
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

    @staticmethod
    def _entry(parent, row: int, column: int, label: str, var,
               width: int = 92, label_width: int = 74):
        ctk.CTkLabel(parent, text=label, width=label_width, anchor="w"
                     ).grid(row=row, column=column, sticky="w", padx=(4, 2), pady=2)
        entry = ctk.CTkEntry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=column + 1, sticky="w", padx=(0, 6), pady=2)
        return entry

    # ================================================================== #
    # Cursors tab
    # ================================================================== #
    def _build_cursor_tab(self, parent) -> None:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", padx=4, pady=(4, 2))
        ctk.CTkButton(bar, text="+ Vertical", width=88,
                      command=lambda: self._arm_cursor("v")).pack(side="left", padx=2)
        ctk.CTkButton(bar, text="+ Horizontal", width=98,
                      command=lambda: self._arm_cursor("h")).pack(side="left", padx=2)
        ctk.CTkButton(bar, text="Quitar", width=70,
                      command=self._remove_cursor).pack(side="left", padx=2)
        ctk.CTkButton(bar, text="Limpiar", width=74,
                      command=self._clear_cursors).pack(side="left", padx=2)

        self.cursor_hint = ctk.CTkLabel(
            parent, text="Hacé clic en el gráfico para colocar el cursor. "
                         "Arrastralos para medir.",
            font=ctk.CTkFont(size=10), anchor="w", justify="left", wraplength=430)
        self.cursor_hint.pack(fill="x", padx=8, pady=(0, 4))

        opts = ctk.CTkFrame(parent, fg_color="transparent")
        opts.pack(fill="x", padx=4, pady=(0, 4))
        self.snap_var = ctk.BooleanVar(value=self.cursors.snap_to_data)
        ctk.CTkCheckBox(opts, text="Ajustar a muestras", variable=self.snap_var,
                        command=self._apply_cursor_options
                        ).pack(side="left", padx=4)
        self.tags_var = ctk.BooleanVar(value=self.cursors.show_tags)
        ctk.CTkCheckBox(opts, text="Etiquetas", variable=self.tags_var,
                        command=self._apply_cursor_options
                        ).pack(side="left", padx=4)
        self.tag_value_var = ctk.BooleanVar(value=self.cursors.tag_with_value)
        ctk.CTkCheckBox(opts, text="Valor en etiqueta", variable=self.tag_value_var,
                        command=self._apply_cursor_options
                        ).pack(side="left", padx=4)

        pos = ctk.CTkFrame(parent, fg_color="transparent")
        pos.pack(fill="x", padx=4, pady=(0, 4))
        ctk.CTkLabel(pos, text="Posición:", width=74, anchor="w").pack(side="left", padx=(4, 2))
        self.cursor_pos_var = ctk.StringVar(value="")
        entry = ctk.CTkEntry(pos, textvariable=self.cursor_pos_var, width=130)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self._apply_cursor_position())
        ctk.CTkButton(pos, text="Aplicar", width=76,
                      command=self._apply_cursor_position).pack(side="left", padx=6)

        self.cursor_list = ctk.CTkScrollableFrame(parent, height=110,
                                                  label_text="Cursores activos")
        self.cursor_list.pack(fill="x", padx=6, pady=(2, 4))

        self.readout = ctk.CTkTextbox(parent, height=210, wrap="none",
                                      font=ctk.CTkFont(family=MONO_FONT[0], size=11))
        self.readout.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        self.readout.configure(state="disabled")

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

    def _on_cursor_change(self) -> None:
        self.refresh_cursor_ui()
        if self._prev_on_change is not None:
            try:
                self._prev_on_change()
            except Exception:
                pass

    def refresh_cursor_ui(self) -> None:
        self._render_cursor_list()
        self._render_readout()
        if not self.cursors.armed:
            self.cursor_hint.configure(
                text="Hacé clic en el gráfico para colocar el cursor. "
                     "Arrastralos para medir.")

    def _render_cursor_list(self) -> None:
        for widget in self.cursor_list.winfo_children():
            widget.destroy()
        self._cursor_rows.clear()
        x_unit, y_unit = self._units()
        for spec in self.cursors.cursors:
            unit = x_unit if spec.orientation == "v" else y_unit
            axis = "X" if spec.orientation == "v" else "Y"
            text = (f"{self.cursors.name_of(spec)}   {axis} = "
                    f"{format_eng(spec.position, unit)}")
            selected = spec.cid == self._sel_cursor
            row = ctk.CTkButton(
                self.cursor_list, text=text, anchor="w", height=24,
                border_width=1 if selected else 0,
                command=lambda cid=spec.cid: self._select_cursor(cid))
            row.pack(fill="x", padx=2, pady=1)
            self._cursor_rows[spec.cid] = row

    def _render_readout(self) -> None:
        x_unit, y_unit = self._units()
        lines: list[str] = []
        for row in self.cursors.readout():
            axis = "X" if row["orientation"] == "v" else "Y"
            unit = x_unit if row["orientation"] == "v" else y_unit
            lines.append(f"{row['name']:<4} {axis} = "
                         f"{format_eng(row['position'], unit)}")
            for item in row["values"]:
                label = _clean(item["label"])[:26]
                if row["orientation"] == "v":
                    lines.append(f"     {label:<26} {format_eng(item['value'], y_unit)}")
                else:
                    crossings = item.get("crossings") or []
                    text = ", ".join(format_eng(c, x_unit) for c in crossings) or "sin cruce"
                    lines.append(f"     {label:<26} {text}")
            lines.append("")

        deltas = self.cursors.deltas()
        if deltas:
            lines.append("-" * 44)
            for item in deltas:
                unit = x_unit if item["orientation"] == "v" else y_unit
                head = f"D {item['from']}->{item['to']}  D = {format_eng(item['delta'], unit)}"
                if item["orientation"] == "v" and item["inverse"] is not None:
                    head += f"   1/D = {format_eng(item['inverse'])}"
                lines.append(head)
                for curve in item["curves"]:
                    lines.append(f"     {_clean(curve['label'])[:26]:<26} "
                                 f"{format_eng(curve['delta'], y_unit)}")

        self.readout.configure(state="normal")
        self.readout.delete("1.0", "end")
        self.readout.insert("1.0", "\n".join(lines) or
                            "Sin cursores. Agregá uno con «+ Vertical».")
        self.readout.configure(state="disabled")

    # ================================================================== #
    # Annotations tab
    # ================================================================== #
    def _build_annotation_tab(self, parent) -> None:
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(4, 2))
        ctk.CTkLabel(top, text="Tipo:", width=48, anchor="w").pack(side="left", padx=(4, 2))
        self.kind_var = ctk.StringVar(value=list(ANNOTATION_KINDS)[0])
        ctk.CTkComboBox(top, values=list(ANNOTATION_KINDS), variable=self.kind_var,
                        width=160, command=lambda _=None: self._on_kind_change()
                        ).pack(side="left", padx=2)
        ctk.CTkLabel(top, text="Preset:", width=52, anchor="w").pack(side="left", padx=(8, 2))
        self.preset_var = ctk.StringVar(value=list(STYLE_PRESETS)[0])
        ctk.CTkComboBox(top, values=list(STYLE_PRESETS), variable=self.preset_var,
                        width=180).pack(side="left", padx=2)
        ctk.CTkButton(top, text="Aplicar preset", width=110,
                      command=self._apply_preset).pack(side="left", padx=4)

        form = ctk.CTkFrame(parent)
        form.pack(fill="x", padx=6, pady=4)

        self.vars: dict[str, ctk.StringVar] = {
            name: ctk.StringVar(value=default) for name, default in (
                ("x", "0"), ("y", "0"), ("x2", "0"), ("y2", "0"),
                ("dx", "26"), ("dy", "20"), ("fontsize", "8"),
                ("linewidth", "0.9"), ("rotation", "0"), ("label_pos", "0.5"),
                ("alpha", "1.0"), ("color", "#222222"),
            )
        }
        self.text_var = ctk.StringVar(value="")
        self.linestyle_var = ctk.StringVar(value="--")
        self.arrow_var = ctk.StringVar(value="->")
        self.boxed_var = ctk.BooleanVar(value=True)

        self.widgets: dict[str, list] = {}

        ctk.CTkLabel(form, text="Texto:", width=74, anchor="w"
                     ).grid(row=0, column=0, sticky="w", padx=(4, 2), pady=2)
        text_entry = ctk.CTkEntry(form, textvariable=self.text_var, width=330)
        text_entry.grid(row=0, column=1, columnspan=3, sticky="we", padx=(0, 6), pady=2)
        self.widgets["text"] = [text_entry]
        ctk.CTkLabel(form, text="Admite mathtext: $f_0 = 9{,}61\\,$kHz",
                     font=ctk.CTkFont(size=10), anchor="w"
                     ).grid(row=1, column=1, columnspan=3, sticky="w", padx=(0, 6))

        self.widgets["x"] = [self._entry(form, 2, 0, "X:", self.vars["x"])]
        self.widgets["y"] = [self._entry(form, 2, 2, "Y:", self.vars["y"])]
        self.widgets["x2"] = [self._entry(form, 3, 0, "X₂:", self.vars["x2"])]
        self.widgets["y2"] = [self._entry(form, 3, 2, "Y₂:", self.vars["y2"])]
        self.widgets["dx"] = [self._entry(form, 4, 0, "Offset X:", self.vars["dx"])]
        self.widgets["dy"] = [self._entry(form, 4, 2, "Offset Y:", self.vars["dy"])]
        self.widgets["fontsize"] = [self._entry(form, 5, 0, "Fuente:", self.vars["fontsize"])]
        self.widgets["linewidth"] = [self._entry(form, 5, 2, "Grosor:", self.vars["linewidth"])]
        self.widgets["rotation"] = [self._entry(form, 6, 0, "Rotación:", self.vars["rotation"])]
        self.widgets["label_pos"] = [self._entry(form, 6, 2, "Pos. etiq.:", self.vars["label_pos"])]
        self.widgets["alpha"] = [self._entry(form, 7, 0, "Alfa:", self.vars["alpha"])]

        ctk.CTkLabel(form, text="Estilo:", width=74, anchor="w"
                     ).grid(row=7, column=2, sticky="w", padx=(4, 2), pady=2)
        style_combo = ctk.CTkComboBox(form, values=LINESTYLES,
                                      variable=self.linestyle_var, width=92)
        style_combo.grid(row=7, column=3, sticky="w", padx=(0, 6), pady=2)
        self.widgets["linestyle"] = [style_combo]

        ctk.CTkLabel(form, text="Flecha:", width=74, anchor="w"
                     ).grid(row=8, column=0, sticky="w", padx=(4, 2), pady=2)
        arrow_combo = ctk.CTkComboBox(form, values=ARROW_STYLES,
                                      variable=self.arrow_var, width=92)
        arrow_combo.grid(row=8, column=1, sticky="w", padx=(0, 6), pady=2)
        self.widgets["arrow"] = [arrow_combo]

        box_check = ctk.CTkCheckBox(form, text="Recuadro", variable=self.boxed_var)
        box_check.grid(row=8, column=2, columnspan=2, sticky="w", padx=(4, 2), pady=2)
        self.widgets["boxed"] = [box_check]

        color_entry = self._entry(form, 9, 0, "Color:", self.vars["color"])
        color_button = ctk.CTkButton(form, text="Elegir...", width=92,
                                     command=self._pick_color)
        color_button.grid(row=9, column=2, columnspan=2, sticky="w", padx=(4, 6), pady=2)
        self.widgets["color"] = [color_entry, color_button]

        capture = ctk.CTkFrame(parent, fg_color="transparent")
        capture.pack(fill="x", padx=4, pady=(0, 2))
        ctk.CTkButton(capture, text="Capturar X/Y del gráfico", width=190,
                      command=lambda: self._capture(False)).pack(side="left", padx=3)
        ctk.CTkButton(capture, text="Capturar X₂/Y₂", width=140,
                      command=lambda: self._capture(True)).pack(side="left", padx=3)
        self.annotation_hint = ctk.CTkLabel(parent, text="", font=ctk.CTkFont(size=10),
                                            anchor="w", justify="left", wraplength=430)
        self.annotation_hint.pack(fill="x", padx=8, pady=(0, 2))

        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(fill="x", padx=4, pady=(0, 2))
        ctk.CTkButton(actions, text="Agregar", width=88,
                      command=self._add_annotation).pack(side="left", padx=2)
        ctk.CTkButton(actions, text="Actualizar", width=92,
                      command=self._update_annotation).pack(side="left", padx=2)
        ctk.CTkButton(actions, text="Quitar", width=76,
                      command=self._remove_annotation).pack(side="left", padx=2)
        ctk.CTkButton(actions, text="Limpiar", width=78,
                      command=self._clear_annotations).pack(side="left", padx=2)

        self.annotation_list = ctk.CTkScrollableFrame(parent, height=120,
                                                      label_text="Anotaciones")
        self.annotation_list.pack(fill="both", expand=True, padx=6, pady=(2, 4))

        io_bar = ctk.CTkFrame(parent, fg_color="transparent")
        io_bar.pack(fill="x", padx=4, pady=(0, 6))
        ctk.CTkButton(io_bar, text="Guardar overlays...", width=160,
                      command=self._save_overlays).pack(side="left", padx=3)
        ctk.CTkButton(io_bar, text="Cargar overlays...", width=160,
                      command=self._load_overlays).pack(side="left", padx=3)

        self._on_kind_change()

    # ------------------------------ form --------------------------------- #
    def _kind(self) -> str:
        return ANNOTATION_KINDS.get(self.kind_var.get(), "point")

    def _on_kind_change(self) -> None:
        """Grey out the fields that the selected kind ignores."""
        active = _KIND_FIELDS.get(self._kind(), set())
        for name, widgets in self.widgets.items():
            state = "normal" if name in active else "disabled"
            for widget in widgets:
                try:
                    widget.configure(state=state)
                except (ValueError, AttributeError):
                    pass
        for key, value in KIND_DEFAULTS.get(self._kind(), {}).items():
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
        initial = self.vars["color"].get().strip() or "#222222"
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
            "axes_index": getattr(self, "_axes_index", 0),
            "dx": _parse_float(self.vars["dx"].get(), 0.0),
            "dy": _parse_float(self.vars["dy"].get(), 0.0),
            "color": self.vars["color"].get().strip() or "#222222",
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
        active = _KIND_FIELDS.get(spec.kind, set())
        for name, widgets in self.widgets.items():
            state = "normal" if name in active else "disabled"
            for widget in widgets:
                try:
                    widget.configure(state=state)
                except (ValueError, AttributeError):
                    pass
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
        self._annotation_rows.clear()
        for index, spec in enumerate(self.annotations.items, start=1):
            label = next((k for k, v in ANNOTATION_KINDS.items() if v == spec.kind),
                         spec.kind)
            caption = _clean(spec.text) or "(sin texto)"
            text = f"A{index}  {label} · {caption[:30]}"
            selected = spec.aid == self._sel_annotation
            row = ctk.CTkButton(self.annotation_list, text=text, anchor="w",
                                height=24, border_width=1 if selected else 0,
                                command=lambda aid=spec.aid: self._select_annotation(aid))
            row.pack(fill="x", padx=2, pady=1)
            self._annotation_rows[spec.aid] = row

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

    Non-modal on purpose: the user must be able to click the canvas while it
    is open, so `grab_set()` is never called.
    """

    def __init__(self, master, cursors: CursorManager,
                 annotations: AnnotationManager,
                 on_refresh: Callable[[], None],
                 unit_provider: Optional[Callable[[], tuple[str, str]]] = None,
                 on_close: Optional[Callable[[], None]] = None):
        super().__init__(master)
        self.title("Cursores y anotaciones")
        self.geometry("470x700")
        self.minsize(430, 560)
        self._on_close = on_close

        self.panel = OverlayPanel(self, cursors, annotations, on_refresh,
                                  unit_provider=unit_provider,
                                  fg_color="transparent")
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
