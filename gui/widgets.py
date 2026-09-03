"""
gui/widgets.py
--------------
Styled primitives shared by the main window, the overlay palette and every
dialog. Centralising them here is what keeps the aesthetic identical across
*all* surfaces: a widget built from these classes cannot drift from the
theme, because none of the colours or fonts are written at the call site.

Design rules encoded here:
  * square corners and hairline rules -- no cards, no shadows, no radii;
  * label on the left, control on the right, thin rule underneath;
  * section headers in letterspaced small caps, muted;
  * sections are static: `StaticSection` renders a header and a body that is
    always visible, because a scrolling panel has nothing to gain from
    hiding parts of itself.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional, Sequence

import customtkinter as ctk

from core.i18n import t

from .theme import BORDER, RADIUS, col, font, spaced, tk_color


# --------------------------------------------------------------------------- #
# Plain-Tk hairlines
# --------------------------------------------------------------------------- #
# Purely decorative 1px rules do not need CustomTkinter. Every CTkFrame is a
# tkinter.Frame *plus* a CTkCanvas that draws the shape, and with
# corner_radius = 0 that canvas draws a plain rectangle -- all cost, no
# benefit. There are ~20 rules on screen at once; as plain tk.Frames they
# cost a fraction of that and, more importantly, drop out of the relayout
# work done on every panel resize.
#
# The tradeoff is that plain Tk widgets do not follow CustomTkinter's
# appearance-mode switching on their own, so they are registered here and
# repainted explicitly by `repaint_plain_widgets()` when the theme changes.
_PLAIN_WIDGETS: list[tuple] = []


def _register_plain(widget, option: str, token: str) -> None:
    _PLAIN_WIDGETS.append((widget, option, token))


def repaint_plain_widgets() -> None:
    """Re-apply theme colours to every plain-Tk widget still alive."""
    survivors = []
    for widget, option, token in _PLAIN_WIDGETS:
        try:
            widget.configure(**{option: tk_color(token)})
        except Exception:
            continue   # destroyed widget: drop it from the registry
        survivors.append((widget, option, token))
    _PLAIN_WIDGETS[:] = survivors

def _hand(widget) -> None:
    """
    Hand cursor on an already-built widget.

    Post-construction `configure(cursor=...)` is not part of the documented
    CustomTkinter surface and raises on some versions, so it is guarded --
    a missing hand cursor is cosmetic, a crash on startup is not.
    """
    try:
        widget.configure(cursor="hand2")
    except Exception:
        pass


# Height of one row in a list (traces, cursors, annotations). Compact on
# purpose: these are index entries, and a panel that shows eight traces
# without scrolling is more useful than one that shows three.
ROW_HEIGHT = 24

LINE_GLYPHS: dict[str, str] = {
    "-":  "──────",
    "--": "─ ─ ─",
    "-.": "─ · ─",
    ":":  "· · · ·",
    # No connecting line at all -- just the trace's marker at each sample,
    # for data that should not read as continuous/interpolated (see
    # `LINESTYLES` in gui/app.py). Bullets, not the finer dots used for
    # ":", so the two are never confused at a glance.
    "None": "•  •  •",
}


# --------------------------------------------------------------------------- #
# Rules and headers
# --------------------------------------------------------------------------- #
class Rule(tk.Frame):
    """One-pixel horizontal hairline (plain Tk: see note above)."""

    def __init__(self, master, strong: bool = False, **kwargs):
        token = "border_str" if strong else "rule"
        kwargs.pop("fg_color", None)   # tolerated for call-site compatibility
        super().__init__(master, height=1, bd=0, highlightthickness=0,
                         bg=tk_color(token), **kwargs)
        _register_plain(self, "bg", token)


class VRule(tk.Frame):
    """One-pixel vertical hairline, for separating toolbar groups."""

    def __init__(self, master, height: int = 20, **kwargs):
        kwargs.pop("fg_color", None)
        super().__init__(master, width=1, height=height, bd=0,
                         highlightthickness=0, bg=tk_color("border"), **kwargs)
        _register_plain(self, "bg", "border")


class SectionHeader(ctk.CTkFrame):
    """Letterspaced small-caps header with an optional right-aligned action."""

    def __init__(self, master, title: str, action: Optional[str] = None,
                 command: Optional[Callable[[], None]] = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.title_label = ctk.CTkLabel(self, text=spaced(title),
                                        font=font("header"),
                                        text_color=col("fg_muted"), anchor="w")
        self.title_label.pack(side="left")
        if action is not None:
            link = ctk.CTkLabel(self, text=action, font=font("hint"),
                                text_color=col("fg_muted"), cursor="hand2")
            link.pack(side="right")
            if command is not None:
                link.bind("<Button-1>", lambda _e: command())
            self.action_label = link

    def set_title(self, title: str) -> None:
        """Retitle in place -- the navigator header changes with the stage."""
        self.title_label.configure(text=spaced(title))


# --------------------------------------------------------------------------- #
# Field rows
# --------------------------------------------------------------------------- #
class Field(ctk.CTkFrame):
    """
    One settings row: label on the left, control area on the right, hairline
    underneath. Callers pack their control into `self.control`.
    """

    def __init__(self, master, label: str, rule: bool = True,
                 label_width: int = 118, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        self.row = row   # exposed so callers can anchor a DirtyDot beside the label
        self.label = ctk.CTkLabel(row, text=label, font=font("label"),
                                  text_color=col("fg_muted"),
                                  width=label_width, anchor="w")
        self.label.pack(side="left", pady=4)
        # height/width of 1: an empty CTkFrame otherwise keeps its default
        # 200x200 request and blows the row open (see the note in app.py).
        self.control = ctk.CTkFrame(row, fg_color="transparent",
                                    width=1, height=1)
        self.control.pack(side="right")
        if rule:
            Rule(self).pack(fill="x", pady=(2, 0))


def entry_field(master, label: str, variable, suffix: str = "",
                width: int = 78, on_enter: Optional[Callable[[], None]] = None,
                rule: bool = True, label_width: int = 118,
                suffix_var: Optional[ctk.Variable] = None) -> ctk.CTkEntry:
    """
    Label + numeric entry + optional unit suffix. Returns the entry.

    `suffix` is a static label, fixed for the life of the widget. Pass
    `suffix_var` instead when the unit it displays can change at runtime
    (e.g. a per-trace unit combo next to it) -- a suffix built from `suffix`
    would otherwise keep showing the unit that was active when the field
    was built, silently mismatched against whatever unit the value is
    actually interpreted in once the surrounding unit combo changes.
    """
    field = Field(master, label, rule=rule, label_width=label_width)
    field.pack(fill="x", pady=(0, 6))
    if suffix_var is not None:
        ctk.CTkLabel(field.control, textvariable=suffix_var, font=font("small"),
                     text_color=col("fg_faint"), width=22, anchor="w"
                     ).pack(side="right", padx=(6, 0))
    elif suffix:
        ctk.CTkLabel(field.control, text=suffix, font=font("small"),
                     text_color=col("fg_faint"), width=22, anchor="w"
                     ).pack(side="right", padx=(6, 0))
    entry = ctk.CTkEntry(field.control, textvariable=variable, width=width,
                         height=26, font=font("mono"), justify="right")
    entry.pack(side="right")
    if on_enter is not None:
        entry.bind("<Return>", lambda _e: on_enter())
        entry.bind("<KP_Enter>", lambda _e: on_enter())
    return entry


def text_field(master, label: str, variable, width: int = 150,
               on_enter: Optional[Callable[[], None]] = None,
               rule: bool = True) -> ctk.CTkEntry:
    """Label + free-text entry (left-aligned, wider than a numeric field)."""
    field = Field(master, label, rule=rule)
    field.pack(fill="x", pady=(0, 6))
    entry = ctk.CTkEntry(field.control, textvariable=variable, width=width,
                         height=26, font=font("body"))
    entry.pack(side="right")
    if on_enter is not None:
        entry.bind("<Return>", lambda _e: on_enter())
    return entry


def combo_field(master, label: str, variable, values: Sequence[str],
                width: int = 150, command: Optional[Callable] = None,
                rule: bool = True, labels: Optional[dict] = None):
    """
    Label + dropdown. With `labels`, the visible text is translated while the
    variable keeps a stable internal identifier (see `LabeledCombo`).
    """
    field = Field(master, label, rule=rule)
    field.pack(fill="x", pady=(0, 6))
    if labels is not None:
        combo = LabeledCombo(field.control, values, variable, labels=labels,
                             command=command, width=width)
    else:
        combo = ctk.CTkComboBox(field.control, values=list(values),
                                variable=variable, width=width, height=26,
                                font=font("body"), dropdown_font=font("body"),
                                command=command)
    combo.pack(side="right")
    return combo


def check_field(master, label: str, variable,
                command: Optional[Callable[[], None]] = None,
                rule: bool = True) -> ctk.CTkCheckBox:
    """A checkbox that reads as a settings row rather than a form control."""
    field = Field(master, label, rule=rule, label_width=170)
    field.pack(fill="x", pady=(0, 6))
    box = ctk.CTkCheckBox(field.control, text="", width=20, checkbox_width=16,
                          checkbox_height=16, variable=variable, command=command)
    box.pack(side="right")
    return box


# --------------------------------------------------------------------------- #
# Segmented control
# --------------------------------------------------------------------------- #
class Segmented(ctk.CTkFrame):
    """
    Square segmented control bound to a StringVar. Adjacent segments overlap
    by one pixel so neighbours share a single hairline instead of doubling it.
    """

    def __init__(self, master, values: Sequence[str], variable,
                 labels: Optional[dict] = None,
                 command: Optional[Callable[[str], None]] = None,
                 width: int = 0, height: int = 26,
                 font_role: str = "label", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.variable = variable
        self.command = command
        self._enabled = True
        self.buttons: dict[str, ctk.CTkButton] = {}

        for index, value in enumerate(values):
            text = (labels or {}).get(value, value)
            button = ctk.CTkButton(
                self, text=text, height=height, corner_radius=RADIUS,
                border_width=BORDER, font=font(font_role),
                command=lambda v=value: self._choose(v))
            if width:
                button.configure(width=width)
            # Segments touch edge-to-edge (no gap). An earlier version used a
            # -1px padx so adjacent segments would share a single hairline
            # instead of doubling it, but negative pack() padding was never
            # valid in Tk -- it happened to be tolerated by some Tk builds
            # and raises TclError on others ("must be positive screen
            # distance"). Butting them together with 0 padding is correct
            # everywhere; the seam is a 2px border instead of 1px, which is
            # not visually meaningful at this scale.
            button.pack(side="left", padx=0)
            self.buttons[value] = button

        self._trace_id = variable.trace_add("write", lambda *_: self._sync())
        self._sync()

    def destroy(self) -> None:
        # Without this, a rebuilt inspector/navigator leaves the OLD button's
        # `_sync` callback registered on `self.variable` -- the variable
        # itself usually survives (it's often owned by `App`, not this
        # widget), so the next `.set()` fires the callback against buttons
        # that no longer exist ("invalid command name ..."), visible as a
        # `TclError` spammed to the console on every stage switch / rebuild.
        try:
            self.variable.trace_remove("write", self._trace_id)
        except Exception:
            pass   # variable already gone, or trace already cleared
        super().destroy()

    def _choose(self, value: str) -> None:
        if not self._enabled:
            return
        self.variable.set(value)
        if self.command is not None:
            self.command(value)

    def _sync(self) -> None:
        current = self.variable.get()
        for value, button in self.buttons.items():
            if value == current:
                button.configure(fg_color=col("accent"), hover_color=col("accent_hi"),
                                 border_color=col("accent"), text_color=col("on_accent"))
            else:
                button.configure(fg_color=col("surface"), hover_color=col("sel"),
                                 border_color=col("border"),
                                 text_color=col("fg_muted") if self._enabled else col("fg_faint"))

    def set_enabled(self, enabled: bool) -> None:
        """Disable without hiding: the control stays legible but inert."""
        self._enabled = bool(enabled)
        for button in self.buttons.values():
            button.configure(state="normal" if enabled else "disabled")
        self._sync()

    # Compatibility with the CTk widget API used elsewhere in the app.
    def configure(self, **kwargs):
        """
        Accept `state=` like a real control, forward everything else.

        CustomTkinter calls `configure` on its own widgets internally (theme
        and scaling changes among them), so this override has to be
        transparent to anything it does not explicitly handle -- and must not
        raise when handed an option `CTkFrame` does not recognise, or a theme
        switch would take the window down with it.
        """
        if "state" in kwargs:
            self.set_enabled(kwargs.pop("state") != "disabled")
        if not kwargs:
            return None
        try:
            return super().configure(**kwargs)
        except (ValueError, TypeError, tk.TclError):
            return None


def segmented_field(master, label: str, values: Sequence[str], variable,
                    labels: Optional[dict] = None,
                    command: Optional[Callable[[str], None]] = None,
                    rule: bool = True, width: int = 62) -> Segmented:
    field = Field(master, label, rule=rule)
    field.pack(fill="x", pady=(0, 6))
    control = Segmented(field.control, values, variable, labels=labels,
                        command=command, width=width)
    control.pack(side="right")
    return control


# --------------------------------------------------------------------------- #
# Tool strip
# --------------------------------------------------------------------------- #
class ToolButton(ctk.CTkButton):
    """Toolbar button with a latched (toggled-on) state."""

    def __init__(self, master, text: str, command: Optional[Callable] = None,
                 width: int = 0, **kwargs):
        super().__init__(master, text=text, command=command, height=26,
                         corner_radius=RADIUS, border_width=BORDER,
                         font=font("label"), **kwargs)
        if width:
            self.configure(width=width)
        self._active = False
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        if self._active:
            self.configure(fg_color=col("accent"), hover_color=col("accent_hi"),
                           border_color=col("accent"), text_color=col("on_accent"))
        else:
            self.configure(fg_color=col("surface"), hover_color=col("sel"),
                           border_color=col("border"), text_color=col("fg"))


class Chip(ctk.CTkFrame):
    """
    Compact source descriptor for the top bar: a monospace tag, the file name
    and its sample count. Purely informational.
    """

    def __init__(self, master, tag: str, name: str, count: str = "",
                 command: Optional[Callable[[], None]] = None, **kwargs):
        super().__init__(master, corner_radius=RADIUS, border_width=BORDER,
                         border_color=col("border"), fg_color=col("surface"),
                         **kwargs)
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(padx=8, pady=3)
        ctk.CTkLabel(inner, text=tag.upper(), font=font("mono", 9),
                     text_color=col("fg_faint")).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(inner, text=name, font=font("mono", 11),
                     text_color=col("fg")).pack(side="left")
        if count:
            ctk.CTkLabel(inner, text=count, font=font("mono", 10),
                         text_color=col("fg_faint")).pack(side="left", padx=(6, 0))
        if command is not None:
            for widget in (self, inner, *inner.winfo_children()):
                widget.bind("<Button-1>", lambda _e: command())
                _hand(widget)


# --------------------------------------------------------------------------- #
# Progressive disclosure
# --------------------------------------------------------------------------- #
class StaticSection(ctk.CTkFrame):
    """
    A titled group that can be folded away with the caret in its header.

    This is the third iteration of this widget and the constraints that broke
    the first two are encoded here:

    * **Sections are independent.** The original was an exclusive accordion,
      so opening one silently closed another and the content under the
      pointer jumped by the height of whatever had just collapsed.
    * **The body is never destroyed**, only unmapped. Rebuilding it on every
      toggle meant losing the state of the widgets inside, and cost a full
      relayout of the panel each time.
    * **The header keeps a fixed height whether open or closed**, so a column
      of collapsed sections is an evenly spaced list rather than a ragged one.
    """

    def __init__(self, master, title: str, expanded: bool = True,
                 on_toggle: Optional[Callable[["StaticSection"], None]] = None,
                 collapsible: bool = True, **kwargs):
        super().__init__(master, fg_color="transparent", height=1, **kwargs)
        self.title = title
        self.on_toggle = on_toggle
        self.collapsible = collapsible
        self._expanded = True

        header = ctk.CTkFrame(self, fg_color="transparent", height=18)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.header = header
        # Badge slot: a collapsed section must still be able to say that it
        # holds non-default values (see `DirtyGroup`).
        self._badge = ctk.CTkLabel(header, text="", width=16,
                                   font=font("mono", 9),
                                   text_color=col("accent"))
        self._badge.pack(side="right")

        self._caret = ctk.CTkLabel(header, text="", width=12, font=font("small"),
                                   text_color=col("fg_faint"))
        self._title = ctk.CTkLabel(header, text=spaced(title), font=font("header"),
                                   text_color=col("fg_muted"), anchor="w")
        if collapsible:
            self._caret.pack(side="left")
            self._title.pack(side="left", fill="x", expand=True)
            for widget in (header, self._caret, self._title):
                widget.bind("<Button-1>", lambda _e: self.toggle())
                _hand(widget)
        else:
            self._title.pack(side="left", fill="x", expand=True)

        Rule(self).pack(fill="x", pady=(4, 0))
        self.body = ctk.CTkFrame(self, fg_color="transparent", height=1)
        self.body.pack(fill="x", pady=(8, 4))
        if not expanded:
            self.collapse()
        self._sync_caret()

    @property
    def expanded(self) -> bool:
        return self._expanded

    def _sync_caret(self) -> None:
        if not self.collapsible:
            return
        self._caret.configure(text="\u25be" if self._expanded else "\u25b8")
        self._title.configure(
            text_color=col("fg_muted") if self._expanded else col("fg_faint"))

    def set_badge(self, text: str) -> None:
        """Short marker shown at the right of the header ('3', '*', ...)."""
        self._badge.configure(text=text)

    def toggle(self) -> None:
        self.collapse() if self._expanded else self.expand()
        if self.on_toggle is not None:
            self.on_toggle(self)

    def expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self.body.pack(fill="x", pady=(8, 4))
        self._sync_caret()

    def collapse(self) -> None:
        if not self._expanded:
            return
        self._expanded = False
        self.body.pack_forget()   # unmapped, never destroyed: state survives
        self._sync_caret()


class SectionGroup:
    """
    Registry of the sections in one panel, so their open/closed state can be
    saved with the session and driven together by a "minimise all" control.
    It enforces no policy about how many may be open at once.
    """

    def __init__(self):
        self.sections: list[StaticSection] = []

    def add(self, section: StaticSection) -> StaticSection:
        self.sections.append(section)
        return section

    def state(self) -> dict[str, bool]:
        return {s.title: s.expanded for s in self.sections}

    def restore(self, state: dict) -> None:
        for section in self.sections:
            wanted = state.get(section.title)
            if wanted is not None:
                section.expand() if wanted else section.collapse()

    def set_all(self, expanded: bool) -> None:
        for section in self.sections:
            section.expand() if expanded else section.collapse()

    def any_expanded(self) -> bool:
        return any(s.expanded for s in self.sections)


# --------------------------------------------------------------------------- #
# Composite panels
# --------------------------------------------------------------------------- #
class TraceRow(ctk.CTkFrame):
    """
    A row in the trace list: visibility box, colour swatch, name, kind tag.
    The selected row is marked by a solid left rule plus a tinted background,
    never by a rounded pill.
    """

    def __init__(self, master, name: str, color: str, tag: str = "",
                 visible: bool = True,
                 on_select: Optional[Callable[[], None]] = None,
                 on_toggle: Optional[Callable[[bool], None]] = None,
                 on_color: Optional[Callable[[], None]] = None,
                 on_move_up: Optional[Callable[[], None]] = None,
                 on_move_down: Optional[Callable[[], None]] = None, **kwargs):
        super().__init__(master, fg_color="transparent", corner_radius=RADIUS,
                         height=ROW_HEIGHT, **kwargs)
        # The row is a fixed-height strip: a list of traces reads as a list,
        # not as a stack of cards. pack_propagate is off so the height below
        # is what it is, whatever the widgets inside ask for.
        self.pack_propagate(False)
        self._selected = False

        # width/height of 1: this frame has no children, and an empty
        # CTkFrame keeps its constructor default of 200x200 -- which stretched
        # every row to that height. `fill="y"` still gives it the full row.
        self._marker = ctk.CTkFrame(self, width=3, height=1,
                                    corner_radius=RADIUS, fg_color="transparent")
        self._marker.pack(side="left", fill="y")

        self.visible_var = ctk.BooleanVar(value=visible)
        ctk.CTkCheckBox(self, text="", width=16, checkbox_width=13,
                        checkbox_height=13, variable=self.visible_var,
                        command=lambda: on_toggle and on_toggle(self.visible_var.get())
                        ).pack(side="left", padx=(7, 5))

        self.swatch = ctk.CTkButton(self, text="", width=11, height=11,
                                    corner_radius=RADIUS, border_width=BORDER,
                                    border_color=col("border_str"),
                                    fg_color=color, hover_color=color,
                                    command=on_color)
        self.swatch.pack(side="left", padx=(0, 7))

        self.name_label = ctk.CTkLabel(self, text=name, font=font("small"),
                                       anchor="w", cursor="hand2")
        self.name_label.pack(side="left", fill="x", expand=True)

        self.tag_label = ctk.CTkLabel(self, text=tag.upper(), font=font("mono", 9),
                                      text_color=col("fg_faint"), cursor="hand2")
        self.tag_label.pack(side="right", padx=(4, 8))

        # Reorder within the list -- this is also the plot/legend draw
        # order (see `App._gather_curves`), so moving a trace here is how
        # its position in the legend is changed, without a separate control.
        if on_move_up is not None or on_move_down is not None:
            moves = ctk.CTkFrame(self, fg_color="transparent")
            moves.pack(side="right", padx=(0, 2))
            up = ctk.CTkLabel(moves, text="▲", font=font("mono", 8),
                              text_color=col("fg_faint"), cursor="hand2")
            up.pack(side="left")
            down = ctk.CTkLabel(moves, text="▼", font=font("mono", 8),
                                text_color=col("fg_faint"), cursor="hand2")
            down.pack(side="left", padx=(3, 0))
            if on_move_up is not None:
                up.bind("<Button-1>", lambda _e: on_move_up())
            if on_move_down is not None:
                down.bind("<Button-1>", lambda _e: on_move_down())

        if on_select is not None:
            for widget in (self, self.name_label, self.tag_label):
                widget.bind("<Button-1>", lambda _e: on_select())

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.configure(fg_color=col("sel") if selected else "transparent")
        self._marker.configure(fg_color=col("accent") if selected else "transparent")


class MeasurementsCard(ctk.CTkFrame):
    """
    Floating readout placed over the canvas. It reports what the cursors
    currently measure, so the numbers sit next to the trace they describe
    instead of in a separate window.
    """

    def __init__(self, master, title: str = "", **kwargs):
        super().__init__(master, corner_radius=RADIUS, border_width=BORDER,
                         border_color=col("border_str"), fg_color=col("surface"),
                         **kwargs)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(7, 4))
        ctk.CTkLabel(head, text=spaced(title or t("Mediciones")),
                     font=font("header"),
                     text_color=col("fg_muted")).pack(side="left")
        self._close = ctk.CTkLabel(head, text="✕", font=font("small"),
                                   text_color=col("fg_faint"), cursor="hand2")
        self._close.pack(side="right")
        Rule(self).pack(fill="x", padx=10)
        self.body = ctk.CTkFrame(self, fg_color="transparent",
                                 width=1, height=1)
        self.body.pack(fill="both", expand=True, padx=10, pady=(6, 8))
        self._empty = t("Sin cursores en el gráfico.")

    def bind_close(self, command: Callable[[], None]) -> None:
        self._close.bind("<Button-1>", lambda _e: command())

    def set_rows(self, rows: Sequence[tuple]) -> None:
        """rows: sequence of (label, value) or ('--', '') for a separator."""
        for child in self.body.winfo_children():
            child.destroy()
        if not rows:
            ctk.CTkLabel(self.body, text=self._empty, font=font("hint"),
                         text_color=col("fg_faint"), anchor="w").pack(fill="x")
            return
        for label, value in rows:
            if label == "--":
                Rule(self.body).pack(fill="x", pady=5)
                continue
            row = ctk.CTkFrame(self.body, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=label, font=font("small"),
                         text_color=col("fg_muted"), anchor="w"
                         ).pack(side="left", padx=(0, 18))
            ctk.CTkLabel(row, text=value, font=font("mono"),
                         text_color=col("fg"), anchor="e").pack(side="right")


def primary_button(master, text: str, command: Optional[Callable] = None,
                   height: int = 32, **kwargs) -> ctk.CTkButton:
    """The single filled button per surface; everything else is outlined."""
    return ctk.CTkButton(master, text=text, command=command, height=height,
                         corner_radius=RADIUS, border_width=BORDER,
                         fg_color=col("accent"), hover_color=col("accent_hi"),
                         border_color=col("accent"), text_color=col("on_accent"),
                         font=font("label"), **kwargs)


def ghost_button(master, text: str, command: Optional[Callable] = None,
                 height: int = 28, **kwargs) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, height=height,
                         corner_radius=RADIUS, border_width=BORDER,
                         fg_color=col("surface"), hover_color=col("sel"),
                         border_color=col("border"), text_color=col("fg"),
                         font=font("label"), **kwargs)


def hint(master, text: str, **kwargs) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=text, font=font("hint"),
                        text_color=col("fg_faint"), anchor="w",
                        justify="left", **kwargs)


class SliderField(ctk.CTkFrame):
    """
    Slider and numeric entry over one value, kept in sync both ways.

    Sliders were removed from this application once already because dragging
    one fired a full re-layout per pixel of travel. That is fixed here rather
    than by dropping the control: `on_change` is debounced, so a drag updates
    the number continuously but only commits when the pointer settles. Typing
    an exact value stays available for the cases a drag cannot hit.
    """

    def __init__(self, master, label: str, variable, minimum: float = 0.0,
                 maximum: float = 1.0, steps: int = 200,
                 on_change: Optional[Callable[[], None]] = None,
                 decimals: int = 3, label_width: int = 132,
                 debounce_ms: int = 90, **kwargs):
        super().__init__(master, fg_color="transparent", width=1, height=1,
                         **kwargs)
        self.variable = variable
        self.on_change = on_change
        self.minimum, self.maximum = minimum, maximum
        self.decimals = decimals
        self.debounce_ms = debounce_ms
        self._job = None
        self._syncing = False

        row = ctk.CTkFrame(self, fg_color="transparent", width=1, height=1)
        row.pack(fill="x")
        ctk.CTkLabel(row, text=label, font=font("label"),
                     text_color=col("fg_muted"), width=label_width,
                     anchor="w").pack(side="left")
        self.entry = ctk.CTkEntry(row, textvariable=variable, width=68,
                                  height=26, font=font("mono"), justify="right")
        self.entry.pack(side="right")
        self.entry.bind("<Return>", lambda _e: self._from_entry())
        self.entry.bind("<FocusOut>", lambda _e: self._from_entry())

        self.slider = ctk.CTkSlider(self, from_=minimum, to=maximum,
                                    number_of_steps=steps,
                                    command=self._from_slider, height=14)
        self.slider.pack(fill="x", pady=(4, 0))
        Rule(self).pack(fill="x", pady=(6, 0))
        self._sync_slider()

    def _value(self) -> float:
        try:
            return float(str(self.variable.get()).strip().replace(",", "."))
        except (ValueError, AttributeError):
            return self.minimum

    def _sync_slider(self) -> None:
        self._syncing = True
        try:
            self.slider.set(min(self.maximum, max(self.minimum, self._value())))
        except Exception:
            pass
        finally:
            self._syncing = False

    def _from_slider(self, value: float) -> None:
        if self._syncing:
            return
        self.variable.set(f"{float(value):.{self.decimals}f}")
        self._schedule()

    def _from_entry(self) -> None:
        value = min(self.maximum, max(self.minimum, self._value()))
        self.variable.set(f"{value:.{self.decimals}f}")
        self._sync_slider()
        self._commit()

    def _schedule(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        self._job = self.after(self.debounce_ms, self._commit)

    def _commit(self) -> None:
        self._job = None
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    def set_value(self, value: float) -> None:
        self.variable.set(f"{float(value):.{self.decimals}f}")
        self._sync_slider()


def stacked_entry(master, label: str, variable, width: int = 0,
                  on_enter: Optional[Callable[[], None]] = None) -> ctk.CTkEntry:
    """
    Label above, full-width entry below. Used where the value is prose (a
    legend name, an axis title) and needs room to be read, unlike the numeric
    rows which stay compact and right-aligned.
    """
    ctk.CTkLabel(master, text=label, font=font("label"),
                 text_color=col("fg_muted"), anchor="w").pack(fill="x", pady=(0, 4))
    entry = ctk.CTkEntry(master, textvariable=variable, height=28, font=font("body"))
    if width:
        entry.configure(width=width)
    entry.pack(fill="x", pady=(0, 12))
    if on_enter is not None:
        entry.bind("<Return>", lambda _e: on_enter())
    return entry


def stacked_label(master, label: str) -> ctk.CTkLabel:
    """Standalone caption for a control group laid out below it."""
    widget = ctk.CTkLabel(master, text=label, font=font("label"),
                          text_color=col("fg_muted"), anchor="w")
    widget.pack(fill="x", pady=(0, 4))
    return widget


# --------------------------------------------------------------------------- #
# Draggable pane divider
# --------------------------------------------------------------------------- #
class Splitter(ctk.CTkFrame):
    """
    Thin drag handle between a fixed-width side panel and the rest of the
    window. Dragging calls `on_drag(delta_px)` continuously and `on_release()`
    once at the end, so the caller can clamp the target width, update layout,
    and persist the final value without doing that work on every pixel of
    motion.

    The handle itself never changes size -- it is not the thing being
    resized, it is the control for resizing something else -- which is what
    keeps it from fighting the panel's own width management.
    """

    def __init__(self, master, on_drag: Callable[[int], None],
                 on_release: Optional[Callable[[], None]] = None,
                 width: int = 6, **kwargs):
        super().__init__(master, width=width, corner_radius=0,
                         fg_color=col("rule"), cursor="sb_h_double_arrow",
                         **kwargs)
        self.on_drag = on_drag
        self.on_release = on_release
        self._dragging = False
        self._start_x = 0
        self._pending = 0
        self._job = None

        self.bind("<Enter>", lambda _e: self.configure(fg_color=col("border_str")))
        self.bind("<Leave>", lambda _e: self._dragging or self.configure(fg_color=col("rule")))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    def _press(self, event) -> None:
        self._dragging = True
        self._start_x = event.x_root

    def _motion(self, event) -> None:
        if not self._dragging:
            return
        # Motion events arrive far faster than the panel can relayout, and
        # each `on_drag` resizes a container holding hundreds of widgets.
        # Accumulating the delta and flushing it once per idle cycle
        # collapses a burst of events into a single relayout, which is what
        # makes the drag track the pointer instead of lagging behind it.
        self._pending += event.x_root - self._start_x
        self._start_x = event.x_root
        if self._job is None:
            self._job = self.after_idle(self._flush)

    def _flush(self) -> None:
        self._job = None
        delta, self._pending = self._pending, 0
        if delta:
            self.on_drag(delta)

    def _release(self, _event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._flush()   # apply whatever motion the last idle cycle missed
        self.configure(fg_color=col("rule"))
        if self.on_release is not None:
            self.on_release()


# --------------------------------------------------------------------------- #
# Small modal prompts
# --------------------------------------------------------------------------- #
class TextPrompt(ctk.CTkToplevel):
    """
    Single-line modal input ("name this profile", "rename this..."). Kept
    intentionally tiny and square-cornered like everything else -- a rounded
    OS-native dialog here would be the one inconsistent surface in the app.
    """

    def __init__(self, master, title: str, message: str, initial: str = "",
                 on_submit: Optional[Callable[[str], None]] = None):
        super().__init__(master)
        self.title(title)
        self.geometry("360x160")
        self.minsize(320, 150)
        self.resizable(True, False)
        self._on_submit = on_submit
        self.result: Optional[str] = None

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=18)
        ctk.CTkLabel(body, text=message, font=font("label"), text_color=col("fg_muted"),
                    anchor="w", wraplength=320).pack(fill="x", pady=(0, 8))

        self.var = ctk.StringVar(value=initial)
        entry = ctk.CTkEntry(body, textvariable=self.var, height=30, font=font("body"))
        entry.pack(fill="x", pady=(0, 16))
        entry.bind("<Return>", lambda _e: self._submit())
        entry.focus_set()
        entry.select_range(0, "end")

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x")
        primary_button(actions, t("Guardar"), self._submit, height=28, width=100
                       ).pack(side="right")
        ghost_button(actions, t("Cancelar"), self.destroy, width=90
                    ).pack(side="right", padx=(0, 8))

        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.wait_window(self)

    def _submit(self) -> None:
        value = self.var.get().strip()
        if not value:
            return
        self.result = value
        if self._on_submit is not None:
            self._on_submit(value)
        self.destroy()


class ShortcutsWindow(ctk.CTkToplevel):
    """Reference card for every keyboard/mouse shortcut in the app."""

    def __init__(self, master, groups: Sequence[tuple[str, Sequence[tuple[str, str]]]]):
        super().__init__(master)
        self.title(t("Atajos"))
        self.geometry("420x520")
        self.minsize(360, 360)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(header, text=spaced(t("Atajos")), font=font("header"),
                    text_color=col("fg_muted")).pack(side="left")
        Rule(self).pack(fill="x", padx=20)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True, padx=20, pady=(10, 18))

        for index, (group_title, rows) in enumerate(groups):
            if index > 0:
                Rule(body).pack(fill="x", pady=10)
            ctk.CTkLabel(body, text=group_title, font=font("label"),
                        text_color=col("fg_muted"), anchor="w").pack(fill="x", pady=(0, 6))
            for keys, description in rows:
                row = ctk.CTkFrame(body, fg_color="transparent")
                row.pack(fill="x", pady=2)
                ctk.CTkLabel(row, text=keys, font=font("mono", 11), text_color=col("fg"),
                            anchor="w", width=120).pack(side="left")
                ctk.CTkLabel(row, text=description, font=font("small"),
                            text_color=col("fg_muted"), anchor="w", wraplength=220
                            ).pack(side="left", fill="x", expand=True)

        self.transient(master)


class CodeDialog(ctk.CTkToplevel):
    """
    Read-only code block with a copy button and an optional live editor for
    the fields that feed it.

    Used after an export to hand back the LaTeX that includes the file just
    written. `fields` is a list of (label, StringVar); whenever one changes,
    `rebuild(values)` is asked for a fresh snippet, so the caption and label
    can be adjusted and the block updates as you type.
    """

    def __init__(self, master, title: str, snippet: str, note: str = "",
                 fields: Optional[Sequence] = None,
                 rebuild: Optional[Callable[[dict], str]] = None,
                 extra_toggle: Optional[tuple] = None):
        super().__init__(master)
        self.title(title)
        self.geometry("680x520")
        self.minsize(520, 380)
        self._rebuild = rebuild
        self._fields = list(fields or [])

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(head, text=spaced(title), font=font("header"),
                     text_color=col("fg_muted")).pack(side="left")
        Rule(self).pack(fill="x", padx=20)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(12, 8))

        for label, var in self._fields:
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=(0, 6))
            ctk.CTkLabel(row, text=label, font=font("label"),
                         text_color=col("fg_muted"), width=90,
                         anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, textvariable=var, height=28,
                                 font=font("body"))
            entry.pack(side="left", fill="x", expand=True)
            var.trace_add("write", lambda *_: self._refresh())

        self._toggle_var = None
        if extra_toggle is not None:
            toggle_label, initial = extra_toggle
            self._toggle_var = ctk.BooleanVar(value=initial)
            ctk.CTkCheckBox(body, text=toggle_label, variable=self._toggle_var,
                            font=font("small"), checkbox_width=16,
                            checkbox_height=16,
                            command=self._refresh).pack(anchor="w", pady=(0, 8))

        self.textbox = ctk.CTkTextbox(body, font=font("mono"), wrap="none",
                                      height=240)
        self.textbox.pack(fill="both", expand=True)
        self.textbox.insert("1.0", snippet)

        if note:
            hint(body, note, wraplength=600).pack(fill="x", pady=(8, 0))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=(0, 18))
        self._status = ctk.CTkLabel(actions, text="", font=font("small"),
                                    text_color=col("fg_faint"))
        self._status.pack(side="left")
        ghost_button(actions, t("Cerrar"), self.destroy, width=90).pack(side="right")
        primary_button(actions, t("Copiar"), self._copy, height=28,
                       width=110).pack(side="right", padx=(0, 8))

        self.transient(master)

    def toggle_value(self) -> bool:
        return bool(self._toggle_var.get()) if self._toggle_var is not None else False

    def _refresh(self) -> None:
        if self._rebuild is None:
            return
        values = {label: var.get() for label, var in self._fields}
        values["__toggle__"] = self.toggle_value()
        try:
            snippet = self._rebuild(values)
        except Exception:
            return   # a half-typed field: keep the last good snippet
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", snippet)

    def _copy(self) -> None:
        text = self.textbox.get("1.0", "end").rstrip("\n")
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()   # make the selection stick on Windows
        except Exception:
            self._status.configure(text=t("No se pudo acceder al portapapeles."))
            return
        self._status.configure(text=t("Copiado al portapapeles."))


class LabeledCombo(ctk.CTkFrame):
    """
    Dropdown that displays translated labels while its variable holds a
    stable internal identifier.

    A plain `CTkComboBox` bound straight to a StringVar stores whatever text
    is on screen, which means the stored value changes when the interface
    language changes -- and any code comparing against it breaks. This keeps
    the two apart: `variable` is the identifier, the visible text is only a
    presentation detail.
    """

    def __init__(self, master, values: Sequence[str], variable,
                 labels: Optional[dict] = None,
                 command: Optional[Callable[[str], None]] = None,
                 width: int = 0, height: int = 26, **kwargs):
        super().__init__(master, fg_color="transparent", width=1, height=1,
                         **kwargs)
        self.values = list(values)
        self.labels = dict(labels or {})
        self.variable = variable
        self.command = command
        self._syncing = False

        self._display = ctk.StringVar(value=self._label_for(variable.get()))
        self._combo = ctk.CTkComboBox(
            self, values=[self._label_for(v) for v in self.values],
            variable=self._display, height=height, font=font("body"),
            dropdown_font=font("body"), command=self._on_pick)
        if width:
            self._combo.configure(width=width)
        self._combo.pack(fill="x")

        self._trace_id = variable.trace_add(
            "write", lambda *_: self._sync_from_variable())

    def destroy(self) -> None:
        # Same leak as `Segmented.destroy` -- see its comment.
        try:
            self.variable.trace_remove("write", self._trace_id)
        except Exception:
            pass
        super().destroy()

    def _label_for(self, value: str) -> str:
        return self.labels.get(value, value)

    def _value_for(self, label: str) -> str:
        for value in self.values:
            if self._label_for(value) == label:
                return value
        return label

    def _on_pick(self, label: str) -> None:
        if self._syncing:
            return
        value = self._value_for(label)
        self.variable.set(value)
        if self.command is not None:
            self.command(value)

    def _sync_from_variable(self) -> None:
        self._syncing = True
        try:
            self._display.set(self._label_for(self.variable.get()))
        finally:
            self._syncing = False

    def configure_values(self, values: Sequence[str],
                         labels: Optional[dict] = None) -> None:
        self.values = list(values)
        if labels is not None:
            self.labels = dict(labels)
        self._combo.configure(values=[self._label_for(v) for v in self.values])
        self._sync_from_variable()

    def configure(self, **kwargs):
        if "state" in kwargs:
            try:
                self._combo.configure(state=kwargs.pop("state"))
            except Exception:
                kwargs.pop("state", None)
        if not kwargs:
            return None
        try:
            return super().configure(**kwargs)
        except (ValueError, TypeError, tk.TclError):
            return None

    # `pack_forget`/`pack` are used by the X/Y mode switch on this widget.


# =========================================================================== #
# Redesign components
# --------------------------------------------------------------------------- #
# Everything below is additive: nothing above changed, so the current window
# keeps working while the new shell (`gui/shell.py`) is migrated screen by
# screen. The same design rules apply -- square corners, hairlines, label left
# / control right -- so a redesigned panel cannot look foreign next to one that
# has not been migrated yet.
# =========================================================================== #

TOOLTIP_DELAY_MS = 450


class Tooltip:
    """
    Hover explanation for one widget.

    This exists to delete paragraphs from the panels. The application
    currently explains itself with permanent `hint()` blocks -- the unit
    tooltip alone is five lines that are on screen whether or not anybody is
    reading them, in a column where vertical space is the scarce resource.
    Moving that text behind a hover keeps the explanation one gesture away
    and gives the space back to the controls.

    Plain Tk on purpose: the popup lives for a second or two, so a CTkFrame's
    extra canvas buys nothing, and being rebuilt on every hover means it
    always picks up the current palette without registering for repaints.
    """

    def __init__(self, widget, text: str, delay_ms: int = TOOLTIP_DELAY_MS,
                 wraplength: int = 280):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._job = None
        self._window: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        # A click means the user is acting, not asking: get out of the way.
        widget.bind("<Button-1>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event=None) -> None:
        self._cancel()
        if self.text:
            self._job = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self) -> None:
        self._job = None
        if self._window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 14
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return                      # widget destroyed mid-hover
        win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)   # no title bar, no taskbar entry
        try:
            win.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(win, background=tk_color("border_str"))
        frame.pack()
        label = tk.Label(frame, text=self.text, justify="left",
                         wraplength=self.wraplength,
                         background=tk_color("surface"),
                         foreground=tk_color("fg_muted"),
                         font=font("hint"), padx=8, pady=5)
        label.pack(padx=1, pady=1)      # 1px frame showing through = hairline
        win.wm_geometry(f"+{x}+{y}")
        self._window = win

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


def info_dot(master, text: str, **kwargs) -> ctk.CTkLabel:
    """The 'ⓘ' marker that carries a `Tooltip`. Pack it beside a label."""
    dot = ctk.CTkLabel(master, text="ⓘ", width=14, font=font("small"),
                       text_color=col("fg_faint"), **kwargs)
    # Attached, not just constructed: the caller needs it to retarget the text
    # when the same marker explains a changing context.
    dot.tooltip = Tooltip(dot, text)
    _hand(dot)
    return dot


class DirtyDot(ctk.CTkLabel):
    """
    Marks a field whose value differs from its default; click to revert.

    The question this answers is "what did I change?", which today can only
    be answered by remembering. It matters most on a trace: offset, gain,
    inversion and unit are all invisible once set, and a figure that came out
    wrong gives no clue which of them is responsible.
    """

    def __init__(self, master, variable, default, label: str = "",
                 on_revert: Optional[Callable[[], None]] = None, **kwargs):
        super().__init__(master, text="", width=10, font=font("small"),
                         text_color=col("accent"), **kwargs)
        self.variable = variable
        self.default = default
        self.label = label
        self.on_revert = on_revert
        self._listeners: list[Callable[[], None]] = []
        self._trace_id = variable.trace_add("write", lambda *_: self._sync())
        self.bind("<Button-1>", lambda _e: self.revert())
        Tooltip(self, t("Modificado -- clic para volver al valor por defecto."))
        self._sync()

    def destroy(self) -> None:
        # Same leak as `Segmented.destroy` (see its comment): a trace never
        # removed keeps this dot, its variable and its listeners alive in
        # Tk's own callback table for the rest of the process, even though
        # `_sync`'s TclError guard means it never crashes visibly here.
        try:
            self.variable.trace_remove("write", self._trace_id)
        except Exception:
            pass
        super().destroy()

    @property
    def dirty(self) -> bool:
        return str(self.variable.get()) != str(self.default)

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Called on every dirty-state change (used by `DirtyGroup`)."""
        self._listeners.append(callback)

    def revert(self) -> None:
        if not self.dirty:
            return
        self.variable.set(self.default)
        if self.on_revert is not None:
            self.on_revert()

    def _sync(self) -> None:
        try:
            self.configure(text="•" if self.dirty else "")
            _hand(self) if self.dirty else self.configure(cursor="")
        except tk.TclError:
            return                      # widget destroyed
        for callback in self._listeners:
            try:
                callback()
            except Exception:
                pass


class DirtyGroup:
    """
    Counts the modified fields of one section and badges its header.

    Without this, progressive disclosure hides information: a collapsed
    section looks exactly the same whether it holds defaults or six edited
    values. The badge is what makes folding a section safe.
    """

    def __init__(self, section: Optional["StaticSection"] = None):
        self.section = section
        self.dots: list[DirtyDot] = []

    def add(self, dot: DirtyDot) -> DirtyDot:
        self.dots.append(dot)
        dot.add_listener(self.refresh)
        self.refresh()
        return dot

    def count(self) -> int:
        return sum(1 for dot in self.dots if dot.dirty)

    def revert_all(self) -> None:
        for dot in self.dots:
            dot.revert()

    def refresh(self) -> None:
        if self.section is None:
            return
        n = self.count()
        self.section.set_badge(str(n) if n else "")


class NavRail(ctk.CTkFrame):
    """
    The permanent left rail: one entry per stage of the workflow.

    Ordered as the work actually happens -- ingest, adjust, annotate, export
    -- and numbered, because the order is the instruction. Selecting a stage
    is what swaps the navigator and the inspector, so at any moment roughly a
    quarter of the application's controls are reachable and the rest are not
    on screen at all.
    """

    def __init__(self, master, items: Sequence[tuple[str, str]], variable,
                 command: Optional[Callable[[str], None]] = None,
                 width: int = 78, **kwargs):
        super().__init__(master, width=width, corner_radius=0,
                         fg_color=col("panel"), **kwargs)
        self.pack_propagate(False)
        self.variable = variable
        self.command = command
        self.entries: dict[str, dict] = {}

        VRule(self, height=1).pack(side="right", fill="y")
        for index, (key, label) in enumerate(items, start=1):
            self.entries[key] = self._build_entry(key, index, label)
        self.refresh()

    def _build_entry(self, key: str, index: int, label: str) -> dict:
        row = ctk.CTkFrame(self, fg_color="transparent", height=58,
                           corner_radius=0)
        row.pack(fill="x")
        row.pack_propagate(False)
        marker = ctk.CTkFrame(row, width=3, height=1, corner_radius=0,
                              fg_color="transparent")
        marker.pack(side="left", fill="y")
        stack = ctk.CTkFrame(row, fg_color="transparent")
        stack.pack(fill="both", expand=True)
        number = ctk.CTkLabel(stack, text=f"{index}", font=font("mono", 10),
                              text_color=col("fg_faint"))
        number.pack(pady=(11, 0))
        name = ctk.CTkLabel(stack, text=label, font=font("small"),
                            text_color=col("fg_muted"))
        name.pack()
        for widget in (row, stack, number, name):
            widget.bind("<Button-1>", lambda _e, k=key: self.select(k))
            _hand(widget)
        return {"row": row, "marker": marker, "number": number, "name": name}

    def select(self, key: str) -> None:
        if key not in self.entries:
            return
        self.variable.set(key)
        self.refresh()
        if self.command is not None:
            self.command(key)

    def refresh(self) -> None:
        current = self.variable.get()
        for key, parts in self.entries.items():
            active = key == current
            parts["row"].configure(fg_color=col("sel") if active else "transparent")
            parts["marker"].configure(
                fg_color=col("accent") if active else "transparent")
            parts["number"].configure(
                text_color=col("fg") if active else col("fg_faint"))
            parts["name"].configure(
                text_color=col("fg") if active else col("fg_muted"))


class PaneStack(ctk.CTkFrame):
    """
    A `Segmented` header over swapped bodies -- this project's CTkTabview.

    CTkTabview is the obvious choice and is deliberately not used: it draws
    its own rounded segmented button and its own card-like frame, neither of
    which can be brought into a vocabulary built on square corners and
    hairlines without fighting its internals on every release. `Segmented`
    already renders the exact control this needs, and `OverlayPanel` already
    swaps panes this way, so this is that pattern promoted to a widget. The
    API is deliberately CTkTabview-shaped (`add`, `tab`, `set`), so swapping
    one for the other later is a mechanical change.
    """

    def __init__(self, master, variable=None, width: int = 0, **kwargs):
        super().__init__(master, fg_color="transparent", width=1, height=1,
                         **kwargs)
        self.variable = variable or ctk.StringVar(value="")
        self._labels: dict[str, str] = {}
        self._bodies: dict[str, ctk.CTkFrame] = {}
        self._segmented: Optional[Segmented] = None
        self._segmented_width = width
        self._header = ctk.CTkFrame(self, fg_color="transparent", width=1,
                                    height=1)
        self._header.pack(fill="x")
        self._holder = ctk.CTkFrame(self, fg_color="transparent", width=1,
                                    height=1)
        self._holder.pack(fill="both", expand=True, pady=(10, 0))

    def add(self, key: str, label: str, scrollable: bool = True) -> ctk.CTkFrame:
        """Register a pane and return the frame its content goes into."""
        self._labels[key] = label
        body = (ctk.CTkScrollableFrame(self._holder, fg_color="transparent",
                                       corner_radius=0) if scrollable
                else ctk.CTkFrame(self._holder, fg_color="transparent",
                                  width=1, height=1))
        self._bodies[key] = body
        if not self.variable.get():
            self.variable.set(key)
        self._rebuild_header()
        return body

    def tab(self, key: str) -> Optional[ctk.CTkFrame]:
        return self._bodies.get(key)

    def set(self, key: str) -> None:
        if key not in self._bodies:
            return
        self.variable.set(key)
        self._show()

    def get(self) -> str:
        return self.variable.get()

    def _rebuild_header(self) -> None:
        if self._segmented is not None:
            self._segmented.destroy()
        keys = list(self._labels)
        self._segmented = Segmented(
            self._header, keys, self.variable, labels=dict(self._labels),
            command=lambda _v: self._show(),
            width=self._segmented_width or max(72, 210 // max(1, len(keys))))
        self._segmented.pack(anchor="w")
        self._show()

    def _show(self) -> None:
        current = self.variable.get()
        for key, body in self._bodies.items():
            body.pack_forget()
            if key == current:
                body.pack(fill="both", expand=True)


class ColorHeader(ctk.CTkFrame):
    """
    Inspector header carrying the selected trace's colour.

    This is the visual link between a control and the curve it drives: the
    swatch here, the swatch in the trace list and the line on the canvas are
    the same colour, so a panel of twenty anonymous fields becomes "the panel
    for the red one" at a glance.
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", height=30,
                         corner_radius=0, **kwargs)
        self.pack_propagate(False)
        self._bar = ctk.CTkFrame(self, width=4, height=1, corner_radius=0,
                                 fg_color=col("border_str"))
        self._bar.pack(side="left", fill="y")
        self._title = ctk.CTkLabel(self, text="", font=font("header"),
                                   text_color=col("fg"), anchor="w")
        self._title.pack(side="left", padx=(9, 0))
        self._tag = ctk.CTkLabel(self, text="", font=font("mono", 9),
                                 text_color=col("fg_faint"))
        self._tag.pack(side="right", padx=(4, 0))

    def set_subject(self, title: str, color: Optional[str] = None,
                    tag: str = "") -> None:
        self._title.configure(text=spaced(title))
        self._tag.configure(text=tag.upper())
        self._bar.configure(fg_color=color or col("border_str"))


class CommandPalette(ctk.CTkToplevel):
    """
    Ctrl+K: type a few letters, run any command.

    The application exposes well over a hundred actions. A palette is what
    keeps that from forcing either a crowded toolbar or a deep menu tree: the
    rarely used commands stop competing for screen space with the frequent
    ones, which is the same reason the rail can afford to show only four
    entries.
    """

    def __init__(self, master, commands: Sequence[tuple[str, Callable[[], None]]],
                 title: str = "", limit: int = 9):
        super().__init__(master)
        self.commands = list(commands)
        self.limit = limit
        self._rows: list[tuple[ctk.CTkFrame, Callable[[], None]]] = []
        self._active = 0

        self.title(title or t("Comandos"))
        self.geometry("520x330")
        self.resizable(False, False)
        self.configure(fg_color=col("panel"))

        self.query_var = ctk.StringVar(value="")
        entry = ctk.CTkEntry(self, textvariable=self.query_var, height=34,
                             font=font("body"),
                             placeholder_text=t("Buscar un comando..."))
        entry.pack(fill="x", padx=16, pady=(16, 10))
        Rule(self).pack(fill="x", padx=16)
        self.list_frame = ctk.CTkFrame(self, fg_color="transparent", width=1,
                                       height=1)
        self.list_frame.pack(fill="both", expand=True, padx=16, pady=(8, 14))

        self.query_var.trace_add("write", lambda *_: self._render())
        for sequence, delta in (("<Down>", 1), ("<Up>", -1)):
            self.bind(sequence, lambda _e, d=delta: self._move(d))
        self.bind("<Return>", lambda _e: self._run(self._active))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(master)
        self._render()
        entry.focus_set()

    @staticmethod
    def _score(query: str, label: str) -> Optional[int]:
        """Subsequence match; a prefix hit outranks a scattered one."""
        query, low = query.strip().lower(), label.lower()
        if not query:
            return 0
        if low.startswith(query):
            return -2
        if query in low:
            return -1
        index = 0
        for char in query:
            index = low.find(char, index) + 1
            if index == 0:
                return None
        return index

    def _matches(self) -> list[tuple[str, Callable[[], None]]]:
        query = self.query_var.get()
        scored = []
        for label, action in self.commands:
            score = self._score(query, label)
            if score is not None:
                scored.append((score, label, action))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [(label, action) for _s, label, action in scored[:self.limit]]

    def _render(self) -> None:
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        self._rows = []
        self._active = 0
        matches = self._matches()
        if not matches:
            hint(self.list_frame, t("Sin resultados.")).pack(fill="x", pady=8)
            return
        for index, (label, action) in enumerate(matches):
            row = ctk.CTkFrame(self.list_frame, corner_radius=0,
                               height=ROW_HEIGHT, fg_color="transparent")
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            name = ctk.CTkLabel(row, text=label, font=font("small"), anchor="w")
            name.pack(side="left", padx=10)
            for widget in (row, name):
                widget.bind("<Button-1>", lambda _e, i=index: self._run(i))
                _hand(widget)
            self._rows.append((row, action))
        self._highlight()

    def _highlight(self) -> None:
        for index, (row, _action) in enumerate(self._rows):
            row.configure(fg_color=col("sel") if index == self._active
                          else "transparent")

    def _move(self, delta: int) -> None:
        if not self._rows:
            return
        self._active = (self._active + delta) % len(self._rows)
        self._highlight()

    def _run(self, index: int) -> None:
        if not (0 <= index < len(self._rows)):
            return
        action = self._rows[index][1]
        self.destroy()
        try:
            action()
        except Exception:
            pass    # a failing command must not take the window with it
