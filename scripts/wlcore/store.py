"""Store: the only way the CLI touches data. Backend-agnostic."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from . import backends
from .model import TABLES, Table, coerce, key_of, sort_rows, validate

ROOT = Path(__file__).resolve().parents[2]


def load_config(data_dir: Path) -> dict:
    """<data dir>/config.toml:
        [backend]
        type = "csv"        # csv | jsonl | sqlite | (custom)
        # other keys are passed to the backend, e.g. file = "workout.db" (relative to the data dir)
    Env overrides: WL_DATA (data dir), WL_BACKEND (type)."""
    cfg = {"type": "csv"}
    p = data_dir / "config.toml"
    if p.exists():
        with open(p, "rb") as f:
            cfg.update(tomllib.load(f).get("backend", {}))
    if os.environ.get("WL_BACKEND"):
        cfg["type"] = os.environ["WL_BACKEND"]
    cfg["path"] = str(data_dir)
    if cfg.get("file") and not Path(cfg["file"]).is_absolute():
        cfg["file"] = str(data_dir / cfg["file"])  # relative to the data dir
    return cfg


def resolve_data_dir() -> Path:
    """Where the user's data lives (it is never committed to this repo):
      1. env WL_DATA
      2. a one-line file `.wl-data` at the repo root containing the path (gitignored)
      3. `data/` inside this repo (gitignored; for local-only use)
    Relative paths are relative to the repo root."""
    raw = os.environ.get("WL_DATA")
    if not raw and (ROOT / ".wl-data").exists():
        raw = (ROOT / ".wl-data").read_text(encoding="utf-8").strip()
    p = Path(raw) if raw else ROOT / "data"
    return p if p.is_absolute() else (ROOT / p).resolve()


class Store:
    def __init__(self, data_dir: Path | None = None, backend: str | None = None):
        self.data_dir = Path(data_dir) if data_dir else resolve_data_dir()
        if not self.data_dir.exists():
            raise SystemExit(f"데이터 폴더가 없음: {self.data_dir}\n"
                             f"새로 시작: cp -r templates data   /   기존 데이터: WL_DATA=<경로> 또는 .wl-data 파일 (skills/onboarding/SKILL.md)")
        cfg = load_config(self.data_dir)
        if backend:
            cfg["type"] = backend
        self.backend = backends.get(cfg["type"])(cfg)
        self._cache: dict[str, list[dict]] = {}

    # ---- read ----
    def rows(self, table: str) -> list[dict]:
        if table not in self._cache:
            t = TABLES[table]
            self._cache[table] = sort_rows(t, [coerce(t, r) for r in self.backend.load(t)])
        return self._cache[table]

    def all(self) -> dict[str, list[dict]]:
        return {name: self.rows(name) for name in TABLES}

    def find(self, table: str, **where) -> list[dict]:
        t = TABLES[table]
        w = coerce(t, where)
        return [r for r in self.rows(table) if all(r[k] == w[k] for k in where)]

    # ---- write (validated, then saved through the backend) ----
    def _commit(self, table: str, rows: list[dict]) -> None:
        t = TABLES[table]
        rows = sort_rows(t, rows)
        snapshot = dict(self.all())
        snapshot[table] = rows
        errs = validate(snapshot)
        if errs:
            raise ValueError("저장 안 함 — 검사 실패:\n  " + "\n  ".join(errs))
        self.backend.save(t, rows)
        self._cache[table] = rows

    def add(self, table: str, new: list[dict]) -> None:
        t = TABLES[table]
        self._commit(table, self.rows(table) + [coerce(t, r) for r in new])

    def update(self, table: str, where: dict, changes: dict) -> int:
        t = TABLES[table]
        w = coerce(t, where)
        n, out = 0, []
        for r in self.rows(table):
            if all(r[k] == w[k] for k in where):
                r = coerce(t, {**r, **changes})
                n += 1
            out.append(r)
        if n:
            self._commit(table, out)
        return n

    def delete(self, table: str, where: dict) -> int:
        t = TABLES[table]
        w = coerce(t, where)
        keep = [r for r in self.rows(table) if not all(r[k] == w[k] for k in where)]
        n = len(self.rows(table)) - len(keep)
        if n:
            self._commit(table, keep)
        return n

    def replace_all(self, data: dict[str, list[dict]]) -> None:
        """Import: replace every table at once (validated together)."""
        clean = {name: sort_rows(TABLES[name], [coerce(TABLES[name], r) for r in data.get(name, [])]) for name in TABLES}
        errs = validate(clean)
        if errs:
            raise ValueError("가져오기 안 함 — 검사 실패:\n  " + "\n  ".join(errs))
        for name, rows in clean.items():
            self.backend.save(TABLES[name], rows)
        self._cache = clean

    def close(self):
        self.backend.close()


def key_str(table: Table, row: dict) -> str:
    return "/".join(str(v) for v in key_of(table, row))
