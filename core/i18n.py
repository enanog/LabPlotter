"""
core/i18n.py
------------
Spanish/English user interface strings.

The catalogue is keyed by the **Spanish string itself** rather than by an
invented identifier. That choice matters here for two reasons:

* an untranslated string falls back to readable Spanish instead of showing a
  raw key like `settings.axes.title` to the user, so a missed entry is a
  cosmetic gap rather than a broken screen;
* call sites read as `t("Escala X")`, which stays legible in the source and
  means converting the application could proceed file by file without any
  intermediate state where the UI was half-broken.

The cost is that changing Spanish wording orphans its translation. The
`missing()` helper exists for exactly that: it reports which strings passed
through `t()` without a translation, so drift is visible instead of silent.
"""

from __future__ import annotations

from typing import Optional

LANGUAGES: dict[str, str] = {"es": "Español", "en": "English"}
DEFAULT_LANGUAGE = "es"

_language = DEFAULT_LANGUAGE
_missing: set[str] = set()

# --------------------------------------------------------------------------- #
# Spanish -> English
# --------------------------------------------------------------------------- #
_EN: dict[str, str] = {
    # --- window, top bar, generic actions ---------------------------------
    "LabPlotter": "LabPlotter",
    "+  Abrir archivo": "+  Open file",
    "Quitar": "Remove",
    "Quitar todas": "Remove all",
    "Aplicar": "Apply",
    "Aplicar cambios": "Apply changes",
    "Cancelar": "Cancel",
    "Cerrar": "Close",
    "Guardar": "Save",
    "Copiar": "Copy",
    "Restablecer": "Reset",
    "Eliminar": "Delete",
    "Aceptar": "OK",
    "Sí": "Yes",
    "No": "No",
    "más": "more",

    # --- panels and sections ---------------------------------------------
    "Trazas": "Traces",
    "Ajustes": "Settings",
    "Ajustes de la traza": "Trace settings",
    "Ejes y escalas": "Axes and scales",
    "Leyenda": "Legend",
    "Datos": "Data",
    "Exportar": "Export",
    "Mediciones": "Measurements",
    "Lectura": "Readout",
    "Cursores": "Cursors",
    "Anotaciones": "Annotations",
    "Opciones de cursor": "Cursor options",
    "Estilo": "Style",
    "Posición de la etiqueta": "Label position",
    "Atajos": "Shortcuts",
    "Márgenes": "Margins",

    # --- tool strip -------------------------------------------------------
    "Tiempo": "Time",
    "X / Y": "X / Y",
    "Bode": "Bode",
    # "Whiteboard", not "Board": "Tablero" (the multi-figure report-layout
    # feature, ~12 entries below) already translates to "Board" -- two
    # unrelated Spanish words mapping to the same English word would show
    # "Board" for two completely different features in English mode.
    "Pizarra": "Whiteboard",
    "Cursor": "Cursor",
    "Anotar": "Annotate",
    "Zoom": "Zoom",
    "Mover": "Pan",
    "Compacto": "Compact",
    "Disposición": "Layout",
    "Juntos": "Together",
    "Separados": "Separate",
    "Minimizar todo": "Minimise all",
    "Expandir todo": "Expand all",

    # --- axes and scales --------------------------------------------------
    "Unidad X": "X unit",
    "Unidad Y": "Y unit",
    "Unidad Y1": "Y1 unit",
    "Unidad Y2": "Y2 unit",
    "Escala X": "X scale",
    "Escala Y": "Y scale",
    "lineal": "linear",
    "log": "log",
    "X mín": "X min",
    "X máx": "X max",
    "Notación de ingeniería": "Engineering notation",
    "Grilla": "Grid",
    "Grilla menor": "Minor grid",
    "Vacío = sin límite, en la unidad X elegida.":
        "Empty = no limit, in the selected X unit.",

    # --- labels and typography -------------------------------------------
    "Título": "Title",
    "Etiqueta X": "X label",
    "Etiqueta Y": "Y label",
    "Etiqueta Y2": "Y2 label",
    "Fuente": "Font",
    "Tamaño de fuente": "Font size",
    "Afecta ejes y ticks; el título usa un punto más. La leyenda "
    "usa un punto menos salvo que se le fije un tamaño propio "
    "en la sección «Leyenda».":
        "Affects axes and ticks; the title is one point larger. The legend "
        "is one point smaller unless it is given its own size in the "
        "“Legend” section.",
    "Tema": "Theme",
    "Claro": "Light",
    "Oscuro": "Dark",
    "Idioma": "Language",
    "Márgenes del gráfico...": "Plot margins...",
    "Aceptan mathtext: $V_{out}$, $^\\circ$.":
        "Mathtext accepted: $V_{out}$, $^\\circ$.",

    # --- legend -----------------------------------------------------------
    "Mostrar leyenda": "Show legend",
    "Posición": "Position",
    "X (fracción)": "X (fraction)",
    "Y (fracción)": "Y (fraction)",
    "Columnas": "Columns",
    "Marco de la leyenda": "Legend frame",
    "Título de la leyenda": "Legend title",
    "Vacío = un punto menos que el tamaño de fuente general.":
        "Empty = one point smaller than the general font size.",
    "Una por línea. Se agregan al final de la leyenda sin curva asociada.":
        "One per line. Appended to the legend with no associated curve.",
    "X/Y y anclaje sólo aplican con «personalizada (x, y)». "
    "Fuera de [0, 1] la leyenda sale del área del gráfico.":
        "X/Y and anchor only apply with \u201ccustom (x, y)\u201d. "
        "Outside [0, 1] the legend sits beyond the plot area.",

    # --- data -------------------------------------------------------------
    "Ninguno": "None",
    "Factor N": "Factor N",
    "Máx. puntos": "Max points",
    "Valor": "Value",
    "Archivos con coma decimal": "Files use comma as decimal separator",
    "«Factor N» conserva 1 de cada N muestras; «Máx. puntos» "
    "reduce hasta esa cantidad.":
        "\u201cFactor N\u201d keeps 1 sample in N; \u201cMax points\u201d "
        "reduces to that count.",
    "Sólo afecta el dibujo en pantalla; la exportación siempre "
    "usa todos los puntos. 0 = sin límite.":
        "Affects on-screen drawing only; export always uses every point. "
        "0 = no limit.",

    # --- export -----------------------------------------------------------
    "Perfil de exportación": "Export profile",
    "Guardar como...": "Save as...",
    "Datos para PGFPlots": "Data for PGFPlots",
    "Individual (1 archivo por señal)": "Individual (1 file per signal)",
    "Combinado (grilla común)": "Combined (shared grid)",
    "Exportar CSV...": "Export CSV...",
    "Exportar figura...": "Export figure...",
    "Importar figura...": "Import figure...",
    "Recupera una figura exportada antes con TODOS sus ajustes "
    "y señales, en una pestaña nueva. Necesita el archivo "
    "«.labplotter.json» que se guarda junto a la figura.":
        "Recovers a previously exported figure with ALL its settings "
        "and signals, in a new tab. Needs the "
        "“.labplotter.json” file saved alongside the figure.",
    "Ajustes de LabPlotter": "LabPlotter settings",
    "Todos los archivos": "All files",
    "No se pudo importar": "Couldn't import",
    "«{name}» no es un archivo de ajustes de LabPlotter válido "
    "(o es de una versión incompatible).":
        "“{name}” is not a valid LabPlotter settings file "
        "(or it's from an incompatible version).",
    "Faltan archivos de origen": "Missing source files",
    "{missing} de {total} señal(es) no se pudieron recargar: "
    "el archivo de datos original ya no está en la misma ruta "
    "que cuando se exportó la figura.":
        "{missing} of {total} signal(s) couldn't be reloaded: "
        "the original data file is no longer at the same path "
        "it was at when the figure was exported.",
    "Formato": "Format",
    "Modo X/Y: se exporta la curva actual.": "X/Y mode: exports the current curve.",
    "PDF, SVG y PGF son vectoriales; el DPI sólo afecta al PNG.":
        "PDF, SVG and PGF are vector formats; DPI only affects PNG.",
    "Incluir en LaTeX": "Include in LaTeX",
    "Ancho": "Width",
    "Escapar caracteres especiales del caption "
    "(desactivalo si escribís $matemática$)":
        "Escape special characters in the caption "
        "(turn off if you write $math$)",

    # --- plot tabs (several plots held in memory at once) -------------------
    "Gráfico 1": "Plot 1",
    "Gráfico {n}": "Plot {n}",
    "Cerrar pestaña": "Close tab",
    "¿Cerrar «{name}»? Se pierden sus señales y ajustes.":
        "Close “{name}”? Its signals and settings will be lost.",
    "Renombrar pestaña": "Rename tab",
    "Nombre de la pestaña:": "Tab name:",

    # --- board (multi-figure layout) ---------------------------------------
    "Tablero (varias figuras en un mismo layout)":
        "Board (several figures in one layout)",
    "Título del panel": "Panel title",
    "+ Agregar gráfico actual": "+ Add current plot",
    "+ Agregar actual": "+ Add current",
    "Ver tablero...": "View board...",
    "Ver...": "View...",
    "Tablero vacío.": "Board is empty.",
    "Cargá y configurá al menos una señal antes de agregar el gráfico al tablero.":
        "Load and configure at least one signal before adding the plot to the board.",
    "Error al agregar al tablero": "Error adding to board",
    "Carpeta donde se guardan las figuras del tablero":
        "Folder where the board's figures are saved",
    "Tablero de figuras": "Figure board",
    "+ Nueva fila": "+ New row",
    "Epígrafe general del tablero": "Board's overall caption",
    "El label se autocompleta a partir del epígrafe si se deja vacío. "
    "El epígrafe de cada figura se edita arriba, panel por panel.":
        "The label is auto-filled from the caption when left empty. "
        "Each figure's own caption is edited above, panel by panel.",
    "Exportar tablero...": "Export board...",
    "Fila": "Row",
    "Eliminar fila": "Delete row",
    "← → reordena dentro de la fila · ↑ ↓ pasa el panel a la "
    "fila de arriba/abajo · ▲ ▼ (junto a cada fila) reordena filas.":
        "← → reorders within the row · ↑ ↓ moves the panel to the "
        "row above/below · ▲ ▼ (next to each row) reorders rows.",
    "Epígrafe": "Caption",
    "Vacía -- agregá un gráfico desde la ventana principal.":
        "Empty -- add a plot from the main window.",
    "El tablero está vacío.": "The board is empty.",
    "Tablero incompleto": "Incomplete board",
    "Carpeta de destino para los PDFs del tablero":
        "Destination folder for the board's PDFs",
    "Figuras del ensayo": "Test figures",
    "archivo(s) copiados a": "file(s) copied to",
    "Vista previa (no vectorial)": "Preview (non-vector)",
    "Incluir tablero en LaTeX": "Include board in LaTeX",
    "Escapar caracteres especiales de los títulos "
    "(desactivalo si escribís $matemática$)":
        "Escape special characters in the titles "
        "(turn off if you write $math$)",

    # --- trace settings ---------------------------------------------------
    "Color": "Colour",
    "Izq": "Left",
    "Der": "Right",
    "«Der» usa un eje Y2 con escala propia.":
        "\u201cRight\u201d uses a Y2 axis with its own scale.",
    "Desplazar en X": "Shift in X",
    "Desplazar en Y": "Shift in Y",
    "Invertir (×−1)": "Invert (\u00d7\u22121)",
    "Nombre": "Name",
    "Se usa en la leyenda y en los ejes por defecto si no hay etiqueta de leyenda propia.":
        "Used in the legend and default axis labels when there is no legend "
        "label of its own.",
    "Alias en la lista": "Alias in the list",
    "Solo cambia cómo se ve en la lista de trazas; nunca aparece en el gráfico ni en la leyenda.":
        "Only changes how it looks in the trace list; it never appears on "
        "the plot or in the legend.",
    "Dominio": "Domain",
    "Magnitud": "Magnitude",
    "Unidad en la que vienen los datos del archivo.":
        "Unit the file's data is expressed in.",
    "Seleccioná una traza de la lista para ver sus ajustes.":
        "Select a trace from the list to see its settings.",
    "Sin trazas. Abrí un archivo para empezar.":
        "No traces. Open a file to get started.",
    "Sin archivos. Abrí uno para empezar.":
        "No files. Open one to get started.",

    # --- cursors and annotations -----------------------------------------
    "+ Vertical": "+ Vertical",
    "+ Horizontal": "+ Horizontal",
    "Sin cursores.": "No cursors.",
    "Sin anotaciones.": "No annotations.",
    "Sin cursores en el gráfico.": "No cursors on the plot.",
    "Clic sobre el gráfico para colocarlo; arrastralos para medir.":
        "Click the plot to place it; drag them to measure.",
    "Cursor armado: hacé clic sobre el gráfico para colocarlo.":
        "Cursor armed: click the plot to place it.",
    "Tipo": "Type",
    "Texto": "Text",
    "Preset": "Preset",
    "Aplicar preset": "Apply preset",
    # STYLE_PRESETS keys (gui/overlays.py) -- reached via t() dynamically
    # (iterated from the dict, not called as string literals), so they were
    # missing here entirely: the annotation "Preset" dropdown fell back to
    # raw Spanish for all three in English mode.
    "Referencia (línea + etiqueta rotada)": "Reference (line + rotated label)",
    "Punto de interés (marcador + flecha)": "Point of interest (marker + arrow)",
    "Cota / ancho de banda (flecha doble)": "Dimension / bandwidth (double arrow)",
    "Línea": "Line",
    "Flecha": "Arrow",
    "Recuadro": "Box",
    "Offset X": "X offset",
    "Offset Y": "Y offset",
    "Rotación": "Rotation",
    "Opacidad": "Opacity",
    "Capturar X/Y": "Capture X/Y",
    "Capturar X₂/Y₂": "Capture X\u2082/Y\u2082",
    "Agregar": "Add",
    "Actualizar": "Update",
    "Limpiar todo": "Clear all",
    "Cargar...": "Load...",
    "Hacé clic sobre el punto deseado del gráfico.":
        "Click the desired point on the plot.",
    "Admite mathtext: $f_0 = 9{,}61\\,$kHz":
        "Mathtext accepted: $f_0 = 9.61\\,$kHz",
    "Punto de interés": "Point of interest",
    "Línea vertical": "Vertical line",
    "Línea horizontal": "Horizontal line",
    "Banda vertical": "Vertical band",
    "Banda horizontal": "Horizontal band",
    "sin cruce": "no crossing",
    "(sin texto)": "(no text)",

    # --- dialogs, messages ------------------------------------------------
    "Sin selección": "Nothing selected",
    "Seleccioná una anotación de la lista.": "Select an annotation from the list.",
    "Seleccioná primero una señal de la lista.":
        "Select a signal from the list first.",
    "Sin señales": "No signals",
    "Cargá al menos una señal antes de exportar.":
        "Load at least one signal before exporting.",
    "Sin datos": "No data",
    "Exportación completa": "Export complete",
    "Error al exportar": "Export error",
    "Error al exportar figura": "Figure export error",
    "Error al leer archivo": "File read error",
    "Error al procesar columna": "Column processing error",
    "Columna vacía": "Empty column",
    "Valor inválido": "Invalid value",
    "Color inválido": "Invalid colour",
    "Error en ajustes": "Settings error",
    "Error al graficar": "Plotting error",
    "Error al guardar": "Save error",
    "Error al cargar": "Load error",
    "Archivo inválido": "Invalid file",
    "Sin archivos válidos": "No valid files",
    "Soltá archivos .csv o .txt para cargarlos.":
        "Drop .csv or .txt files to load them.",
    "Guardado": "Saved",
    "Guardar perfil": "Save profile",
    "Nombre del perfil (formato, DPI y modo CSV actuales):":
        "Profile name (current format, DPI and CSV mode):",
    "Seleccioná al menos una columna de valor distinta de la del eje X.":
        "Select at least one value column other than the X axis one.",
    "Seleccionar archivos de datos": "Select data files",
    "Ignorado": "Ignored",
    "No hay señales visibles con datos en el rango seleccionado.":
        "No visible signals have data in the selected range.",
    "Guardar figura": "Save figure",
    "Sin curva X/Y": "No X/Y curve",
    "Generá primero una curva X/Y válida en el gráfico.":
        "Generate a valid X/Y curve on the plot first.",
    "Carpeta de destino para el CSV X/Y": "Destination folder for the X/Y CSV",
    "Carpeta de destino para los CSV": "Destination folder for the CSVs",
    "Curva X/Y guardada en": "X/Y curve saved to",
    "Guardar CSV combinado": "Save combined CSV",
    "Se generaron {n} archivo(s) en": "{n} file(s) were generated in",
    "CSV combinado guardado en": "Combined CSV saved to",
    "'{color}' no es un color válido. Usá formato hex (#RRGGBB) "
    "o el selector gráfico.":
        "'{color}' is not a valid colour. Use hex format (#RRGGBB) "
        "or the colour picker.",
    "Guardar cursores y anotaciones": "Save cursors and annotations",
    "Cargar cursores y anotaciones": "Load cursors and annotations",
    "Error al redibujar": "Redraw error",
    "Guardar...": "Save...",
    "Overlays guardados en": "Overlays saved to",
    "Márgenes inconsistentes": "Inconsistent margins",
    "Seleccionar columnas": "Select columns",
    "Columna de eje X:": "X axis column:",
    "Columnas de valor (una señal por columna marcada):":
        "Value columns (one signal per ticked column):",
    "Selección inválida": "Invalid selection",

    # --- confirmations ----------------------------------------------------
    "Quitar traza": "Remove trace",
    "Quitar todas las trazas": "Remove all traces",
    "Limpiar anotaciones": "Clear annotations",
    "Eliminar perfil": "Delete profile",
    "Esta acción se puede deshacer con Ctrl+Z.":
        "This can be undone with Ctrl+Z.",

    # --- shortcuts window -------------------------------------------------
    "General": "General",
    "Gráfico": "Plot",
    "Ventana": "Window",
    "Campos numéricos": "Numeric fields",
    "Arrastrar y soltar": "Drag and drop",
    "activo": "active",
    "no disponible": "not available",
    "Abrir archivo(s)": "Open file(s)",
    "Deshacer": "Undo",
    "Rehacer": "Redo",
    "Quitar la traza seleccionada": "Remove the selected trace",
    "Aplicar el campo activo": "Apply the active field",
    "Mostrar esta ventana": "Show this window",
    "Exportar figura (y obtener el bloque LaTeX)":
        "Export figure (and get the LaTeX block)",
    "Exportar CSV para PGFPlots": "Export CSV for PGFPlots",
    "Colocar un cursor de medición": "Place a measurement cursor",
    "Capturar coordenadas para una anotación":
        "Capture coordinates for an annotation",
    "Redimensionar los paneles laterales a mano":
        "Resize the side panels by hand",
    "Arrastrar el borde": "Drag the edge",
    "Modo compacto": "Compact mode",
    "Soltá archivos .csv o .txt sobre la ventana":
        "Drop .csv or .txt files onto the window",
    "la unidad al final se ignora": "the trailing unit is ignored",
    "Cursor + clic": "Cursor + click",
    "Arrastre": "Drag",
    "Anotar + clic": "Annotate + click",

    # --- status -----------------------------------------------------------
    "puntos en gráfico": "points plotted",
    "modo": "mode",
    "Copiado al portapapeles.": "Copied to clipboard.",
    "No se pudo acceder al portapapeles.": "Couldn't access the clipboard.",
    "Fracciones de 0 a 1. Enter aplica el valor.":
        "Fractions from 0 to 1. Enter applies the value.",
    "Leyenda externa": "External legend",

    # --- clearer wording (renamed from the original, terser labels) -------
    "Reducir puntos": "Reduce points",
    "Textos y fuente": "Text and font",
    "Estilo de línea": "Line style",
    "«Sin línea» (el último botón) grafica sólo los puntos, sin interpolar entre ellos.":
        "“No line” (the last button) plots only the points, with no "
        "interpolation between them.",
    "Marcador": "Marker",
    "Tamaño de marcador": "Marker size",
    "Marcador hueco": "Hollow marker",
    "Hueco = sólo el borde, con el color de la traza; sin relleno.":
        "Hollow = outline only, in the trace's own colour; no fill.",
    "Círculo": "Circle",
    "Cruz (x)": "Cross (x)",
    "Cruz (+)": "Cross (+)",
    "Cuadrado": "Square",
    "Triángulo": "Triangle",
    "Triángulo invertido": "Inverted triangle",
    "Rombo": "Diamond",
    "Estrella": "Star",
    "Eje vertical": "Vertical axis",
    "Apariencia": "Appearance",
    "Unidades y Eje": "Units & axis",
    "Muestreo": "Sampling",
    "Desplaza la traza en el eje X. 0 = sin desplazamiento.":
        "Shifts the trace along the X axis. 0 = no shift.",
    "Desplaza la traza en el eje Y. 0 = sin desplazamiento.":
        "Shifts the trace along the Y axis. 0 = no shift.",
    "Se aplica a todas las trazas visibles, no sólo a ésta -- es el mismo "
    "recorte/diezmado de la sección «Datos» del panel Gráfico.":
        "Applies to every visible trace, not just this one -- it's the same "
        "crop/decimation as the “Data” section of the Plot pane.",
    "Guardar solo ajustes (JSON)...": "Save settings only (JSON)...",
    "Guardar solo ajustes (JSON)": "Save settings only (JSON)",
    "Guardar ajustes (sin figura)": "Save settings (no figure)",
    "Error al guardar ajustes": "Error saving settings",
    "No se pudo escribir el archivo.": "Could not write the file.",
    "Correcciones": "Corrections",
    "Ganancia": "Gain",
    "Sólo tiene efecto en señales de tipo «voltage»: escalar "
    "el valor de una traza en dB o fase no tiene sentido "
    "físico (para eso está «Desplazar en Y»).":
        "Only has an effect on “voltage”-type signals: scaling "
        "the value of a dB or phase trace has no physical meaning "
        "(that's what “Shift in Y” is for).",
    "Cursores y anotaciones": "Cursors and annotations",
    "Mover cursor": "Move cursor",
    "Seleccioná un cursor de la lista.": "Select a cursor from the list.",
    "Tipografía y ubicación": "Typography and placement",
    "Peso": "Weight",
    "Alineación H": "H alignment",
    "Alineación V": "V alignment",
    "La fuente aplica al texto plano; los tramos entre $...$ "
    "siguen el set de mathtext.":
        "The family applies to plain text; runs between $...$ follow the "
        "mathtext set.",
    "Admite mathtext: $f_0 = 9{,}61\\,$kHz":
        "Supports mathtext: $f_0 = 9{.}61\\,$kHz",
    "Máx. puntos en pantalla": "Max points on screen",
    "Líneas de texto extra": "Extra text lines",
    "Posición en la línea": "Position along the line",
    "Encuadrar": "Fit view",
    "Punto de anclaje": "Anchor point",
    "Tamaño de texto": "Text size",
    "Grosor de línea": "Line width",
    "De dónde salen los datos": "Where the data comes from",
    "Cómo aparece en la leyenda": "How it shows in the legend",
    "Posición exacta (X o Y)": "Exact position (X or Y)",
    "Mostrar el valor": "Show the value",
    "Etiquetas en el gráfico": "Labels on the plot",
    "Pegar a las muestras": "Snap to samples",
    "Grosor de traza": "Trace width",
    "Ancho de línea de la traza en el gráfico.":
        "Width of the trace's line on the plot.",
    "Arrastrá el slider o escribí el valor y Enter.":
        "Drag the slider or type the value and press Enter.",
    "Todos los márgenes deben ser números entre 0 y 1.":
        "Every margin must be a number between 0 and 1.",
    "El margen izquierdo debe ser menor que el derecho, y el inferior "
    "menor que el superior.":
        "The left margin must be less than the right one, and the bottom "
        "less than the top.",

    # --- added with undo, margins, language and the apply button ----------
    "Márgenes del gráfico": "Plot margins",
    "Aplicar ajustes": "Apply settings",
    "Ajustes aplicados.": "Settings applied.",
    "Error": "Error",
    "Mostrar/ocultar traza": "Show/hide trace",
    "Cambiar color": "Change colour",
    "Reordenar traza": "Reorder trace",
    "Ajustar grosor de traza": "Adjust trace width",
    "Deshecho": "Undone",
    "Rehecho": "Redone",
    "Nada para deshacer.": "Nothing to undo.",
    "Nada para rehacer.": "Nothing to redo.",
    "¿Quitar la traza": "Remove the trace",
    "¿Eliminar las": "Delete the",
    "trazas cargadas?": "loaded traces?",
    "El idioma se aplicará la próxima vez que abras la aplicación.":
        "The language will be applied the next time you open the application.",

    # --- histogram window --------------------------------------------------
    "Histograma": "Histogram",
    "Ver histograma...": "View histogram...",
    "Distribución de los valores (eje X o Y) de una o más señales, "
    "superpuestas.":
        "Distribution of the values (X or Y axis) of one or more signals, "
        "overlaid.",
    "Distribución de los valores de una o más señales.":
        "Distribution of the values of one or more signals.",
    "Señales": "Signals",
    "Sin trazas cargadas.": "No traces loaded.",
    "↻ Actualizar": "↻ Refresh",
    "↻ Actualizar lista de señales": "↻ Refresh signal list",
    "Eje a histogramar": "Axis to histogram",
    "«y» = valores de la señal (tensión, dB, magnitud propia...); "
    "«x» = tiempo o frecuencia.":
        "“y” = the signal's values (voltage, dB, custom quantity...); "
        "“x” = time or frequency.",
    "Regla de bins": "Binning rule",
    "«manual» habilita el campo de cantidad de bins; cualquier otra regla "
    "la calcula sola a partir de los datos (ver `core/histogram.py`).":
        "“manual” enables the bin-count field; every other rule works it out "
        "from the data itself (see `core/histogram.py`).",
    "Cantidad de bins": "Number of bins",
    "Densidad (normalizar área a 1)": "Density (normalise area to 1)",
    "Mismos bordes de bin para todas": "Same bin edges for all",
    "Con esto tildado, las señales superpuestas comparten rango y bordes "
    "de bin -- si no, cada una arma los suyos y comparar alturas entre "
    "ellas no tiene sentido.":
        "With this ticked, the overlaid signals share range and bin edges -- "
        "otherwise each builds its own and comparing bar heights between "
        "them is meaningless.",
    "Opacidad de barras": "Bar opacity",
    "Log en Y es lo habitual para ver la cola de una distribución (como en "
    "la figura de referencia). Log en X sólo tiene sentido si TODOS los "
    "bins caen en valores positivos -- con un histograma que cruza el cero "
    "(p. ej. deltaG) va a recortar la mitad negativa; en ese caso dejalo "
    "en «linear».":
        "Log on Y is the usual choice to see the tail of a distribution (as "
        "in the reference figure). Log on X only makes sense if ALL the bins "
        "fall on positive values -- with a histogram that crosses zero "
        "(e.g. deltaG) it will clip the negative half; leave it on “linear” "
        "in that case.",
    "Estadísticas": "Statistics",
    "sin datos válidos": "no valid data",
    "Cuentas": "Counts",
    "Densidad de probabilidad": "Probability density",
    "Tiempo / frecuencia": "Time / frequency",
    "Exportar histograma": "Export histogram",
    "Elegí al menos una señal a la izquierda.":
        "Select at least one signal on the left.",
    # --- manual units ------------------------------------------------------
    "Unidad X manual": "Manual X unit",
    "Unidad Y1 manual": "Manual Y1 unit",
    "Unidad Y2 manual": "Manual Y2 unit",
    "s/u": "n/u",
    "Con «manual» tildado podés escribir cualquier texto en el combo de "
    "unidad de ese eje (o dejarlo vacío para no mostrar ninguna) -- no hay "
    "conversión de prefijos para una unidad que no sea una de las conocidas.":
        "With “manual” ticked you can type any text in that axis' unit combo "
        "(or leave it empty to show none) -- there is no prefix conversion "
        "for a unit that is not one of the known ones.",
    "Unidad en la que vienen los datos del archivo. Con «Magnitud: custom» "
    "el combo de unidad acepta texto libre (o vacío, para no mostrar ninguna "
    "unidad) -- no hay conversión de prefijos para una magnitud propia.":
        "Unit the file's data is expressed in. With “Magnitude: custom” the "
        "unit combo accepts free text (or empty, to show no unit at all) -- "
        "there is no prefix conversion for a custom magnitude.",

    # --- source file reconnection ------------------------------------------
    "Reconectar archivo de origen": "Reconnect source file",
    "Archivos de datos": "Data files",
    "No se pudo reconectar": "Could not reconnect",
    "El archivo elegido no tiene las columnas esperadas para esta traza.":
        "The chosen file does not have the columns expected for this trace.",
    "Señales reconectadas": "Signals reconnected",
    "Se reconectaron automáticamente {n} señal(es) más desde la misma "
    "carpeta.":
        "{n} more signal(s) were reconnected automatically from the same "
        "folder.",
    "{missing} de {total} señal(es) no se pudieron recargar: el archivo de "
    "datos original ya no está en la misma ruta que cuando se exportó la "
    "figura. Quedaron marcadas (⚠) en la lista de trazas -- hacé clic en "
    "una para reconectarla a mano.":
        "{missing} of {total} signal(s) could not be reloaded: the original "
        "data file is no longer at the same path it was when the figure was "
        "exported. They are flagged (⚠) in the trace list -- click one to "
        "reconnect it by hand.",

    # --- rail / stage labels and tooltips (Shell) --------------------------
    # These were missing an _EN entry entirely -- the rail, its per-stage
    # tooltips and the command palette/topbar fell back to Spanish even in
    # English mode. Audited with a small AST script that walks every t(...)
    # call site in gui/ and diffs it against this dict's keys.
    "Ajuste": "Adjust",
    "Selección": "Selection",
    "Comandos": "Commands",
    "Comandos  ⌘K": "Commands  ⌘K",
    "Atajos de teclado": "Keyboard shortcuts",
    "Buscar un comando...": "Search a command...",
    "Sin resultados.": "No results.",
    "Modificado -- clic para volver al valor por defecto.":
        "Modified -- click to reset to default.",
    "Archivos cargados y qué columna usa cada traza.":
        "Loaded files and which column each trace uses.",
    "Estilo, correcciones y unidades de cada traza.":
        "Style, corrections and units for each trace.",
    "Cursores de medición y anotaciones de la figura.":
        "Measurement cursors and figure annotations.",
    "Formato, DPI y el bloque LaTeX que incluye la figura.":
        "Format, DPI and the LaTeX block that includes the figure.",
    "Exportar CSV": "Export CSV",
    "Exportar figura": "Export figure",
    "Tablero": "Board",
    "Modo compacto (ocultar paneles laterales)":
        "Compact mode (hide side panels)",
    "Con «Magnitud: custom» el combo de unidad acepta texto libre (o vacío, "
    "para no mostrar ninguna unidad) -- no hay conversión de prefijos para "
    "una magnitud propia.":
        "With “Magnitude: custom” the unit combo accepts free text (or "
        "empty, to show no unit at all) -- there is no prefix conversion "
        "for a custom magnitude.",

    # --- Histograma / Tablero stages (this session: embedded, no longer a
    # floating window) and the "Exportar" stage's simplified export console ---
    "Distribución superpuesta de una o más señales cargadas.":
        "Overlaid distribution of one or more loaded signals.",
    "Combina varias figuras ya exportadas en una grilla para el informe.":
        "Combines several already-exported figures into a report grid.",
    "Selección: ajustes de la traza elegida en la lista. Gráfico: ajustes de "
    "toda la figura (ejes, leyenda, exportación) -- no cambian según qué "
    "traza esté seleccionada.":
        "Selection: settings for the trace chosen in the list. Plot: "
        "settings for the whole figure (axes, legend, export) -- they don't "
        "change depending on which trace is selected.",
    "Muestreo (todas las trazas)": "Sampling (all traces)",
    "Más formatos": "More formats",
    "Otros formatos (PNG, SVG, PGF) y DPI.": "Other formats (PNG, SVG, PGF) and DPI.",
    "Exportar figura (PDF)...": "Export figure (PDF)...",
    "Agregá este gráfico como un panel más -- edición, filas y exportación "
    "del tablero viven en el stage «Tablero».":
        "Add this figure as one more panel -- editing rows and exporting "
        "the board live in the “Board” stage.",
    "Etiquetas": "Labels",
}


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def set_language(code: str) -> None:
    global _language
    _language = code if code in LANGUAGES else DEFAULT_LANGUAGE


def get_language() -> str:
    return _language


def language_name(code: Optional[str] = None) -> str:
    return LANGUAGES.get(code or _language, LANGUAGES[DEFAULT_LANGUAGE])


def t(text: str) -> str:
    """Translate a Spanish UI string, falling back to the original."""
    if _language == "es" or not text:
        return text
    translated = _EN.get(text)
    if translated is None:
        _missing.add(text)
        return text
    return translated


def missing() -> list[str]:
    """Strings that reached `t()` without a translation, for auditing."""
    return sorted(_missing)


def coverage() -> tuple[int, int]:
    """(translated, seen-without-translation) counts."""
    return len(_EN), len(_missing)
