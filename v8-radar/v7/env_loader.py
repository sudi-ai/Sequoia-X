from __future__ import annotations

import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path: Path, *, override: bool = False) -> int:
    """Minimal .env loader with no third-party dependency.

    Supports KEY=VALUE, optional quotes and `export KEY=VALUE`.
    Existing environment variables win unless override=True.
    """
    if not path.exists() or not path.is_file():
        return 0
    loaded = 0
    try:
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or not key.replace("_", "").isalnum():
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            if override or key not in os.environ:
                os.environ[key] = value
                loaded += 1
    except Exception:
        return loaded
    return loaded


def load_project_env() -> dict[str, int]:
    # Dedicated V7 file first, then ordinary .env. Existing OS variables still win.
    return {
        ".env.v7": load_env_file(ROOT_DIR / ".env.v7"),
        ".env": load_env_file(ROOT_DIR / ".env"),
    }
