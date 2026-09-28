"""
core/math_channels.py
---------------------
Math channels ("canales matemáticos"): a trace computed from other traces
with a user-written expression, like the MATH function of an oscilloscope
(`A - B`, `A * B`, `deriv(A)`, `integ(A) / 1k` ...).

Design (see DOCUMENTACION_TECNICA.md, "Canales matemáticos"):

* **Live, not materialized.** A math `Signal` stores only the expression and
  which trace is bound to each alias (`Signal.math_expr`,
  `Signal.math_operands`). `refresh_math_signals()` recomputes its
  `t_raw`/`v_raw` from the operands' *processed* data (units, offset, gain
  and inversion already applied) -- the GUI calls it at the start of every
  redraw, so editing a source trace updates the math channel automatically.
* **No `eval()`.** Expressions are parsed with `ast` and walked by a small
  interpreter that only knows the operators/functions whitelisted below. A
  session file or a sidecar JSON can therefore never execute code.
* **Common time base.** Operands are aligned on the sample grid of the first
  alias used in the expression, restricted to the X interval every operand
  covers; an operand on a different grid is linearly interpolated onto it.
  Nothing is ever extrapolated.
* **Base units.** Results are always in the base unit of their domain/kind
  (s or Hz; V, dB or deg). The math Signal keeps its own gain/offset/invert,
  applied on top by `Signal.processed()` exactly as for a loaded trace.

This module has no Tk dependency and is tested headless
(`tests/test_math_channels.py`).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional

import numpy as np

from core.data_io import Signal, x_units_for_domain, y_units_for_kind
from core.i18n import t

# Aliases the expression can use to reference a trace (oscilloscope style).
ALIASES: tuple[str, ...] = ("A", "B", "C", "D")

# Name bound to the aligned X array (time or frequency, base units).
X_NAME = "t"

CONSTANTS: dict[str, float] = {"pi": float(np.pi), "e": float(np.e)}

# Ready-made expressions offered by the dialog: (button text, expression).
PRESETS: tuple[tuple[str, str], ...] = (
    ("A + B", "A + B"),
    ("A − B", "A - B"),
    ("A × B", "A * B"),
    ("A ÷ B", "A / B"),
    ("d/dt A", "deriv(A)"),
    ("∫ A dt", "integ(A)"),
    ("|A|", "abs(A)"),
    ("Suavizar A", "smooth(A, 11)"),
)

_SI_PREFIX = {"T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3, "K": 1e3,
              "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
              "n": 1e-9, "p": 1e-12, "f": 1e-15}
# A number immediately followed by an SI prefix ("1k", "4.7u", "10m") that is
# not the start of a longer identifier and not preceded by a letter/digit/dot.
_SI_LITERAL = re.compile(r"(?<![\w.])(\d+(?:\.\d*)?|\.\d+)([TGMkKmuµμnpf])(?![\w(])")
_ALIAS_TOKEN = re.compile(r"\b([A-D])\b")


class MathError(ValueError):
    """Invalid expression or impossible evaluation.

    Stores a Spanish canonical key plus format arguments and translates
    lazily on `str()`, so a message raised before a language switch is
    still shown in the active language.
    """

    def __init__(self, key: str, **kwargs):
        super().__init__(key)
        self.key = key
        self.kwargs = kwargs

    def __str__(self) -> str:
        try:
            return t(self.key).format(**self.kwargs)
        except (KeyError, IndexError):
            return t(self.key)


# ---------------------------------------------------------------------- #
# Parsing
# ---------------------------------------------------------------------- #
def normalize(expr: str) -> str:
    """Typographic operators -> Python, SI-prefixed literals -> plain floats."""
    text = (str(expr or "")
            .replace("−", "-").replace("×", "*").replace("·", "*")
            .replace("÷", "/").replace("^", "**"))

    def _expand(match: re.Match) -> str:
        return repr(float(match.group(1)) * _SI_PREFIX[match.group(2)])

    return _SI_LITERAL.sub(_expand, text).strip()


_BINOPS: dict[type, Callable] = {
    ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply,
    ast.Div: np.divide, ast.Pow: np.power,
}
_UNARYOPS: dict[type, Callable] = {ast.USub: np.negative, ast.UAdd: np.positive}


def _moving_average(x: np.ndarray, n) -> np.ndarray:
    n = int(round(float(np.asarray(n).ravel()[0]))) if np.size(n) else 1
    if n < 1:
        raise MathError("smooth(x, n): n tiene que ser un entero ≥ 1.")
    if n == 1 or x.size == 0:
        return x
    n = min(n, x.size)
    kernel = np.ones(n)
    # Normalizing by the convolved ones keeps the edges unbiased (fewer
    # samples averaged there instead of implicit zero padding).
    return np.convolve(x, kernel, mode="same") / np.convolve(np.ones_like(x), kernel, mode="same")


def _integrate(x: np.ndarray, xs: np.ndarray) -> np.ndarray:
    if x.size < 2:
        return np.zeros_like(x)
    steps = 0.5 * (x[1:] + x[:-1]) * np.diff(xs)
    return np.concatenate(([0.0], np.cumsum(steps)))


def _derive(x: np.ndarray, xs: np.ndarray) -> np.ndarray:
    if x.size < 2:
        raise MathError("deriv() necesita al menos 2 muestras.")
    return np.gradient(x, xs)


# name -> (min_args, max_args, implementation(args, xs))
_FUNCTIONS: dict[str, tuple[int, int, Callable]] = {
    "abs":   (1, 1, lambda a, xs: np.abs(a[0])),
    "sqrt":  (1, 1, lambda a, xs: np.sqrt(a[0])),
    "exp":   (1, 1, lambda a, xs: np.exp(a[0])),
    "ln":    (1, 1, lambda a, xs: np.log(a[0])),
    "log":   (1, 1, lambda a, xs: np.log(a[0])),
    "log10": (1, 1, lambda a, xs: np.log10(a[0])),
    "sin":   (1, 1, lambda a, xs: np.sin(a[0])),
    "cos":   (1, 1, lambda a, xs: np.cos(a[0])),
    "tan":   (1, 1, lambda a, xs: np.tan(a[0])),
    "db":    (1, 1, lambda a, xs: 20.0 * np.log10(np.abs(a[0]))),
    "deriv": (1, 1, lambda a, xs: _derive(_full(a[0], xs), xs)),
    "integ": (1, 1, lambda a, xs: _integrate(_full(a[0], xs), xs)),
    "smooth": (2, 2, lambda a, xs: _moving_average(_full(a[0], xs), a[1])),
    "mean":  (1, 1, lambda a, xs: np.nanmean(a[0])),
    "rms":   (1, 1, lambda a, xs: np.sqrt(np.nanmean(np.square(a[0])))),
    "min":   (1, 1, lambda a, xs: np.nanmin(a[0])),
    "max":   (1, 1, lambda a, xs: np.nanmax(a[0])),
}
FUNCTION_NAMES: tuple[str, ...] = tuple(_FUNCTIONS)


def _full(value, xs: np.ndarray) -> np.ndarray:
    """Broadcast a scalar operand to the sample grid."""
    arr = np.asarray(value, dtype=float)
    return np.broadcast_to(arr, xs.shape).astype(float) if arr.ndim == 0 else arr


def parse(expr: str) -> ast.Expression:
    """Parse and validate an expression. Raises MathError on anything not whitelisted."""
    text = normalize(expr)
    if not text:
        raise MathError("Escribí una operación (por ejemplo: A - B).")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise MathError("Operación mal escrita: revisá paréntesis y operadores.") from None

    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load)) or type(node) in _BINOPS \
                or type(node) in _UNARYOPS:
            continue
        if isinstance(node, (ast.BinOp, ast.UnaryOp)):
            op = node.op
            if type(op) not in _BINOPS and type(op) not in _UNARYOPS:
                raise MathError("Operador no permitido.")
            continue
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise MathError("Sólo se admiten números como constantes.")
            continue
        if isinstance(node, ast.Name):
            if node.id not in ALIASES and node.id not in CONSTANTS \
                    and node.id != X_NAME and node.id not in _FUNCTIONS:
                raise MathError("Nombre desconocido: «{name}».", name=node.id)
            continue
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
                raise MathError("Función no permitida.")
            if node.keywords:
                raise MathError("Las funciones no aceptan argumentos con nombre.")
            lo, hi, _impl = _FUNCTIONS[node.func.id]
            if not lo <= len(node.args) <= hi:
                raise MathError("{fn}() recibe {n} argumento(s).",
                                fn=node.func.id, n=lo if lo == hi else f"{lo}-{hi}")
            continue
        raise MathError("Construcción no permitida en la operación.")
    return tree


def referenced_aliases(expr: str) -> list[str]:
    """Aliases used by a valid expression, in canonical order (A, B, C, D)."""
    tree = parse(expr)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in ALIASES}
    return [a for a in ALIASES if a in used]


def describe(expr: str, names: Mapping[str, str]) -> str:
    """Expression with aliases replaced by trace names (default channel name)."""
    return _ALIAS_TOKEN.sub(lambda m: names.get(m.group(1), m.group(1)),
                            str(expr or "").strip())


# ---------------------------------------------------------------------- #
# Evaluation
# ---------------------------------------------------------------------- #
def align(series: Mapping[str, tuple[np.ndarray, np.ndarray]],
          order: Iterable[str]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    Put every operand on the sample grid of the first alias in `order`,
    restricted to the X range all of them cover. Operands already on that
    exact grid are sliced, never interpolated.
    """
    order = [a for a in order if a in series]
    if not order:
        raise MathError("La operación tiene que usar al menos un canal (A, B, C o D).")
    clean: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for alias in order:
        x, y = (np.asarray(v, dtype=float) for v in series[alias])
        keep = np.isfinite(x)
        x, y = x[keep], y[keep]
        if x.size < 2:
            raise MathError("El canal {alias} no tiene datos suficientes.", alias=alias)
        if np.any(np.diff(x) < 0):
            idx = np.argsort(x, kind="stable")
            x, y = x[idx], y[idx]
        clean[alias] = (x, y)

    lo = max(x[0] for x, _ in clean.values())
    hi = min(x[-1] for x, _ in clean.values())
    if lo > hi:
        raise MathError("Los canales no se superponen en el eje X.")

    x_ref, _ = clean[order[0]]
    mask = (x_ref >= lo) & (x_ref <= hi)
    xs = x_ref[mask]
    if xs.size < 2:
        raise MathError("Los canales no se superponen en el eje X.")
    out: dict[str, np.ndarray] = {}
    for alias, (x, y) in clean.items():
        if x.shape == x_ref.shape and np.array_equal(x, x_ref):
            out[alias] = y[mask]
        else:
            out[alias] = np.interp(xs, x, y)
    return xs, out


def _eval(node: ast.AST, env: dict, xs: np.ndarray):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env, xs)
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in _FUNCTIONS:
            raise MathError("«{name}» es una función: usala como {name}(...).", name=node.id)
        if node.id not in env:
            raise MathError("El canal {alias} no está asignado a ninguna traza.", alias=node.id)
        return env[node.id]
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval(node.left, env, xs), _eval(node.right, env, xs))
    if isinstance(node, ast.UnaryOp):
        return _UNARYOPS[type(node.op)](_eval(node.operand, env, xs))
    if isinstance(node, ast.Call):
        _lo, _hi, impl = _FUNCTIONS[node.func.id]
        return impl([_eval(arg, env, xs) for arg in node.args], xs)
    raise MathError("Construcción no permitida en la operación.")


def evaluate(expr: str, operands: Mapping[str, tuple[np.ndarray, np.ndarray]]
             ) -> tuple[np.ndarray, np.ndarray]:
    """
    Evaluate `expr` over `operands` (alias -> (x, y) in base units).
    Returns (x, y) on the common grid. Non-finite results (0/0, log of a
    negative, ...) become NaN, which Matplotlib draws as a gap.
    """
    tree = parse(expr)
    used = [a for a in ALIASES
            if any(isinstance(n, ast.Name) and n.id == a for n in ast.walk(tree))]
    missing = [a for a in used if a not in operands]
    if missing:
        raise MathError("El canal {alias} no está asignado a ninguna traza.", alias=missing[0])
    xs, aligned = align({a: operands[a] for a in used}, used)
    env = {**CONSTANTS, X_NAME: xs, **aligned}
    with np.errstate(all="ignore"):
        result = _full(_eval(tree, env, xs), xs)
    if result.shape != xs.shape:
        raise MathError("El resultado no tiene la misma cantidad de muestras que los canales.")
    result = np.where(np.isfinite(result), result, np.nan)
    return xs.copy(), result


# ---------------------------------------------------------------------- #
# Integration with the Signal model
# ---------------------------------------------------------------------- #
@dataclass
class MathSpec:
    """What the dialog returns / what a session record stores."""
    expr: str
    operands: dict[str, str]          # alias -> uid (session: alias -> index)
    name: str
    y_kind: str = "voltage"
    unit: str = ""                    # only meaningful for y_kind == "custom"


def build_math_signal(spec: MathSpec, uid: str, color: Optional[str] = None) -> Signal:
    """Empty math Signal; `refresh_math_signals` fills its samples."""
    y_kind = spec.y_kind if spec.y_kind in ("voltage", "dB", "deg", "custom") else "voltage"
    return Signal(
        uid=uid, name=spec.name or spec.expr, source_path="",
        t_raw=np.array([]), v_raw=np.array([]),
        domain="time", y_kind=y_kind, unit_t_in="s",
        unit_v_in=(spec.unit if y_kind == "custom" else next(iter(y_units_for_kind(y_kind)))),
        color=color,
        math_expr=spec.expr, math_operands=dict(spec.operands),
    )


def depends_on(signals: Mapping[str, Signal], uid: str, target: str,
               _seen: Optional[set] = None) -> bool:
    """True if trace `uid` is `target` or (transitively) computed from it."""
    if uid == target:
        return True
    sig = signals.get(uid)
    if sig is None or not sig.is_math:
        return False
    seen = _seen if _seen is not None else set()
    if uid in seen:
        return False
    seen.add(uid)
    return any(depends_on(signals, op, target, seen) for op in sig.math_operands.values())


def _refresh_one(signals: Mapping[str, Signal], sig: Signal, done: set, visiting: set) -> None:
    if sig.uid in done:
        return
    if sig.uid in visiting:
        raise MathError("Referencia circular entre canales matemáticos.")
    visiting.add(sig.uid)
    try:
        used = referenced_aliases(sig.math_expr)
        operands: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        domains: set[str] = set()
        for alias in used:
            src_uid = sig.math_operands.get(alias)
            if not src_uid:
                raise MathError("El canal {alias} no está asignado a ninguna traza.", alias=alias)
            src = signals.get(src_uid)
            if src is None:
                raise MathError("La traza del canal {alias} ya no existe.", alias=alias)
            if src.is_math:
                _refresh_one(signals, src, done, visiting)
                if src.math_error:
                    raise MathError("El canal {alias} tiene un error propio.", alias=alias)
            if src.missing:
                raise MathError("La traza del canal {alias} no tiene su archivo de origen.",
                                alias=alias)
            domains.add(src.domain)
            operands[alias] = src.processed()
        if len(domains) > 1:
            raise MathError("No se pueden combinar trazas de tiempo y de frecuencia.")
        xs, ys = evaluate(sig.math_expr, operands)
        domain = domains.pop() if domains else "time"
        sig.domain = domain
        sig.unit_t_in = next(iter(x_units_for_domain(domain)))
        if sig.y_kind != "custom":
            sig.unit_v_in = next(iter(y_units_for_kind(sig.y_kind)))
        sig.t_raw, sig.v_raw = xs, ys
        sig.math_error = None
    except MathError as exc:
        sig.t_raw, sig.v_raw = np.array([]), np.array([])
        sig.math_error = str(exc)
    finally:
        visiting.discard(sig.uid)
        done.add(sig.uid)


def refresh_math_signals(signals: Mapping[str, Signal]) -> list[str]:
    """
    Recompute every math Signal in `signals` (dependencies first).
    Never raises: a failing channel ends up empty with `math_error` set.
    Returns the uids that failed.
    """
    done: set = set()
    for sig in list(signals.values()):
        if sig.is_math:
            _refresh_one(signals, sig, done, set())
    return [uid for uid, s in signals.items() if s.is_math and s.math_error]
