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


def save_figure_state(fig_path: str, state: dict) -> bool:
    """Write the sidecar for `fig_path`. Best-effort: a failure here must
    never undo an export that already succeeded."""
    payload = {"version": FIGURE_STATE_VERSION, **state}
    return _write_json(figure_state_path(fig_path), payload)


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
