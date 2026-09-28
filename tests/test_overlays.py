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
                          _perp_ticks, purge_overlay_artists, save_overlays,
                          load_overlays, format_eng)
from core.data_io import x_units_for_domain, y_units_for_kind
from core.export import _scale_pair, export_csv_individual, export_csv_combined
from core.i18n import t
from core.units import parse_eng


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
        kinds = ["point", "arrow", "line", "vline", "hline", "text", "vspan", "hspan"]
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
        self.annotations.add(kind="line", x=1e-4, y=-0.5, x2=8e-4, y2=0.8,
                             text="diag", label_parallel=True, label_free=True,
                             label_x=6e-4, label_y=0.9)
        path = os.path.join(tempfile.mkdtemp(), "ov.json")
        save_overlays(path, self.cursors, self.annotations)
        c2 = CursorManager(self.canvas, max_cursors=None)
        a2 = AnnotationManager(self.canvas)
        load_overlays(path, c2, a2)
        self.assertEqual(len(c2.cursors), len(self.cursors.cursors))
        self.assertEqual(a2.items[0].fontfamily, "monospace")
        self.assertEqual(a2.items[0].va, "top")
        line_spec = next(a for a in a2.items if a.kind == "line")
        self.assertTrue(line_spec.label_parallel)
        self.assertTrue(line_spec.label_free)
        self.assertAlmostEqual(line_spec.label_x, 6e-4)
        self.assertAlmostEqual(line_spec.label_y, 0.9)


class TestAngledLine(Base):
    """The new "line" kind (arbitrary-angle segment) and its label options."""

    def test_segment_endpoints(self):
        self.annotations.add(kind="line", x=1e-4, y=-0.5, x2=8e-4, y2=0.8)
        self.annotations.redraw()
        spec = self.annotations.items[0]
        seg = self.annotations._artists[spec.aid][0]
        xdata, ydata = seg.get_xdata(), seg.get_ydata()
        self.assertEqual((xdata[0], xdata[-1]), (1e-4, 8e-4))
        self.assertEqual((ydata[0], ydata[-1]), (-0.5, 0.8))

    def test_linestyle_is_configurable(self):
        self.annotations.add(kind="line", x=0, y=0, x2=1e-3, y2=1, linestyle=":")
        self.annotations.redraw()
        spec = self.annotations.items[0]
        seg = self.annotations._artists[spec.aid][0]
        self.assertEqual(seg.get_linestyle(), ":")

    def test_label_defaults_to_midpoint(self):
        self.annotations.add(kind="line", x=0.0, y=0.0, x2=1.0, y2=2.0, text="m")
        self.annotations.redraw()
        spec = self.annotations.items[0]
        label = self.annotations._artists[spec.aid][1]
        self.assertEqual(label.get_position(), (0.5, 1.0))

    def test_label_pos_moves_along_segment(self):
        self.annotations.add(kind="line", x=0.0, y=0.0, x2=1.0, y2=2.0, text="m",
                             label_pos=0.25)
        self.annotations.redraw()
        spec = self.annotations.items[0]
        label = self.annotations._artists[spec.aid][1]
        self.assertEqual(label.get_position(), (0.25, 0.5))

    def test_label_free_overrides_automatic_position(self):
        self.annotations.add(kind="line", x=0.0, y=0.0, x2=1.0, y2=2.0, text="m",
                             label_free=True, label_x=9.0, label_y=-3.0)
        self.annotations.redraw()
        spec = self.annotations.items[0]
        label = self.annotations._artists[spec.aid][1]
        self.assertEqual(label.get_position(), (9.0, -3.0))

    def test_label_parallel_follows_screen_angle_not_manual_rotation(self):
        # A perfectly horizontal segment on screen: the parallel angle must
        # be 0 deg regardless of the (deliberately wrong) manual `rotation`.
        self.annotations.add(kind="line", x=0.0, y=0.5, x2=1.0, y2=0.5,
                             text="h", label_parallel=True, rotation=45.0)
        self.annotations.redraw()
        spec = self.annotations.items[0]
        label = self.annotations._artists[spec.aid][1]
        self.assertAlmostEqual(label.get_rotation(), 0.0, places=3)

    def test_label_manual_rotation_used_when_not_parallel(self):
        self.annotations.add(kind="line", x=0.0, y=0.5, x2=1.0, y2=0.5,
                             text="h", label_parallel=False, rotation=45.0)
        self.annotations.redraw()
        spec = self.annotations.items[0]
        label = self.annotations._artists[spec.aid][1]
        self.assertAlmostEqual(label.get_rotation(), 45.0, places=3)

    def test_vline_label_free_uses_data_point_not_axes_fraction(self):
        self.annotations.add(kind="vline", x=5e-4, text="v",
                             label_free=True, label_x=7e-4, label_y=0.6)
        self.annotations.redraw()
        spec = self.annotations.items[0]
        label = self.annotations._artists[spec.aid][1]
        self.assertEqual(label.get_position(), (7e-4, 0.6))

    def test_hline_label_parallel_is_horizontal(self):
        self.annotations.add(kind="hline", y=0.3, text="h",
                             label_parallel=True, rotation=90.0)
        self.annotations.redraw()
        spec = self.annotations.items[0]
        label = self.annotations._artists[spec.aid][1]
        self.assertAlmostEqual(label.get_rotation(), 0.0, places=3)


class TestAxisMarks(Base):
    """`on_axis` custom-text tick marks for vline/hline annotations."""

    def _xtick_texts(self):
        ax = self.ax
        fmt = ax.xaxis.get_major_formatter()
        return {loc: fmt(loc) for loc in ax.xaxis.get_majorticklocs()}

    def _ytick_texts(self):
        ax = self.ax
        fmt = ax.yaxis.get_major_formatter()
        return {loc: fmt(loc) for loc in ax.yaxis.get_majorticklocs()}

    def test_on_axis_shows_custom_text_not_the_number(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             axis_text="$t_{s,5\\%}$")
        self.annotations.redraw()
        texts = self._xtick_texts()
        self.assertIn(5e-4, texts)
        self.assertEqual(texts[5e-4], "$t_{s,5\\%}$")

    def test_axis_text_falls_back_to_label_text(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True)
        self.annotations.redraw()
        self.assertEqual(self._xtick_texts()[5e-4], "f0")

    def test_no_text_at_all_does_not_mark_axis(self):
        self.annotations.add(kind="vline", x=5e-4, on_axis=True)
        self.annotations.redraw()
        self.assertNotIn(5e-4, self.ax.xaxis.get_majorticklocs())

    def test_on_axis_off_leaves_formatter_untouched(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=False)
        self.annotations.redraw()
        base_formatter = self.ax.xaxis.get_major_formatter()
        self.annotations.redraw()
        self.assertIs(self.ax.xaxis.get_major_formatter(), base_formatter)

    def test_hline_marks_y_axis(self):
        self.annotations.add(kind="hline", y=0.42, text="Vout", on_axis=True)
        self.annotations.redraw()
        texts = self._ytick_texts()
        self.assertIn(0.42, texts)
        self.assertEqual(texts[0.42], "Vout")

    def test_marked_tick_label_is_bold_by_default(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True)
        self.annotations.redraw()
        ax = self.ax
        for tick, loc in zip(ax.xaxis.get_major_ticks(), ax.xaxis.get_majorticklocs()):
            if math.isclose(loc, 5e-4, rel_tol=1e-9, abs_tol=1e-12):
                self.assertEqual(tick.label1.get_fontweight(), "bold")
                return
        self.fail("marked tick not found")

    def test_font_controls_apply_to_axis_label(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             axis_fontweight="normal", axis_fontstyle="italic")
        self.annotations.redraw()
        ax = self.ax
        for tick, loc in zip(ax.xaxis.get_major_ticks(), ax.xaxis.get_majorticklocs()):
            if math.isclose(loc, 5e-4, rel_tol=1e-9, abs_tol=1e-12):
                self.assertEqual(tick.label1.get_fontweight(), "normal")
                self.assertEqual(tick.label1.get_fontstyle(), "italic")
                return
        self.fail("marked tick not found")

    def test_removing_mark_resets_tick_style(self):
        spec = self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True)
        self.annotations.redraw()
        self.annotations.update(spec.aid, on_axis=False)
        self.annotations.redraw()
        for tick in self.ax.xaxis.get_major_ticks():
            self.assertEqual(tick.label1.get_fontweight(), "normal")

    def test_two_marks_do_not_clobber_each_other(self):
        self.annotations.add(kind="vline", x=2e-4, text="a", on_axis=True,
                             axis_text="A")
        self.annotations.add(kind="vline", x=7e-4, text="b", on_axis=True,
                             axis_text="B")
        self.annotations.redraw()
        texts = self._xtick_texts()
        self.assertEqual(texts[2e-4], "A")
        self.assertEqual(texts[7e-4], "B")

    def test_invisible_annotation_does_not_mark_axis(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             visible=False)
        self.annotations.redraw()
        self.assertNotIn(5e-4, self.ax.xaxis.get_majorticklocs())

    def test_survives_live_axes_refresh(self):
        """Same shape as App._refresh_overlays: attach()+redraw() on the same
        (not fig.clear()-ed) axes must not nest formatter/locator wrappers."""
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True)
        self.annotations.redraw()
        self.refresh_overlays()
        self.refresh_overlays()
        self.assertEqual(self._xtick_texts()[5e-4], "f0")


class TestAxisSide(Base):
    """`axis_side` -- routing a mark to the opposite spine (top for vline,
    right for hline) via a lazily created secondary axis, instead of the
    plot's own bottom/left tick scale."""

    def test_default_side_is_primary_bottom(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True)
        self.annotations.redraw()
        fmt = self.ax.xaxis.get_major_formatter()
        self.assertEqual(fmt(5e-4), "f0")
        self.assertIsNone(getattr(self.ax, "_lp_secondary_top", None))

    def test_top_side_does_not_touch_primary_xaxis(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             axis_side="top")
        self.annotations.redraw()
        base_formatter = self.ax.xaxis.get_major_formatter()
        self.annotations.redraw()
        self.assertIs(self.ax.xaxis.get_major_formatter(), base_formatter)
        self.assertNotIn(5e-4, self.ax.xaxis.get_majorticklocs())

    def test_top_side_creates_secondary_axis_lazily(self):
        self.assertIsNone(getattr(self.ax, "_lp_secondary_top", None))
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             axis_side="top")
        self.annotations.redraw()
        secondary = getattr(self.ax, "_lp_secondary_top", None)
        self.assertIsNotNone(secondary)
        fmt = secondary.xaxis.get_major_formatter()
        self.assertIn(5e-4, secondary.xaxis.get_majorticklocs())
        self.assertEqual(fmt(5e-4), "f0")

    def test_top_secondary_axis_shows_only_marks_not_full_scale(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             axis_side="top")
        self.annotations.redraw()
        secondary = self.ax._lp_secondary_top
        self.assertEqual(list(secondary.xaxis.get_majorticklocs()), [5e-4])

    def test_no_top_marks_never_creates_secondary_axis(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=False)
        self.annotations.redraw()
        self.assertIsNone(getattr(self.ax, "_lp_secondary_top", None))

    def test_right_side_marks_secondary_y_axis(self):
        self.annotations.add(kind="hline", y=0.42, text="Vout", on_axis=True,
                             axis_side="right")
        self.annotations.redraw()
        secondary = getattr(self.ax, "_lp_secondary_right", None)
        self.assertIsNotNone(secondary)
        fmt = secondary.yaxis.get_major_formatter()
        self.assertIn(0.42, secondary.yaxis.get_majorticklocs())
        self.assertEqual(fmt(0.42), "Vout")
        self.assertNotIn(0.42, self.ax.yaxis.get_majorticklocs())

    def test_uses_opposite_axis(self):
        self.assertFalse(self.annotations.uses_opposite_axis())
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True)
        self.assertFalse(self.annotations.uses_opposite_axis())
        spec = self.annotations.add(kind="hline", y=0.4, text="v", on_axis=True,
                                    axis_side="right")
        self.assertTrue(self.annotations.uses_opposite_axis())
        self.annotations.update(spec.aid, on_axis=False)
        self.assertFalse(self.annotations.uses_opposite_axis())

    def test_removing_top_mark_clears_it_without_destroying_axis(self):
        spec = self.annotations.add(kind="vline", x=5e-4, text="f0",
                                    on_axis=True, axis_side="top")
        self.annotations.redraw()
        secondary_before = self.ax._lp_secondary_top
        self.annotations.update(spec.aid, on_axis=False)
        self.annotations.redraw()
        secondary_after = getattr(self.ax, "_lp_secondary_top", None)
        self.assertIs(secondary_after, secondary_before)
        self.assertEqual(list(secondary_after.xaxis.get_majorticklocs()), [])

    def test_font_controls_apply_on_secondary_axis(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             axis_side="top", axis_fontweight="normal",
                             axis_fontstyle="italic")
        self.annotations.redraw()
        secondary = self.ax._lp_secondary_top
        tick = secondary.xaxis.get_major_ticks()[0]
        self.assertEqual(tick.label1.get_fontweight(), "normal")
        self.assertEqual(tick.label1.get_fontstyle(), "italic")

    def test_two_top_marks_do_not_clobber_each_other(self):
        self.annotations.add(kind="vline", x=2e-4, text="a", on_axis=True,
                             axis_side="top", axis_text="A")
        self.annotations.add(kind="vline", x=7e-4, text="b", on_axis=True,
                             axis_side="top", axis_text="B")
        self.annotations.redraw()
        secondary = self.ax._lp_secondary_top
        fmt = secondary.xaxis.get_major_formatter()
        self.assertEqual(fmt(2e-4), "A")
        self.assertEqual(fmt(7e-4), "B")

    def test_invalid_axis_side_falls_back_to_primary(self):
        # "left" is not a valid side for a vline -- must not silently
        # misroute to a nonexistent secondary axis.
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             axis_side="left")
        self.annotations.redraw()
        self.assertEqual(self.ax.xaxis.get_major_formatter()(5e-4), "f0")
        self.assertIsNone(getattr(self.ax, "_lp_secondary_top", None))

    def test_json_round_trip_persists_axis_side(self):
        self.annotations.add(kind="hline", y=0.3, text="v", on_axis=True,
                             axis_side="right")
        path = tempfile.mktemp(suffix=".json")
        save_overlays(path, self.cursors, self.annotations)
        annotations2 = AnnotationManager(self.canvas)
        annotations2.attach([self.ax])
        load_overlays(path, CursorManager(self.canvas, max_cursors=None), annotations2)
        self.assertEqual(annotations2.items[0].axis_side, "right")
        os.remove(path)

    def test_legacy_json_without_axis_side_defaults_empty(self):
        payload = {"version": 1, "cursors": {"cursors": []},
                  "annotations": {"annotations": [
                      {"aid": 1, "kind": "vline", "x": 0.3, "text": "legacy",
                       "on_axis": True}]}}
        path = tempfile.mktemp(suffix=".json")
        with open(path, "w") as f:
            json.dump(payload, f)
        annotations2 = AnnotationManager(self.canvas)
        annotations2.attach([self.ax])
        load_overlays(path, CursorManager(self.canvas, max_cursors=None), annotations2)
        self.assertEqual(annotations2.items[0].axis_side, "")
        os.remove(path)


class TestAxisFontSize(Base):
    """`axis_fontsize` -- independent size for an on_axis mark's tick label."""

    def _marked_label(self, x=5e-4):
        ax = self.ax
        for tick, loc in zip(ax.xaxis.get_major_ticks(), ax.xaxis.get_majorticklocs()):
            if math.isclose(loc, x, rel_tol=1e-9, abs_tol=1e-12):
                return tick.label1
        self.fail("marked tick not found")

    def test_explicit_size_applies(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             axis_fontsize=14.0)
        self.annotations.redraw()
        self.assertEqual(self._marked_label().get_fontsize(), 14.0)

    def test_zero_inherits_default_tick_size(self):
        default_size = self.ax.xaxis.get_major_ticks()[0].label1.get_fontsize()
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             axis_fontsize=0.0)
        self.annotations.redraw()
        self.assertEqual(self._marked_label().get_fontsize(), default_size)

    def test_removing_mark_resets_font_size(self):
        default_size = self.ax.xaxis.get_major_ticks()[0].label1.get_fontsize()
        spec = self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                                    axis_fontsize=20.0)
        self.annotations.redraw()
        self.annotations.update(spec.aid, on_axis=False)
        self.annotations.redraw()
        for tick in self.ax.xaxis.get_major_ticks():
            self.assertEqual(tick.label1.get_fontsize(), default_size)

    def test_round_trip_json(self):
        self.annotations.add(kind="vline", x=5e-4, text="f0", on_axis=True,
                             axis_fontsize=11.5)
        path = os.path.join(tempfile.mkdtemp(), "ov.json")
        save_overlays(path, self.cursors, self.annotations)
        a2 = AnnotationManager(self.canvas)
        load_overlays(path, CursorManager(self.canvas, max_cursors=None), a2)
        self.assertAlmostEqual(a2.items[0].axis_fontsize, 11.5)
        os.remove(path)

    def test_legacy_json_without_axis_fontsize(self):
        payload = {"annotations": [{"aid": 1, "kind": "vline", "x": 5e-4,
                                    "text": "f0", "on_axis": True}]}
        self.annotations.from_dict(payload)
        self.assertEqual(self.annotations.items[0].axis_fontsize, 0.0)
        self.annotations.redraw()   # must not raise with the legacy default


class TestAxisTickDeclutter(Base):
    """
    `_declutter_auto_ticks` -- the automatic tick(s) an on_axis mark would
    visually collide with get dropped from the axis' own scale, so the mark
    doesn't render glued to a neighbouring tick label (reported directly
    against a rendered demo -- see Claude.md, Fase 9's Pendientes closing
    the gap noted in Fase 7).
    """

    def test_unit_drops_only_the_colliding_tick(self):
        # Direct call, full control over inputs -- no dependency on this
        # fixture's actual figure size/DPI producing any particular pixel
        # gap between ticks.
        self.ax.set_xlim(0, 10)
        auto = [0.0, 1.0, 5.0, 9.0, 10.0]
        marks = {1.0 + 1e-6: None}   # effectively on top of the tick at 1.0
        kept = self.annotations._declutter_auto_ticks(self.ax, "x", auto, marks)
        self.assertNotIn(1.0, kept)
        self.assertEqual(kept, [0.0, 5.0, 9.0, 10.0])

    def test_unit_keeps_far_ticks(self):
        self.ax.set_xlim(0, 10)
        auto = [0.0, 5.0, 10.0]
        marks = {5.0: None}
        kept = self.annotations._declutter_auto_ticks(self.ax, "x", auto, marks)
        self.assertEqual(kept, [0.0, 10.0])

    def test_unit_y_axis_uses_the_y_pixel_component(self):
        self.ax.set_ylim(0, 1)
        auto = [0.0, 0.5, 1.0]
        marks = {0.5 + 1e-9: None}
        kept = self.annotations._declutter_auto_ticks(self.ax, "y", auto, marks)
        self.assertNotIn(0.5, kept)
        self.assertEqual(kept, [0.0, 1.0])

    def test_pixel_distance_not_data_distance_on_log_axis(self):
        """
        The whole point of measuring in pixels: the SAME 1-unit data delta
        is a collision near the compressed (large) end of a log axis, and
        NOT a collision near the expanded (small) end -- a fixed data-space
        cutoff would get exactly one of these two cases wrong.
        """
        self.ax.set_xscale("log")
        self.ax.set_xlim(1, 1000)
        # Large end: 999 and 1000 are 0.0004 decades apart -- a hair's
        # width on screen regardless of figure size.
        near_large = self.annotations._declutter_auto_ticks(
            self.ax, "x", [999.0], {1000.0: None})
        self.assertEqual(near_large, [],
                         "999 should collide with a mark at 1000 on a log axis")
        # Small end: 1 and 2 are a third of a decade apart -- many tens of
        # pixels on any figure wide enough to plot a 3-decade log axis at all.
        near_small = self.annotations._declutter_auto_ticks(
            self.ax, "x", [1.0], {2.0: None})
        self.assertEqual(near_small, [1.0],
                         "1 should NOT collide with a mark at 2 on a log axis")

    def test_integration_mark_on_existing_tick_removes_the_duplicate(self):
        """Through the real add()+redraw() path, not the helper directly."""
        auto = sorted(self.ax.xaxis.get_majorticklocs())
        self.assertTrue(auto)
        target = auto[len(auto) // 2]
        self.annotations.add(kind="vline", x=target, text="mark", on_axis=True)
        self.annotations.redraw()
        combined = self.ax.xaxis.get_majorticklocs()
        close = [loc for loc in combined
                if math.isclose(loc, target, rel_tol=1e-9, abs_tol=1e-12)]
        self.assertEqual(len(close), 1)

    def test_manual_xticks_and_mark_declutter_together(self):
        """
        The general "manual X ticks" axis-cosmetics feature (gui.app, not
        tested here directly -- no tkinter in this VM) feeds its fixed
        positions through the SAME `ax.xaxis.get_majorticklocs()` that
        `_mark_axis` reads its "automatic" ticks from -- so a manual tick
        that collides with a mark gets decluttered exactly like a real
        automatic one would, with no special-casing needed.
        """
        self.ax.set_xticks([0.0, 30.0, 60.0, 80.0, 120.0])
        self.annotations.add(kind="vline", x=61.0, text="pico", on_axis=True)
        self.annotations.redraw()
        combined = self.ax.xaxis.get_majorticklocs()
        self.assertNotIn(60.0, combined)
        self.assertIn(61.0, combined)
        for v in (0.0, 30.0, 80.0, 120.0):
            self.assertIn(v, combined)


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


class TestValueDecimals(Base):
    """
    Fase 11: configurable decimal count for the value shown in a cursor tag
    and in the text «Anotar valor» promotes to a permanent axis mark --
    both come from the exact same `format_eng(..., decimals=...)` call (see
    Claude.md), so exercising `format_eng` directly plus the cursor tag
    artist covers the promoted-annotation case too (its own text-building
    line in `overlay_panel._promote_cursor` cannot be imported here, no
    tkinter in this VM, but it is a one-line call to the same function).
    """

    def test_format_eng_fixed_decimals_regardless_of_magnitude(self):
        # The bug this replaces: `%.4g` gives a decimal count that drifts
        # with magnitude (1 for "999.0", 3 for "1.500"). Fixed `decimals`
        # must not drift.
        self.assertEqual(format_eng(1.5, digits=4), "1.5")            # old behaviour, untouched
        self.assertEqual(format_eng(1.5, decimals=2), "1.50")
        self.assertEqual(format_eng(999.0, decimals=2), "999.00")
        self.assertEqual(format_eng(0.0, decimals=2), "0.00")
        self.assertEqual(format_eng(15.234e-6, unit="s", decimals=2), "15.23 us")

    def test_format_eng_decimals_zero(self):
        self.assertEqual(format_eng(15.678e-6, unit="s", decimals=0), "16 us")

    def test_format_eng_mathtext_prefix_with_decimals(self):
        text = format_eng(15.234e-6, unit="s", decimals=1, mathtext=True)
        self.assertEqual(text, r"15.2 $\mu$s")

    def test_cursor_manager_default_decimals(self):
        self.assertEqual(self.cursors.value_decimals, 2)

    def test_cursor_tag_text_uses_value_decimals(self):
        self.cursors.axis_units = [("s", "")]
        spec = self.cursors.add("v", 15.234e-6)
        self.cursors.redraw()
        tag = self._tag_text(spec.cid)
        self.assertIn("15.23", tag)
        self.cursors.value_decimals = 4
        self.cursors._update_artists(spec)
        tag = self._tag_text(spec.cid)
        self.assertIn("15.2340", tag)

    def _tag_text(self, cid):
        artists = self.cursors._artists[cid]
        self.assertEqual(len(artists), 2, "expected line + tag with show_tags/tag_with_value on")
        return artists[1].get_text()

    def test_round_trip_json_includes_value_decimals(self):
        self.cursors.value_decimals = 3
        payload = self.cursors.to_dict()
        self.assertEqual(payload["value_decimals"], 3)
        restored = CursorManager(self.canvas, max_cursors=None)
        restored.from_dict(payload)
        self.assertEqual(restored.value_decimals, 3)

    def test_legacy_payload_without_field_keeps_default(self):
        restored = CursorManager(self.canvas, max_cursors=None)
        restored.from_dict({"snap_to_data": True})
        self.assertEqual(restored.value_decimals, 2)


class TestAxisFactorOverride(unittest.TestCase):
    """
    Fase 11: "Factor X"/"Factor Y1"/"Factor Y2" -- a manual divisor for the
    WHOLE axis, on top of (and independent from) the existing "Unidad ...
    manual" free-typed unit label. `App._x_factor`/`_y_factor` cannot be
    imported here (no tkinter), so they're AST-extracted the same way
    `TestSliderMath` extracts the slider helpers -- both are pure functions
    of their arguments, no `self` state involved.
    """

    @classmethod
    def setUpClass(cls):
        import ast
        path = os.path.expanduser("~/mnt/LabPlotter/gui/app.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        app = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "App")
        wanted = {"_x_factor", "_y_factor"}
        funcs = [n for n in app.body
                if isinstance(n, ast.FunctionDef) and n.name in wanted]
        assert len(funcs) == 2, "axis-factor helpers not found"
        ns = {"x_units_for_domain": x_units_for_domain,
              "y_units_for_kind": y_units_for_kind}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), path, "exec"), ns)
        # staticmethod: stored bare, same reason as `TestSliderMath` -- plain
        # attribute access would otherwise bind `self` (the TestCase) as the
        # extracted function's own `self` (unused, but shifts every arg by one).
        cls.x_factor = staticmethod(ns["_x_factor"])
        cls.y_factor = staticmethod(ns["_y_factor"])

    def test_x_factor_falls_back_to_known_unit(self):
        settings = {"domain": "time", "x_unit": "us", "x_factor_override": None}
        self.assertAlmostEqual(self.x_factor(None, settings), 1e-6)

    def test_x_factor_override_wins_over_unit_lookup(self):
        settings = {"domain": "time", "x_unit": "banana",  # unknown unit -> would be 1.0
                    "x_factor_override": 2.2e3}
        self.assertAlmostEqual(self.x_factor(None, settings), 2.2e3)

    def test_x_factor_zero_or_missing_override_is_ignored_by_caller(self):
        # `_parse_factor` (gui.app) is what turns 0/empty into `None` before
        # it ever reaches here; this just documents that `_x_factor` itself
        # treats a falsy override as "no override".
        settings = {"domain": "time", "x_unit": "ms", "x_factor_override": 0.0}
        self.assertAlmostEqual(self.x_factor(None, settings), 1e-3)

    def test_y_factor_override_applies_to_voltage(self):
        settings = {"y_factor_override": 5.0, "y2_factor_override": None}
        self.assertAlmostEqual(self.y_factor(None, settings, "voltage", "V"), 5.0)

    def test_y_factor_override_skipped_for_db_and_deg(self):
        settings = {"y_factor_override": 5.0, "y2_factor_override": None}
        self.assertAlmostEqual(
            self.y_factor(None, settings, "dB", "dB"),
            y_units_for_kind("dB").get("dB", 1.0))
        self.assertAlmostEqual(
            self.y_factor(None, settings, "deg", "deg"),
            y_units_for_kind("deg").get("deg", 1.0))

    def test_y_factor_secondary_uses_y2_override(self):
        settings = {"y_factor_override": 5.0, "y2_factor_override": 9.0}
        self.assertAlmostEqual(
            self.y_factor(None, settings, "voltage", "V", secondary=True), 9.0)
        self.assertAlmostEqual(
            self.y_factor(None, settings, "voltage", "V", secondary=False), 5.0)


class TestExportFactorOverride(unittest.TestCase):
    """Fase 11's manual axis factor, on the CSV-export side (`core.export`)."""

    def test_scale_pair_uses_override_over_unit_lookup(self):
        x = np.array([1e-6, 2e-6])
        y = np.array([1.0, 2.0])
        x_disp, y_disp = _scale_pair(x, y, "time", "voltage", "banana", "V",
                                     x_factor=1e-3, y_factor=2.0)
        np.testing.assert_allclose(x_disp, x / 1e-3)
        np.testing.assert_allclose(y_disp, y / 2.0)

    def test_scale_pair_without_override_matches_old_behaviour(self):
        x = np.array([1e-6])
        y = np.array([1.0])
        x_disp, y_disp = _scale_pair(x, y, "time", "voltage", "us", "V")
        np.testing.assert_allclose(x_disp, x / 1e-6)
        np.testing.assert_allclose(y_disp, y / 1.0)

    def test_scale_pair_y_factor_skipped_for_db(self):
        x = np.array([1.0])
        y = np.array([3.0])
        _x_disp, y_disp = _scale_pair(x, y, "freq", "dB", "Hz", "dB",
                                      x_factor=None, y_factor=10.0)
        np.testing.assert_allclose(y_disp, y / y_units_for_kind("dB").get("dB", 1.0))

    def test_export_csv_individual_honors_factor_override(self):
        with tempfile.TemporaryDirectory() as out_dir:
            payload = [("sig1", np.array([1e-6, 2e-6]), np.array([1.0, 2.0]),
                       "time", "voltage")]
            paths = export_csv_individual(payload, out_dir, "banana", "V",
                                          x_factor=1e-3, y_factor=1.0)
            data = np.genfromtxt(paths[0], delimiter=",", skip_header=1)
            np.testing.assert_allclose(data[:, 0], np.array([1e-6, 2e-6]) / 1e-3)

    def test_export_csv_combined_honors_factor_override(self):
        payload = [
            ("sig1", np.array([0.0, 1.0, 2.0]), np.array([0.0, 1.0, 2.0]),
             "time", "voltage"),
            ("sig2", np.array([0.0, 1.0, 2.0]), np.array([0.0, 2.0, 4.0]),
             "time", "voltage"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "combined.csv")
            export_csv_combined(payload, out_path, "banana", "V", n_points=3,
                                x_factor=2.0, y_factor=4.0)
            data = np.genfromtxt(out_path, delimiter=",", skip_header=1)
            np.testing.assert_allclose(data[:, 0], np.array([0.0, 1.0, 2.0]) / 2.0)


class TestTickDecimals(unittest.TestCase):
    """
    "Decimales X/Y manuales" (`gui.app._apply_axis_cosmetics`) -- a fixed
    decimal count on a LINEAR axis' automatic ticks, reported directly
    against a screenshot where Y showed "1.500/1.000/0.500..." (3 decimals,
    `ScalarFormatter`'s own choice for that axis' range) next to X showing
    "0.0/20.0/40.0..." (1 decimal, `ScalarFormatter`'s choice for ITS
    range) -- the mismatch itself was the complaint. `gui.app` cannot be
    imported here (no tkinter in this VM), so the two pure module-level
    functions are AST-extracted directly, same technique as
    `TestSliderMath`/`TestAxisFactorOverride`.
    """

    @classmethod
    def setUpClass(cls):
        import ast
        path = os.path.expanduser("~/mnt/LabPlotter/gui/app.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        wanted = {"_parse_tick_decimals", "_fixed_decimals_tick_label"}
        funcs = [n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name in wanted]
        assert len(funcs) == 2, "tick-decimals helpers not found"
        from typing import Optional
        ns = {"parse_eng": parse_eng, "Optional": Optional}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), path, "exec"), ns)
        cls.parse_decimals = staticmethod(ns["_parse_tick_decimals"])
        cls.fixed_label = staticmethod(ns["_fixed_decimals_tick_label"])

    def test_parse_valid_values(self):
        self.assertEqual(self.parse_decimals("2"), 2)
        self.assertEqual(self.parse_decimals("0"), 0)
        self.assertEqual(self.parse_decimals("6"), 6)
        self.assertEqual(self.parse_decimals("2.0"), 2)   # parse_eng, then rounded

    def test_parse_rejects_out_of_range_and_junk(self):
        self.assertIsNone(self.parse_decimals(""))
        self.assertIsNone(self.parse_decimals("banana"))
        self.assertIsNone(self.parse_decimals("-1"))
        self.assertIsNone(self.parse_decimals("7"))

    def test_fixed_label_matches_requested_decimals_regardless_of_axis_range(self):
        # The bug this replaces: `ScalarFormatter` picks a DIFFERENT decimal
        # count per axis depending on that axis' own data range. A fixed
        # count must not vary with magnitude at all.
        self.assertEqual(self.fixed_label(1.5, 1), "1.5")
        self.assertEqual(self.fixed_label(20.0, 1), "20.0")
        self.assertEqual(self.fixed_label(1.5, 3), "1.500")
        self.assertEqual(self.fixed_label(0.5, 0), "0")

    def test_fixed_label_no_spurious_negative_zero(self):
        self.assertEqual(self.fixed_label(-0.00001, 2), "0.00")
        self.assertEqual(self.fixed_label(-1.5, 1), "-1.5")


class TestDimensionAnnotation(Base):
    """
    "Cota" (`kind="dimension"`): a technical dimension line -- a segment
    capped with a short perpendicular tick at each end -- distinct from
    "arrow" (an arrowhead, no caps) and "line" (a bare segment, no caps).
    `dim_orientation` projects the two captured points before drawing; the
    measured distance itself is never computed here (see the renderer's own
    docstring) -- `text` already carries whatever `OverlayPanel` put there,
    so these tests only cover geometry, never unit formatting.
    """

    def test_auto_orientation_keeps_arbitrary_segment(self):
        self.annotations.add(kind="dimension", x=1e-4, y=-0.5, x2=8e-4, y2=0.8,
                             dim_orientation="auto")
        self.annotations.redraw()
        spec = self.annotations.items[0]
        seg = self.annotations._artists[spec.aid][0]
        self.assertEqual(tuple(seg.get_xdata()), (1e-4, 8e-4))
        self.assertEqual(tuple(seg.get_ydata()), (-0.5, 0.8))

    def test_horizontal_orientation_flattens_y2_to_y1(self):
        self.annotations.add(kind="dimension", x=1e-4, y=0.3, x2=8e-4, y2=0.9,
                             dim_orientation="horizontal")
        self.annotations.redraw()
        spec = self.annotations.items[0]
        seg = self.annotations._artists[spec.aid][0]
        self.assertEqual(tuple(seg.get_ydata()), (0.3, 0.3))

    def test_vertical_orientation_flattens_x2_to_x1(self):
        self.annotations.add(kind="dimension", x=2e-4, y=-0.2, x2=6e-4, y2=0.7,
                             dim_orientation="vertical")
        self.annotations.redraw()
        spec = self.annotations.items[0]
        seg = self.annotations._artists[spec.aid][0]
        self.assertEqual(tuple(seg.get_xdata()), (2e-4, 2e-4))

    def test_renders_segment_plus_two_end_ticks_with_no_text(self):
        self.annotations.add(kind="dimension", x=1e-4, y=0.0, x2=5e-4, y2=0.0)
        self.annotations.redraw()
        spec = self.annotations.items[0]
        artists = self.annotations._artists[spec.aid]
        self.assertEqual(len(artists), 3)   # seg + tick + tick, no label

    def test_text_adds_a_label_at_the_midpoint_by_default(self):
        self.annotations.add(kind="dimension", x=0.0, y=0.0, x2=1.0, y2=0.0,
                             text="1.0 ms")
        self.annotations.redraw()
        spec = self.annotations.items[0]
        artists = self.annotations._artists[spec.aid]
        self.assertEqual(len(artists), 4)
        self.assertEqual(artists[3].get_position(), (0.5, 0.0))

    def test_label_pos_moves_the_text_along_the_segment(self):
        self.annotations.add(kind="dimension", x=0.0, y=0.0, x2=1.0, y2=2.0,
                             text="d", label_pos=0.25)
        self.annotations.redraw()
        spec = self.annotations.items[0]
        label = self.annotations._artists[spec.aid][3]
        self.assertEqual(label.get_position(), (0.25, 0.5))

    def test_label_free_overrides_the_midpoint(self):
        self.annotations.add(kind="dimension", x=0.0, y=0.0, x2=1.0, y2=0.0,
                             text="d", label_free=True, label_x=2.0, label_y=3.0)
        self.annotations.redraw()
        spec = self.annotations.items[0]
        label = self.annotations._artists[spec.aid][3]
        self.assertEqual(label.get_position(), (2.0, 3.0))

    def test_round_trip_json_includes_dimension_fields(self):
        self.annotations.add(kind="dimension", x=0.0, y=0.0, x2=1.0, y2=1.0,
                             dim_orientation="horizontal", dim_auto_text=False)
        payload = self.annotations.to_dict()
        restored = AnnotationManager(self.canvas)
        restored.from_dict(payload)
        spec = restored.items[0]
        self.assertEqual(spec.dim_orientation, "horizontal")
        self.assertFalse(spec.dim_auto_text)

    def test_legacy_json_without_dimension_fields_uses_defaults(self):
        payload = {"annotations": [{"aid": 1, "kind": "dimension", "x": 0.0,
                                    "y": 0.0, "x2": 1.0, "y2": 1.0}]}
        self.annotations.from_dict(payload)
        spec = self.annotations.items[0]
        self.assertEqual(spec.dim_orientation, "auto")
        self.assertTrue(spec.dim_auto_text)
        self.annotations.redraw()
        self.assertTrue(self.annotations._artists[spec.aid])


class TestPerpTicks(unittest.TestCase):
    """
    `_perp_ticks`: the two end-caps of a "cota", computed in PIXEL space
    (`ax.transData`) rather than from the raw data slope -- same technique
    `_screen_angle` already uses, and for the same reason: X and Y rarely
    share a scale in this app, so a perpendicular computed from data
    coordinates would not look perpendicular on screen. Mirrors
    `TestAxisTickDeclutter`'s own pixel-vs-data emphasis.
    """

    def setUp(self):
        self.fig = Figure(figsize=(6, 4))
        self.ax = self.fig.add_subplot(111)

    def _pixel_vec(self, p1, p2):
        t = self.ax.transData
        a = t.transform(p1)
        b = t.transform(p2)
        return b[0] - a[0], b[1] - a[1]

    def test_ticks_are_perpendicular_in_pixel_space(self):
        a1, a2, b1, b2 = _perp_ticks(self.ax, 0.0, 0.0, 1.0, 1.0)
        seg_vec = self._pixel_vec((0.0, 0.0), (1.0, 1.0))
        tick_vec = self._pixel_vec(a1, a2)
        dot = seg_vec[0] * tick_vec[0] + seg_vec[1] * tick_vec[1]
        self.assertAlmostEqual(dot, 0.0, places=6)
        tick_vec_b = self._pixel_vec(b1, b2)
        dot_b = seg_vec[0] * tick_vec_b[0] + seg_vec[1] * tick_vec_b[1]
        self.assertAlmostEqual(dot_b, 0.0, places=6)

    def test_tick_length_matches_tick_pt_in_pixels(self):
        dpi = self.fig.dpi
        a1, a2, _b1, _b2 = _perp_ticks(self.ax, 0.0, 0.0, 1.0, 0.0, tick_pt=6.0)
        px_a1 = self.ax.transData.transform(a1)
        px_a2 = self.ax.transData.transform(a2)
        length = math.hypot(px_a2[0] - px_a1[0], px_a2[1] - px_a1[1])
        self.assertAlmostEqual(length, 6.0 * dpi / 72.0, places=3)

    def test_ticks_are_at_the_two_endpoints(self):
        a1, a2, b1, b2 = _perp_ticks(self.ax, 0.0, 0.0, 1.0, 0.0)
        mid_a = ((a1[0] + a2[0]) / 2.0, (a1[1] + a2[1]) / 2.0)
        mid_b = ((b1[0] + b2[0]) / 2.0, (b1[1] + b2[1]) / 2.0)
        self.assertAlmostEqual(mid_a[0], 0.0, places=6)
        self.assertAlmostEqual(mid_a[1], 0.0, places=6)
        self.assertAlmostEqual(mid_b[0], 1.0, places=6)
        self.assertAlmostEqual(mid_b[1], 0.0, places=6)

    def test_log_axis_perpendicularity_holds_in_pixels_not_data(self):
        # The whole reason this is computed in pixel space: on a log X axis
        # the SAME 1-unit-in-data-space perpendicular offset means wildly
        # different things depending on where it sits, so a naive
        # data-space perpendicular would look skewed on screen. In pixel
        # space the tick must stay exactly perpendicular regardless.
        self.ax.set_xscale("log")
        self.ax.set_xlim(1, 1000)
        a1, a2, b1, b2 = _perp_ticks(self.ax, 10.0, 0.0, 100.0, 1.0)
        seg_vec = self._pixel_vec((10.0, 0.0), (100.0, 1.0))
        tick_vec = self._pixel_vec(a1, a2)
        dot = seg_vec[0] * tick_vec[0] + seg_vec[1] * tick_vec[1]
        self.assertAlmostEqual(dot, 0.0, places=5)


class TestCursorAxisUnits(Base):
    """
    `CursorManager.axis_units`: one (x_unit, y_unit) pair per axes index,
    replacing the old flat `x_unit`/`y_unit` pair that silently mislabeled
    a cursor on a secondary/Y2 axis with the primary axis's unit. Populated
    by `App.update_plot()` every redraw (`App._axes_context`) -- never
    persisted, since it is derived from the CURRENT plot mode, not from
    anything the user actually set on a cursor.
    """

    def test_axis_unit_defaults_to_empty_before_any_redraw(self):
        self.assertEqual(self.cursors.axis_unit(0, "v"), "")
        self.assertEqual(self.cursors.axis_unit(0, "h"), "")

    def test_axis_unit_looks_up_the_right_index_and_orientation(self):
        self.cursors.axis_units = [("s", "V"), ("s", "deg")]
        self.assertEqual(self.cursors.axis_unit(0, "v"), "s")
        self.assertEqual(self.cursors.axis_unit(0, "h"), "V")
        self.assertEqual(self.cursors.axis_unit(1, "v"), "s")
        self.assertEqual(self.cursors.axis_unit(1, "h"), "deg")

    def test_axis_unit_out_of_range_falls_back_to_empty(self):
        self.cursors.axis_units = [("s", "V")]
        self.assertEqual(self.cursors.axis_unit(5, "v"), "")

    def test_axis_units_is_not_persisted(self):
        # Runtime-only, rebuilt every redraw from the current plot mode --
        # saving/loading an overlay file must never freeze it to whatever
        # happened to be on screen when the file was written.
        self.cursors.axis_units = [("s", "V")]
        payload = self.cursors.to_dict()
        self.assertNotIn("axis_units", payload)
        self.assertNotIn("x_unit", payload)
        self.assertNotIn("y_unit", payload)
        restored = CursorManager(self.canvas, max_cursors=None)
        restored.from_dict(payload)
        self.assertEqual(restored.axis_units, [])


class TestReadoutDeltasAxesIndex(Base):
    """
    `readout()`/`deltas()` now carry `axes_index` per row/pair, so a caller
    (`OverlayPanel._render_readout`, `App._measurement_rows`) can format
    each one with ITS OWN axis's unit instead of assuming a single global
    pair -- see `TestCursorAxisUnits` above for the companion piece.
    """

    def test_readout_rows_carry_their_axes_index(self):
        self.cursors.add("v", 2.0e-4, axes_index=0)
        rows = self.cursors.readout()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["axes_index"], 0)

    def test_deltas_carry_their_axes_index(self):
        self.cursors.add("v", 1.0e-4, axes_index=0)
        self.cursors.add("v", 3.0e-4, axes_index=0)
        deltas = self.cursors.deltas()
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0]["axes_index"], 0)

    def test_deltas_only_pair_cursors_sharing_axes_index(self):
        # Two axes on screen (e.g. Y1/Y2): a cursor on each must never be
        # paired into a delta that mixes their units.
        self.fig2 = Figure(figsize=(6, 4))
        ax2 = self.fig2.add_subplot(111)
        self.cursors.attach([self.ax, ax2])
        self.cursors.add("v", 1.0e-4, axes_index=0)
        self.cursors.add("v", 3.0e-4, axes_index=1)
        self.assertEqual(self.cursors.deltas(), [])


class TestAxesContext(unittest.TestCase):
    """
    `App._axes_context`: the single place deciding what each element of
    `self.axes` actually IS on the current redraw (its unit pair and its
    "Eje" selector label) -- replacing the old fixed `cursors.x_unit`/
    `y_unit` pair that mislabeled a Y2/phase axis with the Y1/magnitude
    unit. `App` cannot be imported here (no tkinter), so the method is
    AST-extracted the same way `TestAxisFactorOverride`/`TestSliderMath`
    extract their own pure helpers -- it only reads `settings` and
    `len(self.axes)`, never any other widget state.
    """

    @classmethod
    def setUpClass(cls):
        import ast
        path = os.path.expanduser("~/mnt/LabPlotter/gui/app.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        app = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "App")
        wanted = {"_axes_context", "_axes_labels", "_overlay_units"}
        funcs = [n for n in app.body
                if isinstance(n, ast.FunctionDef) and n.name in wanted]
        assert len(funcs) == 3, "axes-context helpers not found"
        ns = {"t": t}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), path, "exec"), ns)
        cls.axes_context = staticmethod(ns["_axes_context"])
        cls.axes_labels = staticmethod(ns["_axes_labels"])
        cls.overlay_units = staticmethod(ns["_overlay_units"])

    class _FakeApp:
        def __init__(self, n_axes):
            self.axes = [object() for _ in range(n_axes)]
            self._axes_context_cache = []

    def test_single_axes_standard_mode(self):
        app = self._FakeApp(1)
        ctx = self.axes_context(app, {"mode": "Tiempo / Frecuencia",
                                      "x_unit": "s", "y_unit": "V"})
        self.assertEqual(len(ctx), 1)
        self.assertEqual((ctx[0]["x_unit"], ctx[0]["y_unit"]), ("s", "V"))
        self.assertEqual(ctx[0]["label"], t("Principal"))

    def test_standard_mode_with_secondary_y_labels_y1_y2(self):
        app = self._FakeApp(2)
        ctx = self.axes_context(app, {"mode": "Tiempo / Frecuencia",
                                      "x_unit": "s", "y_unit": "V",
                                      "y2_unit": "A"})
        self.assertEqual([c["y_unit"] for c in ctx], ["V", "A"])
        self.assertIn("Y1", ctx[0]["label"])
        self.assertIn("Y2", ctx[1]["label"])

    def test_bode_separate_labels_magnitude_and_phase_plainly(self):
        app = self._FakeApp(2)
        ctx = self.axes_context(app, {"mode": "Diagrama de Bode",
                                      "x_unit": "Hz", "bode_layout": "separate"})
        self.assertEqual([c["y_unit"] for c in ctx], ["dB", "deg"])
        self.assertEqual(ctx[0]["label"], t("Magnitud"))
        self.assertEqual(ctx[1]["label"], t("Fase"))

    def test_bode_shared_labels_magnitude_and_phase_with_y1_y2(self):
        app = self._FakeApp(2)
        ctx = self.axes_context(app, {"mode": "Diagrama de Bode",
                                      "x_unit": "Hz", "bode_layout": "shared"})
        self.assertIn(t("Magnitud"), ctx[0]["label"])
        self.assertIn("Y1", ctx[0]["label"])
        self.assertIn(t("Fase"), ctx[1]["label"])
        self.assertIn("Y2", ctx[1]["label"])

    def test_bode_with_no_curves_falls_back_to_one_axes(self):
        app = self._FakeApp(1)
        ctx = self.axes_context(app, {"mode": "Diagrama de Bode", "x_unit": "Hz"})
        self.assertEqual(len(ctx), 1)
        self.assertEqual(ctx[0]["y_unit"], "dB")

    def test_xy_mode_shares_y_unit_on_both_axes(self):
        app = self._FakeApp(1)
        ctx = self.axes_context(app, {"mode": "Modo X/Y", "x_unit": "s",
                                      "y_unit": "V"})
        self.assertEqual((ctx[0]["x_unit"], ctx[0]["y_unit"]), ("V", "V"))

    def test_histogram_x_axis_uses_x_unit(self):
        app = self._FakeApp(1)
        ctx = self.axes_context(app, {"mode": "Histograma de valores",
                                      "x_unit": "s", "y_unit": "V",
                                      "hist_axis": "x"})
        self.assertEqual(ctx[0]["x_unit"], "s")
        self.assertEqual(ctx[0]["y_unit"], "")

    def test_histogram_y_axis_uses_y_unit(self):
        app = self._FakeApp(1)
        ctx = self.axes_context(app, {"mode": "Histograma de valores",
                                      "x_unit": "s", "y_unit": "V",
                                      "hist_axis": "y"})
        self.assertEqual(ctx[0]["x_unit"], "V")

    def test_blank_mode_has_no_units(self):
        app = self._FakeApp(1)
        ctx = self.axes_context(app, {"mode": "Pizarra en blanco",
                                      "x_unit": "s", "y_unit": "V"})
        self.assertEqual((ctx[0]["x_unit"], ctx[0]["y_unit"]), ("", ""))

    def test_axes_labels_reads_from_the_cache(self):
        app = self._FakeApp(2)
        app._axes_context_cache = [{"label": "A"}, {"label": "B"}]
        self.assertEqual(self.axes_labels(app), ["A", "B"])

    def test_axes_labels_falls_back_to_principal_when_cache_empty(self):
        app = self._FakeApp(0)
        self.assertEqual(self.axes_labels(app), [t("Principal")])

    def test_overlay_units_reads_the_requested_index(self):
        app = self._FakeApp(2)
        app._axes_context_cache = [{"x_unit": "s", "y_unit": "V"},
                                   {"x_unit": "s", "y_unit": "deg"}]
        self.assertEqual(self.overlay_units(app, 1), ("s", "deg"))

    def test_overlay_units_out_of_range_returns_empty(self):
        app = self._FakeApp(1)
        app._axes_context_cache = [{"x_unit": "s", "y_unit": "V"}]
        self.assertEqual(self.overlay_units(app, 3), ("", ""))


class TestOverlayPanelSentinelMasters(unittest.TestCase):
    """
    Static guard for the crash reported live: `_apply_schema` does
    `row.pack(fill="x", before=sentinel)`, and Tkinter's `pack(before=X)`
    requires `row` and `X` to share the same master -- `coords_group`/
    `p2_group` in `_build_annotation_pane` built their sentinel against
    `parent` while their rows lived in a child frame (`coords`/`capture`),
    which raised `_tkinter.TclError: can't pack ... inside ...` on the very
    first `App()` (no tkinter here to reproduce that directly, so this
    walks the AST of every method and checks it structurally instead).
    """

    @staticmethod
    def _find_master_mismatches(path):
        import ast
        tree = ast.parse(open(path, encoding="utf-8").read())
        problems = []

        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef):
                continue
            wrap_master = {}    # row var -> (master src, lineno)
            group_master = {}   # group var -> (sentinel master src, lineno)
            group_rows = {}     # group var -> [(row var, lineno)]

            for node in ast.walk(func):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    call = node.value
                    if (isinstance(call.func, ast.Attribute) and call.func.attr == "_wrap"
                            and call.args and isinstance(node.targets[0], ast.Tuple)
                            and len(node.targets[0].elts) == 2
                            and isinstance(node.targets[0].elts[1], ast.Name)):
                        wrap_master[node.targets[0].elts[1].id] = (
                            ast.unparse(call.args[0]), node.lineno)
                    if (isinstance(call.func, ast.Attribute) and call.func.attr == "_new_group"
                            and call.args and isinstance(call.args[0], ast.Call)
                            and isinstance(call.args[0].func, ast.Attribute)
                            and call.args[0].func.attr == "_sentinel"
                            and isinstance(node.targets[0], ast.Name)):
                        gvar = node.targets[0].id
                        group_master[gvar] = (ast.unparse(call.args[0].args[0]), node.lineno)
                        group_rows.setdefault(gvar, [])

                if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                    gvar = node.target.id
                    if gvar in group_master and isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if (isinstance(elt, ast.Tuple) and elt.elts
                                    and isinstance(elt.elts[0], ast.Name)):
                                group_rows[gvar].append((elt.elts[0].id, node.lineno))

                if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Attribute)
                        and node.value.func.attr == "append"
                        and isinstance(node.value.func.value, ast.Name)):
                    gvar = node.value.func.value.id
                    if gvar in group_master and node.value.args:
                        arg = node.value.args[0]
                        if (isinstance(arg, ast.Tuple) and arg.elts
                                and isinstance(arg.elts[0], ast.Name)):
                            group_rows[gvar].append((arg.elts[0].id, node.lineno))

            for gvar, (sentinel_src, gline) in group_master.items():
                for row_var, use_line in group_rows.get(gvar, []):
                    if row_var not in wrap_master:
                        continue
                    wrap_src, wrap_line = wrap_master[row_var]
                    if wrap_src != sentinel_src:
                        problems.append(
                            f"{path}:{use_line}: group '{gvar}' sentinel master "
                            f"'{sentinel_src}' (line {gline}) != row '{row_var}' "
                            f"_wrap master '{wrap_src}' (line {wrap_line})")
        return problems

    def test_every_hideable_row_shares_its_group_sentinels_master(self):
        path = os.path.expanduser("~/mnt/LabPlotter/gui/overlay_panel.py")
        problems = self._find_master_mismatches(path)
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main(verbosity=2)
