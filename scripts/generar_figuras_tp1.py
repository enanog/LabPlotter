"""
generar_figuras_tp1.py
-----------------------
Regenerates every TP1 figure (except the sweep/sincronismo one, which the
user asked to leave untouched) using LabPlotter's core modules in headless
mode (see README section 13): core.data_io.read_table for oscilloscope CSVs,
core.export for the publication rcParams and the vector PDF export, and
core.session.save_figure_state for the `.labplotter.json` data sidecar the
app itself writes next to an exported figure.

Style rules applied uniformly across the report (see also the color
convention below):

  - Time-domain sim-vs-measured pairs: SIMULATED = dashed line,
    MEASURED = solid line. Same color always means the same role
    (input=blue, simulated output=orange, measured output=red).
  - Bode plots: MAGNITUDE panel = solid line, PHASE panel = dashed line,
    for every curve on that panel (theoretical tolerance curves and the
    measured trace alike).
  - A series with few data points (a handful of manually logged
    frequencies, not a dense sweep/capture) is drawn as discrete markers
    (circle / triangle / square) with NO connecting line, instead of a
    solid/dashed line implying continuous data.
  - Every legend is placed so it does not sit on top of the data (an
    empty corner, "best", or outside the axes) -- checked visually per
    figure, not just left at a default corner.
  - Periodic time-domain signals show exactly 2.5 cycles of the test
    frequency.
  - Axis labels always name the physical quantity and its unit
    ("Tiempo [us]", "Tension [V]", "|H| [dB]"...), never a bare "t"/"V".
  - Measured square/triangular-wave traces (input and output) are scaled
    by a 0.5 gain: the signal generator's actual output amplitude reads
    about 2x the +-1 V used in the LTspice sources, so without this
    correction the measured and simulated traces are not on comparable
    scales.
  - The pasabajos manual Bode measurement (Tabla 1 del Anexo) records
    phase as a positive lag magnitude (theta_dt, theta_xy), opposite in
    sign to the theoretical convention used everywhere else in the
    report (phi_PB = -arctan(wRC), negative). Only that manual series is
    negated before plotting so it overlays correctly on the theoretical
    curves; the autobode (Network Analyzer) measurement already carries
    the correct sign in its source file.

JSON sidecar (`.labplotter.json`, one per exported PDF):
  Written via core.session.save_figure_state with the SAME shape the GUI's
  own `App._gather_plot_state()` produces for one plot tab -- a "settings"
  dict (axis labels/scales/legend/etc.) plus a "signals" list, each entry
  naming a `source_path` + `x_col`/`y_col` (the literal column names
  core.data_io.read_table would assign when the app itself loads that same
  file) instead of a raw copy of the plotted arrays. This is what lets the
  app actually re-import the figure -- a plain dump of x/y numbers is not
  a format it understands. Two figures (`bode_manual_pb`, `vr_medido`) are
  built from a hand-transcribed table (Tabla 1 del Anexo) rather than a
  logged CSV, so that table is written once to `data/processed/` and
  referenced from there like any other source file. A figure with more
  than one subplot (the three per-frequency panels in the combinado
  figures, the two probe panels in sondas_x1_x10) has no single-tab
  equivalent in the app's model: all of that figure's signals are still
  listed in one sidecar so none of the underlying data is lost, but
  re-opening it in the app would show them on one shared axes rather than
  reproducing the original per-panel layout.

Color convention (Matplotlib tab10 palette, same as
core.data_io.DEFAULT_COLOR_CYCLE), fixed across the whole report:
  - Input signal v(t):                       blue   #1f77b4
  - Simulated output (LTspice):               orange #ff7f0e
  - Measured output (experimental):           red    #d62728
  - f0 minimum (tolerance):                  blue   #1f77b4
  - f0 nominal:                              orange #ff7f0e
  - f0 maximum (tolerance):                  green  #2ca02c
  - Automatic measurement (Autobode / NA):    red    #d62728
  - Manual measurement (point by point):      red    #d62728, markers only

Usage:
    cd LabPlotter
    python scripts/generar_figuras_tp1.py

Only requires the core/ dependencies (pandas, numpy, matplotlib); no need
for customtkinter.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

SCRIPT_DIR = Path(__file__).resolve().parent
LABPLOTTER_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(LABPLOTTER_ROOT))

from core.export import set_publication_style, export_figure  # noqa: E402
from core.data_io import read_table  # noqa: E402
from core.session import save_figure_state  # noqa: E402

# ---------------------------------------------------------------------- #
# Report paths (sibling-folder layout: .../ITBA/LabPlotter and
# .../ITBA/25.13-LaboratorioDeElectronica 1)
# ---------------------------------------------------------------------- #
REPORT_ROOT = LABPLOTTER_ROOT.parent / "25.13-LaboratorioDeElectrónica 1" / "TPN1"
DATA_RAW = REPORT_ROOT / "data" / "raw"
DATA_SIM = REPORT_ROOT / "data" / "simulations"
DATA_PROCESSED = REPORT_ROOT / "data" / "processed"
PLOTS_OUT = REPORT_ROOT / "assets" / "plots"

if not REPORT_ROOT.exists():
    raise SystemExit(
        f"Report folder not found at {REPORT_ROOT}. "
        "This script assumes LabPlotter and "
        "'25.13-LaboratorioDeElectrónica 1' are sibling folders."
    )
DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

# Device-side drive root the user's LabPlotter actually runs under (see the
# working `.labplotter.json` example they sent back: every source_path in it
# starts with "D:/Documentos/ITBA/..."). This script itself only ever reads
# from the sandbox mirror of that same tree, so source_path is rebuilt from
# that fixed prefix rather than from whatever local path this process used.
_DEVICE_ROOT = "D:/Documentos/ITBA"


def _device_path(local_path: Path) -> str:
    """Map a local file (inside LABPLOTTER_ROOT.parent) to the matching
    device path string LabPlotter expects in a signal's `source_path`."""
    rel = local_path.resolve().relative_to(LABPLOTTER_ROOT.parent.resolve())
    return f"{_DEVICE_ROOT}/{rel.as_posix()}"


# ---------------------------------------------------------------------- #
# Fixed palette (tab10) and style constants -- see module docstring
# ---------------------------------------------------------------------- #
C_MIN = "#1f77b4"    # blue   -> f0 minimum / input signal
C_NOM = "#ff7f0e"    # orange -> f0 nominal / simulated output
C_MAX = "#2ca02c"    # green  -> f0 maximum
C_MEAS = "#d62728"   # red    -> measured (automatic or manual)

SIM_LS, MEAS_LS = "--", "-"      # time-domain: simulated dashed, measured solid
MAG_LS, PHASE_LS = "-", "--"     # Bode: magnitude solid, phase dashed

MEAS_GAIN = 0.5   # correction for the ~2x amplitude offset of the measured
                   # square/triangular traces relative to the +-1V LTspice source

R_NOM, C_NOM_VAL = 3.3e3, 3.9e-9
F0_MIN, F0_NOM, F0_MAX = 10.71e3, 12.37e3, 14.46e3


def _eng_log_axis(ax):
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(mticker.EngFormatter(unit="", sep=""))
    ax.grid(True, which="both")


# ---------------------------------------------------------------------- #
# JSON sidecar helpers -- mirror the field names/defaults of a real
# `.labplotter.json` (core.tabs.PlotTab / core.session.save_figure_state),
# confirmed against a working export produced by the app itself.
# ---------------------------------------------------------------------- #
def _sig(source_path, x_col, y_col, name, color, *, domain="time", y_kind="voltage",
         unit_t_in="s", unit_v_in="V", gain=1.0, t_offset=0.0, v_offset=0.0,
         invert=False, linestyle="-", marker="None", marker_hollow=False,
         marker_size=5.0, secondary_y=False, legend_label=None, line_width=1.4):
    return {
        "source_path": source_path, "x_col": x_col, "y_col": y_col, "name": name,
        "display_name": None, "legend_label": legend_label,
        "domain": domain, "y_kind": y_kind,
        "unit_t_in": unit_t_in, "unit_v_in": unit_v_in,
        "t_offset": t_offset, "v_offset": v_offset, "gain": gain, "invert": invert,
        "linestyle": linestyle, "marker": marker, "marker_size": marker_size,
        "marker_hollow": marker_hollow, "color": color, "secondary_y": secondary_y,
        "visible": True, "line_width": line_width,
    }


def _settings(*, unit_x="us", unit_y="V", xlabel="", ylabel="", ylabel2="",
              xscale="linear", yscale="linear", xmin="", xmax="",
              legend=True, legend_pos="best", plot_mode="Tiempo / Frecuencia",
              bode_layout="shared", title="", fig_format="pdf"):
    return {
        "unit_x": unit_x, "unit_y": unit_y, "xscale": xscale, "yscale": yscale,
        "xmin": xmin, "xmax": xmax, "engineering_ticks": True, "grid": True,
        "minor_grid": False, "title": title, "xlabel": xlabel, "ylabel": ylabel,
        "ylabel2": ylabel2, "font_family": "LaTeX (Computer Modern)", "font_size": "10",
        "legend_font_size": "", "theme_mode": "Claro", "legend": legend,
        "legend_pos": legend_pos, "legend_x": "1.02", "legend_y": "1.00",
        "legend_corner": "upper left", "legend_ncol": "1", "legend_frameon": True,
        "dec_mode": "none", "dec_value": "1000", "decimal_comma": False,
        "plot_mode": plot_mode, "bode_layout": bode_layout, "fig_format": fig_format,
        "dpi": "300", "csv_mode": "individual", "xy_x": "", "xy_y": "",
        "xy_legend": "", "xy_color": "#8A5A1E",
    }


def export_figure_with_json(fig, out_path_pdf: str, settings: dict, signals: list) -> None:
    """Export step used by every fig_* function: writes the vector PDF via
    core.export.export_figure, then writes the `.labplotter.json` sidecar
    via core.session.save_figure_state -- the app's own mechanism/naming,
    with the real "settings"+"signals" shape (see module docstring)."""
    export_figure(fig, out_path_pdf)
    save_figure_state(out_path_pdf, {"settings": settings, "signals": signals,
                                      "manual_margins": None})


# ---------------------------------------------------------------------- #
# Bode plots (4x): magnitude solid, phase dashed, legend outside the data.
# ---------------------------------------------------------------------- #
def _bode_settings():
    return _settings(unit_x="Hz", xscale="log", ylabel="Ganancia $|H|$ [dB]",
                      ylabel2="Fase $\\angle H$ [$^\\circ$]", plot_mode="Bode",
                      bode_layout="shared")


def _bode_autobode(meas_path, sim_path, out_name):
    """Automatic (Network Analyzer / Autobode) sweep: dense data, so both
    the theoretical tolerance curves and the measured trace are drawn as
    continuous lines (no markers needed). Both files are read via
    core.data_io.read_table -- the same function the app itself uses when
    it reloads a signal from `source_path` -- so the column names recorded
    in the JSON sidecar (x_col/y_col below) are guaranteed to be exactly
    the ones the app will find, instead of a private renaming used only
    for convenience inside this script."""
    meas, _ = read_table(str(meas_path))
    sim, _ = read_table(str(sim_path))
    meas_dev, sim_dev = _device_path(Path(meas_path)), _device_path(Path(sim_path))

    # `sim_path` is the raw LTspice AC-sweep export: each tolerance corner is
    # one column holding LTspice's complex "(mag dB, phase °)" cell format,
    # which read_table decomposes into "<col>_dB"/"<col>_deg"/"<col>_Vlin".
    # V(maximo)=f0 mínimo del rango de tolerancia, V(minimo)=f0 máximo,
    # V(nominal)=f0 nominal (verified numerically against the previous
    # already-decomposed copy of this same data).
    panels = [
        ("Gain (dB)", "V(maximo)_dB", "V(nominal)_dB", "V(minimo)_dB",
         "Ganancia $|H|$ [dB]", MAG_LS, "dB"),
        ("Phase (°)", "V(maximo)_deg", "V(nominal)_deg", "V(minimo)_deg",
         "Fase $\\angle H$ [$^\\circ$]", PHASE_LS, "deg"),
    ]
    fig, (ax_mag, ax_ph) = plt.subplots(1, 2, figsize=(13.0, 4.3))
    signals = []
    for ax, (meas_col, sim_min, sim_nom, sim_max, ylabel, ls, y_kind) in zip(
            (ax_mag, ax_ph), panels):
        is_phase = y_kind == "deg"
        ax.plot(sim["Freq."], sim[sim_min], ls, color=C_MIN, lw=1.3,
                label=r"$f_{0,\mathrm{min}}$")
        ax.plot(sim["Freq."], sim[sim_nom], ls, color=C_NOM, lw=1.3,
                label=r"$f_0$ nominal")
        ax.plot(sim["Freq."], sim[sim_max], ls, color=C_MAX, lw=1.6,
                label=r"$f_{0,\mathrm{max}}$")
        meas_x_col = "Frequency (Hz)" if "Frequency (Hz)" in meas.columns else meas.columns[1]
        ax.plot(meas[meas_x_col], meas[meas_col], ls, color=C_MEAS, lw=1.6,
                label="Medición automática (NA)")
        ax.set_xlabel("Frecuencia [Hz]")
        ax.set_ylabel(ylabel)
        ax.set_xlim(10, 1e6)
        _eng_log_axis(ax)
        ax.legend(loc="best", fontsize=7.5, framealpha=0.9)
        suffix = "_ph" if is_phase else "_mag"
        signals.extend([
            _sig(sim_dev, "Freq.", sim_min, "f0_min" + suffix, C_MIN, domain="freq",
                 y_kind=y_kind, unit_t_in="Hz", unit_v_in=y_kind,
                 linestyle=ls, secondary_y=is_phase, legend_label=r"$f_{0,\mathrm{min}}$"),
            _sig(sim_dev, "Freq.", sim_nom, "f0_nom" + suffix, C_NOM, domain="freq",
                 y_kind=y_kind, unit_t_in="Hz", unit_v_in=y_kind,
                 linestyle=ls, secondary_y=is_phase, legend_label=r"$f_0$ nominal"),
            _sig(sim_dev, "Freq.", sim_max, "f0_max" + suffix, C_MAX, domain="freq",
                 y_kind=y_kind, unit_t_in="Hz", unit_v_in=y_kind,
                 linestyle=ls, secondary_y=is_phase, legend_label=r"$f_{0,\mathrm{max}}$"),
            _sig(meas_dev, meas_x_col, meas_col, "medido" + suffix, C_MEAS,
                 domain="freq", y_kind=y_kind, unit_t_in="Hz", unit_v_in=y_kind,
                 linestyle=ls, secondary_y=is_phase, legend_label="Medición automática (NA)"),
        ])
    fig.tight_layout()
    export_figure_with_json(fig, str(PLOTS_OUT / out_name), _bode_settings(), signals)
    plt.close(fig)


def fig_bode_autobode_pa():
    _bode_autobode(DATA_RAW / "bode-2.csv",
                    DATA_SIM / "bodePasaAltos.txt",
                    "bode_autobode_pa.pdf")


def fig_bode_autobode_pb():
    _bode_autobode(DATA_RAW / "bode-1-e.csv",
                    DATA_SIM / "bodePasaBajos.txt",
                    "bode_autobode_pb.pdf")


# Tabla 1 del Anexo -- manual point-by-point pasabajos measurement (21
# frequencies). Read from the report's own already-existing processed
# tables (data/processed/Bode Manual.csv, .../1g_vr_calculado.csv) rather
# than a copy hardcoded in this script, so there is exactly one authoritative
# source for these numbers and the JSON sidecar's source_path always points
# at a file that is actually on disk. "Gain (dB)" in that table is already
# signed to match the theoretical convention used everywhere else in the
# report (negative = attenuation); "Phase (deg)"/"Phase XY (deg)" are
# recorded as a positive lag magnitude and are negated (`invert=True` below)
# to match phi_PB = -arctan(wRC).
_MANUAL_PB_PATH = DATA_PROCESSED / "Bode Manual.csv"
_VR_CALC_PATH = DATA_PROCESSED / "1g_vr_calculado.csv"


def fig_bode_manual_pb():
    """Sparse manual measurement (21 frequencies, two independent phase
    methods): drawn as markers only, no connecting line, since a solid or
    dashed line would visually claim a continuous sweep that was never
    taken."""
    manual, _ = read_table(str(_MANUAL_PB_PATH))
    sim, _ = read_table(str(DATA_SIM / "bodePasaBajos.txt"))
    sim_dev = _device_path(DATA_SIM / "bodePasaBajos.txt")
    manual_dev = _device_path(_MANUAL_PB_PATH)
    freq, gain_db = manual["Frequency (Hz)"], manual["Gain (dB)"]
    theta_dt, theta_xy = -manual["Phase (deg)"], -manual["Phase XY (deg)"]

    fig, (ax_mag, ax_ph) = plt.subplots(1, 2, figsize=(13.0, 4.3))

    ax_mag.plot(sim["Freq."], sim["V(maximo)_dB"], MAG_LS, color=C_MIN, lw=1.3,
                label=r"$f_{0,\mathrm{min}}$")
    ax_mag.plot(sim["Freq."], sim["V(nominal)_dB"], MAG_LS, color=C_NOM, lw=1.3,
                label=r"$f_0$ nominal")
    ax_mag.plot(sim["Freq."], sim["V(minimo)_dB"], MAG_LS, color=C_MAX, lw=1.6,
                label=r"$f_{0,\mathrm{max}}$")
    ax_mag.plot(freq, gain_db, linestyle="none",
                marker="o", ms=5, mfc="none", mec=C_MEAS, mew=1.3,
                label=r"Medición manual")
    ax_mag.set_ylabel("Ganancia $|H|$ [dB]")

    ax_ph.plot(sim["Freq."], sim["V(maximo)_deg"], PHASE_LS, color=C_MIN, lw=1.3,
               label=r"$f_{0,\mathrm{min}}$")
    ax_ph.plot(sim["Freq."], sim["V(nominal)_deg"], PHASE_LS, color=C_NOM, lw=1.3,
               label=r"$f_0$ nominal")
    ax_ph.plot(sim["Freq."], sim["V(minimo)_deg"], PHASE_LS, color=C_MAX, lw=1.6,
               label=r"$f_{0,\mathrm{max}}$")
    ax_ph.plot(freq, theta_dt, linestyle="none",
               marker="^", ms=5.5, mfc="none", mec=C_MEAS, mew=1.3,
               label=r"Manual ($\Delta t$)")
    ax_ph.plot(freq, theta_xy, linestyle="none",
               marker="s", ms=4.5, mfc="none", mec=C_MEAS, mew=1.3,
               label=r"Manual ($XY$)")
    ax_ph.set_ylabel("Fase $\\angle H$ [$^\\circ$]")

    for ax in (ax_mag, ax_ph):
        ax.set_xlabel("Frecuencia [Hz]")
        ax.set_xlim(10, 1e6)
        _eng_log_axis(ax)
        ax.legend(loc="best", fontsize=7.5, framealpha=0.9)
    fig.tight_layout()

    signals = [
        _sig(sim_dev, "Freq.", "V(maximo)_dB", "f0_min_mag", C_MIN, domain="freq",
             y_kind="dB", unit_t_in="Hz", unit_v_in="dB", linestyle=MAG_LS,
             legend_label=r"$f_{0,\mathrm{min}}$"),
        _sig(sim_dev, "Freq.", "V(nominal)_dB", "f0_nom_mag", C_NOM, domain="freq",
             y_kind="dB", unit_t_in="Hz", unit_v_in="dB", linestyle=MAG_LS,
             legend_label=r"$f_0$ nominal"),
        _sig(sim_dev, "Freq.", "V(minimo)_dB", "f0_max_mag", C_MAX, domain="freq",
             y_kind="dB", unit_t_in="Hz", unit_v_in="dB", linestyle=MAG_LS,
             legend_label=r"$f_{0,\mathrm{max}}$"),
        _sig(manual_dev, "Frequency (Hz)", "Gain (dB)", "medido_mag", C_MEAS, domain="freq",
             y_kind="dB", unit_t_in="Hz", unit_v_in="dB",
             linestyle="None", marker="o", marker_hollow=True, marker_size=5,
             legend_label="Medición manual"),
        _sig(sim_dev, "Freq.", "V(maximo)_deg", "f0_min_ph", C_MIN, domain="freq",
             y_kind="deg", unit_t_in="Hz", unit_v_in="deg", linestyle=PHASE_LS,
             secondary_y=True, legend_label=r"$f_{0,\mathrm{min}}$"),
        _sig(sim_dev, "Freq.", "V(nominal)_deg", "f0_nom_ph", C_NOM, domain="freq",
             y_kind="deg", unit_t_in="Hz", unit_v_in="deg", linestyle=PHASE_LS,
             secondary_y=True, legend_label=r"$f_0$ nominal"),
        _sig(sim_dev, "Freq.", "V(minimo)_deg", "f0_max_ph", C_MAX, domain="freq",
             y_kind="deg", unit_t_in="Hz", unit_v_in="deg", linestyle=PHASE_LS,
             secondary_y=True, legend_label=r"$f_{0,\mathrm{max}}$"),
        _sig(manual_dev, "Frequency (Hz)", "Phase (deg)", "medido_ph_dt", C_MEAS, domain="freq",
             y_kind="deg", unit_t_in="Hz", unit_v_in="deg", invert=True, secondary_y=True,
             linestyle="None", marker="^", marker_hollow=True, marker_size=5.5,
             legend_label=r"Manual ($\Delta t$)"),
        _sig(manual_dev, "Frequency (Hz)", "Phase XY (deg)", "medido_ph_xy", C_MEAS, domain="freq",
             y_kind="deg", unit_t_in="Hz", unit_v_in="deg", invert=True, secondary_y=True,
             linestyle="None", marker="s", marker_hollow=True, marker_size=4.5,
             legend_label=r"Manual ($XY$)"),
    ]
    export_figure_with_json(fig, str(PLOTS_OUT / "bode_manual_pb.pdf"),
                             _bode_settings(), signals)
    plt.close(fig)


def fig_vr_medido():
    """|V_R| already computed (law of cosines on the V/V_C phasor triangle)
    in the report's own data/processed/1g_vr_calculado.csv. Same 21 sparse
    frequencies as the manual Bode figure, so markers only, no connecting
    line."""
    vr, _ = read_table(str(_VR_CALC_PATH))
    vr_dev = _device_path(_VR_CALC_PATH)

    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    ax.plot(vr["f_Hz"], vr["Vr_calc_mV"], linestyle="none", marker="o", ms=5,
            mfc="none", mec=C_MEAS, mew=1.3, label=r"$|V_R|$ medido")
    ax.set_xlabel("Frecuencia [Hz]")
    ax.set_ylabel(r"$|V_R|$ [mV]")
    ax.set_xlim(10, 1e6)
    _eng_log_axis(ax)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    fig.tight_layout()

    settings = _settings(unit_x="Hz", unit_y="mV", xscale="log",
                          ylabel=r"$|V_R|$ [mV]", plot_mode="Tiempo / Frecuencia",
                          legend_pos="upper left")
    signals = [
        _sig(vr_dev, "f_Hz", "Vr_calc_mV", "vr_medido", C_MEAS, domain="freq",
             y_kind="voltage", unit_t_in="Hz", unit_v_in="mV",
             linestyle="None", marker="o", marker_hollow=True, marker_size=5,
             legend_label=r"$|V_R|$ medido"),
    ]
    export_figure_with_json(fig, str(PLOTS_OUT / "vr_medido.pdf"), settings, signals)
    plt.close(fig)


# ---------------------------------------------------------------------- #
# Time-domain helpers: settle/phase alignment and period auto-detection.
# ---------------------------------------------------------------------- #
_PERIOD_US = {
    r"$f=f_0/20$": 1e6 / (F0_NOM / 20),
    r"$f=f_0$": 1e6 / F0_NOM,
    r"$f=20f_0$": 1e6 / (F0_NOM * 20),
}


def _align_rising_edge(t_us: np.ndarray, vin: np.ndarray, *series: np.ndarray,
                        period_us: float, show_periods: float = 2.5):
    """Shift the time axis so t=0 sits on a rising zero-crossing of `vin`
    (mid-level threshold), then crop to `show_periods`. Anchored to the
    *end* of the available data (closest to a periodic steady state --
    see the long-form explanation kept in git history) rather than
    skipping a fixed number of periods from t=0.

    Returns `(t_shifted, vin_cropped, *series_cropped, t0)` -- `t0` (the
    same units as `t_us`, i.e. microseconds in every caller here) is handed
    back so callers can record the exact alignment shift and crop window
    in the JSON sidecar (`t_offset` / `settings.xmin`+`xmax`) instead of
    only baking it into the plotted arrays."""
    window_us = (show_periods + 1.0) * period_us
    t_end = t_us.max()
    search_from = max(t_us.min(), t_end - window_us)
    level = (np.max(vin) + np.min(vin)) / 2.0
    above = vin >= level
    candidates = np.where((t_us[1:] >= search_from) & above[1:] & ~above[:-1])[0]
    t0 = t_us[candidates[0] + 1] if len(candidates) else search_from
    t1 = t0 + show_periods * period_us
    mask = (t_us >= t0) & (t_us <= min(t1, t_us.max()))
    return (t_us[mask] - t0, vin[mask], *[s[mask] for s in series], t0)


def _estimate_period_us(t_us: np.ndarray, v: np.ndarray) -> float:
    """Median spacing between rising zero-crossings, for signals whose
    test frequency is not one of the three fixed f0-relative points."""
    level = (np.max(v) + np.min(v)) / 2.0
    above = v >= level
    idx = np.where(above[1:] & ~above[:-1])[0] + 1
    if len(idx) < 2:
        return float(t_us.max() - t_us.min())
    return float(np.median(np.diff(t_us[idx])))


def fig_pb_cuadrada_combinado():
    """Pasabajos ante entrada cuadrada: simulated (LTspice, dashed) vs.
    measured (solid) v_C(t) overlaid per frequency. Sim columns verified
    numerically: V(n001)=input, V(n002)=output."""
    sim_files = {
        r"$f=f_0/20$": "1f_0.05Fo.txt",
        r"$f=f_0$": "1f_1Fo.txt",
        r"$f=20f_0$": "1f_20Fo.txt",
    }
    meas_files = {
        r"$f=f_0/20$": "1-f-005f0.csv",
        r"$f=f_0$": "1-f-f0.csv",
        r"$f=20f_0$": "1-f-20f0.csv",
    }
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.2))
    signals = []
    for ax, title in zip(axes, sim_files):
        tag = title.strip("$").replace("=", "_").replace("/", "").replace("_0", "0")
        sim_path = DATA_SIM / sim_files[title]
        dfs = pd.read_csv(sim_path, sep=r"\s+", engine="python")
        t_sim = dfs["time"].to_numpy() * 1e6
        vin_sim, vout_sim = dfs["V(n001)"].to_numpy(), dfs["V(n002)"].to_numpy()
        t_sim, vin_sim, vout_sim, t0_sim = _align_rising_edge(
            t_sim, vin_sim, vout_sim, period_us=_PERIOD_US[title])

        meas_path = DATA_RAW / meas_files[title]
        dfm, _ = read_table(str(meas_path))
        t_col, ch1, ch2 = dfm.columns[0], dfm.columns[1], dfm.columns[2]
        t_meas = dfm[t_col].to_numpy() * 1e6
        vin_meas = dfm[ch1].to_numpy() * MEAS_GAIN
        vout_meas = dfm[ch2].to_numpy() * MEAS_GAIN
        t_meas, vin_meas, vout_meas, t0_meas = _align_rising_edge(
            t_meas, vin_meas, vout_meas, period_us=_PERIOD_US[title])

        in_gain = 1.0
        in_label = r"$v(t)$"
        if title == r"$f=20f_0$":
            # Same convention as before: at 20f0 the input is scaled down
            # further so it shares a visual range with the attenuated output.
            vin_sim, vin_meas, in_label, in_gain = (
                vin_sim / 10.0, vin_meas / 10.0, r"$v(t)/10$", 0.1)

        ax.plot(t_meas, vin_meas, MEAS_LS, color=C_MIN, lw=1.0, label=in_label)
        ax.plot(t_sim, vout_sim, SIM_LS, color=C_NOM, lw=1.3, label=r"$v_C$ sim.")
        ax.plot(t_meas, vout_meas, MEAS_LS, color=C_MEAS, lw=1.1, label=r"$v_C$ medido")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel(r"Tiempo [$\mu$s]")
        ax.grid(True, alpha=0.5, lw=0.4)
        ax.legend(loc="best", fontsize=7, framealpha=0.9)

        window_us = 2.5 * _PERIOD_US[title]
        sim_dev, meas_dev = _device_path(sim_path), _device_path(meas_path)
        signals.extend([
            _sig(meas_dev, t_col, ch1, f"vin_meas_{tag}", C_MIN, gain=MEAS_GAIN * in_gain,
                 t_offset=-t0_meas * 1e-6, linestyle=MEAS_LS, legend_label=in_label),
            _sig(sim_dev, "time", "V(n002)", f"vout_sim_{tag}", C_NOM, gain=1.0,
                 t_offset=-t0_sim * 1e-6, linestyle=SIM_LS, legend_label=r"$v_C$ sim."),
            _sig(meas_dev, t_col, ch2, f"vout_meas_{tag}", C_MEAS, gain=MEAS_GAIN,
                 t_offset=-t0_meas * 1e-6, linestyle=MEAS_LS, legend_label=r"$v_C$ medido"),
        ])
    axes[0].set_ylabel("Tensión [V]")
    fig.tight_layout()
    settings = _settings(xlabel=r"Tiempo [$\mu$s]", ylabel="Tensión [V]", xmin="0")
    export_figure_with_json(fig, str(PLOTS_OUT / "pb_cuadrada_combinado.pdf"),
                             settings, signals)
    plt.close(fig)


def fig_pa_triangular_combinado():
    """Sim columns verified numerically: V(n001)=input, V(nominal)=output.
    A secondary y-axis is used for f=f0/20, where v_R(t) is far smaller
    than v(t) (derivator regime); both the simulated and measured output
    share that same twin axis so their amplitudes stay comparable."""
    sim_files = {
        r"$f=f_0/20$": "2e_0.05Fo.txt",
        r"$f=f_0$": "2e_Fo.txt",
        r"$f=20f_0$": "2e_20Fo.txt",
    }
    meas_files = {
        r"$f=f_0/20$": "2-b-005f0.csv",
        r"$f=f_0$": "2-b-f0.csv",
        r"$f=20f_0$": "2-b-200k.csv",
    }
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.2))
    signals = []
    for ax, title in zip(axes, sim_files):
        tag = title.strip("$").replace("=", "_").replace("/", "").replace("_0", "0")
        sim_path = DATA_SIM / sim_files[title]
        dfs = pd.read_csv(sim_path, sep=r"\s+", engine="python")
        t_sim = dfs["time"].to_numpy() * 1e6
        vin_sim, vout_sim = dfs["V(n001)"].to_numpy(), dfs["V(nominal)"].to_numpy()
        t_sim, vin_sim, vout_sim, t0_sim = _align_rising_edge(
            t_sim, vin_sim, vout_sim, period_us=_PERIOD_US[title])

        meas_path = DATA_RAW / meas_files[title]
        dfm, _ = read_table(str(meas_path))
        t_col, ch1, ch2 = dfm.columns[0], dfm.columns[1], dfm.columns[2]
        t_meas = dfm[t_col].to_numpy() * 1e6
        vin_meas = dfm[ch1].to_numpy() * MEAS_GAIN
        vout_meas = dfm[ch2].to_numpy() * MEAS_GAIN
        t_meas, vin_meas, vout_meas, t0_meas = _align_rising_edge(
            t_meas, vin_meas, vout_meas, period_us=_PERIOD_US[title])

        use_twin = title == r"$f=f_0/20$"
        l1, = ax.plot(t_meas, vin_meas, MEAS_LS, color=C_MIN, lw=1.0, label=r"$v(t)$")
        if use_twin:
            ax2 = ax.twinx()
            l2, = ax2.plot(t_sim, vout_sim, SIM_LS, color=C_NOM, lw=1.2, label=r"$v_R$ sim.")
            l3, = ax2.plot(t_meas, vout_meas, MEAS_LS, color=C_MEAS, lw=1.0, label=r"$v_R$ medido")
            ax.set_ylabel(r"$v(t)$ [V]")
            ax2.set_ylabel(r"$v_R(t)$ [V]")
            ax.legend(handles=[l1, l2, l3], loc="best", fontsize=6.5, framealpha=0.9)
        else:
            l2, = ax.plot(t_sim, vout_sim, SIM_LS, color=C_NOM, lw=1.2, label=r"$v_R$ sim.")
            l3, = ax.plot(t_meas, vout_meas, MEAS_LS, color=C_MEAS, lw=1.0, label=r"$v_R$ medido")
            ax.set_ylabel("Tensión [V]")
            ax.legend(loc="best", fontsize=7, framealpha=0.9)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel(r"Tiempo [$\mu$s]")
        ax.grid(True, alpha=0.5, lw=0.4)

        sim_dev, meas_dev = _device_path(sim_path), _device_path(meas_path)
        signals.extend([
            _sig(meas_dev, t_col, ch1, f"vin_meas_{tag}", C_MIN, gain=MEAS_GAIN,
                 t_offset=-t0_meas * 1e-6, linestyle=MEAS_LS, legend_label=r"$v(t)$"),
            _sig(sim_dev, "time", "V(nominal)", f"vout_sim_{tag}", C_NOM, gain=1.0,
                 t_offset=-t0_sim * 1e-6, linestyle=SIM_LS, secondary_y=use_twin,
                 legend_label=r"$v_R$ sim."),
            _sig(meas_dev, t_col, ch2, f"vout_meas_{tag}", C_MEAS, gain=MEAS_GAIN,
                 t_offset=-t0_meas * 1e-6, linestyle=MEAS_LS, secondary_y=use_twin,
                 legend_label=r"$v_R$ medido"),
        ])
    fig.tight_layout()
    settings = _settings(xlabel=r"Tiempo [$\mu$s]", ylabel="Tensión [V]",
                          ylabel2=r"$v_R(t)$ [V]", xmin="0")
    export_figure_with_json(fig, str(PLOTS_OUT / "pa_triangular_combinado.pdf"),
                             settings, signals)
    plt.close(fig)


def fig_pb_practica_ab():
    """v(t)/v_C(t) measurement used to determine f0_real (item a) and the
    i/v_C phase angle (item b): measured only, no simulation counterpart,
    so both traces are solid (there is nothing to contrast a dashed line
    against here)."""
    meas_path = DATA_RAW / "1_a-b.csv"
    df, _ = read_table(str(meas_path))
    t_col, ch1, ch2 = df.columns[0], df.columns[1], df.columns[2]
    t_us_full = df[t_col].to_numpy() * 1e6
    vin_full, vout_full = df[ch1].to_numpy(), df[ch2].to_numpy()
    period_us = _estimate_period_us(t_us_full, vin_full)
    t_us, vin, vout, t0 = _align_rising_edge(t_us_full, vin_full, vout_full,
                                              period_us=period_us)

    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    ax.plot(t_us, vin, "-", color=C_MIN, lw=1.2, label=r"$v(t)$")
    ax.plot(t_us, vout, "-", color=C_MEAS, lw=1.2, label=r"$v_C(t)$")
    ax.set_xlabel(r"Tiempo [$\mu$s]")
    ax.set_ylabel("Tensión [V]")
    ax.grid(True, alpha=0.5, lw=0.4)
    ax.legend(loc="best", fontsize=8, framealpha=0.9)
    fig.tight_layout()

    meas_dev = _device_path(meas_path)
    settings = _settings(xlabel=r"Tiempo [$\mu$s]", ylabel="Tensión [V]", xmin="0")
    signals = [
        _sig(meas_dev, t_col, ch1, "v_in", C_MIN, t_offset=-t0 * 1e-6,
             legend_label=r"$v(t)$"),
        _sig(meas_dev, t_col, ch2, "v_c", C_MEAS, t_offset=-t0 * 1e-6,
             legend_label=r"$v_C(t)$"),
    ]
    export_figure_with_json(fig, str(PLOTS_OUT / "pb_practica_ab.pdf"), settings, signals)
    plt.close(fig)


def fig_sondas_x1_x10():
    """Probe-tip-only response (no external capacitor), x1 vs x10
    attenuation: two independent measured-only captures, one panel each."""
    files = {"Punta $\\times1$": "1-h-x1.csv", "Punta $\\times10$": "1-h-x10.csv"}
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.4))
    signals = []
    for ax, (title, fname) in zip(axes, files.items()):
        tag = "x1" if "1" in fname.split("x")[-1] else "x10"
        meas_path = DATA_RAW / fname
        df, _ = read_table(str(meas_path))
        t_col, ch1, ch2 = df.columns[0], df.columns[1], df.columns[2]
        t_us_full = df[t_col].to_numpy() * 1e6
        vin_full, vout_full = df[ch1].to_numpy(), df[ch2].to_numpy()
        period_us = _estimate_period_us(t_us_full, vin_full)
        t_us, vin, vout, t0 = _align_rising_edge(t_us_full, vin_full, vout_full,
                                                  period_us=period_us)
        ax.plot(t_us, vin, "-", color=C_MIN, lw=1.1, label=r"$v(t)$")
        ax.plot(t_us, vout, "-", color=C_MEAS, lw=1.1, label=r"$v_\mathrm{med}(t)$")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel(r"Tiempo [$\mu$s]")
        ax.grid(True, alpha=0.5, lw=0.4)
        ax.legend(loc="best", fontsize=7.5, framealpha=0.9)

        meas_dev = _device_path(meas_path)
        signals.extend([
            _sig(meas_dev, t_col, ch1, f"v_in_{tag}", C_MIN, t_offset=-t0 * 1e-6,
                 legend_label=r"$v(t)$"),
            _sig(meas_dev, t_col, ch2, f"v_med_{tag}", C_MEAS, t_offset=-t0 * 1e-6,
                 legend_label=r"$v_\mathrm{med}(t)$"),
        ])
    axes[0].set_ylabel("Tensión [V]")
    fig.tight_layout()
    settings = _settings(xlabel=r"Tiempo [$\mu$s]", ylabel="Tensión [V]", xmin="0")
    export_figure_with_json(fig, str(PLOTS_OUT / "sondas_x1_x10.pdf"), settings, signals)
    plt.close(fig)


def fig_triangular_aislada():
    """Single isolated triangular half-cycle captured via hold-off
    triggering: by construction this is one feature, not a periodic
    signal, so it is shown whole rather than cropped to N cycles."""
    meas_path = DATA_RAW / "5-triangulo-modo1.csv"
    df, _ = read_table(str(meas_path))
    df = df.dropna()
    t_col, ch1 = df.columns[0], df.columns[1]
    t_us = df[t_col].to_numpy() * 1e6
    v = df[ch1].to_numpy()

    fig, ax = plt.subplots(figsize=(5.5, 3.2))
    ax.plot(t_us, v, "-", color=C_NOM, lw=1.3, label=r"Semiciclo triangular")
    ax.set_xlabel(r"Tiempo [$\mu$s]")
    ax.set_ylabel("Tensión [V]")
    ax.grid(True, alpha=0.5, lw=0.4)
    ax.legend(loc="best", fontsize=8, framealpha=0.9)
    fig.tight_layout()

    meas_dev = _device_path(meas_path)
    settings = _settings(xlabel=r"Tiempo [$\mu$s]", ylabel="Tensión [V]")
    signals = [
        _sig(meas_dev, t_col, ch1, "semiciclo_triangular", C_NOM,
             legend_label="Semiciclo triangular"),
    ]
    export_figure_with_json(fig, str(PLOTS_OUT / "triangular_aislada.pdf"), settings, signals)
    plt.close(fig)


# ---------------------------------------------------------------------- #
# Sweep (sincronismo): left untouched per instructions -- but the JSON
# sidecar is still generated, since that is a change to the export step
# itself, uniformly applied, not to this figure's content/style.
# ---------------------------------------------------------------------- #
def fig_sweep_combinado():
    meas_path = DATA_RAW / "3.csv"
    df, kinds = read_table(str(meas_path))
    t_col, ch1, ch2 = df.columns[0], df.columns[1], df.columns[2]
    t = df[t_col].to_numpy()
    mask = t >= 0
    t_us = t[mask] * 1e6
    ramp = df[ch1].to_numpy()[mask]
    sweep = df[ch2].to_numpy()[mask]

    fig, (ax_t, ax_xy) = plt.subplots(1, 2, figsize=(11.5, 3.6))

    ax_t.plot(t_us, ramp, "-", color=C_MIN, lw=1.1, label=r"$f(t)=t$")
    ax_t.plot(t_us, sweep, "-", color=C_NOM, lw=1.0, label="Sweep")
    ax_t.set_xlabel(r"Tiempo [$\mu$s]")
    ax_t.set_ylabel("Tensión [V]")
    ax_t.grid(True, alpha=0.5, lw=0.4)
    ax_t.legend(loc="upper left", fontsize=8)

    ax_xy.plot(ramp, sweep, "-", color=C_NOM, lw=1.0, label=r"Sweep vs $f(t)=t$")
    ax_xy.set_xlabel(r"$f(t)=t$ [V]")
    ax_xy.set_ylabel("Sweep [V]")
    ax_xy.grid(True, alpha=0.5, lw=0.4)
    ax_xy.legend(loc="upper left", fontsize=8)

    fig.tight_layout()

    meas_dev = _device_path(meas_path)
    settings = _settings(xlabel=r"Tiempo [$\mu$s]", ylabel="Tensión [V]", xmin="0")
    signals = [
        _sig(meas_dev, t_col, ch1, "ramp", C_MIN, legend_label=r"$f(t)=t$"),
        _sig(meas_dev, t_col, ch2, "sweep", C_NOM, legend_label="Sweep"),
    ]
    export_figure_with_json(fig, str(PLOTS_OUT / "sweep_combinado.pdf"), settings, signals)
    plt.close(fig)


def main():
    set_publication_style("LaTeX (Computer Modern)", base_fontsize=10)
    fig_bode_manual_pb()
    fig_bode_autobode_pb()
    fig_bode_autobode_pa()
    fig_vr_medido()
    fig_pb_practica_ab()
    fig_sondas_x1_x10()
    fig_triangular_aislada()
    fig_pb_cuadrada_combinado()
    fig_pa_triangular_combinado()
    fig_sweep_combinado()
    print("Listo. Figuras regeneradas en:", PLOTS_OUT)


if __name__ == "__main__":
    main()
