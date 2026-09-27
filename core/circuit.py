"""Modelo, renderizado y exportación de esquemas eléctricos.

El documento usa una grilla lógica independiente de Tk. La misma topología
se guarda como JSON, se previsualiza con Matplotlib y se exporta como PDF o
como un documento CircuitikZ editable.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

from matplotlib.figure import Figure
from matplotlib.patches import Circle, Polygon


@dataclass(frozen=True)
class ComponentSpec:
    name: str
    circuitikz: str
    prefix: str
    default_value: str = ""


COMPONENT_SPECS: dict[str, ComponentSpec] = {
    "resistor": ComponentSpec("Resistencia", "R", "R", "1 kΩ"),
    "capacitor": ComponentSpec("Capacitor", "C", "C", "100 nF"),
    "inductor": ComponentSpec("Inductor", "L", "L", "10 mH"),
    "diode": ComponentSpec("Diodo", "D", "D", "1N4148"),
    "voltage": ComponentSpec("Fuente de tensión", "V", "V", "5 V"),
    "current": ComponentSpec("Fuente de corriente", "I", "I", "1 mA"),
    "ground": ComponentSpec("Tierra", "", "GND", ""),
}

DOCUMENT_VERSION = 1


def _identifier() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class CircuitComponent:
    kind: str
    x: float
    y: float
    label: str = ""
    value: str = ""
    rotation: int = 0
    uid: str = field(default_factory=_identifier)

    def __post_init__(self) -> None:
        if self.kind not in COMPONENT_SPECS:
            raise ValueError(f"Componente no soportado: {self.kind}")
        self.x, self.y = float(self.x), float(self.y)
        self.rotation = int(round(self.rotation / 90.0) * 90) % 360

    def terminals(self) -> list[tuple[float, float]]:
        if self.kind == "ground":
            return [(self.x, self.y)]
        angle = math.radians(self.rotation)
        dx, dy = math.cos(angle), math.sin(angle)
        return [(self.x - dx, self.y - dy),
                (self.x + dx, self.y + dy)]


@dataclass
class CircuitWire:
    x1: float
    y1: float
    x2: float
    y2: float
    uid: str = field(default_factory=_identifier)

    def __post_init__(self) -> None:
        self.x1, self.y1 = float(self.x1), float(self.y1)
        self.x2, self.y2 = float(self.x2), float(self.y2)


@dataclass
class CircuitDocument:
    title: str = "Circuito"
    components: list[CircuitComponent] = field(default_factory=list)
    wires: list[CircuitWire] = field(default_factory=list)

    def next_label(self, kind: str) -> str:
        try:
            spec = COMPONENT_SPECS[kind]
        except KeyError as exc:
            raise ValueError(f"Tipo de componente desconocido: {kind}") from exc
        if kind == "ground":
            return "GND"
        used = {component.label for component in self.components}
        index = 1
        while f"{spec.prefix}{index}" in used:
            index += 1
        return f"{spec.prefix}{index}"

    def add_component(self, kind: str, x: float, y: float) -> CircuitComponent:
        try:
            spec = COMPONENT_SPECS[kind]
        except KeyError as exc:
            raise ValueError(f"Tipo de componente desconocido: {kind}") from exc
        component = CircuitComponent(
            kind=kind, x=x, y=y, label=self.next_label(kind),
            value=spec.default_value)
        self.components.append(component)
        return component

    def add_wire(self, start: tuple[float, float],
                 end: tuple[float, float]) -> CircuitWire:
        if start == end:
            raise ValueError("Un cable necesita dos puntos distintos.")
        wire = CircuitWire(start[0], start[1], end[0], end[1])
        self.wires.append(wire)
        return wire

    def find_component(self, uid: str) -> Optional[CircuitComponent]:
        return next((item for item in self.components if item.uid == uid), None)

    def find_wire(self, uid: str) -> Optional[CircuitWire]:
        return next((item for item in self.wires if item.uid == uid), None)

    def remove(self, uid: str) -> bool:
        before = len(self.components) + len(self.wires)
        self.components = [item for item in self.components if item.uid != uid]
        self.wires = [item for item in self.wires if item.uid != uid]
        return before != len(self.components) + len(self.wires)

    def to_dict(self) -> dict:
        return {
            "version": DOCUMENT_VERSION,
            "title": self.title,
            "components": [asdict(item) for item in self.components],
            "wires": [asdict(item) for item in self.wires],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CircuitDocument":
        if not isinstance(payload, dict):
            raise ValueError("El archivo no contiene un circuito válido.")
        version = payload.get("version", DOCUMENT_VERSION)
        if version != DOCUMENT_VERSION:
            raise ValueError(f"Versión de circuito no soportada: {version}")
        try:
            components = [CircuitComponent(**item)
                          for item in payload.get("components", [])]
            wires = [CircuitWire(**item) for item in payload.get("wires", [])]
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(f"Contenido de circuito inválido: {exc}") from exc
        return cls(title=str(payload.get("title", "Circuito")),
                   components=components, wires=wires)

    def save(self, path: str) -> str:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(self.to_dict(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        return path

    @classmethod
    def load(cls, path: str) -> "CircuitDocument":
        try:
            with open(path, "r", encoding="utf-8") as stream:
                return cls.from_dict(json.load(stream))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"No se pudo abrir el circuito: {exc}") from exc


def _rotate(point: tuple[float, float],
            component: CircuitComponent) -> tuple[float, float]:
    x, y = point
    angle = math.radians(component.rotation)
    return (component.x + x * math.cos(angle) - y * math.sin(angle),
            component.y + x * math.sin(angle) + y * math.cos(angle))


def _polyline(ax, component: CircuitComponent, points,
              color: str, width: float) -> None:
    transformed = [_rotate(point, component) for point in points]
    ax.plot([point[0] for point in transformed],
            [point[1] for point in transformed], color=color, lw=width,
            solid_capstyle="round", solid_joinstyle="round")


def _draw_component(ax, component: CircuitComponent,
                    color: str, width: float) -> None:
    kind = component.kind
    if kind == "ground":
        _polyline(ax, component, [(0, 0), (0, .22)], color, width)
        for y, half_width in ((.22, .42), (.37, .27), (.51, .12)):
            _polyline(ax, component, [(-half_width, y), (half_width, y)],
                      color, width)
        return

    if kind == "resistor":
        _polyline(ax, component,
                  [(-1, 0), (-.72, 0), (-.58, -.24), (-.36, .24),
                   (-.14, -.24), (.08, .24), (.30, -.24), (.52, .24),
                   (.72, 0), (1, 0)], color, width)
    elif kind == "capacitor":
        _polyline(ax, component, [(-1, 0), (-.14, 0)], color, width)
        _polyline(ax, component, [(.14, 0), (1, 0)], color, width)
        _polyline(ax, component, [(-.14, -.45), (-.14, .45)], color, width)
        _polyline(ax, component, [(.14, -.45), (.14, .45)], color, width)
    elif kind == "inductor":
        _polyline(ax, component, [(-1, 0), (-.72, 0)], color, width)
        samples = []
        for index in range(65):
            x = -.72 + 1.44 * index / 64
            phase = (x + .72) / 1.44 * 4 * math.pi
            samples.append((x, -.28 * abs(math.sin(phase))))
        _polyline(ax, component, samples, color, width)
        _polyline(ax, component, [(.72, 0), (1, 0)], color, width)
    elif kind == "diode":
        _polyline(ax, component, [(-1, 0), (-.42, 0)], color, width)
        _polyline(ax, component, [(.42, 0), (1, 0)], color, width)
        triangle = [_rotate(point, component)
                    for point in [(-.42, -.42), (-.42, .42), (.34, 0)]]
        ax.add_patch(Polygon(triangle, closed=True, fill=False,
                             edgecolor=color, linewidth=width))
        _polyline(ax, component, [(.40, -.46), (.40, .46)], color, width)
    elif kind in ("voltage", "current"):
        _polyline(ax, component, [(-1, 0), (-.48, 0)], color, width)
        _polyline(ax, component, [(.48, 0), (1, 0)], color, width)
        center = _rotate((0, 0), component)
        ax.add_patch(Circle(center, .48, fill=False,
                            edgecolor=color, linewidth=width))
        if kind == "voltage":
            plus, minus = _rotate((-.22, 0), component), _rotate((.22, 0), component)
            ax.plot([plus[0] - .13, plus[0] + .13], [plus[1], plus[1]],
                    color=color, lw=width, solid_capstyle="round")
            ax.plot([plus[0], plus[0]], [plus[1] - .13, plus[1] + .13],
                    color=color, lw=width, solid_capstyle="round")
            ax.plot([minus[0] - .13, minus[0] + .13], [minus[1], minus[1]],
                    color=color, lw=width, solid_capstyle="round")
        else:
            _polyline(ax, component, [(-.24, 0), (.23, 0)], color, width)
            _polyline(ax, component,
                      [(.08, -.15), (.25, 0), (.08, .15)], color, width)

    label = " · ".join(part for part in (component.label, component.value)
                       if part)
    if label:
        position = _rotate((0, -.62), component)
        ax.text(position[0], position[1], label, color=color, fontsize=9,
                ha="center", va="bottom", rotation=-component.rotation,
                rotation_mode="anchor")


def draw_circuit(ax, document: CircuitDocument,
                 selected: Optional[tuple[str, str]] = None,
                 show_grid: bool = False,
                 pending_wire: Optional[tuple[float, float]] = None) -> None:
    """Renderiza ``document`` sobre un ``Axes`` existente."""
    normal, active = "#25231F", "#8C2F2F"
    ax.clear()
    for wire in document.wires:
        is_selected = selected == ("wire", wire.uid)
        color = active if is_selected else normal
        ax.plot([wire.x1, wire.x2], [wire.y1, wire.y2], color=color,
                lw=2.4 if is_selected else 1.6, solid_capstyle="round")
        ax.plot([wire.x1, wire.x2], [wire.y1, wire.y2], "o",
                color=color, markersize=2.8)
    for component in document.components:
        is_selected = selected == ("component", component.uid)
        _draw_component(ax, component, active if is_selected else normal,
                        2.2 if is_selected else 1.5)
        for terminal in component.terminals():
            ax.plot(terminal[0], terminal[1], "o", color=normal, markersize=3)
    if pending_wire is not None:
        ax.plot(*pending_wire, "o", color=active, markersize=7,
                fillstyle="none", markeredgewidth=1.5)

    ax.set_aspect("equal", adjustable="box")
    ax.set_facecolor("#FFFFFF")
    if show_grid:
        ax.set_xticks(range(0, 25))
        ax.set_yticks(range(0, 17))
        ax.grid(True, color="#E3E1DB", lw=.55)
        ax.tick_params(left=False, bottom=False,
                       labelleft=False, labelbottom=False)
        for spine in ax.spines.values():
            spine.set_color("#B9B5AB")
    else:
        ax.axis("off")


def document_bounds(document: CircuitDocument) -> tuple[float, float, float, float]:
    points: list[tuple[float, float]] = []
    for component in document.components:
        points.extend(component.terminals())
        points.append((component.x, component.y))
    for wire in document.wires:
        points.extend([(wire.x1, wire.y1), (wire.x2, wire.y2)])
    if not points:
        return 0, 10, 0, 7
    xs, ys = zip(*points)
    return min(xs) - 1.2, max(xs) + 1.2, min(ys) - 1.2, max(ys) + 1.2


def export_circuit_pdf(document: CircuitDocument, path: str) -> str:
    if not document.components and not document.wires:
        raise ValueError("El circuito está vacío.")
    xmin, xmax, ymin, ymax = document_bounds(document)
    width, height = max(4.0, xmax - xmin), max(2.5, ymax - ymin)
    figure = Figure(figsize=(min(12, width), min(9, height)), dpi=120)
    axis = figure.add_subplot(111)
    draw_circuit(axis, document)
    axis.set_xlim(xmin, xmax)
    axis.set_ylim(ymax, ymin)
    if document.title.strip():
        axis.set_title(document.title.strip(), fontsize=12, pad=12)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    figure.savefig(path, format="pdf", bbox_inches="tight", facecolor="white")
    return path


def _latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
        "Ω": r"$\Omega$", "µ": r"$\mu$",
    }
    return "".join(replacements.get(char, char) for char in text)


def _latex_coord(x: float, y: float) -> str:
    return f"({x:g},{-y:g})"


def circuitikz_source(document: CircuitDocument,
                      standalone: bool = True) -> str:
    """Genera un documento CircuitikZ editable."""
    lines: list[str] = []
    if standalone:
        lines.extend([r"\documentclass[tikz,border=8pt]{standalone}",
                      r"\usepackage[american]{circuitikz}",
                      r"\begin{document}"])
    lines.append(r"\begin{circuitikz}[american voltages]")
    if document.title.strip():
        lines.append(f"  % {_latex_escape(document.title.strip())}")
    for wire in document.wires:
        lines.append(f"  \\draw {_latex_coord(wire.x1, wire.y1)} -- "
                     f"{_latex_coord(wire.x2, wire.y2)};")
    for component in document.components:
        if component.kind == "ground":
            lines.append(f"  \\draw {_latex_coord(component.x, component.y)} "
                         "node[ground] {};")
            continue
        start, end = component.terminals()
        option = COMPONENT_SPECS[component.kind].circuitikz
        label = " / ".join(part for part in (component.label, component.value)
                           if part)
        if label:
            option += f",l={{{_latex_escape(label)}}}"
        lines.append(f"  \\draw {_latex_coord(*start)} to[{option}] "
                     f"{_latex_coord(*end)};")
    lines.append(r"\end{circuitikz}")
    if standalone:
        lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def export_circuit_tex(document: CircuitDocument, path: str,
                       standalone: bool = True) -> str:
    if not document.components and not document.wires:
        raise ValueError("El circuito está vacío.")
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(circuitikz_source(document, standalone=standalone))
    return path
