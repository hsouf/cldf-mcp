"""Where downloaded data and the built database live."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_DATA_DIR = "CLDF_MCP_DATA_DIR"
ENV_CONFIG = "CLDF_MCP_CONFIG"


def default_data_dir() -> Path:
    if env := os.environ.get(ENV_DATA_DIR):
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "cldf-mcp"


def db_path(data_dir: Path) -> Path:
    return data_dir / "cldf.sqlite"


def raw_dir(data_dir: Path) -> Path:
    return data_dir / "raw"
