# LabPlotter

Aplicación de escritorio para convertir capturas de osciloscopio y simulaciones de LTspice en
figuras y tablas listas para un informe en LaTeX. Carga los datos crudos, permite ajustarlos y
anotarlos de forma interactiva, y exporta tanto el CSV que consume `pgfplots` como la figura
vectorial que se incrusta directamente en el documento.

**Stack:** Python 3.10+ · CustomTkinter · Matplotlib · Pandas · NumPy

---

## 1. Alcance

| Entrada | Salida |
|---|---|
| CSV/TXT de osciloscopio (multicanal, coma decimal, `cp1252` con `°`) | CSV de dos columnas por señal, listo para `\addplot table` |
| Barridos AC de LTspice, incluido el formato `(-40.1dB, 89.4°)` | CSV combinado sobre grilla común (lineal o logarítmica) |
| Cualquier tabla numérica con separador auto-detectado | PDF / SVG / PGF vectorial y PNG a DPI configurable |

Toda la tipografía y el renderizado matemático usan el motor **mathtext** interno de Matplotlib
(`text.usetex = False`). No hace falta una distribución TeX instalada, la exportación a PDF es
rápida y —lo más importante— **la vista previa y el archivo exportado comparten exactamente los
mismos `rcParams`**: lo que se ve en pantalla es lo que sale al informe.

---

## 2. Arquitectura

```
main.py                 # punto de entrada
core/
  data_io.py            # lectura robusta, detección de formato, modelo Signal
  processing.py         # recorte, diezmado por factor y por número objetivo de puntos
  export.py             # estilo de publicación y exportación de datos/figuras
  layout.py             # geometría de leyenda (posiciones externas y coordenadas libres)
gui/
  app.py                # ventana principal: paneles, canvas y orquestación
  theme.py              # identidad visual monocromática (CustomTkinter + chrome Matplotlib)
  overlays.py           # estado y render de cursores y anotaciones (solo Matplotlib/NumPy)
  overlay_panel.py      # paleta flotante que edita ese estado
```

Separación de responsabilidades:

- **`core/`** no importa nada de la GUI. Es scriptable y testeable de forma aislada.
- **`gui/overlays.py`** no importa CustomTkinter: guarda *estado* (dataclasses), no widgets. Por
  eso los cursores y las anotaciones sobreviven al ciclo completo de `fig.clear()` + re-graficado
  que ejecuta `App.update_plot()`, y por eso el conjunto puede serializarse a JSON y recargarse
  para reproducir una figura idéntica.
- **`gui/overlay_panel.py`** es el único módulo que conoce widgets de overlays; edita el estado y
  pide a la aplicación que vuelva a renderizar la capa mediante un callback.

---

## 3. Instalación

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python main.py
```

`requirements.txt`:

```
customtkinter>=5.2.0
matplotlib>=3.7
numpy>=1.24
pandas>=2.0
```

---

## 4. Interfaz

Rediseño minimalista de laboratorio: **paleta estrictamente monocromática** en todo el chrome de
la aplicación (paneles, botones, combos, entradas, deslizadores, pestañas, toolbar de Matplotlib).

La excepción deliberada es el lienzo: **las señales se trazan a color**. El canal cromático se
reserva para los datos, que es donde aporta información; los controles no compiten con las curvas.

El tema se instala en dos capas (`gui/theme.py`):

1. Una pasada en escala de grises sobre todo el diccionario de tema activo de CustomTkinter
   (luminancia Rec.709 sobre cualquier clave que contenga `color`). Garantiza cobertura total
   aunque una versión futura de la librería agregue widgets nuevos.
2. Una tabla explícita de sobreescrituras para las clases que sostienen la jerarquía visual
   (superficies, bordes, acentos), de modo que el contraste sea diseñado y no accidental.

Cada color se declara como par `[claro, oscuro]`, así que el conmutador **Tema → Claro / Oscuro**
reestiliza el árbol de widgets en vivo, sin reconstruirlo.

La figura mantiene fondo blanco, ejes gris oscuro y grilla gris clara en ambos modos: es una
figura de publicación, no un elemento de la UI, y recolorearla rompería la equivalencia
vista previa ↔ exportación.

**Distribución:** panel izquierdo con señales cargadas y parámetros por canal · centro con el
canvas y la barra de herramientas · panel derecho con ajustes globales y exportación · paleta
flotante de cursores y anotaciones.

---

## 5. Modos de graficado

| Modo | Uso |
|---|---|
| **Tiempo / Frecuencia** | Trazado estándar; los canales marcados como `Y secundario` van a un eje derecho real vía `twinx` |
| **Modo X/Y** | Curvas paramétricas y figuras de Lissajous a partir de dos canales |
| **Diagrama de Bode** | Magnitud y fase, superpuestas (`Y1/Y2`) o en subgráficos independientes |

Ajustes por canal: offsets, ganancia, inversión, estilo de línea, color, etiqueta de leyenda, eje
secundario, dominio y tipo de magnitud. Ajustes globales: unidades, escalas lineal/logarítmica,
grilla mayor y menor densa (subdivisiones 2–9 por década), notación de ingeniería en los ejes
logarítmicos (1, 10, 100, 1k, 10k, 1M), márgenes de subgráfico y tipografía.

**Fuentes:** el preset por defecto es *LaTeX (Computer Modern)* — prioridad
`Latin Modern Roman → CMU Serif → cmr10` — para que la figura se integre sin costuras en un
documento con `\usepackage{lmodern}`. `cmr10` viene incluido en Matplotlib, así que el preset
funciona incluso sin TeX instalado.

---

## 6. Leyenda con posición libre

Además de las posiciones nativas de Matplotlib, `core/layout.py` agrega:

| Posición | Resultado |
|---|---|
| `outside right`, `outside right top`, `outside left` | Leyenda a un costado del área de ejes |
| `outside top`, `outside bottom` | Leyenda arriba o debajo (útil con muchas curvas y `Columnas > 1`) |
| `personalizada (x, y)` | Coordenadas libres en fracción de ejes |

En modo personalizado se indican:

- **X / Y** en fracción del área de ejes. Valores fuera de `[0, 1]` colocan la leyenda por fuera
  del gráfico, que es justamente lo que evita tapar transitorios o el pico de resonancia de un
  Bode. Por ejemplo `X = 1.02`, `Y = 1.00` la deja pegada al borde derecho superior.
- **Anclaje**: qué esquina del recuadro de leyenda se fija al punto `(x, y)`.
- **Columnas**: número de columnas, para una leyenda horizontal debajo del eje.

`tight_layout()` ignora las leyendas ancladas fuera del área, así que tras cada redibujo se
reserva el margen correspondiente con `reserve_legend_space()`. La exportación usa
`bbox_inches="tight"`, de modo que el archivo nunca recorta la leyenda.

---

## 7. Cursores múltiples

Banco de cursores de medición sobre el lienzo, sin límite fijo de cantidad
(`CursorManager(max_cursors=None)`).

- **Colocación por clic:** `+ Vertical` / `+ Horizontal` arman el cursor; el siguiente clic sobre
  el gráfico lo ubica. También se puede escribir la coordenada exacta y aplicarla.
- **Arrastre:** cada cursor se toma y se mueve con el mouse (tolerancia de 6 px en pantalla).
- **Ajuste a muestras (*snap*):** el cursor se engancha a la muestra más cercana cuando está a
  menos del 1,5 % del span del eje; fuera de ese margen queda libre.
- **Lectura:**
  - cursor **vertical** → valor de *cada* curva visible interpolado linealmente en esa X;
  - cursor **horizontal** → todas las frecuencias/tiempos donde las curvas **cruzan** ese nivel,
    interpoladas linealmente. Un cursor en `-3` dB devuelve directamente las frecuencias de corte.
- **Deltas:** entre cursores consecutivos de la misma orientación se calcula `Δ`, `1/Δ` (período
  ↔ frecuencia) y `ΔY` por curva.
- **Ejes gemelos:** en Bode «Juntos» el eje de fase se crea con `twinx`. El lector detecta los
  ejes que comparten rectángulo y también incluye sus curvas en la tabla.
- Los valores se muestran en notación de ingeniería con la unidad activa del gráfico; el prefijo
  micro se emite como `$\mu$` para que renderice en cualquier preset tipográfico.

Los cursores se dibujan en gris oscuro punteado: son instrumentación superpuesta, no datos.

---

## 8. Herramientas de anotación

Elementos pensados para marcar puntos de interés en la figura final del informe:

| Tipo | Descripción |
|---|---|
| **Punto de interés** | Marcador sobre la curva + etiqueta con recuadro y flecha guía, con desplazamiento en puntos tipográficos |
| **Flecha** | Flecha entre dos coordenadas (`->`, `<->`, `-\|>`, `<\|-\|>`), con etiqueta opcional al medio: cotas de ancho de banda, saltos de nivel |
| **Línea vertical** | Línea de referencia punteada con etiqueta rotada 90° a la altura elegida (`$f_0 = 9{,}61\,$kHz`) |
| **Línea horizontal** | Ídem sobre el eje Y (`-3\,$dB`, niveles de saturación) |
| **Texto** | Texto libre, con o sin recuadro, en coordenadas de datos |
| **Banda vertical / horizontal** | Región sombreada translúcida para destacar un intervalo (ancho de banda, zona de validez) |

Parámetros por elemento: coordenadas, texto, desplazamiento de etiqueta, color, tamaño de fuente,
estilo y grosor de línea, rotación, posición de la etiqueta a lo largo de la línea, transparencia,
recuadro y estilo de flecha.

- **Captura desde el gráfico:** los botones `Capturar X/Y` y `Capturar X₂/Y₂` toman las
  coordenadas del siguiente clic sobre el lienzo, incluido el subgráfico correcto en modo Bode
  separado.
- **Presets de estilo:** *Referencia (línea + etiqueta rotada)*, *Punto de interés (marcador +
  flecha)* y *Cota / ancho de banda (flecha doble)* cargan de una vez los valores que reproducen
  el estilo clásico de figura de informe.
- **Mathtext:** todas las etiquetas aceptan `$...$`, con la misma tipografía que el resto de la
  figura.
- Las anotaciones se dibujan con `gid` propio, así que no entran en la leyenda ni en la lectura
  de los cursores, y se re-renderizan solas después de cada cambio de escala, unidad o modo.

### Persistencia

`Guardar overlays... / Cargar overlays...` serializa cursores y anotaciones a JSON
(`{"version": 1, "cursors": {...}, "annotations": {...}}`). Permite reconstruir una figura anotada
mucho después, o versionar el conjunto junto al informe.

---

## 9. Exportación

**Datos (PGFPlots):**

- *Individual* — un CSV por señal, con encabezados `t_us,V_V` / `f_Hz,dB`, listo para:

  ```latex
  \addplot table [x=f_Hz, y=dB, col sep=comma] {datos/canal1.csv};
  ```

- *Combinado* — grilla X común interpolada (logarítmica si el dominio es frecuencial), una
  columna Y por señal. Rechaza explícitamente combinar dominios distintos, porque una grilla
  común entre tiempo y frecuencia no tiene sentido físico.
- *Modo X/Y* — exporta el par paramétrico como `x,y`.

**Figura:** PDF, SVG y PGF vectoriales; PNG rasterizado al DPI indicado. Se guarda con
`bbox_inches="tight"` y fuentes embebidas (`pdf.fonttype = 42`), manteniendo el texto
seleccionable. Los cursores y anotaciones visibles forman parte del vectorial exportado.

---

## 10. Robustez de lectura

- Detección automática de separador de campos, separador decimal (incluida la coma europea) y
  codificación (`utf-8` / `cp1252` con símbolo de grado).
- Formato AC de LTspice: celdas complejas `(-40.1dB, 89.4°)` se expanden en columnas de dB, fase
  y magnitud lineal.
- Archivos con más de dos columnas numéricas abren un diálogo de selección de columnas; cada
  columna Y marcada se convierte en una señal independiente.
- Validación explícita de tipos, control de `NaN`/vacíos y manejo defensivo de excepciones: una
  anotación mal formada o un archivo corrupto no interrumpen el graficado.

---

## 11. Atajos y notas de uso

| Acción | Cómo |
|---|---|
| Borrar la señal seleccionada | `Supr` / `Retroceso` (excepto dentro de un campo de texto) |
| Aplicar un valor numérico | `Enter` en el campo correspondiente |
| Márgenes del subgráfico | Botón *Configure subplots* de la barra de Matplotlib |
| Abrir la paleta de overlays | Botón **Cursores / Anotaciones...** sobre el canvas |

La paleta de overlays es **no modal** a propósito: hay que poder hacer clic sobre el gráfico
mientras está abierta, así que nunca captura el bucle de eventos.

---

## 12. Limitaciones conocidas

- Las anotaciones se guardan en coordenadas de datos. Si se cambia la unidad del eje (por ejemplo
  de `us` a `ms`) hay que reubicarlas: no se reescalan solas.
- El *snap* de cursores usa la muestra más cercana en el eje correspondiente, sin tener en cuenta
  la distancia perpendicular a la curva.
- `tight_layout()` puede fallar con leyendas externas muy grandes; en ese caso se conservan los
  márgenes reservados y conviene ajustarlos a mano desde *Configure subplots*.
