# -*- coding: utf-8 -*-
"""Headless tests for core/math_channels.py (no Tk required)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

from core.data_io import Signal
from core.history import History, apply_snapshot
from core.math_channels import (
    MathError, MathSpec, align, build_math_signal, depends_on, describe,
    evaluate, normalize, parse, referenced_aliases, refresh_math_signals,
)


def make_signal(uid, t, v, **kw):
    return Signal(uid=uid, name=uid, source_path=f"{uid}.csv",
                  t_raw=np.asarray(t, float), v_raw=np.asarray(v, float), **kw)


class TestParsing(unittest.TestCase):
    def test_normalize_si_and_typography(self):
        self.assertEqual(eval(normalize("1k")), 1000.0)
        self.assertAlmostEqual(eval(normalize("4.7u")), 4.7e-6)
        self.assertEqual(normalize("A × B ÷ C − D"), "A * B / C - D")
        self.assertEqual(normalize("A^2"), "A**2")
        self.assertEqual(normalize("1e-3"), "1e-3")          # scientific untouched

    def test_referenced_aliases_order(self):
        self.assertEqual(referenced_aliases("C - A + A"), ["A", "C"])

    def test_rejects_unsafe_constructs(self):
        for bad in ("__import__('os')", "A.real", "A[0]", "open", "A if B else C",
                    "lambda: 1", "A < B", "'x'", "abs(A, key=1)", "A // B", "Z + 1"):
            with self.subTest(bad=bad):
                with self.assertRaises(MathError):
                    parse(bad)

    def test_empty_and_syntax(self):
        with self.assertRaises(MathError):
            parse("   ")
        with self.assertRaises(MathError):
            parse("(A + B")

    def test_arity(self):
        with self.assertRaises(MathError):
            parse("smooth(A)")
        parse("smooth(A, 5)")

    def test_describe(self):
        self.assertEqual(describe("A - B", {"A": "CH1", "B": "CH2"}), "CH1 - CH2")
        self.assertEqual(describe("abs(A)", {"A": "Vin"}), "abs(Vin)")


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.t = np.linspace(0, 1e-3, 1001)
        self.a = np.sin(2 * np.pi * 1e3 * self.t)
        self.b = 0.5 * np.ones_like(self.t)

    def test_arithmetic_same_grid(self):
        x, y = evaluate("A - B", {"A": (self.t, self.a), "B": (self.t, self.b)})
        np.testing.assert_allclose(x, self.t)
        np.testing.assert_allclose(y, self.a - 0.5)

    def test_si_constant(self):
        _x, y = evaluate("A / 1k", {"A": (self.t, self.a)})
        np.testing.assert_allclose(y, self.a / 1000)

    def test_scalar_functions_broadcast(self):
        _x, y = evaluate("A - mean(A)", {"A": (self.t, self.a + 2)})
        self.assertAlmostEqual(float(np.mean(y)), 0.0, places=9)
        _x, y = evaluate("rms(A)", {"A": (self.t, self.a)})
        self.assertEqual(y.shape, self.t.shape)
        self.assertAlmostEqual(float(y[0]), 1 / np.sqrt(2), places=2)

    def test_deriv_and_integ(self):
        w = 2 * np.pi * 1e3
        _x, d = evaluate("deriv(A)", {"A": (self.t, self.a)})
        np.testing.assert_allclose(d[5:-5], w * np.cos(w * self.t[5:-5]), rtol=1e-3, atol=w * 1e-3)
        _x, i = evaluate("integ(A)", {"A": (self.t, self.a)})
        np.testing.assert_allclose(i, (1 - np.cos(w * self.t)) / w, atol=1e-8)

    def test_time_variable(self):
        x, y = evaluate("A * 0 + t", {"A": (self.t, self.a)})
        np.testing.assert_allclose(y, x)

    def test_division_by_zero_is_nan_not_inf(self):
        _x, y = evaluate("A / B", {"A": (self.t, self.a), "B": (self.t, np.zeros_like(self.t))})
        self.assertFalse(np.isinf(y).any())
        self.assertTrue(np.isnan(y).any())

    def test_interpolation_and_overlap(self):
        t2 = np.linspace(0.5e-3, 2e-3, 301)
        x, y = evaluate("A + B", {"A": (self.t, self.a), "B": (t2, 2 * t2)})
        self.assertGreaterEqual(x[0], 0.5e-3)
        self.assertLessEqual(x[-1], 1e-3)
        np.testing.assert_allclose(y, np.interp(x, self.t, self.a) + 2 * x, atol=1e-12)

    def test_no_overlap(self):
        with self.assertRaises(MathError):
            evaluate("A + B", {"A": (self.t, self.a), "B": (self.t + 1, self.a)})

    def test_requires_a_channel(self):
        with self.assertRaises(MathError):
            evaluate("1 + 2", {"A": (self.t, self.a)})

    def test_unassigned_alias(self):
        with self.assertRaises(MathError):
            evaluate("A + C", {"A": (self.t, self.a)})

    def test_smooth_keeps_constant_edges(self):
        _x, y = evaluate("smooth(A, 21)", {"A": (self.t, np.full_like(self.t, 3.0))})
        np.testing.assert_allclose(y, 3.0)

    def test_align_unsorted_input(self):
        x = np.array([3.0, 1.0, 2.0]); y = np.array([30.0, 10.0, 20.0])
        xs, out = align({"A": (x, y)}, ["A"])
        np.testing.assert_allclose(xs, [1, 2, 3])
        np.testing.assert_allclose(out["A"], [10, 20, 30])


class TestSignals(unittest.TestCase):
    def setUp(self):
        t = np.arange(0, 10.0)                       # raw in ms
        self.ch1 = make_signal("ch1", t, 2 * t, unit_t_in="ms")
        self.ch2 = make_signal("ch2", t, np.ones_like(t), unit_t_in="ms")
        self.signals = {"ch1": self.ch1, "ch2": self.ch2}

    def add_math(self, uid, expr, ops, **kw):
        sig = build_math_signal(MathSpec(expr=expr, operands=ops, name=uid, **kw), uid)
        self.signals[uid] = sig
        return sig

    def test_uses_processed_data_and_is_live(self):
        m = self.add_math("m", "A - B", {"A": "ch1", "B": "ch2"})
        self.assertTrue(m.is_math)
        self.assertEqual(refresh_math_signals(self.signals), [])
        x, y = m.processed()
        np.testing.assert_allclose(x, np.arange(10) * 1e-3)       # base units (s)
        np.testing.assert_allclose(y, 2 * np.arange(10) - 1)
        self.ch1.gain = 10.0                                    # edit a source
        refresh_math_signals(self.signals)
        np.testing.assert_allclose(m.processed()[1], 20 * np.arange(10) - 1)

    def test_own_gain_applied_on_top(self):
        m = self.add_math("m", "A", {"A": "ch1"})
        m.gain = 0.5
        refresh_math_signals(self.signals)
        np.testing.assert_allclose(m.processed()[1], np.arange(10))

    def test_chained_math(self):
        self.add_math("m1", "A + B", {"A": "ch1", "B": "ch2"})
        m2 = self.add_math("m2", "2 * A", {"A": "m1"})
        refresh_math_signals(self.signals)
        np.testing.assert_allclose(m2.processed()[1], 2 * (2 * np.arange(10) + 1))
        self.assertTrue(depends_on(self.signals, "m2", "ch1"))
        self.assertFalse(depends_on(self.signals, "ch1", "m2"))

    def test_cycle_reports_error_without_raising(self):
        a = self.add_math("ma", "A", {"A": "mb"})
        b = self.add_math("mb", "A", {"A": "ma"})
        failed = refresh_math_signals(self.signals)
        self.assertEqual(set(failed), {"ma", "mb"})
        self.assertEqual(a.t_raw.size, 0)
        self.assertTrue(b.math_error)

    def test_removed_source(self):
        m = self.add_math("m", "A + B", {"A": "ch1", "B": "ch2"})
        del self.signals["ch2"]
        self.assertEqual(refresh_math_signals(self.signals), ["m"])
        self.assertIn("B", m.math_error)
        x, y = m.processed()
        self.assertEqual(x.size, 0)

    def test_domain_mismatch(self):
        f = make_signal("f", np.arange(1, 11.0), np.ones(10), domain="freq", unit_t_in="Hz")
        self.signals["f"] = f
        m = self.add_math("m", "A + B", {"A": "ch1", "B": "f"})
        self.assertEqual(refresh_math_signals(self.signals), ["m"])

    def test_freq_domain_result(self):
        f = make_signal("f", np.arange(1, 11.0), np.ones(10), domain="freq",
                        unit_t_in="kHz", y_kind="dB", unit_v_in="dB")
        self.signals["f"] = f
        m = self.add_math("m", "A - 3", {"A": "f"}, y_kind="dB")
        refresh_math_signals(self.signals)
        self.assertEqual((m.domain, m.unit_t_in, m.unit_v_in), ("freq", "Hz", "dB"))
        np.testing.assert_allclose(m.processed()[0], np.arange(1, 11) * 1e3)

    def test_custom_unit_kept(self):
        m = self.add_math("m", "A * B", {"A": "ch1", "B": "ch2"}, y_kind="custom", unit="W")
        refresh_math_signals(self.signals)
        self.assertEqual((m.y_kind, m.unit_v_in), ("custom", "W"))

    def test_undo_restores_expression(self):
        m = self.add_math("m", "A - B", {"A": "ch1", "B": "ch2"})
        order = list(self.signals)
        hist = History()
        hist.push(hist.capture("edit", self.signals, order, {}, None))
        m.math_expr, m.math_operands = "A + B", {"A": "ch1", "B": "ch2"}
        snap = hist.undo(hist.capture("undo", self.signals, order, {}, None))
        apply_snapshot(snap, self.signals, order, {})
        self.assertEqual(m.math_expr, "A - B")


class TestHistogramBinCap(unittest.TestCase):
    """Shared-range rule bins must stay bounded (math channels mix scales)."""

    def test_rule_bins_capped_on_wide_shared_range(self):
        from core.histogram import MAX_AUTO_BINS, combined_range, compute_histogram
        narrow = np.random.default_rng(0).normal(0, 1e-5, 5000)
        wide = np.linspace(-250, 250, 5000)
        rng = combined_range([narrow, wide])
        res = compute_histogram(narrow, bins="auto", value_range=rng)
        self.assertLessEqual(res.counts.size, MAX_AUTO_BINS)
        self.assertEqual(int(res.counts.sum()), narrow.size)

    def test_normal_case_unchanged(self):
        from core.histogram import compute_histogram
        x = np.random.default_rng(1).normal(0, 1, 2000)
        res = compute_histogram(x, bins="auto", value_range=(x.min(), x.max()))
        self.assertEqual(res.edges.size, np.histogram_bin_edges(x, "auto", (x.min(), x.max())).size)


if __name__ == "__main__":
    unittest.main()
