"""
gui/theme.py
------------
Monochrome (greyscale) visual identity for the whole CustomTkinter chrome.

Design decision -- only the *application* chrome is desaturated. The Matplotlib
figure keeps a white, publication-grade background and full-color traces
because the on-screen canvas is exactly what gets exported to PDF/PGF for the
LaTeX report (see `core.export.set_publication_style`: same rcParams for
preview and export). Recolouring the preview would silently break that
guarantee.

Two layers are applied, in this order:

1. A greyscale pass over the whole active CustomTkinter theme dictionary. Any
   key containing "color" is converted to its Rec.709 luminance equivalent.
   This guarantees full coverage regardless of the CustomTkinter version, so a
   widget class added in a future release still comes out monochrome.
2. An explicit override table for the widget classes that carry the visual
   hierarchy (surfaces, borders, accents), so contrast is designed rather than
   accidental.

Every colour is declared as a [light, dark] pair, which makes
`ctk.set_appearance_mode()` switch between two monochrome variants live,
without rebuilding the widget tree.
"""

from __future__ import annotations

from typing import Any

import customtkinter as ctk
from matplotlib import colors as mcolors

# --------------------------------------------------------------------------- #
# Palettes
# --------------------------------------------------------------------------- #
LIGHT: dict[str, str] = {
    "bg":           "#E9E9EB",   # window background
    "surface":      "#F6F6F7",   # panels
    "surface_alt":  "#DEDFE2",   # inputs, scrollable frames
    "border":       "#C1C3C7",
    "fg":           "#17181A",
    "fg_muted":     "#65676C",
    "accent":       "#3B3E43",   # buttons / active controls
    "accent_hover": "#53575E",
    "on_accent":    "#FFFFFF",
}

DARK: dict[str, str] = {
    "bg":           "#141516",
    "surface":      "#1E1F22",
    "surface_alt":  "#282A2E",
    "border":       "#3A3D43",
    "fg":           "#E6E7E9",
    "fg_muted":     "#9A9CA1",
    "accent":       "#454951",
    "accent_hover": "#596069",
    "on_accent":    "#F4F5F6",
}

# Matplotlib chrome: print-oriented, independent of the UI appearance mode.
PLOT_CHROME: dict[str, str] = {
    "figure_face": "#FFFFFF",
    "axes_face":   "#FFFFFF",
    "spine":       "#3A3A3A",
    "tick":        "#3A3A3A",
    "text":        "#1A1A1A",
    "grid":        "#B5B5B5",
}


def _pair(token: str) -> list[str]:
    """[light, dark] value for a palette token."""
    return [LIGHT[token], DARK[token]]


# --------------------------------------------------------------------------- #
# Greyscale pass
# --------------------------------------------------------------------------- #
def _greyscale(value: str) -> str:
    """Rec.709 luminance equivalent of any Matplotlib-parsable colour."""
    try:
        r, g, b = mcolors.to_rgb(value)
    except (ValueError, TypeError):
        return value                     # "transparent" and friends
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return mcolors.to_hex((lum, lum, lum))


def _desaturate(node: Any, key: str = "") -> Any:
    """Recursively convert every colour entry of a theme dictionary."""
    if isinstance(node, dict):
        return {k: _desaturate(v, k) for k, v in node.items()}
    if "color" not in key.lower():
        return node
    if isinstance(node, str):
        return _greyscale(node)
    if isinstance(node, (list, tuple)):
        return [(_greyscale(v) if isinstance(v, str) else v) for v in node]
    return node


# --------------------------------------------------------------------------- #
# Explicit hierarchy overrides
# --------------------------------------------------------------------------- #
def _overrides() -> dict[str, dict[str, Any]]:
    return {
        "CTk": {"fg_color": _pair("bg")},
        "CTkToplevel": {"fg_color": _pair("bg")},
        "CTkFrame": {
            "fg_color": _pair("surface"),
            "top_fg_color": _pair("surface_alt"),
            "border_color": _pair("border"),
        },
        "CTkButton": {
            "fg_color": _pair("accent"),
            "hover_color": _pair("accent_hover"),
            "border_color": _pair("border"),
            "text_color": _pair("on_accent"),
            "text_color_disabled": _pair("fg_muted"),
        },
        "CTkLabel": {"fg_color": "transparent", "text_color": _pair("fg")},
        "CTkEntry": {
            "fg_color": _pair("surface_alt"),
            "border_color": _pair("border"),
            "text_color": _pair("fg"),
            "placeholder_text_color": _pair("fg_muted"),
        },
        "CTkCheckBox": {
            "fg_color": _pair("accent"),
            "border_color": _pair("border"),
            "hover_color": _pair("accent_hover"),
            "checkmark_color": _pair("on_accent"),
            "text_color": _pair("fg"),
            "text_color_disabled": _pair("fg_muted"),
        },
        "CTkRadioButton": {
            "fg_color": _pair("accent"),
            "border_color": _pair("border"),
            "hover_color": _pair("accent_hover"),
            "text_color": _pair("fg"),
            "text_color_disabled": _pair("fg_muted"),
        },
        "CTkSwitch": {
            "fg_color": _pair("border"),
            "progress_color": _pair("accent"),
            "button_color": _pair("fg_muted"),
            "button_hover_color": _pair("fg"),
            "text_color": _pair("fg"),
        },
        "CTkComboBox": {
            "fg_color": _pair("surface_alt"),
            "border_color": _pair("border"),
            "button_color": _pair("accent"),
            "button_hover_color": _pair("accent_hover"),
            "text_color": _pair("fg"),
            "text_color_disabled": _pair("fg_muted"),
        },
        "CTkOptionMenu": {
            "fg_color": _pair("surface_alt"),
            "button_color": _pair("accent"),
            "button_hover_color": _pair("accent_hover"),
            "text_color": _pair("fg"),
            "text_color_disabled": _pair("fg_muted"),
        },
        "DropdownMenu": {
            "fg_color": _pair("surface_alt"),
            "hover_color": _pair("border"),
            "text_color": _pair("fg"),
        },
        "CTkScrollableFrame": {"label_fg_color": _pair("surface_alt")},
        "CTkScrollbar": {
            "fg_color": "transparent",
            "button_color": _pair("border"),
            "button_hover_color": _pair("fg_muted"),
        },
        "CTkSlider": {
            "fg_color": _pair("border"),
            "progress_color": _pair("accent"),
            "button_color": _pair("fg_muted"),
            "button_hover_color": _pair("fg"),
        },
        "CTkProgressBar": {
            "fg_color": _pair("border"),
            "progress_color": _pair("accent"),
            "border_color": _pair("border"),
        },
        "CTkSegmentedButton": {
            "fg_color": _pair("surface_alt"),
            "selected_color": _pair("accent"),
            "selected_hover_color": _pair("accent_hover"),
            "unselected_color": _pair("surface_alt"),
            "unselected_hover_color": _pair("border"),
            "text_color": _pair("fg"),
            "text_color_disabled": _pair("fg_muted"),
        },
        "CTkTextbox": {
            "fg_color": _pair("surface_alt"),
            "border_color": _pair("border"),
            "text_color": _pair("fg"),
            "scrollbar_button_color": _pair("border"),
            "scrollbar_button_hover_color": _pair("fg_muted"),
        },
        "CTkTabview": {
            "fg_color": _pair("surface"),
            "border_color": _pair("border"),
            "segmented_button_fg_color": _pair("surface_alt"),
            "segmented_button_selected_color": _pair("accent"),
            "segmented_button_selected_hover_color": _pair("accent_hover"),
            "segmented_button_unselected_color": _pair("surface_alt"),
            "segmented_button_unselected_hover_color": _pair("border"),
            "text_color": _pair("fg"),
            "text_color_disabled": _pair("fg_muted"),
        },
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def apply_monochrome_theme(mode: str = "dark") -> None:
    """
    Install the monochrome theme.

    Must run **before** the first widget is created (CustomTkinter reads the
    theme dictionary at widget construction time), i.e. before `App()` is
    instantiated -- see `gui/app.main()`.
    """
    ctk.set_default_color_theme("blue")          # baseline structure

    theme = getattr(ctk.ThemeManager, "theme", None)
    if not isinstance(theme, dict):              # unexpected CTk build
        ctk.set_appearance_mode(_appearance(mode))
        return

    desaturated = _desaturate(theme)
    theme.clear()
    theme.update(desaturated)

    for widget, values in _overrides().items():
        target = theme.get(widget)
        if not isinstance(target, dict):
            continue                             # class absent in this version
        for key, value in values.items():
            if key in target:                    # never invent new theme keys
                target[key] = value

    ctk.set_appearance_mode(_appearance(mode))


def set_theme_mode(mode: str) -> None:
    """
    Switch between the light and dark monochrome variants at runtime.

    Both variants are stored in the same theme dictionary, so this restyles
    the existing widget tree with no rebuild.
    """
    ctk.set_appearance_mode(_appearance(mode))


def _appearance(mode: str) -> str:
    normalized = (mode or "").strip().lower()
    if normalized.startswith("l") or normalized in ("claro", "light"):
        return "Light"
    if normalized.startswith("s") or normalized == "system":
        return "System"
    return "Dark"


def apply_plot_chrome(fig) -> None:
    """
    Neutral, print-oriented chrome for the figure: white canvas, dark-grey
    spines/ticks/text, light-grey gridlines.

    Only colours are touched. Grid visibility, scales, locators and formatters
    stay exactly as `App.update_plot()` configured them, so this is safe to
    call at the end of every redraw.
    """
    try:
        fig.patch.set_facecolor(PLOT_CHROME["figure_face"])
    except AttributeError:
        return

    for ax in fig.axes:
        ax.set_facecolor(PLOT_CHROME["axes_face"])
        for spine in ax.spines.values():
            spine.set_color(PLOT_CHROME["spine"])
        ax.tick_params(axis="both", which="both",
                       colors=PLOT_CHROME["tick"],
                       labelcolor=PLOT_CHROME["text"])
        ax.xaxis.label.set_color(PLOT_CHROME["text"])
        ax.yaxis.label.set_color(PLOT_CHROME["text"])
        if ax.get_title():
            ax.title.set_color(PLOT_CHROME["text"])
        try:
            for gridline in ax.get_xgridlines() + ax.get_ygridlines():
                gridline.set_color(PLOT_CHROME["grid"])
            for axis in (ax.xaxis, ax.yaxis):
                for tick in axis.get_minor_ticks():
                    tick.gridline.set_color(PLOT_CHROME["grid"])
        except (AttributeError, TypeError):
            pass                                  # older Matplotlib: skip grid


def style_matplotlib_toolbar(toolbar, mode: str = "dark") -> None:
    """
    Repaint the classic `NavigationToolbar2Tk`, which is plain Tk and does not
    follow the CustomTkinter theme.

    Everything is wrapped defensively: the toolbar internals are private API
    and differ between Matplotlib releases, so a failure here must never stop
    the application from starting.
    """
    palette = DARK if _appearance(mode) == "Dark" else LIGHT
    background, foreground = palette["surface"], palette["fg"]

    widgets = [toolbar]
    try:
        widgets.append(toolbar.master)
        widgets.extend(toolbar.winfo_children())
    except Exception:
        pass

    for widget in widgets:
        for option, value in (("background", background),
                              ("foreground", foreground),
                              ("highlightbackground", background),
                              ("activebackground", palette["surface_alt"])):
            try:
                widget.configure(**{option: value})
            except Exception:
                continue   # option unsupported by this widget class
