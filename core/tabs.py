"""
core/tabs.py
------------
A named snapshot of one plot's full configuration (loaded signals plus every
setting in `App._persisted_vars()`), so the GUI can hold several independent
plots in memory at once ("pestañas") and switch between them without losing
anything -- switching away from a tab snapshots the live state into it;
switching to one restores it. Each tab can still be added to a `core.board`
tablero on its own, one at a time, exactly like the existing single-plot
"agregar al tablero" action -- a tablero is just built up by visiting each
tab in turn and adding its current figure.

Kept in `core/` (no GUI/Matplotlib import) because it is plain data: the
snapshot's shape is owned by the GUI (`App._gather_plot_state` /
`_apply_plot_state`), this module only names and stores it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlotTab:
    """
    One tab: a display name plus whatever `_gather_plot_state()` returned.

    `history` holds that tab's own `core.history.History` (undo/redo stack)
    while it is not the active tab -- `App._switch_tab`/`_close_tab` save the
    outgoing tab's `self.history` here before switching, and hand it back
    when switching to this tab again, instead of every switch replacing
    `self.history` with a brand-new empty one. It stays `None` until the tab
    has been switched away from at least once, and is deliberately left out
    of the session JSON (`App._gather_state` only ever writes `name`/`state`
    per tab): undo history from a previous run of the app is not meaningful
    to restore, so a freshly-opened session just starts every tab with a
    fresh `History()` on first visit, same as before this field existed.
    """

    name: str
    state: dict[str, Any] = field(default_factory=dict)
    history: Any = None
