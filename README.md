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
  units.py              # parseo de notación de ingeniería en campos numéricos (4u7, 2.2k, -3dB)
  export.py             # estilo de publicación y exportación de datos/figuras
  layout.py             # geometría de leyenda (posiciones externas y coordenadas libres)
  latex.py              # genera el bloque \figure / \subfigure listo para pegar en el informe
  board.py              # modelo de datos del tablero multi-panel (filas de paneles con peso)
  history.py            # undo/redo snapshot-based sobre el conjunto de señales
  tabs.py               # snapshot de una pestaña completa (señales + ajustes + historial)
  session.py            # persistencia de sesión y perfiles de exportación (fuera del repo)
  i18n.py               # catálogo de strings es/en, keyed por el string en español
gui/
  app.py                # ventana principal: paneles, canvas, pestañas y orquestación
  theme.py              # identidad visual monocromática (CustomTkinter + chrome Matplotlib)
  widgets.py            # controles reutilizables (campos, secciones colapsables, chips, etc.)
  overlays.py           # estado y render de cursores y anotaciones (solo Matplotlib/NumPy)
  overlay_panel.py      # paleta flotante que edita ese estado
  board_window.py       # ventana del tablero: arma filas de paneles y exporta el layout
```

### 2.1 `core/` — alcance y funciones de cada módulo

Sin ninguna dependencia de la GUI: cada punto se puede importar y usar desde un script o probar
de forma aislada (ver sección 13).

**`data_io.py`** — carga y parseo de archivos de osciloscopio, LTspice (dominio temporal) y Bode
de LTspice (`(-40.1dB,89.4°)`); detecta automáticamente separador de campo, separador decimal y
codificación, y descompone las celdas complejas de Bode en dB/fase/módulo lineal.

- `Signal` — dataclass con las muestras crudas, offsets, ganancia, inversión y todo ajuste visual
  no destructivo de un canal.
- `read_table(path, decimal_comma=False)` — lee cualquier CSV/TXT soportado con autodetección de
  formato.
- `build_signal(df, time_col, value_col, name, source_path, domain, y_kind, color)` — arma un
  `Signal` a partir de dos columnas ya leídas.
- `x_units_for_domain(domain)` / `y_units_for_kind(y_kind)` — unidades disponibles según el
  dominio (tiempo/frecuencia) o el tipo de magnitud (voltage/dB/deg).

**`processing.py`** — recorte temporal y diezmado sobre arrays ya en unidades SI.

- `crop(t, v, t_min, t_max)` — recorta al intervalo `[t_min, t_max]` en segundos.
- `decimate(t, v, factor)` — toma 1 de cada `factor` muestras.
- `decimate_to_target(t, v, target_points)` — diezma para acercarse a una cantidad de puntos
  objetivo.
- `resample_uniform(t, v, n_points)` — remuestrea a una grilla uniforme de `n_points`.

**`units.py`** — parseo de notación de ingeniería en los campos numéricos de la GUI (`4u7`,
`2.2k`, `-3dB`, `10 kHz`).

- `parse_eng(text, fallback=None)` / `parse_eng_or(text, fallback=0.0)` — texto → float en
  unidades SI base; la variante `_or` nunca devuelve `None`.
- `format_eng(value, unit="", digits=4)` — float → texto en notación de ingeniería para mostrar
  en pantalla.

**`export.py`** — estilo de publicación de Matplotlib y exportación de datos/figuras; fuerza
`text.usetex = False` y renderiza todo con el motor **mathtext** interno.

- `set_publication_style(font_family, base_fontsize, ...)` — aplica fuente, grillas y tamaños
  antes de graficar (la misma función que usa la GUI y cualquier script headless).
- `export_csv_individual(...)` / `export_csv_combined(...)` — CSV por señal o sobre una grilla X
  común, listos para `\addplot table`.
- `export_xy_csv(...)` — exporta el par paramétrico de modo X/Y.
- `export_figure(fig, out_path, dpi=300, ...)` — guarda PDF/SVG/PGF vectorial o PNG rasterizado
  con `bbox_inches="tight"`.
- `_resolve_font(candidates)` — resuelve la primera fuente instalada de una lista de preferencia
  (p. ej. Latin Modern Roman → CMU Serif → cmr10) sin romper si ninguna está instalada.

**`layout.py`** — geometría de leyenda; vive separado de `export.py` porque exportar datos y
posicionar una leyenda son responsabilidades distintas.

- `is_outside(position)` — indica si una posición de leyenda cae fuera del área de ejes.
- `legend_kwargs(position, ...)` — traduce una posición (incluidas las `outside …` y la
  personalizada por coordenadas) a kwargs de `Axes.legend()`.
- `reserve_legend_space(...)` — reserva el margen que `tight_layout()` no puede calcular solo
  cuando la leyenda queda anclada fuera del área de ejes.

**`latex.py`** — genera el bloque LaTeX que reproduce en el informe lo que la GUI acaba de
exportar, con paths siempre en forward slashes.

- `figure_block(path, caption, label, ...)` — bloque `figure` completo
  (`\includegraphics`/`\input` según el tipo de archivo).
- `board_block(rows, caption, label, ...)` — bloque `figure` con un `subfigure` por panel del
  tablero, reproduciendo el mismo layout de filas.
- `axis_block(plots, xlabel, ylabel, ...)` — bloque `pgfplots` a partir de los CSV exportados.
- `sanitize_label(text, prefix="fig")` / `escape(text)` / `latex_path(path, relative_to=None)` —
  utilidades de saneamiento de labels, texto y paths para los bloques anteriores.
- `figure_requirements(path)` / `board_requirements()` — paquetes LaTeX (`\usepackage{...}`) que
  necesita el bloque generado.

**`board.py`** — modelo de datos del tablero multi-panel: filas de `BoardPanel`, cada panel con
título y peso relativo dentro de su fila.

- `BoardPanel` — un panel: figura ya exportada, título y peso.
- `new_row()` — fila vacía para empezar a agregar paneles.
- `validate_board(rows)` — errores de layout (fila vacía, panel sin archivo, etc.) antes de
  exportar.
- `compose_preview_figure(...)` — arma la figura combinada que se previsualiza en
  `gui/board_window.py`.
- `export_individual_pdfs(rows, out_dir)` — copia cada panel a `out_dir`, de-duplicando nombres
  por el archivo de salida real (base + sufijo + extensión), no solo por el slug base.
- `slugify_filename(text, fallback="panel")` — título de panel → nombre de archivo válido.

**`history.py`** — undo/redo *snapshot-based* sobre el conjunto de señales (ver sección 15).

- `Snapshot` — copia de los atributos mutables de cada señal más el orden, en un punto del tiempo.
- `History` — pila de snapshots con `undo`/`redo`.
- `apply_snapshot(snapshot, signals, order, ...)` — restaura un snapshot sobre el estado vivo.

**`tabs.py`** — snapshot de una pestaña completa (ver sección 14).

- `PlotTab` — nombre + `state` (lo que devuelve `App._gather_plot_state`) + `history` propio de
  esa pestaña.

**`session.py`** — persistencia de sesión y perfiles de exportación en JSON, fuera del
repositorio (bajo el directorio de configuración del usuario).

- `load_session()` / `save_session(state)` / `clear_session()` — sesión completa ("qué había en
  pantalla la última vez").
- `load_profiles()` / `save_profiles(profiles)` / `upsert_profile(name, values)` /
  `delete_profile(name)` — perfiles de exportación con nombre.
- `save_figure_state(fig_path, state)` / `load_figure_state(path)` — el sidecar
  `<figura>.labplotter.json` que usa "Importar figura..." (sección 9).
- `config_dir()` — directorio de configuración resuelto según el sistema operativo.

**`i18n.py`** — catálogo de strings es/en, keyed por el string en español (ver sección 18).

- `t(text)` — traduce si hay entrada para el idioma activo; si no, devuelve el español recibido.
- `set_language(code)` / `get_language()` / `language_name(code=None)` — idioma activo.
- `missing()` / `coverage()` — strings sin traducir y cobertura, para detectar huecos.

### 2.2 `gui/` — alcance y funciones de cada módulo

**`app.py`** — ventana principal: `App(ctk.CTk)` orquesta todo, agrupado por responsabilidad
(125 métodos en total; no se listan uno por uno):

- *Ciclo de vida y sesión* — `__init__`, `_persisted_vars`, `_gather_state`/`_apply_state`,
  `_restore_session_if_any`, `_save_session_soon`, `_on_close`.
- *Pestañas* — `_gather_plot_state`/`_apply_plot_state`, `_switch_tab`, `_add_tab`, `_close_tab`,
  `_rename_tab`, `_refresh_tab_strip`.
- *Carga de archivos* — `_load_files`, `_ingest_files`, `_enable_drag_and_drop` y sus callbacks
  `_on_files_dropped`/`_on_drop_enter`/`_on_drop_leave`, `ColumnSelectDialog` (selección de
  columnas Y en archivos multicanal).
- *Panel de señales y parámetros* — `_refresh_signal_list`, `_build_param_panel`,
  `_sync_unit_options`, `_move_signal`, `_remove_selected_signal`/`_remove_all_signals`,
  `_pick_row_color`.
- *Graficado* — `update_plot`, `_draw_standard` (Tiempo/Frecuencia), `_draw_bode`, `_draw_xy`,
  `_gather_curves`, `_apply_axis_cosmetics`, `_decorate_legend`/`_finish_legend`.
- *Deshacer/rehacer* — `_snapshot`, `_record`, `_undo`, `_redo`, `_apply_history` (ver sección 15).
- *Exportación* — `_export_csv`, `_export_figure`, `_import_figure`, `_show_latex_figure`.
- *Tablero* — `_add_current_to_board`, `_open_board_window`.
- *Overlays* — `_refresh_overlays`, `_open_overlay_window`, `_on_overlay_closed`.
- *Ajustes globales / tema / idioma* — `_build_right_panel` y sus secciones
  (`_build_axes_section`, `_build_labels_section`, `_build_legend_section`, `_build_data_section`,
  `_build_export_section`), `_on_theme_change`, `_on_language_change`, `_on_font_change`.
- `SubplotConfigDialog` — diálogo de márgenes de subgráfico (equivalente propio al *Configure
  subplots* de Matplotlib, con persistencia).
- `EditableNavigationToolbar` — barra de herramientas de Matplotlib extendida.
- `main()` — punto de entrada real que usa `main.py`.

**`theme.py`** — identidad visual monocromática, aplicada en dos capas (ver sección 4).

- `apply_theme(mode="light")` / `set_theme_mode(mode)` — instala/cambia el tema sobre el árbol de
  widgets ya construido, sin reconstruirlo.
- `apply_plot_chrome(fig)` / `style_matplotlib_toolbar(toolbar, mode="light")` — chrome de
  Matplotlib (ejes, grilla, toolbar) a tono con el tema activo.
- `col(token)` / `tk_color(token)` — color `[claro, oscuro]` resuelto para el modo activo.
- `font(role="body", size=None, ...)` / `family(role="serif")` — tipografía consistente por rol.
- `set_font_scale(factor)` / `font_scale()` — escala global de fuente de la interfaz.
- `spaced(text)` — texto con tracking manual, para los títulos de sección en versalitas.

**`widgets.py`** — primitivas estilizadas compartidas por toda la interfaz; centralizarlas es lo
que impide que un widget se aparte del tema (ningún color o fuente se escribe en el sitio de uso).

- Campos: `entry_field`, `text_field`, `combo_field`, `check_field`, `segmented_field`,
  `stacked_entry`, `SliderField`, `LabeledCombo`.
- Estructura: `StaticSection`/`SectionGroup` (secciones colapsables), `SectionHeader`, `Rule`/
  `VRule` (filetes), `Splitter` (panel redimensionable).
- Controles: `primary_button`, `ghost_button`, `ToolButton`, `Segmented`, `Chip`, `hint`.
- Filas y diálogos específicos: `TraceRow` (fila de señal en el panel izquierdo),
  `MeasurementsCard`, `TextPrompt`, `ShortcutsWindow`, `CodeDialog`.

**`overlays.py`** — estado e interacción de cursores y anotaciones sobre el canvas; solo
Matplotlib/NumPy, sin CustomTkinter (ver secciones 7 y 8).

- `CursorManager` — banco de cursores: colocación, arrastre con *snap*, lectura por curva y
  cálculo de deltas entre cursores de la misma orientación.
- `AnnotationManager` — anotaciones de calidad de informe: puntos de interés, flechas, líneas y
  bandas de referencia, texto libre.
- `CursorSpec` / `AnnotationSpec` — dataclasses de un cursor/anotación individual.
- `save_overlays(path, cursors, ...)` / `load_overlays(path, cursors, ...)` — serialización a
  JSON del conjunto completo.
- `_crossings(x, y, level, ...)` — cruces de una curva con un nivel dado (lectura de cursor
  horizontal), interpolados linealmente.

**`overlay_panel.py`** — frente CustomTkinter de `gui.overlays`: paleta flotante no modal
(nunca llama `grab_set()`, porque hay que poder clickear el canvas con la paleta abierta).

- `OverlayPanel` — el contenido de la paleta: banco de cursores + editor de anotaciones.
- `OverlayWindow` — el `CTkToplevel` que aloja al panel anterior.
- `refresh_cursor_ui()` — sincroniza los campos del cursor seleccionado tras un arrastre.

**`board_window.py`** — ventana del tablero: arma filas de paneles ya exportados, previsualiza el
layout y exporta los archivos individuales más el bloque LaTeX (ver sección 17).

- `BoardWindow` — edita `app.board_rows` in place: agregar un panel desde la ventana principal
  mientras el tablero está cerrado, o reabrirlo después, siempre ve el estado vivo.
- `_parse_weight(text, fallback=1.0)` — texto de peso de panel → float, con valor por defecto si
  no es un número válido.

Separación de responsabilidades:

- **`core/`** no importa nada de la GUI ni de CustomTkinter. Es scriptable y testeable de forma
  aislada (ver sección 13); `core/i18n.py`, `core/history.py`, `core/tabs.py` y `core/board.py`
  son datos y lógica pura por la misma razón, aunque solo la GUI los use en la práctica.
- **`gui/overlays.py`** no importa CustomTkinter: guarda *estado* (dataclasses), no widgets. Por
  eso los cursores y las anotaciones sobreviven al ciclo completo de `fig.clear()` + re-graficado
  que ejecuta `App.update_plot()`, y por eso el conjunto puede serializarse a JSON y recargarse
  para reproducir una figura idéntica.
- **`gui/overlay_panel.py`** es el único módulo que conoce widgets de overlays; edita el estado y
  pide a la aplicación que vuelva a renderizar la capa mediante un callback.
- **`gui/board_window.py`** edita `app.board_rows` (`list[core.board.BoardRow]`) in place: no hay
  una copia separada que sincronizar entre la ventana principal y la del tablero.

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
customtkinter>=5.2.0   # solo GUI (main.py)
matplotlib>=3.7        # GUI + core/ headless
numpy>=1.24            # GUI + core/ headless
pandas>=2.0            # GUI + core/ headless
tkinterdnd2>=0.1.0     # solo GUI: arrastrar y soltar archivos (opcional, ver más abajo)
```

`tkinterdnd2` es la única dependencia con degradación explícita: si no está instalada, `gui/app.py`
lo detecta en el import (`try/except ImportError`) y deshabilita el *drag & drop* mostrando un
aviso en la interfaz, en vez de fallar al arrancar. El resto de la GUI funciona igual.

Para usar únicamente `core/` desde un script, sin la GUI (ver sección 13), no hace falta
`customtkinter` ni `tkinterdnd2`: alcanza con

```bash
pip install matplotlib pandas numpy
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

Ajustes por canal: offsets, ganancia (solo tiene efecto en señales de tipo `voltage`; para dB o
fase se usa el offset en Y), inversión, estilo y grosor de línea, marcador (con tamaño y variante
hueca/rellena), color, etiqueta de leyenda, eje secundario, dominio y tipo de magnitud. Ajustes
globales: unidades, escalas lineal/logarítmica,
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

**Importar figura:** cada exportación deja, junto al archivo de la figura, un sidecar
`<nombre>.labplotter.json` con los datos, señales y ajustes que la generaron. `Importar
figura...` lee ese sidecar y reconstruye la pestaña completa (equivalente a abrir el `.json` de
sesión de esa figura puntual), para retomar y ajustar una figura ya exportada sin tener que
recordar qué offsets, colores o recorte se usaron.

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
| Deshacer / rehacer | `Ctrl+Z` / `Ctrl+Y` (ver sección 15) |
| Cargar archivos | Diálogo estándar, o arrastrarlos y soltarlos sobre la ventana (`tkinterdnd2`; se avisa en la interfaz si no está instalado) |
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

---

## 13. Uso headless (scripting con `core/`)

`core/` no importa nada de `gui/` ni de CustomTkinter (ver sección 2), así que puede usarse
directamente desde un script de Python sin abrir la aplicación. Esto sirve para regenerar en
lote las figuras de un informe (por ejemplo, cada vez que se corrige un dato crudo) con
exactamente el mismo estilo de publicación que produce la GUI.

### Requisitos

Solo las dependencias numéricas/de graficado, sin `customtkinter`:

```bash
pip install matplotlib pandas numpy
```

### Flujo mínimo

```python
import sys
sys.path.insert(0, "/ruta/a/LabPlotter")   # o agregar LabPlotter al PYTHONPATH

import matplotlib
matplotlib.use("Agg")                       # sin ventana: solo exportar a archivo
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from core.export import set_publication_style, export_figure

# 1) Aplicar el mismo estilo que usa la GUI (fuente, grillas, tamaños).
set_publication_style("LaTeX (Computer Modern)", base_fontsize=10)

# 2) Cargar datos. `core.data_io.read_table` hace la detección automática de
#    separador/encoding/formato Bode si el archivo viene directo de un
#    osciloscopio o de LTspice; para un CSV ya limpio, `pandas.read_csv` alcanza.
df = pd.read_csv("data/processed/mi_medicion_clean.csv")

# 3) Graficar con Matplotlib estándar.
fig, ax = plt.subplots(figsize=(14.9 / 2.54, 6 / 2.54))   # cm -> inch
ax.plot(df["time_us"], df["Vin_V"], "--", label=r"$v(t)$")
ax.plot(df["time_us"], df["Vc_V"], "-", label=r"$v_C(t)$")
ax.set_xlabel(r"Tiempo [$\mu$s]")
ax.set_ylabel("Tensión [V]")
ax.grid(True, which="both")
ax.legend(loc="upper right")
fig.tight_layout()

# 4) Exportar. `export_figure` aplica bbox_inches="tight" y respeta el dpi/formato
#    según la extensión (.pdf/.svg/.pgf vectorial, .png rasterizado).
export_figure(fig, "assets/plots/mi_figura.pdf")
```

Para un eje de frecuencia logarítmico con notación de ingeniería (100, 1k, 10k, ...) como el
que usa la GUI:

```python
ax.set_xscale("log")
ax.xaxis.set_major_formatter(mticker.EngFormatter(unit="", sep=""))
```

Funciones de `core/` más usadas en scripts:

| Módulo | Función | Para qué |
|---|---|---|
| `core.export` | `set_publication_style(font_family, base_fontsize)` | Aplica el estilo de publicación (fuente, grillas, tamaños) antes de graficar. |
| `core.export` | `export_figure(fig, out_path, dpi=300)` | Guarda PDF/SVG/PGF vectorial o PNG rasterizado, con `bbox_inches="tight"`. |
| `core.export` | `export_csv_individual` / `export_csv_combined` | Exportan CSV listos para `\addplot table` de pgfplots, si en vez de una figura se necesita solo la tabla. |
| `core.data_io` | `read_table(path, decimal_comma=False)` | Lee un archivo de osciloscopio/LTspice con detección automática de formato. |
| `core.data_io` | `build_signal(df, ...)` | Arma un `Signal` (offset/ganancia/inversión no destructivos) a partir de dos columnas. |
| `core.processing` | `crop`, `decimate`, `decimate_to_target` | Recorte temporal y diezmado antes de graficar/exportar. |
| `core.layout` | `legend_kwargs(position, ...)` | Traduce una posición de leyenda (incluidas las "outside …") a kwargs de `Axes.legend()`. |
| `core.latex` | `figure_block(path, caption, label, ...)` | Genera el bloque `figure` de LaTeX completo (`\includegraphics`/`\input`, caption, label) listo para pegar. |

### Ejemplo real

El script [`generar_figuras.py`](../25.13-LaboratorioDeElectrónica%201/TPN1/scripts/generar_figuras.py)
del informe TPN1 usa exactamente este flujo para regenerar sus 13 figuras (Bode, respuesta a
onda cuadrada/triangular, mediciones punto a punto) a partir de los CSV/TXT crudos, reemplazando
los antiguos diagramas `pgfplots` embebidos en el `.tex` por `\includegraphics` de los PDF
exportados. Sirve como referencia de cómo estructurar un script de generación en lote para un
informe con muchas figuras.

---

## 14. Pestañas

Cada pestaña (`+` en la barra superior) es un plot completo e independiente: sus propias señales,
modo de graficado y ajustes. Cambiar de pestaña guarda instantáneamente el estado de la que se
deja (incluida su propia pila de deshacer/rehacer, ver sección 15) y restaura el de la que se
abre; no comparten nada entre sí salvo, opcionalmente, terminar en el mismo tablero (sección 17).
Se persisten en la sesión (nombre y ajustes; el historial de deshacer no, ver sección 15).

## 15. Deshacer / rehacer

`Ctrl+Z` / `Ctrl+Y` deshacen y rehacen cualquier cambio sobre el conjunto de señales: carga y
borrado de canales, todo ajuste por canal (offsets, ganancia, inversión, color, estilo y grosor
de línea, marcador, eje secundario, etiqueta de leyenda) y su orden. La estrategia es por
*snapshot* y no por comando (ver `core/history.py`): antes de cada acción se guarda una copia de
los atributos mutables de cada señal (no de las muestras crudas, que pueden ser millones de
puntos), así que deshacer nunca necesita una inversa escrita a mano por acción, a costa de algo
más de memoria por paso. El historial es propio de cada pestaña y no sobrevive a un cierre y
reapertura de la aplicación (no tendría sentido restaurarlo entre sesiones distintas).

## 16. Sesión y perfiles de exportación

`core/session.py` guarda automáticamente, al cerrar la aplicación, todo lo necesario para
continuar donde se dejó: archivos cargados, ajustes por canal, ajustes globales, geometría de
ventana y anchos de panel, en un JSON fuera del repositorio (bajo el directorio de configuración
del usuario, así que sobrevive a un `git clean` y no necesita entrada en `.gitignore`). Se
restaura solo al abrir. Un archivo de sesión corrupto o ausente nunca impide arrancar: se ignora
y se empieza de cero.

Aparte de la sesión, se pueden guardar **perfiles de exportación** con nombre (formato, DPI, modo
de CSV, coma decimal) para cambiar entre configuraciones de exportación habituales sin tener que
reconfigurar cada campo.

## 17. Tablero multi-panel

Además de exportar una figura a la vez, cada plot puede agregarse como panel a un **tablero**
(`core.board`, ventana en `gui/board_window.py`): varias figuras ya generadas se acomodan en
filas, cada panel con su propio título y peso relativo dentro de la fila (paneles iguales, uno
grande arriba y dos chicos abajo, un grid de 2x3 como tres filas de a dos, etc.). El tablero
exporta los archivos vectoriales individuales de cada panel más el bloque LaTeX
(`core.latex.board_block`) que reproduce exactamente ese mismo layout en el informe, vía
`subfigure`.

## 18. Interfaz bilingüe

Toda la interfaz (`core/i18n.py`) está disponible en español e inglés, con el conmutador
**Idioma** en el panel de ajustes globales. El catálogo está *keyed* por el string en español, no
por un identificador inventado: una traducción faltante cae de nuevo en el español legible en vez
de mostrar una clave cruda, así que un hueco de traducción es un detalle cosmético y no una
pantalla rota. Cambiar el idioma reconstruye los paneles en el acto, para que lo que está en
pantalla y el idioma activo nunca queden desincronizados.
