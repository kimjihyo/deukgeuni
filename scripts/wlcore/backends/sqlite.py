"""SQLite backend: a single file, real SQL queries, no server.

Note: the .db file is binary, so git shows no line diffs. Good for local
analysis (`sqlite3 <data dir>/workout.db`), less good as the git-tracked source.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..model import INT, NUM, Table
from .base import Backend

_SQL_TYPE = {INT: "INTEGER", NUM: "REAL"}


class SqliteBackend(Backend):
    name = "sqlite"

    def __init__(self, options: dict):
        super().__init__(options)
        p = Path(options.get("file") or Path(options["path"]) / "workout.db")
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(p)
        self.conn.row_factory = sqlite3.Row

    def _ensure(self, table: Table) -> None:
        cols = ", ".join(f'"{f.name}" {_SQL_TYPE.get(f.type, "TEXT")}' for f in table.fields)
        key = ", ".join(f'"{k}"' for k in table.key)
        self.conn.execute(f'CREATE TABLE IF NOT EXISTS "{table.name}" ({cols}, PRIMARY KEY ({key}))')

    def load(self, table):
        self._ensure(table)
        return [dict(r) for r in self.conn.execute(f'SELECT * FROM "{table.name}"')]

    def save(self, table, rows):
        self._ensure(table)
        cols = table.columns
        q = f'INSERT INTO "{table.name}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) VALUES ({", ".join("?" * len(cols))})'
        with self.conn:  # one transaction: all or nothing
            self.conn.execute(f'DELETE FROM "{table.name}"')
            self.conn.executemany(q, [[r[c] for c in cols] for r in rows])

    def close(self):
        self.conn.close()
