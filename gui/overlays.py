"""
gui/overlays.py
---------------
Interactive overlay layer for the Matplotlib canvas:

* `CursorManager`    -- an arbitrary number of draggable measurement cursors
                        (vertical / horizontal) with per-curve readout and
                        delta computation between cursors.
* `AnnotationManager`-- report-grade annotations: points of interest with a
                        leader arrow, standalone arrows, dashed reference
                        lines with a rotated inline label, dimension lines
                        ("cotas"), free text and shaded bands.

Both managers hold *state* (plain dataclasses), never widgets, and re-create
their artists on demand. This is what makes them survive the full
`fig.clear()` + re-plot cycle performed by `App.update_plot()`, and it is also
what allows an overlay set to be serialised to JSON and reloaded later so a
report figure is exactly reproducible.

The module depends on Matplotlib and NumPy only: no CustomTkinter import, so
it can be driven from a GUI panel, a script or a batch export. It also has NO
notion of axis UNITS (V, Hz, dB...) -- units are an application/GUI concern
(`App`/`OverlayPanel` know which unit belongs to which axis); this module
only ever works in the plot's raw data coordinates.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from typing import Callable, Optional, Sequence

import matplotlib as mpl
import matplotlib.ticker as mticker
import numpy as np

# Every artist created by this module carries a gid prefixed with OVERLAY_GID,
# so data curves can be told apart from overlay artists without relying on
# labels. Each manager uses its own suffix, so a gid sweep performed by one
# manager can never delete artists owned by the other.
OVERLAY_GID = "_labplotter_overlay"
CURSOR_GID = f"{OVERLAY_GID}:cursor"
ANNOTATION_GID = f"{OVERLAY_GID}:annotation"

# Overlay chrome is intentionally greyscale: the color channel belongs to the
# data. Annotations may override the color per item (e.g. to match a curve).
CURSOR_COLOR = "#2A2724"
ANNOTATION_COLOR = "#2A2724"

ARROW_STYLES: list[str] = ["->", "<-", "<->", "-|>", "<|-|>", "-"]
LINESTYLES: list[str] = ["--", "-", "-.", ":"]
# How a "dimension" (cota) annotation's two captured points are projected
# before drawing -- see `AnnotationManager._render_dimension`.
DIM_ORIENTATIONS: list[str] = ["auto", "horizontal", "vertical"]
# Generic Matplotlib families only: they resolve through the font manager on
# any machine, unlike a concrete face that may not be installed. The renderer
# runs on mathtext (text.usetex = False), so this family applies to the plain
# text runs; math runs follow rcParams["mathtext.fontset"].
FONT_FAMILIES: list[str] = ["serif", "sans-serif", "monospace", "cursive",
                            "fantasy"]
FONT_WEIGHTS: list[str] = ["normal", "bold"]
FONT_STYLES: list[str] = ["normal", "italic"]
HA_CHOICES: list[str] = ["left", "center", "right"]
VA_CHOICES: list[str] = ["top", "center", "bottom", "baseline"]
ANNOTATION_KINDS: dict[str, str] = {
    "Punto de interés": "point",
    "Flecha": "arrow",
    "Cota": "dimension",
    "Línea diagonal": "line",
    "Línea vertical": "vline",
    "Línea horizontal": "hline",
    "Texto": "text",
    "Banda vertical": "vspan",
    "Banda horizontal": "hspan",
}

_SI_PREFIXES: tuple[tuple[float, str], ...] = (
    (1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
    (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"),
)
# Micro is the only prefix without a safe glyph in every font stack (the
# project renders text with mathtext and no external TeX), so it is emitted as
# inline math when the string is going to be drawn on the figure.
_MATH_PREFIX = {"u": r"$\mu$"}


# ========================================================================== #
# Helpers
# ========================================================================== #
def format_eng(value: Optional[float], unit: str = "", digits: int = 4,
               mathtext: bool = False, decimals: Optional[int] = None) -> str:
    """
    Format a number in engineering notation (1.23k, 470u, -3.01).

    `decimals`, when given, replaces the `digits`-significant-figures
    rounding with a FIXED number of decimal places on the mantissa (e.g.
    `decimals=2` always gives two, "15.23"/"3.00", never the
    magnitude-dependent 1-to-4 decimals that `%.{digits}g` produces). This
    is what a value meant to be read off an axis or a cursor tag wants --
    a steady format -- as opposed to the compact, variable-precision
    notation used for numbers typed/echoed elsewhere in the app.
    """
    if value is None:
        return "n/a"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(v):
        return "n/a"
    suffix = f" {unit}" if unit else ""
    if v == 0.0:
        mantissa_text = f"{0.0:.{decimals}f}" if decimals is not None else "0"
        return f"{mantissa_text}{suffix}" if decimals is not None else f"0{suffix}"

    magnitude = abs(v)
    factor, prefix = 1e-12, "p"
    for f, p in _SI_PREFIXES:
        if magnitude >= f:
            factor, prefix = f, p
            break

    mantissa = v / factor
    text = f"{mantissa:.{decimals}f}" if decimals is not None else f"{mantissa:.{digits}g}"
    if prefix and mathtext and prefix in _MATH_PREFIX:
        return f"{text} {_MATH_PREFIX[prefix]}{unit}".rstrip()
    return f"{text} {prefix}{unit}".rstrip() if (prefix or unit) else text


def _is_data_line(artist) -> bool:
    """True for a user curve; False for overlay artists and private labels."""
    if (artist.get_gid() or "").startswith(OVERLAY_GID):
        return False
    label = artist.get_label() or "_"
    return not label.startswith("_")


def _sibling_axes(base) -> list:
    """
    Return `base` plus any twin axes stacked on the same rectangle.

    Bode "Juntos" builds the phase axis with `twinx()`, which occupies exactly
    the same position; its curves must appear in the cursor readout too.
    """
    axes_list = [base]
    figure = getattr(base, "figure", None)
    if figure is None:
        return axes_list
    bounds = base.get_position().bounds
    for ax in figure.axes:
        if ax is base:
            continue
        if np.allclose(ax.get_position().bounds, bounds, rtol=1e-3, atol=1e-4):
            axes_list.append(ax)
    return axes_list


def _all_axes(axes: Sequence) -> list:
    """Every axes involved, twins included (Bode "Juntos" uses twinx)."""
    out: list = []
    for base in axes:
        for ax in _sibling_axes(base):
            if not any(ax is seen for seen in out):
                out.append(ax)
    return out


def purge_overlay_artists(axes: Sequence, gid: Optional[str] = None,
                          keep: Sequence = ()) -> int:
    """
    Remove every overlay artist still living on `axes` that is not in `keep`.

    Safety net against "ghost overlays": an artist whose owning manager
    dropped its reference (see `attach`), or one added by a kind renderer
    that raised half-way through. Matching is by gid, never by label, so a
    data curve can never be swept by accident.
    """
    prefix = gid or OVERLAY_GID
    kept = {id(a) for a in keep}
    removed = 0
    for ax in _all_axes(axes):
        # ArtistList views are live in Matplotlib >= 3.7: snapshot before
        # mutating, otherwise the iteration skips elements.
        groups = (list(ax.lines), list(ax.texts), list(ax.patches),
                  list(ax.collections), list(getattr(ax, "artists", [])))
        for group in groups:
            for artist in group:
                if not (artist.get_gid() or "").startswith(prefix):
                    continue
                if id(artist) in kept:
                    continue
                try:
                    artist.remove()
                    removed += 1
                except (NotImplementedError, ValueError, AttributeError):
                    pass
    return removed


def _sorted_xy(line) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """Finite, X-sorted copy of a line's data, or None if unusable."""
    x = np.asarray(line.get_xdata(), dtype=float)
    y = np.asarray(line.get_ydata(), dtype=float)
    if x.size < 2 or x.size != y.size:
        return None
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 2:
        return None
    x, y = x[mask], y[mask]
    order = np.argsort(x)
    return x[order], y[order]


def _crossings(x: np.ndarray, y: np.ndarray, level: float,
               limit: int = 4) -> list[float]:
    """X positions where a curve crosses a horizontal level (linear interp)."""
    shifted = y - level
    sign_change = np.signbit(shifted[:-1]) != np.signbit(shifted[1:])
    idx = np.flatnonzero(sign_change)
    out: list[float] = []
    for i in idx[:limit]:
        y0, y1 = shifted[i], shifted[i + 1]
        if y1 == y0:
            out.append(float(x[i]))
        else:
            t = y0 / (y0 - y1)
            out.append(float(x[i] + t * (x[i + 1] - x[i])))
    return out


def _from_dict(cls, payload: dict):
    """Build a dataclass from a dict, ignoring unknown/legacy keys."""
    valid = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in payload.items() if k in valid})


def _screen_angle(ax, x1: float, y1: float, x2: float, y2: float) -> float:
    """
    On-screen angle (degrees) of the segment (x1, y1)-(x2, y2).

    Computed from the transformed *pixel* positions, not the raw data
    coordinates: X and Y axes rarely share the same scale (a Bode plot, a
    log axis, a stretched window), so a data-space angle would not match
    what the line actually looks like on screen. Folded into (-90, 90] so
    a near-vertical line reads as +90/-90 rather than flipping the text
    upside down.
    """
    px1, py1 = ax.transData.transform((x1, y1))
    px2, py2 = ax.transData.transform((x2, y2))
    angle = math.degrees(math.atan2(py2 - py1, px2 - px1))
    if angle <= -90.0:
        angle += 180.0
    elif angle > 90.0:
        angle -= 180.0
    return angle


def _perp_ticks(ax, x1: float, y1: float, x2: float, y2: float,
                tick_pt: float = 6.0):
    """
    Endpoints of the two short end-ticks of a dimension line, one at each
    end of the segment (x1, y1)-(x2, y2), perpendicular to it -- the
    end-cap convention of a technical/engineering "cota".

    Computed in PIXEL space (`ax.transData`, same technique `_screen_angle`
    already uses) rather than from the raw data slope: X and Y rarely share
    a scale in this app (log frequency axis, a voltage Y next to a time X),
    so a perpendicular computed from data coordinates would not look
    perpendicular on screen. `tick_pt` is a length in points, converted with
    the actual figure DPI (not the `rcParams` default, which may differ
    from the figure this axes belongs to).

    Returns ((a1x, a1y), (a2x, a2y), (b1x, b1y), (b2x, b2y)): the tick at
    point 1 (a1-a2), then the tick at point 2 (b1-b2), back in DATA
    coordinates.
    """
    transform = ax.transData
    inverse = transform.inverted()
    px1, py1 = transform.transform((x1, y1))
    px2, py2 = transform.transform((x2, y2))
    dx, dy = px2 - px1, py2 - py1
    length = math.hypot(dx, dy) or 1.0
    # Perpendicular unit vector in pixel space, scaled to a half-tick length.
    ux, uy = -dy / length, dx / length
    dpi = getattr(ax.figure, "dpi", 100.0) or 100.0
    half = tick_pt * (dpi / 72.0) / 2.0

    def _tick(px: float, py: float):
        a = inverse.transform((px - ux * half, py - uy * half))
        b = inverse.transform((px + ux * half, py + uy * half))
        return (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))

    a1, a2 = _tick(px1, py1)
    b1, b2 = _tick(px2, py2)
    return a1, a2, b1, b2


class _MarkedFormatter(mticker.Formatter):
    """
    Tick formatter that swaps in custom text at specific data positions and
    delegates everything else to a captured base formatter.

    A tick label's text is recomputed from the axis formatter on every draw
    -- calling `.set_text()` directly on the `Text` object does not survive
    so much as the next `get_xticks()` call -- so an axis mark has to work
    at the formatter level, not by poking the rendered label. `set_locs`/
    `set_axis`/`get_offset`/`fix_minus` are proxied to the base formatter
    because several stock formatters (`ScalarFormatter`, and this app's own
    engineering-notation `FuncFormatter`) depend on Matplotlib calling them
    before/around `__call__`; without the proxy every non-marked tick
    silently renders blank.
    """

    def __init__(self, base: mticker.Formatter, marks: dict[float, str]):
        self.base = base
        self.marks = marks

    def __call__(self, x, pos=None):
        for value, text in self.marks.items():
            if math.isclose(x, value, rel_tol=1e-9, abs_tol=1e-12):
                return text
        return self.base(x, pos)

    def format_ticks(self, values):
        self.set_locs(values)
        return [self(v, i) for i, v in enumerate(values)]

    def set_locs(self, locs):
        super().set_locs(locs)
        self.base.set_locs(locs)

    def set_axis(self, axis):
        super().set_axis(axis)
        self.base.set_axis(axis)

    def get_offset(self):
        return self.base.get_offset() if hasattr(self.base, "get_offset") else ""

    def fix_minus(self, s):
        return self.base.fix_minus(s) if hasattr(self.base, "fix_minus") else s


# ========================================================================== #
# Cursors
# ========================================================================== #
@dataclass
class CursorSpec:
    """A single measurement cursor, stored in data coordinates."""
    cid: int
    orientation: str = "v"        # "v" (vertical) | "h" (horizontal)
    position: float = 0.0
    axes_index: int = 0
    label: str = ""               # empty -> auto ("C1", "C2", ...)
    visible: bool = True
    locked: bool = False          # ignored by drag


class CursorManager:
    """
    Any number of draggable cursors on the embedded canvas.

    There is no fixed upper limit: `max_cursors=None` means unlimited, and the
    default cap only exists to keep the readout table readable.
    """

    def __init__(self, canvas, on_change: Optional[Callable[[], None]] = None,
                 max_cursors: Optional[int] = 64):
        self.canvas = canvas
        self.on_change = on_change
        self.max_cursors = max_cursors

        self.axes: list = []
        self.cursors: list[CursorSpec] = []
        self.snap_to_data = True
        self.show_tags = True
        self.tag_with_value = True
        # Decimal places used to format the value in the on-canvas tag and
        # in the text handed to "Anotar valor" (`OverlayPanel._promote_cursor`)
        # -- both read the exact same number, see that method's docstring.
        # A FIXED decimal count (via `format_eng(..., decimals=...)`) rather
        # than the app-wide `digits`-significant-figures default: a report
        # value the user is about to burn into a permanent axis mark wants a
        # steady, user-chosen precision, not one that silently drifts with
        # magnitude (that drift -- e.g. "1.523" next to "15.23" -- is exactly
        # what was reported as "me lo pasa con 3 decimales").
        self.value_decimals = 2
        # One (x_unit, y_unit) pair per axes index, in the SAME order as
        # `self.axes` -- not a single global unit. A cursor sitting on a
        # secondary Y axis (Bode "Juntos" phase, or any `secondary_y` trace)
        # lives in a different unit than the primary axis (deg vs dB, A vs
        # V); a flat pair used to format every cursor's tag with whatever
        # unit the PRIMARY axis happened to have, silently wrong for a
        # cursor placed on the secondary one. `App` populates this once per
        # redraw, in lock-step with how it builds `self.axes` (see
        # `App._axes_context`); a missing/out-of-range entry falls back to
        # "" via `axis_unit()`, never an `IndexError`.
        self.axis_units: list[tuple[str, str]] = []

        self._artists: dict[int, list] = {}
        self._next_id = 1
        self._drag_id: Optional[int] = None
        self._armed: Optional[str] = None      # orientation waiting for a click
        self._cids: list[int] = []
        self._connect()

    # ------------------------------- wiring ------------------------------ #
    def _connect(self) -> None:
        connect = self.canvas.mpl_connect
        self._cids = [
            connect("button_press_event", self._on_press),
            connect("motion_notify_event", self._on_motion),
            connect("button_release_event", self._on_release),
        ]

    def disconnect(self) -> None:
        for cid in self._cids:
            try:
                self.canvas.mpl_disconnect(cid)
            except Exception:
                pass
        self._cids = []

    def attach(self, axes: Sequence) -> None:
        """
        Bind to a freshly created axes list.

        `App._prepare_axes()` calls `fig.clear()`, which destroys every artist;
        only the specs survive. Cursors pointing at an axes index that no
        longer exists fall back to the first axes instead of being dropped.
        """
        # BUGFIX (ghost overlays): this used to only do `self._artists.clear()`,
        # which is correct right after `fig.clear()` -- every artist is already
        # dead -- but WRONG when called on live axes, which is exactly what
        # `App._refresh_overlays()` does on every panel action: the artists
        # stayed on the axes with their references dropped, so `redraw()` found
        # nothing to destroy and painted a second copy on top. Destroy first,
        # then sweep by gid to catch anything untracked.
        for cid in list(self._artists):
            self._destroy_artists(cid)
        self._artists.clear()
        self.axes = list(axes)
        purge_overlay_artists(self.axes, gid=CURSOR_GID)
        n = len(self.axes)
        for spec in self.cursors:
            if spec.axes_index >= n:
                spec.axes_index = 0

    # ------------------------------ mutation ----------------------------- #
    def add(self, orientation: str = "v", position: Optional[float] = None,
            axes_index: int = 0, label: str = "") -> Optional[CursorSpec]:
        if self.max_cursors is not None and len(self.cursors) >= self.max_cursors:
            return None
        if not self.axes:
            return None
        axes_index = max(0, min(int(axes_index), len(self.axes) - 1))
        if position is None:
            ax = self.axes[axes_index]
            lo, hi = ax.get_xlim() if orientation == "v" else ax.get_ylim()
            position = 0.5 * (lo + hi)
        spec = CursorSpec(cid=self._next_id, orientation=orientation,
                          position=float(position), axes_index=axes_index,
                          label=label)
        self._next_id += 1
        self.cursors.append(spec)
        return spec

    def remove(self, cid: int) -> None:
        self._destroy_artists(cid)
        self.cursors = [c for c in self.cursors if c.cid != cid]

    def clear(self) -> None:
        for cid in list(self._artists):
            self._destroy_artists(cid)
        self.cursors.clear()

    def get(self, cid: int) -> Optional[CursorSpec]:
        return next((c for c in self.cursors if c.cid == cid), None)

    def name_of(self, spec: CursorSpec) -> str:
        if spec.label:
            return spec.label
        try:
            return f"C{self.cursors.index(spec) + 1}"
        except ValueError:
            return f"C{spec.cid}"

    def axis_unit(self, axes_index: int, orientation: str) -> str:
        """
        The unit for `axes_index` along `orientation` ("v" -> X, "h" -> Y),
        or "" if `axis_units` hasn't been populated (or is shorter than
        `axes_index`) -- e.g. before the first redraw, or a plot mode this
        manager has never seen. Never raises: an unknown unit degrades to
        an unlabelled number, not an exception in the middle of a redraw.
        """
        if 0 <= axes_index < len(self.axis_units):
            x_unit, y_unit = self.axis_units[axes_index]
            return x_unit if orientation == "v" else y_unit
        return ""

    def arm(self, orientation: str) -> None:
        """Next click on the canvas places a cursor of this orientation."""
        self._armed = orientation

    def disarm(self) -> None:
        self._armed = None

    @property
    def armed(self) -> bool:
        return self._armed is not None

    # ------------------------------ rendering ---------------------------- #
    def redraw(self) -> None:
        """Re-create every cursor artist on the current axes."""
        if not self.axes:
            return
        for cid in list(self._artists):
            self._destroy_artists(cid)
        for spec in self.cursors:
            if spec.visible:
                self._create_artists(spec)

    def _destroy_artists(self, cid: int) -> None:
        for artist in self._artists.pop(cid, []):
            try:
                artist.remove()
            except (NotImplementedError, ValueError, AttributeError):
                pass

    def _create_artists(self, spec: CursorSpec) -> None:
        ax = self.axes[spec.axes_index]
        dashes = (0, (5, 3))
        if spec.orientation == "v":
            line = ax.axvline(spec.position, color=CURSOR_COLOR, lw=0.9,
                              ls=dashes, zorder=6, label="_nolegend_")
        else:
            line = ax.axhline(spec.position, color=CURSOR_COLOR, lw=0.9,
                              ls=dashes, zorder=6, label="_nolegend_")
        line.set_gid(CURSOR_GID)
        artists = [line]

        if self.show_tags:
            name = self.name_of(spec)
            if self.tag_with_value:
                unit = self.axis_unit(spec.axes_index, spec.orientation)
                name = f"{name}: {format_eng(spec.position, unit, mathtext=True, decimals=self.value_decimals)}"
            box = dict(boxstyle="square,pad=0.24", fc="white", ec="#4A473F",
                       lw=0.6, alpha=0.92)
            if spec.orientation == "v":
                tag = ax.text(spec.position, 0.985, name,
                              transform=ax.get_xaxis_transform(),
                              ha="center", va="top", fontsize=7,
                              color=CURSOR_COLOR, bbox=box, zorder=7,
                              clip_on=True)
            else:
                tag = ax.text(0.012, spec.position, name,
                              transform=ax.get_yaxis_transform(),
                              ha="left", va="bottom", fontsize=7,
                              color=CURSOR_COLOR, bbox=box, zorder=7,
                              clip_on=True)
            tag.set_gid(CURSOR_GID)
            artists.append(tag)

        self._artists[spec.cid] = artists

    def _update_artists(self, spec: CursorSpec) -> None:
        """Cheap in-place move used while dragging (no full re-render)."""
        artists = self._artists.get(spec.cid)
        if not artists:
            self._create_artists(spec)
            return
        line = artists[0]
        if spec.orientation == "v":
            line.set_xdata([spec.position, spec.position])
        else:
            line.set_ydata([spec.position, spec.position])
        if len(artists) > 1:
            tag = artists[1]
            name = self.name_of(spec)
            if self.tag_with_value:
                unit = self.axis_unit(spec.axes_index, spec.orientation)
                name = f"{name}: {format_eng(spec.position, unit, mathtext=True, decimals=self.value_decimals)}"
            tag.set_text(name)
            if spec.orientation == "v":
                tag.set_position((spec.position, 0.985))
            else:
                tag.set_position((0.012, spec.position))

    def move(self, cid: int, position: float, snap: Optional[bool] = None,
             notify: bool = False) -> bool:
        """
        Move one cursor in place. Returns False if the cursor is gone.

        Fast path for continuous input (slider, drag): only that cursor's
        artists are touched and the canvas is queued with `draw_idle()` --
        no `fig.clear()`, no re-plot, no layout pass. `notify=False` keeps
        the expensive listener chain (readout table + widget rebuild) out of
        the per-tick path; the caller is expected to debounce `notify()`.
        """
        spec = self.get(cid)
        if spec is None:
            return False
        use_snap = self.snap_to_data if snap is None else bool(snap)
        value = float(position)
        if use_snap and spec.axes_index < len(self.axes):
            value = self._snap(spec.axes_index, spec.orientation, value)
        spec.position = value
        self._update_artists(spec)
        self.canvas.draw_idle()
        if notify:
            self._notify(redraw=False)
        return True

    def notify(self) -> None:
        """Public hook: announce a change without re-rendering the overlays."""
        self._notify(redraw=False)

    def range_for(self, cid: int) -> Optional[tuple[float, float, bool]]:
        """(low, high, is_log) of the axis a cursor travels along."""
        spec = self.get(cid)
        if spec is None or not self.axes:
            return None
        ax = self.axes[min(spec.axes_index, len(self.axes) - 1)]
        if spec.orientation == "v":
            lo, hi = ax.get_xlim()
            log = ax.get_xscale() == "log"
        else:
            lo, hi = ax.get_ylim()
            log = ax.get_yscale() == "log"
        lo, hi = float(min(lo, hi)), float(max(lo, hi))
        if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
            return None
        return lo, hi, bool(log)

    # --------------------------- event handling -------------------------- #
    def _axes_index_for(self, ax) -> Optional[int]:
        if ax is None:
            return None
        for i, base in enumerate(self.axes):
            if base is ax:
                return i
        for i, base in enumerate(self.axes):
            if ax in _sibling_axes(base):
                return i
        return None

    def _event_data(self, index: int, event) -> tuple[float, float]:
        """Event position expressed in the *base* axes data coordinates."""
        ax = self.axes[index]
        x, y = ax.transData.inverted().transform((event.x, event.y))
        return float(x), float(y)

    def _snap(self, index: int, orientation: str, value: float) -> float:
        if not self.snap_to_data:
            return value
        best, best_dist = value, np.inf
        for line in self.data_lines(index):
            data = _sorted_xy(line)
            if data is None:
                continue
            arr = data[0] if orientation == "v" else data[1]
            i = int(np.argmin(np.abs(arr - value)))
            dist = abs(float(arr[i]) - value)
            if dist < best_dist:
                best, best_dist = float(arr[i]), dist
        # Only snap when the nearest sample is closer than 1.5 % of the span.
        ax = self.axes[index]
        lo, hi = ax.get_xlim() if orientation == "v" else ax.get_ylim()
        span = abs(hi - lo) or 1.0
        return best if best_dist <= 0.015 * span else value

    def _on_press(self, event) -> None:
        index = self._axes_index_for(event.inaxes)
        if index is None:
            return
        if self._armed is not None:
            orientation, self._armed = self._armed, None
            x, y = self._event_data(index, event)
            value = x if orientation == "v" else y
            spec = self.add(orientation, self._snap(index, orientation, value), index)
            if spec is not None:
                self._create_artists(spec)
                self._notify()
            return
        if event.button != 1:
            return
        cid = self._pick(index, event)
        if cid is not None:
            self._drag_id = cid

    def _pick(self, index: int, event, tolerance: int = 6) -> Optional[int]:
        ax = self.axes[index]
        for spec in self.cursors:
            if spec.axes_index != index or not spec.visible or spec.locked:
                continue
            if spec.orientation == "v":
                px = ax.transData.transform((spec.position, ax.get_ylim()[0]))[0]
                if abs(px - event.x) <= tolerance:
                    return spec.cid
            else:
                py = ax.transData.transform((ax.get_xlim()[0], spec.position))[1]
                if abs(py - event.y) <= tolerance:
                    return spec.cid
        return None

    def _on_motion(self, event) -> None:
        if self._drag_id is None:
            return
        spec = self.get(self._drag_id)
        if spec is None or event.inaxes is None:
            return
        index = self._axes_index_for(event.inaxes)
        if index is None:
            return
        x, y = self._event_data(spec.axes_index, event)
        value = x if spec.orientation == "v" else y
        spec.position = self._snap(spec.axes_index, spec.orientation, value)
        self._update_artists(spec)
        self.canvas.draw_idle()
        self._notify(redraw=False)

    def _on_release(self, _event) -> None:
        if self._drag_id is not None:
            self._drag_id = None
            self._notify(redraw=False)

    def _notify(self, redraw: bool = True) -> None:
        if redraw:
            self.canvas.draw_idle()
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass   # a UI refresh failure must never break the canvas

    # ------------------------------- readout ----------------------------- #
    def data_lines(self, index: int) -> list:
        if not self.axes or index >= len(self.axes):
            return []
        lines: list = []
        for ax in _sibling_axes(self.axes[index]):
            lines.extend(line for line in ax.get_lines() if _is_data_line(line))
        return lines

    def readout(self) -> list[dict]:
        """
        Per-cursor measurement table.

        Vertical cursor  -> Y of every curve interpolated at the cursor X.
        Horizontal cursor-> X of every crossing of the cursor level (this is
                            what gives the -3 dB frequency straight off a Bode
                            magnitude trace).

        Each row carries its own `axes_index` so a caller formatting the
        table (e.g. `OverlayPanel._render_readout`) can look up the right
        per-axis unit via `axis_unit()` instead of assuming every cursor
        lives on the same axis -- two cursors placed on Y1 and Y2 at once
        are a perfectly normal thing to have on screen together.
        """
        rows: list[dict] = []
        for spec in self.cursors:
            if spec.axes_index >= len(self.axes):
                continue
            entry = {"cid": spec.cid, "name": self.name_of(spec),
                     "orientation": spec.orientation, "position": spec.position,
                     "axes_index": spec.axes_index, "values": []}
            for line in self.data_lines(spec.axes_index):
                data = _sorted_xy(line)
                if data is None:
                    continue
                x, y = data
                item = {"label": line.get_label(), "color": line.get_color()}
                if spec.orientation == "v":
                    inside = x[0] <= spec.position <= x[-1]
                    item["value"] = float(np.interp(spec.position, x, y)) if inside else None
                else:
                    item["value"] = None
                    item["crossings"] = _crossings(x, y, spec.position)
                entry["values"].append(item)
            rows.append(entry)
        return rows

    def deltas(self) -> list[dict]:
        """
        Differences between consecutive cursors of the same orientation.

        Only ever pairs cursors that already share `axes_index` (see the
        `continue` below), so the pair's own unit is unambiguous -- carried
        in the returned dict for the same reason `readout()` now carries one
        per row.
        """
        out: list[dict] = []
        for orientation in ("v", "h"):
            group = [c for c in self.cursors if c.orientation == orientation]
            for a, b in zip(group, group[1:]):
                if a.axes_index != b.axes_index:
                    continue
                delta = b.position - a.position
                item = {"from": self.name_of(a), "to": self.name_of(b),
                        "orientation": orientation, "delta": delta,
                        "axes_index": a.axes_index,
                        "inverse": (1.0 / delta) if delta else None,
                        "curves": []}
                if orientation == "v":
                    for line in self.data_lines(a.axes_index):
                        data = _sorted_xy(line)
                        if data is None:
                            continue
                        x, y = data
                        if not (x[0] <= a.position <= x[-1] and x[0] <= b.position <= x[-1]):
                            continue
                        ya = float(np.interp(a.position, x, y))
                        yb = float(np.interp(b.position, x, y))
                        item["curves"].append({"label": line.get_label(),
                                               "delta": yb - ya})
                out.append(item)
        return out

    # ----------------------------- persistence --------------------------- #
    def to_dict(self) -> dict:
        return {"snap_to_data": self.snap_to_data, "show_tags": self.show_tags,
                "tag_with_value": self.tag_with_value,
                "value_decimals": self.value_decimals,
                "cursors": [asdict(c) for c in self.cursors]}

    def from_dict(self, payload: dict) -> None:
        self.clear()
        self.snap_to_data = bool(payload.get("snap_to_data", self.snap_to_data))
        self.show_tags = bool(payload.get("show_tags", self.show_tags))
        self.tag_with_value = bool(payload.get("tag_with_value", self.tag_with_value))
        self.value_decimals = int(payload.get("value_decimals", self.value_decimals))
        for item in payload.get("cursors", []):
            try:
                spec = _from_dict(CursorSpec, item)
            except TypeError:
                continue
            spec.cid = self._next_id
            self._next_id += 1
            self.cursors.append(spec)


# ========================================================================== #
# Annotations
# ========================================================================== #
@dataclass
class AnnotationSpec:
    """
    One annotation item. A single flat record keeps JSON round-tripping
    trivial; unused fields are simply ignored by the renderer of each kind.

    Which fields a given `kind` actually reads is decided by each
    `_render_<kind>` method below -- that is the one real source of truth.
    `gui.overlay_panel.ANNOTATION_SCHEMA` mirrors it for the FORM (which
    rows to show for the selected kind); keeping the schema in the GUI
    layer, not here, is deliberate: this module has no business knowing
    how a field is edited, only how it is drawn.
    """
    aid: int
    kind: str = "point"           # point | arrow | dimension | line | vline |
                                   # hline | text | vspan | hspan
    x: float = 0.0
    y: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    text: str = ""
    axes_index: int = 0
    dx: float = 24.0              # label offset in points (point / arrow kinds)
    dy: float = 20.0
    color: str = ANNOTATION_COLOR
    fontsize: float = 8.0
    linestyle: str = "--"
    linewidth: float = 0.9
    rotation: float = 0.0         # label rotation in degrees
    boxed: bool = True
    arrow: str = "->"
    label_pos: float = 0.5        # axes fraction along a reference line, or
                                   # fraction along the segment for "line"/
                                   # "dimension"
    label_parallel: bool = False  # rotate the label to match the on-screen
                                   # angle of the line instead of `rotation`
    label_free: bool = False      # place the label at (label_x, label_y)
                                   # instead of the automatic position
    label_x: float = 0.0
    label_y: float = 0.0
    on_axis: bool = False        # also stamp a tick mark at x (vline) / y
                                  # (hline) on the axis itself
    axis_text: str = ""          # text shown at that tick; "" falls back to
                                  # `text` -- never the raw numeric value
    axis_fontfamily: str = ""    # "" -> inherit `fontfamily`
    axis_fontweight: str = "bold"
    axis_fontstyle: str = "normal"
    axis_fontsize: float = 0.0   # 0.0 -> inherit the axis' own tick size
                                  # (rcParams `{x,y}tick.labelsize`, read live
                                  # so it follows GUI vs. export context)
    axis_side: str = ""          # "" -> default side ("bottom" for vline,
                                  # "left" for hline); "top"/"right" puts
                                  # the mark on the opposite axis instead
    dim_orientation: str = "auto"  # "auto" | "horizontal" | "vertical" --
                                    # how a "dimension" kind's two captured
                                    # points are projected before drawing
                                    # (see `_render_dimension`)
    dim_auto_text: bool = True    # "dimension" only: the GUI recomputes
                                   # `text` as the measured distance (in the
                                   # selected axis' unit) on every Agregar/
                                   # Actualizar; False keeps whatever the
                                   # user typed by hand. This module never
                                   # reads it -- it only ever renders
                                   # whatever `text` already holds (see the
                                   # module docstring: no unit awareness here).
    alpha: float = 1.0
    marker: str = "o"
    markersize: float = 4.0
    fontfamily: str = ""          # "" -> rcParams default
    fontweight: str = "normal"    # normal | bold
    fontstyle: str = "normal"     # normal | italic
    ha: str = ""                  # "" -> per-kind default (see _text_kwargs)
    va: str = ""
    visible: bool = True


# Ready-made styles. "referencia" reproduces the classic report look: thin
# dashed line, small rotated math label in a white box.
# Sensible starting values per annotation kind. The UI seeds its form with
# these so a vertical reference line comes out with a rotated label and a band
# comes out translucent, without the renderer having to special-case anything.
KIND_DEFAULTS: dict[str, dict] = {
    "point": {"rotation": 0.0, "alpha": 1.0, "boxed": True,
              "dx": 26.0, "dy": 20.0, "arrow": "->"},
    "arrow": {"rotation": 0.0, "alpha": 1.0, "boxed": True,
              "dx": 0.0, "dy": 10.0, "arrow": "<->"},
    "dimension": {"rotation": 0.0, "alpha": 1.0, "boxed": True,
                  "label_pos": 0.5, "linestyle": "-", "linewidth": 0.9,
                  "dim_orientation": "auto", "dim_auto_text": True},
    "line": {"rotation": 0.0, "alpha": 1.0, "boxed": True, "label_pos": 0.5,
             "linestyle": "-"},
    "vline": {"rotation": 90.0, "alpha": 1.0, "boxed": True, "label_pos": 0.45},
    "hline": {"rotation": 0.0, "alpha": 1.0, "boxed": True, "label_pos": 0.5},
    "text":  {"rotation": 0.0, "alpha": 1.0, "boxed": False},
    "vspan": {"rotation": 0.0, "alpha": 0.12, "boxed": True, "label_pos": 0.90},
    "hspan": {"rotation": 0.0, "alpha": 0.12, "boxed": True, "label_pos": 0.90},
}

# Display names kept short and jargon-free (novice-facing panel redesign):
# what used to describe the preset's parameters in the label itself now just
# names what the user is trying to do with it.
STYLE_PRESETS: dict[str, dict] = {
    "Clásico": {
        "linestyle": "--", "linewidth": 0.9, "fontsize": 7.5,
        "boxed": True, "rotation": 90.0, "label_pos": 0.45,
    },
    "Destacar un punto": {
        "marker": "o", "markersize": 4.0, "fontsize": 8.0,
        "boxed": True, "arrow": "->", "dx": 26.0, "dy": 20.0,
    },
    "Medir una distancia": {
        "arrow": "<->", "linewidth": 0.9, "fontsize": 8.0, "boxed": True,
        "dy": 10.0, "dx": 0.0,
    },
}


# (primary, secondary) side per axis kind: vline marks x ("bottom"/"top"),
# hline marks y ("left"/"right"). An empty `spec.axis_side` means "primary".
_AXIS_SIDES: dict[str, tuple[str, str]] = {"x": ("bottom", "top"),
                                            "y": ("left", "right")}


class AnnotationManager:
    """Persistent annotation set rendered on top of the current axes."""

    def __init__(self, canvas, on_change: Optional[Callable[[], None]] = None):
        self.canvas = canvas
        self.on_change = on_change
        self.axes: list = []
        self.items: list[AnnotationSpec] = []
        self._artists: dict[int, list] = {}
        self._next_id = 1
        self._pick_callback: Optional[Callable[[int, float, float], None]] = None
        self._cid = canvas.mpl_connect("button_press_event", self._on_press)

    # ------------------------------- wiring ------------------------------ #
    def disconnect(self) -> None:
        try:
            self.canvas.mpl_disconnect(self._cid)
        except Exception:
            pass

    def attach(self, axes: Sequence) -> None:
        # Same fix as CursorManager.attach: destroy before dropping the
        # references, then sweep orphans by gid.
        for aid in list(self._artists):
            self._destroy_artists(aid)
        self._artists.clear()
        self.axes = list(axes)
        purge_overlay_artists(self.axes, gid=ANNOTATION_GID)
        n = len(self.axes)
        for spec in self.items:
            if spec.axes_index >= n:
                spec.axes_index = 0

    # ------------------------------ mutation ----------------------------- #
    def add(self, **kwargs) -> AnnotationSpec:
        spec = AnnotationSpec(aid=self._next_id, **kwargs)
        self._next_id += 1
        if self.axes:
            spec.axes_index = max(0, min(spec.axes_index, len(self.axes) - 1))
        self.items.append(spec)
        return spec

    def update(self, aid: int, **kwargs) -> Optional[AnnotationSpec]:
        spec = self.get(aid)
        if spec is None:
            return None
        for key, value in kwargs.items():
            if hasattr(spec, key):
                setattr(spec, key, value)
        # `add()` clamps `axes_index` against `self.axes` at creation time,
        # but this didn't: editing an annotation (e.g. via the "Actualizar"
        # form, which still carries the axes_index it was captured with)
        # after the plot was rebuilt with FEWER axes -- switching Bode from
        # "Separado" (two axes) to "Juntos" (one) -- left `spec.axes_index`
        # out of range. `_render()` then indexed `self.axes[spec.axes_index]`
        # and raised, which `redraw()`'s broad except silently swallowed:
        # the annotation just vanished from the canvas with no error shown.
        if self.axes:
            spec.axes_index = max(0, min(spec.axes_index, len(self.axes) - 1))
        return spec

    def remove(self, aid: int) -> None:
        self._destroy_artists(aid)
        self.items = [a for a in self.items if a.aid != aid]

    def clear(self) -> None:
        for aid in list(self._artists):
            self._destroy_artists(aid)
        self.items.clear()

    def get(self, aid: int) -> Optional[AnnotationSpec]:
        return next((a for a in self.items if a.aid == aid), None)

    # --------------------------- point capture --------------------------- #
    def arm_pick(self, callback: Callable[[int, float, float], None]) -> None:
        """Capture the next canvas click and hand back (axes_index, x, y)."""
        self._pick_callback = callback

    def disarm(self) -> None:
        self._pick_callback = None

    @property
    def armed(self) -> bool:
        return self._pick_callback is not None

    def _on_press(self, event) -> None:
        if self._pick_callback is None or event.inaxes is None:
            return
        index = 0
        for i, base in enumerate(self.axes):
            if base is event.inaxes or event.inaxes in _sibling_axes(base):
                index = i
                break
        ax = self.axes[index] if self.axes else event.inaxes
        x, y = ax.transData.inverted().transform((event.x, event.y))
        callback, self._pick_callback = self._pick_callback, None
        try:
            callback(index, float(x), float(y))
        except Exception:
            pass

    # ------------------------------ rendering ---------------------------- #
    def redraw(self) -> None:
        if not self.axes:
            return
        for aid in list(self._artists):
            self._destroy_artists(aid)
        for spec in self.items:
            if spec.visible:
                try:
                    self._artists[spec.aid] = self._render(spec)
                except Exception:
                    # A single malformed annotation must not abort the plot.
                    self._artists[spec.aid] = []
        try:
            self._apply_axis_marks()
        except Exception:
            pass   # a tick-styling glitch must never break the canvas

    def _destroy_artists(self, aid: int) -> None:
        for artist in self._artists.pop(aid, []):
            try:
                artist.remove()
            except (NotImplementedError, ValueError, AttributeError):
                pass

    # ------------------------------ axis marks ---------------------------- #
    def _axis_marks(self, axes_index: int, axis_kind: str, side: str) -> dict:
        """Specs that stamp a mark on one (axis_kind, side), keyed by position.

        `side` is one of the two values `_AXIS_SIDES[axis_kind]` holds (e.g.
        "bottom"/"top" for axis_kind="x"). A spec with an empty or invalid
        `axis_side` falls back to the primary side -- the first entry of
        that tuple -- which also keeps every annotation created before this
        field existed exactly where it always rendered.
        """
        kind = "vline" if axis_kind == "x" else "hline"
        primary, secondary = _AXIS_SIDES[axis_kind]
        marks: dict[float, AnnotationSpec] = {}
        for spec in self.items:
            if (spec.kind != kind or not spec.visible or not spec.on_axis
                    or spec.axes_index != axes_index):
                continue
            spec_side = spec.axis_side if spec.axis_side in (primary, secondary) else primary
            if spec_side != side:
                continue
            text = spec.axis_text or spec.text
            if not text:
                continue
            value = spec.x if axis_kind == "x" else spec.y
            marks[float(value)] = spec
        return marks

    def _apply_axis_marks(self) -> None:
        for index, ax in enumerate(self.axes):
            for axis_kind, (primary, secondary) in _AXIS_SIDES.items():
                self._mark_axis(ax, axis_kind,
                                self._axis_marks(index, axis_kind, primary))
                self._mark_opposite_axis(
                    ax, axis_kind, secondary,
                    self._axis_marks(index, axis_kind, secondary))

    def uses_opposite_axis(self) -> bool:
        """
        True if any visible mark asks for the non-default side (top/right).

        `App.update_plot`/`_refresh_overlays` check this to decide whether a
        second `tight_layout()` pass is worth running after `redraw()` --
        see the note on `_mark_opposite_axis` for why the first pass, run
        before the secondary axis exists, cannot already have reserved
        room for it.
        """
        for spec in self.items:
            if (spec.kind in ("vline", "hline") and spec.visible and spec.on_axis
                    and spec.axis_side in ("top", "right")):
                return True
        return False

    _AXIS_TICK_PIXEL_MARGIN = 26   # see `_declutter_auto_ticks`

    def _declutter_auto_ticks(self, ax, axis_kind: str, auto_locs,
                              marks: dict) -> list:
        """
        Drop the automatic ticks that would visually collide with a mark.

        A fixed DATA-space cutoff is meaningless here: it would be wrong on
        a log axis (where equal data distance means very different pixel
        distance depending on where you are in the decade) and wrong again
        the moment the user zooms/pans. Distance is measured in PIXELS
        instead, via `ax.transData` -- the same transform `_screen_angle`
        already relies on elsewhere in this module -- so "too close" tracks
        what actually overlaps on screen regardless of scale or view.

        `_AXIS_TICK_PIXEL_MARGIN` is a rough half-label-width budget, not a
        measured text extent: getting the exact rendered width of a tick
        label needs a real draw pass with a live renderer, which isn't
        guaranteed to exist yet at this point in `redraw()` (same
        constraint already noted on `_screen_angle`). Erring conservative
        (dropping a tick that might have just barely fit) reads as "the
        axis made room for the mark", which is what was actually asked for;
        erring the other way reads as a bug.
        """
        if not marks:
            return list(auto_locs)
        idx = 0 if axis_kind == "x" else 1
        def px(value: float) -> float:
            point = (value, 0.0) if axis_kind == "x" else (0.0, value)
            return ax.transData.transform(point)[idx]
        mark_px = [px(v) for v in marks]
        return [loc for loc in auto_locs
                if all(abs(px(loc) - mpx) >= self._AXIS_TICK_PIXEL_MARGIN
                       for mpx in mark_px)]

    def _style_marked_ticks(self, axis, marks: dict, axis_kind: str) -> None:
        """
        Bold/tint the tick label at each marked position; reset the rest.

        Shared by the primary axis (where unmarked ticks come from the
        app's own locator/formatter) and a secondary axis (where every
        tick IS a mark, so the "reset" branch below simply never matches
        there) -- one styling rule instead of two copies to keep in sync.
        Tick `Text` objects are cached and reused across `set_major_locator`
        calls, so a stale bold/tinted style from a removed or moved mark
        must be explicitly cleared here, not just left un-reapplied.

        Font SIZE is read live from `mpl.rcParams` (`"{x,y}tick.labelsize"`)
        for the same reason `default_color` already is: `core/export.py`
        overrides that rcParam for the export figure, and reading it live
        here (instead of hardcoding a number) makes the reset branch follow
        whichever context -- live GUI canvas or export render -- is
        actually active for this `redraw()` call.
        """
        default_color = mpl.rcParams.get(f"{axis_kind}tick.color", "black")
        default_fontsize = mpl.rcParams.get(f"{axis_kind}tick.labelsize")
        for tick, loc in zip(axis.get_major_ticks(), axis.get_majorticklocs()):
            label = tick.label1
            spec = next((s for v, s in marks.items()
                        if math.isclose(loc, v, rel_tol=1e-9, abs_tol=1e-12)), None)
            if spec is not None:
                label.set_fontweight(spec.axis_fontweight or "bold")
                label.set_fontstyle(spec.axis_fontstyle or "normal")
                label.set_fontfamily(spec.axis_fontfamily or spec.fontfamily or
                                     mpl.rcParams["font.family"])
                label.set_color(spec.color)
                label.set_fontsize(spec.axis_fontsize or default_fontsize)
            else:
                label.set_fontweight("normal")
                label.set_fontstyle("normal")
                label.set_fontfamily(mpl.rcParams["font.family"])
                label.set_color(default_color)
                label.set_fontsize(default_fontsize)

    def _mark_axis(self, ax, axis_kind: str, marks: dict) -> None:
        """
        Add/refresh custom-text tick marks on the PRIMARY side of `ax`
        (bottom for x, left for y) -- the plot's normal tick scale stays as
        it was, with the marked position(s) swapped to custom text and
        styled, EXCEPT for any automatic tick close enough to collide with
        a mark on screen (see `_declutter_auto_ticks`) -- dropped so the
        mark doesn't render glued to a neighbouring tick label. See
        `_mark_opposite_axis` for the top/right case.

        The axis' own locator/formatter are snapshotted once per live `ax`
        instance (guarded by `hasattr`): `redraw()` can run several times
        against the same `ax` without an intervening `fig.clear()` (e.g.
        toggling a checkbox), and re-snapshotting an already-wrapped
        formatter would nest wrappers instead of restoring the real base.
        A fresh `ax` -- created by `App._prepare_axes()`'s `fig.clear()` --
        has neither attribute, so the next call re-snapshots correctly.
        """
        axis = ax.xaxis if axis_kind == "x" else ax.yaxis
        locator_attr = f"_lp_base_{axis_kind}locator"
        formatter_attr = f"_lp_base_{axis_kind}formatter"
        if not hasattr(ax, locator_attr):
            setattr(ax, locator_attr, axis.get_major_locator())
        if not hasattr(ax, formatter_attr):
            setattr(ax, formatter_attr, axis.get_major_formatter())
        base_locator = getattr(ax, locator_attr)
        base_formatter = getattr(ax, formatter_attr)

        # Restore the axis' own auto locator first -- otherwise a previous
        # call's FixedLocator (marked positions baked in) would compound
        # with itself instead of being recomputed from the current view.
        axis.set_major_locator(base_locator)
        if marks:
            kept_auto = self._declutter_auto_ticks(
                ax, axis_kind, axis.get_majorticklocs(), marks)
            combined = sorted(set(kept_auto) | set(marks))
            axis.set_major_locator(mticker.FixedLocator(combined))
            axis.set_major_formatter(_MarkedFormatter(
                base_formatter,
                {v: (s.axis_text or s.text) for v, s in marks.items()}))
        else:
            axis.set_major_formatter(base_formatter)

        self._style_marked_ticks(axis, marks, axis_kind)

    def _mark_opposite_axis(self, ax, axis_kind: str, side: str, marks: dict) -> None:
        """
        Mirror-side mark: top for a vline, right for an hline.

        Unlike `_mark_axis`, which highlights one of the plot's normal
        ticks in place, the whole point of choosing the opposite side is to
        put a value where the regular ticks are NOT -- so this never
        inherits or duplicates the primary scale. A `secondary_xaxis`/
        `secondary_yaxis` is created lazily (only once marks actually ask
        for this side, cached on `ax` the same way the base locator/
        formatter are) and shows ONLY the marked position(s): no "base"
        formatter to fall back to, because every tick on it is a mark.

        If it was never created and there is nothing to show, this is a
        no-op -- an unused secondary axis would otherwise leave a bare,
        permanently empty spine the user never asked for. Once created, it
        is kept (with an empty locator) rather than destroyed when its last
        mark is removed: recreating axes mid-session is the kind of
        geometry churn this codebase has already been burned by (see
        Claude.md, the `<Configure>` cascade bug), and an empty secondary
        spine is visually inert.
        """
        cache_attr = f"_lp_secondary_{side}"
        secondary = getattr(ax, cache_attr, None)
        if secondary is None:
            if not marks:
                return
            make = ax.secondary_xaxis if axis_kind == "x" else ax.secondary_yaxis
            secondary = make(side)
            setattr(ax, cache_attr, secondary)

        axis = secondary.xaxis if axis_kind == "x" else secondary.yaxis
        axis.set_major_locator(mticker.FixedLocator(sorted(marks)))
        axis.set_major_formatter(mticker.FuncFormatter(
            lambda x, _pos=None, m=marks: next(
                ((s.axis_text or s.text) for v, s in m.items()
                 if math.isclose(x, v, rel_tol=1e-9, abs_tol=1e-12)), "")))
        self._style_marked_ticks(axis, marks, axis_kind)

    def _box(self, spec: AnnotationSpec) -> Optional[dict]:
        if not spec.boxed:
            return None
        # Square box: the report figure and the application chrome share the
        # same right-angled vocabulary.
        return dict(boxstyle="square,pad=0.30", fc="white", ec="#4A473F",
                    lw=0.6, alpha=0.90)

    def _tracked(self) -> list:
        """Every artist this manager currently owns."""
        return [artist for group in self._artists.values() for artist in group]

    def _text_kwargs(self, spec: AnnotationSpec, ha: str = "center",
                     va: str = "center") -> dict:
        """
        Text styling shared by every kind renderer.

        `ha`/`va` are the per-kind defaults; an empty value on the spec means
        "keep the default", so annotations saved before these fields existed
        render exactly as they used to.
        """
        kwargs = {"fontsize": spec.fontsize, "color": spec.color,
                  "ha": spec.ha or ha, "va": spec.va or va}
        if spec.fontfamily:
            kwargs["fontfamily"] = spec.fontfamily
        if spec.fontweight and spec.fontweight != "normal":
            kwargs["fontweight"] = spec.fontweight
        if spec.fontstyle and spec.fontstyle != "normal":
            kwargs["fontstyle"] = spec.fontstyle
        return kwargs

    def _render(self, spec: AnnotationSpec) -> list:
        ax = self.axes[spec.axes_index]
        artists: list = []
        renderer = getattr(self, f"_render_{spec.kind}", None)
        if renderer is None:
            return artists
        try:
            produced = renderer(ax, spec)
        except Exception:
            # The kind renderer may have added artists before failing (e.g.
            # axvline succeeds, then invalid mathtext blows up in ax.text).
            # `redraw()` swallows the exception, so without this sweep those
            # artists survive untracked -- a permanent ghost.
            purge_overlay_artists([ax], gid=ANNOTATION_GID, keep=self._tracked())
            raise
        for artist in produced:
            if artist is None:
                continue
            try:
                artist.set_gid(ANNOTATION_GID)
            except AttributeError:
                pass
            artists.append(artist)
        return artists

    def _render_point(self, ax, spec: AnnotationSpec) -> list:
        marker, = ax.plot([spec.x], [spec.y], linestyle="none",
                          marker=spec.marker, markersize=spec.markersize,
                          markerfacecolor=spec.color, markeredgecolor=spec.color,
                          alpha=spec.alpha, zorder=7, label="_nolegend_")
        out = [marker]
        if spec.text:
            arrowprops = None
            if spec.dx or spec.dy:
                arrowprops = dict(arrowstyle=spec.arrow, color=spec.color,
                                  lw=spec.linewidth, shrinkA=0.0, shrinkB=3.0)
            out.append(ax.annotate(
                spec.text, xy=(spec.x, spec.y), xytext=(spec.dx, spec.dy),
                textcoords="offset points", bbox=self._box(spec),
                arrowprops=arrowprops, zorder=8, annotation_clip=False,
                **self._text_kwargs(spec)))
        return out

    def _render_arrow(self, ax, spec: AnnotationSpec) -> list:
        out = [ax.annotate(
            "", xy=(spec.x2, spec.y2), xytext=(spec.x, spec.y),
            xycoords="data", textcoords="data",
            arrowprops=dict(arrowstyle=spec.arrow, color=spec.color,
                            lw=spec.linewidth, shrinkA=0.0, shrinkB=0.0),
            zorder=7, annotation_clip=False)]
        if spec.text:
            mid_x = 0.5 * (spec.x + spec.x2)
            mid_y = 0.5 * (spec.y + spec.y2)
            out.append(ax.annotate(
                spec.text, xy=(mid_x, mid_y), xytext=(spec.dx, spec.dy),
                textcoords="offset points", bbox=self._box(spec), zorder=8,
                annotation_clip=False, **self._text_kwargs(spec)))
        return out

    def _render_dimension(self, ax, spec: AnnotationSpec) -> list:
        """
        Technical dimension line ("cota"): a segment between two points
        capped with a short perpendicular tick at each end, the acotado
        convention of a mechanical/electronic drawing. Distinct from
        "arrow" (a leader with an arrowhead, no end caps, no measuring
        intent) and from "line" (a bare segment, no caps either).

        `dim_orientation` projects the two captured points before drawing:
        "horizontal" flattens the second point to the first one's Y (the
        cota reads a distance along X, drawn at a fixed height); "vertical"
        flattens to the first point's X; "auto" draws the segment exactly
        as captured, an arbitrary line.

        The measured distance itself is never computed here -- `text`
        already carries it (or whatever the user typed by hand): this
        module has no notion of axis units (see the module docstring), so
        turning a distance into "1.20 kHz" is `OverlayPanel._form_values`'s
        job, done once, when the annotation is added/updated, exactly the
        same "completar y confirmar" convention every other field in this
        form already follows.
        """
        x1, y1, x2, y2 = spec.x, spec.y, spec.x2, spec.y2
        if spec.dim_orientation == "horizontal":
            y2 = y1
        elif spec.dim_orientation == "vertical":
            x2 = x1
        seg, = ax.plot([x1, x2], [y1, y2], color=spec.color, ls=spec.linestyle,
                       lw=spec.linewidth, alpha=spec.alpha, solid_capstyle="butt",
                       zorder=5, label="_nolegend_")
        out = [seg]
        a1, a2, b1, b2 = _perp_ticks(ax, x1, y1, x2, y2)
        for p1, p2 in ((a1, a2), (b1, b2)):
            tick, = ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=spec.color,
                            lw=spec.linewidth, alpha=spec.alpha,
                            solid_capstyle="butt", zorder=5, label="_nolegend_")
            out.append(tick)
        if spec.text:
            if spec.label_free:
                tx, ty = spec.label_x, spec.label_y
            else:
                tx = x1 + spec.label_pos * (x2 - x1)
                ty = y1 + spec.label_pos * (y2 - y1)
            rotation = (_screen_angle(ax, x1, y1, x2, y2)
                       if spec.label_parallel else spec.rotation)
            out.append(ax.text(
                tx, ty, spec.text, rotation=rotation, rotation_mode="anchor",
                bbox=self._box(spec), zorder=8, clip_on=False,
                **self._text_kwargs(spec)))
        return out

    def _render_vline(self, ax, spec: AnnotationSpec) -> list:
        line = ax.axvline(spec.x, color=spec.color, ls=spec.linestyle,
                          lw=spec.linewidth, alpha=spec.alpha, zorder=5,
                          label="_nolegend_")
        out = [line]
        if spec.text:
            # A vertical line is always vertical on screen no matter the data
            # scale, so "parallel" is just the historical 90 deg default --
            # no angle computation needed here, unlike the generic "line".
            rotation = 90.0 if spec.label_parallel else spec.rotation
            if spec.label_free:
                out.append(ax.text(
                    spec.label_x, spec.label_y, spec.text, rotation=rotation,
                    rotation_mode="anchor", bbox=self._box(spec), zorder=8,
                    clip_on=False,
                    **self._text_kwargs(spec, ha="center", va="bottom")))
            else:
                out.append(ax.text(
                    spec.x, spec.label_pos, spec.text,
                    transform=ax.get_xaxis_transform(), rotation=rotation,
                    rotation_mode="anchor", bbox=self._box(spec), zorder=8,
                    clip_on=False,
                    **self._text_kwargs(spec, ha="center", va="bottom")))
        return out

    def _render_hline(self, ax, spec: AnnotationSpec) -> list:
        line = ax.axhline(spec.y, color=spec.color, ls=spec.linestyle,
                          lw=spec.linewidth, alpha=spec.alpha, zorder=5,
                          label="_nolegend_")
        out = [line]
        if spec.text:
            rotation = 0.0 if spec.label_parallel else spec.rotation
            if spec.label_free:
                out.append(ax.text(
                    spec.label_x, spec.label_y, spec.text, rotation=rotation,
                    rotation_mode="anchor", bbox=self._box(spec), zorder=8,
                    clip_on=False,
                    **self._text_kwargs(spec, ha="center", va="bottom")))
            else:
                out.append(ax.text(
                    spec.label_pos, spec.y, spec.text,
                    transform=ax.get_yaxis_transform(), rotation=rotation,
                    bbox=self._box(spec), zorder=8, clip_on=False,
                    **self._text_kwargs(spec, ha="center", va="bottom")))
        return out

    def _render_line(self, ax, spec: AnnotationSpec) -> list:
        """Straight segment between two arbitrary points, at any angle."""
        seg, = ax.plot([spec.x, spec.x2], [spec.y, spec.y2], color=spec.color,
                       ls=spec.linestyle, lw=spec.linewidth, alpha=spec.alpha,
                       solid_capstyle="butt", zorder=5, label="_nolegend_")
        out = [seg]
        if spec.text:
            if spec.label_free:
                tx, ty = spec.label_x, spec.label_y
            else:
                tx = spec.x + spec.label_pos * (spec.x2 - spec.x)
                ty = spec.y + spec.label_pos * (spec.y2 - spec.y)
            rotation = (_screen_angle(ax, spec.x, spec.y, spec.x2, spec.y2)
                       if spec.label_parallel else spec.rotation)
            out.append(ax.text(
                tx, ty, spec.text, rotation=rotation, rotation_mode="anchor",
                bbox=self._box(spec), zorder=8, clip_on=False,
                **self._text_kwargs(spec)))
        return out

    def _render_text(self, ax, spec: AnnotationSpec) -> list:
        return [ax.text(spec.x, spec.y, spec.text, rotation=spec.rotation,
                        bbox=self._box(spec), zorder=8, clip_on=False,
                        **self._text_kwargs(spec))]

    def _render_vspan(self, ax, spec: AnnotationSpec) -> list:
        span = ax.axvspan(min(spec.x, spec.x2), max(spec.x, spec.x2),
                          color=spec.color, alpha=spec.alpha, lw=0.0, zorder=1)
        out = [span]
        if spec.text:
            out.append(ax.text(
                0.5 * (spec.x + spec.x2), spec.label_pos, spec.text,
                transform=ax.get_xaxis_transform(), bbox=self._box(spec),
                zorder=8, clip_on=False,
                **self._text_kwargs(spec, ha="center", va="bottom")))
        return out

    def _render_hspan(self, ax, spec: AnnotationSpec) -> list:
        span = ax.axhspan(min(spec.y, spec.y2), max(spec.y, spec.y2),
                          color=spec.color, alpha=spec.alpha, lw=0.0, zorder=1)
        out = [span]
        if spec.text:
            out.append(ax.text(
                spec.label_pos, 0.5 * (spec.y + spec.y2), spec.text,
                transform=ax.get_yaxis_transform(), bbox=self._box(spec),
                zorder=8, clip_on=False,
                **self._text_kwargs(spec, ha="center", va="bottom")))
        return out

    # ----------------------------- persistence --------------------------- #
    def to_dict(self) -> dict:
        return {"annotations": [asdict(a) for a in self.items]}

    def from_dict(self, payload: dict) -> None:
        self.clear()
        for item in payload.get("annotations", []):
            try:
                spec = _from_dict(AnnotationSpec, item)
            except TypeError:
                continue
            spec.aid = self._next_id
            self._next_id += 1
            self.items.append(spec)


# ========================================================================== #
# Combined overlay state (JSON on disk)
# ========================================================================== #
OVERLAY_FILE_VERSION = 1


def save_overlays(path: str, cursors: CursorManager,
                  annotations: AnnotationManager) -> str:
    payload = {"version": OVERLAY_FILE_VERSION,
               "cursors": cursors.to_dict(),
               "annotations": annotations.to_dict()}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return path


def load_overlays(path: str, cursors: CursorManager,
                  annotations: AnnotationManager) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Archivo de overlays inválido.")
    cursors.from_dict(payload.get("cursors", {}) or {})
    annotations.from_dict(payload.get("annotations", {}) or {})
