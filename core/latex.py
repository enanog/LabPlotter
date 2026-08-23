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
