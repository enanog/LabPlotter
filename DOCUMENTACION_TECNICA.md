# LabPlotter - Visión General

LabPlotter es una aplicación de escritorio (Python 3.10+, CustomTkinter + Matplotlib) que resuelve un problema puntual del flujo de trabajo en ingeniería electrónica: convertir capturas crudas de osciloscopio y barridos de LTspice en figuras y tablas de calidad de publicación para informes en LaTeX, sin pasar por Excel, sin exportar manualmente cada traza y sin depender de una instalación de TeX para previsualizar cómo va a quedar la figura final.

El programa carga archivos CSV/TXT de osciloscopio (multicanal, con autodetección de separador, separador decimal y encoding) y de LTspice, incluyendo el formato complejo de barridos AC `(-40.1dB, 89.4°)`, que descompone automáticamente en magnitud, fase y módulo lineal. Sobre esos datos permite editar de forma no destructiva cada canal (offset, ganancia, inversión, recorte temporal, diezmado, unidades), superponer cursores de medición y anotaciones tipo informe, y armar hasta un "tablero" multipanel combinando varias figuras ya exportadas en una sola grilla. Todo el ajuste ocurre sobre el mismo motor de renderizado que después exporta el archivo final, así que lo que se ve en pantalla es exactamente lo que termina en el PDF.

La exportación entrega tanto el vector gráfico (PDF/SVG/PGF a 300 DPI) como el CSV con las columnas ya nombradas para `pgfplots`, y el bloque LaTeX (`figure`/`subfigure`/`axis`) que los incluye, con paths y requerimientos de paquetes resueltos automáticamente. El resultado es que el ciclo "medir en el osciloscopio o simular en LTspice → figura en el informe" se reduce a cargar el archivo, ajustar visualmente y exportar, en vez de procesar los datos a mano en cada entrega.

## Stack Tecnológico

| Categoría | Tecnología | Rol |
|---|---|---|
| Lenguaje | Python 3.10+ | Sintaxis `X \| None`, `from __future__ import annotations` en todo el código |
| GUI | CustomTkinter ≥ 5.2.0 | Widgets estilizados sobre Tkinter; solo lo usa `gui/` |
| Gráficos | Matplotlib ≥ 3.7 | Canvas embebido (`FigureCanvasTkAgg`) y exportación vectorial/rasterizada |
| Datos | Pandas ≥ 2.0 | Parseo tabular de los archivos de entrada |
| Numérico | NumPy ≥ 1.24 | Arrays de muestras, histogramas, remuestreo |
| Drag & drop | tkinterdnd2 ≥ 0.1.0 | Opcional; si falta, la app arranca igual y degrada con aviso |

**Decisiones técnicas relevantes:**

- Renderizado matemático con el motor **mathtext** interno de Matplotlib (`text.usetex = False`) en lugar de invocar una distribución TeX externa. Elimina la dependencia de un sistema LaTeX instalado y garantiza que la vista previa en pantalla y el PDF exportado compartan exactamente los mismos `rcParams` — no hay paso de "compilar para ver cómo queda".
- `core/` no importa nada de `gui/`: toda la lógica de I/O, procesamiento, exportación y generación de LaTeX es headless y se puede usar desde un script o testear sin levantar Tk.
- Persistencia de sesión y perfiles de exportación en el directorio de configuración del usuario (no en el repo), para que sobreviva a un `git clean` y no dependa de `.gitignore`.

## Arquitectura y Estructura de Archivos

```
LabPlotter/
├── main.py                    # Punto de entrada: valida dependencias e invoca gui.app.main()
├── requirements.txt
├── core/                      # Lógica headless (sin import de gui/, testeable/scriptable)
│   ├── data_io.py             # Modelo Signal + carga/parseo (CSV/TXT, LTspice, Bode complejo)
│   ├── processing.py          # crop / decimate / decimate_to_target / resample_uniform
│   ├── units.py                # Parseo de notación de ingeniería ("4u7", "2.2k", "-3dB")
│   ├── export.py              # Estilo de publicación Matplotlib + export CSV/figura
│   ├── layout.py              # Geometría de leyenda (posiciones externas, coordenadas libres)
│   ├── latex.py               # Genera bloques LaTeX (figure/subfigure/axis) + saneamiento
│   ├── board.py               # Modelo de datos del tablero multipanel (filas de paneles)
│   ├── histogram.py           # Cálculo de histogramas sobre señales ya cargadas
│   ├── history.py             # Undo/redo snapshot-based sobre el conjunto de señales
│   ├── tabs.py                # Snapshot de una pestaña completa (señales + ajustes + historial)
│   ├── session.py             # Persistencia de sesión y perfiles de exportación (fuera del repo)
│   └── i18n.py                # Catálogo de strings es/en, keyed por el string en español
└── gui/                       # Interfaz (CustomTkinter + Matplotlib embebido)
    ├── app.py                 # Ventana principal: paneles, canvas, pestañas, orquestación (App)
    ├── theme.py                # Identidad visual monocromática "laboratory paper"
    ├── widgets.py              # Controles reutilizables (Field, Segmented, TraceRow, Splitter...)
    ├── overlays.py             # Estado y render de cursores/anotaciones (solo Matplotlib/NumPy)
    ├── overlay_panel.py        # Paleta flotante no-modal que edita ese estado
    ├── board_window.py         # Ventana del tablero: arma filas de paneles y exporta el layout
    └── histogram_window.py     # Ventana auxiliar de histograma sobre señales cargadas
```

**Principio de diseño central:** separación estricta entre `core/` (lógica pura, sin dependencia de GUI, importable desde cualquier script) y `gui/` (presentación). Módulos como `overlays.py` o `histogram.py` mantienen ese mismo criterio dentro de `gui/`: guardan estado en dataclasses planas y delegan el cálculo numérico a `core/`, de forma que se puedan probar sin levantar Tk. `board.py` nunca rasteriza ni reconvierte el vector exportado — guarda el path del PDF/SVG/PGF real (`vector_path`) separado del PNG liviano usado solo para la vista previa en pantalla (`preview_path`).

## Características Principales

- **Ingesta robusta y autodetección de formato:** separador de campo (coma, punto y coma, tabulación), separador decimal, encoding (`utf-8-sig`/`utf-8`/`cp1252`/`latin-1`, cubriendo instrumentos que emiten `°` en cp1252) y presencia de encabezado, todo detectado automáticamente al leer el archivo.
- **Formato Bode de LTspice:** reconoce y descompone celdas complejas tipo `(-40.1dB, 89.4°)` en tres columnas derivadas (magnitud en dB, fase en grados, módulo lineal en V), sin intervención manual.
- **Edición no destructiva por canal:** offset y ganancia en X/Y, inversión, recorte temporal y diezmado (por factor o por cantidad objetivo de puntos), unidades de entrada configurables por notación de ingeniería (`4u7`, `2.2k`, `-3dB`, `10 kHz`) — todo aplicado sobre los datos crudos en el momento de graficar, nunca sobre el archivo original.
- **Multi-pestaña:** varios gráficos independientes (señales + ajustes propios) en memoria simultáneamente, cada uno con su propio historial de undo/redo, sin perder el trabajo al alternar entre ellos.
- **Cursores de medición y anotaciones tipo informe:** cursores arrastrables (verticales/horizontales) con lectura por curva y delta entre cursores; anotaciones con flecha líder, flechas sueltas, líneas de referencia con label rotado, texto libre y bandas sombreadas — serializables a JSON para reproducir exactamente la misma figura más adelante.
- **Tablero multipanel:** combina varias figuras ya exportadas en filas de paneles con peso relativo configurable (uno ancho, dos o tres lado a lado, grillas asimétricas), y exporta tanto los PDFs individuales como el bloque LaTeX con `subfigure` que reproduce el mismo layout.
- **Histogramas:** distribución de valores (eje X o Y) de una o más señales superpuestas, con reglas de binning de NumPy (`auto`, `sturges`, `fd`, `scott`, `sqrt`) y manejo explícito de `NaN`/`inf`.
- **Exportación de calidad de publicación:** PDF/SVG/PGF vectorial y PNG a 300 DPI configurable, con `bbox_inches="tight"`; CSV individual o combinado sobre grilla común (lineal o logarítmica) listo para `\addplot table` de `pgfplots`.
- **Generación automática de LaTeX:** bloques `figure`/`subfigure`/`axis` con paths normalizados a forward slashes (válido también viniendo de Windows), labels saneados y detección de los paquetes (`\usepackage{...}`) que el bloque generado requiere.
- **Undo/redo por snapshot:** captura los atributos editables de cada traza (no los arrays de muestras, que pueden ser millones de puntos) antes de cada cambio, evitando el riesgo de una inversa mal escrita que corrompa datos silenciosamente.
- **Persistencia de sesión:** estado completo (archivos cargados, parámetros por traza, ajustes globales, geometría de ventana) guardado automáticamente al cerrar y restaurado al abrir, más perfiles de exportación con nombre (formato, DPI, modo CSV, coma decimal).
- **Interfaz bilingüe (es/en):** catálogo de strings keyed por el texto en español mismo, de forma que un string sin traducir cae a español legible en vez de romper la pantalla.
- **Drag & drop opcional:** soltar archivos `.csv`/`.txt` directamente sobre la ventana cuando `tkinterdnd2` está disponible; si no lo está, la aplicación arranca igual sin esa función.

## Flujo de Ejecución / Uso

**Arranque:** `python main.py` inserta la raíz del repo en `sys.path` e importa `gui.app.main`; si falta una dependencia, imprime la instrucción de instalación (`pip install -r requirements.txt`) en vez de fallar con un traceback crudo. `gui.app.main()` carga la sesión guardada del run anterior (`core/session.py`), fija idioma y tema **antes** de instanciar la ventana — necesario porque CustomTkinter lee el diccionario de tema al construir cada widget — y recién ahí levanta `App` (`ctk.CTk`).

**Carga de datos:** el usuario abre archivo(s) por diálogo o drag & drop. `core.data_io.read_table()` autodetecta formato y devuelve un `DataFrame`; un diálogo de selección de columnas define qué par tiempo/valor (o X/Y) usar, y `core.data_io.build_signal()` arma un `Signal` por traza — sus arrays `t_raw`/`v_raw` quedan intactos tal como se leyeron del archivo.

**Ajuste interactivo:** cada cambio de parámetro sobre una traza (offset, ganancia, unidad, recorte, diezmado, estilo) se aplica sobre el `Signal` en memoria; `Signal.processed()` recalcula `(t, v)` en la unidad base del dominio (s/Hz, V/dB/deg) con offset, ganancia e inversión aplicados, sin tocar nunca los datos crudos. Antes de cada cambio, `core/history.py` guarda un snapshot de los atributos editables para permitir undo/redo. `App.update_plot()` limpia la figura y regrafica cada señal procesada, reaplica cursores/anotaciones (`gui/overlays.py`) y la cosmética de ejes y leyenda (`core/layout.py`).

**Exportación:** al exportar, `core.export.set_publication_style()` fija los `rcParams` de publicación (los mismos que ya rigen la vista previa) y `export_figure()`/`export_csv_*()` generan el vector gráfico y el CSV; `core.latex` produce el bloque LaTeX correspondiente, listo para pegar en el informe con el path y los `\usepackage` correctos. Una figura exportada puede además agregarse al tablero (`core.board`), que acumula paneles hasta que `gui/board_window.py` los organiza en filas y exporta el layout completo (PDFs individuales + LaTeX con `subfigure`).

**Cierre:** `App` persiste el estado completo (señales, pestañas, ajustes, geometría) en `session.json` dentro del directorio de configuración del usuario; un fallo de lectura/escritura de ese archivo nunca impide que la aplicación arranque o se cierre.
