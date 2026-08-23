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

# Optional: enables dropping .csv/.txt files onto the window. Not part of
# CustomTkinter or core Tk, so its absence (or a build mismatched with this
# Python's Tcl/Tk) must never stop the app from starting -- see
# `App._enable_drag_and_drop`, which is the only place this is used.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = None
    TkinterDnD = None

from core.data_io import (
    FREQ_UNIT_LATEX, TIME_UNIT_LATEX, VOLT_UNIT_LATEX,
    Signal, build_signal, read_table, x_units_for_domain, y_units_for_kind,
)
from core.export import (
    FONT_FAMILIES, export_csv_combined, export_csv_individual,
    export_figure, export_xy_csv, set_publication_style,
)
from core.layout import (
    CUSTOM_ANCHOR_CORNERS, CUSTOM_POSITION, LEGEND_POSITIONS,
    legend_kwargs, reserve_legend_space,
)
from core import latex, session
from core.processing import crop, decimate, decimate_to_target
from core.units import parse_eng
from gui.overlays import AnnotationManager, CursorManager, format_eng
from gui.overlay_panel import OverlayWindow
from gui.theme import (
    TRACE_CYCLE, apply_plot_chrome, apply_theme, col, font, font_scale,
    set_font_scale, set_theme_mode, spaced, tk_color,
)
from gui.widgets import (
    Chip, LINE_GLYPHS, MeasurementsCard, Rule, SectionHeader,
    CodeDialog, Segmented, Splitter, StaticSection, TextPrompt, ShortcutsWindow,
    ToolButton, TraceRow, VRule, check_field, combo_field, entry_field,
    ghost_button, hint, primary_button, repaint_plain_widgets, segmented_field,
    stacked_entry, stacked_label,
)

# Window width at which the type scale is exactly as designed (1.0). Matches
# the default geometry, so the app opens at native size. Clamping lives in
# `theme.set_font_scale`.
_REFERENCE_WIDTH = 1480

# Draggable clamp for the side panels (see `App._drag_left` / `_drag_right`).
_LEFT_MIN, _LEFT_MAX = 220, 480
_RIGHT_MIN, _RIGHT_MAX = 260, 520

PLOT_MODES = ["Tiempo / Frecuencia", "Modo X/Y", "Diagrama de Bode"]
# Short labels for the mode strip; the internal identifiers above are what the
# drawing code and `_read_settings` keep using, so nothing downstream changes.
MODE_LABELS = {
    "Tiempo / Frecuencia": "Tiempo",
    "Modo X/Y": "X / Y",
    "Diagrama de Bode": "Bode",
}
BODE_LAYOUTS = ["Juntos (superpuestos, Y1/Y2)", "Separados (independientes)"]
BODE_LABELS = {BODE_LAYOUTS[0]: "Juntos", BODE_LAYOUTS[1]: "Separados"}
LINESTYLES = ["-", "--", "-.", ":"]
DEC_MODES = {"Ninguno": "none", "Factor N": "factor", "Máx. puntos": "target"}
SCALE_LABELS = {"linear": "lineal", "log": "log"}


def _parse_float(text: str, fallback: float = 0.0) -> float:
    """
    Parse a numeric field, accepting engineering notation.

    `2.2k`, `4u7`, `470p`, `10 kHz` and `-3dB` all resolve to a plain float in
    base units -- see `core.units.parse_eng`. Anything unparseable falls back
    to the supplied default rather than raising, because these run on every
    keystroke-triggered redraw.
    """
    value = parse_eng(text, None)
    return fallback if value is None else value


def _parse_optional_float(text: str) -> Optional[float]:
    """Same as `_parse_float`, but an empty field means "no limit"."""
    return parse_eng(text, None)


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
    Keyboard-first replacement for Matplotlib's "Configure subplots" tool.

    This used to pair every margin with a draggable slider. The sliders were
    removed: a slider is the wrong control for a value you want to set
    precisely, each drag fired a live re-layout of the whole figure, and
    CustomTkinter's slider was the least reliable widget on the panel. Typing
    a number and pressing Enter is both exact and instant; the presets cover
    the cases where you just want "a bit more room" without thinking in
    fractions.
    """

    FIELDS = [
        ("left", "Margen izquierdo"), ("right", "Margen derecho"),
        ("bottom", "Margen inferior"), ("top", "Margen superior"),
        ("wspace", "Espacio horizontal"), ("hspace", "Espacio vertical"),
    ]
    DEFAULTS = {"left": 0.125, "right": 0.9, "bottom": 0.11,
                "top": 0.88, "wspace": 0.2, "hspace": 0.2}
    # Ready-made margin sets for the two situations that actually come up.
    PRESETS = {
        "Compacto": {"left": 0.09, "right": 0.97, "bottom": 0.10,
                     "top": 0.95, "wspace": 0.18, "hspace": 0.28},
        "Leyenda externa": {"left": 0.10, "right": 0.78, "bottom": 0.12,
                            "top": 0.92, "wspace": 0.20, "hspace": 0.30},
    }

    def __init__(self, master, fig):
        super().__init__(master)
        self.fig = fig
        self.title("Márgenes del gráfico")
        self.geometry("380x430")
        self.minsize(340, 400)
        self.resizable(True, True)
        self.transient(master)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(head, text=spaced("Márgenes"), font=font("header"),
                     text_color=col("fg_muted")).pack(side="left")
        Rule(self).pack(fill="x", padx=20)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(12, 0))

        pars = fig.subplotpars
        self.vars: dict[str, ctk.StringVar] = {}
        for name, label in self.FIELDS:
            var = ctk.StringVar(value=f"{getattr(pars, name):.3f}")
            self.vars[name] = var
            entry_field(body, label, var, width=76, on_enter=self._apply,
                        label_width=132, rule=(name != "hspace"))

        hint(body, "Fracciones de 0 a 1. Enter aplica el valor.",
             wraplength=300).pack(fill="x", pady=(10, 0))

        presets = ctk.CTkFrame(body, fg_color="transparent")
        presets.pack(fill="x", pady=(12, 0))
        for name in self.PRESETS:
            ghost_button(presets, name, lambda n=name: self._preset(n),
                         width=132).pack(side="left", padx=(0, 8))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=16)
        primary_button(actions, "Aplicar", self._apply, height=28,
                       width=104).pack(side="right")
        ghost_button(actions, "Restablecer", self._reset,
                     width=112).pack(side="right", padx=(0, 8))
        ghost_button(actions, "Cerrar", self.destroy,
                     width=88).pack(side="left")

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _values(self) -> Optional[dict]:
        values = {}
        for name, _label in self.FIELDS:
            value = parse_eng(self.vars[name].get(), None)
            if value is None:
                messagebox.showerror("Valor inválido",
                                     "Todos los márgenes deben ser números "
                                     "entre 0 y 1.", parent=self)
                return None
            values[name] = min(1.0, max(0.0, value))
        # Matplotlib rejects a left margin at or past the right one (and the
        # same vertically); catching it here gives a readable message instead
        # of a traceback from deep inside the layout engine.
        if values["left"] >= values["right"] or values["bottom"] >= values["top"]:
            messagebox.showerror(
                "Márgenes inconsistentes",
                "El margen izquierdo debe ser menor que el derecho, y el "
                "inferior menor que el superior.", parent=self)
            return None
        return values

    def _apply(self) -> None:
        values = self._values()
        if values is None:
            return
        for name, value in values.items():
            self.vars[name].set(f"{value:.3f}")
        try:
            self.fig.subplots_adjust(**values)
        except (ValueError, AttributeError) as exc:
            messagebox.showerror("Error", str(exc), parent=self)
            return
        self.fig.canvas.draw_idle()

    def _preset(self, name: str) -> None:
        for key, value in self.PRESETS[name].items():
            self.vars[key].set(f"{value:.3f}")
        self._apply()

    def _reset(self) -> None:
        for key, value in self.DEFAULTS.items():
            self.vars[key].set(f"{value:.3f}")
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

        # ONE scroll region for both groups. Two stacked CTkScrollableFrames
        # is the same pattern that had to be removed from the main window: each
        # is a canvas listening to <Configure> to recompute its scrollregion,
        # and two of them competing for vertical space drives a
        # configure -> resize -> configure loop that locks the UI thread.
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent",
                                        corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        ctk.CTkLabel(scroll, text="Columna de eje X:", anchor="w"
                     ).pack(fill="x", anchor="w")
        self.x_var = ctk.StringVar(value=default_x)
        x_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        x_frame.pack(fill="x", pady=(2, 8))
        for col in columns:
            ctk.CTkRadioButton(
                x_frame, text=f"{col}  ({kind_label.get(col_kind.get(col, ''), '—')})",
                variable=self.x_var, value=col).pack(anchor="w", pady=1)

        ctk.CTkLabel(scroll, text="Columnas de valor (una señal por columna marcada):",
                     anchor="w").pack(fill="x", anchor="w", pady=(6, 2))

        y_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        y_frame.pack(fill="x", pady=(0, 6))

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
        self.title("LabPlotter")
        self.geometry("1480x880")
        # Both panels are now user-resizable (see `_drag_left`/`_drag_right`),
        # and the whole interface scales proportionally with window width
        # (see `_on_root_configure`), so the floor no longer has to fit the
        # old fixed panel widths -- just enough for a usable three-column layout.
        self.minsize(900, 600)

        # NOTE: appearance mode / color theme are no longer set here.
        # `apply_theme()` installs the palette and the appearance mode
        # BEFORE this window is constructed (see `main()`
        # at the bottom of this file) -- CustomTkinter reads the theme
        # dictionary at widget-creation time, so it must run first.

        # ------------------------- Application state ------------------------- #
        self.signals: dict[str, Signal] = {}
        self.signal_order: list[str] = []
        self.selected_uid: Optional[str] = None
        self.row_widgets: dict[str, dict] = {}
        self._color_index = 0
        self._axis_labels_dirty = False   # True once the user edits axis labels
        self.overlay_window = None        # floating cursor/annotation palette
        self._shortcuts_window = None

        # (x_col, y_col) used to build each loaded Signal, keyed by uid.
        # Only used to replay `read_table` + `build_signal` when restoring a
        # saved session; signals added any other way simply aren't persisted.
        self._signal_columns: dict[str, tuple[str, str]] = {}
        self._export_profiles: dict[str, dict] = {}

        # Panel widths and layout state, all mutable at runtime via the
        # draggable splitters and the compact-mode toggle.
        self._left_width = 300
        self._right_width = 320
        self._compact = False

        # Proportional type scale, driven by window width and debounced in
        # `_on_root_configure`. Only shared font objects are resized -- the
        # widget tree itself is never rescaled, so the OS display-scaling
        # factor is left exactly as the system reports it.
        self._scale_job: Optional[str] = None
        self._last_width: Optional[int] = None
        self._current_scale = 1.0
        # Set while a batch of settings is applied at once (session restore),
        # so the figure is rendered once at the end instead of after each
        # individual field.
        self._plot_suspended = False

        self.decimal_comma_var = ctk.BooleanVar(value=False)
        # Default font matches a typical LaTeX report (lmodern / Computer
        # Modern), so exported figures blend in with the document out of
        # the box. Selectable in the GUI like any other font.
        self.font_family_var = ctk.StringVar(value="LaTeX (Computer Modern)")

        set_publication_style(font_family=self.font_family_var.get())

        self._build_layout()
        self._enable_drag_and_drop()   # needs self.canvas and both panels
        self.bind("<Configure>", self._on_root_configure)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if not self._restore_session_if_any():
            self.update_plot()

    # ------------------------------------------------------------------ #
    # Layout construction
    # ------------------------------------------------------------------ #
    def _build_layout(self) -> None:
        # Column layout: [left panel] [splitter] [center, weight=1] [splitter] [right panel]
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_topbar()
        self._build_left_panel()
        self.left_splitter = Splitter(self, on_drag=self._drag_left,
                                      on_release=self._save_session_soon)
        self.left_splitter.grid(row=1, column=1, sticky="ns")
        self._build_center_panel()
        self.right_splitter = Splitter(self, on_drag=self._drag_right,
                                       on_release=self._save_session_soon)
        self.right_splitter.grid(row=1, column=3, sticky="ns")
        self._build_right_panel()
        # Global keyboard shortcuts, skipped inside text entries by their
        # own handlers where relevant.
        self.bind_all("<Delete>", self._on_delete_key)
        self.bind_all("<BackSpace>", self._on_delete_key)
        self.bind_all("<Control-o>", lambda _e: self._load_files())
        self.bind_all("<Control-e>", lambda _e: self._export_figure())
        self.bind_all("<Control-s>", lambda _e: self._export_csv())
        self.bind_all("<F1>", lambda _e: self._open_shortcuts())

    # ------------------------------------------------------------------ #
    # Resizable panels: drag handles, compact mode, proportional scaling
    # ------------------------------------------------------------------ #
    def _drag_left(self, delta: int) -> None:
        self._left_width = max(_LEFT_MIN, min(_LEFT_MAX, self._left_width + delta))
        self.left_panel.configure(width=self._left_width)

    def _drag_right(self, delta: int) -> None:
        self._right_width = max(_RIGHT_MIN, min(_RIGHT_MAX, self._right_width - delta))
        self.right_panel.configure(width=self._right_width)

    def _toggle_compact(self) -> None:
        self._set_compact(not self._compact)

    def _set_compact(self, active: bool) -> None:
        """Hide both side panels so the plot takes the full window width."""
        self._compact = bool(active)
        for widget in (self.left_panel, self.left_splitter,
                       self.right_splitter, self.right_panel):
            widget.grid_remove() if self._compact else widget.grid()
        self.compact_button.set_active(self._compact)

    def _on_root_configure(self, event) -> None:
        """
        Debounced entry point for the proportional type scale.

        Only the root window's own resize events matter; child widgets fire
        `<Configure>` constantly and must be ignored, and a window *move*
        reports the same width, which is filtered out below.
        """
        if event.widget is not self:
            return
        width = event.width
        if width == self._last_width or width <= 1:
            return
        self._last_width = width
        if self._scale_job is not None:
            try:
                self.after_cancel(self._scale_job)
            except Exception:
                pass
        # Coalesced: this fires once the drag settles, not per pixel. Only
        # shared font objects are touched (see theme.set_font_scale), so the
        # cost is a text relayout rather than a full widget reconfiguration.
        self._scale_job = self.after(120, lambda w=width: self._apply_font_scale(w))

    def _apply_font_scale(self, width: int) -> None:
        self._scale_job = None
        if set_font_scale(width / _REFERENCE_WIDTH):
            self._current_scale = font_scale()

    # ------------------------------------------------------------------ #
    # Drag and drop: open files by dropping them on the window
    # ------------------------------------------------------------------ #
    def _enable_drag_and_drop(self) -> None:
        """
        Register the window as a drop target for .csv/.txt files.

        tkinterdnd2 patches `drop_target_register` and `dnd_bind` onto
        `tkinter.BaseWidget` when it is imported, and exposes `require()`
        specifically for frameworks that own their own Tk root -- its
        docstring names CustomTkinter. An earlier version here hand-grafted
        the two methods onto this instance instead, which was both unnecessary
        and easy to get subtly wrong.

        Targets are registered on the root *and* on the main surfaces: tkdnd
        dispatches to the registered widget under the pointer, so covering the
        canvas and both panels means a drop lands wherever it is released.

        Failure is never fatal -- dragging is a shortcut for the "+ Abrir
        archivo" button, not a requirement -- but it is recorded in
        `self._dnd_status` and surfaced in the help window (F1) so a missing
        dependency is visible instead of silently doing nothing.
        """
        self._dnd_enabled = False
        self._dnd_status = "no disponible"

        if TkinterDnD is None:
            self._dnd_status = ("requiere  pip install tkinterdnd2  "
                                "(no está instalado)")
            return
        try:
            # `require` is the supported entry point; `_require` is the older
            # private name, kept as a fallback for earlier releases.
            register = getattr(TkinterDnD, "require", None) or TkinterDnD._require
            self.TkdndVersion = register(self)
        except Exception as exc:
            self._dnd_status = f"la librería tkdnd no cargó ({exc})"
            return

        targets = [self]
        for widget in (getattr(self, "canvas", None), self.left_panel,
                       self.right_panel):
            if widget is None:
                continue
            targets.append(widget.get_tk_widget()
                           if hasattr(widget, "get_tk_widget") else widget)

        registered = 0
        for target in targets:
            try:
                target.drop_target_register(DND_FILES)
                target.dnd_bind("<<Drop>>", self._on_files_dropped)
                target.dnd_bind("<<DropEnter>>", self._on_drop_enter)
                target.dnd_bind("<<DropLeave>>", self._on_drop_leave)
                registered += 1
            except Exception:
                continue   # one uncooperative widget must not disable the rest

        if registered:
            self._dnd_enabled = True
            self._dnd_status = "activo"
        else:
            self._dnd_status = "ningún widget aceptó registrarse como destino"

    def _on_drop_enter(self, _event) -> None:
        self.status_label.configure(text="Soltá para cargar el/los archivo(s)...")

    def _on_drop_leave(self, _event) -> None:
        self.status_label.configure(text="")

    def _on_files_dropped(self, event) -> None:
        self.status_label.configure(text="")
        data = getattr(event, "data", "") or ""
        try:
            # Tcl's own list parsing handles the `{...}` quoting tkdnd puts
            # around paths containing spaces -- and a Windows user's paths
            # contain spaces constantly. A naive split() breaks on those.
            raw_paths = [str(p) for p in self.tk.splitlist(data)]
        except Exception:
            raw_paths = [data] if data else []

        paths, rejected = [], []
        for path in raw_paths:
            path = path.strip().strip("{}")
            if not path:
                continue
            if os.path.isdir(path):
                # Dropping a folder loads the data files inside it, one level
                # deep; recursing would surprise more than it would help.
                for name in sorted(os.listdir(path)):
                    candidate = os.path.join(path, name)
                    if (os.path.isfile(candidate)
                            and os.path.splitext(name)[1].lower() in (".csv", ".txt")):
                        paths.append(candidate)
                continue
            if os.path.splitext(path)[1].lower() in (".csv", ".txt"):
                paths.append(path)
            else:
                rejected.append(os.path.basename(path))

        if not paths:
            detail = (f"\n\nIgnorado: {', '.join(rejected[:5])}"
                      if rejected else "")
            messagebox.showinfo(
                "Sin archivos válidos",
                f"Soltá archivos .csv o .txt para cargarlos.{detail}")
            return
        self._ingest_files(paths)

    def _open_shortcuts(self) -> None:
        if self._shortcuts_window is not None and self._shortcuts_window.winfo_exists():
            self._shortcuts_window.lift()
            self._shortcuts_window.focus()
            return
        groups = [
            ("General", [
                ("Ctrl+O", "Abrir archivo(s)"),
                ("Ctrl+E", "Exportar figura (y obtener el bloque LaTeX)"),
                ("Ctrl+S", "Exportar CSV para PGFPlots"),
                ("Supr / Backspace", "Quitar la traza seleccionada"),
                ("Enter", "Aplicar el campo activo"),
                ("F1", "Mostrar esta ventana"),
            ]),
            ("Gráfico", [
                ("Cursor + clic", "Colocar un cursor de medición"),
                ("Arrastre", "Mover un cursor, o zoom/paneo según la herramienta activa"),
                ("Anotar + clic", "Capturar coordenadas para una anotación"),
            ]),
            ("Campos numéricos", [
                ("2.2k", "2200"),
                ("4u7", "4,7 µ  (notación R: el prefijo hace de coma)"),
                ("470p / 10M", "prefijos T G M k m u n p f"),
                ("10 kHz", "la unidad al final se ignora"),
            ]),
            ("Arrastrar y soltar", [
                (self._dnd_status, "Soltá archivos .csv o .txt sobre la ventana"),
            ]),
            ("Ventana", [
                ("Arrastrar el borde", "Redimensionar los paneles laterales a mano"),
                ("Modo compacto", "Ocultar los paneles y usar todo el ancho para el gráfico"),
                ("Redimensionar ventana", "La tipografía y los controles escalan proporcionalmente"),
            ]),
        ]
        self._shortcuts_window = ShortcutsWindow(self, groups)

    # ------------------------------------------------------------------ #
    # Session persistence ("recordar el último proyecto")
    # ------------------------------------------------------------------ #
    def _persisted_vars(self) -> dict[str, "ctk.Variable"]:
        """Every StringVar/BooleanVar whose value should survive a restart."""
        return {
            "unit_x": self.unit_x_var, "unit_y": self.unit_y_var,
            "xscale": self.xscale_var, "yscale": self.yscale_var,
            "xmin": self.xmin_var, "xmax": self.xmax_var,
            "engineering_ticks": self.engineering_ticks_var,
            "grid": self.grid_var, "minor_grid": self.minor_grid_var,
            "title": self.title_var, "xlabel": self.xlabel_var,
            "ylabel": self.ylabel_var, "ylabel2": self.ylabel2_var,
            "font_family": self.font_family_var, "theme_mode": self.theme_mode_var,
            "legend": self.legend_var, "legend_pos": self.legend_pos_var,
            "legend_x": self.legend_x_var, "legend_y": self.legend_y_var,
            "legend_corner": self.legend_corner_var, "legend_ncol": self.legend_ncol_var,
            "dec_mode": self.dec_mode_var, "dec_value": self.dec_value_var,
            "decimal_comma": self.decimal_comma_var,
            "plot_mode": self.plot_mode_var, "bode_layout": self.bode_layout_var,
            "fig_format": self.fig_format_var, "dpi": self.dpi_var,
            "csv_mode": self.csv_mode_var,
            "xy_x": self.xy_x_var, "xy_y": self.xy_y_var,
            "xy_legend": self.xy_legend_var, "xy_color": self.xy_color_var,
        }

    def _gather_state(self) -> dict:
        settings = {}
        for key, var in self._persisted_vars().items():
            try:
                settings[key] = var.get()
            except Exception:
                pass   # a widget torn down mid-close: skip that one field

        signals = []
        for uid in self.signal_order:
            cols = self._signal_columns.get(uid)
            sig = self.signals.get(uid)
            if cols is None or sig is None:
                continue   # no known source columns: nothing to replay later
            signals.append({
                "source_path": sig.source_path, "x_col": cols[0], "y_col": cols[1],
                "name": sig.name, "legend_label": sig.legend_label,
                "domain": sig.domain, "y_kind": sig.y_kind,
                "unit_t_in": sig.unit_t_in, "unit_v_in": sig.unit_v_in,
                "t_offset": sig.t_offset, "v_offset": sig.v_offset,
                "gain": sig.gain, "invert": sig.invert, "linestyle": sig.linestyle,
                "color": sig.color, "secondary_y": sig.secondary_y,
                "visible": sig.visible,
            })

        return {
            "geometry": self.geometry(),
            "left_width": self._left_width, "right_width": self._right_width,
            "compact": self._compact,
            "settings": settings,
            "signals": signals,
        }

    def _restore_signal(self, record: dict) -> bool:
        path = record.get("source_path")
        x_col, y_col = record.get("x_col"), record.get("y_col")
        if not path or not x_col or not y_col or not os.path.isfile(path):
            return False
        try:
            df, _col_kind = read_table(path, decimal_comma=self.decimal_comma_var.get())
            sig = build_signal(df, x_col, y_col, record.get("name") or "señal", path,
                               domain=record.get("domain", "time"),
                               y_kind=record.get("y_kind", "voltage"),
                               color=record.get("color") or self._next_color())
        except Exception:
            return False   # file moved/changed/corrupted: skip, don't crash

        for attr in ("unit_t_in", "unit_v_in", "t_offset", "v_offset", "gain",
                    "invert", "linestyle", "secondary_y"):
            if attr in record:
                setattr(sig, attr, record[attr])
        sig.legend_label = record.get("legend_label")
        sig.visible = record.get("visible", True)

        self.signals[sig.uid] = sig
        self.signal_order.append(sig.uid)
        self._signal_columns[sig.uid] = (x_col, y_col)
        return True

    def _apply_state(self, data: dict) -> None:
        settings = data.get("settings", {}) or {}
        for key, var in self._persisted_vars().items():
            if key in settings:
                try:
                    var.set(settings[key])
                except Exception:
                    pass

        geometry = data.get("geometry")
        if geometry:
            try:
                self.geometry(geometry)
            except Exception:
                pass

        self._left_width = max(_LEFT_MIN, min(_LEFT_MAX,
                               int(data.get("left_width", self._left_width))))
        self._right_width = max(_RIGHT_MIN, min(_RIGHT_MAX,
                                int(data.get("right_width", self._right_width))))
        self.left_panel.configure(width=self._left_width)
        self.right_panel.configure(width=self._right_width)
        if data.get("compact"):
            self._set_compact(True)

        restored = sum(self._restore_signal(r) for r in data.get("signals", []))
        if restored:
            self._sync_unit_options()
            self._refresh_signal_list()
            self._refresh_xy_combos()

        # These three have effects beyond their own variable (rcParams,
        # appearance mode, contextual-row visibility), so re-trigger them
        # explicitly instead of relying on the trace-based widgets alone.
        self._plot_suspended = True
        try:
            if "theme_mode" in settings:
                self._on_theme_change(settings["theme_mode"])
            if "font_family" in settings:
                self._on_font_change()
            self._on_mode_change()
        finally:
            self._plot_suspended = False
        self.update_plot()   # single render for the whole restored state

    def _restore_session_if_any(self) -> bool:
        data = session.load_session()
        if not data:
            return False
        try:
            self._apply_state(data)
        except Exception:
            return False
        return True

    def _save_session_soon(self) -> None:
        """Persist panel widths right after a drag, not just on close."""
        try:
            session.save_session(self._gather_state())
        except Exception:
            pass

    def _on_close(self) -> None:
        try:
            session.save_session(self._gather_state())
        except Exception:
            pass
        self.destroy()

    def _build_topbar(self) -> None:
        """
        Wordmark, the list of loaded sources, and the one action that starts
        every session. Everything else lives in a panel, so the bar never
        becomes a second toolbar.
        """
        bar = ctk.CTkFrame(self, height=46, corner_radius=0, fg_color=col("bar"))
        bar.grid(row=0, column=0, columnspan=5, sticky="ew")
        # pack_propagate, not grid_propagate: this frame's children are packed,
        # and grid_propagate only governs grid-managed children -- so the
        # requested height above was being ignored entirely.
        bar.pack_propagate(False)
        Rule(bar, strong=True).pack(side="bottom", fill="x")

        content = ctk.CTkFrame(bar, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=18)

        ctk.CTkLabel(content, text=spaced("LabPlotter"), font=font("title"),
                     text_color=col("fg")).pack(side="left", pady=11)
        VRule(content, height=20).pack(side="left", padx=16, pady=13)

# An empty `ctk.CTkFrame` keeps its constructor default of 200x200 px: with no
# children there is nothing for geometry propagation to shrink it to. Every
# container below can legitimately be empty (no files loaded, no traces, a mode
# with no extra controls), so each one is created at 1x1 and grows from its
# children instead. This is what produced the large blank bands under the top
# bar, above the plot and inside the trace list on a fresh launch.
        self.chip_bar = ctk.CTkFrame(content, fg_color="transparent",
                                     width=1, height=1)
        self.chip_bar.pack(side="left", pady=9)

        primary_button(content, "+  Abrir archivo", self._load_files,
                       height=28, width=140).pack(side="left", padx=10, pady=9)

    def _refresh_chips(self) -> None:
        """One chip per source file: extension tag, name and sample count."""
        for child in self.chip_bar.winfo_children():
            child.destroy()

        totals: dict[str, int] = {}
        for uid in self.signal_order:
            sig = self.signals[uid]
            key = sig.source_path or sig.name
            totals[key] = totals.get(key, 0) + int(sig.t_raw.size)

        items = list(totals.items())
        for index, (path, points) in enumerate(items[:4]):
            name = os.path.basename(path) or path
            tag = os.path.splitext(name)[1].lstrip(".") or "dat"
            Chip(self.chip_bar, tag, name, f"{points:,}".replace(",", "\u2009")
                 ).pack(side="left", padx=(0 if index == 0 else 6, 0))
        if len(items) > 4:
            hint(self.chip_bar, f"+{len(items) - 4} más").pack(side="left", padx=8)

    def _build_left_panel(self) -> None:
        left = ctk.CTkFrame(self, width=self._left_width, corner_radius=0,
                            fg_color=col("panel"))
        left.grid(row=1, column=0, sticky="ns")
        left.pack_propagate(False)   # children are packed; see _build_topbar
        self.left_panel = left

        # Buttons are packed to the bottom edge FIRST, before the expanding
        # scroll region below, so they can never be pushed off-window.
        footer = ctk.CTkFrame(left, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=16, pady=12)
        ghost_button(footer, "Quitar", self._remove_selected_signal,
                     width=90).pack(side="left")
        ghost_button(footer, "Quitar todas", self._remove_all_signals,
                     width=118).pack(side="left", padx=6)

        # ONE scroll region for the whole column. This previously held two
        # separate CTkScrollableFrames stacked inside a fixed-width parent
        # with grid_propagate(False). Each CTkScrollableFrame is a canvas
        # that listens to <Configure> to recompute its scrollregion and
        # resize its inner frame; two of them competing for vertical space
        # inside a container whose own size is pinned drove a
        # configure -> resize -> configure feedback loop that pegged the UI
        # thread. It was worst with no file loaded, because the empty
        # parameter frame's requested height oscillated -- which is exactly
        # when the window appeared frozen on startup.
        scroll = ctk.CTkScrollableFrame(left, fg_color="transparent",
                                        corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=(16, 8), pady=(14, 0))

        SectionHeader(scroll, "Trazas").pack(fill="x")
        Rule(scroll).pack(fill="x", pady=(6, 4))

        # Plain containers now, not scroll regions of their own.
        self.signal_list_frame = ctk.CTkFrame(scroll, fg_color="transparent",
                                              width=1, height=1)
        self.signal_list_frame.pack(fill="x")

        Rule(scroll).pack(fill="x", pady=(6, 0))

        # Contextual: the per-trace controls only exist while a trace is
        # selected, which is what keeps this column from becoming a wall.
        self.param_frame = ctk.CTkFrame(scroll, fg_color="transparent",
                                        width=1, height=1)
        self.param_frame.pack(fill="x", pady=(10, 0))
        self._build_param_placeholder()
        # Render the "no traces yet" state now, so the list is never an empty
        # container on a fresh launch.
        self._refresh_signal_list()

    def _build_param_placeholder(self) -> None:
        for w in self.param_frame.winfo_children():
            w.destroy()
        hint(self.param_frame, "Seleccioná una traza de la lista para ver "
                               "sus ajustes.", wraplength=250).pack(fill="x", pady=20)

    def _build_center_panel(self) -> None:
        center = ctk.CTkFrame(self, corner_radius=0, fg_color=col("app"))
        center.grid(row=1, column=2, sticky="nsew")
        center.grid_rowconfigure(2, weight=1)
        center.grid_columnconfigure(0, weight=1)

        # ------------------------- tool strip ------------------------- #
        strip = ctk.CTkFrame(center, height=44, corner_radius=0, fg_color=col("bar"))
        strip.grid(row=0, column=0, sticky="ew")
        strip.pack_propagate(False)   # children are packed; see _build_topbar
        Rule(strip).pack(side="bottom", fill="x")

        tools = ctk.CTkFrame(strip, fg_color="transparent")
        tools.pack(fill="both", expand=True, padx=16)

        self.plot_mode_var = ctk.StringVar(value=PLOT_MODES[0])
        Segmented(tools, PLOT_MODES, self.plot_mode_var, labels=MODE_LABELS,
                  command=lambda _v: self._on_mode_change(), width=76
                  ).pack(side="left", pady=9)
        VRule(tools, height=20).pack(side="left", padx=14, pady=12)

        self._active_tool: Optional[str] = None
        self.tool_buttons: dict[str, ToolButton] = {}
        for key, label in (("cursor", "Cursor"), ("annotate", "Anotar"),
                           ("zoom", "Zoom"), ("pan", "Mover")):
            button = ToolButton(tools, label, width=72,
                                command=lambda k=key: self._select_tool(k))
            button.pack(side="left", padx=(0, 5), pady=9)
            self.tool_buttons[key] = button
        ghost_button(tools, "Ajustar a los datos", self._fit_to_data,
                     width=142).pack(side="left", padx=(8, 0), pady=9)

        self.compact_button = ToolButton(tools, "Compacto", width=88,
                                         command=self._toggle_compact)
        self.compact_button.pack(side="left", padx=(6, 0), pady=9)

        help_button = ghost_button(tools, "?", self._open_shortcuts, width=28)
        help_button.pack(side="right", pady=9)

        self.status_label = ctk.CTkLabel(tools, text="", font=font("hint"),
                                         text_color=col("fg_faint"))
        self.status_label.pack(side="right", padx=(0, 12), pady=9)
        self.tool_hint = hint(tools, "")
        self.tool_hint.pack(side="right", padx=16, pady=9)

        # ---------------- mode-specific contextual row ---------------- #
        # Both live directly in row 1 of `center` and only one is ever shown.
        # They used to sit inside a wrapper frame, which stayed on screen even
        # when both children were hidden -- and an empty CTkFrame holds a
        # 200x200 request, so it reserved a blank band above the plot in the
        # default (Tiempo) mode, which shows neither of them.
        center.grid_rowconfigure(1, weight=0)

        self.xy_frame = ctk.CTkFrame(center, corner_radius=0, fg_color=col("bar"),
                                     height=1)
        self.xy_frame.grid(row=1, column=0, sticky="ew")
        xy_inner = ctk.CTkFrame(self.xy_frame, fg_color="transparent", height=1)
        xy_inner.pack(fill="x", padx=16, pady=8)
        self._build_xy_controls(xy_inner)
        self.xy_frame.grid_remove()

        self.bode_frame = ctk.CTkFrame(center, corner_radius=0, fg_color=col("bar"),
                                       height=1)
        self.bode_frame.grid(row=1, column=0, sticky="ew")
        bode_inner = ctk.CTkFrame(self.bode_frame, fg_color="transparent", height=1)
        bode_inner.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(bode_inner, text="Disposición", font=font("label"),
                     text_color=col("fg_muted")).pack(side="left", padx=(0, 10))
        self.bode_layout_var = ctk.StringVar(value=BODE_LAYOUTS[0])
        Segmented(bode_inner, BODE_LAYOUTS, self.bode_layout_var,
                  labels=BODE_LABELS, width=94,
                  command=lambda _v: self.update_plot()).pack(side="left")
        self.bode_frame.grid_remove()

        # ---------------------------- canvas -------------------------- #
        plot_container = ctk.CTkFrame(center, corner_radius=0, fg_color=col("app"))
        plot_container.grid(row=2, column=0, sticky="nsew", padx=20, pady=18)

        self.fig = Figure(figsize=(7.6, 5.2), dpi=100)
        self.axes: list = [self.fig.add_subplot(111)]
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_container)
        canvas_widget = self.canvas.get_tk_widget()
        canvas_widget.configure(borderwidth=0, highlightthickness=1,
                                highlightbackground=tk_color("border_str"),
                                highlightcolor=tk_color("border_str"))
        canvas_widget.pack(fill="both", expand=True)

        # The stock Matplotlib toolbar is created but never displayed: the
        # tool strip above drives pan/zoom/margins through it, so the window
        # keeps a single visual language instead of two.
        self._toolbar_host = tk.Frame(plot_container)
        self.mpl_toolbar = EditableNavigationToolbar(self.canvas, self._toolbar_host)
        self.mpl_toolbar.update()

        self.measurements = MeasurementsCard(plot_container, width=230)
        self.measurements.bind_close(self._hide_measurements)
        self._measurements_visible = False

        # Overlay layer. Both managers keep plain specs, so they survive the
        # fig.clear() performed on every re-plot (see `update_plot`).
        self.cursors = CursorManager(self.canvas, on_change=self._on_cursor_change,
                                     max_cursors=None)
        self.annotations = AnnotationManager(self.canvas)
        self.cursors.attach(self.axes)
        self.annotations.attach(self.axes)

    def _build_xy_controls(self, parent) -> None:
        ctk.CTkLabel(parent, text="Eje X", font=font("label"),
                     text_color=col("fg_muted")).pack(side="left", padx=(0, 8))
        self.xy_x_var = ctk.StringVar(value="")
        self.xy_x_combo = ctk.CTkComboBox(parent, values=[""], variable=self.xy_x_var,
                                           width=160, height=26, font=font("body"),
                                           dropdown_font=font("body"))
        self.xy_x_combo.pack(side="left")

        ctk.CTkLabel(parent, text="Eje Y", font=font("label"),
                     text_color=col("fg_muted")).pack(side="left", padx=(16, 8))
        self.xy_y_var = ctk.StringVar(value="")
        self.xy_y_combo = ctk.CTkComboBox(parent, values=[""], variable=self.xy_y_var,
                                           width=160, height=26, font=font("body"),
                                           dropdown_font=font("body"))
        self.xy_y_combo.pack(side="left")

        ctk.CTkLabel(parent, text="Leyenda", font=font("label"),
                     text_color=col("fg_muted")).pack(side="left", padx=(16, 8))
        self.xy_legend_var = ctk.StringVar(value="")
        legend_entry = ctk.CTkEntry(parent, textvariable=self.xy_legend_var,
                                     width=160, height=26, font=font("body"))
        legend_entry.pack(side="left")
        legend_entry.bind("<Return>", lambda _e: self.update_plot())

        # The X/Y trace is synthesised from two channels, so it gets its own
        # colour instead of silently inheriting one of them.
        self.xy_color_var = ctk.StringVar(value=TRACE_CYCLE[3])
        self.xy_color_preview = ctk.CTkButton(
            parent, text="", width=22, height=22, corner_radius=0, border_width=1,
            border_color=col("border_str"), fg_color=self.xy_color_var.get(),
            hover_color=self.xy_color_var.get(), command=self._pick_xy_color)
        self.xy_color_preview.pack(side="left", padx=(16, 0))

        def _sync_preview(*_):
            value = self.xy_color_var.get().strip()
            try:
                self.xy_color_preview.configure(fg_color=value, hover_color=value)
            except (tk.TclError, ValueError):
                pass   # invalid hex while typing: keep the last valid preview

        self.xy_color_var.trace_add("write", _sync_preview)

    def _pick_xy_color(self) -> None:
        initial = self.xy_color_var.get().strip() or TRACE_CYCLE[3]
        try:
            _rgb, hex_color = colorchooser.askcolor(color=initial, parent=self)
        except tk.TclError:
            _rgb, hex_color = colorchooser.askcolor(parent=self)
        if hex_color:
            self.xy_color_var.set(hex_color)
            self.update_plot()

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    def _select_tool(self, key: str) -> None:
        """Clicking the active tool releases it; tools are mutually exclusive."""
        self._set_tool(None if self._active_tool == key else key)

    def _set_tool(self, key: Optional[str]) -> None:
        previous = self._active_tool
        if previous in ("zoom", "pan") and previous != key:
            try:
                getattr(self.mpl_toolbar, previous)()   # toggles that mode off
            except Exception:
                pass
        if previous == "cursor":
            self.cursors.disarm()
        if previous == "annotate":
            self.annotations.disarm()

        self._active_tool = key
        for name, button in self.tool_buttons.items():
            button.set_active(name == key)

        if key == "zoom":
            self.mpl_toolbar.zoom()
            self.tool_hint.configure(text="Arrastrá sobre el gráfico para acercar.")
        elif key == "pan":
            self.mpl_toolbar.pan()
            self.tool_hint.configure(text="Arrastrá para desplazar el gráfico.")
        elif key == "cursor":
            self.annotations.disarm()
            self.cursors.arm("v")
            self._show_measurements()
            self.tool_hint.configure(
                text="Clic sobre el gráfico para colocar un cursor; arrastralo para medir.")
        elif key == "annotate":
            self.cursors.disarm()
            self._open_overlay_window(tab="Anotaciones")
            self.tool_hint.configure(
                text="Definí la anotación en el panel y capturá el punto.")
        else:
            self.tool_hint.configure(text="")

    def _fit_to_data(self) -> None:
        """Rescale every axes to the data currently plotted."""
        for ax in self.fig.axes:
            try:
                ax.relim(visible_only=True)
                ax.autoscale_view()
            except (TypeError, ValueError):
                ax.autoscale_view()
        self.canvas.draw_idle()

    # ------------------------------------------------------------------ #
    # Measurements card
    # ------------------------------------------------------------------ #
    def _show_measurements(self) -> None:
        if self._measurements_visible:
            self._on_cursor_change()
            return
        self._measurements_visible = True
        self.measurements.place(relx=1.0, rely=0.0, anchor="ne", x=-16, y=16)
        self._on_cursor_change()

    def _hide_measurements(self) -> None:
        self._measurements_visible = False
        self.measurements.place_forget()

    def _measurement_rows(self) -> list[tuple[str, str]]:
        x_unit, y_unit = self.cursors.x_unit, self.cursors.y_unit
        rows: list[tuple[str, str]] = []
        for entry in self.cursors.readout():
            axis = "X" if entry["orientation"] == "v" else "Y"
            unit = x_unit if entry["orientation"] == "v" else y_unit
            rows.append((f"{entry['name']}  ·  {axis}",
                         format_eng(entry["position"], unit)))
            for item in entry["values"][:4]:
                label = (item["label"] or "").replace("$", "").replace("\\", "")
                if entry["orientation"] == "v":
                    rows.append((f"   {label[:16]}", format_eng(item["value"], y_unit)))
                else:
                    crossings = item.get("crossings") or []
                    text = ", ".join(format_eng(c, x_unit) for c in crossings[:2])
                    rows.append((f"   {label[:16]}", text or "sin cruce"))
        deltas = self.cursors.deltas()
        if deltas:
            rows.append(("--", ""))
            for item in deltas:
                unit = x_unit if item["orientation"] == "v" else y_unit
                rows.append((f"Δ {item['from']}→{item['to']}",
                             format_eng(item["delta"], unit)))
                if item["orientation"] == "v" and item["inverse"] is not None:
                    rows.append(("   1/Δ", format_eng(item["inverse"])))
        return rows[:16]

    def _on_cursor_change(self) -> None:
        if self._measurements_visible:
            self.measurements.set_rows(self._measurement_rows())
        window = self.overlay_window
        if window is not None and window.winfo_exists():
            window.panel.refresh_cursor_ui()

    # ------------------------------------------------------------------ #
    # Right panel: one accordion, one section open at a time
    # ------------------------------------------------------------------ #
    def _build_right_panel(self) -> None:
        right = ctk.CTkFrame(self, width=self._right_width, corner_radius=0,
                             fg_color=col("panel"))
        right.grid(row=1, column=4, sticky="ns")
        right.pack_propagate(False)   # children are packed; see _build_topbar
        self.right_panel = right

        body = ctk.CTkScrollableFrame(right, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True, padx=16, pady=(14, 12))

        SectionHeader(body, "Ajustes").pack(fill="x")
        Rule(body, strong=True).pack(fill="x", pady=(6, 12))
        self._build_axes_section(body)
        self._build_labels_section(body)
        self._build_legend_section(body)
        self._build_data_section(body)
        self._build_export_section(body)

    def _section(self, parent, title: str, expanded: bool = True):
        """
        One settings group, always visible.

        `expanded` is accepted and ignored: it is left in the signature so the
        call sites read the same, but collapsing was removed -- see
        `widgets.StaticSection` for why.
        """
        section = StaticSection(parent, title)
        section.pack(fill="x", pady=(0, 12))
        return section.body

    def _build_axes_section(self, parent) -> None:
        box = self._section(parent, "Ejes y escalas", expanded=True)

        self.unit_x_var = ctk.StringVar(value="us")
        self.unit_x_combo = combo_field(box, "Unidad X", self.unit_x_var,
                                         ["s", "ms", "us", "ns"], width=110)
        self.unit_y_var = ctk.StringVar(value="V")
        self.unit_y_combo = combo_field(box, "Unidad Y", self.unit_y_var,
                                         ["V", "mV"], width=110)

        self.xscale_var = ctk.StringVar(value="linear")
        segmented_field(box, "Escala X", ["linear", "log"], self.xscale_var,
                        labels=SCALE_LABELS, command=lambda _v: self.update_plot())
        self.yscale_var = ctk.StringVar(value="linear")
        self.yscale_seg = segmented_field(box, "Escala Y", ["linear", "log"],
                                           self.yscale_var, labels=SCALE_LABELS,
                                           command=lambda _v: self.update_plot())

        self.xmin_var = ctk.StringVar(value="")
        entry_field(box, "X mín", self.xmin_var, on_enter=self.update_plot)
        self.xmax_var = ctk.StringVar(value="")
        entry_field(box, "X máx", self.xmax_var, on_enter=self.update_plot)
        hint(box, "Vacío = sin límite, en la unidad X elegida.",
             wraplength=280).pack(fill="x", pady=(0, 10))

        self.engineering_ticks_var = ctk.BooleanVar(value=True)
        check_field(box, "Notación de ingeniería", self.engineering_ticks_var,
                    command=self.update_plot)
        self.grid_var = ctk.BooleanVar(value=True)
        check_field(box, "Grilla", self.grid_var, command=self.update_plot)
        self.minor_grid_var = ctk.BooleanVar(value=False)
        check_field(box, "Grilla menor", self.minor_grid_var,
                    command=self.update_plot, rule=False)

    def _build_labels_section(self, parent) -> None:
        box = self._section(parent, "Rótulos y tipografía", expanded=False)

        self.title_var = ctk.StringVar(value="")
        stacked_entry(box, "Título", self.title_var, on_enter=self.update_plot)
        self.xlabel_var = ctk.StringVar(value="")
        entry = stacked_entry(box, "Etiqueta X", self.xlabel_var,
                              on_enter=self.update_plot)
        entry.bind("<KeyRelease>", lambda _e: self._mark_labels_dirty())
        self.ylabel_var = ctk.StringVar(value="")
        entry = stacked_entry(box, "Etiqueta Y", self.ylabel_var,
                              on_enter=self.update_plot)
        entry.bind("<KeyRelease>", lambda _e: self._mark_labels_dirty())
        self.ylabel2_var = ctk.StringVar(value="Fase [$^\\circ$]")
        stacked_entry(box, "Etiqueta Y2", self.ylabel2_var, on_enter=self.update_plot)
        hint(box, "Aceptan mathtext: $V_{out}$, $^\\circ$.",
             wraplength=280).pack(fill="x", pady=(0, 12))

        combo_field(box, "Fuente", self.font_family_var,
                    list(FONT_FAMILIES.keys()), width=150,
                    command=lambda _=None: self._on_font_change())

        self.theme_mode_var = ctk.StringVar(value="Claro")
        segmented_field(box, "Tema", ["Claro", "Oscuro"], self.theme_mode_var,
                        command=self._on_theme_change, width=64)
        ghost_button(box, "Márgenes del gráfico...",
                     self.mpl_toolbar.configure_subplots).pack(fill="x", pady=(4, 0))

    def _build_legend_section(self, parent) -> None:
        box = self._section(parent, "Leyenda", expanded=False)

        self.legend_var = ctk.BooleanVar(value=True)
        check_field(box, "Mostrar leyenda", self.legend_var, command=self.update_plot)

        self.legend_pos_var = ctk.StringVar(value="upper right")
        combo_field(box, "Posición", self.legend_pos_var, LEGEND_POSITIONS,
                    width=170, command=lambda _=None: self.update_plot())

        self.legend_x_var = ctk.StringVar(value="1.02")
        entry_field(box, "X (fracción)", self.legend_x_var, on_enter=self.update_plot)
        self.legend_y_var = ctk.StringVar(value="1.00")
        entry_field(box, "Y (fracción)", self.legend_y_var, on_enter=self.update_plot)

        self.legend_corner_var = ctk.StringVar(value="upper left")
        combo_field(box, "Anclaje", self.legend_corner_var, CUSTOM_ANCHOR_CORNERS,
                    width=150, command=lambda _=None: self.update_plot())

        self.legend_ncol_var = ctk.StringVar(value="1")
        entry_field(box, "Columnas", self.legend_ncol_var, width=56,
                    on_enter=self.update_plot, rule=False)
        hint(box, "X/Y y anclaje sólo aplican con «personalizada (x, y)». "
                  "Fuera de [0, 1] la leyenda sale del área del gráfico.",
             wraplength=280).pack(fill="x", pady=(8, 0))

    def _build_data_section(self, parent) -> None:
        box = self._section(parent, "Datos", expanded=False)

        self.dec_mode_var = ctk.StringVar(value="Ninguno")
        combo_field(box, "Diezmado", self.dec_mode_var, list(DEC_MODES.keys()),
                    width=150, command=lambda _=None: self.update_plot())
        self.dec_value_var = ctk.StringVar(value="1000")
        entry_field(box, "Valor", self.dec_value_var, on_enter=self.update_plot)
        hint(box, "«Factor N» conserva 1 de cada N muestras; «Máx. puntos» "
                  "reduce hasta esa cantidad.", wraplength=280).pack(fill="x", pady=(8, 10))

        # Display-only cap: keeps redraws fast on multi-million-sample
        # captures without ever touching what gets exported.
        self.max_points_var = ctk.StringVar(value="20k")
        entry_field(box, "Máx. en pantalla", self.max_points_var,
                    on_enter=self.update_plot, rule=False, label_width=132)
        hint(box, "Sólo afecta el dibujo en pantalla; la exportación siempre "
                  "usa todos los puntos. 0 = sin límite.",
             wraplength=280).pack(fill="x", pady=(8, 0))

        self.decimal_comma_check = check_field(
            box, "Archivos con coma decimal", self.decimal_comma_var, rule=False)

    def _build_export_section(self, parent) -> None:
        box = self._section(parent, "Exportar", expanded=True)

        stacked_label(box, "Perfil de exportación")
        profile_row = ctk.CTkFrame(box, fg_color="transparent")
        profile_row.pack(fill="x", pady=(0, 6))
        self.profile_var = ctk.StringVar(value="")
        self.profile_combo = ctk.CTkComboBox(
            profile_row, values=[], variable=self.profile_var, height=28,
            font=font("body"), dropdown_font=font("body"),
            command=lambda _=None: self._apply_export_profile())
        self.profile_combo.pack(fill="x")
        profile_actions = ctk.CTkFrame(box, fg_color="transparent")
        profile_actions.pack(fill="x", pady=(0, 14))
        ghost_button(profile_actions, "Guardar como...", self._save_export_profile,
                     width=140).pack(side="left")
        ghost_button(profile_actions, "Eliminar", self._delete_export_profile,
                     width=90).pack(side="left", padx=6)
        self._refresh_export_profiles()
        Rule(box).pack(fill="x", pady=(0, 12))

        self.csv_mode_container = ctk.CTkFrame(box, fg_color="transparent")
        self.csv_mode_container.pack(fill="x", pady=(0, 8))
        stacked_label(self.csv_mode_container, "Datos para PGFPlots")
        self.csv_mode_var = ctk.StringVar(value="Individual (1 archivo por señal)")
        self.csv_mode_combo = ctk.CTkComboBox(
            self.csv_mode_container,
            values=["Individual (1 archivo por señal)", "Combinado (grilla común)"],
            variable=self.csv_mode_var, height=28, font=font("body"),
            dropdown_font=font("body"))
        self.csv_xy_note = hint(self.csv_mode_container,
                                 "Modo X/Y: se exporta la curva actual.",
                                 wraplength=280)
        self.csv_mode_combo.pack(fill="x")   # default (non-XY) state
        ghost_button(box, "Exportar CSV...", self._export_csv,
                     height=30).pack(fill="x", pady=(8, 14))

        Rule(box).pack(fill="x", pady=(0, 12))

        self.fig_format_var = ctk.StringVar(value="pdf")
        combo_field(box, "Formato", self.fig_format_var,
                    ["pdf", "png", "svg", "pgf"], width=110)
        self.dpi_var = ctk.StringVar(value="300")
        entry_field(box, "DPI", self.dpi_var, rule=False)
        hint(box, "PDF, SVG y PGF son vectoriales; el DPI sólo afecta al PNG.",
             wraplength=280).pack(fill="x", pady=(6, 10))
        primary_button(box, "Exportar figura...", self._export_figure,
                       height=32).pack(fill="x")

    def _refresh_export_profiles(self) -> None:
        self._export_profiles = session.load_profiles()
        names = list(self._export_profiles)
        self.profile_combo.configure(values=names)
        if self.profile_var.get() not in names:
            self.profile_var.set(names[0] if names else "")

    def _apply_export_profile(self) -> None:
        profile = self._export_profiles.get(self.profile_var.get())
        if not profile:
            return
        self.fig_format_var.set(profile.get("fig_format", self.fig_format_var.get()))
        self.dpi_var.set(profile.get("dpi", self.dpi_var.get()))
        self.csv_mode_var.set(profile.get("csv_mode", self.csv_mode_var.get()))
        self.decimal_comma_var.set(
            profile.get("decimal_comma", self.decimal_comma_var.get()))

    def _save_export_profile(self) -> None:
        def _submit(name: str) -> None:
            self._export_profiles = session.upsert_profile(name, {
                "fig_format": self.fig_format_var.get(),
                "dpi": self.dpi_var.get(),
                "csv_mode": self.csv_mode_var.get(),
                "decimal_comma": self.decimal_comma_var.get(),
            })
            self._refresh_export_profiles()
            self.profile_var.set(name)

        TextPrompt(self, "Guardar perfil",
                  "Nombre del perfil (formato, DPI y modo CSV actuales):",
                  on_submit=_submit)

    def _delete_export_profile(self) -> None:
        name = self.profile_var.get()
        if not name:
            return
        if not messagebox.askyesno("Eliminar perfil",
                                   f"¿Eliminar el perfil «{name}»?"):
            return
        self._export_profiles = session.delete_profile(name)
        self._refresh_export_profiles()

    def _mark_labels_dirty(self) -> None:
        """Stop auto-generating axis labels once the user typed custom ones."""
        self._axis_labels_dirty = True

    # ------------------------------------------------------------------ #
    # File loading / signal management
    # ------------------------------------------------------------------ #
    def _next_color(self) -> str:
        color = TRACE_CYCLE[self._color_index % len(TRACE_CYCLE)]
        self._color_index += 1
        return color

    def _load_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Seleccionar archivos de datos",
            filetypes=[("CSV/TXT", "*.csv *.txt"), ("Todos los archivos", "*.*")])
        if not paths:
            return
        self._ingest_files(paths)

    def _ingest_files(self, paths) -> None:
        """
        Shared loading path for both "+ Abrir archivo" and dropping files
        onto the window: everything from here down used to live directly in
        `_load_files`, which only ever supplied paths from the file dialog.
        """
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
                # Remembered so a saved session can replay this exact load
                # (see `_restore_signal`) without guessing which column was
                # picked out of a multi-column file.
                self._signal_columns[sig.uid] = (x_col, y_col)
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
        self._signal_columns.pop(self.selected_uid, None)
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
        self._signal_columns.clear()
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

        if not self.signal_order:
            hint(self.signal_list_frame,
                 "Sin trazas. Abrí un archivo para empezar.",
                 wraplength=240).pack(fill="x", pady=14)
            self._refresh_chips()
            return

        for uid in self.signal_order:
            sig = self.signals[uid]
            tag = {"dB": "dB", "deg": "fase"}.get(sig.y_kind, "V")
            row = TraceRow(
                self.signal_list_frame, name=sig.name,
                color=sig.color or "#8A8A8A", tag=tag, visible=sig.visible,
                on_select=lambda u=uid: self._select_signal(u),
                on_toggle=lambda value, u=uid: self._toggle_signal(u, value),
                on_color=lambda u=uid: self._pick_row_color(u))
            row.pack(fill="x", pady=1)
            self.row_widgets[uid] = {"row": row}

        self._highlight_selected()
        self._refresh_chips()

    def _toggle_signal(self, uid: str, visible: bool) -> None:
        signal = self.signals.get(uid)
        if signal is None:
            return
        signal.visible = bool(visible)
        self.update_plot()

    def _pick_row_color(self, uid: str) -> None:
        """Open the native colour picker straight from the trace row."""
        sig = self.signals.get(uid)
        if sig is None:
            return
        initial = sig.color or TRACE_CYCLE[0]
        try:
            _rgb, hex_color = colorchooser.askcolor(color=initial, parent=self,
                                                     title=f"Color de {sig.name}")
        except tk.TclError:
            _rgb, hex_color = colorchooser.askcolor(parent=self, title=f"Color de {sig.name}")
        if not hex_color:
            return
        sig.color = hex_color
        self._refresh_signal_list()
        if self.selected_uid == uid:
            self._build_param_panel(uid)   # keep the open per-trace panel in sync
        self.update_plot()

    def _highlight_selected(self) -> None:
        for uid, widgets in self.row_widgets.items():
            widgets["row"].set_selected(uid == self.selected_uid)

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
        """
        Per-trace controls. Only the four things you change on every trace are
        visible; correction and source metadata sit behind two collapsibles,
        so the column stays readable with several traces loaded.
        """
        sig = self.signals[uid]
        for w in self.param_frame.winfo_children():
            w.destroy()

        SectionHeader(self.param_frame, "Ajustes de la traza").pack(fill="x")
        Rule(self.param_frame).pack(fill="x", pady=(6, 12))

        legend_var = ctk.StringVar(value=sig.legend_label or "")
        stacked_entry(self.param_frame, "Nombre en la leyenda", legend_var)

        # --- colour ---------------------------------------------------- #
        stacked_label(self.param_frame, "Color")
        color_row = ctk.CTkFrame(self.param_frame, fg_color="transparent")
        color_row.pack(fill="x", pady=(0, 12))
        color_var = ctk.StringVar(value=sig.color or TRACE_CYCLE[0])
        swatch = ctk.CTkButton(color_row, text="", width=22, height=26,
                                corner_radius=0, border_width=1,
                                border_color=col("border_str"),
                                fg_color=color_var.get(), hover_color=color_var.get())
        swatch.pack(side="left")
        color_entry = ctk.CTkEntry(color_row, textvariable=color_var, height=26,
                                    font=font("mono"))
        color_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))

        def _sync_swatch(*_):
            value = color_var.get().strip()
            try:
                swatch.configure(fg_color=value, hover_color=value)
            except (tk.TclError, ValueError):
                pass   # invalid hex while typing: keep the last valid preview

        color_var.trace_add("write", _sync_swatch)

        def _pick_color():
            initial = color_var.get().strip() or TRACE_CYCLE[0]
            try:
                _rgb, hex_color = colorchooser.askcolor(color=initial, parent=self)
            except tk.TclError:
                _rgb, hex_color = colorchooser.askcolor(parent=self)
            if hex_color:
                color_var.set(hex_color)

        swatch.configure(command=_pick_color)

        # --- stroke ---------------------------------------------------- #
        stacked_label(self.param_frame, "Trazo")
        style_var = ctk.StringVar(value=sig.linestyle)
        Segmented(self.param_frame, LINESTYLES, style_var, labels=LINE_GLYPHS,
                  width=56).pack(fill="x", pady=(0, 12))

        # --- which Y axis ---------------------------------------------- #
        stacked_label(self.param_frame, "Dibujar contra")
        axis_var = ctk.StringVar(value="Der" if sig.secondary_y else "Izq")
        Segmented(self.param_frame, ["Izq", "Der"], axis_var, width=56
                  ).pack(fill="x", pady=(0, 4))
        hint(self.param_frame, "«Der» usa un eje Y2 con escala propia.",
             wraplength=240).pack(fill="x", pady=(0, 12))

        # --- corrections ------------------------------------------------ #
        corrections = StaticSection(self.param_frame, "Corregir los datos")
        corrections.pack(fill="x", pady=(4, 8))
        box = corrections.body

        x_unit_now, y_unit_now = sig.unit_t_in, sig.unit_v_in
        xoff_var = ctk.StringVar(
            value=f"{sig.t_offset / x_units_for_domain(sig.domain)[x_unit_now]:g}")
        entry_field(box, "Desplazar en X", xoff_var, suffix=x_unit_now, label_width=104)
        yoff_var = ctk.StringVar(
            value=f"{sig.v_offset / y_units_for_kind(sig.y_kind)[y_unit_now]:g}")
        entry_field(box, "Desplazar en Y", yoff_var, suffix=y_unit_now, label_width=104)
        gain_var = ctk.StringVar(value=f"{sig.gain:g}")
        entry_field(box, "Multiplicar por", gain_var, suffix="×", label_width=104)
        invert_var = ctk.BooleanVar(value=sig.invert)
        check_field(box, "Invertir (×−1)", invert_var, rule=False)

        # --- source metadata --------------------------------------------- #
        source = StaticSection(self.param_frame, "Origen de los datos")
        source.pack(fill="x", pady=(0, 8))
        box = source.body

        name_var = ctk.StringVar(value=sig.name)
        stacked_entry(box, "Nombre", name_var)

        domain_var = ctk.StringVar(value=sig.domain)
        combo_field(box, "Dominio", domain_var, ["time", "freq"], width=110)
        ykind_var = ctk.StringVar(value=sig.y_kind)
        combo_field(box, "Magnitud", ykind_var, ["voltage", "dB", "deg"], width=110)

        unit_x_in_var = ctk.StringVar(value=sig.unit_t_in)
        unit_x_combo = combo_field(box, "Unidad X", unit_x_in_var,
                                    list(x_units_for_domain(sig.domain).keys()),
                                    width=110)
        unit_y_in_var = ctk.StringVar(value=sig.unit_v_in)
        unit_y_combo = combo_field(box, "Unidad Y", unit_y_in_var,
                                    list(y_units_for_kind(sig.y_kind).keys()),
                                    width=110, rule=False)
        hint(box, "Unidad en la que vienen los datos del archivo.",
             wraplength=240).pack(fill="x", pady=(6, 0))

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

        # --- apply ------------------------------------------------------ #
        def apply_changes():
            new_domain = domain_var.get()
            new_kind = ykind_var.get()
            x_units = x_units_for_domain(new_domain)
            y_units = y_units_for_kind(new_kind)
            new_ux = unit_x_in_var.get() if unit_x_in_var.get() in x_units else list(x_units)[0]
            new_uy = unit_y_in_var.get() if unit_y_in_var.get() in y_units else list(y_units)[0]

            color = color_var.get().strip()
            try:
                swatch.configure(fg_color=color)
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
            sig.secondary_y = axis_var.get() == "Der"

            self._sync_unit_options()
            self._refresh_signal_list()
            self._refresh_xy_combos()
            self._select_signal(uid)
            self.update_plot()

        primary_button(self.param_frame, "Aplicar cambios", apply_changes
                       ).pack(fill="x", pady=(6, 14))

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
        self.yscale_seg.set_enabled(mode != "Diagrama de Bode")

        self._axis_labels_dirty = False   # regenerate default labels for the new mode
        self.update_plot()

    def _on_font_change(self) -> None:
        set_publication_style(font_family=self.font_family_var.get())
        self.update_plot()

    def _on_theme_change(self, value: str) -> None:
        """
        Live switch between the light and dark variants of the same palette.
        Both are stored as [light, dark] pairs in the theme dictionary, so the
        existing widget tree is restyled without being rebuilt.
        """
        set_theme_mode("light" if value.lower().startswith("c") else "dark")
        # Hairlines are plain Tk widgets (for rendering cost, see the note in
        # gui/widgets.py) and therefore opt out of CustomTkinter's automatic
        # appearance-mode switching -- they have to be repainted by hand.
        repaint_plain_widgets()
        try:
            self.canvas.get_tk_widget().configure(
                highlightbackground=tk_color("border_str"),
                highlightcolor=tk_color("border_str"))
        except tk.TclError:
            pass

    def _legend_anchor(self) -> Optional[tuple[float, float]]:
        """(x, y) in axes fractions, only meaningful for the custom position."""
        if self.legend_pos_var.get() != CUSTOM_POSITION:
            return None
        return (_parse_float(self.legend_x_var.get(), 1.02),
                _parse_float(self.legend_y_var.get(), 1.0))

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
            "max_display_points": max(0, int(_parse_float(
                self.max_points_var.get(), 0.0))),
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
            "legend_anchor": self._legend_anchor(),
            "legend_corner": self.legend_corner_var.get(),
            "legend_ncol": max(1, int(_parse_float(self.legend_ncol_var.get(), 1.0))),
            "bode_layout": "separate" if self.bode_layout_var.get().startswith("Separados") else "shared",
        }

    def _gather_curves(self, settings: dict, for_display: bool = True
                       ) -> list[tuple[str, np.ndarray, np.ndarray]]:
        """
        Return (uid, x, y) for visible signals, cropped and decimated, in
        base units.

        `for_display` additionally caps each curve at `max_display_points`.
        A 2 million-sample capture cannot resolve more than a few thousand
        pixels of screen width, so drawing every point costs redraw time --
        on every resize, pan and zoom -- and buys nothing visible. **Export
        always passes `for_display=False`**: the file on disk gets the full
        record, whatever the screen is showing.
        """
        cap = settings.get("max_display_points", 0) if for_display else 0
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
            if cap and x.size > cap:
                x, y = decimate_to_target(x, y, cap)
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
        if self._plot_suspended:
            return
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

        # tight_layout ignores legends anchored outside the axes: reserve the
        # margin explicitly so the preview matches the exported figure.
        reserve_legend_space(self.fig, settings["legend_pos"],
                             settings.get("legend_anchor"))
        apply_plot_chrome(self.fig)

        # The axes were re-created by _reset_figure (fig.clear()), so the
        # overlay artists have to be rebuilt from their persistent specs.
        self.cursors.attach(self.axes)
        self.annotations.attach(self.axes)
        self.cursors.x_unit = settings["x_unit"]
        self.cursors.y_unit = "dB" if mode == "Diagrama de Bode" else settings["y_unit"]
        self.cursors.redraw()
        self.annotations.redraw()
        self._on_cursor_change()
        if self.overlay_window is not None and self.overlay_window.winfo_exists():
            self.overlay_window.panel.refresh_all()

        self.canvas.draw_idle()
        self.status_label.configure(text=f"{n_points} puntos en gráfico · modo: {mode}")

    def _finish_legend(self, ax, settings: dict, handles=None, labels=None) -> None:
        if not settings["show_legend"]:
            return
        kwargs = legend_kwargs(settings["legend_pos"],
                               anchor=settings.get("legend_anchor"),
                               corner=settings.get("legend_corner", "upper left"),
                               ncol=settings.get("legend_ncol", 1))
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
            ax.legend(h1 + h2, l1 + l2, **legend_kwargs(settings["legend_pos"],
                                                         anchor=settings.get("legend_anchor"),
                                                         corner=settings.get("legend_corner", "upper left"),
                                                         ncol=settings.get("legend_ncol", 1)))
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
                ax_ph.legend(**legend_kwargs(settings["legend_pos"],
                                             anchor=settings.get("legend_anchor"),
                                             corner=settings.get("legend_corner", "upper left"),
                                             ncol=settings.get("legend_ncol", 1)))
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
            ax_mag.legend(h1 + h2, l1 + l2, **legend_kwargs(settings["legend_pos"],
                                                             anchor=settings.get("legend_anchor"),
                                                             corner=settings.get("legend_corner", "upper left"),
                                                             ncol=settings.get("legend_ncol", 1)))
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

        curves = self._gather_curves(settings, for_display=False)
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
        dpi = max(50, int(_parse_float(self.dpi_var.get(), 300.0)))

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

        self._last_export_path = out_path
        self._show_latex_figure(out_path)

    def _show_latex_figure(self, out_path: str) -> None:
        r"""
        Hand back the LaTeX that includes the file just written.

        Exporting the PDF is only half the task -- the figure still has to be
        wrapped in a `figure` environment with a caption, a label and a width,
        and a `.pgf` needs `\input` rather than `\includegraphics`. Building
        it here guarantees the path, the file type and the label agree with
        what actually landed on disk.
        """
        default_caption = (self.title_var.get().strip()
                           or os.path.splitext(os.path.basename(out_path))[0])
        caption_var = ctk.StringVar(value=default_caption)
        label_var = ctk.StringVar(value=latex.sanitize_label(default_caption))
        width_var = ctk.StringVar(value="0.85\\linewidth")
        project_dir = os.path.dirname(os.path.dirname(out_path)) or None

        def build(values: dict) -> str:
            return latex.figure_block(
                out_path,
                caption=values.get("Caption", ""),
                label=values.get("Label", ""),
                width=values.get("Ancho", "0.85\\linewidth"),
                relative_to=project_dir,
                escape_caption=bool(values.get("__toggle__")))

        initial = build({"Caption": caption_var.get(), "Label": label_var.get(),
                         "Ancho": width_var.get(), "__toggle__": False})
        CodeDialog(
            self, "Incluir en LaTeX", initial,
            note=f"{latex.figure_requirements(out_path)}    ·    "
                 f"Archivo: {out_path}",
            fields=[("Caption", caption_var), ("Label", label_var),
                    ("Ancho", width_var)],
            rebuild=build,
            extra_toggle=("Escapar caracteres especiales del caption "
                          "(desactivalo si escribís $matemática$)", False))

    # ------------------------------------------------------------------ #
    # Overlay layer: cursors and annotations
    # ------------------------------------------------------------------ #
    def _overlay_units(self) -> tuple[str, str]:
        """Units used to format the cursor readout."""
        return self.cursors.x_unit, self.cursors.y_unit

    def _refresh_overlays(self) -> None:
        """Re-render only the overlay artists; the data plot is untouched."""
        self.cursors.attach(self.axes)
        self.annotations.attach(self.axes)
        self.cursors.redraw()
        self.annotations.redraw()
        self.canvas.draw_idle()

    def _open_overlay_window(self, tab: str = "Cursores") -> None:
        if self.overlay_window is not None and self.overlay_window.winfo_exists():
            self.overlay_window.panel.show_pane(tab)
            self.overlay_window.lift()
            self.overlay_window.focus()
            return
        self.overlay_window = OverlayWindow(
            self, self.cursors, self.annotations,
            on_refresh=self._refresh_overlays,
            unit_provider=self._overlay_units,
            on_close=self._on_overlay_closed,
            initial_pane=tab)

    def _on_overlay_closed(self) -> None:
        self.overlay_window = None


def main() -> None:
    # CustomTkinter reads the theme dictionary when each widget is built, so
    # the palette must be installed before the window exists.
    apply_theme("light")                  # use "dark" for the dark variant
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
