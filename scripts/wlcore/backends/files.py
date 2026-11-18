"""File backends: CSV and JSON Lines. One file per table in the data directory."""
from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path

from ..model import Table
from .base import Backend


def _atomic_write(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            write(f)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


class _FileBackend(Backend):
    ext = ""

    def __init__(self, options: dict):
        super().__init__(options)
        self.dir = Path(options["path"])

    def path(self, table: Table) -> Path:
        return self.dir / f"{table.name}.{self.ext}"


class CsvBackend(_FileBackend):
    """Default. Human-readable, clean git diffs, opens in any spreadsheet."""
    name, ext = "csv", "csv"

    def load(self, table):
        p = self.path(table)
        if not p.exists():
            return []
        with open(p, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def save(self, table, rows):
        def write(f):
            w = csv.DictWriter(f, fieldnames=table.columns, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if v is None else v) for k, v in r.items()})
        _atomic_write(self.path(table), write)


class JsonlBackend(_FileBackend):
    """One JSON object per line. Typed values, still diff-friendly. mongoimport-compatible."""
    name, ext = "jsonl", "jsonl"

    def load(self, table):
        p = self.path(table)
        if not p.exists():
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def save(self, table, rows):
        def write(f):
            for r in rows:
                f.write(json.dumps({k: v for k, v in r.items() if v is not None}, ensure_ascii=False) + "\n")
        _atomic_write(self.path(table), write)
