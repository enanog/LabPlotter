"""
core/units.py
-------------
Engineering-notation input parsing for every numeric field in the GUI.

An oscilloscope user types `4u7`, `2.2k`, `-3dB` or `10 kHz`; making them
expand that into `0.0000047` by hand is a transcription-error generator. This
module accepts all of those spellings and returns a plain float in base SI
units, so the rest of the application keeps working with ordinary numbers and
never has to know a prefix existed.

Accepted forms (all optionally signed, comma or dot as decimal separator):

    12            22.5          -3
    1e-6          2.2E3                       scientific
    2.2k          470p          10M           SI prefix
    4u7           1k5           2R2           "R notation" (prefix as comma)
    10 kHz        3.3 V         -20 dB        trailing unit, ignored

Case matters where physics says it must: `M` is mega and `m` is milli. `K` is
tolerated as kilo because keyboards and habit produce it constantly, and there
is no competing meaning for an uppercase K in this domain.
"""

from __future__ import annotations

import re
from typing import Optional

# Prefix -> multiplier. `µ` (U+00B5 micro sign) and `μ` (U+03BC greek mu) are
# both accepted because different keyboards and different source files produce
# different code points for the same character.
PREFIXES: dict[str, float] = {
    "T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3, "K": 1e3,
    "": 1.0,
    "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
    "n": 1e-9, "p": 1e-12, "f": 1e-15,
}

# "R" is the resistance placeholder in R notation (2R2 = 2.2 ohm); it carries
# no multiplier, only the decimal-point position.
_R_NOTATION = re.compile(
    r"^([+-]?)(\d+)\s*([TGMkKmuµμnpfR])\s*(\d+)$")

_PREFIXED = re.compile(
    r"^([+-]?)"                       # sign
    r"(\d+(?:[.,]\d*)?|[.,]\d+)"      # mantissa
    r"\s*"
    r"([TGMkKmuµμnpf]?)"              # optional SI prefix
    r"\s*"
    r"([^\d\s]*)$"                    # optional trailing unit, ignored
)


def parse_eng(text, fallback: Optional[float] = None) -> Optional[float]:
    """
    Parse an engineering-notation string to a float in base units.

    Returns `fallback` when the input is empty or cannot be understood, so
    callers can decide between "leave the previous value" and "treat as
    unset" without having to catch anything.
    """
    if text is None:
        return fallback
    if isinstance(text, (int, float)):
        return float(text)

    raw = str(text).strip()
    if not raw:
        return fallback
    # Normalise the characters keyboards and locales vary on.
    raw = (raw.replace("\u2212", "-")     # unicode minus
              .replace("\u00a0", " ")     # non-breaking space
              .replace("\u2009", ""))     # thin space used as a group separator

    # Plain and scientific notation first: `1e3` must not be read as
    # "1" followed by a stray unit.
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        pass

    match = _R_NOTATION.match(raw)
    if match:
        sign, whole, prefix, frac = match.groups()
        multiplier = 1.0 if prefix == "R" else PREFIXES.get(prefix, 1.0)
        value = float(f"{whole}.{frac}") * multiplier
        return -value if sign == "-" else value

    match = _PREFIXED.match(raw)
    if match:
        sign, mantissa, prefix, _unit = match.groups()
        try:
            value = float(mantissa.replace(",", ".")) * PREFIXES.get(prefix, 1.0)
        except ValueError:
            return fallback
        return -value if sign == "-" else value

    return fallback


def parse_eng_or(text, fallback: float = 0.0) -> float:
    """`parse_eng` that always returns a number."""
    value = parse_eng(text, None)
    return fallback if value is None else value


_FORMAT_STEPS: tuple[tuple[float, str], ...] = (
    (1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
    (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"),
)


def format_eng(value: Optional[float], unit: str = "", digits: int = 4) -> str:
    """
    Inverse of `parse_eng`, for echoing a parsed value back to the user.

    `gui.overlays` carries its own formatter for the plot canvas: that module
    is deliberately dependency-free so the overlay engine can run headless,
    and it needs mathtext output (`$\\mu$`) that would be wrong here.
    """
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v != v or v in (float("inf"), float("-inf")):
        return ""
    suffix = f" {unit}" if unit else ""
    if v == 0.0:
        return f"0{suffix}"

    magnitude = abs(v)
    factor, prefix = 1e-12, "p"
    for step, name in _FORMAT_STEPS:
        if magnitude >= step:
            factor, prefix = step, name
            break
    return f"{v / factor:.{digits}g} {prefix}{unit}".rstrip()
