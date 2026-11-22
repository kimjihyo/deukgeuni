"""Coaching operations on one user's Store — shared by every runtime (CLI agents, Telegram bot, MCP server).

Each function takes a Store (already scoped to one user) and returns plain text the model can read.
Writes go through Store, so they are validated before saving. Standard library only.
"""
from __future__ import annotations

import contextlib
import io
import re
import shlex
from pathlib import Path

from .model import SETS
from .store import ROOT, Store

KNOWLEDGE_ROOTS = ("AGENTS.md", "docs/", "skills/")
BLOCKED_WL = {"import", "export", "backends"}  # storage admin / server files: never for a model
SPEC = re.compile(r"^(?P<w>-?\d+(?:\.\d+)?)?x(?P<r>\d+)(?:@(?P<rpe>\d+(?:\.\d+)?))?$")


class ServiceError(ValueError):
    pass


# ---------------------------------------------------------------- generic CLI bridge
def run_wl(store: Store, argv: list[str] | str) -> str:
    """Run a wl.py command in-process against this store. Returns its output; '[실패 n] …' on error."""
    import wl  # scripts/ must be on sys.path (callers ensure it)
    if isinstance(argv, str):
        try:
            argv = shlex.split(argv)
        except ValueError as e:
            return f"인자 해석 실패: {e}"
    if not argv:
        return "args가 비었음"
    if argv[0] in BLOCKED_WL or "--backend" in argv:
        return f"'{argv[0]}'는 이 환경에서 쓸 수 없는 명령"
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wl.main(argv, store=store)
    except SystemExit as e:  # argparse errors and validation failures
        code = e.code if isinstance(e.code, int) else 1
        if e.code and not isinstance(e.code, int):
            err.write(str(e.code))
    text = (out.getvalue() + err.getvalue()).strip() or "(출력 없음)"
    return text if code == 0 else f"[실패 {code}] {text}"


def _ok(text: str) -> str:
    if text.startswith("[실패"):
        raise ServiceError(text)
    return text


# ---------------------------------------------------------------- knowledge (repo docs, read-only)
def read_knowledge(path: str = "") -> str:
    path = (path or "").strip()
    if not path:
        files = [str(p.relative_to(ROOT)) for root in KNOWLEDGE_ROOTS
                 for p in ([ROOT / root] if root.endswith(".md") else sorted((ROOT / root).rglob("*.md")))]
        return "\n".join(files)
    p = (ROOT / path).resolve()
    rel = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else ""
    if not rel or not any(rel == r or rel.startswith(r) for r in KNOWLEDGE_ROOTS) or p.suffix != ".md" or not p.exists():
        raise ServiceError(f"읽을 수 없는 경로: {path} (목록은 path를 비워서 요청)")
    return p.read_text(encoding="utf-8")


def knowledge_files() -> list[str]:
    return read_knowledge("").splitlines()


def skills() -> list[tuple[str, str]]:
    out = []
    for p in sorted((ROOT / "skills").glob("*/SKILL.md")):
        fm = p.read_text(encoding="utf-8").split("---")[1]
        name = re.search(r"^name:\s*(.+)$", fm, re.M).group(1).strip().strip('"')
        desc = re.search(r"^description:\s*(.+)$", fm, re.M).group(1).strip().strip('"')
        out.append((name, desc))
    return out


# ---------------------------------------------------------------- user documents
def get_doc(store: Store, path: str = "") -> str:
    if not (path or "").strip():
        return "\n".join(store.docs()) or "(문서 없음)"
    text = store.doc(path)
    if text is None:
        raise ServiceError(f"문서 없음: {path}")
    return text


def put_doc(store: Store, path: str, content: str) -> str:
    store.put_doc(path, content)
    return f"{path} 저장됨 ({len(content)}자)"


def current_block_path(store: Store) -> str | None:
    status = store.doc("status.md") or ""
    m = re.search(r"blocks/[A-Za-z0-9_.-]+\.md", status)
    if m and store.doc(m.group(0)) is not None:
        return m.group(0)
    blocks = [d for d in store.docs() if d.startswith("blocks/") and "template" not in d]
    return blocks[-1] if blocks else None


def coaching_state(store: Store) -> str:
    parts = []
    for path in ("profile.md", "status.md"):
        parts.append(f"===== {path} =====\n{store.doc(path) or '(없음)'}")
    b = current_block_path(store)
    parts.append(f"===== {b or '현재 블록'} =====\n{store.doc(b) if b else '(블록 없음)'}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- records: write
def ensure_session(store: Store, date: str, title: str | None = None, block: str | None = None,
                   program_day: str | None = None) -> tuple[str, str]:
    """Return (session_id, note). Uses an existing session on that date (planned → done), else creates one."""
    same_day = [s for s in store.rows("sessions") if s["date"] == date]
    if same_day:
        s = next((x for x in same_day if x["status"] == "planned"), same_day[-1])
        if s["status"] != "done":
            store.update("sessions", {"session_id": s["session_id"]}, {"status": "done"})
            return s["session_id"], f"세션 {s['session_id']} → done"
        return s["session_id"], f"세션 {s['session_id']}에 추가"
    _ok(run_wl(store, ["session", "add", date] + (["--title", title] if title else []) +
               (["--block", block] if block else []) + (["--day", program_day] if program_day else [])))
    return date, f"세션 {date} 새로 만듦"


def log_sets(store: Store, date: str, exercise_id: str, sets: list[dict], session_title: str | None = None,
             block: str | None = None, program_day: str | None = None) -> str:
    """sets: [{weight_kg, reps, rpe?, set_type?='work', times?=1, target?: '160x3+@8', note?}]
    Failed attempt: reps=0 and no rpe."""
    if not store.find("exercises", exercise_id=exercise_id):
        raise ServiceError(f"운동 '{exercise_id}' 없음 — find_exercise로 찾거나 add_exercise로 추가")
    if not sets:
        raise ServiceError("sets가 비었음")
    sid, note = ensure_session(store, date, session_title, block, program_day)
    lines = [note]
    for s in sets:
        w = "" if s.get("weight_kg") in (None, "") else f"{float(s['weight_kg']):g}"
        spec = f"{w}x{int(s['reps'])}" + (f"@{float(s['rpe']):g}" if s.get("rpe") not in (None, "") else "")
        argv = ["set", "add", sid, exercise_id, spec, "--type", s.get("set_type") or "work",
                "--times", str(int(s.get("times") or 1))]
        if s.get("target"):
            argv += ["--target", str(s["target"])]
        if s.get("note"):
            argv += ["--note", str(s["note"])]
        lines.append(_ok(run_wl(store, argv)))
    lines.append(run_wl(store, ["show", sid]))
    return "\n".join(lines)


def edit_set(store: Store, session_id: str, exercise_id: str, set_no: int, changes: dict) -> str:
    allowed = {f.name for f in SETS.fields} - {"date", "session_id", "exercise_id", "set_no"}
    bad = set(changes) - allowed
    if bad:
        raise ServiceError(f"바꿀 수 없는 필드: {sorted(bad)}")
    before = store.find("sets", session_id=session_id, exercise_id=exercise_id, set_no=set_no)
    if not before:
        raise ServiceError("해당 세트 없음")
    store.update("sets", {"session_id": session_id, "exercise_id": exercise_id, "set_no": set_no}, changes)
    after = store.find("sets", session_id=session_id, exercise_id=exercise_id, set_no=set_no)[0]
    diff = ", ".join(f"{k}: {before[0][k]} → {after[k]}" for k in changes)
    return f"수정: {session_id} {exercise_id} #{set_no} ({diff})"


def delete_set(store: Store, session_id: str, exercise_id: str, set_no: int, confirm: bool) -> str:
    if not confirm:
        raise ServiceError("삭제는 사용자 확인을 받은 뒤 confirm=true로 다시 호출")
    n = store.delete("sets", {"session_id": session_id, "exercise_id": exercise_id, "set_no": set_no})
    return f"{n}행 삭제" if n else "해당 세트 없음"


def set_session(store: Store, date_or_id: str, status: str | None = None, notes: str | None = None,
                title: str | None = None, block: str | None = None, program_day: str | None = None,
                create: bool = True) -> str:
    changes = {k: v for k, v in {"status": status, "notes": notes, "title": title, "block": block,
                                 "program_day": program_day}.items() if v is not None}
    if store.find("sessions", session_id=date_or_id):
        store.update("sessions", {"session_id": date_or_id}, changes)
        return f"세션 {date_or_id} 수정: {changes}"
    if not create:
        raise ServiceError(f"세션 {date_or_id} 없음")
    argv = ["session", "add", date_or_id, "--status", status or "planned"]
    for flag, v in (("--title", title), ("--block", block), ("--day", program_day), ("--notes", notes)):
        if v:
            argv += [flag, v]
    return _ok(run_wl(store, argv))


def add_exercise(store: Store, fields: dict) -> str:
    store.add("exercises", [fields])
    return f"운동 추가: {fields.get('exercise_id')}"


# ---------------------------------------------------------------- records: read / compute
def find_exercise(store: Store, text: str) -> str:
    return run_wl(store, ["find", text]) if text.strip() else run_wl(store, ["query", "exercises"])


def get_session(store: Store, date: str | None = None) -> str:
    return run_wl(store, ["show"] + ([date] if date else []))


def exercise_history(store: Store, exercise_id: str, limit: int = 30) -> str:
    rows = store.find("sets", exercise_id=exercise_id)[-limit:]
    if not rows:
        return f"{exercise_id}: 기록 없음"
    def f(v):
        return "" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))
    return "\n".join(f"{r['date']} #{r['set_no']} {r['set_type']} {f(r['weight_kg']) or '?'}x{f(r['reps'])}"
                     + (f"@{f(r['rpe'])}" if r['rpe'] is not None else "") + (f" — {r['note']}" if r['note'] else "")
                     for r in rows)


def e1rm(store: Store, lift: str | None = None, all_history: bool = False) -> str:
    return run_wl(store, ["e1rm"] + (["--lift", lift] if lift else []) + (["--all"] if all_history else []))


def prescribe(store: Store, e1rm_kg: float, reps: float, rpe: float, step: float = 5) -> str:
    return run_wl(store, ["load", f"{e1rm_kg:g}", f"{reps:g}", f"{rpe:g}", "--step", f"{step:g}"])


def weekly_volume(store: Store, weeks: int = 1, end: str | None = None, min_rpe: float = 0) -> str:
    return run_wl(store, ["volume", "--weeks", str(weeks)] + (["--end", end] if end else []) +
                  (["--min-rpe", f"{min_rpe:g}"] if min_rpe else []))


def check(store: Store) -> str:
    return run_wl(store, ["check"])


# ---------------------------------------------------------------- new users
def seed_from_templates(store: Store) -> bool:
    """Give an empty user the default exercise dictionary and profile/status forms."""
    if store.rows("exercises") or store.docs():
        return False
    tpl = Store(data_dir=ROOT / "templates", backend="csv")
    store.replace_all(tpl.all())
    for d in tpl.docs():
        store.put_doc(d, tpl.doc(d))
    return True


def _path(p: str | Path) -> Path:  # pragma: no cover
    return Path(p)
