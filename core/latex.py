"""
core/latex.py
-------------
Turns an exported artefact into the LaTeX that includes it.

Exporting a PDF is only half the job: the figure still has to be wrapped in a
`figure` environment with a caption, a label and a sensible width, and a `.pgf`
export needs `\\input` rather than `\\includegraphics`. Generating that block
here means the path, the file type and the label all agree with what was
actually written to disk, instead of being retyped from memory.

Paths are emitted with forward slashes regardless of platform: LaTeX requires
them, and a Windows backslash inside `\\includegraphics` is an escape
sequence, not a separator.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Sequence

# `\input` for the code-generating format, `\includegraphics` for the rest.
_INPUT_FORMATS = {".pgf", ".tex"}

_LABEL_CLEAN = re.compile(r"[^a-z0-9]+")


def latex_path(path: str, relative_to: Optional[str] = None) -> str:
    """
    Path as LaTeX wants it: forward slashes, relative when possible.

    Separators are normalised to the running platform's before `relpath`,
    because `os.path.relpath` does not recognise backslashes when it is not
    running on Windows -- without this, a Windows path processed anywhere
    else silently produces a bogus `../D:/...` result.
    """
    normalized = (path or "").replace("\\", os.sep).replace("/", os.sep)
    result = normalized
    if relative_to:
        base = relative_to.replace("\\", os.sep).replace("/", os.sep)
        try:
            candidate = os.path.relpath(normalized, base)
            # Only prefer the relative form when it actually stays inside the
            # project; a path full of `..` is worse than the absolute one.
            if not candidate.startswith(os.pardir + os.sep) and candidate != os.pardir:
                result = candidate
        except ValueError:
            pass                 # different drive on Windows: keep absolute
    return result.replace("\\", "/").replace(os.sep, "/")


def sanitize_label(text: str, prefix: str = "fig") -> str:
    """`Respuesta en frecuencia` -> `fig:respuesta-en-frecuencia`."""
    slug = _LABEL_CLEAN.sub("-", (text or "").strip().lower()).strip("-")
    return f"{prefix}:{slug or 'sin-titulo'}"


def escape(text: str) -> str:
    """Escape the characters that would otherwise be LaTeX syntax."""
    replacements = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in (text or ""))


def figure_block(path: str, caption: str = "", label: str = "",
                 width: str = "0.85\\linewidth", placement: str = "htbp",
                 relative_to: Optional[str] = None,
                 escape_caption: bool = True) -> str:
    """
    Complete `figure` environment for an exported file, ready to paste.

    `.pgf` is included with `\\input` (it is LaTeX source that draws the plot
    with the document's own fonts); everything else goes through
    `\\includegraphics`.
    """
    extension = os.path.splitext(path)[1].lower()
    included = latex_path(path, relative_to)
    caption_text = escape(caption) if escape_caption else (caption or "")
    label_text = label or sanitize_label(caption or os.path.splitext(
        os.path.basename(path))[0])

    if extension in _INPUT_FORMATS:
        body = f"  \\input{{{included}}}"
    else:
        body = f"  \\includegraphics[width={width}]{{{included}}}"

    return "\n".join([
        f"\\begin{{figure}}[{placement}]",
        "  \\centering",
        body,
        f"  \\caption{{{caption_text}}}",
        f"  \\label{{{label_text}}}",
        "\\end{figure}",
    ])


def figure_requirements(path: str) -> str:
    """One-line note on what the preamble needs for this block to compile."""
    extension = os.path.splitext(path)[1].lower()
    if extension in _INPUT_FORMATS:
        return r"Requiere: \usepackage{pgf}  (y las fuentes del documento)"
    if extension == ".svg":
        return (r"Requiere: \usepackage{svg} y compilar con --shell-escape; "
                r"para PDF/LaTeX conviene exportar en PDF.")
    return r"Requiere: \usepackage{graphicx}"


def addplot_block(csv_path: str, x_col: str, y_col: str,
                  legend: str = "", relative_to: Optional[str] = None,
                  escape_legend: bool = True) -> str:
    """
    A single `\\addplot` line for a CSV exported for pgfplots, plus its
    legend entry. Column names are used verbatim -- they are the header the
    exporter actually wrote.
    """
    table = latex_path(csv_path, relative_to)
    line = (f"\\addplot table [x={x_col}, y={y_col}, col sep=comma] "
            f"{{{table}}};")
    if not legend:
        return line
    legend_text = escape(legend) if escape_legend else legend
    return f"{line}\n\\addlegendentry{{{legend_text}}}"


def board_requirements() -> str:
    """One-line note on what the preamble needs to compile `board_block()`."""
    return (r"Requiere: \usepackage{graphicx} y \usepackage{subcaption} "
            r"(subfiguras numeradas a, b, c...).")


def board_block(rows: Sequence[Sequence], caption: str = "", label: str = "",
                relative_to: Optional[str] = None, width: str = "\\linewidth",
                subfig_gap: float = 0.02, placement: str = "htbp",
                escape_titles: bool = True) -> str:
    """
    `figure` environment holding one row of `subfigure`s per board row, each
    `\\includegraphics` pointing at that panel's own exported file and
    captioned with its title -- the LaTeX equivalent of a `core.board`
    layout, so the printed figure reproduces the on-screen arrangement.

    `rows` is a sequence of rows, each a sequence of panel-like objects
    exposing `.title`, `.vector_path` and `.weight` (i.e.
    `core.board.BoardPanel`); this module does not import `core.board` so
    the two responsibilities -- layout bookkeeping and LaTeX text -- stay
    decoupled. Within a row, each panel's width is its `weight` normalised
    against the row's total, minus the `subfig_gap` reserved between
    panels; rows are separated by a `\\\\[1em]` line break, which is the
    standard way of stacking `subfigure` rows inside one `figure`.
    """
    lines = [f"\\begin{{figure}}[{placement}]", "  \\centering"]
    nonempty_rows = [row for row in rows if row]

    for r, row in enumerate(nonempty_rows):
        n = len(row)
        gap = subfig_gap if n > 1 else 0.0
        available = max(1.0 - gap * (n - 1), 0.05)
        total_weight = sum(max(panel.weight, 1e-6) for panel in row)

        for c, panel in enumerate(row):
            frac = available * (max(panel.weight, 1e-6) / total_weight)
            included = latex_path(panel.vector_path, relative_to)
            caption_text = escape(panel.title) if escape_titles else (panel.title or "")
            label_text = sanitize_label(panel.title or f"panel-{r + 1}-{c + 1}")

            lines.append(f"  \\begin{{subfigure}}[t]{{{frac:.3f}{width}}}")
            lines.append("    \\centering")
            lines.append(f"    \\includegraphics[width=\\linewidth]{{{included}}}")
            if caption_text:
                lines.append(f"    \\caption{{{caption_text}}}")
            lines.append(f"    \\label{{{label_text}}}")
            lines.append("  \\end{subfigure}")
            if c < n - 1:
                lines.append("  \\hfill")

        if r < len(nonempty_rows) - 1:
            lines.append("  \\\\[1em]")

    if caption:
        caption_text = escape(caption) if escape_titles else caption
        lines.append(f"  \\caption{{{caption_text}}}")
    lines.append(f"  \\label{{{label or sanitize_label(caption or 'tablero')}}}")
    lines.append("\\end{figure}")
    return "\n".join(lines)


def axis_block(plots: Sequence[str], xlabel: str = "", ylabel: str = "",
               xmode: str = "normal", ymode: str = "normal",
               grid: bool = True, width: str = "0.85\\linewidth") -> str:
    """
    Wrap `\\addplot` lines in a pgfplots `axis`, so the CSV export is
    paste-ready and not just a file sitting on disk.
    """
    options = [f"width={width}", "grid=both" if grid else "grid=none"]
    if xlabel:
        options.append(f"xlabel={{{xlabel}}}")
    if ylabel:
        options.append(f"ylabel={{{ylabel}}}")
    if xmode == "log":
        options.append("xmode=log")
    if ymode == "log":
        options.append("ymode=log")
    options.append("legend pos=outer north east")

    indented_options = ",\n    ".join(options)
    body = "\n".join(f"    {line}" for plot in plots
                     for line in plot.splitlines())
    return "\n".join([
        "\\begin{tikzpicture}",
        "  \\begin{axis}[",
        f"    {indented_options}",
        "  ]",
        body,
        "  \\end{axis}",
        "\\end{tikzpicture}",
    ])
