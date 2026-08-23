"""
gui/theme.py
------------
Visual identity: a light, warm-neutral, square-cornered "laboratory paper"
look. Two layers, applied in this order:

1. A greyscale pass over the whole active CustomTkinter theme dictionary
   (Rec.709 luminance on any key containing "color"), so a widget class added
   by a future CustomTkinter release still comes out monochrome.
2. An explicit override table for the classes that carry the visual
   hierarchy, plus `corner_radius = 0` and `border_width = 1` everywhere, so
   the chrome reads as ruled paper rather than as rounded cards.

Colour is spent in exactly one place: the plotted traces. Everything else is
neutral. The Matplotlib figure keeps a paper-white background because the
canvas is what gets exported to PDF/PGF for the report -- recolouring the
preview would break the preview/export equivalence guaranteed by
`core.export.set_publication_style`.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

import customtkinter as ctk
from matplotlib import colors as mcolors

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #
LIGHT: dict[str, str] = {
    "app":        "#F6F5F2",   # window background / canvas gutter
    "panel":      "#F0EFEB",   # side panels
    "bar":        "#FAF9F6",   # top bar, toolbars
    "surface":    "#FFFFFF",   # entries, cards, plot paper
    "rule":       "#E3E1DB",   # hairlines between fields
    "border":     "#D3D0C8",   # widget outlines
    "border_str": "#B9B5AB",   # emphasised outlines
    "fg":         "#23211D",
    "fg_muted":   "#6E6B63",
    "fg_faint":   "#9C988E",
    "accent":     "#2A2724",   # selected segment / primary button fill
    "accent_hi":  "#43403A",
    "on_accent":  "#F6F5F2",
    "sel":        "#E4E2DB",   # selected row background
}

DARK: dict[str, str] = {
    "app":        "#191817",
    "panel":      "#1F1E1C",
    "bar":        "#232220",
    "surface":    "#2A2825",
    "rule":       "#343230",
    "border":     "#3D3A36",
    "border_str": "#54504A",
    "fg":         "#E9E7E2",
    "fg_muted":   "#9C988E",
    "fg_faint":   "#75716A",
    "accent":     "#E9E7E2",
    "accent_hi":  "#FFFFFF",
    "on_accent":  "#1F1E1C",
    "sel":        "#302E2B",
}

# Matplotlib chrome: print-oriented, identical in both appearance modes.
PLOT_CHROME: dict[str, str] = {
    "figure_face": "#FFFFFF",
    "axes_face":   "#FFFFFF",
    "spine":       "#4A473F",
    "tick":        "#4A473F",
    "text":        "#23211D",
    "grid":        "#DCDAD3",
}

# Trace colours: muted, print-safe, distinguishable in greyscale by value.
TRACE_CYCLE: list[str] = [
    "#8C2F2F",   # oxide red
    "#2E5E43",   # bottle green
    "#2B4C74",   # ink blue
    "#8A5A1E",   # ochre
    "#5B3E72",   # aubergine
    "#1F6C70",   # teal
    "#7A2B54",   # plum
    "#4A5320",   # olive
]

RADIUS = 0          # square corners, everywhere
BORDER = 1          # hairline outlines

# Character-level letterspacing for small-caps headers. Tk has no letter
# spacing property, so a thin space (U+2009) is interleaved instead.
LETTERSPACING = True
_THIN = "\u2009"


def spaced(text: str) -> str:
    """Small-caps header string with optical letterspacing."""
    if not LETTERSPACING or len(text) < 2:
        return text.upper()
    return _THIN.join(text.upper())


def col(token: str) -> tuple[str, str]:
    """(light, dark) colour pair for a palette token; CustomTkinter resolves it."""
    return (LIGHT[token], DARK[token])


# --------------------------------------------------------------------------- #
# Typography
# --------------------------------------------------------------------------- #
# Preference order per role. The first family actually installed wins, so the
# same code gives a coherent result on Windows, macOS and Linux.
_FAMILY_CANDIDATES: dict[str, Sequence[str]] = {
    "serif": ("Georgia", "Times New Roman", "Liberation Serif", "DejaVu Serif", "serif"),
    "sans":  ("Segoe UI", "Inter", "Helvetica Neue", "Liberation Sans", "DejaVu Sans", "sans-serif"),
    "mono":  ("Consolas", "SF Mono", "Liberation Mono", "DejaVu Sans Mono", "monospace"),
}
_resolved: dict[str, str] = {}


def family(role: str = "serif") -> str:
    """Resolve a font role to an installed family name (cached)."""
    if role in _resolved:
        return _resolved[role]
    candidates = _FAMILY_CANDIDATES.get(role, _FAMILY_CANDIDATES["serif"])
    installed: set[str] = set()
    try:
        import tkinter.font as tkfont
        installed = {name.lower() for name in tkfont.families()}
    except Exception:
        pass   # no root window yet: fall back to the first candidate
    chosen = next((c for c in candidates if c.lower() in installed), candidates[0])
    _resolved[role] = chosen
    return chosen


# Named type scale: role -> (family role, size, weight, slant).
# Sizes are points at scale 1.0, on top of whatever display scaling the OS
# reports. The original values here were roughly two points larger across the
# board, which read as oversized once the operating system's own DPI factor
# was applied on top -- these are conventional desktop-UI sizes instead.
_FONT_SPECS: dict[str, tuple[str, int, str, str]] = {
    "title":  ("serif", 13, "bold",   "roman"),   # the wordmark
    "header": ("serif", 10, "bold",   "roman"),   # small-caps section headers
    "body":   ("serif", 12, "normal", "roman"),   # default UI text
    "label":  ("serif", 11, "normal", "roman"),   # field labels and buttons
    "small":  ("serif", 10, "normal", "roman"),   # hints and captions
    "hint":   ("serif", 10, "normal", "italic"),
    "mono":   ("mono",  10, "normal", "roman"),   # numeric values, readouts
}

# Font objects are cached and *shared* between every widget that asks for the
# same role. That is deliberate: a Tk font object is live, so resizing the
# cached instance restyles every widget using it at once, with no widget-tree
# reconfiguration. It is what makes `set_font_scale` cheap enough to run on
# window resize -- and it also stops the app from allocating several hundred
# near-identical font objects during startup.
_FONT_CACHE: dict[tuple, "ctk.CTkFont"] = {}
_FONT_BASE: dict[tuple, int] = {}
_FONT_SCALE = 1.0
_MIN_FONT_PT = 7


def font(role: str = "body", size: Optional[int] = None,
         weight: str = "normal", slant: str = "roman") -> "ctk.CTkFont":
    """Shared font object for a role (see `_FONT_SPECS`)."""
    key = (role, size, weight, slant)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached

    role_family, default_size, default_weight, default_slant = _FONT_SPECS.get(
        role, _FONT_SPECS["body"])
    base = int(size or default_size)
    instance = ctk.CTkFont(
        family=family(role_family),
        size=max(_MIN_FONT_PT, round(base * _FONT_SCALE)),
        weight=weight if weight != "normal" else default_weight,
        slant=slant if slant != "roman" else default_slant)
    _FONT_CACHE[key] = instance
    _FONT_BASE[key] = base
    return instance


def set_font_scale(factor: float) -> bool:
    """
    Resize the whole type scale by `factor` (1.0 = as designed).

    This is the *only* scaling mechanism the app uses. An earlier version
    called `ctk.set_widget_scaling()` with a factor derived from the screen's
    pixel width, which was wrong twice over: screen width is not a measure of
    DPI (a 1920px monitor does not mean "30% larger UI"), and CustomTkinter
    already applies the operating system's own display scaling, so the two
    multiplied together and produced the oversized text. Scaling only the
    shared font objects leaves the OS DPI factor exactly as the system
    reports it and touches nothing else.

    Returns True when the scale actually changed.
    """
    global _FONT_SCALE
    factor = max(0.88, min(1.10, float(factor)))
    if abs(factor - _FONT_SCALE) < 0.02:
        return False   # below the visible threshold: skip the relayout
    _FONT_SCALE = factor
    for key, instance in _FONT_CACHE.items():
        try:
            instance.configure(
                size=max(_MIN_FONT_PT, round(_FONT_BASE[key] * factor)))
        except Exception:
            continue   # a torn-down font: leave the rest of the scale alone
    return True


def font_scale() -> float:
    return _FONT_SCALE


# --------------------------------------------------------------------------- #
# Greyscale pass
# --------------------------------------------------------------------------- #
def _greyscale(value: str) -> str:
    """Rec.709 luminance equivalent of any Matplotlib-parsable colour."""
    try:
        r, g, b = mcolors.to_rgb(value)
    except (ValueError, TypeError):
        return value                     # "transparent", Tk names, etc.
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return mcolors.to_hex((lum, lum, lum))


def _desaturate(node: Any, key: str = "") -> Any:
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
        "CTk": {"fg_color": col("app")},
        "CTkToplevel": {"fg_color": col("app")},
        "CTkFrame": {
            "fg_color": col("panel"),
            "top_fg_color": col("bar"),
            "border_color": col("border"),
            "corner_radius": RADIUS,
            "border_width": 0,
        },
        "CTkButton": {
            "fg_color": col("surface"),
            "hover_color": col("sel"),
            "border_color": col("border_str"),
            "text_color": col("fg"),
            "text_color_disabled": col("fg_faint"),
            "corner_radius": RADIUS,
            "border_width": BORDER,
        },
        "CTkLabel": {"fg_color": "transparent", "text_color": col("fg"),
                     "corner_radius": RADIUS},
        "CTkEntry": {
            "fg_color": col("surface"),
            "border_color": col("border"),
            "text_color": col("fg"),
            "placeholder_text_color": col("fg_faint"),
            "corner_radius": RADIUS,
            "border_width": BORDER,
        },
        "CTkCheckBox": {
            "fg_color": col("accent"),
            "border_color": col("border_str"),
            "hover_color": col("accent_hi"),
            "checkmark_color": col("on_accent"),
            "text_color": col("fg"),
            "text_color_disabled": col("fg_faint"),
            "corner_radius": RADIUS,
            "border_width": BORDER,
        },
        "CTkRadioButton": {
            "fg_color": col("accent"),
            "border_color": col("border_str"),
            "hover_color": col("accent_hi"),
            "text_color": col("fg"),
            "text_color_disabled": col("fg_faint"),
            "corner_radius": RADIUS,
        },
        "CTkSwitch": {
            "fg_color": col("border"),
            "progress_color": col("accent"),
            "button_color": col("fg_muted"),
            "button_hover_color": col("fg"),
            "text_color": col("fg"),
            "corner_radius": RADIUS,
            "button_corner_radius": RADIUS,
        },
        "CTkComboBox": {
            "fg_color": col("surface"),
            "border_color": col("border"),
            "button_color": col("border"),
            "button_hover_color": col("border_str"),
            "text_color": col("fg"),
            "text_color_disabled": col("fg_faint"),
            "corner_radius": RADIUS,
            "border_width": BORDER,
        },
        "CTkOptionMenu": {
            "fg_color": col("surface"),
            "button_color": col("border"),
            "button_hover_color": col("border_str"),
            "text_color": col("fg"),
            "text_color_disabled": col("fg_faint"),
            "corner_radius": RADIUS,
        },
        "DropdownMenu": {
            "fg_color": col("surface"),
            "hover_color": col("sel"),
            "text_color": col("fg"),
        },
        "CTkScrollableFrame": {"label_fg_color": col("bar")},
        "CTkScrollbar": {
            "fg_color": "transparent",
            "button_color": col("border"),
            "button_hover_color": col("border_str"),
            "corner_radius": RADIUS,
        },
        "CTkSlider": {
            "fg_color": col("border"),
            "progress_color": col("accent"),
            "button_color": col("fg_muted"),
            "button_hover_color": col("fg"),
            "corner_radius": RADIUS,
            "button_corner_radius": RADIUS,
        },
        "CTkProgressBar": {
            "fg_color": col("border"),
            "progress_color": col("accent"),
            "border_color": col("border"),
            "corner_radius": RADIUS,
        },
        "CTkSegmentedButton": {
            "fg_color": col("surface"),
            "selected_color": col("accent"),
            "selected_hover_color": col("accent_hi"),
            "unselected_color": col("surface"),
            "unselected_hover_color": col("sel"),
            "text_color": col("fg"),
            "text_color_disabled": col("fg_faint"),
            "corner_radius": RADIUS,
        },
        "CTkTextbox": {
            "fg_color": col("surface"),
            "border_color": col("border"),
            "text_color": col("fg"),
            "scrollbar_button_color": col("border"),
            "scrollbar_button_hover_color": col("border_str"),
            "corner_radius": RADIUS,
            "border_width": BORDER,
        },
        "CTkTabview": {
            "fg_color": col("panel"),
            "border_color": col("border"),
            "segmented_button_fg_color": col("surface"),
            "segmented_button_selected_color": col("accent"),
            "segmented_button_selected_hover_color": col("accent_hi"),
            "segmented_button_unselected_color": col("surface"),
            "segmented_button_unselected_hover_color": col("sel"),
            "text_color": col("fg"),
            "corner_radius": RADIUS,
        },
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def apply_theme(mode: str = "light") -> None:
    """
    Install the theme. Must run **before** the first widget is created --
    CustomTkinter reads the theme dictionary at widget construction time.
    """
    ctk.set_default_color_theme("blue")          # baseline structure

    theme = getattr(ctk.ThemeManager, "theme", None)
    if not isinstance(theme, dict):              # unexpected CustomTkinter build
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

    # Square every remaining corner the override table did not reach.
    for values in theme.values():
        if isinstance(values, dict) and "corner_radius" in values:
            values["corner_radius"] = RADIUS

    ctk.set_appearance_mode(_appearance(mode))


# Backwards-compatible alias: earlier revisions called this
# `apply_monochrome_theme`.
apply_monochrome_theme = apply_theme


def set_theme_mode(mode: str) -> None:
    """Switch light/dark at runtime; both variants live in the same theme dict."""
    ctk.set_appearance_mode(_appearance(mode))


def _appearance(mode: str) -> str:
    normalized = (mode or "").strip().lower()
    if normalized.startswith("o") or normalized in ("dark", "oscuro"):
        return "Dark"
    if normalized == "system":
        return "System"
    return "Light"


def apply_plot_chrome(fig) -> None:
    """
    Neutral, print-oriented chrome for the figure: paper-white canvas,
    dark-grey spines/ticks/text, light-grey gridlines.

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
            spine.set_linewidth(0.8)
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


def style_matplotlib_toolbar(toolbar, mode: str = "light") -> None:
    """
    Repaint the classic `NavigationToolbar2Tk`, which is plain Tk and ignores
    the CustomTkinter theme. Kept for the cases where the stock toolbar is
    shown; the main window drives it headlessly from its own tool strip.
    """
    palette = DARK if _appearance(mode) == "Dark" else LIGHT
    background, foreground = palette["bar"], palette["fg"]

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
                              ("activebackground", palette["sel"])):
            try:
                widget.configure(**{option: value})
            except Exception:
                continue   # option unsupported by this widget class


def tk_color(token: str) -> str:
    """
    Single colour string for plain-Tk widgets (which cannot take a
    light/dark pair), resolved against the current appearance mode.
    """
    palette = DARK if ctk.get_appearance_mode() == "Dark" else LIGHT
    return palette.get(token, LIGHT["fg"])
