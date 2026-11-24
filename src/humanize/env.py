"""Minimal ``.env`` loader -- avoids a python-dotenv dependency.

Values already present in the real environment always win, so CI/secret managers
are not silently overridden by a stale local file.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = ".env"


def load_env(path: str | Path = ENV_PATH) -> int:
    """Load KEY=VALUE lines into os.environ. Returns how many keys were set."""
    p = Path(path)
    if not p.exists():
        return 0

    loaded = 0
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
