"""Editor de circuitos integrado en la etapa ``Circuitos`` de la interfaz."""

from __future__ import annotations

import math
import os
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from core.circuit import (
    COMPONENT_SPECS,
    CircuitDocument,
    CircuitWire,
    circuitikz_source,
    draw_circuit,
    export_circuit_pdf,
    export_circuit_tex,
)
from core.i18n import t
from gui.theme import col, font
from gui.widgets import CodeDialog, Rule, SectionHeader, ghost_button, hint, primary_button


@dataclass
class CircuitEditorState:
    """Estado que sobrevive a cambios de idioma/reconstrucciones de widgets."""

    document: CircuitDocument = field(default_factory=CircuitDocument)
    file_path: Optional[str] = None
    dirty: bool = False
    undo: list[dict] = field(default_factory=list)
    redo: list[dict] = field(default_factory=list)


class CircuitEditor:
    """Controlador de la biblioteca lateral y del lienzo de esquemas."""

    def __init__(self, app, nav_parent, plot_parent,
                 state: CircuitEditorState) -> None:
        self.app = app
        self.state = state
        self.document = state.document
        self.nav_parent = nav_parent
        self.plot_parent = plot_parent
        self.selected: Optional[tuple[str, str]] = None
        self.tool = "select"
        self.pending_wire: Optional[tuple[float, float]] = None
        self._drag_snapshot: Optional[dict] = None
        self._drag_moved = False
        self._tool_buttons: dict[str, ctk.CTkButton] = {}

        self._build_controls()
        self._build_canvas()
        self._set_tool("select")
        self._render()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_controls(self) -> None:
        parent = self.nav_parent
        for widget in parent.winfo_children():
            widget.destroy()

        SectionHeader(parent, t("Documento")).pack(fill="x")
        Rule(parent).pack(fill="x", pady=(6, 8))
        ctk.CTkLabel(parent, text=t("Título"), anchor="w", font=font("small"),
                     text_color=col("fg_muted")).pack(fill="x")
        self.title_var = ctk.StringVar(value=self.document.title)
        ctk.CTkEntry(parent, textvariable=self.title_var, height=30,
                     font=font("body")).pack(fill="x", pady=(3, 8))
        file_row = ctk.CTkFrame(parent, fg_color="transparent")
        file_row.pack(fill="x", pady=(0, 4))
        ghost_button(file_row, t("Nuevo"), self.new, width=82).pack(side="left")
        ghost_button(file_row, t("Abrir..."), self.open, width=82).pack(side="left", padx=4)
        ghost_button(file_row, t("Guardar"), self.save, width=82).pack(side="left")

        SectionHeader(parent, t("Herramientas")).pack(fill="x", pady=(14, 0))
        Rule(parent).pack(fill="x", pady=(6, 8))
        self._tool_button(parent, "select", t("Seleccionar / mover"))
        self._tool_button(parent, "wire", t("Cable"))

        SectionHeader(parent, t("Componentes")).pack(fill="x", pady=(14, 0))
        Rule(parent).pack(fill="x", pady=(6, 8))
        for key, spec in COMPONENT_SPECS.items():
            self._tool_button(parent, key, t(spec.name))

        SectionHeader(parent, t("Selección")).pack(fill="x", pady=(14, 0))
        Rule(parent).pack(fill="x", pady=(6, 8))
        self.selection_name = ctk.CTkLabel(
            parent, text=t("Nada seleccionado"), anchor="w", font=font("body"),
            text_color=col("fg_muted"))
        self.selection_name.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(parent, text=t("Referencia"), anchor="w", font=font("small"),
                     text_color=col("fg_muted")).pack(fill="x")
        self.label_var = ctk.StringVar(value="")
        ctk.CTkEntry(parent, textvariable=self.label_var, height=30,
                     font=font("body")).pack(fill="x", pady=(3, 8))
        ctk.CTkLabel(parent, text=t("Valor"), anchor="w", font=font("small"),
                     text_color=col("fg_muted")).pack(fill="x")
        self.value_var = ctk.StringVar(value="")
        ctk.CTkEntry(parent, textvariable=self.value_var, height=30,
                     font=font("body")).pack(fill="x", pady=(3, 8))
        primary_button(parent, t("Aplicar propiedades"), self.apply_properties,
                       height=30).pack(fill="x", pady=(0, 6))
        edit_row = ctk.CTkFrame(parent, fg_color="transparent")
        edit_row.pack(fill="x")
        ghost_button(edit_row, t("Girar 90°"), self.rotate_selected,
                     width=116).pack(side="left")
        ghost_button(edit_row, t("Eliminar"), self.delete_selected,
                     width=96).pack(side="left", padx=(6, 0))

        history_row = ctk.CTkFrame(parent, fg_color="transparent")
        history_row.pack(fill="x", pady=(8, 0))
        ghost_button(history_row, t("Deshacer"), self.undo,
                     width=108).pack(side="left")
        ghost_button(history_row, t("Rehacer"), self.redo,
                     width=104).pack(side="left", padx=(6, 0))

        SectionHeader(parent, t("Exportar circuito")).pack(fill="x", pady=(14, 0))
        Rule(parent).pack(fill="x", pady=(6, 8))
        primary_button(parent, t("Exportar PDF..."), self.export_pdf,
                       height=31).pack(fill="x", pady=(0, 6))
        ghost_button(parent, t("Exportar LaTeX..."), self.export_tex,
                     height=29).pack(fill="x")
        hint(parent, t("PDF es vectorial y no requiere LaTeX. El archivo .tex "
                       "usa CircuitikZ y queda completamente editable."),
             wraplength=280).pack(fill="x", pady=(8, 0))

    def _tool_button(self, parent, key: str, label: str) -> None:
        button = ctk.CTkButton(parent, text=label, anchor="w", height=30,
                               font=font("label"),
                               command=lambda: self._set_tool(key))
        button.pack(fill="x", pady=2)
        self._tool_buttons[key] = button

    def _build_canvas(self) -> None:
        for widget in self.plot_parent.winfo_children():
            widget.destroy()
        paper = ctk.CTkFrame(self.plot_parent, corner_radius=0,
                             fg_color=col("surface"), border_width=1,
                             border_color=col("border_str"))
        paper.pack(fill="both", expand=True, padx=2, pady=(2, 8))
        self.figure = Figure(figsize=(8, 5.8), dpi=100, facecolor="#FFFFFF")
        self.axis = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=paper)
        widget = self.canvas.get_tk_widget()
        widget.configure(borderwidth=0, highlightthickness=0, cursor="crosshair",
                         takefocus=True)
        widget.pack(fill="both", expand=True)
        self.status = ctk.CTkLabel(self.plot_parent, text="", anchor="w",
                                   font=font("hint"), text_color=col("fg_faint"))
        self.status.pack(fill="x", padx=4, pady=(0, 2))

        self.canvas.mpl_connect("button_press_event", self._press)
        self.canvas.mpl_connect("motion_notify_event", self._motion)
        self.canvas.mpl_connect("button_release_event", self._release)
        widget.bind("<Delete>", lambda _e: self._shortcut(self.delete_selected))
        widget.bind("<BackSpace>", lambda _e: self._shortcut(self.delete_selected))
        widget.bind("<Key-r>", lambda _e: self._shortcut(self.rotate_selected))
        widget.bind("<Escape>", lambda _e: self._shortcut(self.cancel_action))
        widget.bind("<Control-s>", lambda _e: self._shortcut(self.save))
        widget.bind("<Control-o>", lambda _e: self._shortcut(self.open))

    @staticmethod
    def _shortcut(action):
        action()
        return "break"

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def _set_tool(self, tool: str) -> None:
        self.tool = tool
        self.pending_wire = None
        for key, button in self._tool_buttons.items():
            active = key == tool
            button.configure(
                fg_color=col("accent") if active else col("surface"),
                hover_color=col("accent_hi") if active else col("sel"),
                text_color=col("on_accent") if active else col("fg"),
                border_color=col("accent") if active else col("border"))
        self._render()

    @staticmethod
    def _point(event) -> Optional[tuple[float, float]]:
        if event.inaxes is None or event.xdata is None or event.ydata is None:
            return None
        return round(event.xdata), round(event.ydata)

    def _press(self, event) -> None:
        self.canvas.get_tk_widget().focus_set()
        point = self._point(event)
        if point is None:
            return
        if event.button == 3:
            self.cancel_action()
            return
        if event.button != 1:
            return

        if self.tool in COMPONENT_SPECS:
            self._checkpoint()
            component = self.document.add_component(self.tool, *point)
            self.selected = ("component", component.uid)
            self._sync_properties()
        elif self.tool == "wire":
            if self.pending_wire is None:
                self.pending_wire = point
            else:
                if point != self.pending_wire:
                    self._checkpoint()
                    wire = self.document.add_wire(self.pending_wire, point)
                    self.selected = ("wire", wire.uid)
                self.pending_wire = None
                self._sync_properties()
        else:
            self.selected = self._hit_test(event.xdata, event.ydata)
            self._sync_properties()
            if self.selected and self.selected[0] == "component":
                self._drag_snapshot = self.document.to_dict()
                self._drag_moved = False
        self._render()

    def _motion(self, event) -> None:
        if (self._drag_snapshot is None or not self.selected
                or self.selected[0] != "component"):
            return
        point = self._point(event)
        component = self.document.find_component(self.selected[1])
        if point is None or component is None or (component.x, component.y) == point:
            return
        old_terminals = component.terminals()
        component.x, component.y = point
        self._move_attached_wires(old_terminals, component.terminals())
        self._drag_moved = True
        self._mark_dirty()
        self._render()

    def _release(self, _event) -> None:
        if self._drag_snapshot is not None and self._drag_moved:
            self.state.undo.append(self._drag_snapshot)
            self.state.undo[:] = self.state.undo[-80:]
            self.state.redo.clear()
        self._drag_snapshot = None
        self._drag_moved = False

    def _hit_test(self, x: float, y: float) -> Optional[tuple[str, str]]:
        for component in reversed(self.document.components):
            radius = .7 if component.kind == "ground" else 1.05
            if math.hypot(x - component.x, y - component.y) <= radius:
                return "component", component.uid
        best, best_distance = None, .28
        for wire in self.document.wires:
            distance = self._wire_distance(x, y, wire)
            if distance < best_distance:
                best, best_distance = wire, distance
        return ("wire", best.uid) if best else None

    @staticmethod
    def _wire_distance(x: float, y: float, wire: CircuitWire) -> float:
        dx, dy = wire.x2 - wire.x1, wire.y2 - wire.y1
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return math.hypot(x - wire.x1, y - wire.y1)
        ratio = max(0.0, min(1.0, ((x - wire.x1) * dx
                                   + (y - wire.y1) * dy) / length_sq))
        return math.hypot(x - (wire.x1 + ratio * dx),
                          y - (wire.y1 + ratio * dy))

    def _move_attached_wires(self, old_terminals, new_terminals) -> None:
        for wire in self.document.wires:
            for old, new in zip(old_terminals, new_terminals):
                if math.hypot(wire.x1 - old[0], wire.y1 - old[1]) < 1e-6:
                    wire.x1, wire.y1 = new
                    break
            for old, new in zip(old_terminals, new_terminals):
                if math.hypot(wire.x2 - old[0], wire.y2 - old[1]) < 1e-6:
                    wire.x2, wire.y2 = new
                    break

    def _render(self) -> None:
        draw_circuit(self.axis, self.document, selected=self.selected,
                     show_grid=True, pending_wire=self.pending_wire)
        self.axis.set_xlim(0, 22)
        self.axis.set_ylim(15, 0)
        self.figure.subplots_adjust(left=.02, right=.98, bottom=.03, top=.98)
        self.canvas.draw_idle()
        self._update_status()

    def _sync_properties(self) -> None:
        component = (self.document.find_component(self.selected[1])
                     if self.selected and self.selected[0] == "component" else None)
        if component is None:
            self.selection_name.configure(
                text=t("Cable seleccionado") if self.selected else t("Nada seleccionado"))
            self.label_var.set("")
            self.value_var.set("")
            return
        self.selection_name.configure(text=t(COMPONENT_SPECS[component.kind].name))
        self.label_var.set(component.label)
        self.value_var.set(component.value)

    def apply_properties(self) -> None:
        new_title = self.title_var.get().strip() or t("Circuito")
        component = (self.document.find_component(self.selected[1])
                     if self.selected and self.selected[0] == "component" else None)
        new_label, new_value = self.label_var.get().strip(), self.value_var.get().strip()
        title_changed = new_title != self.document.title
        component_changed = (component is not None
                             and (new_label, new_value) != (component.label, component.value))
        if title_changed or component_changed:
            self._checkpoint()
        self.document.title = new_title
        if component_changed:
            component.label, component.value = new_label, new_value
        self._render()

    def rotate_selected(self) -> None:
        if not self.selected or self.selected[0] != "component":
            return
        component = self.document.find_component(self.selected[1])
        if component is None or component.kind == "ground":
            return
        self._checkpoint()
        old_terminals = component.terminals()
        component.rotation = (component.rotation + 90) % 360
        self._move_attached_wires(old_terminals, component.terminals())
        self._render()

    def delete_selected(self) -> None:
        if not self.selected:
            return
        self._checkpoint()
        self.document.remove(self.selected[1])
        self.selected = None
        self._sync_properties()
        self._render()

    def cancel_action(self) -> None:
        self.pending_wire = None
        self._drag_snapshot = None
        self._set_tool("select")

    # ------------------------------------------------------------------
    # History / files / export
    # ------------------------------------------------------------------
    def _mark_dirty(self) -> None:
        self.state.dirty = True

    def _checkpoint(self) -> None:
        self.state.undo.append(self.document.to_dict())
        self.state.undo[:] = self.state.undo[-80:]
        self.state.redo.clear()
        self._mark_dirty()

    def undo(self) -> None:
        if not self.state.undo:
            return
        self.state.redo.append(self.document.to_dict())
        self._replace_document(CircuitDocument.from_dict(self.state.undo.pop()))

    def redo(self) -> None:
        if not self.state.redo:
            return
        self.state.undo.append(self.document.to_dict())
        self._replace_document(CircuitDocument.from_dict(self.state.redo.pop()))

    def _replace_document(self, document: CircuitDocument) -> None:
        self.document = document
        self.state.document = document
        self.state.dirty = True
        self.selected = None
        self.pending_wire = None
        self.title_var.set(document.title)
        self._sync_properties()
        self._render()

    def _confirm_discard(self) -> bool:
        return (not self.state.dirty or messagebox.askyesno(
            t("Cambios sin guardar"),
            t("Hay cambios sin guardar. ¿Querés descartarlos?"), parent=self.app))

    def new(self) -> None:
        if not self._confirm_discard():
            return
        self.state.file_path = None
        self.state.undo.clear()
        self.state.redo.clear()
        self._replace_document(CircuitDocument(title=t("Circuito")))
        self.state.dirty = False

    def open(self) -> None:
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            parent=self.app, title=t("Abrir circuito"),
            filetypes=[(t("Circuito LabPlotter"), "*.labcircuit.json"),
                       ("JSON", "*.json")])
        if not path:
            return
        try:
            document = CircuitDocument.load(path)
        except ValueError as exc:
            messagebox.showerror(t("No se pudo abrir"), str(exc), parent=self.app)
            return
        self.state.file_path = path
        self.state.undo.clear()
        self.state.redo.clear()
        self._replace_document(document)
        self.state.dirty = False

    def save(self) -> bool:
        self.apply_properties()
        path = self.state.file_path
        if not path:
            path = filedialog.asksaveasfilename(
                parent=self.app, title=t("Guardar circuito"),
                defaultextension=".labcircuit.json",
                filetypes=[(t("Circuito LabPlotter"), "*.labcircuit.json"),
                           ("JSON", "*.json")])
        if not path:
            return False
        try:
            self.document.save(path)
        except OSError as exc:
            messagebox.showerror(t("No se pudo guardar"), str(exc), parent=self.app)
            return False
        self.state.file_path = path
        self.state.dirty = False
        self._update_status(t("Circuito guardado."))
        return True

    def export_pdf(self) -> None:
        self.apply_properties()
        path = filedialog.asksaveasfilename(
            parent=self.app, title=t("Exportar circuito a PDF"),
            defaultextension=".pdf", filetypes=[(t("PDF vectorial"), "*.pdf")])
        if not path:
            return
        try:
            export_circuit_pdf(self.document, path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(t("No se pudo exportar"), str(exc), parent=self.app)
            return
        messagebox.showinfo(t("Exportación completa"),
                            f"{t('PDF guardado en:')}\n{path}", parent=self.app)

    def export_tex(self) -> None:
        self.apply_properties()
        path = filedialog.asksaveasfilename(
            parent=self.app, title=t("Exportar circuito a LaTeX"),
            defaultextension=".tex",
            filetypes=[(t("LaTeX / CircuitikZ"), "*.tex")])
        if not path:
            return
        try:
            export_circuit_tex(self.document, path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(t("No se pudo exportar"), str(exc), parent=self.app)
            return
        CodeDialog(self.app, t("CircuitikZ exportado"),
                   circuitikz_source(self.document),
                   note=f"{t('Requiere')} \\usepackage[american]{{circuitikz}} · {path}")

    def can_close(self) -> bool:
        return self._confirm_discard()

    def _update_status(self, message: str = "") -> None:
        if not message:
            if self.tool == "wire" and self.pending_wire is not None:
                message = t("Cable: elegí el segundo extremo · Esc cancela")
            elif self.tool == "wire":
                message = t("Cable: elegí el primer extremo")
            elif self.tool in COMPONENT_SPECS:
                message = f"{t('Colocar')} {t(COMPONENT_SPECS[self.tool].name).lower()}"
            else:
                message = t("Clic para seleccionar · arrastrá para mover")
        self.status.configure(text=message)
