"""
core/data_io.py
-----------------
Carga y parseo de archivos de datos de osciloscopio, LTspice (dominio
temporal) y respuesta en frecuencia / Bode de LTspice (formato complejo
`(mag_dB, fase°)`).

Soporta:
- Separadores de campo: coma, punto y coma o tabulación (autodetección,
  con manejo especial para no confundir la coma interna del formato Bode
  de LTspice con el separador de campo real).
- Separador decimal: punto o coma (configurable).
- Archivos con o sin fila de encabezado (autodetección).
- Archivos con más de dos columnas (múltiples canales).
- Trazas complejas de LTspice tipo `(-40.1dB,89.4°)`: se descomponen
  automáticamente en tres columnas derivadas: magnitud en dB, fase en
  grados y módulo lineal en Volts (V = 10^(dB/20)).
"""

from __future__ import annotations

import csv
import re
import uuid
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------- #
# Unidades soportadas por dominio / tipo de magnitud
# ---------------------------------------------------------------------- #
TIME_UNITS = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}
FREQ_UNITS = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}
VOLT_UNITS = {"V": 1.0, "mV": 1e-3}
DB_UNITS = {"dB": 1.0}
DEG_UNITS = {"deg": 1.0}

# Etiquetas LaTeX (mathtext) para cada unidad, usadas en ejes del gráfico
TIME_UNIT_LATEX = {"s": "s", "ms": "ms", "us": r"\mu s", "ns": "ns"}
FREQ_UNIT_LATEX = {"Hz": "Hz", "kHz": "kHz", "MHz": "MHz", "GHz": "GHz"}
VOLT_UNIT_LATEX = {"V": "V", "mV": "mV"}

# Paleta de colores por defecto asignada de forma rotativa a cada señal
# cargada (equivalente al ciclo de color "tab10" de Matplotlib).
DEFAULT_COLOR_CYCLE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def x_units_for_domain(domain: str) -> dict:
    """Devuelve el diccionario de unidades de eje X correspondiente al dominio."""
    return FREQ_UNITS if domain == "freq" else TIME_UNITS


def y_units_for_kind(y_kind: str) -> dict:
    """Devuelve el diccionario de unidades de eje Y correspondiente al tipo de magnitud."""
    if y_kind == "dB":
        return DB_UNITS
    if y_kind == "deg":
        return DEG_UNITS
    return VOLT_UNITS


# ---------------------------------------------------------------------- #
# Modelo de señal
# ---------------------------------------------------------------------- #
@dataclass
class Signal:
    """
    Representa una señal cargada.

    Los arrays `t_raw`/`v_raw` se guardan exactamente como fueron leídos del
    archivo (sin conversión de unidades). `domain` indica si el eje X es
    "time" o "freq"; `y_kind` indica si el eje Y es "voltage", "dB" o "deg".
    `unit_t_in`/`unit_v_in` indican en qué unidad concreta están expresados
    los datos crudos dentro de ese dominio/tipo. `processed()` devuelve la
    señal ya convertida a la unidad base de su dominio/tipo (s o Hz; V, dB
    o deg) con offset, ganancia e inversión aplicados. Este diseño permite
    editar los parámetros desde la GUI de forma no destructiva.
    """

    uid: str
    name: str
    source_path: str
    t_raw: np.ndarray
    v_raw: np.ndarray

    domain: str = "time"      # "time" | "freq"
    y_kind: str = "voltage"   # "voltage" | "dB" | "deg"

    unit_t_in: str = "s"
    unit_v_in: str = "V"

    t_offset: float = 0.0   # offset en X, en la unidad base del dominio (s o Hz)
    v_offset: float = 0.0   # offset en Y, en la unidad base del tipo (V, dB o deg)
    gain: float = 1.0       # factor de escala/ganancia sobre v
    invert: bool = False
    visible: bool = True
    linestyle: str = "-"

    marker: str = "None"          # símbolo de Matplotlib ("o", "x", "+", ...); "None" = sin marcador
    marker_size: float = 5.0      # tamaño del marcador en puntos
    marker_hollow: bool = False   # True = marcador hueco (sin relleno, borde del color de la traza)

    color: Optional[str] = None          # color hex (#RRGGBB); None = automático
    legend_label: Optional[str] = None   # leyenda personalizada; None = usar `name`
    secondary_y: bool = False            # True = graficar contra un eje Y2 (twinx), sin tocar la ganancia

    # Alias puramente cosmético para la lista de trazas de la GUI. A
    # diferencia de `name` -- que además alimenta la leyenda y el eje
    # X/Y por defecto cuando `legend_label` está vacío -- este campo no lo
    # lee ninguna función de graficado ni de exportación: existe para poder
    # renombrar una traza en la lista (por ejemplo, aclarar qué archivo es
    # cada una) sin que ese cambio se filtre al gráfico.
    display_name: Optional[str] = None

    def processed(self) -> tuple[np.ndarray, np.ndarray]:
        """Devuelve (x, y) en la unidad base del dominio/tipo, con offset/ganancia/inversión aplicados."""
        x_units = x_units_for_domain(self.domain)
        y_units = y_units_for_kind(self.y_kind)
        t = self.t_raw * x_units[self.unit_t_in] + self.t_offset
        v = self.v_raw * y_units[self.unit_v_in] * self.gain + self.v_offset
        if self.invert:
            v = -v
        return t, v


# ---------------------------------------------------------------------- #
# Detección de formato complejo Bode de LTspice: "(mag dB, fase °)"
# ---------------------------------------------------------------------- #
_NUM = r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_BODE_PATTERN = re.compile(
    rf"^\(?\s*(?P<mag>{_NUM})\s*dB\s*,\s*(?P<phase>{_NUM})\s*(?:°|deg|degrees)?\s*\)?$",
    re.IGNORECASE,
)
# Indicio rápido de contenido Bode en una muestra cruda de texto, usado para
# excluir la coma como candidato a separador de campo (la coma del formato
# Bode separa magnitud/fase, no columnas).
_BODE_HINT_RE = re.compile(rf"{_NUM}\s*dB\s*,", re.IGNORECASE)

BODE_MAG_SUFFIX = "_dB"
BODE_PHASE_SUFFIX = "_deg"
BODE_LINEAR_SUFFIX = "_Vlin"

# Encodings probed, in order, when loading a text file. Measurement
# instruments and Spanish-locale exports frequently emit cp1252 (e.g. the
# degree sign '°' as the single byte 0xB0), which is not valid UTF-8 and
# would otherwise raise UnicodeDecodeError. latin-1 maps every byte 0-255,
# so it never fails and is kept as the last-resort fallback.
_CANDIDATE_ENCODINGS = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]


def _detect_encoding(path: str) -> str:
    """Probe a small sample of the file to find a working text encoding."""
    with open(path, "rb") as f:
        raw = f.read(8192)
    for enc in _CANDIDATE_ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"  # unreachable in practice: latin-1 always decodes


def _sniff_delimiter(sample: str) -> str:
    """Detecta el delimitador de campo (',', ';' o tab) de una muestra de texto."""
    candidates = [",", ";", "\t"]
    if _BODE_HINT_RE.search(sample):
        # El formato Bode de LTspice usa la coma dentro de "(...)"; la
        # excluimos como candidato para no confundirla con el separador real.
        candidates = [";", "\t"]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(candidates))
        return dialect.delimiter
    except csv.Error:
        first_line = next((l for l in sample.splitlines() if l.strip()), "")
        counts = {d: first_line.count(d) for d in candidates}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else candidates[-1]


def _is_bode_series(series: pd.Series) -> bool:
    sample = series.dropna().astype(str).head(20)
    if sample.empty:
        return False
    matches = sample.apply(lambda s: bool(_BODE_PATTERN.match(s.strip())))
    return bool(matches.mean() > 0.8)


def _parse_bode_column(series: pd.Series):
    """Descompone una columna de texto Bode en (magnitud_dB, fase_deg, magnitud_lineal_V)."""
    extracted = series.astype(str).str.strip().str.extract(_BODE_PATTERN)
    mag_db = pd.to_numeric(extracted["mag"], errors="coerce")
    phase_deg = pd.to_numeric(extracted["phase"], errors="coerce")
    mag_v = 10.0 ** (mag_db / 20.0)
    return mag_db, phase_deg, mag_v


def _to_numeric_series(series: pd.Series, decimal: str) -> pd.Series:
    if decimal == ",":
        series = series.astype(str).str.strip().str.replace(",", ".", regex=False)
    return pd.to_numeric(series, errors="coerce")


# ---------------------------------------------------------------------- #
# Lectura de archivos
# ---------------------------------------------------------------------- #
def read_table(path: str, decimal_comma: bool = False):
    """
    Lee un archivo .csv/.txt de osciloscopio, LTspice (dominio temporal) o
    de respuesta en frecuencia / Bode de LTspice.

    Parameters
    ----------
    path : ruta del archivo.
    decimal_comma : True si el archivo usa coma como separador decimal
                    (típico de equipos configurados en español/europeo).
                    No afecta a los valores numéricos dentro del formato
                    complejo `(...)` de LTspice, que siempre usa punto como
                    separador decimal (formato interno fijo de LTspice).

    Returns
    -------
    (df, col_kind) donde:
      - df : DataFrame puramente numérico. Las columnas con formato Bode se
        descomponen en tres columnas derivadas (sufijos `_dB`, `_deg`,
        `_Vlin`).
      - col_kind : dict que indica la naturaleza física de cada columna de
        `df`: "time", "freq", "voltage", "dB" o "deg". Se usa para
        preseleccionar automáticamente el dominio y el tipo de magnitud al
        construir una Signal.
    """
    encoding = _detect_encoding(path)
    with open(path, "r", encoding=encoding, errors="replace") as f:
        sample = f.read(4096)

    delimiter = _sniff_delimiter(sample)
    decimal = "," if decimal_comma else "."
    if decimal == "," and delimiter == ",":
        delimiter = ";"

    first_line = next((l for l in sample.splitlines() if l.strip()), "")
    fields = [f.strip() for f in first_line.split(delimiter) if f.strip() != ""]

    def _is_number_or_bode(s: str) -> bool:
        if _BODE_PATTERN.match(s):
            return True
        s2 = s.replace(decimal, ".") if decimal == "," else s
        try:
            float(s2)
            return True
        except ValueError:
            return False

    has_header = not (fields and all(_is_number_or_bode(f) for f in fields))

    # NOTE: no `comment=` filter is applied here. Passing comment="#" would
    # silently drop the header line whenever a column is literally named "#"
    # (a common index-column convention in lab/instrument exports, e.g. the
    # sample "bode2.csv"), causing pandas to treat the first data row as the
    # header instead.
    read_kwargs = dict(sep=delimiter, engine="python",
                        skip_blank_lines=True, dtype=str,
                        encoding=encoding, encoding_errors="replace")
    if has_header:
        df_raw = pd.read_csv(path, header=0, **read_kwargs)
        df_raw.columns = [str(c).strip() for c in df_raw.columns]
    else:
        df_raw = pd.read_csv(path, header=None, **read_kwargs)
        df_raw.columns = [f"col{i}" for i in range(df_raw.shape[1])]

    columns_out: dict[str, pd.Series] = {}
    col_kind: dict[str, str] = {}
    first_col = df_raw.columns[0]

    for col in df_raw.columns:
        series = df_raw[col]
        if _is_bode_series(series):
            mag_db, phase_deg, mag_v = _parse_bode_column(series)
            columns_out[f"{col}{BODE_MAG_SUFFIX}"] = mag_db
            col_kind[f"{col}{BODE_MAG_SUFFIX}"] = "dB"
            columns_out[f"{col}{BODE_PHASE_SUFFIX}"] = phase_deg
            col_kind[f"{col}{BODE_PHASE_SUFFIX}"] = "deg"
            columns_out[f"{col}{BODE_LINEAR_SUFFIX}"] = mag_v
            col_kind[f"{col}{BODE_LINEAR_SUFFIX}"] = "voltage"
        else:
            columns_out[col] = _to_numeric_series(series, decimal)
            header_text = str(col)
            if re.search(r"freq|hz", header_text, re.IGNORECASE):
                col_kind[col] = "freq"
            elif re.search(r"phase|fase|°|deg", header_text, re.IGNORECASE):
                col_kind[col] = "deg"
            elif re.search(r"\bdB\b|gain|ganancia|magnitud", header_text, re.IGNORECASE):
                # A plain numeric "Gain (dB)" column (as opposed to LTspice's
                # complex "(mag dB, phase °)" cell format) is only treated as
                # dB when the header explicitly names the unit; otherwise
                # "Amplitude"/"Gain" columns default to voltage.
                col_kind[col] = "dB" if re.search(r"\bdB\b", header_text, re.IGNORECASE) else "voltage"
            elif col == first_col:
                col_kind[col] = "time"
            else:
                col_kind[col] = "voltage"

    df = pd.DataFrame(columns_out)
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.dropna(axis=1, how="all")
    col_kind = {k: v for k, v in col_kind.items() if k in df.columns}

    if df.empty or df.shape[1] < 2:
        raise ValueError(
            "No se pudieron extraer al menos 2 columnas numéricas del archivo. "
            "Verificá el separador de campo/decimal o si el archivo tiene "
            "encabezados/metadata inusuales."
        )
    return df, col_kind


def build_signal(
    df: pd.DataFrame,
    time_col,
    value_col,
    name: str,
    source_path: str,
    domain: str = "time",
    y_kind: str = "voltage",
    color: Optional[str] = None,
) -> Signal:
    """Construye un Signal a partir de dos columnas de un DataFrame numérico."""
    t_raw = df[time_col].to_numpy(dtype=float)
    v_raw = df[value_col].to_numpy(dtype=float)

    mask = ~(np.isnan(t_raw) | np.isnan(v_raw))
    t_raw, v_raw = t_raw[mask], v_raw[mask]

    order = np.argsort(t_raw)
    t_raw, v_raw = t_raw[order], v_raw[order]

    unit_t_in = "Hz" if domain == "freq" else "s"
    if y_kind == "dB":
        unit_v_in = "dB"
    elif y_kind == "deg":
        unit_v_in = "deg"
    else:
        unit_v_in = "V"

    return Signal(
        uid=str(uuid.uuid4()),
        name=name,
        source_path=source_path,
        t_raw=t_raw,
        v_raw=v_raw,
        domain=domain,
        y_kind=y_kind,
        unit_t_in=unit_t_in,
        unit_v_in=unit_v_in,
        color=color,
    )

