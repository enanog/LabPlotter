"""
core/histogram.py
------------------
Cálculo de histogramas sobre los valores (eje X o eje Y) de una o más
señales ya cargadas. Separado de `gui/histogram_window.py` para poder
probarse sin Tk/matplotlib, siguiendo el mismo criterio que
`core/processing.py`.

No decide nada sobre unidades: opera directamente sobre los arrays que
`Signal.processed()` ya devuelve en la unidad base del dominio/tipo (s, Hz,
V, dB, deg o la magnitud personalizada que el usuario haya definido -- ver
`core/data_io.py`). El eje del histograma (X o Y) y la etiqueta de unidad a
mostrar quedan a cargo del llamador.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

# Reglas de binning soportadas por `np.histogram_bin_edges`. "auto" delega en
# NumPy (máximo entre Sturges y Freedman-Diaconis), que da un resultado
# razonable tanto para muestras pequeñas (pocas decenas de puntos, típico de
# un barrido) como para señales largas (cientos de miles de muestras de
# osciloscopio).
BIN_RULES = ("auto", "sturges", "fd", "scott", "sqrt")


@dataclass
class HistogramResult:
    """Un histograma ya calculado, listo para graficar con `ax.stairs`/`ax.bar`."""

    counts: np.ndarray      # alto de cada barra (frecuencia o densidad)
    edges: np.ndarray       # bordes de bin, len(edges) == len(counts) + 1
    n_samples: int          # cantidad de muestras finitas usadas (post-NaN/inf)
    n_dropped: int          # NaN/inf descartados del array de entrada
    mean: float
    std: float

    @property
    def centers(self) -> np.ndarray:
        return 0.5 * (self.edges[:-1] + self.edges[1:])

    @property
    def widths(self) -> np.ndarray:
        return np.diff(self.edges)


def compute_histogram(
    values: np.ndarray,
    bins: "int | str" = "auto",
    value_range: Optional[tuple[float, float]] = None,
    density: bool = False,
) -> Optional[HistogramResult]:
    """
    Calcula un histograma robusto a NaN/inf y a entradas vacías.

    Parameters
    ----------
    values : array de valores crudos (ya en la unidad base de la señal).
    bins   : entero (cantidad fija de bins) o una de `BIN_RULES`.
    value_range : (min, max) para fijar el rango de bins entre señales
                  superpuestas; None = usar el rango de `values`.
    density : True -> normaliza a densidad de probabilidad (área == 1),
              en lugar de conteo absoluto de muestras por bin.

    Returns
    -------
    `HistogramResult`, o None si no quedó ninguna muestra finita.
    """
    arr = np.asarray(values, dtype=float).ravel()
    finite = arr[np.isfinite(arr)]
    n_dropped = arr.size - finite.size

    if finite.size == 0:
        return None

    bin_spec = bins if isinstance(bins, (int, np.integer)) and bins > 0 else (
        bins if bins in BIN_RULES else "auto")

    # A single-valued sample (or a huge run of decimal-truncated duplicates,
    # e.g. an 8-bit ADC log) makes every NumPy bin rule divide by a zero
    # data range; np.histogram itself handles that by padding the range by
    # 0.5 on each side, but only when we do NOT also pass an explicit
    # `range=`, so we let it choose freely in that one case.
    spread = finite.max() - finite.min()
    effective_range = value_range if (value_range is not None and spread > 0) else None

    counts, edges = np.histogram(
        finite, bins=bin_spec, range=effective_range, density=density)

    return HistogramResult(
        counts=counts, edges=edges,
        n_samples=int(finite.size), n_dropped=int(n_dropped),
        mean=float(finite.mean()), std=float(finite.std()),
    )


def combined_range(all_values: list[np.ndarray]) -> Optional[tuple[float, float]]:
    """
    Rango común [min, max] a partir de varios arrays, ignorando NaN/inf.

    Usado para que histogramas superpuestos de distintas señales compartan
    los mismos bordes de bin (de lo contrario, comparar sus alturas
    visualmente no tiene sentido).
    """
    finite_mins, finite_maxs = [], []
    for values in all_values:
        arr = np.asarray(values, dtype=float).ravel()
        arr = arr[np.isfinite(arr)]
        if arr.size:
            finite_mins.append(arr.min())
            finite_maxs.append(arr.max())
    if not finite_mins:
        return None
    return float(min(finite_mins)), float(max(finite_maxs))
