"""Storage backend interface.

A backend only has to load and save whole tables. The data is small
(thousands of rows), so add/update/delete are done in memory by Store and
written back with save(). A database backend may override the optional
methods for efficiency, but does not have to.

To add a backend (Postgres, MongoDB, Google Sheets, ...):
  1. subclass Backend, implement load() and save()
  2. register it in backends/__init__.py
  3. run:  python3 scripts/wl.py export > all.jsonl
           WL_BACKEND=<name> python3 scripts/wl.py import all.jsonl
           WL_BACKEND=<name> python3 scripts/wl.py check
See docs/storage.md.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..model import Table


class Backend(ABC):
    name = "base"

    def __init__(self, options: dict):
        """options come from <data dir>/config.toml [backend] section (and env overrides)."""
        self.options = options

    @abstractmethod
    def load(self, table: Table) -> list[dict]:
        """Return all rows of the table. Values may be raw (strings etc.);
        Store runs model.coerce() on them. Missing table -> []."""

    @abstractmethod
    def save(self, table: Table, rows: list[dict]) -> None:
        """Replace the whole table with rows (already coerced and sorted).
        Must be atomic enough that a crash does not leave a half-written table."""

    def close(self) -> None:
        pass
