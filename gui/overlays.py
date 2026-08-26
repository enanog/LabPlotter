"""
gui/overlays.py
---------------
Interactive overlay layer for the Matplotlib canvas:

* `CursorManager`    -- an arbitrary number of draggable measurement cursors
                        (vertical / horizontal) with per-curve readout and
                        delta computation between cursors.
* `AnnotationManager`-- report-grade annotations: points of interest with a
                        leader arrow, standalone arrows, dashed reference
                        lines with a rotated inline label, free text and
                        shaded bands.

Both managers hold *state* (plain dataclasses), never widgets, and re-create
their artists on demand. This is what makes them survive the full
`fig.clear()` + re-plot cycle performed by `App.update_plot()`, and it is also
what allows an overlay set to be serialised to JSON and reloaded later so a
report figure is exactly reproducible.

The module depends on Matplotlib and NumPy only: no CustomTkinter import, so
it can be driven from a GUI panel, a script or a batch export.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from typing import Callable, Optional, Sequence

import numpy as np

# Every artist created by this module carries this gid, so data curves can be
# told apart from overlay artists without relying on labels.
OVERLAY_GID = "_labplotter_overlay"

# Overlay chrome is intentionally greyscale: the color channel belongs to the
# data. Annotations may override the color per item (e.g. to match a curve).
CURSOR_COLOR = "#2A2724"
ANNOTATION_COLOR = "#2A2724"

ARROW_STYLES: list[str] = ["->", "<-", "<->", "-|>", "<|-|>", "-"]
LINESTYLES: list[str] = ["--", "-", "-.", ":"]
ANNOTATION_KINDS: dict[str, str] = {
    "Punto de interés": "point",
    "Flecha": "arrow",
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
               mathtext: bool = False) -> str:
    """Format a number in engineering notation (1.23k, 470u, -3.01)."""
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
        return f"0{suffix}"

    magnitude = abs(v)
    factor, prefix = 1e-12, "p"
    for f, p in _SI_PREFIXES:
        if magnitude >= f:
            factor, prefix = f, p
            break

    text = f"{v / factor:.{digits}g}"
    if prefix and mathtext and prefix in _MATH_PREFIX:
        return f"{text} {_MATH_PREFIX[prefix]}{unit}".rstrip()
    return f"{text} {prefix}{unit}".rstrip() if (prefix or unit) else text


def _is_data_line(artist) -> bool:
    """True for a user curve; False for overlay artists and private labels."""
    if artist.get_gid() == OVERLAY_GID:
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
        self.x_unit = ""
        self.y_unit = ""

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
        self.axes = list(axes)
        self._artists.clear()
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
        line.set_gid(OVERLAY_GID)
        artists = [line]

        if self.show_tags:
            name = self.name_of(spec)
            if self.tag_with_value:
                unit = self.x_unit if spec.orientation == "v" else self.y_unit
                name = f"{name}: {format_eng(spec.position, unit, 4, mathtext=True)}"
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
            tag.set_gid(OVERLAY_GID)
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
                unit = self.x_unit if spec.orientation == "v" else self.y_unit
                name = f"{name}: {format_eng(spec.position, unit, 4, mathtext=True)}"
            tag.set_text(name)
            if spec.orientation == "v":
                tag.set_position((spec.position, 0.985))
            else:
                tag.set_position((0.012, spec.position))

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
        """
        rows: list[dict] = []
        for spec in self.cursors:
            if spec.axes_index >= len(self.axes):
                continue
            entry = {"cid": spec.cid, "name": self.name_of(spec),
                     "orientation": spec.orientation, "position": spec.position,
                     "values": []}
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
        """Differences between consecutive cursors of the same orientation."""
        out: list[dict] = []
        for orientation in ("v", "h"):
            group = [c for c in self.cursors if c.orientation == orientation]
            for a, b in zip(group, group[1:]):
                if a.axes_index != b.axes_index:
                    continue
                delta = b.position - a.position
                item = {"from": self.name_of(a), "to": self.name_of(b),
                        "orientation": orientation, "delta": delta,
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
                "cursors": [asdict(c) for c in self.cursors]}

    def from_dict(self, payload: dict) -> None:
        self.clear()
        self.snap_to_data = bool(payload.get("snap_to_data", self.snap_to_data))
        self.show_tags = bool(payload.get("show_tags", self.show_tags))
        self.tag_with_value = bool(payload.get("tag_with_value", self.tag_with_value))
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
    """
    aid: int
    kind: str = "point"           # point | arrow | vline | hline | text | vspan | hspan
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
    label_pos: float = 0.5        # axes fraction along a reference line
    alpha: float = 1.0
    marker: str = "o"
    markersize: float = 4.0
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
    "vline": {"rotation": 90.0, "alpha": 1.0, "boxed": True, "label_pos": 0.45},
    "hline": {"rotation": 0.0, "alpha": 1.0, "boxed": True, "label_pos": 0.5},
    "text":  {"rotation": 0.0, "alpha": 1.0, "boxed": False},
    "vspan": {"rotation": 0.0, "alpha": 0.12, "boxed": True, "label_pos": 0.90},
    "hspan": {"rotation": 0.0, "alpha": 0.12, "boxed": True, "label_pos": 0.90},
}

STYLE_PRESETS: dict[str, dict] = {
    "Referencia (línea + etiqueta rotada)": {
        "linestyle": "--", "linewidth": 0.9, "fontsize": 7.5,
        "boxed": True, "rotation": 90.0, "label_pos": 0.45,
    },
    "Punto de interés (marcador + flecha)": {
        "marker": "o", "markersize": 4.0, "fontsize": 8.0,
        "boxed": True, "arrow": "->", "dx": 26.0, "dy": 20.0,
    },
    "Cota / ancho de banda (flecha doble)": {
        "arrow": "<->", "linewidth": 0.9, "fontsize": 8.0, "boxed": True,
        "dy": 10.0, "dx": 0.0,
    },
}


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
        self.axes = list(axes)
        self._artists.clear()
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

    def _destroy_artists(self, aid: int) -> None:
        for artist in self._artists.pop(aid, []):
            try:
                artist.remove()
            except (NotImplementedError, ValueError, AttributeError):
                pass

    def _box(self, spec: AnnotationSpec) -> Optional[dict]:
        if not spec.boxed:
            return None
        # Square box: the report figure and the application chrome share the
        # same right-angled vocabulary.
        return dict(boxstyle="square,pad=0.30", fc="white", ec="#4A473F",
                    lw=0.6, alpha=0.90)

    def _render(self, spec: AnnotationSpec) -> list:
        ax = self.axes[spec.axes_index]
        artists: list = []
        renderer = getattr(self, f"_render_{spec.kind}", None)
        if renderer is None:
            return artists
        for artist in renderer(ax, spec):
            if artist is None:
                continue
            try:
                artist.set_gid(OVERLAY_GID)
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
                textcoords="offset points", fontsize=spec.fontsize,
                color=spec.color, ha="center", va="center",
                bbox=self._box(spec), arrowprops=arrowprops, zorder=8,
                annotation_clip=False))
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
                textcoords="offset points", fontsize=spec.fontsize,
                color=spec.color, ha="center", va="center",
                bbox=self._box(spec), zorder=8, annotation_clip=False))
        return out

    def _render_vline(self, ax, spec: AnnotationSpec) -> list:
        line = ax.axvline(spec.x, color=spec.color, ls=spec.linestyle,
                          lw=spec.linewidth, alpha=spec.alpha, zorder=5,
                          label="_nolegend_")
        out = [line]
        if spec.text:
            out.append(ax.text(
                spec.x, spec.label_pos, spec.text,
                transform=ax.get_xaxis_transform(), rotation=spec.rotation,
                rotation_mode="anchor", ha="center", va="bottom",
                fontsize=spec.fontsize, color=spec.color,
                bbox=self._box(spec), zorder=8, clip_on=False))
        return out

    def _render_hline(self, ax, spec: AnnotationSpec) -> list:
        line = ax.axhline(spec.y, color=spec.color, ls=spec.linestyle,
                          lw=spec.linewidth, alpha=spec.alpha, zorder=5,
                          label="_nolegend_")
        out = [line]
        if spec.text:
            out.append(ax.text(
                spec.label_pos, spec.y, spec.text,
                transform=ax.get_yaxis_transform(), rotation=spec.rotation,
                ha="center", va="bottom", fontsize=spec.fontsize,
                color=spec.color, bbox=self._box(spec), zorder=8, clip_on=False))
        return out

    def _render_text(self, ax, spec: AnnotationSpec) -> list:
        return [ax.text(spec.x, spec.y, spec.text, fontsize=spec.fontsize,
                        color=spec.color, rotation=spec.rotation,
                        ha="center", va="center", bbox=self._box(spec),
                        zorder=8, clip_on=False)]

    def _render_vspan(self, ax, spec: AnnotationSpec) -> list:
        span = ax.axvspan(min(spec.x, spec.x2), max(spec.x, spec.x2),
                          color=spec.color, alpha=spec.alpha, lw=0.0, zorder=1)
        out = [span]
        if spec.text:
            out.append(ax.text(
                0.5 * (spec.x + spec.x2), spec.label_pos, spec.text,
                transform=ax.get_xaxis_transform(), ha="center", va="bottom",
                fontsize=spec.fontsize, color=spec.color,
                bbox=self._box(spec), zorder=8, clip_on=False))
        return out

    def _render_hspan(self, ax, spec: AnnotationSpec) -> list:
        span = ax.axhspan(min(spec.y, spec.y2), max(spec.y, spec.y2),
                          color=spec.color, alpha=spec.alpha, lw=0.0, zorder=1)
        out = [span]
        if spec.text:
            out.append(ax.text(
                spec.label_pos, 0.5 * (spec.y + spec.y2), spec.text,
                transform=ax.get_yaxis_transform(), ha="center", va="bottom",
                fontsize=spec.fontsize, color=spec.color,
                bbox=self._box(spec), zorder=8, clip_on=False))
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
