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
* the annotation form's per-kind fields are shown or hidden with
  `ANNOTATION_SCHEMA` (see below) instead of being greyed out: a form for
  "Texto" no longer keeps a disabled row for "Flecha" or "Marcar valor en
  el eje" sitting on screen -- it simply isn't there. Every row is built
  ONCE, at panel-construction time, and only `pack()`/`pack_forget()`'d
  afterwards -- never destroyed/rebuilt and never recomputing its own
  layout in response to its own resize (that combination, on a live
  widget, is what caused the `<Configure>` cascade this codebase already
  hit once; see `hint()` in `gui/widgets.py`). Rows within a section are
  always re-inserted with `before=<that section's sentinel>`, in a fixed
  canonical order, so toggling visibility back and forth can never
  silently reorder the form -- `pack_forget()` followed by a bare
  `pack()` would otherwise append a re-shown row at the END of its
  siblings instead of restoring its original position.
"""

from __future__ import annotations

import math
from tkinter import colorchooser, filedialog, messagebox
from typing import Callable, Optional, Sequence

import customtkinter as ctk

from core.i18n import t
from core.units import parse_eng

from .overlays import (
    ANNOTATION_KINDS, ARROW_STYLES, AnnotationManager, AnnotationSpec,
    CursorManager, DIM_ORIENTATIONS, FONT_FAMILIES, FONT_STYLES, FONT_WEIGHTS,
    HA_CHOICES, KIND_DEFAULTS, LINESTYLES, STYLE_PRESETS, VA_CHOICES,
    format_eng, load_overlays, save_overlays,
)
from .theme import col, font, spaced
from .widgets import (
    ROW_HEIGHT, MeasurementsCard, Rule, SectionGroup, SectionHeader, Segmented,
    StaticSection, check_field,
    combo_field, entry_field, ghost_button, hint, primary_button,
    stacked_entry, stacked_label,
)

# Internal pane ids; the visible text comes from `_pane_labels()`.
PANES = ["cursors", "annotations"]


def _pane_labels() -> dict:
    return {"cursors": t("Cursores"), "annotations": t("Anotaciones")}

# Declarative "which fields does this kind actually use" table -- the single
# source of truth the form is BUILT FROM. It intentionally does not need to
# match `_render_<kind>` in `gui/overlays.py` byte for byte (that renderer
# body is, and stays, the real ground truth for what is DRAWN); this table
# only decides what is worth showing the person editing it, curated once
# here instead of split across a widget-enable dict, a defaults dict and a
# manually-written form-to-spec mapping the way it used to be. Persisted
# JSON is untouched by this: `AnnotationSpec` itself stays the same flat
# dataclass it always was (see its docstring), so a saved overlay file from
# before this table existed still loads exactly as it used to.
ANNOTATION_SCHEMA: dict[str, set[str]] = {
    "point": {"x", "y", "text", "dx", "dy", "arrow", "fontsize", "boxed", "color"},
    "arrow": {"x", "y", "x2", "y2", "text", "dx", "dy", "arrow", "linewidth",
              "fontsize", "boxed", "color"},
    "dimension": {"x", "y", "x2", "y2", "text", "linestyle", "linewidth",
                  "rotation", "label_pos", "label_parallel", "label_free",
                  "label_x", "label_y", "dim_orientation", "dim_auto_text",
                  "fontsize", "boxed", "color", "alpha"},
    "line": {"x", "y", "x2", "y2", "text", "linestyle", "linewidth", "rotation",
             "label_pos", "label_parallel", "label_free", "label_x", "label_y",
             "fontsize", "boxed", "color", "alpha"},
    "vline": {"x", "text", "linestyle", "linewidth", "rotation", "label_pos",
              "label_parallel", "label_free", "label_x", "label_y",
              "on_axis", "axis_side", "axis_text", "axis_fontfamily",
              "axis_fontweight", "axis_fontstyle", "axis_fontsize",
              "fontsize", "boxed", "color"},
    "hline": {"y", "text", "linestyle", "linewidth", "rotation", "label_pos",
              "label_parallel", "label_free", "label_x", "label_y",
              "on_axis", "axis_side", "axis_text", "axis_fontfamily",
              "axis_fontweight", "axis_fontstyle", "axis_fontsize",
              "fontsize", "boxed", "color"},
    "text":  {"x", "y", "text", "rotation", "fontsize", "boxed", "color"},
    "vspan": {"x", "x2", "text", "alpha", "label_pos", "fontsize", "boxed", "color"},
    "hspan": {"y", "y2", "text", "alpha", "label_pos", "fontsize", "boxed", "color"},
}

# Composite rows whose visibility depends on more than one schema key at
# once (a button that only makes sense once ANY of a few fields is active),
# rather than a single field name `_apply_schema` can look up directly.
_PSEUDO_GROUPS: dict[str, set[str]] = {
    "_capture_p2": {"x2", "y2"},
    "_placement_hint": {"label_parallel", "label_free"},
    "_axis_mark_hint": {"on_axis"},
}

# Every kind carries a text label, so the typography controls (built once,
# never hidden) apply to all -- this mirrors the pre-existing behaviour
# rather than changing it.
_TEXT_STYLE_FIELDS = {"fontfamily", "fontweight", "fontstyle", "ha", "va"}
for _kind in ANNOTATION_SCHEMA:
    ANNOTATION_SCHEMA[_kind] |= _TEXT_STYLE_FIELDS

# Sentinel for "inherit the rcParams family": an empty string cannot be shown
# in a CTkComboBox without looking like a rendering glitch.
FONT_DEFAULT = "(por defecto)"

# Axis-mark side options, per kind -- keyed by the same internal id the
# renderer expects (`AnnotationSpec.axis_side`); labels are looked up at
# combo-build time via `t(...)` so they follow the interface language.
_AXIS_SIDE_OPTIONS: dict[str, list[str]] = {
    "vline": ["bottom", "top"],
    "hline": ["left", "right"],
}
_AXIS_SIDE_LABELS: dict[str, str] = {
    "bottom": "Abajo", "top": "Arriba", "left": "Izquierda", "right": "Derecha",
}

# "Cota" orientation combo -- internal id (matches `AnnotationSpec.
# dim_orientation`) -> Spanish label, translated at combo-build time.
_DIM_ORIENTATION_LABELS: dict[str, str] = {
    "auto": "Automática", "horizontal": "Horizontal", "vertical": "Vertical",
}

# Novice-facing labels for raw matplotlib codes these combos would
# otherwise show verbatim ("--", "-.", "<|-|>", ...) -- the internal id
# never changes (still exactly what `AnnotationSpec`/matplotlib expect),
# only what the dropdown displays.
_LINESTYLE_LABELS: dict[str, str] = {
    "--": "Discontinua", "-": "Sólida", "-.": "Guión y punto", ":": "Punteada",
}
_ARROWSTYLE_LABELS: dict[str, str] = {
    "->": "Flecha simple", "<-": "Flecha invertida", "<->": "Flecha doble",
    "-|>": "Flecha rellena", "<|-|>": "Doble flecha rellena", "-": "Sin punta",
}
# fontweight/fontstyle are binary in this app (`FONT_WEIGHTS`/`FONT_STYLES`,
# `gui/overlays.py`) -- "Sí"/"No" against a "Negrita"/"Cursiva" row label
# reads as a yes/no question instead of a "normal"/"bold" combo nobody
# outside matplotlib recognises.
_FONT_WEIGHT_LABELS: dict[str, str] = {"normal": "No", "bold": "Sí"}
_FONT_STYLE_LABELS: dict[str, str] = {"normal": "No", "italic": "Sí"}
_HA_LABELS: dict[str, str] = {"left": "Izquierda", "center": "Centro", "right": "Derecha"}
_VA_LABELS: dict[str, str] = {"top": "Arriba", "center": "Centro", "bottom": "Abajo",
                              "baseline": "Línea base"}

# Coordinate rows (X/Y/X₂/Y₂) are hidden by default: clicking on the chart
# ("Marcar un punto en el gráfico") is the primary, novice-facing path now;
# typing raw numbers is the opt-in advanced path gated by
# `manual_coords_var` in `_apply_schema`.
_COORD_KEYS: set[str] = {"x", "y", "x2", "y2"}


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
                 unit_provider: Optional[Callable[[int], tuple[str, str]]] = None,
                 axes_provider: Optional[Callable[[], list[str]]] = None,
                 initial_pane: str = "cursors", show_header: bool = True,
                 **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.cursors = cursors
        self.annotations = annotations
        self.on_refresh = on_refresh
        # `unit_provider(axes_index)` -> (x_unit, y_unit) for THAT axis --
        # not a single fixed pair. A figure with a secondary Y axis (Bode
        # "Juntos", or any `secondary_y` trace) has two independent units
        # on screen at once (e.g. dB and deg); asking for "the" unit with
        # no axis argument is exactly the bug this panel used to have (a
        # cursor on the phase axis showing its value tagged in dB). See
        # `_units()`.
        self.unit_provider = unit_provider
        # `axes_provider()` -> translated labels, one per axes currently on
        # screen, in the same order `App.axes` lists them -- drives the
        # "Eje" selector below. `App._axes_context` is the real
        # implementation; this panel never needs to know WHY there are one,
        # two or three axes (twin Y, Bode "Separados", a plain single plot),
        # only how many and what to call them.
        self.axes_provider = axes_provider

        self._sel_cursor: Optional[int] = None
        self._sel_annotation: Optional[int] = None
        self._axes_index = 0
        self.axes_labels: list[str] = [t("Principal")]

        # `show_header=False` when embedded in the "Anotar" stage's navigator
        # (`App._build_overlay_panel`) -- `Shell.navigator_header` already
        # prints this exact same title ("Cursores y anotaciones") above the
        # navigator column, so repeating it here just doubled it on screen.
        # Still on by default for `OverlayWindow` below, the floating
        # `CTkToplevel` this panel was originally built for (currently
        # unused, kept for reference): there, the window's own title bar is
        # a separate surface, so this header isn't a duplicate.
        if show_header:
            header = ctk.CTkFrame(self, fg_color="transparent")
            header.pack(fill="x", padx=18, pady=(16, 0))
            ctk.CTkLabel(header, text=spaced(t("Cursores y anotaciones")), font=font("header"),
                        text_color=col("fg_muted")).pack(side="left")
            Rule(self, strong=True).pack(fill="x", padx=18, pady=(8, 12))

        self.pane_var = ctk.StringVar(value=initial_pane if initial_pane in PANES
                                      else PANES[0])
        Segmented(self, PANES, self.pane_var, labels=_pane_labels(),
                  command=lambda _v: self._show_pane(),
                  width=132).pack(padx=18, pady=(16, 0), anchor="w")

        self.panes: dict[str, ctk.CTkFrame] = {}
        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="both", expand=True, padx=18, pady=(14, 16))
        for name in PANES:
            # A plain frame, NOT its own CTkScrollableFrame: this panel is
            # embedded in `navigators["annotate"]` (`App._build_overlay_panel`),
            # which is ALREADY a `CTkScrollableFrame` (`Shell._build_navigator`,
            # same as every other stage's navigator column). Nesting a second
            # scrollable canvas inside that one is exactly the pattern
            # `gui/shell.py` already warns about elsewhere in this codebase
            # (two `CTkScrollableFrame`s competing for vertical space): the
            # inner one doesn't stretch to fill the outer one's real
            # available height, it just claims a height of its own -- which
            # is what read as "a box with a fixed height" that stopped short
            # of the column's actual bottom instead of using it. A plain
            # frame has no height of its own to claim; it just flows as part
            # of the ONE scrollable region the navigator column already is,
            # using all the height that's there and scrolling with it.
            self.panes[name] = ctk.CTkFrame(holder, fg_color="transparent")
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

    def _units(self, axes_index: Optional[int] = None) -> tuple[str, str]:
        """(x_unit, y_unit) for `axes_index` (default: the selected axis)."""
        if self.unit_provider is None:
            return "", ""
        index = self._axes_index if axes_index is None else axes_index
        try:
            return self.unit_provider(index)
        except Exception:
            return "", ""

    # ------------------------------------------------------------------ #
    # Row-visibility plumbing
    #
    # A field row is built exactly once (at panel-construction time) inside
    # a small wrapper frame this class owns (`_wrap`); showing/hiding a
    # field means `pack()`/`pack_forget()` on that ONE wrapper, always
    # relative to a fixed per-section "sentinel" frame placed right after
    # that section's last row. `tk`'s `pack(before=...)` re-inserts a
    # forgotten widget exactly where it belongs relative to the sentinel,
    # so toggling several rows on and off never reshuffles their relative
    # order -- a bare `pack_forget()` + `pack()` would instead always
    # append the row at the END of its current siblings.
    # ------------------------------------------------------------------ #
    def _wrap(self, master, builder, *args, **kwargs):
        """Build one form row (via a `gui.widgets` helper) inside its own
        hideable frame; returns (control, row)."""
        row = ctk.CTkFrame(master, fg_color="transparent")
        row.pack(fill="x")
        control = builder(row, *args, **kwargs)
        return control, row

    def _sentinel(self, master) -> ctk.CTkFrame:
        """Zero-size marker: everything conditionally shown in this section
        is re-inserted with `before=this`, which is what keeps their
        relative order stable across repeated show/hide cycles."""
        marker = ctk.CTkFrame(master, fg_color="transparent", width=1, height=1)
        marker.pack(fill="x")
        return marker

    def _new_group(self, sentinel: ctk.CTkFrame) -> list:
        group: list = []
        self._field_groups.append((sentinel, group))
        return group

    def _apply_schema(self, kind: str) -> None:
        """Show exactly the rows `ANNOTATION_SCHEMA[kind]` lists, in each
        section's fixed canonical order; hide every other row.

        Coordinate rows (X/Y/X₂/Y₂) get an extra condition on top of the
        schema: they only show once `manual_coords_var` is checked --
        clicking on the chart ("Marcar un punto en el gráfico") is the
        default, novice-facing way to place a point now, typing raw
        numbers is opt-in. `_load_form` turns the checkbox on when an
        EXISTING annotation is selected, since reviewing/nudging its exact
        numbers is what editing is for.
        """
        active = ANNOTATION_SCHEMA.get(kind, set())
        manual = bool(self.manual_coords_var.get())
        for sentinel, rows in self._field_groups:
            for row, _key in rows:
                row.pack_forget()
            for row, key in rows:
                wanted = (bool(_PSEUDO_GROUPS[key] & active) if key in _PSEUDO_GROUPS
                         else key in active)
                if wanted and key in _COORD_KEYS and not manual:
                    wanted = False
                if wanted:
                    row.pack(fill="x", before=sentinel)
        # "Marca sobre el eje" is a whole accordion, not a single row -- for
        # every kind other than vline/hline it would otherwise expand to an
        # empty body, which reads as broken rather than "not relevant here".
        # Same criterion the rest of this method already applies to rows,
        # one level up.
        if "on_axis" in active:
            self._axis_section.pack(fill="x", pady=(0, 8),
                                    before=self._axis_section_sentinel)
        else:
            self._axis_section.pack_forget()

    # ================================================================== #
    # Cursors
    # ================================================================== #
    def _build_cursor_pane(self, parent) -> None:
        # Stacked, not a row of fixed-width buttons: the old 104+112+76px
        # row was sized for the ~440px floating OverlayWindow and doesn't
        # fit the navigator column, so it clipped (see git history for the
        # exact budget). One full-width button per row -- plain `pack`,
        # deliberately no `grid` here (a `grid`+`pack` mix inside a
        # DPI-scaled CTk hierarchy is where the next report traced back to)
        # -- uses the column's actual width, whatever it is, and the
        # vertical room a single cramped row was leaving empty.
        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(fill="x")
        primary_button(actions, t("Cursor vertical"), lambda: self._arm_cursor("v"),
                       height=28).pack(fill="x")
        ghost_button(actions, t("Cursor horizontal"), lambda: self._arm_cursor("h"),
                     height=28).pack(fill="x", pady=(6, 0))
        ghost_button(actions, t("Eliminar cursor"), self._remove_cursor,
                     height=28).pack(fill="x", pady=(6, 0))
        ghost_button(actions, t("Fijar este valor"), self._promote_cursor,
                     height=28).pack(fill="x", pady=(6, 0))

        self.cursor_hint = hint(parent,
                                t("Hacé clic en el gráfico para colocarlo. "
                                  "Arrastralo para medir una distancia."), wraplength=230)
        self.cursor_hint.pack(fill="x", pady=(8, 12))

        self.cursor_list = ctk.CTkFrame(parent, fg_color="transparent",
                                        width=1, height=1)
        self.cursor_list.pack(fill="x")

        Rule(parent).pack(fill="x", pady=12)

        self.cursor_readout = MeasurementsCard(parent, title=t("Valores medidos"))
        self.cursor_readout.bind_close(self._clear_cursors)
        self.cursor_readout.pack(fill="x")

        # Collapsed by default: nothing here is needed to place a cursor and
        # read a value off it -- see the panel's redesign notes (2026-09).
        section = StaticSection(parent, t("Más opciones de cursores"), expanded=False)
        section.pack(fill="x", pady=(12, 0))
        box = section.body

        self.snap_var = ctk.BooleanVar(value=self.cursors.snap_to_data)
        check_field(box, t("Ajustar al punto más cercano"), self.snap_var,
                    command=self._apply_cursor_options)
        self.tags_var = ctk.BooleanVar(value=self.cursors.show_tags)
        check_field(box, t("Mostrar nombre en el gráfico"), self.tags_var,
                    command=self._apply_cursor_options)
        self.tag_value_var = ctk.BooleanVar(value=self.cursors.tag_with_value)
        check_field(box, t("Mostrar el valor medido"), self.tag_value_var,
                    command=self._apply_cursor_options)
        self.value_decimals_var = ctk.StringVar(value=str(self.cursors.value_decimals))
        entry_field(box, t("Cantidad de decimales"), self.value_decimals_var, width=60,
                    on_enter=self._apply_cursor_options, rule=False, label_width=124)
        hint(box, t("Cuántos decimales mostrar en el valor del cursor y al "
                    "usar «Fijar este valor»."),
             wraplength=230).pack(fill="x", pady=(0, 8))

        self.cursor_pos_var = ctk.StringVar(value="")
        entry_field(box, t("Ubicar en un valor exacto"), self.cursor_pos_var, width=110,
                    on_enter=self._apply_cursor_position, rule=False,
                    label_width=124)

        # ---- Live position slider ---------------------------------------- #
        # Normalised 0..1 travel: the mapping to data coordinates is rebuilt on
        # every selection and every replot (`_sync_cursor_slider`), so the same
        # widget serves a linear time axis and a log frequency axis.
        stacked_label(box, t("Deslizar para mover"))
        self.cursor_slider = ctk.CTkSlider(
            box, from_=0.0, to=1.0, number_of_steps=1000,
            command=self._on_cursor_slide, height=14)
        self.cursor_slider.pack(fill="x", pady=(0, 2))
        self.cursor_slider.set(0.5)
        self.cursor_slider.configure(state="disabled")
        self.slider_readout = hint(box, t("Elegí un cursor de la lista para moverlo."),
                                   wraplength=230)
        self.slider_readout.pack(fill="x", pady=(0, 4))

        self._slider_range: tuple[float, float, bool] = (0.0, 1.0, False)
        self._slider_syncing = False   # guards set() -> command re-entrancy
        self._slider_job = None        # debounce handle for the heavy refresh

    def _arm_cursor(self, orientation: str) -> None:
        self.annotations.disarm()
        self.cursors.arm(orientation)
        self.cursor_hint.configure(
            text=t("Cursor listo: hacé clic en el gráfico para colocarlo."))

    def _apply_cursor_options(self) -> None:
        self.cursors.snap_to_data = bool(self.snap_var.get())
        self.cursors.show_tags = bool(self.tags_var.get())
        self.cursors.tag_with_value = bool(self.tag_value_var.get())
        decimals = int(_parse_float(self.value_decimals_var.get(),
                                     self.cursors.value_decimals))
        self.cursors.value_decimals = max(0, min(6, decimals))
        self.value_decimals_var.set(str(self.cursors.value_decimals))
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
        unit = (self.cursors.axis_unit(spec.axes_index, spec.orientation)
               if spec is not None else "")
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
                text=t("Elegí un cursor de la lista para moverlo."))
            return
        self._slider_range = rng
        self.cursor_slider.configure(state="normal")
        self._slider_syncing = True
        try:
            self.cursor_slider.set(self._data_to_slider(spec.position))
        finally:
            self._slider_syncing = False
        unit = self.cursors.axis_unit(spec.axes_index, spec.orientation)
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

    def _promote_cursor(self) -> None:
        """
        Turn the selected cursor's current reading into a permanent vline/
        hline annotation -- same text the cursor's own on-canvas tag already
        shows (`name: value`, see `CursorManager._create_artists`), so the
        promoted mark reads exactly like what the user was already looking
        at. Silent no-op with nothing selected, matching `_remove_cursor`'s
        own local convention (no messagebox) rather than `_update_annotation`'s.

        Unit and axes_index both follow the CURSOR's own axis, not the
        panel's currently-selected one in the "Anotaciones" tab -- the
        promoted annotation has to land on the same axis the cursor was
        actually measuring, whatever the annotation form happens to be set
        to at that moment.
        """
        if self._sel_cursor is None:
            return
        spec = self.cursors.get(self._sel_cursor)
        if spec is None:
            return
        kind = "vline" if spec.orientation == "v" else "hline"
        unit = self.cursors.axis_unit(spec.axes_index, spec.orientation)
        name = self.cursors.name_of(spec)
        text = f"{name}: {format_eng(spec.position, unit, mathtext=True, decimals=self.cursors.value_decimals)}"
        kwargs = dict(kind=kind, text=text, axes_index=spec.axes_index)
        if spec.orientation == "v":
            kwargs["x"] = spec.position
        else:
            kwargs["y"] = spec.position
        for key, value in KIND_DEFAULTS.get(kind, {}).items():
            kwargs.setdefault(key, value)
        new_spec = self.annotations.add(**kwargs)
        self._refresh_canvas()
        self.refresh_annotation_list()
        self.show_pane("annotations")
        self._select_annotation(new_spec.aid)

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
                text=t("Hacé clic en el gráfico para colocarlo. "
                       "Arrastralo para medir una distancia."))
        # Keep the slider tracking the selection and the current axis limits:
        # a zoom or a replot changes the travel range under it.
        self._sync_cursor_slider()

    def _render_cursor_list(self) -> None:
        for widget in self.cursor_list.winfo_children():
            widget.destroy()
        if not self.cursors.cursors:
            hint(self.cursor_list, t("Sin cursores.")).pack(fill="x")
            return
        for spec in self.cursors.cursors:
            unit = self.cursors.axis_unit(spec.axes_index, spec.orientation)
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
            axis_tag = (f" · {self.axes_labels[spec.axes_index]}"
                       if len(self.axes_labels) > 1
                       and spec.axes_index < len(self.axes_labels) else "")
            name = ctk.CTkLabel(row, text=f"{self.cursors.name_of(spec)}  ·  {axis}{axis_tag}",
                                font=font("small"), anchor="w", cursor="hand2")
            name.pack(side="left", padx=(8, 0))
            value = ctk.CTkLabel(row, text=format_eng(spec.position, unit),
                                 font=font("mono"), text_color=col("fg_muted"),
                                 cursor="hand2")
            value.pack(side="right", padx=8)
            for widget in (row, name, value):
                widget.bind("<Button-1>", lambda _e, c=spec.cid: self._select_cursor(c))

    def _render_readout(self) -> None:
        rows: list[tuple[str, str]] = []
        for entry in self.cursors.readout():
            x_unit, y_unit = self._units(entry["axes_index"])
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
                x_unit, y_unit = self._units(item["axes_index"])
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
        # Every row that isn't shown for every single kind is registered
        # here so `_apply_schema()` can toggle it; see the class docstring
        # for why this is `pack(before=sentinel)`-based rather than a bare
        # show/hide.
        self._field_groups: list[tuple[ctk.CTkFrame, list[tuple[ctk.CTkFrame, str]]]] = []

        stacked_label(parent, t("¿Qué querés agregar?"))
        self.kind_var = ctk.StringVar(value=t(list(ANNOTATION_KINDS)[0]))
        ctk.CTkComboBox(parent, values=[t(k) for k in ANNOTATION_KINDS],
                        variable=self.kind_var,
                        height=28, font=font("body"), dropdown_font=font("body"),
                        command=lambda _=None: self._on_kind_change()
                        ).pack(fill="x", pady=(0, 12))

        # "Eje": which of the currently plotted axes (Y1/Y2, or two
        # independent Bode subplots) this annotation is anchored to. Built
        # unconditionally but only ever shown once `_refresh_axes_combo()`
        # sees more than one axis on screen -- the common case (a single
        # plot, no secondary Y) never has to think about this at all. This
        # used to be decided implicitly by wherever "Capturar X/Y" happened
        # to land a click, which -- for two axes stacked exactly on top of
        # each other via `twinx()` -- Matplotlib resolves by z-order, in
        # practice almost always the secondary one; there was no way to
        # deliberately target the primary axis by hand. `_capture()` below
        # still updates this selector from a click too, so the two ways of
        # picking an axis (click, or pick from this list first) always agree.
        self.axes_var = ctk.StringVar(value=self.axes_labels[0])
        self.axes_combo, self.axes_field_row = self._wrap(
            parent, combo_field, t("¿En qué eje va?"), self.axes_var, self.axes_labels,
            command=lambda _=None: self._on_axes_change())
        self.axes_field_row.pack_forget()   # shown by `_refresh_axes_combo`
        # Dedicated zero-size sentinel, built right after the row it belongs
        # to -- same pattern as every other group in this class (`_sentinel`
        # docstring). `_axes_after_sentinel()` re-inserts the row
        # `before=` THIS marker, not before some unrelated widget, so it
        # always lands back between "Tipo" and "Texto" no matter how many
        # times it is hidden and shown again.
        self._axes_sentinel = self._sentinel(parent)

        self.text_var = ctk.StringVar(value="")
        stacked_entry(parent, t("Texto que se muestra"), self.text_var)
        hint(parent, t("Podés escribir fórmulas, ej: $f_0 = 9{,}61\\,$kHz"),
             wraplength=230).pack(fill="x", pady=(0, 12))

        self.vars: dict[str, ctk.StringVar] = {
            name: ctk.StringVar(value=default) for name, default in (
                ("x", "0"), ("y", "0"), ("x2", "0"), ("y2", "0"),
                ("dx", "26"), ("dy", "20"), ("fontsize", "8"),
                ("linewidth", "0.9"), ("rotation", "0"), ("label_pos", "0.5"),
                ("alpha", "1.0"), ("color", "#2A2724"),
                ("label_x", "0"), ("label_y", "0"), ("axis_fontsize", "0"),
            )
        }
        self.linestyle_var = ctk.StringVar(value="--")
        self.arrow_var = ctk.StringVar(value="->")
        self.boxed_var = ctk.BooleanVar(value=True)
        # Advanced label placement, shared by "line"/"vline"/"hline"/
        # "dimension": follow the line's on-screen angle instead of a
        # manual `rotation`, or drop the automatic position entirely for an
        # explicit (label_x, label_y).
        self.label_parallel_var = ctk.BooleanVar(value=False)
        self.label_free_var = ctk.BooleanVar(value=False)
        self.dim_orientation_var = ctk.StringVar(value="auto")
        self.dim_auto_text_var = ctk.BooleanVar(value=True)
        # Default OFF: clicking on the chart ("Marcar un punto en el
        # gráfico", below) is now the primary way to place a mark -- typing
        # raw coordinates is the opt-in advanced path this checkbox reveals
        # (see `_apply_schema`). `_load_form` turns it on when editing an
        # EXISTING annotation, since seeing/nudging its exact numbers is
        # exactly what editing is for.
        self.manual_coords_var = ctk.BooleanVar(value=False)
        self.widgets: dict[str, list] = {}

        # ---- location: click-to-place first, typed coordinates opt-in ---- #
        capture = ctk.CTkFrame(parent, fg_color="transparent")
        capture.pack(fill="x", pady=(0, 4))
        primary_button(capture, t("Marcar un punto en el gráfico"),
                       lambda: self._capture("p1"), height=28).pack(fill="x")
        p2_group = self._new_group(self._sentinel(capture))
        p2_btn, p2_row = self._wrap(capture, ghost_button, t("Marcar el segundo punto"),
                                    lambda: self._capture("p2"), height=28)
        p2_btn.pack(fill="x", pady=(6, 0))
        p2_group.append((p2_row, "_capture_p2"))

        self.annotation_hint = hint(parent, "", wraplength=230)
        self.annotation_hint.pack(fill="x", pady=(2, 8))

        check_field(parent, t("Prefiero escribir las coordenadas"),
                    self.manual_coords_var,
                    command=lambda: self._apply_schema(self._kind()))
        hint(parent, t("Escribí los valores exactos si preferís no hacer "
                       "clic en el gráfico."),
             wraplength=230).pack(fill="x", pady=(0, 10))

        coords = ctk.CTkFrame(parent, fg_color="transparent", width=1, height=1)
        coords.pack(fill="x")
        coords_group = self._new_group(self._sentinel(coords))
        x_entry, x_row = self._wrap(coords, entry_field, "X", self.vars["x"], label_width=60)
        y_entry, y_row = self._wrap(coords, entry_field, "Y", self.vars["y"], label_width=60)
        x2_entry, x2_row = self._wrap(coords, entry_field, "X₂", self.vars["x2"], label_width=60)
        y2_entry, y2_row = self._wrap(coords, entry_field, "Y₂", self.vars["y2"],
                                      label_width=60, rule=False)
        self.widgets["x"], self.widgets["y"] = [x_entry], [y_entry]
        self.widgets["x2"], self.widgets["y2"] = [x2_entry], [y2_entry]
        coords_group += [(x_row, "x"), (y_row, "y"), (x2_row, "x2"), (y2_row, "y2")]

        # "Cota" only: how the two captured points become a dimension line,
        # and whether its text is the measured distance (recomputed on every
        # Agregar/Actualizar) or whatever was typed by hand.
        dim_group = self._new_group(self._sentinel(parent))
        dim_orientation_combo, dim_orientation_row = self._wrap(
            parent, combo_field, t("¿Cómo medir?"), self.dim_orientation_var,
            DIM_ORIENTATIONS, labels={k: t(v) for k, v in _DIM_ORIENTATION_LABELS.items()},
            width=110)
        dim_auto_check, dim_auto_row = self._wrap(
            parent, check_field, t("Completar el texto automáticamente"), self.dim_auto_text_var)
        dim_group += [(dim_orientation_row, "dim_orientation"),
                      (dim_auto_row, "dim_auto_text")]
        self.widgets["dim_orientation"] = [dim_orientation_combo]
        self.widgets["dim_auto_text"] = [dim_auto_check]
        hint(parent, t("«Automática» mide la distancia real entre los dos "
                       "puntos. «Horizontal» o «Vertical» miden solo en esa "
                       "dirección."), wraplength=230).pack(fill="x", pady=(0, 10))

        # Quick-start shortcut: pick a ready-made look without ever opening
        # "Personalizar" below -- the fastest path to something presentable.
        stacked_label(parent, t("Estilo ya armado"))
        self.preset_var = ctk.StringVar(value=t(list(STYLE_PRESETS)[0]))
        ctk.CTkComboBox(parent, values=[t(k) for k in STYLE_PRESETS],
                        variable=self.preset_var,
                        height=28, font=font("body"), dropdown_font=font("body")
                        ).pack(fill="x", pady=(0, 6))
        ghost_button(parent, t("Usar esta plantilla"), self._apply_preset
                    ).pack(fill="x", pady=(0, 2))
        hint(parent, t("Aplica un conjunto de estilos ya elegidos. Podés "
                       "seguir ajustando en «Personalizar»."),
             wraplength=230).pack(fill="x", pady=(0, 12))

        # Everything from here down is optional: a novice can create a
        # perfectly good annotation without ever opening any of these three
        # accordions (colour/line/typography, fine label placement, axis
        # mark) -- they all start COLLAPSED now, grouped under one
        # "Personalizar" master toggle. `StaticSection` was already built to
        # fold safely (body unmapped, not destroyed -- no relayout, no
        # `<Configure>` binding involved, unlike the Fase 5 bug in `hint()`,
        # which this reuses nothing of) and `SectionGroup` already drives
        # exactly this "Expandir todo" pattern for the settings pane in
        # `App._build_inspector` -- reusing both here instead of inventing
        # a new mechanism. On TOP of that folding, each row below is also
        # schema-gated (see the class docstring): a kind that never uses
        # "Tipo de línea" no longer shows a disabled row for it even once
        # "Estilo visual" is expanded -- the row simply isn't there.
        self.style_sections = SectionGroup()
        style_header = SectionHeader(parent, t("Personalizar"),
                                     action=t("Expandir todo"),
                                     command=self._toggle_style_sections)
        style_header.pack(fill="x", pady=(4, 6))
        self._style_header = style_header

        def _on_style_toggle(_section) -> None:
            self._refresh_style_toggle_label()

        # ---- Estilo visual: color, line/arrow, size, box, transparency,
        # typography -- everything about how it LOOKS, all in one place
        # (this used to be split across three differently-named sections;
        # "Opacidad" in particular used to live inside "Posición de la
        # etiqueta" by historical accident, not by design). ---------------- #
        appearance = StaticSection(parent, t("Estilo visual"), expanded=False,
                                   on_toggle=_on_style_toggle)
        appearance.pack(fill="x", pady=(0, 8))
        self.style_sections.add(appearance)
        box = appearance.body

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

        style_group = self._new_group(self._sentinel(box))
        linestyle_combo, linestyle_row = self._wrap(
            box, combo_field, t("Tipo de línea"), self.linestyle_var, LINESTYLES,
            labels={k: t(v) for k, v in _LINESTYLE_LABELS.items()}, width=140)
        arrow_combo, arrow_row = self._wrap(
            box, combo_field, t("Tipo de flecha"), self.arrow_var, ARROW_STYLES,
            labels={k: t(v) for k, v in _ARROWSTYLE_LABELS.items()}, width=140)
        linewidth_entry, linewidth_row = self._wrap(
            box, entry_field, t("Grosor"), self.vars["linewidth"], width=64)
        self.widgets["linestyle"] = [linestyle_combo]
        self.widgets["arrow"] = [arrow_combo]
        self.widgets["linewidth"] = [linewidth_entry]
        style_group += [(linestyle_row, "linestyle"), (arrow_row, "arrow"),
                        (linewidth_row, "linewidth")]

        # "Tamaño de letra" and "Fondo alrededor del texto" apply to every
        # kind (see `ANNOTATION_SCHEMA`, every set includes
        # "fontsize"/"boxed"), so unlike the three above they are built
        # directly, never hidden.
        self.widgets["fontsize"] = [entry_field(box, t("Tamaño de letra"), self.vars["fontsize"],
                                                 width=64)]
        self.widgets["boxed"] = [check_field(box, t("Fondo alrededor del texto"), self.boxed_var)]

        alpha_group = self._new_group(self._sentinel(box))
        alpha_entry, alpha_row = self._wrap(
            box, entry_field, t("Transparencia"), self.vars["alpha"])
        self.widgets["alpha"] = [alpha_entry]
        alpha_group.append((alpha_row, "alpha"))

        self.fontfamily_var = ctk.StringVar(value=FONT_DEFAULT)
        self.fontweight_var = ctk.StringVar(value="normal")
        self.fontstyle_var = ctk.StringVar(value="normal")
        self.ha_var = ctk.StringVar(value="center")
        self.va_var = ctk.StringVar(value="center")

        self.widgets["fontfamily"] = [combo_field(
            box, t("Tipo de letra"), self.fontfamily_var,
            [FONT_DEFAULT] + FONT_FAMILIES, width=140)]
        self.widgets["fontweight"] = [combo_field(
            box, t("Negrita"), self.fontweight_var, FONT_WEIGHTS,
            labels={k: t(v) for k, v in _FONT_WEIGHT_LABELS.items()}, width=90)]
        self.widgets["fontstyle"] = [combo_field(
            box, t("Cursiva"), self.fontstyle_var, FONT_STYLES,
            labels={k: t(v) for k, v in _FONT_STYLE_LABELS.items()}, width=90)]
        self.widgets["ha"] = [combo_field(
            box, t("Alineación horizontal"), self.ha_var, HA_CHOICES,
            labels={k: t(v) for k, v in _HA_LABELS.items()}, width=110)]
        self.widgets["va"] = [combo_field(
            box, t("Alineación vertical"), self.va_var, VA_CHOICES,
            labels={k: t(v) for k, v in _VA_LABELS.items()}, width=110,
            rule=False)]
        hint(box, t("La tipografía elegida aplica al texto normal. Lo que "
                    "esté entre signos $...$ usa el formato de fórmulas."),
             wraplength=230).pack(fill="x", pady=(4, 0))
        self.widgets["text"] = []

        # ---- Ubicación del texto: fine placement of the floating label --- #
        placement = StaticSection(parent, t("Ubicación del texto"),
                                  expanded=False, on_toggle=_on_style_toggle)
        placement.pack(fill="x", pady=(0, 8))
        self.style_sections.add(placement)
        box = placement.body

        placement_group = self._new_group(self._sentinel(box))
        dx_entry, dx_row = self._wrap(box, entry_field, t("Separación horizontal"), self.vars["dx"], suffix="pt")
        dy_entry, dy_row = self._wrap(box, entry_field, t("Separación vertical"), self.vars["dy"], suffix="pt")
        rotation_entry, rotation_row = self._wrap(
            box, entry_field, t("Girar el texto"), self.vars["rotation"], suffix="°")
        label_pos_entry, label_pos_row = self._wrap(
            box, entry_field, t("Posición a lo largo de la línea"), self.vars["label_pos"])
        label_parallel_check, label_parallel_row = self._wrap(
            box, check_field, t("Girar el texto junto con la línea"), self.label_parallel_var)
        self.widgets["dx"] = [dx_entry]
        self.widgets["dy"] = [dy_entry]
        self.widgets["rotation"] = [rotation_entry]
        self.widgets["label_pos"] = [label_pos_entry]
        self.widgets["label_parallel"] = [label_parallel_check]
        placement_group += [(dx_row, "dx"), (dy_row, "dy"), (rotation_row, "rotation"),
                            (label_pos_row, "label_pos"),
                            (label_parallel_row, "label_parallel")]

        # Grouped as a block: capturing/typing a fixed point only matters
        # once that point is actually being used, so all four rows below
        # are shown/hidden together (all keyed "label_free" -- every kind
        # that has any of them has all of them, see `ANNOTATION_SCHEMA`).
        free_group = self._new_group(self._sentinel(box))
        free_check, free_check_row = self._wrap(
            box, check_field, t("Elegir un punto fijo para el texto"), self.label_free_var)
        label_x_entry, label_x_row = self._wrap(box, entry_field, t("Posición X del texto"), self.vars["label_x"])
        label_y_entry, label_y_row = self._wrap(box, entry_field, t("Posición Y del texto"), self.vars["label_y"])
        capture_label_btn, capture_label_row = self._wrap(
            box, ghost_button, t("Marcar posición en el gráfico"),
            lambda: self._capture("label"), height=28)
        capture_label_btn.pack(fill="x", pady=(0, 10))
        self.widgets["label_free"] = [free_check, capture_label_btn]
        self.widgets["label_x"] = [label_x_entry]
        self.widgets["label_y"] = [label_y_entry]
        free_group += [(free_check_row, "label_free"), (label_x_row, "label_free"),
                      (label_y_row, "label_free"), (capture_label_row, "label_free")]

        hint_group = self._new_group(self._sentinel(box))
        _, placement_hint_row = self._wrap(
            box, hint, t("El texto puede girar junto con la línea, o "
                        "quedar fijo en un punto que vos elijas."),
            wraplength=230)
        placement_hint_row.winfo_children()[0].pack(fill="x", pady=(0, 10))
        hint_group.append((placement_hint_row, "_placement_hint"))

        # ---- Marca sobre el eje: vline/hline only (see `ANNOTATION_SCHEMA`)
        # -- stamps a tick at the line's own position, with text and font
        # independent from the floating label above. A whole accordion, not
        # just its rows, is hidden for every other kind -- see
        # `_apply_schema`. --------------------------------------------------#
        self.on_axis_var = ctk.BooleanVar(value=False)
        axis_section = StaticSection(parent, t("Marca sobre el eje"),
                                     expanded=False, on_toggle=_on_style_toggle)
        axis_section.pack(fill="x", pady=(0, 8))
        self.style_sections.add(axis_section)
        self._axis_section = axis_section
        self._axis_section_sentinel = self._sentinel(parent)
        box = axis_section.body

        axis_group = self._new_group(self._sentinel(box))
        on_axis_check, on_axis_row = self._wrap(
            box, check_field, t("Mostrar este valor en el eje"), self.on_axis_var)
        self.axis_side_var = ctk.StringVar(value="bottom")
        axis_side_combo, axis_side_row = self._wrap(
            box, combo_field, t("¿De qué lado?"), self.axis_side_var,
            _AXIS_SIDE_OPTIONS["vline"],
            labels={k: t(v) for k, v in _AXIS_SIDE_LABELS.items()}, width=110)
        self.axis_side_combo = axis_side_combo
        self.axis_text_var = ctk.StringVar(value="")
        axis_text_entry, axis_text_row = self._wrap(
            box, stacked_entry, t("Texto a mostrar en el eje"), self.axis_text_var)
        self.axis_fontfamily_var = ctk.StringVar(value=FONT_DEFAULT)
        self.axis_fontweight_var = ctk.StringVar(value="bold")
        self.axis_fontstyle_var = ctk.StringVar(value="normal")
        axis_fontfamily_combo, axis_fontfamily_row = self._wrap(
            box, combo_field, t("Tipo de letra"), self.axis_fontfamily_var,
            [FONT_DEFAULT] + FONT_FAMILIES, width=140)
        axis_fontweight_combo, axis_fontweight_row = self._wrap(
            box, combo_field, t("Negrita"), self.axis_fontweight_var,
            FONT_WEIGHTS, labels={k: t(v) for k, v in _FONT_WEIGHT_LABELS.items()}, width=90)
        axis_fontstyle_combo, axis_fontstyle_row = self._wrap(
            box, combo_field, t("Cursiva"), self.axis_fontstyle_var,
            FONT_STYLES, labels={k: t(v) for k, v in _FONT_STYLE_LABELS.items()}, width=90)
        axis_fontsize_entry, axis_fontsize_row = self._wrap(
            box, entry_field, t("Tamaño de letra"), self.vars["axis_fontsize"],
            suffix="pt", width=70, rule=False)
        self.widgets["on_axis"] = [on_axis_check]
        self.widgets["axis_side"] = [axis_side_combo]
        self.widgets["axis_text"] = [axis_text_entry]
        self.widgets["axis_fontfamily"] = [axis_fontfamily_combo]
        self.widgets["axis_fontweight"] = [axis_fontweight_combo]
        self.widgets["axis_fontstyle"] = [axis_fontstyle_combo]
        self.widgets["axis_fontsize"] = [axis_fontsize_entry]
        axis_group += [(on_axis_row, "on_axis"), (axis_side_row, "axis_side"),
                       (axis_text_row, "axis_text"),
                       (axis_fontfamily_row, "axis_fontfamily"),
                       (axis_fontweight_row, "axis_fontweight"),
                       (axis_fontstyle_row, "axis_fontstyle"),
                       (axis_fontsize_row, "axis_fontsize")]

        axis_hint_group = self._new_group(self._sentinel(box))
        _, axis_hint_row = self._wrap(
            box, hint,
            t("Si dejás el texto vacío, se muestra el mismo Texto de "
              "arriba. El tamaño y la tipografía de acá son "
              "independientes de la etiqueta flotante."), wraplength=230)
        axis_hint_row.winfo_children()[0].pack(fill="x", pady=(4, 10))
        axis_hint_group.append((axis_hint_row, "_axis_mark_hint"))

        # Same stacked layout as the cursor pane's action row, and for the
        # same reason: fixed-width buttons in one row don't fit the
        # navigator column.
        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(fill="x")
        primary_button(actions, t("Agregar al gráfico"), self._add_annotation,
                       height=28).pack(fill="x")
        ghost_button(actions, t("Guardar cambios"), self._update_annotation,
                     height=28).pack(fill="x", pady=(6, 0))
        ghost_button(actions, t("Eliminar"), self._remove_annotation,
                     height=28).pack(fill="x", pady=(6, 0))

        Rule(parent).pack(fill="x", pady=12)
        SectionHeader(parent, t("Anotaciones en este gráfico"), action=t("Borrar todas"),
                      command=self._clear_annotations).pack(fill="x", pady=(0, 6))
        # A plain frame, not a scroll region: the pane around it already
        # scrolls, and nesting two of them drives the configure/resize
        # feedback loop that had to be removed from the main window.
        self.annotation_list = ctk.CTkFrame(parent, fg_color="transparent",
                                            width=1, height=1)
        self.annotation_list.pack(fill="x")

        io_bar = ctk.CTkFrame(parent, fg_color="transparent")
        io_bar.pack(fill="x", pady=(10, 0))
        ghost_button(io_bar, t("Guardar en un archivo"), self._save_overlays,
                     height=28).pack(fill="x")
        ghost_button(io_bar, t("Cargar desde un archivo"), self._load_overlays,
                     height=28).pack(fill="x", pady=(6, 0))

        self._on_kind_change()

    # ------------------------------ axes selector ------------------------- #
    def _axes_labels_from_provider(self) -> list[str]:
        if self.axes_provider is None:
            return [t("Principal")]
        try:
            labels = list(self.axes_provider())
        except Exception:
            labels = []
        return labels or [t("Principal")]

    def _refresh_axes_combo(self) -> None:
        """
        Re-read `axes_provider()` and update the "Eje" combo -- called from
        `refresh_all()`, which `App.update_plot()` already runs after every
        single redraw, so this never goes stale: switching plot mode,
        toggling a trace's "secondary_y" or flipping the Bode layout all
        flow through the same `update_plot()` -> `refresh_all()` path.
        """
        labels = self._axes_labels_from_provider()
        self.axes_labels = labels
        self.axes_combo.configure(values=labels)
        if self._axes_index >= len(labels):
            self._axes_index = 0
        if self.axes_var.get() not in labels:
            self.axes_var.set(labels[self._axes_index])
        else:
            self._axes_index = labels.index(self.axes_var.get())
        if len(labels) > 1:
            self.axes_field_row.pack(fill="x", pady=(0, 12), before=self._axes_after_sentinel())
        else:
            self.axes_field_row.pack_forget()

    def _axes_after_sentinel(self):
        """
        The "Eje" row re-inserts itself `before=self._axes_sentinel`, a
        dedicated zero-size marker frame built immediately after it (see
        `_build_annotation_pane`) -- this is what keeps it landing back
        between "Tipo" and "Texto" every time `_refresh_axes_combo()` shows
        it again, instead of drifting to wherever `pack()` would otherwise
        append it.
        """
        return self._axes_sentinel

    def _on_axes_change(self) -> None:
        label = self.axes_var.get()
        if label in self.axes_labels:
            self._axes_index = self.axes_labels.index(label)

    def _sync_axes_from_capture(self, axes_index: int) -> None:
        if 0 <= axes_index < len(self.axes_labels):
            self._axes_index = axes_index
            self.axes_var.set(self.axes_labels[axes_index])

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

    def _toggle_style_sections(self) -> None:
        self.style_sections.set_all(not self.style_sections.any_expanded())
        self._refresh_style_toggle_label()

    def _refresh_style_toggle_label(self) -> None:
        # Same fix App._refresh_toggle_all_label already had to make for the
        # settings pane: the label has to track whichever way the LAST
        # remaining open/closed section flips, including one folded by
        # clicking its own caret directly, not just via this link.
        label = (t("Minimizar todo") if self.style_sections.any_expanded()
                else t("Expandir todo"))
        self._style_header.action_label.configure(text=label)

    def _sync_axis_side_options(self, kind: str) -> None:
        """
        Swap the "Lado del eje" combo's options for the kind's own pair
        ("bottom"/"top" for vline, "left"/"right" for hline) -- one shared
        combo instead of two kind-specific ones, since only one of "line"/
        "vline"/"hline" is ever active at a time. `configure_values` only
        touches the dropdown's option list, not its geometry, so this is
        safe to call on every kind change (unlike a `wraplength` recompute,
        see the Fase 5 bug this codebase already hit with that).
        """
        options = _AXIS_SIDE_OPTIONS.get(kind, _AXIS_SIDE_OPTIONS["vline"])
        self.axis_side_combo.configure_values(
            options, labels={k: t(_AXIS_SIDE_LABELS[k]) for k in options})
        if self.axis_side_var.get() not in options:
            self.axis_side_var.set(options[0])

    def _on_kind_change(self) -> None:
        kind = self._kind()
        self._apply_schema(kind)
        # Advanced label placement is opt-in per annotation, not per kind
        # (KIND_DEFAULTS never sets it): reset it on every kind switch so a
        # "parallel"/"point" choice made for one line doesn't silently carry
        # over into the next annotation being built.
        self.label_parallel_var.set(False)
        self.label_free_var.set(False)
        self.on_axis_var.set(False)
        self.dim_orientation_var.set("auto")
        self.dim_auto_text_var.set(True)
        self._sync_axis_side_options(kind)
        self.axis_text_var.set("")
        self.axis_fontfamily_var.set(FONT_DEFAULT)
        self.axis_fontweight_var.set("bold")
        self.axis_fontstyle_var.set("normal")
        for key, value in KIND_DEFAULTS.get(kind, {}).items():
            if key == "boxed":
                self.boxed_var.set(bool(value))
            elif key == "arrow":
                self.arrow_var.set(str(value))
            elif key == "linestyle":
                self.linestyle_var.set(str(value))
            elif key == "dim_orientation":
                self.dim_orientation_var.set(str(value))
            elif key == "dim_auto_text":
                self.dim_auto_text_var.set(bool(value))
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

    def _capture(self, target: str = "p1") -> None:
        """
        Arm a one-shot canvas click. `target` selects which field pair the
        clicked point fills: "p1" (X/Y), "p2" (X₂/Y₂) or "label" (the free
        text position) -- capturing a point for the label also flips
        `label_free` on, since clicking a point only makes sense if that
        point is then actually used instead of the automatic placement.

        Every capture also syncs the "Eje" selector to whichever axis the
        click actually landed on (`_sync_axes_from_capture`) -- without
        this, clicking straight on the secondary axis's curve would fill
        X/Y correctly but leave the form still pointed at whatever axis was
        selected before, silently mismatched.
        """
        self.cursors.disarm()

        def _done(axes_index: int, x: float, y: float) -> None:
            if target == "p2":
                self.vars["x2"].set(f"{x:.6g}")
                self.vars["y2"].set(f"{y:.6g}")
            elif target == "label":
                self.vars["label_x"].set(f"{x:.6g}")
                self.vars["label_y"].set(f"{y:.6g}")
                self.label_free_var.set(True)
            else:
                self.vars["x"].set(f"{x:.6g}")
                self.vars["y"].set(f"{y:.6g}")
            self._sync_axes_from_capture(axes_index)
            self.annotation_hint.configure(
                text=f"Capturado: X = {format_eng(x)}, Y = {format_eng(y)} "
                     f"({self.axes_labels[axes_index] if axes_index < len(self.axes_labels) else axes_index + 1})."
            )

        self.annotations.arm_pick(_done)
        self.annotation_hint.configure(
            text=t("Hacé clic sobre el punto deseado del gráfico."))

    def _dimension_auto_text(self, x: float, y: float, x2: float, y2: float,
                             orientation: str, axes_index: int) -> str:
        """
        Distance implied by the two captured points, formatted with the
        REAL unit of the axis this annotation is anchored to -- this is
        the entire reason "Cota" exists as its own kind instead of a
        manually-labelled "Flecha". Mirrors the same projection
        `AnnotationManager._render_dimension` applies (`gui/overlays.py`),
        so the printed number always matches what the rendered cota
        actually spans on screen.
        """
        x_unit, y_unit = self._units(axes_index)
        if orientation == "horizontal":
            return format_eng(abs(x2 - x), x_unit, mathtext=True)
        if orientation == "vertical":
            return format_eng(abs(y2 - y), y_unit, mathtext=True)
        # "auto": report whichever axis actually carries most of the
        # measured distance -- an arbitrary two-point capture rarely lands
        # exactly on one axis, and a unit-less Euclidean number mixing (say)
        # a voltage with a time would not mean anything physically.
        dx, dy = abs(x2 - x), abs(y2 - y)
        return (format_eng(dx, x_unit, mathtext=True) if dx >= dy
               else format_eng(dy, y_unit, mathtext=True))

    def _form_values(self) -> dict:
        kind = self._kind()
        values = {
            "kind": kind,
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
            "label_parallel": bool(self.label_parallel_var.get()),
            "label_free": bool(self.label_free_var.get()),
            "label_x": _parse_float(self.vars["label_x"].get(), 0.0),
            "label_y": _parse_float(self.vars["label_y"].get(), 0.0),
            "on_axis": bool(self.on_axis_var.get()),
            "axis_side": self.axis_side_var.get(),
            "axis_text": self.axis_text_var.get(),
            "axis_fontfamily": ("" if self.axis_fontfamily_var.get() == FONT_DEFAULT
                                else self.axis_fontfamily_var.get()),
            "axis_fontweight": self.axis_fontweight_var.get(),
            "axis_fontstyle": self.axis_fontstyle_var.get(),
            "axis_fontsize": max(0.0, _parse_float(
                self.vars["axis_fontsize"].get(), 0.0)),
            "dim_orientation": self.dim_orientation_var.get(),
            "dim_auto_text": bool(self.dim_auto_text_var.get()),
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
        if kind == "dimension" and values["dim_auto_text"]:
            computed = self._dimension_auto_text(
                values["x"], values["y"], values["x2"], values["y2"],
                values["dim_orientation"], values["axes_index"])
            values["text"] = computed
            self.text_var.set(computed)
        return values

    def _load_form(self, spec: AnnotationSpec) -> None:
        label = next((k for k, v in ANNOTATION_KINDS.items() if v == spec.kind),
                     list(ANNOTATION_KINDS)[0])
        self.kind_var.set(t(label))
        # Editing an existing annotation is exactly the "I want the precise
        # numbers" case -- reveal the coordinate fields instead of forcing
        # a re-click on "Prefiero escribir las coordenadas" every time.
        self.manual_coords_var.set(True)
        self._apply_schema(spec.kind)
        for key in ("x", "y", "x2", "y2", "dx", "dy", "fontsize", "linewidth",
                    "rotation", "label_pos", "alpha", "label_x", "label_y",
                    "axis_fontsize"):
            self.vars[key].set(f"{getattr(spec, key):g}")
        self.vars["color"].set(spec.color)
        self.text_var.set(spec.text)
        self.linestyle_var.set(spec.linestyle)
        self.arrow_var.set(spec.arrow)
        self.boxed_var.set(spec.boxed)
        self.label_parallel_var.set(spec.label_parallel)
        self.label_free_var.set(spec.label_free)
        self.on_axis_var.set(spec.on_axis)
        self.dim_orientation_var.set(spec.dim_orientation
                                     if spec.dim_orientation in DIM_ORIENTATIONS else "auto")
        self.dim_auto_text_var.set(spec.dim_auto_text)
        self._sync_axis_side_options(spec.kind)
        if spec.axis_side in _AXIS_SIDE_OPTIONS.get(spec.kind, ()):
            self.axis_side_var.set(spec.axis_side)
        self.axis_text_var.set(spec.axis_text)
        self.axis_fontfamily_var.set(spec.axis_fontfamily or FONT_DEFAULT)
        self.axis_fontweight_var.set(spec.axis_fontweight or "bold")
        self.axis_fontstyle_var.set(spec.axis_fontstyle or "normal")
        self.fontfamily_var.set(spec.fontfamily or FONT_DEFAULT)
        self.fontweight_var.set(spec.fontweight or "normal")
        self.fontstyle_var.set(spec.fontstyle or "normal")
        self.ha_var.set(spec.ha or "center")
        self.va_var.set(spec.va or "center")
        self._sync_axes_from_capture(spec.axes_index)

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
        self._refresh_axes_combo()
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
                 unit_provider: Optional[Callable[[int], tuple[str, str]]] = None,
                 axes_provider: Optional[Callable[[], list[str]]] = None,
                 on_close: Optional[Callable[[], None]] = None,
                 initial_pane: str = "cursors"):
        super().__init__(master)
        self.title(f'{t("Cursores")} / {t("Anotaciones")}')
        self.geometry("440x760")
        self.minsize(420, 600)
        self._on_close = on_close

        self.panel = OverlayPanel(self, cursors, annotations, on_refresh,
                                  unit_provider=unit_provider,
                                  axes_provider=axes_provider,
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
