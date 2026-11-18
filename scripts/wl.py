#!/usr/bin/env python3
"""workout-log CLI. 데이터는 반드시 이 명령으로 읽고 쓴다 (저장 방식과 무관하게 동작).
표준 라이브러리만 사용. 데이터 위치는 WL_DATA > .wl-data 파일 > ./data, 저장 방식은 <데이터>/config.toml (docs/storage.md).

계산
  wl.py e1rm [--lift ID] [--all]          3대 e1RM 추이 + 현재 추정치
  wl.py pct 3 8                           RTS 표: 3회 @RPE8 = 몇 %
  wl.py load 200 3 8 [--step 5]           e1RM 200일 때 3회 @8 처방 무게

기록
  wl.py session add 2026-11-21 --title "스쿼트 헤비" [--block C --day W2D1 --status planned --notes ..]
  wl.py session set 2026-11-21 status=done notes="..."
  wl.py set add 2026-11-21 barbell_back_squat 160x4@9 --type top [--target "160x3+@8"] [--note ..]
  wl.py set add 2026-11-21 barbell_back_squat 145x3@8 --times 4 --type backoff
  wl.py set add 2026-11-01 barbell_deadlift 230x0 --type top --note "실패: 그립"   (실패 = 0회, RPE 없음)
  wl.py set add 2026-11-11 chest_supported_tbar_row x10@8 --times 2              (무게 모름)
  wl.py set edit 2026-11-21 barbell_back_squat 2 rpe=8.5
  wl.py set rm 2026-11-21 barbell_back_squat 2 --yes
  wl.py exercise add exercise_id=hack_squat name_ko="핵 스쿼트" pattern=스쿼트 increment_kg=5
  wl.py exercise set hack_squat aliases="핵스쿼트, 핵 스쿼트"

조회·이전
  wl.py show [2026-11-18]                 세션 요약 (기본: 가장 최근)
  wl.py find 스쿼트                       이름·별칭으로 exercise_id 찾기
  wl.py volume [--end 2026-11-22] [--weeks 4] [--min-rpe 7]   근육별 주간 세트
  wl.py query sets exercise_id=barbell_deadlift [--format text|csv|json]
  wl.py check                             무결성 검사
  wl.py export [out.jsonl]                전체를 공통 형식(JSON Lines)으로
  wl.py import in.jsonl --yes             현재 저장소를 파일 내용으로 교체
  wl.py backends                          사용 가능한 저장 방식
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wlcore import backends  # noqa: E402
from wlcore.model import SETS, TABLES, key_of  # noqa: E402
from wlcore.rts import e1rm, pct, usable  # noqa: E402
from wlcore.store import Store  # noqa: E402

MAIN = ["barbell_back_squat", "barbell_bench_press", "barbell_deadlift"]


def fmt(v) -> str:
    return "" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))


def kv(pairs: list[str]) -> dict:
    out = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"'{p}': field=value 형식이어야 함")
        k, v = p.split("=", 1)
        out[k] = v
    return out


SPEC = re.compile(r"^(?P<w>-?\d+(?:\.\d+)?)?x(?P<r>\d+)(?:@(?P<rpe>\d+(?:\.\d+)?))?$")
TARGET = re.compile(r"^(?P<w>-?\d+(?:\.\d+)?)?x(?P<r>[\d+\-]+)(?:@(?P<rpe>\d+(?:\.\d+)?))?$")


def parse_spec(s: str, rx=SPEC) -> tuple:
    m = rx.match(s.replace("×", "x").replace("X", "x").replace(" ", ""))
    if not m:
        raise SystemExit(f"'{s}': 무게x횟수@RPE 형식이어야 함 (예: 160x4@9, x10@8, 230x0)")
    return m["w"], m["r"], m["rpe"]


def session_of(st: Store, sid: str) -> dict:
    rows = st.find("sessions", session_id=sid)
    if not rows:
        raise SystemExit(f"세션 '{sid}' 없음. 먼저 'wl.py session add {sid} ...'")
    return rows[0]


# ---------- 계산 ----------
def cmd_e1rm(a, st):
    sets = st.rows("sets")
    total = 0.0
    for lift in [a.lift] if a.lift else MAIN:
        best = {}
        for s in sets:
            if s["exercise_id"] == lift and usable(s):
                v = e1rm(s["weight_kg"], s["reps"], s["rpe"])
                if s["date"] not in best or v > best[s["date"]][0]:
                    best[s["date"]] = (v, f'{fmt(s["weight_kg"])}x{s["reps"]}@{fmt(s["rpe"])}')
        print(f"\n== {lift} ==")
        dates = sorted(best)
        for d in dates if a.all else dates[-6:]:
            print(f"  {d}  {best[d][1]:<14} e1RM {best[d][0]:6.1f}")
        if dates:
            last = date.fromisoformat(dates[-1])
            recent = sorted(best[d][0] for d in dates if date.fromisoformat(d) >= last - timedelta(days=a.window))
            n = len(recent)
            cur = recent[n // 2] if n % 2 else (recent[n // 2 - 1] + recent[n // 2]) / 2
            total += cur
            print(f"  현재 추정(최근 {a.window}일 세션 최고값의 중앙값): {cur:.1f}  | 최고 {recent[-1]:.1f}")
    if not a.lift:
        print(f"\n합계 ≈ {total:.0f} kg")


def cmd_pct(a, st):
    print(f"{a.reps:g}회 @RPE{a.rpe:g} = {pct(a.reps, a.rpe):.1f}%")


def cmd_load(a, st):
    raw = a.e1rm * pct(a.reps, a.rpe) / 100
    print(f"{raw:.1f} kg → 처방 {round(raw / a.step) * a.step:g} kg")


# ---------- 기록 ----------
def cmd_session_add(a, st):
    sid = a.id or a.date
    if not a.id and st.find("sessions", session_id=sid):
        n = 2
        while st.find("sessions", session_id=f"{a.date}-{n}"):
            n += 1
        sid = f"{a.date}-{n}"
    st.add("sessions", [{"session_id": sid, "date": a.date, "block": a.block, "program_day": a.day,
                         "title": a.title, "status": a.status, "notes": a.notes}])
    print(f"session {sid} 추가 ({a.status})")


def cmd_session_set(a, st):
    n = st.update("sessions", {"session_id": a.session_id}, kv(a.changes))
    print(f"session {a.session_id}: {n}행 수정" if n else f"세션 '{a.session_id}' 없음")


def cmd_set_add(a, st):
    s = session_of(st, a.session_id)
    if not st.find("exercises", exercise_id=a.exercise_id):
        raise SystemExit(f"운동 '{a.exercise_id}' 없음. 'wl.py find <이름>'으로 찾거나 'wl.py exercise add ...'")
    tw = tr = trpe = None
    if a.target:
        tw, tr, trpe = parse_spec(a.target, TARGET)
    existing = [r["set_no"] for r in st.find("sets", session_id=a.session_id, exercise_id=a.exercise_id)]
    no = max(existing, default=0)
    new = []
    for spec in a.specs:
        w, r, rpe = parse_spec(spec)
        if r == "0" and rpe:
            raise SystemExit("실패 세트(0회)는 RPE를 비운다 — note에 이유를 적을 것")
        for _ in range(a.times):
            no += 1
            new.append({"date": s["date"], "session_id": a.session_id, "exercise_id": a.exercise_id,
                        "set_no": no, "set_type": a.type, "weight_kg": w, "reps": r, "rpe": rpe,
                        "target_weight_kg": tw, "target_reps": tr, "target_rpe": trpe,
                        "note": a.note if len(new) == 0 else None})
    st.add("sets", new)
    print(f"{a.exercise_id}: 세트 {new[0]['set_no']}–{no} 추가 ({len(new)}개)")


def cmd_set_edit(a, st):
    n = st.update("sets", {"session_id": a.session_id, "exercise_id": a.exercise_id, "set_no": a.set_no}, kv(a.changes))
    print(f"{n}행 수정" if n else "해당 세트 없음")


def cmd_set_rm(a, st):
    if not a.yes:
        raise SystemExit("삭제는 사용자 확인 후 --yes로만")
    n = st.delete("sets", {"session_id": a.session_id, "exercise_id": a.exercise_id, "set_no": a.set_no})
    print(f"{n}행 삭제")


def cmd_ex_add(a, st):
    st.add("exercises", [kv(a.fields)])
    print("운동 추가")


def cmd_ex_set(a, st):
    n = st.update("exercises", {"exercise_id": a.exercise_id}, kv(a.changes))
    print(f"{n}행 수정" if n else f"운동 '{a.exercise_id}' 없음")


# ---------- 조회 ----------
def cmd_show(a, st):
    sessions = st.rows("sessions")
    if not sessions:
        return print("세션 없음")
    pick = [s for s in sessions if a.date in (s["session_id"], s["date"])] if a.date else \
        [max((s for s in sessions if s["status"] == "done"), key=lambda s: s["date"], default=sessions[-1])]
    names = {e["exercise_id"]: e["name_ko"] for e in st.rows("exercises")}
    for s in pick:
        print(f"[{s['session_id']}] {s['title'] or ''} — {s['status']} {s['block'] or ''}{s['program_day'] or ''}")
        if s["notes"]:
            print(f"  ({s['notes']})")
        cur = None
        for r in st.find("sets", session_id=s["session_id"]):
            if r["exercise_id"] != cur:
                cur = r["exercise_id"]
                print(f"  {names.get(cur, cur)}")
            e = f"  e1RM {e1rm(r['weight_kg'], r['reps'], r['rpe']):.0f}" if usable(r) else ""
            rpe = f"@{fmt(r['rpe'])}" if r["rpe"] is not None else ""
            note = f"  — {r['note']}" if r["note"] else ""
            print(f"    {r['set_no']:>2}. {r['set_type']:<7} {fmt(r['weight_kg']) or '?'}x{fmt(r['reps'])}{rpe}{e}{note}")


def cmd_find(a, st):
    q = a.text.replace(" ", "").lower()
    for e in st.rows("exercises"):
        hay = [e["exercise_id"], e["name_ko"]] + (e["aliases"] or "").split(",")
        if any(q in h.replace(" ", "").lower() for h in hay):
            print(f"{e['exercise_id']:<30} {e['name_ko']}  ({e['aliases'] or ''})")


def cmd_query(a, st):
    t = TABLES[a.table]
    rows = st.find(a.table, **kv(a.where)) if a.where else st.rows(a.table)
    if a.format == "json":
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
    else:
        w = csv.writer(sys.stdout, delimiter="," if a.format == "csv" else "\t", lineterminator="\n")
        w.writerow(t.columns)
        w.writerows([[fmt(r[c]) for c in t.columns] for r in rows])


def cmd_volume(a, st):
    """근육별 주간 세트 (주동근 1.0 + 보조근 0.5). 대상: 워밍업 제외, 1회 이상 한 세트."""
    end = date.fromisoformat(a.end) if a.end else max((date.fromisoformat(s["date"]) for s in st.rows("sets")), default=date.today())
    ex = {e["exercise_id"]: e for e in st.rows("exercises")}
    for w in range(a.weeks - 1, -1, -1):
        hi = end - timedelta(days=7 * w)
        lo = hi - timedelta(days=a.days - 1)
        tot: dict[str, float] = {}
        n = 0
        for s in st.rows("sets"):
            d = date.fromisoformat(s["date"])
            if not (lo <= d <= hi) or s["set_type"] == "warmup" or not s["reps"]:
                continue
            if a.min_rpe and (s["rpe"] is None or s["rpe"] < a.min_rpe):
                continue
            n += 1
            e = ex.get(s["exercise_id"], {})
            for m in filter(None, (e.get("primary_muscles") or "").split(";")):
                tot[m] = tot.get(m, 0) + 1.0
            for m in filter(None, (e.get("secondary_muscles") or "").split(";")):
                tot[m] = tot.get(m, 0) + 0.5
        print(f"\n== {lo} ~ {hi}  (작업 세트 {n}개{f', RPE ≥ {a.min_rpe:g}' if a.min_rpe else ''}) ==")
        for m, v in sorted(tot.items(), key=lambda kv: -kv[1]):
            print(f"  {m:<8} {v:5.1f}  {'#' * round(v)}")


def cmd_check(a, st):
    from wlcore.model import validate
    errs = validate(st.all())
    print("\n".join(errs) if errs else
          f"OK ({st.backend.name}) — exercises {len(st.rows('exercises'))}, sessions {len(st.rows('sessions'))}, sets {len(st.rows('sets'))}")
    sys.exit(1 if errs else 0)


def cmd_export(a, st):
    out = open(a.file, "w", encoding="utf-8") if a.file else sys.stdout
    for name in TABLES:
        for r in st.rows(name):
            out.write(json.dumps({"table": name, **{k: v for k, v in r.items() if v is not None}}, ensure_ascii=False) + "\n")
    if a.file:
        out.close()
        print(f"{a.file}: " + ", ".join(f"{n} {len(st.rows(n))}" for n in TABLES), file=sys.stderr)


def cmd_import(a, st):
    if not a.yes:
        raise SystemExit(f"현재 저장소({st.backend.name})의 모든 데이터를 덮어씀. 확인 후 --yes")
    data = {n: [] for n in TABLES}
    with open(a.file, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                data[r.pop("table")].append(r)
    st.replace_all(data)
    print(f"{st.backend.name}로 가져옴: " + ", ".join(f"{n} {len(v)}" for n, v in data.items()))


def cmd_backends(a, st):
    for n in backends.REGISTRY:
        print(("* " if n == st.backend.name else "  ") + n)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", help="이번 실행만 다른 저장 방식 사용 (WL_BACKEND와 같음)")
    sp = p.add_subparsers(dest="cmd", required=True)

    x = sp.add_parser("e1rm"); x.add_argument("--lift"); x.add_argument("--all", action="store_true"); x.add_argument("--window", type=int, default=21); x.set_defaults(f=cmd_e1rm)
    x = sp.add_parser("pct"); x.add_argument("reps", type=float); x.add_argument("rpe", type=float); x.set_defaults(f=cmd_pct)
    x = sp.add_parser("load"); x.add_argument("e1rm", type=float); x.add_argument("reps", type=float); x.add_argument("rpe", type=float); x.add_argument("--step", type=float, default=5); x.set_defaults(f=cmd_load)

    ss = sp.add_parser("session").add_subparsers(dest="sub", required=True)
    x = ss.add_parser("add"); x.add_argument("date"); x.add_argument("--title"); x.add_argument("--block"); x.add_argument("--day"); x.add_argument("--status", default="done"); x.add_argument("--notes"); x.add_argument("--id"); x.set_defaults(f=cmd_session_add)
    x = ss.add_parser("set"); x.add_argument("session_id"); x.add_argument("changes", nargs="+"); x.set_defaults(f=cmd_session_set)

    ts = sp.add_parser("set").add_subparsers(dest="sub", required=True)
    x = ts.add_parser("add"); x.add_argument("session_id"); x.add_argument("exercise_id"); x.add_argument("specs", nargs="+")
    x.add_argument("--type", default="work", choices=SETS.field("set_type").choices); x.add_argument("--times", type=int, default=1)
    x.add_argument("--target"); x.add_argument("--note"); x.set_defaults(f=cmd_set_add)
    x = ts.add_parser("edit"); x.add_argument("session_id"); x.add_argument("exercise_id"); x.add_argument("set_no", type=int); x.add_argument("changes", nargs="+"); x.set_defaults(f=cmd_set_edit)
    x = ts.add_parser("rm"); x.add_argument("session_id"); x.add_argument("exercise_id"); x.add_argument("set_no", type=int); x.add_argument("--yes", action="store_true"); x.set_defaults(f=cmd_set_rm)

    es = sp.add_parser("exercise").add_subparsers(dest="sub", required=True)
    x = es.add_parser("add"); x.add_argument("fields", nargs="+"); x.set_defaults(f=cmd_ex_add)
    x = es.add_parser("set"); x.add_argument("exercise_id"); x.add_argument("changes", nargs="+"); x.set_defaults(f=cmd_ex_set)

    x = sp.add_parser("show"); x.add_argument("date", nargs="?"); x.set_defaults(f=cmd_show)
    x = sp.add_parser("find"); x.add_argument("text"); x.set_defaults(f=cmd_find)
    x = sp.add_parser("volume"); x.add_argument("--end"); x.add_argument("--days", type=int, default=7); x.add_argument("--weeks", type=int, default=1); x.add_argument("--min-rpe", type=float, default=0); x.set_defaults(f=cmd_volume)
    x = sp.add_parser("query"); x.add_argument("table", choices=list(TABLES)); x.add_argument("where", nargs="*"); x.add_argument("--format", default="text", choices=["text", "csv", "json"]); x.set_defaults(f=cmd_query)
    x = sp.add_parser("check"); x.set_defaults(f=cmd_check)
    x = sp.add_parser("export"); x.add_argument("file", nargs="?"); x.set_defaults(f=cmd_export)
    x = sp.add_parser("import"); x.add_argument("file"); x.add_argument("--yes", action="store_true"); x.set_defaults(f=cmd_import)
    x = sp.add_parser("backends"); x.set_defaults(f=cmd_backends)

    a = p.parse_args()
    st = Store(backend=a.backend)
    try:
        a.f(a, st)
    except ValueError as e:
        raise SystemExit(str(e))
    finally:
        st.close()


if __name__ == "__main__":
    main()
