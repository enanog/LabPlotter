# LabPlotter

Aplicación de escritorio (CustomTkinter + Matplotlib) para cargar, previsualizar,
recortar/diezmar y exportar señales de osciloscopio o simulaciones de LTspice
(`.csv` / `.txt`) en formato listo para usar en informes de LaTeX con PGFPlots/TikZ.

## Estructura del proyecto

```
osci_ltspice_tool/
├── main.py                  # Punto de entrada
├── requirements.txt
├── core/
│   ├── data_io.py           # Carga/parseo de archivos, autodetección de formato
│   ├── processing.py        # Recorte temporal y diezmado (downsampling)
│   └── export.py            # Exportación a CSV (PGFPlots) y a figura (PDF/PNG/PGF)
└── gui/
    └── app.py                # Interfaz gráfica (CustomTkinter + Matplotlib embebido)
```

## 1. Instalación

Requiere Python ≥ 3.10.

```bash
python -m venv .venv
source .venv/bin/activate        # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> **Linux:** si `tkinter` no está instalado en el sistema (base de CustomTkinter),
> instalalo con el gestor de paquetes de tu distro, por ejemplo:
> `sudo apt install python3-tk` (Debian/Ubuntu).
>
> **Exportar con `usetex` real o formato `.pgf`:** ambas opciones delegan el
> renderizado de texto a una instalación de LaTeX (`latex`/`pdflatex` en el
> `PATH`). Si no tenés TeX instalado, dejá desmarcada la opción "Usar LaTeX
> real"; la app igual produce una estética serif tipo LaTeX usando `mathtext`
> (sin dependencias externas), suficiente para la mayoría de los informes.

## 2. Ejecución

```bash
python main.py
```

## 3. Uso

1. **Cargar archivos** — botón "+ Cargar archivo(s)" (soporta selección
   múltiple). Se autodetecta el separador de campo (`,`, `;`, tab) y si hay
   fila de encabezado. Si tus archivos usan coma como separador decimal
   (equipos en configuración europea/española), marcá la casilla
   correspondiente **antes** de cargar.
   - Si un archivo tiene más de 2 columnas numéricas (p. ej. una exportación
     multicanal de osciloscopio), se abre un diálogo para elegir la columna
     de tiempo y una o más columnas de valor; cada columna de valor marcada
     se importa como una señal independiente.
2. **Seleccionar una señal** en la lista de la izquierda para editar sus
   parámetros individuales:
   - Unidad en la que está expresado el archivo fuente (tiempo y tensión).
   - Offset temporal (alineación en x) y offset de nivel DC (en y).
   - Ganancia (factor multiplicativo) e inversión (`×-1`).
   - Estilo de línea.
   - El checkbox junto a cada señal en la lista controla su visibilidad en
     el gráfico sin necesidad de eliminarla.
3. **Ajustes globales del gráfico** (panel derecho): título, unidades de
   visualización de los ejes (s/ms/µs/ns y V/mV), recorte de ventana
   temporal (`t_min`/`t_max`, en la unidad de tiempo elegida), diezmado
   (por factor fijo o por cantidad máxima de puntos), grilla y leyenda.
   Los cambios se aplican con "Actualizar gráfico".
4. **Exportar CSV** — dos modos:
   - *Individual*: un `.csv` por señal visible, con columnas
     `t_<unidad>` / `V_<unidad>` (nombres consistentes con `\addplot table`).
   - *Combinado*: un único `.csv` con una grilla temporal común (interpolada)
     y una columna de tensión por señal — útil para tablas con varias curvas.
5. **Exportar figura** — PDF vectorial, PNG a 300 DPI, o `.pgf` (código
   nativo de PGFPlots/TikZ), con fuente serif y tamaños de fuente coherentes
   con un documento LaTeX. La opción "Usar LaTeX real" delega el renderizado
   de texto a una instalación de TeX del sistema (glifos idénticos a los del
   documento final); si falla o no está disponible, desmarcala.

## 4. Integración con LaTeX / PGFPlots

Con la exportación CSV individual (por ejemplo `data/canal1.csv`, columnas
`t_us,V_V`), en un archivo modular de tu informe (p. ej. `figuras/fig_canal1.tex`,
incluido desde el documento principal con `\input{figuras/fig_canal1}`):

```latex
% Requiere \usepackage{pgfplots} y \pgfplotsset{compat=1.18} en el preámbulo
\begin{figure}[htbp]
    \centering
    \begin{tikzpicture}
        \begin{axis}[
            width=0.85\linewidth,
            xlabel={$t\ [\mu\text{s}]$},
            ylabel={$V\ [\text{V}]$},
            grid=both,
            minor tick num=1,
            legend pos=north east,
        ]
        \addplot[thick, blue] table[x=t_us, y=V_V, col sep=comma]
            {data/V_out_ltspice.csv};
        \addlegendentry{$V_{out}$}
        \end{axis}
    \end{tikzpicture}
    \caption{Señal de salida medida en el punto de prueba.}
    \label{fig:canal1}
\end{figure}
```

Para el modo *combinado* (`t_ms,V_mV_sig1,V_mV_sig2,...`), simplemente
agregá un `\addplot` por cada columna `V_...` apuntando al mismo archivo:

```latex
\addplot table[x=t_ms, y=V_mV_V_out_ltspice, col sep=comma]{data/combined.csv};
\addlegendentry{$V_{out}$}
\addplot table[x=t_ms, y=V_mV_CH1_osci, col sep=comma]{data/combined.csv};
\addlegendentry{Canal 1 (osciloscopio)}
```

Si exportaste directamente la figura como `.pdf`, incluila con
`\includegraphics` dentro de un entorno `figure` estándar; si exportaste
`.pgf`, incluila con `\input{figuras/figura.pgf}` (requiere `\usepackage{pgf}`).

## 5. Notas y limitaciones

- Pensado para datos en el dominio del tiempo (transitorios de LTspice,
  capturas de osciloscopio). No procesa datos complejos de barridos AC
  (magnitud/fase).
- El diezmado es un submuestreo simple (toma 1 de cada N puntos), no un
  filtro anti-aliasing; para señales ruidosas con alta frecuencia de
  muestreo, preferí un factor moderado o usá el modo "Máx. puntos".
- La exportación combinada requiere que las señales seleccionadas compartan
  al menos parcialmente su rango temporal (se interpolan a una grilla común
  dentro de la intersección de rangos).
