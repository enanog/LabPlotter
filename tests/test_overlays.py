# -*- coding: utf-8 -*-
"""Headless regression tests for the overlay layer (no Tk required)."""
import os, sys, json, math, tempfile, unittest

sys.path.insert(0, os.path.expanduser("~/mnt/LabPlotter"))
import matplotlib
matplotlib.use("Agg")
import numpy as np
from matplotlib.figure import Figure

from gui.overlays import (ANNOTATION_GID, CURSOR_GID, OVERLAY_GID,
                          AnnotationManager, CursorManager, _is_data_line,
                          purge_overlay_artists, save_overlays, load_overlays)


def overlay_artists(fig, prefix=OVERLAY_GID):
    """Every artist on the figure whose gid marks it as an overlay."""
    found = []
    for ax in fig.axes:
        for group in (ax.lines, ax.texts, ax.patches, ax.collections,
                      getattr(ax, "artists", [])):
            for artist in group:
                if (artist.get_gid() or "").startswith(prefix):
                    found.append(artist)
    return found


class Base(unittest.TestCase):
    def setUp(self):
        self.fig = Figure(figsize=(6, 4))
        self.ax = self.fig.add_subplot(111)
        t = np.linspace(0, 1e-3, 500)
        self.ax.plot(t, np.sin(2 * np.pi * 1e3 * t), label="V1")
        self.canvas = self.fig.canvas          # FigureCanvasAgg
        self.cursors = CursorManager(self.canvas, max_cursors=None)
        self.annotations = AnnotationManager(self.canvas)
        self.cursors.attach([self.ax])
        self.annotations.attach([self.ax])

    def populate(self):
        self.cursors.add("v", 2.0e-4)
        self.cursors.add("h", 0.5)
        self.annotations.add(kind="vline", x=5e-4, text="f0")
        self.annotations.add(kind="point", x=3e-4, y=0.8, text="pico")
        self.annotations.add(kind="vspan", x=1e-4, x2=2e-4, text="banda")
        self.cursors.redraw()
        self.annotations.redraw()

    def refresh_overlays(self):
        """Exact replica of App._refresh_overlays (live axes, no fig.clear)."""
        self.cursors.attach(self.fig.axes)
        self.annotations.attach(self.fig.axes)
        self.cursors.redraw()
        self.annotations.redraw()


class TestGhostOverlays(Base):
    def test_refresh_on_live_axes_does_not_duplicate(self):
        self.populate()
        baseline = len(overlay_artists(self.fig))
        self.assertGreater(baseline, 0)
        for _ in range(6):
            self.refresh_overlays()
            self.assertEqual(len(overlay_artists(self.fig)), baseline)

    def test_counts_per_manager_are_isolated(self):
        self.populate()
        c0 = len(overlay_artists(self.fig, CURSOR_GID))
        a0 = len(overlay_artists(self.fig, ANNOTATION_GID))
        for _ in range(4):
            self.refresh_overlays()
        self.assertEqual(len(overlay_artists(self.fig, CURSOR_GID)), c0)
        self.assertEqual(len(overlay_artists(self.fig, ANNOTATION_GID)), a0)

    def test_cursor_attach_does_not_sweep_annotations(self):
        """attach() order in App: cursors first, annotations second."""
        self.populate()
        a0 = len(overlay_artists(self.fig, ANNOTATION_GID))
        self.cursors.attach(self.fig.axes)          # must not touch annotations
        self.assertEqual(len(overlay_artists(self.fig, ANNOTATION_GID)), a0)

    def test_replot_cycle_fig_clear(self):
        """update_plot() path: fig.clear() then attach + redraw."""
        self.populate()
        baseline = len(overlay_artists(self.fig))
        for _ in range(3):
            self.fig.clear()
            ax = self.fig.add_subplot(111)
            ax.plot([0, 1e-3], [0, 1])
            self.cursors.attach([ax])
            self.annotations.attach([ax])
            self.cursors.redraw()
            self.annotations.redraw()
        self.assertEqual(len(overlay_artists(self.fig)), baseline)

    def test_remove_leaves_nothing_behind(self):
        self.populate()
        cid = self.cursors.cursors[0].cid
        aid = self.annotations.items[0].aid
        self.cursors.remove(cid)
        self.annotations.remove(aid)
        self.refresh_overlays()
        expected = len(self.cursors.cursors) * 2 + 4   # 2 artists/cursor
        self.assertEqual(len(overlay_artists(self.fig)), expected)

    def test_partial_render_failure_leaves_no_orphan(self):
        """A renderer that raises after adding its line must not leak it."""
        self.annotations.add(kind="vline", x=5e-4, text=r"$\badcmd{x}$")
        self.annotations.redraw()
        # Force the text artist to be laid out: mathtext errors surface then.
        try:
            self.fig.canvas.draw()
        except Exception:
            pass
        tracked = {id(a) for group in self.annotations._artists.values()
                   for a in group}
        for artist in overlay_artists(self.fig, ANNOTATION_GID):
            self.assertIn(id(artist), tracked, "untracked annotation artist")

    def test_purge_never_touches_data_curves(self):
        self.populate()
        data_before = [ln for ln in self.ax.lines if _is_data_line(ln)]
        purge_overlay_artists([self.ax])
        self.assertEqual(len(overlay_artists(self.fig)), 0)
        self.assertEqual([ln for ln in self.ax.lines if _is_data_line(ln)],
                         data_before)

    def test_data_lines_visible_to_readout(self):
        self.populate()
        self.assertEqual(len(self.cursors.data_lines(0)), 1)
        rows = self.cursors.readout()
        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(rows[0]["values"][0]["value"])


class TestCursorMove(Base):
    def test_move_is_in_place(self):
        spec = self.cursors.add("v", 1e-4)
        self.cursors.redraw()
        n0 = len(overlay_artists(self.fig, CURSOR_GID))
        ok = self.cursors.move(spec.cid, 7.5e-4, snap=False)
        self.assertTrue(ok)
        self.assertAlmostEqual(spec.position, 7.5e-4)
        self.assertEqual(len(overlay_artists(self.fig, CURSOR_GID)), n0)
        line = self.cursors._artists[spec.cid][0]
        self.assertAlmostEqual(float(np.asarray(line.get_xdata())[0]), 7.5e-4)

    def test_move_unknown_cursor(self):
        self.assertFalse(self.cursors.move(999, 1.0))

    def test_move_notify_optional(self):
        calls = []
        self.cursors.on_change = lambda: calls.append(1)
        spec = self.cursors.add("v", 1e-4)
        self.cursors.redraw()
        self.cursors.move(spec.cid, 2e-4, snap=False)            # silent
        self.assertEqual(calls, [])
        self.cursors.move(spec.cid, 3e-4, snap=False, notify=True)
        self.assertEqual(len(calls), 1)
        self.cursors.notify()
        self.assertEqual(len(calls), 2)

    def test_move_honours_snap(self):
        spec = self.cursors.add("v", 1e-4)
        self.cursors.redraw()
        self.cursors.snap_to_data = True
        self.cursors.move(spec.cid, 5.0001e-4)     # snap=None -> global flag
        x = np.asarray(self.ax.lines[0].get_xdata())
        self.assertIn(spec.position, x.tolist())

    def test_range_for_linear_and_log(self):
        spec = self.cursors.add("v", 1e-4)
        lo, hi, log = self.cursors.range_for(spec.cid)
        self.assertFalse(log)
        self.assertLess(lo, hi)
        self.ax.set_xscale("log")
        self.ax.set_xlim(10, 1e5)
        lo, hi, log = self.cursors.range_for(spec.cid)
        self.assertTrue(log)
        self.assertAlmostEqual(lo, 10.0)
        self.assertIsNone(self.cursors.range_for(4242))

    def test_range_for_horizontal_uses_y(self):
        self.ax.set_ylim(-2.0, 3.0)
        spec = self.cursors.add("h", 0.0)
        lo, hi, _ = self.cursors.range_for(spec.cid)
        self.assertAlmostEqual(lo, -2.0)
        self.assertAlmostEqual(hi, 3.0)


class TestTypography(Base):
    def test_fields_reach_the_artist(self):
        self.annotations.add(kind="text", x=5e-4, y=0.5, text="Hola",
                             fontfamily="monospace", fontweight="bold",
                             fontstyle="italic", ha="left", va="top")
        self.annotations.redraw()
        txt = [a for a in overlay_artists(self.fig, ANNOTATION_GID)
               if hasattr(a, "get_text")][0]
        self.assertEqual(txt.get_fontfamily(), ["monospace"])
        self.assertEqual(txt.get_fontweight(), "bold")
        self.assertEqual(txt.get_fontstyle(), "italic")
        self.assertEqual(txt.get_horizontalalignment(), "left")
        self.assertEqual(txt.get_verticalalignment(), "top")

    def test_defaults_preserve_previous_look(self):
        """Empty ha/va must keep each kind's historical alignment."""
        self.annotations.add(kind="vline", x=5e-4, text="ref")
        self.annotations.add(kind="text", x=5e-4, y=0.2, text="libre")
        self.annotations.redraw()
        texts = {a.get_text(): a for a in overlay_artists(self.fig, ANNOTATION_GID)
                 if hasattr(a, "get_text")}
        self.assertEqual(texts["ref"].get_verticalalignment(), "bottom")
        self.assertEqual(texts["libre"].get_verticalalignment(), "center")

    def test_every_kind_renders_with_typography(self):
        kinds = ["point", "arrow", "vline", "hline", "text", "vspan", "hspan"]
        for kind in kinds:
            self.annotations.add(kind=kind, x=2e-4, y=0.3, x2=6e-4, y2=0.7,
                                 text=kind, fontfamily="serif", ha="right",
                                 va="baseline")
        self.annotations.redraw()
        for spec in self.annotations.items:
            self.assertTrue(self.annotations._artists[spec.aid],
                            f"{spec.kind} rendered nothing")
        self.fig.canvas.draw()   # must not raise

    def test_legacy_json_without_new_fields(self):
        payload = {"annotations": [{"aid": 1, "kind": "text", "x": 1.0,
                                    "y": 2.0, "text": "viejo", "fontsize": 9.0}]}
        self.annotations.from_dict(payload)
        spec = self.annotations.items[0]
        self.assertEqual(spec.fontfamily, "")
        self.assertEqual(spec.ha, "")
        self.annotations.redraw()
        self.assertTrue(self.annotations._artists[spec.aid])

    def test_round_trip_json(self):
        self.populate()
        self.annotations.items[0].fontfamily = "monospace"
        self.annotations.items[0].va = "top"
        path = os.path.join(tempfile.mkdtemp(), "ov.json")
        save_overlays(path, self.cursors, self.annotations)
        c2 = CursorManager(self.canvas, max_cursors=None)
        a2 = AnnotationManager(self.canvas)
        load_overlays(path, c2, a2)
        self.assertEqual(len(c2.cursors), len(self.cursors.cursors))
        self.assertEqual(a2.items[0].fontfamily, "monospace")
        self.assertEqual(a2.items[0].va, "top")


class TestSliderMath(unittest.TestCase):
    """
    `gui.overlay_panel` cannot be imported here (no tkinter in this VM), so the
    two pure mapping helpers are extracted from the source and executed alone.
    """
    @classmethod
    def setUpClass(cls):
        import ast, textwrap
        path = os.path.expanduser("~/mnt/LabPlotter/gui/overlay_panel.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        panel = next(n for n in tree.body
                     if isinstance(n, ast.ClassDef) and n.name == "OverlayPanel")
        wanted = {"_slider_to_data", "_data_to_slider"}
        funcs = [n for n in panel.body
                 if isinstance(n, ast.FunctionDef) and n.name in wanted]
        assert len(funcs) == 2, "slider helpers not found"
        ns = {"math": math}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), path, "exec"), ns)
        # staticmethod: stored bare, attribute access would bind the TestCase
        # itself as the `self` of the extracted function.
        cls.to_data = staticmethod(ns["_slider_to_data"])
        cls.to_slider = staticmethod(ns["_data_to_slider"])

    class Panel:
        def __init__(self, rng):
            self._slider_range = rng

    def test_linear_mapping(self):
        p = self.Panel((0.0, 1e-3, False))
        self.assertAlmostEqual(self.to_data(p, 0.0), 0.0)
        self.assertAlmostEqual(self.to_data(p, 1.0), 1e-3)
        self.assertAlmostEqual(self.to_data(p, 0.5), 5e-4)

    def test_log_mapping_is_uniform_per_decade(self):
        p = self.Panel((10.0, 1e5, True))
        self.assertAlmostEqual(self.to_data(p, 0.0), 10.0)
        self.assertAlmostEqual(self.to_data(p, 1.0), 1e5, places=3)
        self.assertAlmostEqual(self.to_data(p, 0.5), 1e3, places=6)

    def test_round_trip_and_clamping(self):
        for rng in ((0.0, 1e-3, False), (10.0, 1e5, True), (-2.0, 3.0, False)):
            p = self.Panel(rng)
            for frac in (0.0, 0.137, 0.5, 0.999, 1.0):
                back = self.to_slider(p, self.to_data(p, frac))
                self.assertAlmostEqual(back, frac, places=6)
            self.assertEqual(self.to_slider(p, -1e9), 0.0)
            self.assertEqual(self.to_slider(p, 1e12), 1.0)

    def test_degenerate_ranges_do_not_raise(self):
        p = self.Panel((1.0, 1.0, False))
        self.assertEqual(self.to_slider(p, 1.0), 0.0)
        p = self.Panel((10.0, 1e5, True))
        self.assertEqual(self.to_slider(p, 0.0), 0.0)   # log of non-positive


if __name__ == "__main__":
    unittest.main(verbosity=2)
