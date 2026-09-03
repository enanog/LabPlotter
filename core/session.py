"""
core/session.py
----------------
Persistence for two independent, small JSON documents stored under the
user's config directory (never inside the project folder, so it survives a
`git clean` and does not need a `.gitignore` entry):

* **session** -- "what was on screen last time": loaded files, per-trace
  parameters, every global plot setting, window geometry and pane widths.
  Restored automatically on startup; saved automatically on close.
* **export profiles** -- named (format, DPI, CSV mode, decimal-comma) combos
  the person can switch between from the Exportar section.

Both are plain `dict`s from the caller's point of view; this module only
owns *where* they live on disk and makes read/write failures non-fatal --
a corrupt or missing file must never stop the application from starting.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

APP_DIR_NAME = "LabPlotter"
SESSION_FILE = "session.json"
PROFILES_FILE = "export_profiles.json"
SESSION_VERSION = 1


def config_dir() -> Path:
    """
    Platform-appropriate per-user config directory, created if missing.

    Windows -> %APPDATA%\\LabPlotter
    macOS   -> ~/Library/Application Support/LabPlotter
    Linux   -> $XDG_CONFIG_HOME/LabPlotter or ~/.config/LabPlotter
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home())
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    path = Path(base) / APP_DIR_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass   # read-only environment: callers already tolerate a missing dir
    return path


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, payload: dict) -> bool:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        tmp.replace(path)   # atomic on both POSIX and Windows (same volume)
    except OSError:
        return False
    return True


# ========================================================================== #
# Portabilidad de rutas entre computadoras
# ========================================================================== #
def _iter_signal_lists(payload: dict):
    """Recorre toda lista de registros de señales dentro de un payload de
    sesión/sidecar: la plana de nivel superior (legacy + sidecars de figura)
    y la de cada tab, si existen."""
    if isinstance(payload.get("signals"), list):
        yield payload["signals"]
    for tab in payload.get("tabs", []) or []:
        state = tab.get("state") if isinstance(tab, dict) else None
        if isinstance(state, dict) and isinstance(state.get("signals"), list):
            yield state["signals"]


def _stamp_relative_paths(payload: dict, anchor_dir: Path) -> None:
    """
    Agrega `source_rel` a cada registro de señal, relativo a `anchor_dir`
    (la carpeta donde este mismo JSON está por escribirse). Best-effort:
    en Windows, `os.path.relpath` levanta ValueError si origen y ancla
    están en discos distintos -- en ese caso sobrevive solo la ruta
    absoluta (ver `core.data_io.resolve_source_path`).
    """
    for signals in _iter_signal_lists(payload):
        for record in signals:
            src = record.get("source_path")
            if not src:
                continue
            try:
                record["source_rel"] = os.path.relpath(src, anchor_dir)
            except ValueError:
                record.pop("source_rel", None)


# ========================================================================== #
# Session ("last project")
# ========================================================================== #
def session_path() -> Path:
    return config_dir() / SESSION_FILE


def load_session() -> Optional[dict]:
    """Return the saved session, or None if there isn't one / it's unreadable."""
    data = _read_json(session_path())
    if data is None or data.get("version") != SESSION_VERSION:
        return None
    return data


def save_session(state: dict) -> bool:
    payload = {"version": SESSION_VERSION, **state}
    _stamp_relative_paths(payload, config_dir())
    return _write_json(session_path(), payload)


def clear_session() -> None:
    try:
        session_path().unlink(missing_ok=True)
    except OSError:
        pass


# ========================================================================== #
# Export profiles
# ========================================================================== #
def profiles_path() -> Path:
    return config_dir() / PROFILES_FILE


def load_profiles() -> dict[str, dict[str, Any]]:
    data = _read_json(profiles_path())
    return data.get("profiles", {}) if data else {}


def save_profiles(profiles: dict[str, dict[str, Any]]) -> bool:
    return _write_json(profiles_path(), {"profiles": profiles})


def upsert_profile(name: str, values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Add or overwrite one named profile; returns the full updated set."""
    profiles = load_profiles()
    profiles[name] = values
    save_profiles(profiles)
    return profiles


def delete_profile(name: str) -> dict[str, dict[str, Any]]:
    profiles = load_profiles()
    profiles.pop(name, None)
    save_profiles(profiles)
    return profiles


# ========================================================================== #
# Figure sidecars ("import this exported figure back with its settings")
# ========================================================================== #
# Unlike the session above (one fixed file under `config_dir()`), one of
# these is written next to EVERY exported figure -- so re-opening an old
# export later means picking that file, not hunting through a single
# ever-growing "last session" blob. It carries exactly the shape
# `App._gather_plot_state()` / `App._apply_plot_state()` already use for one
# plot tab (settings + signals + manual margins): the same replay mechanism
# that already restores a signal from its original data file on disk, by
# `source_path`, is reused here rather than re-solving the same problem.
FIGURE_STATE_SUFFIX = ".labplotter.json"
FIGURE_STATE_VERSION = 1


def figure_state_path(fig_path: str) -> Path:
    """
    Sidecar path for one exported figure's settings, sitting right beside
    the figure itself (not under `config_dir()`) so it travels with the
    figure if the folder is moved or zipped up, and is easy to spot in the
    export folder by name alone.
    """
    base, _ext = os.path.splitext(fig_path)
    return Path(base + FIGURE_STATE_SUFFIX)


def save_figure_state_to(json_path: str, state: dict) -> bool:
    """
    Write the sidecar directly at `json_path` -- no figure file involved.

    Used by "save settings only" (no PNG/PDF/SVG/PGF exported alongside).
    Unlike `save_figure_state`, which derives the sidecar path FROM an image
    path via `figure_state_path`, this writes exactly the path it is given,
    so it also works for a path that already ends in `.labplotter.json`
    (running that through `figure_state_path` would double the suffix).
    """
    payload = {"version": FIGURE_STATE_VERSION, **state}
    path = Path(json_path)
    _stamp_relative_paths(payload, path.parent)
    return _write_json(path, payload)


def save_figure_state(fig_path: str, state: dict) -> bool:
    """Write the sidecar for `fig_path`. Best-effort: a failure here must
    never undo an export that already succeeded."""
    return save_figure_state_to(str(figure_state_path(fig_path)), state)


def load_figure_state(path: str) -> Optional[dict]:
    """
    Read a figure sidecar from the path the user picked directly (via a
    file dialog) -- as opposed to `figure_state_path`, which derives the
    sidecar path FROM a figure path for writing.
    """
    data = _read_json(Path(path))
    if data is None or data.get("version") != FIGURE_STATE_VERSION:
        return None
    return data
