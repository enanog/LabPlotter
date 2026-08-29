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
from matplotlib.lines import Line2D
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
    Signal, build_missing_signal, build_signal, read_table,
    resolve_source_path, x_units_for_domain, y_units_for_kind,
)
from core.export import (
    FONT_FAMILIES, export_csv_combined, export_csv_individual,
    export_figure, export_xy_csv, set_publication_style,
)
from core.layout import (
    CUSTOM_ANCHOR_CORNERS, CUSTOM_POSITION, LEGEND_POSITIONS,
    legend_kwargs, reserve_legend_space,
)
from core import board, latex, session, tabs
from core.history import History, apply_snapshot
from core.i18n import LANGUAGES, get_language, set_language, t
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
    CodeDialog, LabeledCombo, Segmented, SectionGroup, Splitter, StaticSection,
    SliderField, TextPrompt, ShortcutsWindow,
    ToolButton, TraceRow, VRule, check_field, combo_field, entry_field,
    ghost_button, hint, primary_button, repaint_plain_widgets, segmented_field,
    stacked_entry, stacked_label,
)
from gui.board_window import BoardWindow

# Window width at which the type scale is exactly as designed (1.0). Matches
# the default geometry, so the app opens at native size. Clamping lives in
# `theme.set_font_scale`.
_REFERENCE_WIDTH = 1480

# Draggable clamp for the side panels (see `App._drag_left` / `_drag_right`).
_LEFT_MIN, _LEFT_MAX = 220, 480
_RIGHT_MIN, _RIGHT_MAX = 260, 520

PLOT_MODES = ["Tiempo / Frecuencia", "Modo X/Y", "Diagrama de Bode", "Pizarra en blanco"]
# Internal identifiers, never shown. Display text lives in `*_LABELS` and goes
# through `t()`; an earlier version compared translated strings directly
# (`startswith("Separados")`), which would silently break the moment the
# interface language changed.
BODE_LAYOUTS = ["shared", "separate"]
LINESTYLES = ["-", "--", "-.", ":", "None"]
# Matplotlib recognises the literal string "None" as its own no-line alias
# (equivalent to `linestyle="none"`), so a trace with this style and any
# marker set draws only its data points -- no segment connecting one sample
# to the next, i.e. no visual interpolation between them.
# Matplotlib marker codes offered per trace. "None" (the string) means no
# marker at all -- Matplotlib's own sentinel for "not set" is the object
# `None`, but a StringVar cannot hold that, so this string is translated to
# the real `None` in `_marker_kwargs` below.
MARKERS = ["None", "o", "x", "+", "s", "^", "v", "D", "*"]
DEC_MODES = ["none", "factor", "target"]
CSV_MODES = ["individual", "combined"]
SCALES = ["linear", "log"]
THEMES = ["light", "dark"]
AXIS_SIDES = ["primary", "secondary"]


def _labels() -> dict:
    """Display labels for every internal identifier, in the active language."""
    return {
        "modes": {"Tiempo / Frecuencia": t("Tiempo"), "Modo X/Y": t("X / Y"),
                  "Diagrama de Bode": t("Bode"), "Pizarra en blanco": t("Pizarra")},
        "bode": {"shared": t("Juntos"), "separate": t("Separados")},
        "dec": {"none": t("Ninguno"), "factor": t("Factor N"),
                "target": t("Máx. puntos")},
        "csv": {"individual": t("Individual (1 archivo por señal)"),
                "combined": t("Combinado (grilla común)")},
        "scale": {"linear": t("lineal"), "log": t("log")},
        "theme": {"light": t("Claro"), "dark": t("Oscuro")},
        "side": {"primary": t("Izq"), "secondary": t("Der")},
        "marker": {"None": t("Ninguno"), "o": "○ " + t("Círculo"),
                   "x": "✕ " + t("Cruz (x)"), "+": "+ " + t("Cruz (+)"),
                   "s": "□ " + t("Cuadrado"), "^": "△ " + t("Triángulo"),
                   "v": "▽ " + t("Triángulo invertido"), "D": "◇ " + t("Rombo"),
                   "*": "✶ " + t("Estrella")},
    }


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
        "compact": {"left": 0.09, "right": 0.97, "bottom": 0.10,
                     "top": 0.95, "wspace": 0.18, "hspace": 0.28},
        "external_legend": {"left": 0.10, "right": 0.78, "bottom": 0.12,
                            "top": 0.92, "wspace": 0.20, "hspace": 0.30},
    }

    def __init__(self, master, fig, on_apply=None):
        super().__init__(master)
        self.fig = fig
        self.on_apply = on_apply
        self.title(t("Márgenes del gráfico"))
        self.geometry("400x620")
        self.minsize(360, 520)
        self.resizable(True, True)
        self.transient(master)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(head, text=spaced(t("Márgenes")), font=font("header"),
                     text_color=col("fg_muted")).pack(side="left")
        Rule(self).pack(fill="x", padx=20)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(12, 0))

        pars = fig.subplotpars
        self.vars: dict[str, ctk.StringVar] = {}
        self.sliders: dict[str, SliderField] = {}
        for name, label in self.FIELDS:
            var = ctk.StringVar(value=f"{getattr(pars, name):.3f}")
            self.vars[name] = var
            field = SliderField(body, label, var, minimum=0.0, maximum=1.0,
                                on_change=self._apply_live)
            field.pack(fill="x", pady=(0, 8))
            self.sliders[name] = field

        hint(body, t("Arrastrá el slider o escribí el valor y Enter."),
             wraplength=300).pack(fill="x", pady=(6, 0))

        presets = ctk.CTkFrame(body, fg_color="transparent")
        presets.pack(fill="x", pady=(12, 0))
        preset_labels = {"compact": t("Compacto"),
                         "external_legend": t("Leyenda externa")}
        for name in self.PRESETS:
            ghost_button(presets, preset_labels.get(name, name),
                         lambda n=name: self._preset(n),
                         width=132).pack(side="left", padx=(0, 8))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=16)
        primary_button(actions, t("Aplicar"), self._apply, height=28,
                       width=104).pack(side="right")
        ghost_button(actions, t("Restablecer"), self._reset,
                     width=112).pack(side="right", padx=(0, 8))
        ghost_button(actions, t("Cerrar"), self.destroy,
                     width=88).pack(side="left")

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _values(self, silent: bool = False) -> Optional[dict]:
        values = {}
        for name, _label in self.FIELDS:
            value = parse_eng(self.vars[name].get(), None)
            if value is None:
                if not silent:
                    messagebox.showerror(
                        t("Valor inválido"),
                        t("Todos los márgenes deben ser números entre 0 y 1."),
                        parent=self)
                return None
            values[name] = min(1.0, max(0.0, value))
        # Matplotlib rejects a left margin at or past the right one (and the
        # same vertically); catching it here gives a readable message instead
        # of a traceback from deep inside the layout engine.
        if values["left"] >= values["right"] or values["bottom"] >= values["top"]:
            if not silent:
                messagebox.showerror(
                    t("Márgenes inconsistentes"),
                    t("El margen izquierdo debe ser menor que el derecho, y "
                      "el inferior menor que el superior."), parent=self)
            return None
        return values

    def _apply_live(self) -> None:
        """Slider-driven apply: silent, so a drag through an invalid
        intermediate state (left past right) does not raise a dialog."""
        self._apply(silent=True)

    def _apply(self, silent: bool = False) -> None:
        values = self._values(silent=silent)
        if values is None:
            return
        for name, value in values.items():
            field = self.sliders.get(name)
            field.set_value(value) if field else self.vars[name].set(f"{value:.3f}")
        try:
            self.fig.subplots_adjust(**values)
        except (ValueError, AttributeError) as exc:
            messagebox.showerror(t("Error"), str(exc), parent=self)
            return
        self.fig.canvas.draw_idle()
        if self.on_apply is not None:
            # Hand the values back so replots stop discarding them.
            self.on_apply(dict(values))

    def _preset(self, name: str) -> None:
        for key, value in self.PRESETS[name].items():
            self.sliders[key].set_value(value)
        self._apply()

    def _reset(self) -> None:
        for key, value in self.DEFAULTS.items():
            self.sliders[key].set_value(value)
        self._apply()
        if self.on_apply is not None:
            self.on_apply(None)   # back to automatic layout


class EditableNavigationToolbar(NavigationToolbar2Tk):
    """Matplotlib toolbar whose 'Configure subplots' button opens `SubplotConfigDialog`."""

    def configure_subplots(self) -> None:  # noqa: D102 - overrides base class
        SubplotConfigDialog(self.canvas.get_tk_widget(), self.canvas.figure,
                            on_apply=getattr(self, "on_margins_applied", None))


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
        self.title(f'{t("Seleccionar columnas")} — {filename}')
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
        ctk.CTkButton(btns, text=t("Aceptar"), command=self._accept).pack(side="left", padx=6)
        ctk.CTkButton(btns, text=t("Cancelar"), command=self._cancel,
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

        ctk.CTkLabel(scroll, text=t("Columna de eje X:"), anchor="w"
                     ).pack(fill="x", anchor="w")
        self.x_var = ctk.StringVar(value=default_x)
        x_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        x_frame.pack(fill="x", pady=(2, 8))
        for col in columns:
            ctk.CTkRadioButton(
                x_frame, text=f"{col}  ({kind_label.get(col_kind.get(col, ''), '—')})",
                variable=self.x_var, value=col).pack(anchor="w", pady=1)

        ctk.CTkLabel(scroll, text=t("Columnas de valor (una señal por columna marcada):"),
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
                t("Selección inválida"),
                t("Seleccioná al menos una columna de valor distinta de la del eje X."),
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

        # Multi-figure board: a list of rows, each a list of `board.BoardPanel`
        # (see core/board.py). Panels are added from the export section with
        # the plot currently on screen; the board window itself only
        # arranges/retitles/exports them, so this list is the single source
        # of truth for both surfaces. Not part of `_persisted_vars` / the
        # saved session -- it is scratch space for the current run, exactly
        # like `_last_export_path`.
        self.board_rows: list = [board.new_row()]
        self._board_window: Optional[BoardWindow] = None
        self._board_export_dir: Optional[str] = None

        # Plot tabs: several independent plots (own signals + settings) held
        # in memory at once, so building the next figure for the tablero
        # never means losing the previous one. `active_tab` is the index
        # into `plot_tabs` currently shown on screen; everything else in
        # `self.signals`/`self.signal_order`/the settings StringVars IS that
        # tab's live state -- switching tabs snapshots it out via
        # `_gather_plot_state()` and restores the target tab via
        # `_apply_plot_state()`. Not part of `_persisted_vars()`; the whole
        # list (plus which tab is active) is saved/restored as its own
        # top-level session key instead (see `_gather_state`/`_apply_state`).
        self.plot_tabs: list[tabs.PlotTab] = [tabs.PlotTab(name=t("Gráfico 1"))]
        self.active_tab: int = 0

        # (x_col, y_col) used to build each loaded Signal, keyed by uid.
        # Only used to replay `read_table` + `build_signal` when restoring a
        # saved session; signals added any other way simply aren't persisted.
        self._signal_columns: dict[str, tuple[str, str]] = {}
        # Per-trace line weight. Held here rather than on the Signal model,
        # which this module does not own -- setting an attribute on a class
        # that may define __slots__ would fail at runtime.
        self.line_widths: dict[str, float] = {}
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

        # Undo/redo over the trace set. Snapshot-based: see core/history.py.
        self.history = History()
        # Margins set by hand in the margins dialog. While this is set,
        # `update_plot` skips tight_layout -- otherwise every replot silently
        # threw the manual layout away, which is what made adjusting margins
        # feel like it never stuck.
        self._manual_margins: Optional[dict] = None

        self.decimal_comma_var = ctk.BooleanVar(value=False)
        # Default font matches a typical LaTeX report (lmodern / Computer
        # Modern), so exported figures blend in with the document out of
        # the box. Selectable in the GUI like any other font.
        self.font_family_var = ctk.StringVar(value="LaTeX (Computer Modern)")
        # Base font size in points, applied to axis labels/ticks/legend by
        # `set_publication_style` (the title itself is drawn one point
        # larger -- see that function). 10pt matches the previous hardcoded
        # default, so existing sessions look identical until changed.
        self.font_size_var = ctk.StringVar(value="10")

        set_publication_style(font_family=self.font_family_var.get(),
                              base_fontsize=_parse_float(self.font_size_var.get(), 10.0))

        self._build_layout()

        # Snapshot of every persisted setting's startup value, taken right
        # after the widgets that own them exist and before any session/tab
        # restore touches them. A tab created empty (`_add_tab`) has no
        # "settings" of its own yet -- without this, switching to it would
        # leave every field showing whatever the previously active tab last
        # set, since `_apply_plot_state` only ever *sets* variables, it
        # never clears one just because the new state doesn't mention it.
        self._default_settings: dict = {}
        for key, var in self._persisted_vars().items():
            try:
                self._default_settings[key] = var.get()
            except Exception:
                pass

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
        self.bind_all("<Control-z>", lambda _e: self._undo())
        self.bind_all("<Control-y>", lambda _e: self._redo())
        self.bind_all("<Control-Shift-Z>", lambda _e: self._redo())
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
        self._dnd_status = t("no disponible")

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
            self._dnd_status = t("activo")
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
            detail = (f"\n\n{t('Ignorado')}: {', '.join(rejected[:5])}"
                      if rejected else "")
            messagebox.showinfo(
                t("Sin archivos válidos"),
                t("Soltá archivos .csv o .txt para cargarlos.") + detail)
            return
        self._ingest_files(paths)

    def _open_shortcuts(self) -> None:
        if self._shortcuts_window is not None and self._shortcuts_window.winfo_exists():
            self._shortcuts_window.lift()
            self._shortcuts_window.focus()
            return
        groups = [
            (t("General"), [
                ("Ctrl+O", t("Abrir archivo(s)")),
                ("Ctrl+Z", t("Deshacer")),
                ("Ctrl+Y", t("Rehacer")),
                ("Ctrl+E", t("Exportar figura (y obtener el bloque LaTeX)")),
                ("Ctrl+S", t("Exportar CSV para PGFPlots")),
                ("Supr / Backspace", t("Quitar la traza seleccionada")),
                ("Enter", t("Aplicar el campo activo")),
                ("F1", t("Mostrar esta ventana")),
            ]),
            (t("Gráfico"), [
                (t("Cursor + clic"), t("Colocar un cursor de medición")),
                (t("Arrastre"), "Mover un cursor, o zoom/paneo según la herramienta activa"),
                (t("Anotar + clic"), t("Capturar coordenadas para una anotación")),
            ]),
            (t("Campos numéricos"), [
                ("2.2k", "2200"),
                ("4u7", "4,7 µ  (notación R: el prefijo hace de coma)"),
                ("470p / 10M", "prefijos T G M k m u n p f"),
                ("10 kHz", t("la unidad al final se ignora")),
            ]),
            (t("Arrastrar y soltar"), [
                (self._dnd_status, t("Soltá archivos .csv o .txt sobre la ventana")),
            ]),
            (t("Ventana"), [
                (t("Arrastrar el borde"), t("Redimensionar los paneles laterales a mano")),
                (t("Modo compacto"), "Ocultar los paneles y usar todo el ancho para el gráfico"),
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
            "font_family": self.font_family_var, "font_size": self.font_size_var,
            "legend_font_size": self.legend_font_size_var,
            "theme_mode": self.theme_mode_var,
            "legend": self.legend_var, "legend_pos": self.legend_pos_var,
            "legend_x": self.legend_x_var, "legend_y": self.legend_y_var,
            "legend_corner": self.legend_corner_var, "legend_ncol": self.legend_ncol_var,
            "legend_frameon": self.legend_frameon_var,
            "dec_mode": self.dec_mode_var, "dec_value": self.dec_value_var,
            "decimal_comma": self.decimal_comma_var,
            "plot_mode": self.plot_mode_var, "bode_layout": self.bode_layout_var,
            "fig_format": self.fig_format_var, "dpi": self.dpi_var,
            "csv_mode": self.csv_mode_var,
            "xy_x": self.xy_x_var, "xy_y": self.xy_y_var,
            "xy_legend": self.xy_legend_var, "xy_color": self.xy_color_var,
        }

    def _gather_state(self) -> dict:
        # Snapshot the tab currently on screen before reading it back out,
        # so `plot_tabs` is never stale relative to what is actually loaded.
        plot_state = self._gather_plot_state()
        self.plot_tabs[self.active_tab].state = plot_state

        return {
            "geometry": self.geometry(),
            "left_width": self._left_width, "right_width": self._right_width,
            "compact": self._compact,
            "sections": self.settings.state(),
            "manual_margins": plot_state["manual_margins"],
            "language": get_language(),
            # Kept at the top level too (not just inside "tabs") so a session
            # file from before plot tabs existed, and any code that still
            # expects "the" settings/signals, both keep working.
            "settings": plot_state["settings"],
            "signals": plot_state["signals"],
            "tabs": [{"name": tab.name, "state": tab.state} for tab in self.plot_tabs],
            "active_tab": self.active_tab,
        }

    def _restore_signal(self, record: dict, anchor_dir: Optional[str] = None) -> bool:
        """
        Replay one saved signal record. `anchor_dir` is the folder of the
        JSON currently being loaded (session config dir, or a
        *.labplotter.json sidecar's own folder) -- it is what lets
        `resolve_source_path` find the data file again when the absolute
        path from another machine no longer exists.

        A record whose file can't be found is NOT dropped: it survives as a
        "missing" placeholder (see `build_missing_signal`) so its settings,
        order and legend entry aren't lost, and the user can reconnect it by
        hand from the trace list (`_relink_signal`).
        """
        x_col, y_col = record.get("x_col"), record.get("y_col")
        if not x_col or not y_col:
            return False   # no columns to replay with -- nothing to keep either

        sig = None
        resolved = resolve_source_path(record, anchor_dir)
        if resolved:
            try:
                df, _col_kind = read_table(resolved, decimal_comma=self.decimal_comma_var.get())
                sig = build_signal(df, x_col, y_col, record.get("name") or "señal", resolved,
                                   domain=record.get("domain", "time"),
                                   y_kind=record.get("y_kind", "voltage"),
                                   color=record.get("color") or self._next_color())
            except Exception:
                sig = None   # found but unreadable/corrupted: fall through to placeholder

        if sig is None:
            sig = build_missing_signal(record, color=record.get("color") or self._next_color())

        for attr in ("unit_t_in", "unit_v_in", "t_offset", "v_offset", "gain",
                    "invert", "linestyle", "marker", "marker_size",
                    "marker_hollow", "secondary_y"):
            if attr in record:
                setattr(sig, attr, record[attr])
        sig.legend_label = record.get("legend_label")
        sig.display_name = record.get("display_name")
        # A missing placeholder has nothing to draw -- force it hidden
        # regardless of what was saved, so it doesn't show as an empty
        # legend entry until it's reconnected.
        sig.visible = record.get("visible", True) and not sig.missing
        self.line_widths[sig.uid] = float(
            record.get("line_width", self.DEFAULT_LINE_WIDTH))

        self.signals[sig.uid] = sig
        self.signal_order.append(sig.uid)
        self._signal_columns[sig.uid] = (x_col, y_col)
        return True

    def _replace_missing_signal(self, uid: str, path: str) -> bool:
        """
        Swap the placeholder `Signal` at `uid` for real data read from
        `path`, keeping every cosmetic/replay setting it already had
        (offsets, gain, color, legend, marker, visibility...). Returns
        False -- leaving the placeholder untouched -- if `path` can't be
        read with the x/y columns this trace was originally saved under,
        e.g. it turns out to be an unrelated file that just shares a name.
        """
        sig = self.signals.get(uid)
        if sig is None:
            return False
        x_col, y_col = self._signal_columns.get(uid, (None, None))
        if not x_col or not y_col:
            return False
        try:
            df, _col_kind = read_table(path, decimal_comma=self.decimal_comma_var.get())
            new_sig = build_signal(df, x_col, y_col, sig.name, path,
                                   domain=sig.domain, y_kind=sig.y_kind, color=sig.color)
        except Exception:
            return False

        for attr in ("unit_t_in", "unit_v_in", "t_offset", "v_offset", "gain",
                    "invert", "linestyle", "marker", "marker_size",
                    "marker_hollow", "secondary_y", "legend_label", "display_name"):
            setattr(new_sig, attr, getattr(sig, attr))
        new_sig.visible = True
        new_sig.uid = uid   # keep identity: signal_order/line_widths/selection key off this
        self.signals[uid] = new_sig
        return True

    def _relink_signal(self, uid: str) -> None:
        """
        Manually point a 'missing' trace at its new location via a file
        dialog, then piggy-back on that pick to resolve every OTHER missing
        trace that originally lived in the same folder: if the folder the
        user just pointed to also contains a file with each one's original
        basename, it's reconnected automatically too -- this is the common
        case of a whole batch of CSVs that got moved/synced together (e.g.
        a OneDrive folder under a new root on another computer), so the
        person doesn't have to repeat this dialog once per trace.
        """
        sig = self.signals.get(uid)
        if sig is None or not sig.missing:
            return

        initial_dir = os.path.dirname(sig.source_rel or sig.source_path or "") or None
        path = filedialog.askopenfilename(
            title=t("Reconectar archivo de origen"), initialdir=initial_dir,
            filetypes=[(t("Archivos de datos"), "*.csv *.txt"),
                      (t("Todos los archivos"), "*.*")])
        if not path:
            return

        if not self._replace_missing_signal(uid, path):
            messagebox.showerror(
                t("No se pudo reconectar"),
                t("El archivo elegido no tiene las columnas esperadas para esta traza."))
            return

        # Other still-missing traces that originally sat in the very same
        # folder as this one: try their own basename inside the folder just
        # picked. Silent/best-effort -- a miss here just leaves that trace
        # missing, same as before.
        old_dir = os.path.dirname(sig.source_path or "")
        new_dir = os.path.dirname(path)
        resolved_extra = 0
        if old_dir:
            for other_uid in list(self.signal_order):
                if other_uid == uid:
                    continue
                other = self.signals.get(other_uid)
                if other is None or not other.missing or not other.source_path:
                    continue
                if os.path.dirname(other.source_path) != old_dir:
                    continue
                candidate = os.path.join(new_dir, os.path.basename(other.source_path))
                if os.path.isfile(candidate) and self._replace_missing_signal(other_uid, candidate):
                    resolved_extra += 1

        self._refresh_signal_list()
        self._refresh_xy_combos()
        self.update_plot()
        self._save_session_soon()
        if resolved_extra:
            messagebox.showinfo(
                t("Señales reconectadas"),
                t("Se reconectaron automáticamente {n} señal(es) más desde "
                  "la misma carpeta.").format(n=resolved_extra))

    def _apply_state(self, data: dict) -> None:
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
        sections = data.get("sections")
        if isinstance(sections, dict):
            self.settings.restore(sections)

        # Tabs: prefer the new "tabs"/"active_tab" keys. A session saved
        # before plot tabs existed only has the old top-level
        # "settings"/"signals", which becomes a single tab -- nothing from
        # an older session is lost, it just now lives in "Gráfico 1".
        raw_tabs = data.get("tabs")
        if raw_tabs:
            self.plot_tabs = [
                tabs.PlotTab(name=raw.get("name") or t("Gráfico {n}").format(n=i + 1),
                            state=raw.get("state") or {})
                for i, raw in enumerate(raw_tabs)]
            self.active_tab = max(0, min(int(data.get("active_tab", 0)),
                                        len(self.plot_tabs) - 1))
        else:
            self.plot_tabs = [tabs.PlotTab(
                name=t("Gráfico 1"),
                state={"settings": data.get("settings", {}),
                       "signals": data.get("signals", []),
                       "manual_margins": data.get("manual_margins")})]
            self.active_tab = 0

        self._refresh_tab_strip()
        # Anchor for path resolution: tabs not yet visited this run still
        # carry raw (unresolved) records straight from session.json, so
        # `_switch_tab` needs this too, not just the tab restored right now.
        self._session_anchor_dir = str(session.config_dir())
        # Restoring a whole session (as opposed to switching tabs) is the one
        # case with no "previous tab" to keep the app-wide theme from, so it
        # is the one caller that restores `theme_mode` too.
        self._apply_plot_state(self.plot_tabs[self.active_tab].state, restore_theme=True,
                               anchor_dir=self._session_anchor_dir)

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

        primary_button(content, t("+  Abrir archivo"), self._load_files,
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
            # "más" already has an _EN entry ("more") but this line built the
            # chip text as a raw f-string bypassing t() entirely, so English
            # mode always showed "+N más" instead of "+N more".
            hint(self.chip_bar, f"+{len(items) - 4} {t('más')}").pack(side="left", padx=8)

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
        ghost_button(footer, t("Quitar"), self._remove_selected_signal,
                     width=90).pack(side="left")
        ghost_button(footer, t("Quitar todas"), self._remove_all_signals,
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

        SectionHeader(scroll, t("Trazas")).pack(fill="x")
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
        hint(self.param_frame, t("Seleccioná una traza de la lista para ver sus ajustes."), wraplength=250).pack(fill="x", pady=20)

    def _build_center_panel(self) -> None:
        center = ctk.CTkFrame(self, corner_radius=0, fg_color=col("app"))
        center.grid(row=1, column=2, sticky="nsew")
        center.grid_rowconfigure(3, weight=1)
        center.grid_columnconfigure(0, weight=1)

        # ------------------------- plot tabs ---------------------------- #
        self._build_tab_strip(center)

        # ------------------------- tool strip ------------------------- #
        strip = ctk.CTkFrame(center, height=44, corner_radius=0, fg_color=col("bar"))
        strip.grid(row=1, column=0, sticky="ew")
        strip.pack_propagate(False)   # children are packed; see _build_topbar
        Rule(strip).pack(side="bottom", fill="x")

        tools = ctk.CTkFrame(strip, fg_color="transparent")
        tools.pack(fill="both", expand=True, padx=16)

        self.plot_mode_var = ctk.StringVar(value=PLOT_MODES[0])
        Segmented(tools, PLOT_MODES, self.plot_mode_var,
                  labels=_labels()["modes"],
                  command=lambda _v: self._on_mode_change(), width=76
                  ).pack(side="left", pady=9)
        VRule(tools, height=20).pack(side="left", padx=14, pady=12)

        self._active_tool: Optional[str] = None
        self.tool_buttons: dict[str, ToolButton] = {}
        for key, label in (("cursor", t("Cursor")), ("annotate", t("Anotar")),
                           ("zoom", t("Zoom")), ("pan", t("Mover"))):
            button = ToolButton(tools, label, width=72,
                                command=lambda k=key: self._select_tool(k))
            button.pack(side="left", padx=(0, 5), pady=9)
            self.tool_buttons[key] = button
        ghost_button(tools, t("Encuadrar"), self._fit_to_data,
                     width=142).pack(side="left", padx=(8, 0), pady=9)

        self.compact_button = ToolButton(tools, t("Compacto"), width=88,
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
        # Both live directly in row 2 of `center` and only one is ever shown.
        # They used to sit inside a wrapper frame, which stayed on screen even
        # when both children were hidden -- and an empty CTkFrame holds a
        # 200x200 request, so it reserved a blank band above the plot in the
        # default (Tiempo) mode, which shows neither of them.
        center.grid_rowconfigure(2, weight=0)

        self.xy_frame = ctk.CTkFrame(center, corner_radius=0, fg_color=col("bar"),
                                     height=1)
        self.xy_frame.grid(row=2, column=0, sticky="ew")
        xy_inner = ctk.CTkFrame(self.xy_frame, fg_color="transparent", height=1)
        xy_inner.pack(fill="x", padx=16, pady=8)
        self._build_xy_controls(xy_inner)
        self.xy_frame.grid_remove()

        self.bode_frame = ctk.CTkFrame(center, corner_radius=0, fg_color=col("bar"),
                                       height=1)
        self.bode_frame.grid(row=2, column=0, sticky="ew")
        bode_inner = ctk.CTkFrame(self.bode_frame, fg_color="transparent", height=1)
        bode_inner.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(bode_inner, text=t("Disposición"), font=font("label"),
                     text_color=col("fg_muted")).pack(side="left", padx=(0, 10))
        self.bode_layout_var = ctk.StringVar(value="shared")
        Segmented(bode_inner, BODE_LAYOUTS, self.bode_layout_var,
                  labels=_labels()["bode"], width=94,
                  command=lambda _v: self.update_plot()).pack(side="left")
        self.bode_frame.grid_remove()

        # ---------------------------- canvas -------------------------- #
        plot_container = ctk.CTkFrame(center, corner_radius=0, fg_color=col("app"))
        plot_container.grid(row=3, column=0, sticky="nsew", padx=20, pady=18)

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
        self.mpl_toolbar.on_margins_applied = self._on_margins_applied
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

    # ------------------------------------------------------------------ #
    # Plot tabs -- several independent plots held in memory at once
    # ------------------------------------------------------------------ #
    def _build_tab_strip(self, parent) -> None:
        strip = ctk.CTkFrame(parent, height=36, corner_radius=0, fg_color=col("panel"))
        strip.grid(row=0, column=0, sticky="ew")
        strip.pack_propagate(False)   # children are packed; see _build_topbar
        Rule(strip).pack(side="bottom", fill="x")

        self.tab_strip_row = ctk.CTkFrame(strip, fg_color="transparent")
        self.tab_strip_row.pack(side="left", fill="both", expand=True, padx=(12, 0))

        ghost_button(strip, "+", self._add_tab, width=30, height=26
                     ).pack(side="left", padx=8, pady=5)

        self._refresh_tab_strip()

    def _refresh_tab_strip(self) -> None:
        """Rebuild the tab chips from `self.plot_tabs`/`self.active_tab`."""
        for child in self.tab_strip_row.winfo_children():
            child.destroy()

        for i, tab in enumerate(self.plot_tabs):
            active = i == self.active_tab
            chip = ctk.CTkFrame(self.tab_strip_row, corner_radius=0, border_width=1,
                                fg_color=col("accent") if active else "transparent",
                                border_color=col("accent") if active else col("border"))
            chip.pack(side="left", padx=(0, 4), pady=5)

            label = ctk.CTkLabel(
                chip, text=tab.name, font=font("label"), cursor="hand2",
                text_color=col("on_accent") if active else col("fg_muted"))
            label.pack(side="left", padx=(10, 6), pady=4)
            label.bind("<Button-1>", lambda _e, idx=i: self._switch_tab(idx))
            label.bind("<Double-Button-1>", lambda _e, idx=i: self._rename_tab(idx))
            chip.bind("<Button-1>", lambda _e, idx=i: self._switch_tab(idx))

            if len(self.plot_tabs) > 1:
                close = ctk.CTkLabel(
                    chip, text="✕", font=font("label"), cursor="hand2",
                    text_color=col("on_accent") if active else col("fg_faint"))
                close.pack(side="left", padx=(0, 8))
                close.bind("<Button-1>", lambda _e, idx=i: self._close_tab(idx))

    def _gather_plot_state(self) -> dict:
        """
        Everything that belongs to ONE plot: its settings plus its loaded
        signals -- exactly the shape a whole saved session used to carry at
        its top level. Used both to build the session file (`_gather_state`)
        and to snapshot a tab before switching away from it.
        """
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
                "name": sig.name, "display_name": sig.display_name,
                "legend_label": sig.legend_label,
                "domain": sig.domain, "y_kind": sig.y_kind,
                "unit_t_in": sig.unit_t_in, "unit_v_in": sig.unit_v_in,
                "t_offset": sig.t_offset, "v_offset": sig.v_offset,
                "gain": sig.gain, "invert": sig.invert, "linestyle": sig.linestyle,
                "marker": sig.marker, "marker_size": sig.marker_size,
                "marker_hollow": sig.marker_hollow,
                "color": sig.color, "secondary_y": sig.secondary_y,
                "visible": sig.visible,
                "line_width": self._lw(uid),
            })

        return {"settings": settings, "signals": signals,
                "manual_margins": self._manual_margins,
                # Cursors/annotations live in ONE shared CursorManager/
                # AnnotationManager instance for the whole app (see __init__),
                # since `update_plot()` re-attaches them to whatever axes it
                # just rebuilt on every redraw -- they were never scoped to a
                # tab on their own. Snapshotting them here, and reloading them
                # in `_apply_plot_state`, is what makes each tab keep its own
                # cursors/annotations instead of the same set bleeding into
                # whichever tab happens to be on screen.
                "cursors": self.cursors.to_dict(),
                "annotations": self.annotations.to_dict()}

    def _tab_persisted_vars(self) -> dict[str, "ctk.Variable"]:
        """
        Same as `_persisted_vars()`, minus settings that are app-wide rather
        than tied to one particular plot -- currently just the light/dark
        theme, which would be jarring to flip every time the user switches
        tabs. (A tab's stored `settings` dict still happens to carry a
        `theme_mode` value from whenever it was last gathered; it is simply
        never read back out through this map.)
        """
        return {k: v for k, v in self._persisted_vars().items() if k != "theme_mode"}

    def _apply_plot_state(self, data: dict, restore_theme: bool = False,
                          history: Optional[History] = None,
                          anchor_dir: Optional[str] = None) -> None:
        """
        Load ONE plot's settings + signals as the live state, replacing
        whatever was previously on screen. Shared by whole-session restore
        (`restore_theme=True`, since there is no "previous tab" to keep the
        app-wide theme from) and by tab switching (`restore_theme=False`).

        `history` lets a caller hand back a tab's OWN previously-saved undo
        stack (see `PlotTab.history`) instead of always starting fresh: this
        used to unconditionally do `self.history = History()` here, which
        meant switching tabs (or closing the active one, which switches to
        its neighbour) silently wiped that tab's undo/redo every time --
        Ctrl+Z right after switching back to a tab you had just been editing
        did nothing, because its history was gone. `None` (a genuinely new
        or restored-from-session tab, which has no meaningful prior history)
        still gets a fresh `History()`.

        `anchor_dir` is forwarded to `_restore_signal` for each signal
        record -- the folder to resolve a moved/renamed `source_path`
        against (see `core.data_io.resolve_source_path`).
        """
        settings = data.get("settings", {}) or {}
        varmap = self._persisted_vars() if restore_theme else self._tab_persisted_vars()
        for key, var in varmap.items():
            if key in settings:
                value = settings[key]
            elif key in self._default_settings:
                # Not part of this state (e.g. a freshly-created empty tab,
                # or an older session predating some field): fall back to
                # the startup default instead of leaving whatever the
                # previously active tab last set.
                value = self._default_settings[key]
            else:
                continue
            try:
                var.set(value)
            except Exception:
                pass

        margins = data.get("manual_margins")
        self._manual_margins = margins if isinstance(margins, dict) else None

        self.signals = {}
        self.signal_order = []
        self._signal_columns = {}
        self.line_widths = {}
        self.selected_uid = None
        for record in data.get("signals", []):
            self._restore_signal(record, anchor_dir)

        self._sync_unit_options()
        self._refresh_signal_list()
        self._refresh_xy_combos()
        self._build_param_placeholder()

        # Swap in THIS tab's own cursors/annotations -- `from_dict` clears
        # whatever the previously active tab had left in the shared managers
        # first, so a tab with none of its own starts clean instead of still
        # showing the last tab's cursors/annotations (see `_gather_plot_state`).
        self.cursors.from_dict(data.get("cursors") or {})
        self.annotations.from_dict(data.get("annotations") or {})

        self._plot_suspended = True
        try:
            if restore_theme and "theme_mode" in settings:
                self._on_theme_change(settings["theme_mode"])
            if ("font_family" in settings or "font_size" in settings
                    or "legend_font_size" in settings):
                self._on_font_change()
            self._on_mode_change()
        finally:
            self._plot_suspended = False

        self.history = history if history is not None else History()
        self.update_plot()

    def _switch_tab(self, index: int) -> None:
        if not (0 <= index < len(self.plot_tabs)) or index == self.active_tab:
            return
        self.plot_tabs[self.active_tab].state = self._gather_plot_state()
        self.plot_tabs[self.active_tab].history = self.history
        self.active_tab = index
        self._apply_plot_state(self.plot_tabs[index].state,
                               history=self.plot_tabs[index].history,
                               anchor_dir=getattr(self, "_session_anchor_dir", None))
        self._refresh_tab_strip()

    def _next_tab_name(self) -> str:
        """
        Smallest "Gráfico N" not currently in use by any open tab.

        Previously this was `len(self.plot_tabs) + 1`: a plain tab COUNT,
        not a name lookup. Close or rename "Gráfico 1" and every future new
        tab still starts counting from however many tabs happen to be open
        right now -- "Gráfico 1" is never offered again, and closing tabs
        down and adding new ones can even hand out an already-used name
        (e.g. close #1 with two tabs open, add one: count-based naming
        gives "Gráfico 2" again, colliding with the tab that survived).
        Picking the lowest free number instead reuses "Gráfico 1" as soon
        as it's free and never repeats a name still in use.
        """
        template = t("Gráfico {n}")
        prefix, _, suffix = template.partition("{n}")
        pattern = re.compile(re.escape(prefix) + r"(\d+)" + re.escape(suffix) + r"$")
        used = set()
        for tab in self.plot_tabs:
            m = pattern.match(tab.name or "")
            if m:
                used.add(int(m.group(1)))
        n = 1
        while n in used:
            n += 1
        return template.format(n=n)

    def _add_tab(self) -> None:
        self.plot_tabs[self.active_tab].state = self._gather_plot_state()
        self.plot_tabs[self.active_tab].history = self.history
        name = self._next_tab_name()
        self.plot_tabs.append(tabs.PlotTab(name=name))
        self.active_tab = len(self.plot_tabs) - 1
        self._apply_plot_state(self.plot_tabs[self.active_tab].state)
        self._refresh_tab_strip()

    def _close_tab(self, index: int) -> None:
        if len(self.plot_tabs) <= 1 or not (0 <= index < len(self.plot_tabs)):
            return
        name = self.plot_tabs[index].name
        if not messagebox.askyesno(
                t("Cerrar pestaña"),
                t("¿Cerrar «{name}»? Se pierden sus señales y ajustes.").format(name=name)):
            return

        was_active = index == self.active_tab
        del self.plot_tabs[index]
        if index < self.active_tab:
            self.active_tab -= 1
        self.active_tab = max(0, min(self.active_tab, len(self.plot_tabs) - 1))

        if was_active:
            self._apply_plot_state(self.plot_tabs[self.active_tab].state,
                                   history=self.plot_tabs[self.active_tab].history)
        self._refresh_tab_strip()

    def _rename_tab(self, index: int) -> None:
        def _submit(name: str) -> None:
            self.plot_tabs[index].name = name
            self._refresh_tab_strip()

        TextPrompt(self, t("Renombrar pestaña"), t("Nombre de la pestaña:"),
                  initial=self.plot_tabs[index].name, on_submit=_submit)

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

        ctk.CTkLabel(parent, text=t("Leyenda"), font=font("label"),
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
            self._open_overlay_window(tab="annotations")
            self.tool_hint.configure(
                text="Definí la anotación en el panel y capturá el punto.")
        else:
            self.tool_hint.configure(text="")

    def _on_margins_applied(self, margins: Optional[dict]) -> None:
        """
        Remember margins chosen by hand, or return to automatic layout when
        the dialog is reset. Stored rather than applied once, because
        `update_plot` has to reassert them after every redraw.
        """
        self._manual_margins = margins
        self._save_session_soon()

    def _apply_settings(self) -> None:
        """
        Redraw with the current settings, leaving the manual margins alone.

        Separate from the per-trace "Aplicar cambios" button on purpose: that
        one commits edits to a trace, this one only re-renders, so neither
        can surprise you by doing the other's job.
        """
        self.update_plot()
        self.status_label.configure(text=t("Ajustes aplicados."))

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
                    rows.append((f"   {label[:16]}", text or t("sin cruce")))
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

        self.settings = SectionGroup()
        self._sections_header = SectionHeader(
            body, t("Ajustes"), action=t("Minimizar todo"),
            command=self._toggle_all_sections)
        self._sections_header.pack(fill="x")
        Rule(body, strong=True).pack(fill="x", pady=(6, 12))
        ghost_button(body, t("Aplicar ajustes"), self._apply_settings,
                     height=28).pack(fill="x", pady=(0, 14))
        self._build_axes_section(body)
        self._build_labels_section(body)
        self._build_legend_section(body)
        self._build_data_section(body)
        self._build_export_section(body)

    def _section(self, parent, title: str, expanded: bool = True):
        """One settings group, foldable from the caret in its header."""
        def _on_toggle(_s) -> None:
            self._save_session_soon()
            self._refresh_toggle_all_label()

        section = StaticSection(parent, title, expanded=expanded, on_toggle=_on_toggle)
        section.pack(fill="x", pady=(0, 12))
        self.settings.add(section)
        return section.body

    def _toggle_all_sections(self) -> None:
        self.settings.set_all(not self.settings.any_expanded())
        self._refresh_toggle_all_label()
        self._save_session_soon()

    def _refresh_toggle_all_label(self) -> None:
        """
        "Minimizar todo"/"Expandir todo": an `_EN` entry for "Expandir todo"
        already existed but nothing ever showed it -- the header's action
        label was set once at build time and never touched again, so the
        the "collapse everything" link never flipped to "expand everything"
        once every section was actually collapsed (whether via this link or
        by folding sections one by one).
        """
        label = t("Minimizar todo") if self.settings.any_expanded() else t("Expandir todo")
        self._sections_header.action_label.configure(text=label)

    def _build_axes_section(self, parent) -> None:
        box = self._section(parent, t("Ejes y escalas"), expanded=True)

        self.unit_x_var = ctk.StringVar(value="us")
        self.unit_x_combo = combo_field(box, t("Unidad X"), self.unit_x_var,
                                         ["s", "ms", "us", "ns"], width=110)
        self.unit_y_var = ctk.StringVar(value="V")
        self.unit_y_combo = combo_field(box, t("Unidad Y1"), self.unit_y_var,
                                         ["V", "mV"], width=110)
        # Y2 carries its own unit: the secondary axis usually holds a
        # different quantity altogether (phase in degrees against magnitude
        # in dB), so forcing both to share one unit was simply wrong.
        self.unit_y2_var = ctk.StringVar(value="V")
        self.unit_y2_combo = combo_field(box, t("Unidad Y2"), self.unit_y2_var,
                                          ["V", "mV"], width=110)

        self.xscale_var = ctk.StringVar(value="linear")
        segmented_field(box, t("Escala X"), SCALES, self.xscale_var,
                        labels=_labels()["scale"],
                        command=lambda _v: self.update_plot())
        self.yscale_var = ctk.StringVar(value="linear")
        self.yscale_seg = segmented_field(box, t("Escala Y"), SCALES,
                                           self.yscale_var,
                                           labels=_labels()["scale"],
                                           command=lambda _v: self.update_plot())

        self.xmin_var = ctk.StringVar(value="")
        entry_field(box, t("X mín"), self.xmin_var, on_enter=self.update_plot)
        self.xmax_var = ctk.StringVar(value="")
        entry_field(box, t("X máx"), self.xmax_var, on_enter=self.update_plot)
        hint(box, t("Vacío = sin límite, en la unidad X elegida."),
             wraplength=280).pack(fill="x", pady=(0, 10))

        self.engineering_ticks_var = ctk.BooleanVar(value=True)
        check_field(box, t("Notación de ingeniería"), self.engineering_ticks_var,
                    command=self.update_plot)
        self.grid_var = ctk.BooleanVar(value=True)
        check_field(box, t("Grilla"), self.grid_var, command=self.update_plot)
        self.minor_grid_var = ctk.BooleanVar(value=False)
        check_field(box, t("Grilla menor"), self.minor_grid_var,
                    command=self.update_plot, rule=False)

    def _build_labels_section(self, parent) -> None:
        box = self._section(parent, t("Textos y fuente"), expanded=False)

        self.title_var = ctk.StringVar(value="")
        stacked_entry(box, t("Título"), self.title_var, on_enter=self.update_plot)
        self.xlabel_var = ctk.StringVar(value="")
        entry = stacked_entry(box, t("Etiqueta X"), self.xlabel_var,
                              on_enter=self.update_plot)
        entry.bind("<KeyRelease>", lambda _e: self._mark_labels_dirty())
        self.ylabel_var = ctk.StringVar(value="")
        entry = stacked_entry(box, t("Etiqueta Y"), self.ylabel_var,
                              on_enter=self.update_plot)
        entry.bind("<KeyRelease>", lambda _e: self._mark_labels_dirty())
        self.ylabel2_var = ctk.StringVar(value="Fase [$^\\circ$]")
        stacked_entry(box, t("Etiqueta Y2"), self.ylabel2_var, on_enter=self.update_plot)
        hint(box, t("Aceptan mathtext: $V_{out}$, $^\\circ$."),
             wraplength=280).pack(fill="x", pady=(0, 12))

        combo_field(box, t("Fuente"), self.font_family_var,
                    list(FONT_FAMILIES.keys()), width=150,
                    command=lambda _=None: self._on_font_change())
        entry_field(box, t("Tamaño de fuente"), self.font_size_var, suffix="pt",
                    on_enter=self._on_font_change)
        hint(box, t("Afecta ejes y ticks; el título usa un punto más. La leyenda "
                    "usa un punto menos salvo que se le fije un tamaño propio "
                    "en la sección «Leyenda»."),
             wraplength=280).pack(fill="x", pady=(0, 12))

        self.theme_mode_var = ctk.StringVar(value="light")
        segmented_field(box, t("Tema"), THEMES, self.theme_mode_var,
                        labels=_labels()["theme"],
                        command=self._on_theme_change, width=64)

        self.language_var = ctk.StringVar(value=get_language())
        segmented_field(box, t("Idioma"), list(LANGUAGES), self.language_var,
                        labels=LANGUAGES, command=self._on_language_change,
                        width=76)
        ghost_button(box, t("Márgenes del gráfico..."),
                     self.mpl_toolbar.configure_subplots).pack(fill="x", pady=(4, 0))

    def _build_legend_section(self, parent) -> None:
        box = self._section(parent, t("Leyenda"), expanded=False)

        self.legend_var = ctk.BooleanVar(value=True)
        check_field(box, t("Mostrar leyenda"), self.legend_var, command=self.update_plot)

        self.legend_pos_var = ctk.StringVar(value="upper right")
        combo_field(box, t("Posición"), self.legend_pos_var, LEGEND_POSITIONS,
                    width=170, command=lambda _=None: self.update_plot())

        self.legend_x_var = ctk.StringVar(value="1.02")
        entry_field(box, t("X (fracción)"), self.legend_x_var, on_enter=self.update_plot)
        self.legend_y_var = ctk.StringVar(value="1.00")
        entry_field(box, t("Y (fracción)"), self.legend_y_var, on_enter=self.update_plot)

        self.legend_corner_var = ctk.StringVar(value="upper left")
        combo_field(box, t("Punto de anclaje"), self.legend_corner_var, CUSTOM_ANCHOR_CORNERS,
                    width=150, command=lambda _=None: self.update_plot())

        self.legend_ncol_var = ctk.StringVar(value="1")
        entry_field(box, t("Columnas"), self.legend_ncol_var, width=56,
                    on_enter=self.update_plot)

        self.legend_frameon_var = ctk.BooleanVar(value=True)
        check_field(box, t("Marco de la leyenda"), self.legend_frameon_var,
                    command=self.update_plot)

        self.legend_title_var = ctk.StringVar(value="")
        stacked_entry(box, t("Título de la leyenda"), self.legend_title_var,
                      on_enter=self.update_plot)

        self.legend_font_size_var = ctk.StringVar(value="")
        entry_field(box, t("Tamaño de fuente"), self.legend_font_size_var, suffix="pt",
                    on_enter=self._on_font_change)
        hint(box, t("Vacío = un punto menos que el tamaño de fuente general."),
             wraplength=280).pack(fill="x", pady=(0, 8))

        # Free-text rows appended to the legend with no curve behind them --
        # for the component values and test conditions that belong in the
        # legend box of a report figure but are not a plotted series.
        stacked_label(box, t("Líneas de texto extra"))
        self.legend_extra_box = ctk.CTkTextbox(box, height=64, font=font("body"),
                                               wrap="none")
        self.legend_extra_box.pack(fill="x", pady=(0, 4))
        self.legend_extra_box.bind(
            "<FocusOut>", lambda _e: self.update_plot())
        hint(box, t("Una por línea. Se agregan al final de la leyenda sin "
                    "curva asociada."), wraplength=280).pack(fill="x", pady=(0, 8))
        hint(box, t("X/Y y anclaje sólo aplican con «personalizada (x, y)». "
                  "Fuera de [0, 1] la leyenda sale del área del gráfico."),
             wraplength=280).pack(fill="x", pady=(8, 0))

    def _build_data_section(self, parent) -> None:
        box = self._section(parent, t("Datos"), expanded=False)

        self.dec_mode_var = ctk.StringVar(value="none")
        self.dec_mode_combo = combo_field(
            box, t("Reducir puntos"), self.dec_mode_var, DEC_MODES, width=150,
            labels=_labels()["dec"], command=lambda _=None: self.update_plot())
        self.dec_value_var = ctk.StringVar(value="1000")
        entry_field(box, t("Valor"), self.dec_value_var, on_enter=self.update_plot)
        hint(box, t("«Factor N» conserva 1 de cada N muestras; «Máx. puntos» "
                  "reduce hasta esa cantidad."), wraplength=280).pack(fill="x", pady=(8, 10))

        # Display-only cap: keeps redraws fast on multi-million-sample
        # captures without ever touching what gets exported.
        self.max_points_var = ctk.StringVar(value="20k")
        entry_field(box, t("Máx. puntos en pantalla"), self.max_points_var,
                    on_enter=self.update_plot, rule=False, label_width=132)
        hint(box, t("Sólo afecta el dibujo en pantalla; la exportación siempre "
                  "usa todos los puntos. 0 = sin límite."),
             wraplength=280).pack(fill="x", pady=(8, 0))

        self.decimal_comma_check = check_field(
            box, t("Archivos con coma decimal"), self.decimal_comma_var, rule=False)

    def _build_export_section(self, parent) -> None:
        box = self._section(parent, t("Exportar"), expanded=True)

        stacked_label(box, t("Perfil de exportación"))
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
        ghost_button(profile_actions, t("Guardar como..."), self._save_export_profile,
                     width=140).pack(side="left")
        ghost_button(profile_actions, t("Eliminar"), self._delete_export_profile,
                     width=90).pack(side="left", padx=6)
        self._refresh_export_profiles()
        Rule(box).pack(fill="x", pady=(0, 12))

        self.csv_mode_container = ctk.CTkFrame(box, fg_color="transparent")
        self.csv_mode_container.pack(fill="x", pady=(0, 8))
        stacked_label(self.csv_mode_container, t("Datos para PGFPlots"))
        self.csv_mode_var = ctk.StringVar(value="individual")
        self.csv_mode_combo = LabeledCombo(
            self.csv_mode_container, CSV_MODES, self.csv_mode_var,
            labels=_labels()["csv"], height=28)
        self.csv_xy_note = hint(self.csv_mode_container,
                                 t("Modo X/Y: se exporta la curva actual."),
                                 wraplength=280)
        self.csv_mode_combo.pack(fill="x")   # default (non-XY) state
        ghost_button(box, t("Exportar CSV..."), self._export_csv,
                     height=30).pack(fill="x", pady=(8, 14))

        Rule(box).pack(fill="x", pady=(0, 12))

        self.fig_format_var = ctk.StringVar(value="pdf")
        combo_field(box, t("Formato"), self.fig_format_var,
                    ["pdf", "png", "svg", "pgf"], width=110)
        self.dpi_var = ctk.StringVar(value="300")
        entry_field(box, "DPI", self.dpi_var, rule=False)
        hint(box, t("PDF, SVG y PGF son vectoriales; el DPI sólo afecta al PNG."),
             wraplength=280).pack(fill="x", pady=(6, 10))
        primary_button(box, t("Exportar figura..."), self._export_figure,
                       height=32).pack(fill="x")
        ghost_button(box, t("Importar figura..."), self._import_figure,
                    height=28).pack(fill="x", pady=(6, 0))
        hint(box, t("Recupera una figura exportada antes con TODOS sus ajustes "
                    "y señales, en una pestaña nueva. Necesita el archivo "
                    "«.labplotter.json» que se guarda junto a la figura."),
             wraplength=280).pack(fill="x", pady=(4, 0))

        Rule(box).pack(fill="x", pady=(14, 12))

        stacked_label(box, t("Tablero (varias figuras en un mismo layout)"))
        self.board_title_var = ctk.StringVar(value="")
        stacked_entry(box, t("Título del panel"), self.board_title_var)
        board_actions = ctk.CTkFrame(box, fg_color="transparent")
        board_actions.pack(fill="x", pady=(0, 8))
        ghost_button(board_actions, t("+ Agregar gráfico actual"), self._add_current_to_board,
                    width=170).pack(side="left")
        ghost_button(board_actions, t("Ver tablero..."), self._open_board_window,
                    width=100).pack(side="left", padx=(6, 0))
        self.board_status_label = hint(box, t("Tablero vacío."), wraplength=280)
        self.board_status_label.pack(fill="x")

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

        TextPrompt(self, t("Guardar perfil"),
                  t("Nombre del perfil (formato, DPI y modo CSV actuales):"),
                  on_submit=_submit)

    def _delete_export_profile(self) -> None:
        name = self.profile_var.get()
        if not name:
            return
        if not messagebox.askyesno(t("Eliminar perfil"),
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
            title=t("Seleccionar archivos de datos"),
            filetypes=[("CSV/TXT", "*.csv *.txt"), (t("Todos los archivos"), "*.*")])
        if not paths:
            return
        self._ingest_files(paths)

    def _ingest_files(self, paths) -> None:
        """
        Shared loading path for both "+ Abrir archivo" and dropping files
        onto the window: everything from here down used to live directly in
        `_load_files`, which only ever supplied paths from the file dialog.
        """
        self._record(t("Abrir archivo(s)"))
        loaded = 0
        for path in paths:
            try:
                df, col_kind = read_table(path, decimal_comma=self.decimal_comma_var.get())
            except Exception as exc:
                messagebox.showerror(t("Error al leer archivo"),
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
                    messagebox.showerror(t("Error al procesar columna"), f"{name}:\n{exc}")
                    continue
                if sig.t_raw.size == 0:
                    messagebox.showwarning(
                        t("Columna vacía"),
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
            messagebox.showinfo(t("Sin selección"),
                                t("Seleccioná primero una señal de la lista."))
            return
        signal = self.signals.get(self.selected_uid)
        name = signal.name if signal is not None else ""
        if not messagebox.askyesno(
                t("Quitar traza"),
                f"{t('¿Quitar la traza')} «{name}»?\n\n"
                f"{t('Esta acción se puede deshacer con Ctrl+Z.')}"):
            return
        self._record(f"{t('Quitar traza')} «{name}»")
        self.signals.pop(self.selected_uid, None)
        self._signal_columns.pop(self.selected_uid, None)
        self.line_widths.pop(self.selected_uid, None)
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
                t("Quitar todas las trazas"),
                f"{t('¿Eliminar las')} {len(self.signals)} {t('trazas cargadas?')}\n\n"
                f"{t('Esta acción se puede deshacer con Ctrl+Z.')}"):
            return
        self._record(t("Quitar todas las trazas"))
        self.signals.clear()
        self.signal_order.clear()
        self._signal_columns.clear()
        self.line_widths.clear()
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
                 t("Sin trazas. Abrí un archivo para empezar."),
                 wraplength=240).pack(fill="x", pady=14)
            self._refresh_chips()
            return

        for uid in self.signal_order:
            sig = self.signals[uid]
            tag = {"dB": "dB", "deg": "fase"}.get(sig.y_kind, "V")
            # A missing trace (source file not found -- see `_restore_signal`)
            # gets a "⚠" marker in its label and is clicked to reconnect
            # (`_select_signal`) instead of opening the normal param panel.
            label = sig.display_name or sig.name
            row = TraceRow(
                self.signal_list_frame, name=f"⚠ {label}" if sig.missing else label,
                color=sig.color or "#8A8A8A", tag=tag, visible=sig.visible,
                on_select=lambda u=uid: self._select_signal(u),
                on_toggle=lambda value, u=uid: self._toggle_signal(u, value),
                on_color=lambda u=uid: self._pick_row_color(u),
                on_move_up=lambda u=uid: self._move_signal(u, -1),
                on_move_down=lambda u=uid: self._move_signal(u, 1))
            row.pack(fill="x", pady=1)
            self.row_widgets[uid] = {"row": row}

        self._highlight_selected()
        self._refresh_chips()

    def _move_signal(self, uid: str, delta: int) -> None:
        """
        Move a trace up/down in `signal_order` -- this is also the order
        curves are plotted in (see `_gather_curves`), so it is what
        actually reorders the legend entries, not just the list on screen.
        """
        if uid not in self.signal_order:
            return
        i = self.signal_order.index(uid)
        j = i + delta
        if not (0 <= j < len(self.signal_order)):
            return
        self._record(t("Reordenar traza"))
        self.signal_order[i], self.signal_order[j] = self.signal_order[j], self.signal_order[i]
        self._refresh_signal_list()   # also re-highlights the selected row
        self.update_plot()

    def _toggle_signal(self, uid: str, visible: bool) -> None:
        signal = self.signals.get(uid)
        if signal is None:
            return
        self._record(t("Mostrar/ocultar traza"))
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
                                                     title=f"Color de {sig.display_name or sig.name}")
        except tk.TclError:
            _rgb, hex_color = colorchooser.askcolor(parent=self, title=f"Color de {sig.display_name or sig.name}")
        if not hex_color:
            return
        self._record(t("Cambiar color"))
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
        sig = self.signals.get(uid)
        if sig is not None and sig.missing:
            self._relink_signal(uid)
            return
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

        # `next(iter(...))`, not `.pop()`: `kinds` is a set and `.pop()`
        # mutates it in place, removing the very element it returns -- when
        # every loaded signal shared one kind (e.g. all "dB" in a Bode
        # session), `kinds` was already empty two lines below at
        # `(kinds or {"voltage"})`, so Y2's unit list silently fell back to
        # voltage-only units instead of the dB/deg units actually in use.
        y_kind = next(iter(kinds)) if len(kinds) == 1 else "voltage"
        y_values = list(y_units_for_kind(y_kind).keys())
        self.unit_y_combo.configure(values=y_values)
        if self.unit_y_var.get() not in y_values:
            self.unit_y_var.set(y_values[0])

        # Y2 offers every unit any loaded signal could need, since the
        # secondary axis often holds a different quantity from Y1.
        y2_values = sorted({u for kind in (kinds or {"voltage"})
                            for u in y_units_for_kind(kind)} | set(y_values))
        self.unit_y2_combo.configure(values=y2_values)
        if self.unit_y2_var.get() not in y2_values:
            self.unit_y2_var.set(y2_values[0])

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

        SectionHeader(self.param_frame, t("Ajustes de la traza")).pack(fill="x")
        Rule(self.param_frame).pack(fill="x", pady=(6, 12))

        legend_var = ctk.StringVar(value=sig.legend_label or "")
        stacked_entry(self.param_frame, t("Cómo aparece en la leyenda"), legend_var)

        # --- colour ---------------------------------------------------- #
        stacked_label(self.param_frame, t("Color"))
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
        stacked_label(self.param_frame, t("Estilo de línea"))
        style_var = ctk.StringVar(value=sig.linestyle)

        def _on_style_change(value: str) -> None:
            # Turning the line off with no marker set would just make the
            # trace disappear -- pick a visible marker automatically so
            # "sin línea" always shows the data points, with nothing to
            # add by hand.
            if value == "None" and marker_var.get() == "None":
                marker_var.set("o")

        Segmented(self.param_frame, LINESTYLES, style_var, labels=LINE_GLYPHS,
                  command=_on_style_change, width=56).pack(fill="x", pady=(0, 12))
        hint(self.param_frame, t("«Sin línea» (el último botón) grafica sólo "
                                 "los puntos, sin interpolar entre ellos."),
             wraplength=240).pack(fill="x", pady=(0, 12))

        # --- marker ------------------------------------------------------ #
        marker_var = ctk.StringVar(value=sig.marker or "None")
        combo_field(self.param_frame, t("Marcador"), marker_var, MARKERS,
                    width=170, labels=_labels()["marker"])
        marker_size_var = ctk.StringVar(value=f"{sig.marker_size:g}")
        entry_field(self.param_frame, t("Tamaño de marcador"), marker_size_var,
                    suffix="pt", label_width=140)
        marker_hollow_var = ctk.BooleanVar(value=sig.marker_hollow)
        check_field(self.param_frame, t("Marcador hueco"), marker_hollow_var)
        hint(self.param_frame, t("Hueco = sólo el borde, con el color de la traza; "
                                 "sin relleno."),
             wraplength=240).pack(fill="x", pady=(0, 12))

        # --- line weight ------------------------------------------------ #
        width_var = ctk.StringVar(value=f"{self._lw(uid):.1f}")
        SliderField(self.param_frame, t("Grosor de traza"), width_var,
                    minimum=0.4, maximum=5.0, steps=46, decimals=1,
                    label_width=112,
                    on_change=lambda u=uid, v=width_var: self._preview_width(u, v)
                    ).pack(fill="x", pady=(0, 10))

        # --- which Y axis ---------------------------------------------- #
        stacked_label(self.param_frame, t("Eje vertical"))
        axis_var = ctk.StringVar(value="secondary" if sig.secondary_y
                                 else "primary")
        Segmented(self.param_frame, AXIS_SIDES, axis_var,
                  labels=_labels()["side"], width=56).pack(fill="x", pady=(0, 4))
        hint(self.param_frame, t("«Der» usa un eje Y2 con escala propia."),
             wraplength=240).pack(fill="x", pady=(0, 12))

        # --- corrections ------------------------------------------------ #
        corrections = StaticSection(self.param_frame, t("Correcciones"))
        corrections.pack(fill="x", pady=(4, 8))
        box = corrections.body

        x_unit_now, y_unit_now = sig.unit_t_in, sig.unit_v_in
        # The suffix next to each offset field must track the "Unidad X/Y"
        # combo below (`unit_x_in_var`/`unit_y_in_var`) live: a static suffix
        # kept showing the unit that was active when the panel was built, so
        # changing the unit combo *without* retouching the offset field
        # silently reinterpreted the same typed number in the new unit at
        # "Aplicar cambios" time (e.g. "5" meant as 5 ms became 5 s). See the
        # `_on_unit_x_change`/`_on_unit_y_change` traces below, which keep
        # both the suffix and the numeric value in sync with the combo.
        xoff_suffix_var = ctk.StringVar(value=x_unit_now)
        xoff_var = ctk.StringVar(
            value=f"{sig.t_offset / x_units_for_domain(sig.domain)[x_unit_now]:g}")
        entry_field(box, t("Desplazar en X"), xoff_var, suffix_var=xoff_suffix_var,
                    label_width=104)
        yoff_suffix_var = ctk.StringVar(value=y_unit_now)
        yoff_var = ctk.StringVar(
            value=f"{sig.v_offset / y_units_for_kind(sig.y_kind)[y_unit_now]:g}")
        entry_field(box, t("Desplazar en Y"), yoff_var, suffix_var=yoff_suffix_var,
                    label_width=104)
        gain_var = ctk.StringVar(value=f"{sig.gain:g}")
        entry_field(box, t("Ganancia"), gain_var, suffix="×", label_width=104)
        hint(box, t("Sólo tiene efecto en señales de tipo «voltage»: escalar "
                    "el valor de una traza en dB o fase no tiene sentido "
                    "físico (para eso está «Desplazar en Y»)."),
             wraplength=240).pack(fill="x", pady=(0, 8))
        invert_var = ctk.BooleanVar(value=sig.invert)
        check_field(box, t("Invertir (×−1)"), invert_var, rule=False)

        # --- source metadata --------------------------------------------- #
        source = StaticSection(self.param_frame, t("De dónde salen los datos"))
        source.pack(fill="x", pady=(0, 8))
        box = source.body

        name_var = ctk.StringVar(value=sig.name)
        stacked_entry(box, t("Nombre"), name_var)
        hint(box, t("Se usa en la leyenda y en los ejes por defecto "
                    "si no hay etiqueta de leyenda propia."),
             wraplength=240).pack(fill="x", pady=(0, 10))

        alias_var = ctk.StringVar(value=sig.display_name or "")
        stacked_entry(box, t("Alias en la lista"), alias_var)
        hint(box, t("Solo cambia cómo se ve en la lista de trazas; nunca "
                    "aparece en el gráfico ni en la leyenda."),
             wraplength=240).pack(fill="x", pady=(0, 10))

        domain_var = ctk.StringVar(value=sig.domain)
        combo_field(box, t("Dominio"), domain_var, ["time", "freq"], width=110)
        ykind_var = ctk.StringVar(value=sig.y_kind)
        combo_field(box, t("Magnitud"), ykind_var, ["voltage", "dB", "deg"], width=110)

        unit_x_in_var = ctk.StringVar(value=sig.unit_t_in)
        unit_x_combo = combo_field(box, t("Unidad X"), unit_x_in_var,
                                    list(x_units_for_domain(sig.domain).keys()),
                                    width=110)
        unit_y_in_var = ctk.StringVar(value=sig.unit_v_in)
        unit_y_combo = combo_field(box, t("Unidad Y"), unit_y_in_var,
                                    list(y_units_for_kind(sig.y_kind).keys()),
                                    width=110, rule=False)
        hint(box, t("Unidad en la que vienen los datos del archivo."),
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

        # Keep the offset fields' displayed number and suffix meaning the
        # same physical shift when "Unidad X"/"Unidad Y" changes -- without
        # this, the value typed while the combo showed e.g. "ms" would be
        # silently re-read as "s" (or whatever unit is selected at "Aplicar
        # cambios" time), a 1000x error the field's frozen suffix used to hide.
        _prev_x_unit = {"value": x_unit_now}
        _prev_y_unit = {"value": y_unit_now}

        def _on_unit_x_change(*_a):
            new_unit = unit_x_in_var.get()
            old_unit = _prev_x_unit["value"]
            if new_unit != old_unit:
                units = x_units_for_domain(domain_var.get())
                if old_unit in units and new_unit in units:
                    current = _parse_float(xoff_var.get(), 0.0)
                    xoff_var.set(f"{current * units[old_unit] / units[new_unit]:g}")
                xoff_suffix_var.set(new_unit)
            _prev_x_unit["value"] = new_unit

        def _on_unit_y_change(*_a):
            new_unit = unit_y_in_var.get()
            old_unit = _prev_y_unit["value"]
            if new_unit != old_unit:
                units = y_units_for_kind(ykind_var.get())
                if old_unit in units and new_unit in units:
                    current = _parse_float(yoff_var.get(), 0.0)
                    yoff_var.set(f"{current * units[old_unit] / units[new_unit]:g}")
                yoff_suffix_var.set(new_unit)
            _prev_y_unit["value"] = new_unit

        unit_x_in_var.trace_add("write", _on_unit_x_change)
        unit_y_in_var.trace_add("write", _on_unit_y_change)

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
                    t("Color inválido"),
                    t("'{color}' no es un color válido. Usá formato hex (#RRGGBB) "
                      "o el selector gráfico.").format(color=color))
                return

            self._record(t("Aplicar cambios"))
            sig.name = name_var.get().strip() or sig.name
            sig.display_name = alias_var.get().strip() or None
            sig.legend_label = legend_var.get().strip() or None
            sig.domain = new_domain
            sig.y_kind = new_kind
            sig.unit_t_in = new_ux
            sig.unit_v_in = new_uy
            sig.t_offset = _parse_float(xoff_var.get(), 0.0) * x_units[new_ux]
            sig.v_offset = _parse_float(yoff_var.get(), 0.0) * y_units[new_uy]
            # `gain` multiplies the raw value (see Signal.processed() in
            # core/data_io.py) -- physically correct for a "voltage" trace
            # (amplitude scaling / probe attenuation), but meaningless for
            # "dB" or "deg": multiplying a decibel or a phase-degree number
            # by a factor is not the same as scaling the underlying transfer
            # function (that would mean ADDING to the dB value, which is
            # exactly what "Desplazar en Y" already does for a dB trace).
            # Force it to a no-op for those two kinds so a stray value here
            # can't silently corrupt a Bode magnitude/phase curve.
            sig.gain = _parse_float(gain_var.get(), 1.0) if new_kind == "voltage" else 1.0
            sig.invert = invert_var.get()
            sig.linestyle = style_var.get()
            sig.marker = marker_var.get()
            sig.marker_size = max(1.0, _parse_float(marker_size_var.get(), sig.marker_size))
            sig.marker_hollow = marker_hollow_var.get()
            sig.color = color
            sig.secondary_y = axis_var.get() == "secondary"
            self.line_widths[uid] = max(0.2, _parse_float(
                width_var.get(), self.DEFAULT_LINE_WIDTH))

            self._sync_unit_options()
            self._refresh_signal_list()
            self._refresh_xy_combos()
            self._select_signal(uid)
            self.update_plot()

        primary_button(self.param_frame, t("Aplicar cambios"), apply_changes
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
        size = max(6.0, min(32.0, _parse_float(self.font_size_var.get(), 10.0)))
        self.font_size_var.set(f"{size:g}")

        legend_text = self.legend_font_size_var.get().strip()
        legend_size: Optional[float] = None
        if legend_text:
            legend_size = max(6.0, min(48.0, _parse_float(legend_text, size - 1)))
            self.legend_font_size_var.set(f"{legend_size:g}")

        set_publication_style(font_family=self.font_family_var.get(),
                              base_fontsize=size, legend_fontsize=legend_size)
        self.update_plot()

    def _on_theme_change(self, value: str) -> None:
        """
        Live switch between the light and dark variants of the same palette.
        Both are stored as [light, dark] pairs in the theme dictionary, so the
        existing widget tree is restyled without being rebuilt.
        """
        set_theme_mode(value if value in THEMES else "light")
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

    def _on_language_change(self, code: str) -> None:
        """
        Switch interface language immediately, by rebuilding the panels.

        Every widget resolves its label through `t()` when it is constructed,
        so an already-built tree cannot be re-translated in place. Deferring
        the change to the next launch was the previous approach and it left
        the window in a mixed state whenever anything had been built before
        the language was known. Rebuilding is the only way to guarantee that
        what is on screen and the active language always agree.

        Traces, overlays and every setting survive: the data lives in
        `self.signals` and in the manager specs, none of which are owned by
        the widgets being replaced.
        """
        if code == get_language():
            return
        set_language(code)
        self._rebuild_ui()
        self._save_session_soon()

    def _rebuild_ui(self) -> None:
        """Tear down and rebuild the panels, preserving all application state."""
        settings = {}
        for key, var in self._persisted_vars().items():
            try:
                settings[key] = var.get()
            except Exception:
                continue
        extra_legend = self._legend_extra_entries()
        cursors = self.cursors.to_dict()
        annotations = self.annotations.to_dict()
        selected, compact = self.selected_uid, self._compact

        if self.overlay_window is not None and self.overlay_window.winfo_exists():
            self.overlay_window.destroy()
        self.overlay_window = None
        if self._shortcuts_window is not None and self._shortcuts_window.winfo_exists():
            self._shortcuts_window.destroy()
        self._shortcuts_window = None
        if self._board_window is not None and self._board_window.winfo_exists():
            self._board_window.destroy()
        self._board_window = None

        self._plot_suspended = True
        try:
            for child in list(self.winfo_children()):
                child.destroy()
            self._build_layout()

            for key, var in self._persisted_vars().items():
                if key in settings:
                    try:
                        var.set(settings[key])
                    except Exception:
                        continue
            if extra_legend:
                self.legend_extra_box.insert("1.0", "\n".join(extra_legend))

            self.cursors.from_dict(cursors)
            self.annotations.from_dict(annotations)

            self.selected_uid = selected if selected in self.signals else None
            self._refresh_signal_list()
            self._refresh_xy_combos()
            if self.selected_uid is not None:
                self._build_param_panel(self.selected_uid)
            if compact:
                self._set_compact(True)
        finally:
            self._plot_suspended = False
        self.update_plot()

    def _preview_width(self, uid: str, var) -> None:
        """
        Live preview while the weight slider moves.

        Applied straight away rather than waiting for "Aplicar cambios",
        because line weight is judged by eye: you need to see it on the plot
        to know whether it is right. `SliderField` already debounces this to
        one call per pause in the drag (not per pixel of motion), so a
        `_record()` here doesn't spam the undo stack the way one per motion
        event would -- it used to mutate `self.line_widths` with no snapshot
        at all, which meant Ctrl+Z after adjusting a trace's width did
        nothing (the width change silently rode along inside whatever the
        NEXT recorded action happened to be, so undoing THAT reverted the
        width too, as an unrelated side effect).
        """
        self._record(t("Ajustar grosor de traza"))
        self.line_widths[uid] = max(0.2, _parse_float(var.get(),
                                                      self.DEFAULT_LINE_WIDTH))
        self.update_plot()

    def _legend_extra_entries(self) -> list[str]:
        try:
            raw = self.legend_extra_box.get("1.0", "end")
        except Exception:
            return []
        return [line.strip() for line in raw.splitlines() if line.strip()]

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

        dec_mode = self.dec_mode_var.get()
        if dec_mode not in DEC_MODES:
            dec_mode = "none"
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
            "y2_unit": self.unit_y2_var.get(),
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
            "legend_frameon": self.legend_frameon_var.get(),
            "legend_title": self.legend_title_var.get().strip(),
            "legend_extra": self._legend_extra_entries(),
            "bode_layout": self.bode_layout_var.get(),
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

    def _marker_kwargs(self, sig: Signal, color_override: Optional[str] = None) -> dict:
        """
        Matplotlib `plot()` kwargs for a trace's marker, or `{}` for "None".

        A hollow marker keeps the trace's own colour on the edge but drops
        the fill (`markerfacecolor="none"`) -- the "puntos vacíos" look, as
        opposed to a solid dot/square/etc. `color_override` is for callers
        (X/Y mode) that plot with a colour other than `sig.color`, so the
        hollow edge still matches what is actually on screen.
        """
        marker = sig.marker or "None"
        if marker == "None":
            return {}
        kwargs = {"marker": marker, "markersize": sig.marker_size}
        if sig.marker_hollow:
            kwargs["markerfacecolor"] = "none"
            kwargs["markeredgecolor"] = color_override or sig.color
        return kwargs

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
            messagebox.showerror(t("Error en ajustes"), str(exc))
            return

        mode = settings["mode"]
        try:
            if mode == "Modo X/Y":
                n_points = self._draw_xy(settings)
            elif mode == "Diagrama de Bode":
                n_points = self._draw_bode(settings)
            elif mode == "Pizarra en blanco":
                n_points = self._draw_blank(settings)
            else:
                n_points = self._draw_standard(settings)
        except Exception as exc:
            messagebox.showerror(t("Error al graficar"), str(exc))
            return

        if self._manual_margins is None:
            try:
                self.fig.tight_layout()
            except Exception:
                pass   # tight_layout can fail with an outside legend; harmless
        else:
            # Manual margins win: tight_layout recomputes the layout from
            # scratch and would discard them on every single redraw.
            try:
                self.fig.subplots_adjust(**self._manual_margins)
            except (ValueError, AttributeError):
                self._manual_margins = None

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
        self.status_label.configure(text=f'{n_points} {t("puntos en gráfico")} · {t("modo")}: '
                 f'{_labels()["modes"].get(mode, mode)}')

    def _decorate_legend(self, handles: list, labels: list,
                         settings: dict) -> tuple[list, list]:
        """
        Append the free-text entries to a legend's handles/labels.

        Each one gets an invisible handle, which is how Matplotlib renders a
        legend row with text but no line or marker.
        """
        extra = settings.get("legend_extra") or []
        if not extra:
            return handles, labels
        blanks = [Line2D([], [], linestyle="none", marker="") for _ in extra]
        return list(handles) + blanks, list(labels) + list(extra)

    def _finish_legend(self, ax, settings: dict, handles=None, labels=None) -> None:
        if not settings["show_legend"]:
            return
        kwargs = legend_kwargs(settings["legend_pos"],
                               anchor=settings.get("legend_anchor"),
                               corner=settings.get("legend_corner", "upper left"),
                               ncol=settings.get("legend_ncol", 1),
                               frameon=settings.get("legend_frameon", True))
        if handles is None:
            handles, labels = ax.get_legend_handles_labels()
        handles, labels = self._decorate_legend(handles, labels, settings)
        if not handles:
            return
        legend = ax.legend(handles, labels, **kwargs)
        title = settings.get("legend_title")
        if title and legend is not None:
            legend.set_title(title)

    def _draw_standard(self, settings: dict) -> int:
        """
        Time / frequency mode: every visible signal on a single axes, except
        signals flagged `secondary_y` which are drawn on an independent
        right-hand Y2 axis (twinx) — a real second scale, not a gain hack.

        Like `_draw_bode`'s "shared" layout, the legend is re-ordered to
        match `signal_order` (i.e. the trace list) after drawing, instead of
        using `ax.get_legend_handles_labels() + ax2.get_legend_handles_labels()`
        directly: primary-axis and secondary-axis (Y2) traces are plotted in
        two separate loops -- each internally in `signal_order`, but any Y2
        trace interleaved with primary ones in the trace list would still
        end up grouped as "all primary, then all Y2" in the legend, ignoring
        where the user actually put it.
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
            unit = settings["y2_unit"] if sig.secondary_y else settings["y_unit"]
            y_factor = y_units_for_kind(sig.y_kind).get(unit, 1.0)
            x_disp, y_disp = x / x_factor, y / y_factor
            (secondary if sig.secondary_y else primary).append((sig, x_disp, y_disp))
            total += x.size

        handle_by_uid: dict = {}

        for sig, xd, yd in primary:
            line, = ax.plot(xd, yd, linestyle=sig.linestyle, color=sig.color,
                            linewidth=self._lw(sig.uid), label=self._legend_label(sig),
                            **self._marker_kwargs(sig))
            handle_by_uid[sig.uid] = line

        if secondary:
            ax2 = ax.twinx()
            self.axes = [ax, ax2]
            for sig, xd, yd in secondary:
                line, = ax2.plot(xd, yd, linestyle=sig.linestyle, color=sig.color,
                                 linewidth=self._lw(sig.uid),
                                 label=self._legend_label(sig), **self._marker_kwargs(sig))
                handle_by_uid[sig.uid] = line

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

        ordered_handles = [handle_by_uid[uid] for uid, _x, _y in curves if uid in handle_by_uid]
        ordered_labels = [self._legend_label(self.signals[uid]) for uid, _x, _y in curves
                          if uid in handle_by_uid]
        if settings["show_legend"] and ordered_handles:
            self._finish_legend(ax, settings, ordered_handles, ordered_labels)
        return total

    def _draw_blank(self, settings: dict) -> int:
        """
        "Pizarra en blanco": an empty canvas with no data, ticks or scale
        semantics -- just a fixed 0-1 square the overlay tools (cursors and,
        above all, the annotation set: arrows, text, boxes, reference lines)
        can draw on freely, exactly like on top of any real signal plot.

        No axis cosmetics, log scale or unit conversion apply here: those
        are meaningless without data, so this bypasses `_apply_axis_cosmetics`
        entirely rather than have half of the "Ejes y escalas" section do
        nothing silently.
        """
        self._reset_figure(1)
        ax = self.axes[0]
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_aspect("auto")
        for spine in ax.spines.values():
            spine.set_color("0.6")
        if settings["title"]:
            ax.set_title(settings["title"])
        return 0

    def _draw_bode(self, settings: dict) -> int:
        """
        Bode mode: magnitude (dB) and phase (deg) vs frequency.

        `bode_layout == "shared"` ("Juntos") overlays both on ONE set of
        axes using a secondary Y axis (twinx): magnitude reads off the left
        (Y1) scale, phase off the right (Y2) scale, phase drawn in its own
        configured line style/marker but colored to match its paired
        magnitude trace (same original signal, matched via
        `_bode_base_key`). `"separate"` keeps two fully independent stacked
        axes, each with its own X ticks/label and each signal's own
        configured color/linestyle/marker.

        Voltage-kind signals are converted to dB on the fly so a linear AC
        sweep still renders as a proper Bode magnitude plot.

        In "shared" layout the legend entries are re-assembled in
        `signal_order` after drawing, instead of using
        `ax.get_legend_handles_labels()` directly: drawing must plot a
        magnitude trace right before its paired phase trace (for the
        `_bode_base_key` match), which groups the legend into "all
        magnitude, then all phase" if left as-is -- ignoring the order the
        user set in the trace list. Re-ordering from `signal_order` after
        the fact keeps the trace list's ▲/▼ controls (and the "Columnas"
        column count) in direct control of what the legend shows and how
        it's split into columns, in this layout too.
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
                    ax_ph.plot(x_disp, y, linestyle=sig.linestyle, color=sig.color,
                               linewidth=self._lw(uid), label=label,
                               **self._marker_kwargs(sig))
                else:
                    y_db = y if sig.y_kind == "dB" else _voltage_to_db(y)
                    ax_mag.plot(x_disp, y_db, linestyle=sig.linestyle,
                                color=sig.color, linewidth=self._lw(uid),
                                label=label, **self._marker_kwargs(sig))
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
                                             ncol=settings.get("legend_ncol", 1),
                                             frameon=settings.get("legend_frameon", True)))
            return total

        # "Juntos": true overlay, one set of axes, dual Y scales (twinx).
        self._reset_figure(1)
        ax_mag = self.axes[0]
        ax_ph = ax_mag.twinx()
        self.axes = [ax_mag, ax_ph]

        deg_curves = [(uid, x, y) for uid, x, y in curves if self.signals[uid].y_kind == "deg"]
        mag_curves = [(uid, x, y) for uid, x, y in curves if self.signals[uid].y_kind != "deg"]
        used_deg: set = set()

        # Drawing has to plot every magnitude trace before its paired phase
        # trace (so the pairing lookup below can match on `_bode_base_key`),
        # but that draw order is NOT the order the user set in the trace
        # list (`signal_order`): a magnitude/phase pair only ends up
        # adjacent here when they happen to be adjacent there too. So the
        # legend is NOT built from `ax.get_legend_handles_labels()` (which
        # would silently reflect this draw-order grouping -- all magnitude
        # entries, then all phase entries -- instead of what `signal_order`
        # actually says). Instead every plotted handle is stashed by uid and
        # the final handles/labels list is re-assembled by walking `curves`,
        # i.e. `signal_order` itself, so reordering a trace with ▲/▼ always
        # moves its legend entry (and, combined with "Columnas", which
        # column it lands in) exactly where the user put it.
        handle_by_uid: dict = {}

        for uid, x, y in mag_curves:
            sig = self.signals[uid]
            x_disp = x / x_factor
            y_db = y if sig.y_kind == "dB" else _voltage_to_db(y)
            line, = ax_mag.plot(x_disp, y_db, linestyle=sig.linestyle, color=sig.color,
                                linewidth=self._lw(uid), label=self._legend_label(sig),
                                **self._marker_kwargs(sig))
            handle_by_uid[uid] = line
            total += x.size

            # Pair with the phase trace from the same original signal (if
            # any): same color, drawn on the Y2 axis, in THAT trace's own
            # line style -- it used to be forced dashed regardless of what
            # was picked for it, which silently overrode a per-trace choice
            # like "sin línea" the moment it was a phase trace paired with
            # a magnitude one.
            key = _bode_base_key(sig.name)
            match = next((d for d in deg_curves if d[0] not in used_deg
                          and _bode_base_key(self.signals[d[0]].name) == key), None)
            if match:
                duid, dx, dy = match
                used_deg.add(duid)
                dsig = self.signals[duid]
                pline, = ax_ph.plot(dx / x_factor, dy, linestyle=dsig.linestyle, color=sig.color,
                                    linewidth=self._lw(duid),
                                    label=self._legend_label(dsig), **self._marker_kwargs(dsig))
                handle_by_uid[duid] = pline
                total += dx.size

        for duid, dx, dy in deg_curves:
            if duid in used_deg:
                continue
            dsig = self.signals[duid]
            line, = ax_ph.plot(dx / x_factor, dy, linestyle=dsig.linestyle, color=dsig.color,
                               linewidth=self._lw(duid), label=self._legend_label(dsig),
                               **self._marker_kwargs(dsig))
            handle_by_uid[duid] = line
            total += dx.size

        self._apply_axis_cosmetics(ax_mag, dict(settings, yscale="linear"), xlabel, mag_label)
        ax_ph.set_ylabel(ph_label)
        ax_ph.set_yscale("linear")
        if settings["title"]:
            ax_mag.set_title(settings["title"])

        ordered_handles = [handle_by_uid[uid] for uid, _x, _y in curves if uid in handle_by_uid]
        ordered_labels = [self._legend_label(self.signals[uid]) for uid, _x, _y in curves
                          if uid in handle_by_uid]
        if settings["show_legend"] and ordered_handles:
            self._finish_legend(ax_mag, settings, ordered_handles, ordered_labels)
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
                color=xy_color, linewidth=self._lw(y_uid), label=xy_label,
                **self._marker_kwargs(sig_y, color_override=xy_color))

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
            messagebox.showinfo(t("Sin señales"), t("Cargá al menos una señal antes de exportar."))
            return

        settings = self._read_settings()

        # X/Y mode exports the parametric curve currently on screen.
        if settings["mode"] == "Modo X/Y":
            curve = getattr(self, "_xy_last_curve", None)
            if curve is None:
                messagebox.showinfo(t("Sin curva X/Y"),
                                     t("Generá primero una curva X/Y válida en el gráfico."))
                return
            out_dir = filedialog.askdirectory(title=t("Carpeta de destino para el CSV X/Y"))
            if not out_dir:
                return
            try:
                paths = export_xy_csv([curve], out_dir)
            except Exception as exc:
                messagebox.showerror(t("Error al exportar"), str(exc))
                return
            messagebox.showinfo(t("Exportación completa"),
                                 f"{t('Curva X/Y guardada en')}:\n{paths[0]}")
            return

        curves = self._gather_curves(settings, for_display=False)
        if not curves:
            messagebox.showinfo(t("Sin datos"),
                                 t("No hay señales visibles con datos en el rango seleccionado."))
            return

        payload = [(self.signals[uid].name, x, y,
                    self.signals[uid].domain, self.signals[uid].y_kind)
                   for uid, x, y in curves]

        if self.csv_mode_var.get() == "individual":
            out_dir = filedialog.askdirectory(title=t("Carpeta de destino para los CSV"))
            if not out_dir:
                return
            try:
                paths = export_csv_individual(payload, out_dir,
                                               settings["x_unit"], settings["y_unit"])
            except Exception as exc:
                messagebox.showerror(t("Error al exportar"), str(exc))
                return
            messagebox.showinfo(
                t("Exportación completa"),
                t("Se generaron {n} archivo(s) en").format(n=len(paths)) + f":\n{out_dir}")
        else:
            out_path = filedialog.asksaveasfilename(
                title=t("Guardar CSV combinado"), defaultextension=".csv",
                filetypes=[("CSV", "*.csv")])
            if not out_path:
                return
            try:
                export_csv_combined(payload, out_path,
                                     settings["x_unit"], settings["y_unit"])
            except Exception as exc:
                messagebox.showerror(t("Error al exportar"), str(exc))
                return
            messagebox.showinfo(t("Exportación completa"),
                                 f"{t('CSV combinado guardado en')}:\n{out_path}")

    def _export_figure(self) -> None:
        if not self.signals:
            messagebox.showinfo(t("Sin señales"), t("Cargá al menos una señal antes de exportar."))
            return

        fmt = self.fig_format_var.get()
        dpi = max(50, int(_parse_float(self.dpi_var.get(), 300.0)))

        out_path = filedialog.asksaveasfilename(
            title=t("Guardar figura"), defaultextension=f".{fmt}",
            filetypes=[(fmt.upper(), f"*.{fmt}")])
        if not out_path:
            return

        # The preview already uses the final rcParams (mathtext, no external
        # TeX), so the on-screen figure can be saved as-is.
        try:
            export_figure(self.fig, out_path, dpi=dpi)
        except Exception as exc:
            messagebox.showerror(t("Error al exportar figura"), str(exc))
            return

        self._last_export_path = out_path
        try:
            session.save_figure_state(out_path, self._gather_plot_state())
        except Exception:
            pass   # the sidecar is a convenience: never undo a successful export
        self._show_latex_figure(out_path)

    def _import_figure(self) -> None:
        """
        Reopen a figure exported earlier -- with every setting and signal it
        had at export time -- from the `.labplotter.json` sidecar written
        next to it by `_export_figure`. Restored into a NEW tab rather than
        replacing the current one, same reasoning as `_add_tab`: importing
        an old figure should never cost you whatever you already have on
        screen.
        """
        path = filedialog.askopenfilename(
            title=t("Importar figura..."),
            filetypes=[(t("Ajustes de LabPlotter"), "*.labplotter.json"),
                      (t("Todos los archivos"), "*.*")])
        if not path:
            return

        data = session.load_figure_state(path)
        if data is None:
            messagebox.showerror(
                t("No se pudo importar"),
                t("«{name}» no es un archivo de ajustes de LabPlotter válido "
                  "(o es de una versión incompatible).").format(
                      name=os.path.basename(path)))
            return

        self.plot_tabs[self.active_tab].state = self._gather_plot_state()
        name = os.path.basename(path)
        if name.endswith(session.FIGURE_STATE_SUFFIX):
            name = name[: -len(session.FIGURE_STATE_SUFFIX)]
        name = name or self._next_tab_name()
        self.plot_tabs.append(tabs.PlotTab(name=name, state=data))
        self.active_tab = len(self.plot_tabs) - 1
        self._apply_plot_state(data, anchor_dir=os.path.dirname(path))
        self._refresh_tab_strip()

        total = len(data.get("signals", []) or [])
        missing = sum(1 for uid in self.signal_order if self.signals[uid].missing)
        if missing > 0:
            messagebox.showwarning(
                t("Faltan archivos de origen"),
                t("{missing} de {total} señal(es) no se pudieron recargar: "
                  "el archivo de datos original ya no está en la misma ruta "
                  "que cuando se exportó la figura. Quedaron marcadas (⚠) en "
                  "la lista de trazas -- hacé clic en una para reconectarla "
                  "a mano.").format(
                      missing=missing, total=total))

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
                width=values.get(t("Ancho"), "0.85\\linewidth"),
                relative_to=project_dir,
                escape_caption=bool(values.get("__toggle__")))

        initial = build({"Caption": caption_var.get(), "Label": label_var.get(),
                         t("Ancho"): width_var.get(), "__toggle__": False})
        CodeDialog(
            self, t("Incluir en LaTeX"), initial,
            note=f"{latex.figure_requirements(out_path)}    ·    "
                 f"Archivo: {out_path}",
            fields=[("Caption", caption_var), ("Label", label_var),
                    (t("Ancho"), width_var)],
            rebuild=build,
            extra_toggle=(t("Escapar caracteres especiales del caption "
                            "(desactivalo si escribís $matemática$)"), False))

    # ------------------------------------------------------------------ #
    # Multi-figure board
    # ------------------------------------------------------------------ #
    def _add_current_to_board(self) -> None:
        """
        Snapshot the figure currently on screen as one board panel: a real
        vector PDF (what the generated LaTeX will embed) plus a lightweight
        PNG (used only to draw the board's on-screen preview -- Matplotlib
        cannot rasterize a PDF back into an image without an extra
        dependency, so the raster is produced here, from the same figure,
        instead).
        """
        if not self.signals:
            messagebox.showinfo(
                t("Sin señales"),
                t("Cargá y configurá al menos una señal antes de "
                  "agregar el gráfico al tablero."))
            return

        if self._board_export_dir is None:
            chosen = filedialog.askdirectory(
                title=t("Carpeta donde se guardan las figuras del tablero"))
            if not chosen:
                return
            self._board_export_dir = chosen

        title = (self.board_title_var.get().strip()
                 or self.title_var.get().strip()
                 or f"Figura {board.total_panels(self.board_rows) + 1}")

        slug = latex.sanitize_label(title, prefix="panel").split(":", 1)[1]
        preview_dir = os.path.join(self._board_export_dir, ".board_preview")
        vector_path = os.path.join(self._board_export_dir, f"{slug}.pdf")
        preview_path = os.path.join(preview_dir, f"{slug}.png")
        n = 1
        while os.path.exists(vector_path):
            n += 1
            vector_path = os.path.join(self._board_export_dir, f"{slug}_{n}.pdf")
            preview_path = os.path.join(preview_dir, f"{slug}_{n}.png")

        try:
            export_figure(self.fig, vector_path)
            export_figure(self.fig, preview_path, dpi=100)
        except Exception as exc:
            messagebox.showerror(t("Error al agregar al tablero"), str(exc))
            return

        self.board_rows[-1].append(
            board.BoardPanel(title=title, vector_path=vector_path,
                             preview_path=preview_path))
        self.board_title_var.set("")

        count = board.total_panels(self.board_rows)
        self.board_status_label.configure(
            text=f"{count} panel(es) · {self._board_export_dir}")

        if self._board_window is not None and self._board_window.winfo_exists():
            self._board_window._refresh()

    def _open_board_window(self) -> None:
        if self._board_window is not None and self._board_window.winfo_exists():
            self._board_window.lift()
            self._board_window.focus_force()
            return
        self._board_window = BoardWindow(self, self)

    # ------------------------------------------------------------------ #
    # Overlay layer: cursors and annotations
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # Undo / redo
    # ------------------------------------------------------------------ #
    DEFAULT_LINE_WIDTH = 1.4

    def _lw(self, uid: str) -> float:
        return self.line_widths.get(uid, self.DEFAULT_LINE_WIDTH)

    def _snapshot(self, label: str):
        return self.history.capture(label, self.signals, self.signal_order,
                                    self._signal_columns, self.selected_uid,
                                    extras=self.line_widths)

    def _record(self, label: str) -> None:
        """Call immediately *before* mutating the trace set."""
        self.history.push(self._snapshot(label))

    def _undo(self) -> None:
        restored = self.history.undo(self._snapshot(t("Deshacer")))
        if restored is None:
            self.status_label.configure(text=t("Nada para deshacer."))
            return
        self._apply_history(restored, t("Deshecho"))

    def _redo(self) -> None:
        restored = self.history.redo(self._snapshot(t("Rehacer")))
        if restored is None:
            self.status_label.configure(text=t("Nada para rehacer."))
            return
        self._apply_history(restored, t("Rehecho"))

    def _apply_history(self, snapshot, verb: str) -> None:
        self.selected_uid = apply_snapshot(snapshot, self.signals,
                                           self.signal_order,
                                           self._signal_columns,
                                           extras=self.line_widths)
        self._sync_unit_options()
        self._refresh_signal_list()
        self._refresh_xy_combos()
        if self.selected_uid is not None:
            self._build_param_panel(self.selected_uid)
        else:
            self._build_param_placeholder()
        self.update_plot()
        self.status_label.configure(text=f"{verb}: {snapshot.label}")

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

    def _open_overlay_window(self, tab: str = "cursors") -> None:
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
    # Language and palette are both read at widget-construction time, so both
    # have to be settled before the window exists.
    saved = session.load_session() or {}
    set_language(saved.get("language", "es"))
    apply_theme(saved.get("settings", {}).get("theme_mode", "light"))
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
