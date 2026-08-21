"""
core/processing.py
--------------------
Funciones de recorte temporal (crop) y diezmado / downsampling sobre
arrays de tiempo/tensión ya expresados en unidades SI (segundos, voltios).
"""

from __future__ import annotations

import numpy as np


def crop(t: np.ndarray, v: np.ndarray, t_min: float | None, t_max: float | None):
    """Recorta (t, v) al intervalo [t_min, t_max] (en segundos). None = sin límite."""
    mask = np.ones_like(t, dtype=bool)
    if t_min is not None:
        mask &= t >= t_min
    if t_max is not None:
        mask &= t <= t_max
    return t[mask], v[mask]


def decimate(t: np.ndarray, v: np.ndarray, factor: int):
    """Diezma tomando 1 de cada `factor` puntos. factor <= 1 -> sin cambios."""
    factor = max(1, int(factor))
    return t[::factor], v[::factor]


def decimate_to_target(t: np.ndarray, v: np.ndarray, target_points: int):
    """Calcula automáticamente el factor de diezmado para no superar target_points."""
    n = len(t)
    if target_points <= 0 or n <= target_points:
        return t, v
    factor = int(np.ceil(n / target_points))
    return decimate(t, v, factor)


def resample_uniform(t: np.ndarray, v: np.ndarray, n_points: int):
    """
    Interpola (t, v) a una grilla temporal uniforme de n_points puntos.
    Útil para exportar varias señales con una base temporal común.
    """
    if len(t) < 2:
        return t, v
    t_uniform = np.linspace(t[0], t[-1], n_points)
    v_uniform = np.interp(t_uniform, t, v)
    return t_uniform, v_uniform
