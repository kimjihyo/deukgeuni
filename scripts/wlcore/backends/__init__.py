"""Backend registry. Add new backends here (see docs/storage.md)."""
from __future__ import annotations

import importlib

# name -> "module:Class" (imported lazily, so optional drivers are only needed if used)
REGISTRY = {
    "csv": "wlcore.backends.files:CsvBackend",
    "jsonl": "wlcore.backends.files:JsonlBackend",
    "sqlite": "wlcore.backends.sqlite:SqliteBackend",
}


def get(name: str):
    try:
        mod, cls = REGISTRY[name].split(":")
    except KeyError:
        raise SystemExit(f"알 수 없는 backend '{name}'. 사용 가능: {', '.join(REGISTRY)}") from None
    return getattr(importlib.import_module(mod), cls)
