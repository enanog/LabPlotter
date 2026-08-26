"""
core/history.py
---------------
Undo/redo for the trace set.

The approach is snapshot-based rather than command-based: before any action
that changes the traces, the caller records the current state; undo restores
it. Commands would be leaner in memory, but every action would need its own
inverse written and kept correct as the application grows -- and a wrong
inverse corrupts data silently, which is a far worse failure than holding a
few dictionaries.

What is captured is deliberately small: the `Signal` objects themselves are
*not* copied (they hold the sample arrays, which can be millions of points).
Only their mutable attributes are, along with the ordering. A removed trace
stays alive because the snapshot still references it, so an undo puts back the
same object rather than re-reading the file.
"""

from __future__ import annotations

from typing import Optional

# Signal attributes that the GUI can change. Sample arrays are excluded on
# purpose -- they are never modified in place, only read.
#
# `marker`/`marker_size`/`marker_hollow` and `display_name` were added to
# `Signal` (see core/data_io.py) after this tuple was first written, and were
# never added here: changing a trace's marker style or its list alias was
# silently invisible to undo/redo -- pressing Ctrl+Z after picking a marker
# did nothing, because the snapshot never captured the "before" value in the
# first place. Every field the per-trace panel in gui/app.py can edit should
# be listed here.
TRACKED_ATTRS: tuple[str, ...] = (
    "name", "display_name", "legend_label", "domain", "y_kind",
    "unit_t_in", "unit_v_in", "t_offset", "v_offset", "gain", "invert",
    "linestyle", "marker", "marker_size", "marker_hollow", "color",
    "secondary_y", "visible",
)


class Snapshot:
    """One restorable point in time."""

    __slots__ = ("label", "order", "objects", "attrs", "columns", "selected",
                 "extras")

    def __init__(self, label: str, order: list, objects: dict,
                 attrs: dict, columns: dict, selected: Optional[str],
                 extras: Optional[dict] = None):
        self.label = label
        self.order = order
        self.objects = objects
        self.attrs = attrs
        self.columns = columns
        self.selected = selected
        # Per-trace values the GUI owns rather than the Signal model -- line
        # weight, for instance. Kept here so undo covers them too.
        self.extras = dict(extras or {})


class History:
    """
    Bounded undo/redo stack.

    `limit` caps memory: each snapshot is small, but an unbounded stack in a
    long session is still a leak. Pushing a new snapshot discards the redo
    branch, which is what every editor does and what users expect.
    """

    def __init__(self, limit: int = 50):
        self.limit = limit
        self._undo: list[Snapshot] = []
        self._redo: list[Snapshot] = []

    # ------------------------------------------------------------------ #
    def capture(self, label: str, signals: dict, order: list,
                columns: dict, selected: Optional[str],
                extras: Optional[dict] = None) -> Snapshot:
        """Build a snapshot of the current trace set without storing it."""
        return Snapshot(
            label=label,
            order=list(order),
            objects=dict(signals),
            attrs={uid: {a: getattr(sig, a, None) for a in TRACKED_ATTRS}
                   for uid, sig in signals.items()},
            columns=dict(columns),
            selected=selected,
            extras=extras,
        )

    def push(self, snapshot: Snapshot) -> None:
        self._undo.append(snapshot)
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()   # a new action invalidates the redo branch

    def undo(self, current: Snapshot) -> Optional[Snapshot]:
        if not self._undo:
            return None
        self._redo.append(current)
        return self._undo.pop()

    def redo(self, current: Snapshot) -> Optional[Snapshot]:
        if not self._redo:
            return None
        self._undo.append(current)
        return self._redo.pop()

    # ------------------------------------------------------------------ #
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def next_undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    def next_redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()


def apply_snapshot(snapshot: Snapshot, signals: dict, order: list,
                   columns: dict, extras: Optional[dict] = None) -> Optional[str]:
    """
    Restore a snapshot in place, mutating the caller's containers.

    Returns the uid that was selected when the snapshot was taken, or None if
    that trace no longer exists.
    """
    signals.clear()
    signals.update(snapshot.objects)
    for uid, values in snapshot.attrs.items():
        target = signals.get(uid)
        if target is None:
            continue
        for attr, value in values.items():
            try:
                setattr(target, attr, value)
            except Exception:
                continue   # an attribute the model no longer has: skip it
    order[:] = [uid for uid in snapshot.order if uid in signals]
    columns.clear()
    columns.update(snapshot.columns)
    if extras is not None:
        extras.clear()
        extras.update(snapshot.extras)
    return snapshot.selected if snapshot.selected in signals else None
