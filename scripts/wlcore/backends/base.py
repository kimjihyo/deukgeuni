"""Storage backend interface.

Records: a backend only has to load and save whole tables. The data is small
(thousands of rows), so add/update/delete are done in memory by Store and
written back with save().

Documents: the user's Markdown coaching state (profile.md, status.md,
history.md, blocks/*.md). The default implementation keeps them as files in
the data directory; a database backend overrides load_doc/save_doc/list_docs.

To add a backend (Postgres, Google Sheets, ...):
  1. subclass Backend, implement load() and save() (and the doc methods if
     the backend has no data directory)
  2. register it in backends/__init__.py
  3. run:  python3 scripts/wl.py export > all.jsonl
           WL_BACKEND=<name> python3 scripts/wl.py import all.jsonl --yes
           WL_BACKEND=<name> python3 scripts/wl.py check
See docs/storage.md.
"""
from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from ..model import Table


class Backend(ABC):
    name = "base"

    def __init__(self, options: dict):
        """options come from <data dir>/config.toml [backend] (and env overrides),
        or from Store(config=...)."""
        self.options = options

    # ---- records ----
    @abstractmethod
    def load(self, table: Table) -> list[dict]:
        """Return all rows of the table. Values may be raw (strings etc.);
        Store runs model.coerce() on them. Missing table -> []."""

    @abstractmethod
    def save(self, table: Table, rows: list[dict]) -> None:
        """Replace the whole table with rows (already coerced and sorted).
        Must be atomic enough that a crash does not leave a half-written table."""

    # ---- documents (default: files in the data dir) ----
    def _doc_dir(self) -> Path:
        if not self.options.get("path"):
            raise NotImplementedError(f"{self.name}: 문서 저장 위치가 없음 (path 옵션 또는 load_doc 구현 필요)")
        return Path(self.options["path"])

    def load_doc(self, path: str) -> str | None:
        p = self._doc_dir() / path
        return p.read_text(encoding="utf-8") if p.exists() else None

    def save_doc(self, path: str, text: str) -> None:
        p = self._doc_dir() / path
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, p)

    def list_docs(self) -> list[str]:
        d = self._doc_dir()
        return [str(p.relative_to(d)) for p in d.rglob("*.md") if not any(part.startswith(".") for part in p.relative_to(d).parts)]

    def close(self) -> None:
        pass
