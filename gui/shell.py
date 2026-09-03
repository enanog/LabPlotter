"""
gui/shell.py
------------
Redesigned main-window shell: structure only.

This is the view skeleton of the reorganisation, kept beside `gui/app.py`
rather than replacing it. `App` is ~3800 lines of working behaviour whose
methods are almost all reusable as-is; what has to change is *where its
panels live and when they are shown*, not what they do. So this file builds
the new frame and marks, with `-> App.<method>` comments, which existing
method each region should be handed. Migration is then one region at a time,
with a running application at every step.

The reorganisation in one sentence: the two settings columns are replaced by
a **navigator** (what exists) and an **inspector** (properties of whatever is
selected), with a **rail** that narrows both to one stage of the workflow.

    stage  ->  navigator shows          inspector shows
    ------------------------------------------------------------------
    data       sources / columns        the plot (figure-level settings)
    adjust     the trace list           the selected trace, or the plot
    annotate   cursors + annotations    the plot (figure-level settings)
    board      row/panel editor         the plot (figure-level settings)
    export     figure / board queue     the plot (figure-level settings)

    Only "adjust" ever has an object worth showing in "Selección" (a
    trace); every other stage leaves the inspector on "Gráfico" so it is
    never showing a placeholder for a selection concept that stage does
    not have.

Why not one more accordion: the current right panel is five equal sections
that are all always available, so every control the application owns is
always one scroll away and nothing signals what matters now. Staging cuts
what is reachable at any moment to roughly a quarter, and the numbering on
the rail states the order the work actually happens in.
"""

from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from core.i18n import t

from .theme import col, font, spaced
from .widgets import (
    ColorHeader, CommandPalette, DirtyGroup, NavRail, PaneStack, Rule,
    SectionHeader, Splitter, StaticSection, VRule, ghost_button, hint,
    info_dot, primary_button,
)

# Rail order == workflow order. The labels are the stage names the user sees;
# the keys are what the routing switches on.
#
# "board" was a floating `CTkToplevel` window opened on demand from a button
# buried in the "export" stage (see `gui/board_window.py`). Promoted to a
# stage of its own for the same reason "annotate" stopped being a floating
# `OverlayWindow`: a side window the user has to remember to reopen reads as
# "not really part of the app". As a stage, entering it swaps the workspace
# canvas to the board preview exactly like entering "annotate" already swaps
# the navigator -- see `App._show_plot_frame`.
#
# "histogram" went through the same promotion for one session and was
# reverted: the user asked for it back as one more entry in the per-plot mode
# switch (Tiempo/X-Y/Bode/Pizarra -- see `App.PLOT_MODES`), sharing the SAME
# figure/canvas those already draw into, with its settings folded into the
# "Gráfico" inspector pane instead of a dedicated navigator column. That also
# means a histogram is just whatever `self.fig` currently shows, so
# `App._add_current_to_board` (add the current plot to the board) and every
# export action already work on it with no special case.
def STAGES() -> list[tuple[str, str]]:
    """
    (key, label) pairs, translated fresh on every call.

    Was a module-level constant; `t(...)` only ran once, at import time, so
    the rail kept showing whatever language was active the first time
    `gui.shell` was imported -- language switches rebuild the rail (see
    `App._rebuild_ui` -> `_build_layout` -> `_build_rail`) but a frozen list
    never picks up the new translations. This was the "izquierda no pasa a
    ingles" bug: every other label in the app is produced by a method or a
    widget built at call time, so only this constant was stuck.
    """
    return [
        ("data", t("Datos")),
        ("adjust", t("Ajuste")),
        ("annotate", t("Anotar")),
        ("board", t("Tablero")),
        ("export", t("Exportar")),
    ]

RAIL_WIDTH = 78
NAVIGATOR_WIDTH = 236
INSPECTOR_WIDTH = 308

# Stages whose navigator content doesn't fit the default column width without
# clipping (forms with several fields side by side, not a simple list) --
# "annotate" (cursor/annotation editor) and "board" (used to be a floating
# ~1040px-wide window). `App._set_stage` widens the navigator to this on
# first entering one of them, unless the user already dragged it.
WIDE_STAGES = frozenset({"annotate", "board"})
WIDE_NAVIGATOR_WIDTH = 340


class Shell(ctk.CTk):
    """
    Main window: rail | navigator | canvas | inspector.

    Only the view is built here. Every callback is a thin seam that should
    end up calling the corresponding `App` method unchanged.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("LabPlotter")
        self.geometry("1520x900")
        self.minsize(1180, 720)
        self.configure(fg_color=col("app"))
        self._init_shell_state()
        self._build_layout()
        self._register_commands()
        self._bind_shortcuts()
        self._set_stage(self.stage_var.get())

    def _init_shell_state(self) -> None:
        """
        View state, split out of `__init__` so a subclass (`App`) can set
        its OWN state first and call this at the exact point it needs
        Shell's view-state to exist -- instead of inheriting Shell's whole
        build sequence (state -> layout -> commands -> shortcuts -> stage)
        as one block it cannot interleave its own setup with.

        `selection` is what the inspector renders; it is the single reason
        the right panel ever changes, which is what makes the panel
        learnable instead of modal.
        """
        self.stage_var = ctk.StringVar(value="adjust")
        self.selection: Optional[tuple[str, str]] = None   # ("trace", uid)
        self.navigators: dict[str, ctk.CTkFrame] = {}
        self.dirty_groups: dict[str, DirtyGroup] = {}
        self.commands: list[tuple[str, Callable[[], None]]] = []

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _build_layout(self) -> None:
        # [rail] [navigator] [splitter] [canvas w=1] [splitter] [inspector]
        self.grid_columnconfigure(3, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_topbar()
        self._build_rail()
        self._build_navigator()
        Splitter(self, on_drag=self._drag_navigator).grid(row=1, column=2,
                                                          sticky="ns")
        self._build_workspace()
        Splitter(self, on_drag=self._drag_inspector).grid(row=1, column=4,
                                                          sticky="ns")
        self._build_inspector()
        self._build_statusbar()

    def _build_topbar(self) -> None:
        """Wordmark, plot tabs, and the one action that starts a session."""
        # 64, not the original 46: the tab strip alone (a chip's label at
        # this app's own "label" font, plus the chip's internal padding,
        # plus its own outer pady) needs ~59px end to end in THIS
        # environment's fallback font -- 46 was simply smaller than what its
        # own children asked for, so every chip's name rendered squeezed
        # into a shorter box than its text actually needed (confirmed by
        # measuring winfo_height() against winfo_reqheight() at every
        # level: `content` itself requested more height than `bar` gave
        # it). 64 leaves a few pixels of real margin over that 59px instead
        # of matching it exactly -- a real font on the user's machine can
        # need a little more than this headless fallback font does.
        bar = ctk.CTkFrame(self, height=64, corner_radius=0, fg_color=col("bar"))
        bar.grid(row=0, column=0, columnspan=6, sticky="ew")
        bar.pack_propagate(False)       # children are packed
        Rule(bar, strong=True).pack(side="bottom", fill="x")

        content = ctk.CTkFrame(bar, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=18)
        self.topbar_content = content   # exposed so a subclass can add its
                                         # own left-aligned controls after
                                         # the wordmark/tabs (see App._build_topbar)
        ctk.CTkLabel(content, text=spaced("LabPlotter"), font=font("title"),
                     text_color=col("fg")).pack(side="left", pady=11)
        VRule(content, height=20).pack(side="left", padx=16, pady=13)

        # Plot tabs move up here from the canvas column: they switch the whole
        # document, so they belong beside the wordmark and not inside the one
        # region they replace. -> App._build_tab_strip / _refresh_tab_strip
        self.tab_strip = ctk.CTkFrame(content, fg_color="transparent",
                                      width=1, height=1)
        # fill+expand: without this the frame sizes to CTkFrame's own
        # default width (200px, since App._build_tab_strip disables
        # propagate on its inner "strip" without giving it an explicit
        # width either) and never grows past that no matter how many tabs
        # are open or how long a tab's name is -- chips just silently
        # overflow the fixed box instead of the strip claiming the leftover
        # room between the wordmark and the topbar's right-aligned buttons.
        self.tab_strip.pack(side="left", fill="x", expand=True, pady=6)

        ghost_button(content, "?", self._open_shortcuts,
                     width=28).pack(side="right", pady=9)
        ghost_button(content, t("Comandos  ⌘K"), self._open_palette,
                     width=124).pack(side="right", padx=8, pady=9)

    def _build_rail(self) -> None:
        """Permanent stage rail -- the only always-visible global control."""
        self.rail = NavRail(self, STAGES(), self.stage_var,
                            command=self._set_stage, width=RAIL_WIDTH)
        self.rail.grid(row=1, column=0, sticky="ns")

    def _build_navigator(self) -> None:
        """
        Column 1: the list of whatever the current stage operates on.

        One frame per stage, built once and swapped, so switching stages never
        rebuilds a list or loses its scroll position.
        """
        panel = ctk.CTkFrame(self, width=NAVIGATOR_WIDTH, corner_radius=0,
                             fg_color=col("panel"))
        panel.grid(row=1, column=1, sticky="ns")
        panel.pack_propagate(False)
        self.navigator_panel = panel

        header_row = ctk.CTkFrame(panel, fg_color="transparent",
                                  width=1, height=1)
        header_row.pack(fill="x", padx=16, pady=(14, 0))
        self.navigator_header = SectionHeader(header_row, t("Trazas"))
        self.navigator_header.pack(side="left", fill="x", expand=True)
        # One marker, retargeted per stage: what this stage is for lives
        # behind a hover instead of a permanent paragraph.
        self.navigator_info = info_dot(header_row, "")
        self.navigator_info.pack(side="right")
        Rule(panel, strong=True).pack(fill="x", padx=16, pady=(6, 10))

        holder = ctk.CTkFrame(panel, fg_color="transparent", width=1, height=1)
        holder.pack(fill="both", expand=True)
        for key, _label in STAGES():
            self.navigators[key] = ctk.CTkScrollableFrame(
                holder, fg_color="transparent", corner_radius=0)

        # -> App._refresh_signal_list fills navigators["adjust"] with TraceRow
        # -> App._refresh_chips     fills navigators["data"]
        # -> OverlayPanel cursor/annotation lists fill navigators["annotate"]
        self.navigator_footer = ctk.CTkFrame(panel, fg_color="transparent")
        self.navigator_footer.pack(side="bottom", fill="x", padx=16, pady=12)

    def _build_workspace(self) -> None:
        """Column 3: tool strip, contextual row, canvas."""
        center = ctk.CTkFrame(self, corner_radius=0, fg_color=col("app"))
        center.grid(row=1, column=3, sticky="nsew")
        center.grid_rowconfigure(2, weight=1)
        center.grid_columnconfigure(0, weight=1)
        self.workspace = center

        strip = ctk.CTkFrame(center, height=44, corner_radius=0,
                             fg_color=col("bar"))
        strip.grid(row=0, column=0, sticky="ew")
        strip.pack_propagate(False)
        Rule(strip).pack(side="bottom", fill="x")
        # Stage-specific tools: cursor/annotate buttons only exist in the
        # annotate stage, zoom/pan/fit in every stage. -> App._select_tool
        self.tool_strip = ctk.CTkFrame(strip, fg_color="transparent")
        self.tool_strip.pack(fill="both", expand=True, padx=16)

        # Mode-specific row (X/Y colour, Bode layout). Height 1 so it takes no
        # space when empty. -> App._build_xy_controls / bode controls
        self.context_row = ctk.CTkFrame(center, corner_radius=0,
                                        fg_color=col("bar"), height=1)
        self.context_row.grid(row=1, column=0, sticky="ew")

        self.plot_container = ctk.CTkFrame(center, corner_radius=0,
                                           fg_color=col("app"))
        self.plot_container.grid(row=2, column=0, sticky="nsew", padx=20,
                                 pady=18)
        # -> App: FigureCanvasTkAgg + EditableNavigationToolbar + MeasurementsCard

    def _build_inspector(self) -> None:
        """
        Column 5: properties of the current selection.

        Two panes, not five sections. "Selección" is whatever is selected --
        a trace, a cursor, an annotation -- and "Gráfico" is everything that
        belongs to the figure itself. That split is what removes the current
        ambiguity where 'Unidad X' exists in both columns meaning two
        different things (source unit vs. display unit): now they can never
        be on screen at the same time, and each sits under the object it
        actually belongs to.
        """
        panel = ctk.CTkFrame(self, width=INSPECTOR_WIDTH, corner_radius=0,
                             fg_color=col("panel"))
        panel.grid(row=1, column=5, sticky="ns")
        panel.pack_propagate(False)
        self.inspector_panel = panel

        subject_row = ctk.CTkFrame(panel, fg_color="transparent",
                                   width=1, height=1)
        subject_row.pack(fill="x", padx=16, pady=(14, 0))
        self.subject = ColorHeader(subject_row)
        self.subject.pack(side="left", fill="x", expand=True)
        # What tells "Selección" and "Gráfico" apart isn't visible from the
        # tab names alone -- one is per-trace, the other whole-figure, and
        # both are reachable from every stage. Same info_dot pattern as
        # `navigator_info` in `_build_navigator`, not a new widget language;
        # placed beside the subject header rather than inside `PaneStack`
        # (whose own header is rebuilt on every `add()`/language change, so
        # anything packed into it directly would need to survive that).
        info_dot(subject_row,
                 t("Selección: ajustes de la traza elegida en la lista. "
                   "Gráfico: ajustes de toda la figura (ejes, leyenda, "
                   "exportación) -- no cambian según qué traza esté "
                   "seleccionada.")).pack(side="right")
        Rule(panel, strong=True).pack(fill="x", padx=16, pady=(8, 12))

        self.inspector = PaneStack(panel)
        self.inspector.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.pane_selection = self.inspector.add("selection", t("Selección"))
        self.pane_plot = self.inspector.add("plot", t("Gráfico"))

        self._build_selection_pane(self.pane_selection)
        self._build_plot_pane(self.pane_plot)

    def _build_selection_pane(self, parent) -> None:
        """
        Rebuilt on every selection change. Three sections, in the order a
        trace is actually worked on: how it looks, how it is corrected, where
        it came from.  -> App._build_param_panel
        """
        self.selection_body = ctk.CTkFrame(parent, fg_color="transparent",
                                           width=1, height=1)
        self.selection_body.pack(fill="both", expand=True)
        self._build_selection_placeholder()

    def _build_selection_placeholder(self) -> None:
        for widget in self.selection_body.winfo_children():
            widget.destroy()
        hint(self.selection_body,
             t("Seleccioná una traza de la lista para ver sus ajustes."),
             wraplength=250).pack(fill="x", pady=20)

    def _build_plot_pane(self, parent) -> None:
        """
        Figure-level settings. Same five groups as today minus the per-trace
        ones, each badged by a `DirtyGroup` so a folded section still reports
        how many of its values are off default.
        """
        for key, title, expanded in (
                ("axes", t("Ejes y escalas"), True),
                ("labels", t("Etiquetas"), False),
                ("legend", t("Leyenda"), False),
                ("data", t("Datos"), False)):
            section = StaticSection(parent, title, expanded=expanded)
            section.pack(fill="x", pady=(0, 12))
            self.dirty_groups[key] = DirtyGroup(section)
            # -> App._build_axes_section / _build_labels_section /
            #    _build_legend_section / _build_data_section, each field
            #    wrapped as: group.add(DirtyDot(row, var, default))
            setattr(self, f"section_{key}", section)

        ghost_button(parent, t("Aplicar ajustes"), self._apply_settings,
                     height=28).pack(fill="x", pady=(4, 0))

    def _build_statusbar(self) -> None:
        """
        Persistent bottom strip. The status text currently shares the tool
        strip with eight controls, where it is the first thing that gets
        clipped on a narrow window.
        """
        bar = ctk.CTkFrame(self, height=26, corner_radius=0, fg_color=col("bar"))
        bar.grid(row=2, column=0, columnspan=6, sticky="ew")
        bar.pack_propagate(False)
        Rule(bar).pack(side="top", fill="x")
        self.status_label = ctk.CTkLabel(bar, text="", font=font("hint"),
                                         text_color=col("fg_faint"))
        self.status_label.pack(side="left", padx=16)
        self.hint_label = hint(bar, "")
        self.hint_label.pack(side="right", padx=16)

    # ------------------------------------------------------------------ #
    # Stage and selection routing
    # ------------------------------------------------------------------ #
    def _set_stage(self, key: str) -> None:
        """Swap navigator, tool strip and inspector default pane."""
        for name, frame in self.navigators.items():
            frame.pack_forget()
            if name == key:
                frame.pack(fill="both", expand=True, padx=(16, 8))
        self.navigator_header.set_title(self._navigator_title(key))
        self.navigator_info.tooltip.set_text(self._stage_help(key))
        self._build_stage_tools(key)
        self._build_stage_footer(key)
        # Only "adjust" ever has something to select (a trace), so it is the
        # only stage where "Selección" means anything -- showing it for
        # "annotate" too used to leave the trace-specific placeholder text
        # ("Seleccioná una traza...") on screen while cursors/annotations
        # live entirely in the navigator now, which read as a dead/broken
        # panel rather than "there is nothing to select here".
        self.inspector.set("selection" if key == "adjust" else "plot")
        self._sync_inspector()

    @staticmethod
    def _navigator_title(key: str) -> str:
        return {"data": t("Archivos de datos"), "adjust": t("Trazas"),
                "annotate": t("Cursores y anotaciones"),
                "board": t("Tablero de figuras"),
                "export": t("Exportar")}.get(key, "")

    @staticmethod
    def _stage_help(key: str) -> str:
        return {
            "data": t("Archivos cargados y qué columna usa cada traza."),
            "adjust": t("Estilo, correcciones y unidades de cada traza."),
            "annotate": t("Cursores de medición y anotaciones de la figura."),
            "board": t("Combina varias figuras ya exportadas en una grilla para el informe."),
            "export": t("Formato, DPI y el bloque LaTeX que incluye la figura."),
        }.get(key, "")

    def _build_stage_tools(self, key: str) -> None:
        """Only the tools this stage uses.  -> App._select_tool / _set_tool"""
        for widget in self.tool_strip.winfo_children():
            widget.destroy()
        # -> plot mode Segmented always; cursor/annotate ToolButtons only for
        #    "annotate"; zoom/pan/fit always; export preview toggle for
        #    "export".

    def _build_stage_footer(self, key: str) -> None:
        """The stage's one primary action, always in the same place."""
        for widget in self.navigator_footer.winfo_children():
            widget.destroy()
        actions = {
            "data": (t("+  Abrir archivo"), self._load_files),
            "adjust": (t("Quitar"), self._remove_selected_signal),
            # "annotate", "board" and "export" have no primary-button
            # action here on purpose: each of those stages'
            # navigator (-> App._build_overlay_panel / _build_board_panel /
            # _build_export_navigator) already ends with its own primary
            # button(s) built into the content itself, so a footer button
            # would just be the same action offered a second time in the
            # same column.
        }
        label, command = actions.get(key, (None, None))
        if label is not None:
            primary_button(self.navigator_footer, label, command,
                           height=28).pack(fill="x")

    def select(self, kind: str, uid: str) -> None:
        """Single entry point for 'the user picked something'."""
        self.selection = (kind, uid)
        self.inspector.set("selection")
        self._sync_inspector()

    def _sync_inspector(self) -> None:
        """Header + body follow the selection; nothing else touches them."""
        if self.selection is None:
            self.subject.set_subject(t("Gráfico"))
            self._build_selection_placeholder()
            return
        kind, uid = self.selection
        # -> App: look the signal/overlay up and pass its colour, so the
        #    header bar, the list swatch and the Matplotlib line all agree.
        self.subject.set_subject(uid, color=None, tag=kind)

    # ------------------------------------------------------------------ #
    # Commands and shortcuts
    # ------------------------------------------------------------------ #
    def _register_commands(self) -> None:
        """
        One registry feeding the palette. Registering here rather than at each
        call site means a command exists exactly once, whatever surface
        invokes it.
        """
        self.commands = [
            (t("+  Abrir archivo"), self._load_files),
            (t("Exportar figura"), self._export_figure),
            (t("Exportar CSV"), self._export_csv),
            (t("Encuadrar"), self._fit_to_data),
            (t("Deshacer"), self._undo),
            (t("Rehacer"), self._redo),
            (t("Histograma"), self._open_histogram),
            (t("Tablero"), self._open_board),
            (t("Atajos de teclado"), self._open_shortcuts),
        ]

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-k>", lambda _e: self._open_palette())
        self.bind_all("<Control-o>", lambda _e: self._load_files())
        self.bind_all("<Control-e>", lambda _e: self._export_figure())
        self.bind_all("<Control-z>", lambda _e: self._undo())
        self.bind_all("<F1>", lambda _e: self._open_shortcuts())
        for index, (key, _label) in enumerate(STAGES(), start=1):
            self.bind_all(f"<Control-Key-{index}>",
                          lambda _e, k=key: self.rail.select(k))

    def _open_palette(self) -> None:
        CommandPalette(self, self.commands)

    # ------------------------------------------------------------------ #
    # Seams -- each of these is an existing App method, unchanged
    # ------------------------------------------------------------------ #
    def _drag_navigator(self, delta: int) -> None: ...      # -> App._drag_left
    def _drag_inspector(self, delta: int) -> None: ...      # -> App._drag_right
    def _load_files(self) -> None: ...                      # -> App._load_files
    def _remove_selected_signal(self) -> None: ...          # -> App
    def _apply_settings(self) -> None: ...                  # -> App
    def _export_figure(self) -> None: ...                   # -> App
    def _export_csv(self) -> None: ...                      # -> App
    def _fit_to_data(self) -> None: ...                     # -> App
    def _undo(self) -> None: ...                            # -> App
    def _redo(self) -> None: ...                            # -> App
    def _open_histogram(self) -> None: ...                  # -> App
    def _open_board(self) -> None: ...                      # -> App
    def _open_shortcuts(self) -> None: ...                  # -> App
