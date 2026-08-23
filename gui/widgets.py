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
        ctk.CTkLabel(self, text=spaced(title), font=font("header"),
                     text_color=col("fg_muted"), anchor="w"
                     ).pack(side="left")
        if action is not None:
            link = ctk.CTkLabel(self, text=action, font=font("hint"),
                                text_color=col("fg_muted"), cursor="hand2")
            link.pack(side="right")
            if command is not None:
                link.bind("<Button-1>", lambda _e: command())
            self.action_label = link


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
                rule: bool = True, label_width: int = 118) -> ctk.CTkEntry:
    """Label + numeric entry + optional unit suffix. Returns the entry."""
    field = Field(master, label, rule=rule, label_width=label_width)
    field.pack(fill="x", pady=(0, 6))
    if suffix:
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
                rule: bool = True) -> ctk.CTkComboBox:
    """Label + dropdown, for value sets that change at runtime."""
    field = Field(master, label, rule=rule)
    field.pack(fill="x", pady=(0, 6))
    combo = ctk.CTkComboBox(field.control, values=list(values), variable=variable,
                            width=width, height=26, font=font("body"),
                            dropdown_font=font("body"), command=command)
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

        variable.trace_add("write", lambda *_: self._sync())
        self._sync()

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
    A titled group whose body is always visible.

    This replaces an earlier collapsible/accordion implementation that was
    removed outright. Collapsing sections inside a scrolling column moved the
    content under the pointer by the height of whatever had just opened or
    closed, so every interaction with the panel felt like it jumped. The panel
    scrolls; there is nothing to gain from hiding parts of it, and a header
    that cannot be clicked is a header that cannot misbehave.
    """

    def __init__(self, master, title: str, **kwargs):
        super().__init__(master, fg_color="transparent", height=1, **kwargs)
        self.title = title
        ctk.CTkLabel(self, text=spaced(title), font=font("header"),
                     text_color=col("fg_muted"), anchor="w").pack(fill="x")
        Rule(self).pack(fill="x", pady=(4, 0))
        self.body = ctk.CTkFrame(self, fg_color="transparent", height=1)
        self.body.pack(fill="x", pady=(8, 4))


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
                 on_color: Optional[Callable[[], None]] = None, **kwargs):
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

    def __init__(self, master, title: str = "Mediciones", **kwargs):
        super().__init__(master, corner_radius=RADIUS, border_width=BORDER,
                         border_color=col("border_str"), fg_color=col("surface"),
                         **kwargs)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(7, 4))
        ctk.CTkLabel(head, text=spaced(title), font=font("header"),
                     text_color=col("fg_muted")).pack(side="left")
        self._close = ctk.CTkLabel(head, text="✕", font=font("small"),
                                   text_color=col("fg_faint"), cursor="hand2")
        self._close.pack(side="right")
        Rule(self).pack(fill="x", padx=10)
        self.body = ctk.CTkFrame(self, fg_color="transparent",
                                 width=1, height=1)
        self.body.pack(fill="both", expand=True, padx=10, pady=(6, 8))
        self._empty = "Sin cursores en el gráfico."

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
        primary_button(actions, "Guardar", self._submit, height=28, width=100
                       ).pack(side="right")
        ghost_button(actions, "Cancelar", self.destroy, width=90
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
        self.title("Atajos")
        self.geometry("420x520")
        self.minsize(360, 360)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(header, text=spaced("Atajos"), font=font("header"),
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
        ghost_button(actions, "Cerrar", self.destroy, width=90).pack(side="right")
        primary_button(actions, "Copiar", self._copy, height=28,
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
            self._status.configure(text="No se pudo acceder al portapapeles.")
            return
        self._status.configure(text="Copiado al portapapeles.")
