"""
gui/app.py
-----------
Main GUI: file loading, interactive preview and export of oscilloscope /
LTspice data (time domain, frequency response and parametric X/Y curves)
for LaTeX reports.

Layout: left panel = signal list + per-channel parameters, center = embedded
Matplotlib canvas, right panel = global plot settings and export actions.
"""

from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox
from typing import Optional

import customtkinter as ctk
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.ticker import AutoMinorLocator, FuncFormatter, LogLocator, NullFormatter

from core.data_io import (
    DEFAULT_COLOR_CYCLE, FREQ_UNIT_LATEX, TIME_UNIT_LATEX, VOLT_UNIT_LATEX,
    Signal, build_signal, read_table, x_units_for_domain, y_units_for_kind,
)
from core.export import (
    FONT_FAMILIES, LEGEND_POSITIONS, export_csv_combined, export_csv_individual,
    export_figure, export_xy_csv, legend_kwargs, set_publication_style,
)
from core.processing import crop, decimate, decimate_to_target

PLOT_MODES = ["Tiempo / Frecuencia", "Modo X/Y", "Diagrama de Bode"]
BODE_LAYOUTS = ["Juntos (superpuestos, Y1/Y2)", "Separados (independientes)"]
LINESTYLES = ["-", "--", "-.", ":"]
DEC_MODES = {"Ninguno": "none", "Factor N": "factor", "Máx. puntos": "target"}


def _parse_float(text: str, fallback: float = 0.0) -> float:
    """Parse a float defensively, accepting comma as decimal separator."""
    try:
        return float(text.strip().replace(",", "."))
    except (ValueError, AttributeError):
        return fallback


def _parse_optional_float(text: str) -> Optional[float]:
    """Return None for an empty field, otherwise a float (None if invalid)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _configure_minor_ticks(axis, scale: str) -> None:
    """
    Configure minor ticks for a single axis (xaxis or yaxis).

    On a log-scaled axis, Matplotlib's default minor locator thins out the
    secondary lines once the plotted range spans more than a couple of
    decades, showing only some of 2..9 per decade. This forces every
    secondary line (2,3,4,5,6,7,8,9 -> 20,30,...,90 -> ...) to be generated
    across the full axis range, with no tick labels to avoid clutter.
    """
    if scale == "log":
        axis.set_minor_locator(LogLocator(base=10.0, subs=np.arange(2, 10)))
        axis.set_minor_formatter(NullFormatter())
    else:
        axis.set_minor_locator(AutoMinorLocator())


# SI/engineering-notation prefixes, largest scale first (checked in order).
_ENGINEERING_PREFIXES = [
    (1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
    (1e-3, "m"), (1e-6, "\u00b5"), (1e-9, "n"), (1e-12, "p"),
]


def _engineering_tick_label(value: float, _pos=None) -> str:
    """
    Format a log-axis tick as plain engineering notation (1, 10, 100, 1k,
    10k, 1M, ...) instead of Matplotlib's default "10^n" mathtext style --
    the convention used in Bode/filter plots in most electronics reports.
    """
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    scale, suffix = 1.0, ""
    for s, suf in _ENGINEERING_PREFIXES:
        if magnitude >= s * (1 - 1e-9):
            scale, suffix = s, suf
            break
    mantissa = magnitude / scale
    text = f"{round(mantissa):d}" if abs(mantissa - round(mantissa)) < 1e-6 else f"{mantissa:.2g}"
    return f"{sign}{text}{suffix}"


def _voltage_to_db(y: np.ndarray) -> np.ndarray:
    """Convert a linear-voltage magnitude array to dB (20*log10|y|), NaN-safe."""
    with np.errstate(divide="ignore", invalid="ignore"):
        y_db = 20.0 * np.log10(np.abs(y))
    return np.where(np.isfinite(y_db), y_db, np.nan)


_BODE_SUFFIX_RE = re.compile(r"(_dB|_deg|_Vlin)$", re.IGNORECASE)


def _bode_base_key(name: str) -> str:
    """
    Strip the auto-generated Bode suffix (_dB / _deg / _Vlin) from a signal
    name. Two signals sharing the same base key came from the same original
    trace, so the combined Bode view can pair them (same color, dashed phase).
    """
    return _BODE_SUFFIX_RE.sub("", name)


# ========================================================================== #
# Editable subplot-margins dialog (keyboard input instead of mouse-only sliders)
# ========================================================================== #
class SubplotConfigDialog(ctk.CTkToplevel):
    """
    Keyboard-friendly replacement for Matplotlib's built-in "Configure
    subplots" tool. Keeps the familiar draggable slider for each margin, but
    pairs it with a text entry: type an exact value and press Enter, or drag
    the slider as before -- both stay in sync and apply live.
    """

    FIELDS = [
        ("left", "Margen izquierdo"), ("right", "Margen derecho"),
        ("bottom", "Margen inferior"), ("top", "Margen superior"),
        ("wspace", "Espacio horiz. (wspace)"), ("hspace", "Espacio vert. (hspace)"),
    ]
    DEFAULTS = {"left": 0.125, "right": 0.9, "bottom": 0.11,
                "top": 0.88, "wspace": 0.2, "hspace": 0.2}

    def __init__(self, master, fig):
        super().__init__(master)
        self.fig = fig
        self.title("Configurar subplots")
        self.geometry("400x470")
        self.minsize(380, 440)
        self.resizable(True, True)
        self.transient(master)

        ctk.CTkLabel(self, text="Arrastrá el slider o tipeá el valor y Enter",
                     font=ctk.CTkFont(weight="bold")).pack(pady=(14, 8), padx=14, anchor="w")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(side="bottom", pady=14)
        ctk.CTkButton(btns, text="Aplicar", command=self._apply).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Reset", fg_color="gray40", command=self._reset).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Cerrar", fg_color="gray40", command=self.destroy).pack(side="left", padx=6)

        self.vars: dict[str, ctk.StringVar] = {}
        self.sliders: dict[str, ctk.CTkSlider] = {}
        pars = fig.subplotpars
        for name, label in self.FIELDS:
            self._build_row(name, label, getattr(pars, name))

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build_row(self, name: str, label: str, current: float) -> None:
        var = ctk.StringVar(value=f"{current:.3f}")
        self.vars[name] = var

        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=14, pady=6)
        ctk.CTkLabel(frame, text=label, anchor="w").pack(anchor="w")

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", pady=(2, 0))

        slider = ctk.CTkSlider(row, from_=0.0, to=1.0, number_of_steps=400,
                                command=lambda v, n=name: self._on_slider(n, v))
        slider.set(min(1.0, max(0.0, current)))
        slider.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.sliders[name] = slider

        entry = ctk.CTkEntry(row, textvariable=var, width=64)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e, n=name: self._on_entry(n))
        entry.bind("<KP_Enter>", lambda _e, n=name: self._on_entry(n))
        entry.bind("<FocusOut>", lambda _e, n=name: self._on_entry(n))

    def _on_slider(self, name: str, value: float) -> None:
        """Dragging the slider updates its entry and applies live (silently
        skipping transient invalid states, e.g. left momentarily > right)."""
        self.vars[name].set(f"{float(value):.3f}")
        self._apply(silent=True)

    def _on_entry(self, name: str) -> None:
        """Typing a value + Enter (or leaving the field) snaps the slider to it."""
        try:
            value = float(self.vars[name].get().strip().replace(",", "."))
        except ValueError:
            messagebox.showerror("Valor inválido", "Ingresá un número (0 a 1).", parent=self)
            return
        value = min(1.0, max(0.0, value))
        self.vars[name].set(f"{value:.3f}")
        self.sliders[name].set(value)
        self._apply()

    def _apply(self, silent: bool = False) -> None:
        try:
            kwargs = {name: float(var.get().strip().replace(",", "."))
                      for name, var in self.vars.items()}
        except ValueError:
            if not silent:
                messagebox.showerror("Valor inválido", "Todos los campos deben ser números.",
                                      parent=self)
            return
        try:
            self.fig.subplots_adjust(**kwargs)
        except Exception as exc:
            if not silent:
                messagebox.showerror("Error", str(exc), parent=self)
            return
        self.fig.canvas.draw_idle()

    def _reset(self) -> None:
        for name, val in self.DEFAULTS.items():
            self.vars[name].set(f"{val:.3f}")
            self.sliders[name].set(val)
        self._apply()


class EditableNavigationToolbar(NavigationToolbar2Tk):
    """Matplotlib toolbar whose 'Configure subplots' button opens `SubplotConfigDialog`."""

    def configure_subplots(self) -> None:  # noqa: D102 - overrides base class
        SubplotConfigDialog(self.canvas.get_tk_widget(), self.canvas.figure)


# ========================================================================== #
# Column selection dialog (files with more than two columns)
# ========================================================================== #
class ColumnSelectDialog(ctk.CTkToplevel):
    """
    Modal dialog to pick the X column and one or more Y columns when a file
    exposes more than two numeric columns (multi-channel scope captures or
    LTspice AC sweeps expanded into dB / phase / linear columns). Each ticked
    Y column becomes an independent Signal.
    """

    def __init__(self, master, columns: list[str], col_kind: dict[str, str], filename: str):
        super().__init__(master)
        self.title(f"Seleccionar columnas — {filename}")
        self.geometry("420x520")
        self.minsize(380, 420)
        self.resizable(True, True)
        self.result: Optional[tuple[str, list[str]]] = None

        self.transient(master)
        self.grab_set()

        # Buttons are packed to the bottom edge FIRST (before the expanding
        # column lists below) so they always stay visible and are never
        # pushed off-window by tall content or Windows DPI scaling.
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(side="bottom", pady=12)
        ctk.CTkButton(btns, text="Aceptar", command=self._accept).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Cancelar", command=self._cancel,
                      fg_color="gray40").pack(side="left", padx=6)

        ctk.CTkLabel(self, text=filename, font=ctk.CTkFont(weight="bold")
                     ).pack(pady=(12, 2), padx=12, anchor="w")

        x_candidates = [c for c in columns if col_kind.get(c) in ("time", "freq")] or columns
        default_x = x_candidates[0]
        kind_label = {"time": "tiempo", "freq": "frecuencia", "dB": "magnitud [dB]",
                       "deg": "fase [°]", "voltage": "tensión"}

        ctk.CTkLabel(self, text="Columna de eje X:").pack(padx=12, anchor="w")
        self.x_var = ctk.StringVar(value=default_x)
        x_frame = ctk.CTkScrollableFrame(self, height=110)
        x_frame.pack(fill="x", expand=False, padx=12, pady=(0, 6))
        for col in columns:
            ctk.CTkRadioButton(
                x_frame, text=f"{col}  ({kind_label.get(col_kind.get(col, ''), '—')})",
                variable=self.x_var, value=col).pack(anchor="w", pady=1)

        ctk.CTkLabel(self, text="Columnas de valor (una señal por columna marcada):"
                     ).pack(padx=12, pady=(6, 2), anchor="w")

        y_frame = ctk.CTkScrollableFrame(self, height=200)
        y_frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        self.y_vars: dict[str, ctk.BooleanVar] = {}
        for col in columns:
            # Phase and linear-magnitude columns stay unticked by default:
            # the dB trace is the usual primary deliverable of an AC sweep.
            default_on = (col != default_x and col_kind.get(col) not in ("deg", "time", "freq")
                          and not col.endswith("_Vlin"))
            var = ctk.BooleanVar(value=default_on)
            self.y_vars[col] = var
            ctk.CTkCheckBox(
                y_frame, text=f"{col}  ({kind_label.get(col_kind.get(col, ''), '—')})",
                variable=var).pack(anchor="w", pady=1)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window(self)

    def _accept(self) -> None:
        x_col = self.x_var.get()
        y_cols = [c for c, v in self.y_vars.items() if v.get() and c != x_col]
        if not y_cols:
            messagebox.showwarning(
                "Selección inválida",
                "Seleccioná al menos una columna de valor distinta de la del eje X.",
                parent=self)
            return
        self.result = (x_col, y_cols)
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


# ========================================================================== #
# Main application
# ========================================================================== #
class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Osci/LTspice → LaTeX Data Tool")
        self.geometry("1480x880")
        self.minsize(1200, 700)

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        # ------------------------- Application state ------------------------- #
        self.signals: dict[str, Signal] = {}
        self.signal_order: list[str] = []
        self.selected_uid: Optional[str] = None
        self.row_widgets: dict[str, dict] = {}
        self._color_index = 0
        self._axis_labels_dirty = False   # True once the user edits axis labels

        self.decimal_comma_var = ctk.BooleanVar(value=False)
        # Default font matches a typical LaTeX report (lmodern / Computer
        # Modern), so exported figures blend in with the document out of
        # the box. Selectable in the GUI like any other font.
        self.font_family_var = ctk.StringVar(value="LaTeX (Computer Modern)")

        set_publication_style(font_family=self.font_family_var.get())

        self._build_layout()
        self.update_plot()

    # ------------------------------------------------------------------ #
    # Layout construction
    # ------------------------------------------------------------------ #
    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_left_panel()
        self._build_center_panel()
        self._build_right_panel()
        # Global keyboard shortcut: Delete/Backspace removes the selected
        # signal from anywhere in the window (skipped inside text entries).
        self.bind_all("<Delete>", self._on_delete_key)
        self.bind_all("<BackSpace>", self._on_delete_key)

    def _build_left_panel(self) -> None:
        left = ctk.CTkFrame(self, width=330)
        left.grid(row=0, column=0, sticky="ns", padx=(8, 4), pady=8)
        left.grid_propagate(False)

        ctk.CTkLabel(left, text="Señales cargadas",
                     font=ctk.CTkFont(weight="bold", size=14)
                     ).pack(pady=(10, 4), padx=10, anchor="w")

        btns = ctk.CTkFrame(left, fg_color="transparent")
        btns.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkButton(btns, text="+ Cargar archivo(s)", command=self._load_files
                      ).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(btns, text="− Quitar", width=70, fg_color="gray40",
                      command=self._remove_selected_signal).pack(side="left", padx=(0, 4))
        ctk.CTkButton(btns, text="Quitar todos", width=95, fg_color="gray30",
                      command=self._remove_all_signals).pack(side="left")

        ctk.CTkLabel(left, text="Tip: seleccioná una señal y presioná Supr/Backspace para borrarla.",
                     text_color="gray", font=ctk.CTkFont(size=10), wraplength=300, justify="left"
                     ).pack(padx=10, pady=(0, 4), anchor="w")

        ctk.CTkCheckBox(left, text="Los archivos usan coma decimal",
                         variable=self.decimal_comma_var
                         ).pack(padx=10, pady=(0, 8), anchor="w")

        self.signal_list_frame = ctk.CTkScrollableFrame(left, height=200)
        self.signal_list_frame.pack(fill="x", padx=8, pady=(0, 8))

        self.param_frame = ctk.CTkScrollableFrame(left)
        self.param_frame.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        self._build_param_placeholder()

    def _build_param_placeholder(self) -> None:
        for w in self.param_frame.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.param_frame,
                     text="Seleccioná una señal de la lista\npara ajustar sus parámetros.",
                     justify="center", text_color="gray").pack(expand=True, pady=40)

    def _build_center_panel(self) -> None:
        center = ctk.CTkFrame(self)
        center.grid(row=0, column=1, sticky="nsew", padx=4, pady=8)
        center.grid_rowconfigure(1, weight=1)
        center.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(center, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 0))
        ctk.CTkLabel(top, text="Modo:").pack(side="left")
        self.plot_mode_var = ctk.StringVar(value=PLOT_MODES[0])
        ctk.CTkComboBox(top, values=PLOT_MODES, variable=self.plot_mode_var,
                         width=190, command=lambda _=None: self._on_mode_change()
                         ).pack(side="left", padx=(6, 12))
        ctk.CTkButton(top, text="Actualizar gráfico", command=self.update_plot,
                      width=150).pack(side="left")
        self.status_label = ctk.CTkLabel(top, text="", text_color="gray")
        self.status_label.pack(side="left", padx=12)

        plot_container = ctk.CTkFrame(center)
        plot_container.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)

        self.fig = Figure(figsize=(7.6, 5.2), dpi=100)
        self.axes: list = [self.fig.add_subplot(111)]
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_container)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        toolbar_frame = tk.Frame(plot_container)
        toolbar_frame.pack(fill="x")
        self.mpl_toolbar = EditableNavigationToolbar(self.canvas, toolbar_frame)
        self.mpl_toolbar.update()

        # X/Y mode axis pickers, shown only when that mode is active.
        self.xy_frame = ctk.CTkFrame(center)
        self.xy_frame.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 6))

        xy_row1 = ctk.CTkFrame(self.xy_frame, fg_color="transparent")
        xy_row1.pack(fill="x")
        ctk.CTkLabel(xy_row1, text="Eje X:").pack(side="left", padx=(8, 4), pady=(8, 4))
        self.xy_x_var = ctk.StringVar(value="")
        self.xy_x_combo = ctk.CTkComboBox(xy_row1, values=[""],
                                           variable=self.xy_x_var, width=180)
        self.xy_x_combo.pack(side="left", padx=4)
        ctk.CTkLabel(xy_row1, text="Eje Y:").pack(side="left", padx=(12, 4))
        self.xy_y_var = ctk.StringVar(value="")
        self.xy_y_combo = ctk.CTkComboBox(xy_row1, values=[""],
                                           variable=self.xy_y_var, width=180)
        self.xy_y_combo.pack(side="left", padx=4)

        xy_row2 = ctk.CTkFrame(self.xy_frame, fg_color="transparent")
        xy_row2.pack(fill="x")

        # Leyenda de la curva resultante (no de cada canal por separado):
        # una sola leyenda para el par X/Y, editable directamente acá.
        ctk.CTkLabel(xy_row2, text="Leyenda:").pack(side="left", padx=(8, 4), pady=(0, 8))
        self.xy_legend_var = ctk.StringVar(value="")
        xy_legend_entry = ctk.CTkEntry(xy_row2, textvariable=self.xy_legend_var, width=170)
        xy_legend_entry.pack(side="left", padx=2)
        xy_legend_entry.bind("<Return>", lambda _e: self.update_plot())

        # Color of the resulting X/Y curve. It is a synthesized curve (not
        # literally either underlying channel), so it gets its own color
        # instead of silently inheriting whichever signal is "Eje Y".
        ctk.CTkLabel(xy_row2, text="Color:").pack(side="left", padx=(12, 4))
        self.xy_color_var = ctk.StringVar(value=DEFAULT_COLOR_CYCLE[3])
        xy_color_entry = ctk.CTkEntry(xy_row2, textvariable=self.xy_color_var, width=80)
        xy_color_entry.pack(side="left", padx=2)
        self.xy_color_preview = ctk.CTkFrame(xy_row2, width=22, height=22,
                                              fg_color=self.xy_color_var.get(), corner_radius=4)
        self.xy_color_preview.pack(side="left", padx=4)
        self.xy_color_preview.pack_propagate(False)

        def _xy_update_preview(*_):
            value = self.xy_color_var.get().strip()
            try:
                self.xy_color_preview.configure(fg_color=value)
            except (tk.TclError, ValueError):
                pass   # invalid hex while typing: keep last valid preview

        self.xy_color_var.trace_add("write", _xy_update_preview)
        xy_color_entry.bind("<Return>", lambda _e: self.update_plot())

        def _xy_pick_color():
            initial = self.xy_color_var.get().strip() or "#1f77b4"
            try:
                _rgb, hex_color = colorchooser.askcolor(color=initial, parent=self)
            except tk.TclError:
                _rgb, hex_color = colorchooser.askcolor(parent=self)
            if hex_color:
                self.xy_color_var.set(hex_color)
                self.update_plot()

        ctk.CTkButton(xy_row2, text="Elegir...", width=70,
                      command=_xy_pick_color).pack(side="left", padx=4)
        self.xy_frame.grid_remove()

        # Bode mode layout picker, shown only when that mode is active.
        self.bode_frame = ctk.CTkFrame(center)
        self.bode_frame.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 6))
        ctk.CTkLabel(self.bode_frame, text="Disposición:").pack(side="left", padx=(8, 4), pady=8)
        self.bode_layout_var = ctk.StringVar(value=BODE_LAYOUTS[0])
        ctk.CTkComboBox(self.bode_frame, values=BODE_LAYOUTS,
                         variable=self.bode_layout_var, width=260,
                         command=lambda _=None: self.update_plot()
                         ).pack(side="left", padx=4)
        self.bode_frame.grid_remove()

    def _build_right_panel(self) -> None:
        right = ctk.CTkScrollableFrame(self, width=360)
        right.grid(row=0, column=2, sticky="ns", padx=(4, 8), pady=8)

        # ----------------------- Plot settings ----------------------- #
        ctk.CTkLabel(right, text="Ajustes del gráfico",
                     font=ctk.CTkFont(weight="bold", size=14)
                     ).pack(pady=(6, 4), padx=10, anchor="w")

        self.title_var = ctk.StringVar(value="")
        self._labeled_entry(right, "Título:", self.title_var, width=90, apply_on_enter=True)

        self.xlabel_var = ctk.StringVar(value="")
        self._labeled_entry(right, "Etiqueta X:", self.xlabel_var, width=90,
                            on_edit=self._mark_labels_dirty, apply_on_enter=True)
        self.ylabel_var = ctk.StringVar(value="")
        self._labeled_entry(right, "Etiqueta Y:", self.ylabel_var, width=90,
                            on_edit=self._mark_labels_dirty, apply_on_enter=True)
        self.ylabel2_var = ctk.StringVar(value="Fase [$^\\circ$]")
        self._labeled_entry(right, "Etiqueta Y2:", self.ylabel2_var, width=90, apply_on_enter=True)
        ctk.CTkLabel(right, text="Y2: fase en Bode, o señales con \"Eje Y secundario\" marcado.",
                     text_color="gray", font=ctk.CTkFont(size=10)
                     ).pack(padx=12, anchor="w")

        # Units
        units = ctk.CTkFrame(right, fg_color="transparent")
        units.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(units, text="Unidad X:", width=90, anchor="w").grid(row=0, column=0, sticky="w")
        self.unit_x_var = ctk.StringVar(value="us")
        self.unit_x_combo = ctk.CTkComboBox(units, values=["s", "ms", "us", "ns"],
                                             variable=self.unit_x_var, width=100)
        self.unit_x_combo.grid(row=0, column=1, padx=6)
        ctk.CTkLabel(units, text="Unidad Y:", width=90, anchor="w"
                     ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.unit_y_var = ctk.StringVar(value="V")
        self.unit_y_combo = ctk.CTkComboBox(units, values=["V", "mV"],
                                             variable=self.unit_y_var, width=100)
        self.unit_y_combo.grid(row=1, column=1, padx=6, pady=(6, 0))

        # Scales
        scales = ctk.CTkFrame(right, fg_color="transparent")
        scales.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(scales, text="Escala X:", width=90, anchor="w").grid(row=0, column=0, sticky="w")
        self.xscale_var = ctk.StringVar(value="linear")
        ctk.CTkComboBox(scales, values=["linear", "log"], variable=self.xscale_var,
                         width=100).grid(row=0, column=1, padx=6)
        ctk.CTkLabel(scales, text="Escala Y:", width=90, anchor="w"
                     ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.yscale_var = ctk.StringVar(value="linear")
        self.yscale_combo = ctk.CTkComboBox(scales, values=["linear", "log"], variable=self.yscale_var,
                                             width=100)
        self.yscale_combo.grid(row=1, column=1, padx=6, pady=(6, 0))

        self.engineering_ticks_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(right, text="Notación de ingeniería en ejes log (1, 10, 100, 1k...)",
                         variable=self.engineering_ticks_var).pack(padx=12, pady=(4, 2), anchor="w")

        # X range
        rng = ctk.CTkFrame(right, fg_color="transparent")
        rng.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(rng, text="X mín:", width=90, anchor="w").grid(row=0, column=0, sticky="w")
        self.xmin_var = ctk.StringVar(value="")
        xmin_entry = ctk.CTkEntry(rng, textvariable=self.xmin_var, width=100)
        xmin_entry.grid(row=0, column=1, padx=6)
        xmin_entry.bind("<Return>", lambda _e: self.update_plot())
        ctk.CTkLabel(rng, text="X máx:", width=90, anchor="w"
                     ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.xmax_var = ctk.StringVar(value="")
        xmax_entry = ctk.CTkEntry(rng, textvariable=self.xmax_var, width=100)
        xmax_entry.grid(row=1, column=1, padx=6, pady=(6, 0))
        xmax_entry.bind("<Return>", lambda _e: self.update_plot())
        ctk.CTkLabel(rng, text="(en la unidad X elegida; vacío = sin límite)",
                     text_color="gray", font=ctk.CTkFont(size=10)
                     ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # Downsampling
        dec = ctk.CTkFrame(right, fg_color="transparent")
        dec.pack(fill="x", padx=10, pady=(10, 2))
        ctk.CTkLabel(dec, text="Diezmado:", width=90, anchor="w").grid(row=0, column=0, sticky="w")
        self.dec_mode_var = ctk.StringVar(value="Ninguno")
        ctk.CTkComboBox(dec, values=list(DEC_MODES.keys()), variable=self.dec_mode_var,
                         width=140).grid(row=0, column=1, padx=6)
        self.dec_value_var = ctk.StringVar(value="1000")
        dec_value_entry = ctk.CTkEntry(dec, textvariable=self.dec_value_var, width=80)
        dec_value_entry.grid(row=0, column=2, padx=4)
        dec_value_entry.bind("<Return>", lambda _e: self.update_plot())

        # Typography
        font_frame = ctk.CTkFrame(right, fg_color="transparent")
        font_frame.pack(fill="x", padx=10, pady=(10, 2))
        ctk.CTkLabel(font_frame, text="Fuente:", width=90, anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkComboBox(font_frame, values=list(FONT_FAMILIES.keys()),
                         variable=self.font_family_var, width=140,
                         command=lambda _=None: self._on_font_change()
                         ).grid(row=0, column=1, padx=6)

        # Legend
        ctk.CTkLabel(right, text="Leyenda", font=ctk.CTkFont(weight="bold", size=13)
                     ).pack(pady=(12, 2), padx=10, anchor="w")
        self.legend_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(right, text="Mostrar leyenda", variable=self.legend_var
                         ).pack(padx=12, pady=2, anchor="w")
        leg = ctk.CTkFrame(right, fg_color="transparent")
        leg.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(leg, text="Posición:", width=90, anchor="w").pack(side="left")
        self.legend_pos_var = ctk.StringVar(value="upper right")
        ctk.CTkComboBox(leg, values=LEGEND_POSITIONS, variable=self.legend_pos_var,
                         width=190).pack(side="left", padx=6)

        self.grid_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(right, text="Mostrar grilla", variable=self.grid_var
                         ).pack(padx=12, pady=(6, 2), anchor="w")
        self.minor_grid_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(right, text="Grilla menor (útil en escala log)",
                         variable=self.minor_grid_var).pack(padx=12, pady=(2, 14), anchor="w")

        ctk.CTkFrame(right, height=2, fg_color="gray30").pack(fill="x", padx=10, pady=4)

        # ----------------------- Export ----------------------- #
        ctk.CTkLabel(right, text="Exportar CSV (PGFPlots)",
                     font=ctk.CTkFont(weight="bold", size=14)
                     ).pack(pady=(10, 4), padx=10, anchor="w")
        self.csv_mode_container = ctk.CTkFrame(right, fg_color="transparent")
        self.csv_mode_container.pack(fill="x", padx=10, pady=2)
        self.csv_mode_var = ctk.StringVar(value="Individual (1 archivo por señal)")
        self.csv_mode_combo = ctk.CTkComboBox(self.csv_mode_container,
                                               values=["Individual (1 archivo por señal)",
                                                       "Combinado (grilla común)"],
                                               variable=self.csv_mode_var, width=300)
        self.csv_xy_note = ctk.CTkLabel(
            self.csv_mode_container, text="Modo X/Y: se exporta la curva (par X/Y) actual.",
            text_color="gray", font=ctk.CTkFont(size=10))
        self.csv_mode_combo.pack(fill="x")   # default (non-XY) state
        ctk.CTkButton(right, text="Exportar CSV...", command=self._export_csv
                      ).pack(fill="x", padx=10, pady=(6, 14))

        ctk.CTkLabel(right, text="Exportar figura",
                     font=ctk.CTkFont(weight="bold", size=14)
                     ).pack(pady=(4, 4), padx=10, anchor="w")
        figf = ctk.CTkFrame(right, fg_color="transparent")
        figf.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(figf, text="Formato:", width=90, anchor="w").grid(row=0, column=0, sticky="w")
        self.fig_format_var = ctk.StringVar(value="pdf")
        ctk.CTkComboBox(figf, values=["pdf", "png", "svg", "pgf"],
                         variable=self.fig_format_var, width=100).grid(row=0, column=1, padx=6)
        ctk.CTkLabel(figf, text="DPI:", width=90, anchor="w"
                     ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.dpi_var = ctk.StringVar(value="300")
        ctk.CTkEntry(figf, textvariable=self.dpi_var, width=100
                     ).grid(row=1, column=1, padx=6, pady=(6, 0))
        ctk.CTkLabel(right, text="PDF/SVG son vectoriales; el DPI aplica al PNG.",
                     text_color="gray", font=ctk.CTkFont(size=10)
                     ).pack(padx=12, anchor="w", pady=(2, 0))
        ctk.CTkButton(right, text="Exportar figura...", command=self._export_figure
                      ).pack(fill="x", padx=10, pady=(8, 18))

    def _labeled_entry(self, parent, label: str, var, width: int = 60,
                       on_edit=None, apply_on_enter: bool = False):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(frame, text=label, width=width, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(frame, textvariable=var)
        entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        if on_edit is not None:
            entry.bind("<KeyRelease>", lambda _e: on_edit())
        if apply_on_enter:
            entry.bind("<Return>", lambda _e: self.update_plot())
        return entry

    def _mark_labels_dirty(self) -> None:
        """Stop auto-generating axis labels once the user typed custom ones."""
        self._axis_labels_dirty = True

    # ------------------------------------------------------------------ #
    # File loading / signal management
    # ------------------------------------------------------------------ #
    def _next_color(self) -> str:
        color = DEFAULT_COLOR_CYCLE[self._color_index % len(DEFAULT_COLOR_CYCLE)]
        self._color_index += 1
        return color

    def _load_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Seleccionar archivos de datos",
            filetypes=[("CSV/TXT", "*.csv *.txt"), ("Todos los archivos", "*.*")])
        if not paths:
            return

        loaded = 0
        for path in paths:
            try:
                df, col_kind = read_table(path, decimal_comma=self.decimal_comma_var.get())
            except Exception as exc:
                messagebox.showerror("Error al leer archivo",
                                      f"{os.path.basename(path)}:\n{exc}")
                continue

            columns = list(df.columns)
            base_name = os.path.splitext(os.path.basename(path))[0]

            if len(columns) == 2:
                x_col, y_cols = columns[0], [columns[1]]
            else:
                dialog = ColumnSelectDialog(self, columns, col_kind, os.path.basename(path))
                if dialog.result is None:
                    continue
                x_col, y_cols = dialog.result

            domain = "freq" if col_kind.get(x_col) == "freq" else "time"
            for y_col in y_cols:
                y_kind = col_kind.get(y_col, "voltage")
                name = base_name if len(y_cols) == 1 else f"{base_name}_{y_col}"
                try:
                    sig = build_signal(df, x_col, y_col, name, path,
                                        domain=domain, y_kind=y_kind,
                                        color=self._next_color())
                except Exception as exc:
                    messagebox.showerror("Error al procesar columna", f"{name}:\n{exc}")
                    continue
                if sig.t_raw.size == 0:
                    messagebox.showwarning(
                        "Columna vacía",
                        f"La columna '{y_col}' no contiene datos numéricos válidos.")
                    continue
                self.signals[sig.uid] = sig
                self.signal_order.append(sig.uid)
                loaded += 1

        if loaded:
            self._sync_unit_options()
            self._refresh_signal_list()
            self._refresh_xy_combos()
            self.status_label.configure(text=f"{loaded} señal(es) cargada(s).")
            self.update_plot()

    def _remove_selected_signal(self) -> None:
        if self.selected_uid is None:
            messagebox.showinfo("Sin selección", "Seleccioná primero una señal de la lista.")
            return
        self.signals.pop(self.selected_uid, None)
        if self.selected_uid in self.signal_order:
            self.signal_order.remove(self.selected_uid)
        self.selected_uid = None
        self._refresh_signal_list()
        self._refresh_xy_combos()
        self._build_param_placeholder()
        self.update_plot()

    def _remove_all_signals(self) -> None:
        if not self.signals:
            return
        if not messagebox.askyesno(
                "Quitar todas las señales",
                f"¿Eliminar las {len(self.signals)} señales cargadas? Esta acción no se puede deshacer."):
            return
        self.signals.clear()
        self.signal_order.clear()
        self.selected_uid = None
        self._refresh_signal_list()
        self._refresh_xy_combos()
        self._build_param_placeholder()
        self.update_plot()

    def _on_delete_key(self, event=None) -> None:
        """Global Supr/Backspace shortcut to remove the selected signal.

        Skipped while the user is typing in any text entry, so it never
        interferes with normal text editing (offsets, names, hex colors...).
        """
        focused = self.focus_get()
        if isinstance(focused, (tk.Entry, tk.Text)):
            return
        if self.selected_uid is None:
            return
        self._remove_selected_signal()

    def _refresh_signal_list(self) -> None:
        for w in self.signal_list_frame.winfo_children():
            w.destroy()
        self.row_widgets.clear()

        for uid in self.signal_order:
            sig = self.signals[uid]
            row = ctk.CTkFrame(self.signal_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)

            vis_var = ctk.BooleanVar(value=sig.visible)

            def toggle(uid=uid, var=vis_var):
                self.signals[uid].visible = var.get()
                self.update_plot()

            ctk.CTkCheckBox(row, text="", variable=vis_var, width=20,
                             command=toggle).pack(side="left")

            # Quick color access straight from the list: click the swatch to
            # open the native color picker, no need to select the channel
            # and scroll to its parameter panel first.
            swatch = ctk.CTkButton(row, text="", width=16, height=16, corner_radius=3,
                                    fg_color=sig.color or "#888888",
                                    hover_color=sig.color or "#888888",
                                    border_width=1, border_color="gray50", cursor="hand2")
            swatch.configure(command=lambda u=uid, s=swatch: self._pick_row_color(u, s))
            swatch.pack(side="left", padx=(0, 6))

            tag = {"dB": " [dB]", "deg": " [°]"}.get(sig.y_kind, "")
            label = ctk.CTkLabel(row, text=f"{sig.name}{tag}", anchor="w", cursor="hand2")
            label.pack(side="left", fill="x", expand=True)
            label.bind("<Button-1>", lambda _e, u=uid: self._select_signal(u))

            self.row_widgets[uid] = {"row": row, "label": label, "swatch": swatch}

        self._highlight_selected()

    def _pick_row_color(self, uid: str, swatch: ctk.CTkButton) -> None:
        """Open the native color picker for a signal directly from the list row."""
        sig = self.signals.get(uid)
        if sig is None:
            return
        initial = sig.color or "#1f77b4"
        try:
            _rgb, hex_color = colorchooser.askcolor(color=initial, parent=self,
                                                     title=f"Color de {sig.name}")
        except tk.TclError:
            _rgb, hex_color = colorchooser.askcolor(parent=self, title=f"Color de {sig.name}")
        if not hex_color:
            return
        sig.color = hex_color
        swatch.configure(fg_color=hex_color, hover_color=hex_color)
        if self.selected_uid == uid:
            self._build_param_panel(uid)   # keep the open per-channel panel in sync
        self.update_plot()

    def _highlight_selected(self) -> None:
        for uid, widgets in self.row_widgets.items():
            color = ("gray75", "gray30") if uid == self.selected_uid else "transparent"
            widgets["row"].configure(fg_color=color)

    def _select_signal(self, uid: str) -> None:
        self.selected_uid = uid
        self._highlight_selected()
        self._build_param_panel(uid)

    def _refresh_xy_combos(self) -> None:
        names = [self.signals[uid].name for uid in self.signal_order]
        values = names or [""]
        self.xy_x_combo.configure(values=values)
        self.xy_y_combo.configure(values=values)
        if self.xy_x_var.get() not in values:
            self.xy_x_var.set(values[0])
        if self.xy_y_var.get() not in values:
            self.xy_y_var.set(values[1] if len(values) > 1 else values[0])

    def _dominant_domain(self) -> str:
        """Domain ("time"/"freq") of the currently loaded signals, majority-freq wins."""
        domains = {self.signals[u].domain for u in self.signal_order}
        return "freq" if domains == {"freq"} else "time"

    def _reset_xscale_for_domain(self, domain: str) -> None:
        """
        Snap the X-axis scale to the sensible default for a domain: log for
        frequency sweeps/Bode, linear for time-domain. Called on file load
        and on every plot-mode switch, so leaving Bode (or a freq-domain
        view) for the standard time view always lands back on a linear axis
        instead of keeping a stale log scale from the previous mode -- and
        vice versa.
        """
        if domain == "freq":
            self.xscale_var.set("log")
            self.minor_grid_var.set(True)
        else:
            self.xscale_var.set("linear")

    def _sync_unit_options(self) -> None:
        """Match the X/Y unit dropdowns to the dominant domain and magnitude type."""
        domain = self._dominant_domain()
        kinds = {self.signals[u].y_kind for u in self.signal_order}

        x_values = list(x_units_for_domain(domain).keys())
        self.unit_x_combo.configure(values=x_values)
        if self.unit_x_var.get() not in x_values:
            self.unit_x_var.set("Hz" if domain == "freq" else "us")

        y_kind = kinds.pop() if len(kinds) == 1 else "voltage"
        y_values = list(y_units_for_kind(y_kind).keys())
        self.unit_y_combo.configure(values=y_values)
        if self.unit_y_var.get() not in y_values:
            self.unit_y_var.set(y_values[0])

        # Frequency sweeps default to a log X axis, the standard for filters.
        # One-directional on purpose: this runs after routine file loads and
        # per-channel edits too, so it must never silently undo a deliberate
        # manual linear<->log choice made within the same domain. The full
        # bidirectional reset only happens on an explicit mode switch, see
        # `_reset_xscale_for_domain` calls in `_on_mode_change`.
        if domain == "freq" and self.xscale_var.get() == "linear":
            self._reset_xscale_for_domain("freq")

    # ------------------------------------------------------------------ #
    # Per-channel parameter panel
    # ------------------------------------------------------------------ #
    def _build_param_panel(self, uid: str) -> None:
        sig = self.signals[uid]
        for w in self.param_frame.winfo_children():
            w.destroy()

        ctk.CTkLabel(self.param_frame, text=f"Parámetros — {sig.name}",
                     font=ctk.CTkFont(weight="bold")).pack(pady=(8, 6), padx=8, anchor="w")

        name_var = ctk.StringVar(value=sig.name)
        self._labeled_entry(self.param_frame, "Nombre:", name_var, width=80)

        legend_var = ctk.StringVar(value=sig.legend_label or "")
        self._labeled_entry(self.param_frame, "Leyenda:", legend_var, width=80)
        ctk.CTkLabel(self.param_frame,
                     text="Vacío = usa el nombre. Acepta mathtext: $V_{out}$",
                     text_color="gray", font=ctk.CTkFont(size=10)
                     ).pack(padx=12, anchor="w")

        # Domain / magnitude type
        dom = ctk.CTkFrame(self.param_frame, fg_color="transparent")
        dom.pack(fill="x", padx=8, pady=(8, 2))
        ctk.CTkLabel(dom, text="Dominio:", width=80, anchor="w").grid(row=0, column=0, sticky="w")
        domain_var = ctk.StringVar(value=sig.domain)
        ctk.CTkComboBox(dom, values=["time", "freq"], variable=domain_var,
                         width=110).grid(row=0, column=1, padx=4)
        ctk.CTkLabel(dom, text="Magnitud:", width=80, anchor="w"
                     ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ykind_var = ctk.StringVar(value=sig.y_kind)
        ctk.CTkComboBox(dom, values=["voltage", "dB", "deg"], variable=ykind_var,
                         width=110).grid(row=1, column=1, padx=4, pady=(6, 0))

        # Source units
        units = ctk.CTkFrame(self.param_frame, fg_color="transparent")
        units.pack(fill="x", padx=8, pady=(8, 2))
        ctk.CTkLabel(units, text="Unidad X:", width=80, anchor="w").grid(row=0, column=0, sticky="w")
        unit_x_in_var = ctk.StringVar(value=sig.unit_t_in)
        unit_x_combo = ctk.CTkComboBox(units, values=list(x_units_for_domain(sig.domain).keys()),
                                        variable=unit_x_in_var, width=110)
        unit_x_combo.grid(row=0, column=1, padx=4)
        ctk.CTkLabel(units, text="Unidad Y:", width=80, anchor="w"
                     ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        unit_y_in_var = ctk.StringVar(value=sig.unit_v_in)
        unit_y_combo = ctk.CTkComboBox(units, values=list(y_units_for_kind(sig.y_kind).keys()),
                                        variable=unit_y_in_var, width=110)
        unit_y_combo.grid(row=1, column=1, padx=4, pady=(6, 0))
        ctk.CTkLabel(self.param_frame, text="(unidad en la que vienen los datos del archivo)",
                     text_color="gray", font=ctk.CTkFont(size=10)
                     ).pack(padx=12, anchor="w")

        def _sync_source_units(_=None):
            xs = list(x_units_for_domain(domain_var.get()).keys())
            unit_x_combo.configure(values=xs)
            if unit_x_in_var.get() not in xs:
                unit_x_in_var.set(xs[0])
            ys = list(y_units_for_kind(ykind_var.get()).keys())
            unit_y_combo.configure(values=ys)
            if unit_y_in_var.get() not in ys:
                unit_y_in_var.set(ys[0])

        domain_var.trace_add("write", lambda *_: _sync_source_units())
        ykind_var.trace_add("write", lambda *_: _sync_source_units())

        x_unit_now = sig.unit_t_in
        y_unit_now = sig.unit_v_in
        xoff_var = ctk.StringVar(
            value=f"{sig.t_offset / x_units_for_domain(sig.domain)[x_unit_now]:g}")
        self._labeled_entry(self.param_frame, f"Offset X:", xoff_var, width=80)
        yoff_var = ctk.StringVar(
            value=f"{sig.v_offset / y_units_for_kind(sig.y_kind)[y_unit_now]:g}")
        self._labeled_entry(self.param_frame, f"Offset Y:", yoff_var, width=80)
        gain_var = ctk.StringVar(value=f"{sig.gain:g}")
        self._labeled_entry(self.param_frame, "Ganancia:", gain_var, width=80)

        invert_var = ctk.BooleanVar(value=sig.invert)
        ctk.CTkCheckBox(self.param_frame, text="Invertir señal (×−1)",
                         variable=invert_var).pack(padx=10, pady=6, anchor="w")

        secondary_var = ctk.BooleanVar(value=sig.secondary_y)
        ctk.CTkCheckBox(self.param_frame, text="Eje Y secundario (Y2)",
                         variable=secondary_var).pack(padx=10, pady=(0, 2), anchor="w")
        ctk.CTkLabel(self.param_frame,
                     text="Modo Tiempo/Frecuencia: escala propia sin tocar la ganancia.",
                     text_color="gray", font=ctk.CTkFont(size=10)
                     ).pack(padx=12, pady=(0, 6), anchor="w")

        style = ctk.CTkFrame(self.param_frame, fg_color="transparent")
        style.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(style, text="Línea:", width=80, anchor="w").pack(side="left")
        style_var = ctk.StringVar(value=sig.linestyle)
        ctk.CTkComboBox(style, values=LINESTYLES, variable=style_var,
                         width=90).pack(side="left", padx=4)

        # Color: hex entry + live preview/RGB readout + interactive OS picker
        # + a clickable quick palette, all kept in sync via color_var.
        color_frame = ctk.CTkFrame(self.param_frame, fg_color="transparent")
        color_frame.pack(fill="x", padx=8, pady=(8, 2))
        ctk.CTkLabel(color_frame, text="Color:", width=80, anchor="w").pack(side="left")
        color_var = ctk.StringVar(value=sig.color or DEFAULT_COLOR_CYCLE[0])
        color_entry = ctk.CTkEntry(color_frame, textvariable=color_var, width=90)
        color_entry.pack(side="left", padx=4)
        preview = ctk.CTkFrame(color_frame, width=26, height=26,
                                fg_color=color_var.get(), corner_radius=4)
        preview.pack(side="left", padx=6)
        preview.pack_propagate(False)
        rgb_label = ctk.CTkLabel(color_frame, text="", font=ctk.CTkFont(size=10),
                                  text_color="gray", width=95, anchor="w")
        rgb_label.pack(side="left", padx=(2, 0))

        def _update_preview(*_):
            value = color_var.get().strip()
            try:
                preview.configure(fg_color=value)
                r, g, b = (c // 256 for c in self.winfo_rgb(value))
                rgb_label.configure(text=f"RGB {r},{g},{b}")
            except (tk.TclError, ValueError):
                rgb_label.configure(text="color inválido")   # invalid while typing

        color_var.trace_add("write", _update_preview)
        _update_preview()

        def _pick_color():
            initial = color_var.get().strip() or "#1f77b4"
            try:
                _rgb, hex_color = colorchooser.askcolor(color=initial, parent=self)
            except tk.TclError:
                _rgb, hex_color = colorchooser.askcolor(parent=self)
            if hex_color:
                color_var.set(hex_color)

        ctk.CTkButton(color_frame, text="Elegir...", width=70,
                      command=_pick_color).pack(side="left", padx=4)

        # Quick palette: click a swatch to apply that color instantly.
        palette = ctk.CTkFrame(self.param_frame, fg_color="transparent")
        palette.pack(fill="x", padx=88, pady=(0, 8))
        for hex_c in DEFAULT_COLOR_CYCLE:
            ctk.CTkButton(palette, text="", width=18, height=18, corner_radius=3,
                          fg_color=hex_c, hover_color=hex_c, border_width=1,
                          border_color="gray50",
                          command=lambda c=hex_c: color_var.set(c)
                          ).pack(side="left", padx=1)

        def apply_changes():
            new_domain = domain_var.get()
            new_kind = ykind_var.get()
            x_units = x_units_for_domain(new_domain)
            y_units = y_units_for_kind(new_kind)
            new_ux = unit_x_in_var.get() if unit_x_in_var.get() in x_units else list(x_units)[0]
            new_uy = unit_y_in_var.get() if unit_y_in_var.get() in y_units else list(y_units)[0]

            color = color_var.get().strip()
            try:
                preview.configure(fg_color=color)
            except (tk.TclError, ValueError):
                messagebox.showerror(
                    "Color inválido",
                    f"'{color}' no es un color válido. Usá formato hex (#RRGGBB) "
                    "o el selector gráfico.")
                return

            sig.name = name_var.get().strip() or sig.name
            sig.legend_label = legend_var.get().strip() or None
            sig.domain = new_domain
            sig.y_kind = new_kind
            sig.unit_t_in = new_ux
            sig.unit_v_in = new_uy
            sig.t_offset = _parse_float(xoff_var.get(), 0.0) * x_units[new_ux]
            sig.v_offset = _parse_float(yoff_var.get(), 0.0) * y_units[new_uy]
            sig.gain = _parse_float(gain_var.get(), 1.0)
            sig.invert = invert_var.get()
            sig.linestyle = style_var.get()
            sig.color = color
            sig.secondary_y = secondary_var.get()

            self._sync_unit_options()
            self._refresh_signal_list()
            self._refresh_xy_combos()
            self._select_signal(uid)
            self.update_plot()

        ctk.CTkButton(self.param_frame, text="Aplicar cambios", command=apply_changes
                      ).pack(fill="x", padx=8, pady=(12, 10))

    # ------------------------------------------------------------------ #
    # Settings gathering / plotting
    # ------------------------------------------------------------------ #
    def _on_mode_change(self) -> None:
        mode = self.plot_mode_var.get()
        if mode == "Modo X/Y":
            self._refresh_xy_combos()
            self.xy_frame.grid()
            self.bode_frame.grid_remove()
            self._reset_xscale_for_domain(self._dominant_domain())
        elif mode == "Diagrama de Bode":
            self.xy_frame.grid_remove()
            self.bode_frame.grid()
            self._reset_xscale_for_domain("freq")   # Bode is always frequency-domain
        else:
            self.xy_frame.grid_remove()
            self.bode_frame.grid_remove()
            self._reset_xscale_for_domain(self._dominant_domain())

        # CSV export: the Individual/Combinado choice is meaningless in X/Y
        # mode (a single paired curve is exported regardless) -- swap it for
        # a one-line note instead of leaving a control that does nothing.
        if mode == "Modo X/Y":
            self.csv_mode_combo.pack_forget()
            self.csv_xy_note.pack(fill="x")
        else:
            self.csv_xy_note.pack_forget()
            self.csv_mode_combo.pack(fill="x")

        # Escala Y is always forced to linear while in Bode mode (dB/phase
        # aren't log-scaled), so the control is disabled there instead of
        # pretending it has an effect.
        self.yscale_combo.configure(state="disabled" if mode == "Diagrama de Bode" else "normal")

        self._axis_labels_dirty = False   # regenerate default labels for the new mode
        self.update_plot()

    def _on_font_change(self) -> None:
        set_publication_style(font_family=self.font_family_var.get())
        self.update_plot()

    def _read_settings(self) -> dict:
        mode = self.plot_mode_var.get()
        x_unit = self.unit_x_var.get()
        y_unit = self.unit_y_var.get()

        domains = {self.signals[u].domain for u in self.signal_order} or {"time"}
        domain = "freq" if domains == {"freq"} else "time"
        x_factor = x_units_for_domain(domain).get(x_unit, 1.0)

        x_min_disp = _parse_optional_float(self.xmin_var.get())
        x_max_disp = _parse_optional_float(self.xmax_var.get())

        dec_mode = DEC_MODES.get(self.dec_mode_var.get(), "none")
        try:
            dec_value = int(float(self.dec_value_var.get().replace(",", ".")))
        except (ValueError, AttributeError):
            dec_value = 0   # invalid input: fall back to no decimation
        if dec_value <= 0:
            dec_mode = "none"

        return {
            "mode": mode,
            "domain": domain,
            "x_unit": x_unit,
            "y_unit": y_unit,
            "x_min": x_min_disp * x_factor if x_min_disp is not None else None,
            "x_max": x_max_disp * x_factor if x_max_disp is not None else None,
            "dec_mode": dec_mode,
            "dec_value": dec_value,
            "title": self.title_var.get().strip(),
            "xlabel": self.xlabel_var.get().strip(),
            "ylabel": self.ylabel_var.get().strip(),
            "ylabel2": self.ylabel2_var.get().strip(),
            "xscale": self.xscale_var.get(),
            "yscale": self.yscale_var.get(),
            "show_grid": self.grid_var.get(),
            "minor_grid": self.minor_grid_var.get(),
            "show_legend": self.legend_var.get(),
            "legend_pos": self.legend_pos_var.get(),
            "bode_layout": "separate" if self.bode_layout_var.get().startswith("Separados") else "shared",
        }

    def _gather_curves(self, settings: dict) -> list[tuple[str, np.ndarray, np.ndarray]]:
        """Return (uid, x, y) for visible signals, cropped and decimated, in base units."""
        out = []
        for uid in self.signal_order:
            sig = self.signals[uid]
            if not sig.visible:
                continue
            x, y = sig.processed()
            x, y = crop(x, y, settings["x_min"], settings["x_max"])
            if x.size == 0:
                continue
            if settings["dec_mode"] == "factor":
                x, y = decimate(x, y, settings["dec_value"])
            elif settings["dec_mode"] == "target":
                x, y = decimate_to_target(x, y, settings["dec_value"])
            out.append((uid, x, y))
        return out

    def _default_xlabel(self, settings: dict) -> str:
        unit = settings["x_unit"]
        if settings["domain"] == "freq":
            return f"$f$ [{FREQ_UNIT_LATEX.get(unit, unit)}]"
        return f"$t$ [${TIME_UNIT_LATEX.get(unit, unit)}$]"

    def _default_ylabel(self, settings: dict, curves) -> str:
        kinds = {self.signals[uid].y_kind for uid, _x, _y in curves} or {"voltage"}
        if kinds == {"dB"}:
            return "Magnitud [dB]"
        if kinds == {"deg"}:
            return "Fase [$^\\circ$]"
        unit = settings["y_unit"]
        return f"$V$ [{VOLT_UNIT_LATEX.get(unit, unit)}]"

    def _legend_label(self, sig: Signal) -> str:
        return sig.legend_label or sig.name

    def _apply_axis_cosmetics(self, ax, settings: dict, xlabel: str, ylabel: str) -> None:
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        try:
            ax.set_xscale(settings["xscale"])
            ax.set_yscale(settings["yscale"])
        except ValueError:
            ax.set_xscale("linear")
            ax.set_yscale("linear")

        # Plain "1, 10, 100, 1k, 10k..." major tick labels on log axes,
        # instead of Matplotlib's default "10^n" mathtext exponent style.
        if self.engineering_ticks_var.get():
            if settings["xscale"] == "log":
                ax.xaxis.set_major_formatter(FuncFormatter(_engineering_tick_label))
            if settings["yscale"] == "log":
                ax.yaxis.set_major_formatter(FuncFormatter(_engineering_tick_label))

        if settings["show_grid"]:
            ax.grid(True, which="major", linewidth=0.4, alpha=0.5)
            if settings["minor_grid"]:
                _configure_minor_ticks(ax.xaxis, settings["xscale"])
                _configure_minor_ticks(ax.yaxis, settings["yscale"])
                ax.grid(True, which="minor", linewidth=0.3, alpha=0.25)
            else:
                ax.grid(False, which="minor")
        else:
            ax.grid(False, which="both")

    def _reset_figure(self, n_axes: int, share_x: bool = True) -> None:
        """Rebuild the subplot grid only when the axes count changes."""
        self.fig.clear()
        if n_axes == 2:
            ax1 = self.fig.add_subplot(211)
            ax2 = self.fig.add_subplot(212, sharex=ax1 if share_x else None)
            self.axes = [ax1, ax2]
        else:
            self.axes = [self.fig.add_subplot(111)]

    def update_plot(self) -> None:
        try:
            settings = self._read_settings()
        except Exception as exc:
            messagebox.showerror("Error en ajustes", str(exc))
            return

        mode = settings["mode"]
        try:
            if mode == "Modo X/Y":
                n_points = self._draw_xy(settings)
            elif mode == "Diagrama de Bode":
                n_points = self._draw_bode(settings)
            else:
                n_points = self._draw_standard(settings)
        except Exception as exc:
            messagebox.showerror("Error al graficar", str(exc))
            return

        try:
            self.fig.tight_layout()
        except Exception:
            pass   # tight_layout can fail with an outside legend; harmless
        self.canvas.draw_idle()
        self.status_label.configure(text=f"{n_points} puntos en gráfico · modo: {mode}")

    def _finish_legend(self, ax, settings: dict, handles=None, labels=None) -> None:
        if not settings["show_legend"]:
            return
        kwargs = legend_kwargs(settings["legend_pos"])
        if handles is not None:
            if handles:
                ax.legend(handles, labels, **kwargs)
        elif ax.get_legend_handles_labels()[0]:
            ax.legend(**kwargs)

    def _draw_standard(self, settings: dict) -> int:
        """
        Time / frequency mode: every visible signal on a single axes, except
        signals flagged `secondary_y` which are drawn on an independent
        right-hand Y2 axis (twinx) — a real second scale, not a gain hack.
        """
        curves = self._gather_curves(settings)
        self._reset_figure(1)
        ax = self.axes[0]
        ax2 = None

        x_factor = x_units_for_domain(settings["domain"]).get(settings["x_unit"], 1.0)
        primary, secondary = [], []
        total = 0
        for uid, x, y in curves:
            sig = self.signals[uid]
            y_factor = y_units_for_kind(sig.y_kind).get(settings["y_unit"], 1.0)
            x_disp, y_disp = x / x_factor, y / y_factor
            (secondary if sig.secondary_y else primary).append((sig, x_disp, y_disp))
            total += x.size

        for sig, xd, yd in primary:
            ax.plot(xd, yd, linestyle=sig.linestyle, color=sig.color, label=self._legend_label(sig))

        if secondary:
            ax2 = ax.twinx()
            self.axes = [ax, ax2]
            for sig, xd, yd in secondary:
                ax2.plot(xd, yd, linestyle=sig.linestyle, color=sig.color,
                         label=self._legend_label(sig))

        xlabel = settings["xlabel"] or self._default_xlabel(settings)
        ylabel = settings["ylabel"] or self._default_ylabel(
            settings, [(s.uid, x, y) for s, x, y in primary] or curves)
        self._apply_axis_cosmetics(ax, settings, xlabel, ylabel)
        if ax2 is not None:
            ax2.set_ylabel(settings["ylabel2"] or "Y2")
            try:
                ax2.set_yscale(settings["yscale"])
            except ValueError:
                ax2.set_yscale("linear")
        if settings["title"]:
            ax.set_title(settings["title"])

        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels() if ax2 is not None else ([], [])
        if settings["show_legend"] and (h1 or h2):
            ax.legend(h1 + h2, l1 + l2, **legend_kwargs(settings["legend_pos"]))
        return total

    def _draw_bode(self, settings: dict) -> int:
        """
        Bode mode: magnitude (dB) and phase (deg) vs frequency.

        `bode_layout == "shared"` ("Juntos") overlays both on ONE set of
        axes using a secondary Y axis (twinx): magnitude reads off the left
        (Y1) scale, phase off the right (Y2) scale, phase always dashed and
        colored to match its paired magnitude trace (same original signal,
        matched via `_bode_base_key`). `"separate"` keeps two fully
        independent stacked axes, each with its own X ticks/label and each
        signal's own configured color/linestyle.

        Voltage-kind signals are converted to dB on the fly so a linear AC
        sweep still renders as a proper Bode magnitude plot.
        """
        curves = self._gather_curves(settings)
        if not curves:
            self._reset_figure(1)
            self._apply_axis_cosmetics(self.axes[0], settings,
                                        settings["xlabel"] or "$f$ [Hz]",
                                        settings["ylabel"] or "Magnitud [dB]")
            return 0

        separate = settings["bode_layout"] == "separate"
        x_factor = x_units_for_domain(settings["domain"]).get(settings["x_unit"], 1.0)
        xlabel = settings["xlabel"] or self._default_xlabel(settings)
        mag_label = settings["ylabel"] or "Magnitud [dB]"
        ph_label = settings["ylabel2"] or "Fase [$^\\circ$]"
        total = 0

        if separate:
            self._reset_figure(2, share_x=False)
            ax_mag, ax_ph = self.axes
            for uid, x, y in curves:
                sig = self.signals[uid]
                x_disp = x / x_factor
                label = self._legend_label(sig)
                if sig.y_kind == "deg":
                    ax_ph.plot(x_disp, y, linestyle=sig.linestyle, color=sig.color, label=label)
                else:
                    y_db = y if sig.y_kind == "dB" else _voltage_to_db(y)
                    ax_mag.plot(x_disp, y_db, linestyle=sig.linestyle, color=sig.color, label=label)
                total += x.size

            self._apply_axis_cosmetics(ax_mag, dict(settings, yscale="linear"), xlabel, mag_label)
            self._apply_axis_cosmetics(ax_ph, dict(settings, yscale="linear"), xlabel, ph_label)
            self.fig.subplots_adjust(hspace=0.45)
            if settings["title"]:
                ax_mag.set_title(settings["title"])
            self._finish_legend(ax_mag, settings)
            if ax_ph.get_legend_handles_labels()[0] and settings["show_legend"]:
                ax_ph.legend(**legend_kwargs(settings["legend_pos"]))
            return total

        # "Juntos": true overlay, one set of axes, dual Y scales (twinx).
        self._reset_figure(1)
        ax_mag = self.axes[0]
        ax_ph = ax_mag.twinx()
        self.axes = [ax_mag, ax_ph]

        deg_curves = [(uid, x, y) for uid, x, y in curves if self.signals[uid].y_kind == "deg"]
        mag_curves = [(uid, x, y) for uid, x, y in curves if self.signals[uid].y_kind != "deg"]
        used_deg: set = set()

        for uid, x, y in mag_curves:
            sig = self.signals[uid]
            x_disp = x / x_factor
            y_db = y if sig.y_kind == "dB" else _voltage_to_db(y)
            ax_mag.plot(x_disp, y_db, linestyle=sig.linestyle, color=sig.color,
                        label=self._legend_label(sig))
            total += x.size

            # Pair with the phase trace from the same original signal (if
            # any): same color, always dashed, drawn on the Y2 axis.
            key = _bode_base_key(sig.name)
            match = next((d for d in deg_curves if d[0] not in used_deg
                          and _bode_base_key(self.signals[d[0]].name) == key), None)
            if match:
                duid, dx, dy = match
                used_deg.add(duid)
                dsig = self.signals[duid]
                ax_ph.plot(dx / x_factor, dy, linestyle="--", color=sig.color,
                           label=self._legend_label(dsig))
                total += dx.size

        for duid, dx, dy in deg_curves:
            if duid in used_deg:
                continue
            dsig = self.signals[duid]
            ax_ph.plot(dx / x_factor, dy, linestyle="--", color=dsig.color,
                       label=self._legend_label(dsig))
            total += dx.size

        self._apply_axis_cosmetics(ax_mag, dict(settings, yscale="linear"), xlabel, mag_label)
        ax_ph.set_ylabel(ph_label)
        ax_ph.set_yscale("linear")
        if settings["title"]:
            ax_mag.set_title(settings["title"])

        h1, l1 = ax_mag.get_legend_handles_labels()
        h2, l2 = ax_ph.get_legend_handles_labels()
        if settings["show_legend"] and (h1 or h2):
            ax_mag.legend(h1 + h2, l1 + l2, **legend_kwargs(settings["legend_pos"]))
        return total

    def _draw_xy(self, settings: dict) -> int:
        """
        Parametric X/Y mode (Lissajous / transfer curves): one signal drives
        the X axis and another the Y axis. Both are interpolated onto a shared
        base so mismatched sample grids still produce a valid curve.
        """
        self._reset_figure(1)
        ax = self.axes[0]
        curves = {self.signals[uid].name: (uid, x, y)
                  for uid, x, y in self._gather_curves(settings)}

        x_name, y_name = self.xy_x_var.get(), self.xy_y_var.get()
        if x_name not in curves or y_name not in curves:
            self._apply_axis_cosmetics(ax, settings,
                                        settings["xlabel"] or "X",
                                        settings["ylabel"] or "Y")
            self.status_label.configure(
                text="Modo X/Y: elegí dos señales visibles con datos.")
            return 0

        x_uid, xb, xv = curves[x_name]
        y_uid, yb, yv = curves[y_name]
        sig_x, sig_y = self.signals[x_uid], self.signals[y_uid]

        base_min = max(float(xb[0]), float(yb[0]))
        base_max = min(float(xb[-1]), float(yb[-1]))
        if not np.isfinite(base_min) or not np.isfinite(base_max) or base_min >= base_max:
            raise ValueError(
                "Las dos señales elegidas no comparten un rango común en su eje "
                "independiente; ajustá el rango o el offset X.")

        n = int(min(max(len(xb), len(yb)), 20000))
        base = np.linspace(base_min, base_max, n)
        x_curve = np.interp(base, xb, xv)
        y_curve = np.interp(base, yb, yv)

        fx = y_units_for_kind(sig_x.y_kind).get(settings["y_unit"], 1.0)
        fy = y_units_for_kind(sig_y.y_kind).get(settings["y_unit"], 1.0)
        xy_color = self.xy_color_var.get().strip() or sig_y.color
        xy_label = self.xy_legend_var.get().strip() or \
            f"{self._legend_label(sig_y)} vs {self._legend_label(sig_x)}"
        ax.plot(x_curve / fx, y_curve / fy, linestyle=sig_y.linestyle,
                color=xy_color, label=xy_label)

        unit = settings["y_unit"]
        default_x = f"{self._legend_label(sig_x)} [{VOLT_UNIT_LATEX.get(unit, unit)}]"
        default_y = f"{self._legend_label(sig_y)} [{VOLT_UNIT_LATEX.get(unit, unit)}]"
        self._apply_axis_cosmetics(ax, settings,
                                    settings["xlabel"] or default_x,
                                    settings["ylabel"] or default_y)
        if settings["title"]:
            ax.set_title(settings["title"])
        self._finish_legend(ax, settings)
        self._xy_last_curve = (f"{sig_y.name}_vs_{sig_x.name}", x_curve / fx, y_curve / fy)
        return n

    # ------------------------------------------------------------------ #
    # Export actions
    # ------------------------------------------------------------------ #
    def _export_csv(self) -> None:
        if not self.signals:
            messagebox.showinfo("Sin señales", "Cargá al menos una señal antes de exportar.")
            return

        settings = self._read_settings()

        # X/Y mode exports the parametric curve currently on screen.
        if settings["mode"] == "Modo X/Y":
            curve = getattr(self, "_xy_last_curve", None)
            if curve is None:
                messagebox.showinfo("Sin curva X/Y",
                                     "Generá primero una curva X/Y válida en el gráfico.")
                return
            out_dir = filedialog.askdirectory(title="Carpeta de destino para el CSV X/Y")
            if not out_dir:
                return
            try:
                paths = export_xy_csv([curve], out_dir)
            except Exception as exc:
                messagebox.showerror("Error al exportar", str(exc))
                return
            messagebox.showinfo("Exportación completa",
                                 f"Curva X/Y guardada en:\n{paths[0]}")
            return

        curves = self._gather_curves(settings)
        if not curves:
            messagebox.showinfo("Sin datos",
                                 "No hay señales visibles con datos en el rango seleccionado.")
            return

        payload = [(self.signals[uid].name, x, y,
                    self.signals[uid].domain, self.signals[uid].y_kind)
                   for uid, x, y in curves]

        if self.csv_mode_var.get().startswith("Individual"):
            out_dir = filedialog.askdirectory(title="Carpeta de destino para los CSV")
            if not out_dir:
                return
            try:
                paths = export_csv_individual(payload, out_dir,
                                               settings["x_unit"], settings["y_unit"])
            except Exception as exc:
                messagebox.showerror("Error al exportar", str(exc))
                return
            messagebox.showinfo("Exportación completa",
                                 f"Se generaron {len(paths)} archivo(s) en:\n{out_dir}")
        else:
            out_path = filedialog.asksaveasfilename(
                title="Guardar CSV combinado", defaultextension=".csv",
                filetypes=[("CSV", "*.csv")])
            if not out_path:
                return
            try:
                export_csv_combined(payload, out_path,
                                     settings["x_unit"], settings["y_unit"])
            except Exception as exc:
                messagebox.showerror("Error al exportar", str(exc))
                return
            messagebox.showinfo("Exportación completa", f"CSV combinado guardado en:\n{out_path}")

    def _export_figure(self) -> None:
        if not self.signals:
            messagebox.showinfo("Sin señales", "Cargá al menos una señal antes de exportar.")
            return

        fmt = self.fig_format_var.get()
        try:
            dpi = max(50, int(float(self.dpi_var.get().replace(",", "."))))
        except (ValueError, AttributeError):
            dpi = 300

        out_path = filedialog.asksaveasfilename(
            title="Guardar figura", defaultextension=f".{fmt}",
            filetypes=[(fmt.upper(), f"*.{fmt}")])
        if not out_path:
            return

        # The preview already uses the final rcParams (mathtext, no external
        # TeX), so the on-screen figure can be saved as-is.
        try:
            export_figure(self.fig, out_path, dpi=dpi)
        except Exception as exc:
            messagebox.showerror("Error al exportar figura", str(exc))
            return
        messagebox.showinfo("Exportación completa", f"Figura guardada en:\n{out_path}")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
