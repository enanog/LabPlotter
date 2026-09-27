# -*- mode: python ; coding: utf-8 -*-
"""Build reproducible del ejecutable portable de LabPlotter."""

from PyInstaller.utils.hooks import collect_all


ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
dnd_datas, dnd_binaries, dnd_hidden = collect_all("tkinterdnd2")

analysis = Analysis(
    ["main.py"],
    pathex=[],
    binaries=ctk_binaries + dnd_binaries,
    datas=ctk_datas + dnd_datas,
    hiddenimports=ctk_hidden + dnd_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="LabPlotter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
