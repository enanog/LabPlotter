"""
gui/histogram_window.py
------------------------
Ventana auxiliar "Histograma": distribución de los valores de una o más
señales ya cargadas en la ventana principal, superpuestas en un mismo eje.

Mismo patrón que `gui/board_window.py`: un `ctk.CTkToplevel` que lee/edita
estado en vivo de `app` (acá sólo lectura -- no modifica `app.signals`) y
delega todo el cálculo numérico en un módulo sin Tk (`core/histogram.py`)
para poder probarlo aparte.
"""

from __future__ import annotations

import os
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from core.export import export_figure
from core.histogram import BIN_RULES, combined_range, compute_histogram
from core.i18n import t

from .theme import apply_plot_chrome, col, font, spaced
from .widgets import (
    check_field, combo_field, entry_field, ghost_button, hint, primary_button,
    segmented_field, stacked_label,
)

# Local copy of `gui.app.SCALES`: importing it from `gui.app` would create a
# circular import (`gui.app` itself imports `HistogramWindow` from this
# module to wire up the "Ver histograma..." button).
SCALES = ["linear", "log"]


class HistogramWindow(ctk.CTkToplevel):
    """Histograma superpuesto de los valores (eje X o Y) de las señales elegidas."""

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app
        self.title(t("Histograma"))
        self.geometry("1040x660")
        self.minsize(820, 520)

        # uid -> BooleanVar ("incluir esta señal"). Reconstruido en
        # `_refresh_signal_checks` cada vez que cambia la lista de señales
        # de `app`, para no quedar mirando uids que ya no existen.
        self._signal_vars: dict[str, ctk.BooleanVar] = {}
        self._canvas: Optional[FigureCanvasTkAgg] = None
        self._toolbar: Optional[NavigationToolbar2Tk] = None

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(header, text=spaced(t("Histograma")), font=font("header"),
                     text_color=col("fg_muted")).pack(side="left")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(8, 8))
        body.grid_columnconfigure(0, weight=0, minsize=260)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # --- left: controls ------------------------------------------- #
        left = ctk.CTkScrollableFrame(body, fg_color="transparent", corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        stacked_label(left, t("Señales")).pack(fill="x", pady=(0, 2))
        self.signal_checks_frame = ctk.CTkFrame(left, fg_color="transparent")
        self.signal_checks_frame.pack(fill="x", pady=(0, 4))
        ghost_button(left, t("↻ Actualizar lista de señales"), self.refresh_signals,
                    height=24).pack(fill="x", pady=(0, 10))

        self.axis_var = ctk.StringVar(value="y")
        combo_field(left, t("Eje a histogramar"), self.axis_var, ["y", "x"],
                    width=90, command=lambda _v: self._redraw())
        hint(left, t("«y» = valores de la señal (tensión, dB, magnitud "
                     "propia...); «x» = tiempo o frecuencia."),
             wraplength=230).pack(fill="x", pady=(0, 10))

        self.bin_rule_var = ctk.StringVar(value="auto")
        combo_field(left, t("Regla de bins"), self.bin_rule_var,
                    list(BIN_RULES) + ["manual"], width=90,
                    command=lambda _v: self._on_bin_rule_change())
        self.bin_count_var = ctk.StringVar(value="30")
        self.bin_count_field = entry_field(left, t("Cantidad de bins"),
                                           self.bin_count_var, rule=False,
                                           on_enter=self._redraw)
        self.bin_count_field.configure(state="disabled")
        hint(left, t("«manual» habilita el campo de cantidad de bins; "
                     "cualquier otra regla la calcula sola a partir de los "
                     "datos (ver `core/histogram.py`)."),
             wraplength=230).pack(fill="x", pady=(0, 10))

        self.density_var = ctk.BooleanVar(value=False)
        check_field(left, t("Densidad (normalizar área a 1)"), self.density_var,
                    command=self._redraw)
        self.shared_bins_var = ctk.BooleanVar(value=True)
        check_field(left, t("Mismos bordes de bin para todas"), self.shared_bins_var,
                    command=self._redraw, rule=False)
        hint(left, t("Con esto tildado, las señales superpuestas comparten "
                     "rango y bordes de bin -- si no, cada una arma los "
                     "suyos y comparar alturas entre ellas no tiene sentido."),
             wraplength=230).pack(fill="x", pady=(0, 10))

        self.alpha_var = ctk.StringVar(value="0.55")
        entry_field(left, t("Opacidad de barras"), self.alpha_var, rule=False,
                    on_enter=self._redraw)

        self.xscale_var = ctk.StringVar(value="linear")
        segmented_field(left, t("Escala X"), SCALES, self.xscale_var,
                        command=lambda _v: self._redraw())
        self.yscale_var = ctk.StringVar(value="linear")
        segmented_field(left, t("Escala Y"), SCALES, self.yscale_var,
                        command=lambda _v: self._redraw(), rule=False)
        hint(left, t("Log en Y es lo habitual para ver la cola de una "
                     "distribución (como en la figura de referencia). Log "
                     "en X sólo tiene sentido si TODOS los bins caen en "
                     "valores positivos -- con un histograma que cruza el "
                     "cero (p. ej. deltaG) va a recortar la mitad negativa; "
                     "en ese caso dejalo en «linear»."),
             wraplength=230).pack(fill="x", pady=(0, 10))

        ghost_button(left, t("↻ Actualizar"), self._redraw, height=30
                     ).pack(fill="x", pady=(10, 6))

        stacked_label(left, t("Estadísticas")).pack(fill="x", pady=(8, 2))
        self.stats_label = hint(left, "", wraplength=230)
        self.stats_label.pack(fill="x")

        self.dpi_var = ctk.StringVar(value="300")
        entry_field(left, "DPI", self.dpi_var, rule=False)
        primary_button(left, t("Exportar figura..."), self._export,
                       height=32).pack(fill="x", pady=(10, 0))

        # --- right: plot ------------------------------------------------- #
        right = ctk.CTkFrame(body, fg_color=col("surface"), corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.fig = Figure(figsize=(6.6, 4.6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.plot_container = ctk.CTkFrame(right, fg_color="transparent")
        self.plot_container.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        self.toolbar_container = ctk.CTkFrame(right, fg_color="transparent")
        self.toolbar_container.grid(row=1, column=0, sticky="ew")

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_container)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.toolbar_container)
        self.toolbar.update()

        self._refresh_signal_checks()
        self._redraw()
        self.transient(master)

    # ------------------------------------------------------------------ #
    def _on_bin_rule_change(self) -> None:
        manual = self.bin_rule_var.get() == "manual"
        self.bin_count_field.configure(state="normal" if manual else "disabled")
        self._redraw()

    def refresh_signals(self) -> None:
        """Call after signals are added/removed/reloaded in the main window."""
        self._refresh_signal_checks()
        self._redraw()

    def _refresh_signal_checks(self) -> None:
        for w in self.signal_checks_frame.winfo_children():
            w.destroy()

        live_uids = set(self.app.signal_order)
        self._signal_vars = {
            uid: var for uid, var in self._signal_vars.items() if uid in live_uids
        }

        if not self.app.signal_order:
            hint(self.signal_checks_frame, t("Sin trazas cargadas."),
                 wraplength=230).pack(fill="x")
            return

        for uid in self.app.signal_order:
            sig = self.app.signals[uid]
            if sig.missing:
                continue
            var = self._signal_vars.setdefault(
                uid, ctk.BooleanVar(value=sig.visible))
            label = sig.display_name or sig.name
            check_field(self.signal_checks_frame, label, var,
                        command=self._redraw, rule=False)

    def _selected_signals(self):
        return [self.app.signals[uid] for uid, var in self._signal_vars.items()
                if var.get() and uid in self.app.signals]

    def _values_for(self, sig, axis: str) -> np.ndarray:
        x, y = sig.processed()
        return x if axis == "x" else y

    def _bin_spec(self):
        if self.bin_rule_var.get() == "manual":
            try:
                n = int(float(str(self.bin_count_var.get()).strip().replace(",", ".")))
            except (TypeError, ValueError):
                n = 30
            return max(1, n)
        return self.bin_rule_var.get()

    def _redraw(self) -> None:
        self.ax.clear()
        signals = self._selected_signals()
        axis = self.axis_var.get()
        density = self.density_var.get()
        try:
            alpha = max(0.05, min(1.0, float(str(self.alpha_var.get()).replace(",", "."))))
        except (TypeError, ValueError):
            alpha = 0.55

        if not signals:
            self.ax.text(0.5, 0.5, t("Elegí al menos una señal a la izquierda."),
                         ha="center", va="center", transform=self.ax.transAxes,
                         color=col("fg_muted"))
            apply_plot_chrome(self.fig)
            self.canvas.draw_idle()
            self.stats_label.configure(text="")
            return

        all_values = [self._values_for(sig, axis) for sig in signals]
        value_range = combined_range(all_values) if self.shared_bins_var.get() else None
        bins = self._bin_spec()

        stats_lines = []
        any_drawn = False
        for sig, values in zip(signals, all_values):
            result = compute_histogram(values, bins=bins, value_range=value_range,
                                       density=density)
            if result is None:
                stats_lines.append(f"{sig.display_name or sig.name}: {t('sin datos válidos')}")
                continue
            any_drawn = True
            label = self.app._legend_label(sig) if hasattr(self.app, "_legend_label") \
                else (sig.legend_label or sig.name)
            self.ax.stairs(result.counts, result.edges, fill=True,
                           color=sig.color, alpha=alpha, label=label)
            self.ax.stairs(result.counts, result.edges, fill=False,
                           color=sig.color, alpha=1.0, linewidth=1.0)
            unit = sig.unit_v_in if axis == "y" else sig.unit_t_in
            unit_txt = f" {unit}" if unit else ""
            stats_lines.append(
                f"{sig.display_name or sig.name}: n={result.n_samples}"
                + (f" (+{result.n_dropped} desc.)" if result.n_dropped else "")
                + f", μ={result.mean:.4g}{unit_txt}, σ={result.std:.4g}{unit_txt}")

        xlabel = t("Valor") if axis == "y" else t("Tiempo / frecuencia")
        self.ax.set_xlabel(xlabel)
        self.ax.set_ylabel(t("Densidad de probabilidad") if density else t("Cuentas"))
        if any_drawn:
            self.ax.legend(loc="best", fontsize=8)

        # `set_xscale`/`set_yscale` never raise on non-positive data under
        # "log" -- matplotlib just silently clips whatever isn't > 0 out of
        # view (see the hint next to the two segmented controls) -- but the
        # try/except stays as a hard guard against some future matplotlib
        # version changing that, since a crash here would take the whole
        # window down instead of just leaving the axis unscaled.
        try:
            self.ax.set_xscale(self.xscale_var.get())
        except ValueError:
            pass
        try:
            self.ax.set_yscale(self.yscale_var.get())
        except ValueError:
            pass

        apply_plot_chrome(self.fig)
        self.fig.tight_layout()
        self.canvas.draw_idle()
        self.stats_label.configure(text="\n".join(stats_lines))

    # ------------------------------------------------------------------ #
    def _export(self) -> None:
        try:
            dpi = max(50, int(float(str(self.dpi_var.get()).strip())))
        except (TypeError, ValueError):
            dpi = 300

        out_path = filedialog.asksaveasfilename(
            title=t("Exportar histograma"), defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf"), ("PNG", "*.png"), ("SVG", "*.svg")])
        if not out_path:
            return
        try:
            export_figure(self.fig, out_path, dpi=dpi)
        except OSError as exc:
            messagebox.showerror(t("Error al exportar"), str(exc))
