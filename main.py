"""
main.py
--------
Punto de entrada de la aplicación.

Ejecutar con:
    python main.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from gui.app import main
except ImportError as exc:
    print("Error al importar dependencias. Instalá los paquetes requeridos con:")
    print("    pip install -r requirements.txt")
    print(f"\nDetalle: {exc}")
    sys.exit(1)


if __name__ == "__main__":
    main()
