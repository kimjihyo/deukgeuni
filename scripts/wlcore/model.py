"""Canonical data model. Every backend stores exactly these tables and fields.

Values in memory are plain Python: str, int, float, or None (missing).
Backends convert to/from their own representation; nothing else in the code
knows how data is stored. See docs/data-format.md for meanings.
"""
from __future__ import annotations

from dataclasses import dataclass

STR, INT, NUM = "str", "int", "num"


@dataclass(frozen=True)
class Field:
    name: str
    type: str  # STR / INT / NUM
    required: bool = False
    choices: tuple = ()


@dataclass(frozen=True)
class Table:
    name: str
    fields: tuple[Field, ...]
    key: tuple[str, ...]          # unique key
    order: tuple[str, ...]        # canonical sort order

    @property
    def columns(self) -> list[str]:
        return [f.name for f in self.fields]

    def field(self, name: str) -> Field:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"{self.name}: unknown field '{name}'")


SET_TYPES = ("warmup", "work", "top", "backoff", "drop", "amrap")
STATUSES = ("done", "skipped", "planned")

EXERCISES = Table(
    "exercises",
    (
        Field("exercise_id", STR, True),
        Field("name_ko", STR, True),
        Field("category", STR, choices=("대회종목", "변형", "보조")),
        Field("pattern", STR, choices=("스쿼트", "힌지", "수평밀기", "수직밀기", "수평당기기", "수직당기기", "런지", "고립")),
        Field("equipment", STR),
        Field("load_type", STR, choices=("중량", "맨몸", "맨몸+추가", "어시스트")),
        Field("primary_muscles", STR),
        Field("secondary_muscles", STR),
        Field("increment_kg", NUM),
        Field("aliases", STR),
    ),
    key=("exercise_id",),
    order=("exercise_id",),
)

SESSIONS = Table(
    "sessions",
    (
        Field("session_id", STR, True),
        Field("date", STR, True),
        Field("block", STR),
        Field("program_day", STR),
        Field("title", STR),
        Field("status", STR, True, STATUSES),
        Field("notes", STR),
    ),
    key=("session_id",),
    order=("date", "session_id"),
)

SETS = Table(
    "sets",
    (
        Field("date", STR, True),
        Field("session_id", STR, True),
        Field("exercise_id", STR, True),
        Field("set_no", INT, True),
        Field("set_type", STR, True, SET_TYPES),
        Field("weight_kg", NUM),
        Field("reps", INT),
        Field("rpe", NUM),
        Field("target_weight_kg", NUM),
        Field("target_reps", STR),
        Field("target_rpe", NUM),
        Field("note", STR),
    ),
    key=("session_id", "exercise_id", "set_no"),
    order=("session_id", "exercise_id", "set_no"),
)

TABLES = {t.name: t for t in (EXERCISES, SESSIONS, SETS)}


def coerce(table: Table, row: dict) -> dict:
    """Return a row with exactly the table's columns, values converted to canonical types.
    Raises ValueError on bad types. Unknown keys are an error (typo protection)."""
    unknown = set(row) - set(table.columns)
    if unknown:
        raise ValueError(f"{table.name}: unknown field(s) {sorted(unknown)}")
    out = {}
    for f in table.fields:
        v = row.get(f.name)
        if v is None or (isinstance(v, str) and v.strip() == ""):
            out[f.name] = None
            continue
        try:
            if f.type == INT:
                fv = float(v)
                if not fv.is_integer():
                    raise ValueError
                out[f.name] = int(fv)
            elif f.type == NUM:
                fv = float(v)
                out[f.name] = int(fv) if fv.is_integer() else fv
            else:
                out[f.name] = str(v)
        except (TypeError, ValueError):
            raise ValueError(f"{table.name}.{f.name}: '{v}' is not {f.type}") from None
    return out


def key_of(table: Table, row: dict) -> tuple:
    return tuple(row[k] for k in table.key)


def sort_rows(table: Table, rows: list[dict]) -> list[dict]:
    def k(r):
        return tuple((r[c] is None, r[c] if r[c] is not None else "") for c in table.order)
    return sorted(rows, key=k)


def validate(data: dict[str, list[dict]]) -> list[str]:
    """Backend-independent integrity check. data = {table_name: rows}."""
    errs: list[str] = []
    for t in TABLES.values():
        seen = set()
        for r in data.get(t.name, []):
            k = key_of(t, r)
            tag = f"{t.name}{list(k)}"
            if k in seen:
                errs.append(f"{tag}: 중복 키")
            seen.add(k)
            for f in t.fields:
                v = r.get(f.name)
                if f.required and v is None:
                    errs.append(f"{tag}: {f.name} 필수")
                if f.choices and v is not None and v not in f.choices:
                    errs.append(f"{tag}: {f.name}='{v}' 허용값 아님 {f.choices}")
    ex = {r["exercise_id"] for r in data.get("exercises", [])}
    sess = {r["session_id"]: r for r in data.get("sessions", [])}
    for r in data.get("sets", []):
        tag = f"sets{list(key_of(SETS, r))}"
        s = sess.get(r["session_id"])
        if s is None:
            errs.append(f"{tag}: 없는 session_id")
        elif s["date"] != r["date"]:
            errs.append(f"{tag}: date가 세션 날짜와 다름")
        if r["exercise_id"] not in ex:
            errs.append(f"{tag}: 없는 exercise_id")
        rpe = r.get("rpe")
        if rpe is not None and (not 5 <= rpe <= 10 or (rpe * 2) % 1):
            errs.append(f"{tag}: RPE {rpe} 범위(5–10)/0.5 단위 아님")
        if r.get("reps") == 0 and rpe is not None:
            errs.append(f"{tag}: 실패 세트(reps 0)는 RPE를 비워야 함")
    return errs
