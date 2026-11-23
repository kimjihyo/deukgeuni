"""Strength estimates, calibrated prescriptions and fatigue signals — decided by code, not by the agent.

Why: prose rules ("judge by top sets", "high-rep sets overestimate") were not enforced, so an agent
mixed a 6-rep set and a post-squat volume set into the estimate and prescribed ~1 RPE too heavy.
Every rule below is explicit and covered by regression tests (tests/test_estimate.py).

Rules
  qualifying set   main lift, set_type top|work|amrap, 1–MAX_REPS reps, RPE ≥ 6.5, weight > 0,
                   and — unless it is the session's top set — not done after another lift's top set
                   in the same session (e.g. bench volume after heavy squats)
  session value    best e1RM among the session's qualifying sets
  estimate         0.6 × latest session value + 0.4 × median(previous two)   (one session → that value)
  calibration      for the last ≤4 qualifying top sets (1–MAX_REPS reps) with a target RPE: actual RPE −
                   RPE predicted by the estimate *before* that session; mean, shrunk by n/4. "Heavier than
                   predicted" counts fully, "lighter" only half (progress should not push loads up fast);
                   clamped to −0.75 … +1.5
  prescription     weight for reps @ (target RPE − bias), rounded to the increment
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

from .rts import _R10, _R95, e1rm, pct

MAIN = ("barbell_back_squat", "barbell_bench_press", "barbell_deadlift")
MAX_REPS = 5
WINDOW_DAYS = 42


@dataclass
class Estimate:
    lift: str
    value: float | None
    basis: list[tuple[str, str, float]] = field(default_factory=list)  # (date, set text, e1RM) newest first
    excluded: list[tuple[str, str, str]] = field(default_factory=list)  # (date, set text, reason)

    def text(self) -> str:
        if self.value is None:
            return f"{self.lift}: 기준이 될 탑세트 없음 (1–{MAX_REPS}회, RPE 6.5 이상)"
        lines = [f"{self.lift}: e1RM 추정 {self.value:.1f}"]
        lines += [f"  근거 {d} {s} → {v:.1f}" for d, s, v in self.basis]
        if self.excluded:
            lines += [f"  제외 {d} {s} ({why})" for d, s, why in self.excluded[:4]]
        return "\n".join(lines)


def _fmt(s: dict) -> str:
    def f(v):
        return f"{v:g}" if isinstance(v, float) else str(v)
    return f"{f(s['weight_kg'])}x{s['reps']}@{f(s['rpe'])}"


def _pct_cont(eff: float) -> float:
    """% of 1RM for a (fractional) number of effective reps at RPE 10, interpolating the chart."""
    pts = []
    for i, v in enumerate(_R10):
        pts.append((i + 1.0, v))
        if i < len(_R95):
            pts.append((i + 1.5, _R95[i]))
    pts.sort()
    if eff <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= eff <= x1:
            return y0 + (y1 - y0) * (eff - x0) / (x1 - x0)
    return pts[-1][1]


def predicted_rpe(weight: float, reps: int, e1rm_value: float) -> float:
    """RPE the chart predicts for weight × reps given an e1RM (may exceed 10 = estimate too low)."""
    target = weight / e1rm_value * 100
    lo, hi = float(reps), float(reps) + 10
    for _ in range(40):  # %1RM falls as effective reps rise: bisect
        mid = (lo + hi) / 2
        if _pct_cont(mid) > target:
            lo = mid
        else:
            hi = mid
    rir = (lo + hi) / 2 - reps
    if target > pct(reps, 10):
        rir = -(target - pct(reps, 10)) / 3  # heavier than a true max at this rep count
    return round(10 - rir, 2)


def _sessions_with_top(sets: list[dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for s in sets:
        if s["set_type"] == "top" and s["exercise_id"] in MAIN:
            out.setdefault(s["session_id"], set()).add(s["exercise_id"])
    return out


def qualifying(sets: list[dict], lift: str, as_of: str | None = None) -> tuple[dict[str, tuple[str, float]], list]:
    """{date: (set text, e1RM)} best per session, plus excluded sets with reasons."""
    tops = _sessions_with_top(sets)
    best: dict[str, tuple[str, float]] = {}
    excluded = []
    for s in sets:
        if s["exercise_id"] != lift or (as_of and s["date"] > as_of):
            continue
        w, r, rpe = s["weight_kg"], s["reps"], s["rpe"]
        if s["set_type"] not in ("top", "work", "amrap") or None in (w, r, rpe) or w <= 0 or rpe < 6.5 or r < 1:
            continue
        if r > MAX_REPS:
            excluded.append((s["date"], _fmt(s), f"{r}회: 고반복은 e1RM을 높게 추정"))
            continue
        others = tops.get(s["session_id"], set()) - {lift}
        if s["set_type"] != "top" and others:
            excluded.append((s["date"], _fmt(s), f"같은 날 {', '.join(sorted(others))} 탑세트 뒤"))
            continue
        v = e1rm(w, r, rpe)
        if s["date"] not in best or v > best[s["date"]][1]:
            best[s["date"]] = (_fmt(s), v)
    return best, excluded


def estimate(sets: list[dict], lift: str, as_of: str | None = None) -> Estimate:
    best, excluded = qualifying(sets, lift, as_of)
    if not best:
        return Estimate(lift, None, excluded=excluded)
    dates = sorted(best, reverse=True)
    cutoff = (date.fromisoformat(dates[0]) - timedelta(days=WINDOW_DAYS)).isoformat()
    dates = [d for d in dates if d >= cutoff][:3]
    vals = [best[d][1] for d in dates]
    value = vals[0] if len(vals) == 1 else 0.6 * vals[0] + 0.4 * median(vals[1:])
    excluded = sorted({e for e in excluded if e[0] >= cutoff}, reverse=True)
    return Estimate(lift, value, [(d, best[d][0], best[d][1]) for d in dates], excluded)


def calibration(sets: list[dict], lift: str, as_of: str | None = None) -> tuple[float, list[str]]:
    """Mean (actual − predicted) RPE on recent prescribed top sets. Positive = feels heavier than the chart says."""
    tops = sorted((s for s in sets if s["exercise_id"] == lift and s["set_type"] == "top" and s["target_rpe"] is not None
                   and s["rpe"] is not None and s["reps"] and 1 <= s["reps"] <= MAX_REPS and s["weight_kg"]
                   and (not as_of or s["date"] <= as_of)),
                  key=lambda s: s["date"])[-4:]
    diffs, notes = [], []
    for s in tops:
        prev_day = (date.fromisoformat(s["date"]) - timedelta(days=1)).isoformat()
        est = estimate(sets, lift, as_of=prev_day).value
        if est is None:
            continue
        d = s["rpe"] - predicted_rpe(s["weight_kg"], s["reps"], est)
        diffs.append(d)
        notes.append(f"{s['date']} {_fmt(s)}: 예상 RPE {s['rpe'] - d:.1f} → 실제 {s['rpe']:g} ({d:+.1f})")
    if not diffs:
        return 0.0, notes
    diffs = [d if d > 0 else d / 2 for d in diffs]
    bias = sum(diffs) / len(diffs) * min(len(diffs), 4) / 4
    return max(-0.75, min(1.5, bias)), notes


def prescribe(sets: list[dict], lift: str, reps: int, rpe: float, step: float = 5, as_of: str | None = None) -> str:
    est = estimate(sets, lift, as_of)
    if est.value is None:
        return est.text() + "\n→ 처방 불가: 먼저 RPE를 적은 탑세트 기록이 필요"
    bias, notes = calibration(sets, lift, as_of)
    eff_rpe = max(6.0, min(10.0, rpe - bias))
    raw = est.value * _pct_cont(reps + (10 - eff_rpe)) / 100
    w = round(raw / step) * step
    lines = [f"처방 {lift}: {w:g} kg × {reps} @{rpe:g}   (계산 {raw:.1f} kg)",
             f"  이 무게의 예상 RPE {predicted_rpe(w, reps, est.value) + bias:.1f}"
             f" (차트 {predicted_rpe(w, reps, est.value):.1f} + 개인 보정 {bias:+.1f})",
             est.text()]
    if notes:
        lines.append("  보정 근거: " + "; ".join(notes))
    return "\n".join(lines)


def fatigue(sets: list[dict], sessions: list[dict], as_of: str | None = None, days: int = 21) -> str:
    """The three data signals of docs/principles/fatigue-deload.md, on comparable sets only. Plus pain notes."""
    last = as_of or max((s["date"] for s in sets), default=date.today().isoformat())
    since = (date.fromisoformat(last) - timedelta(days=days)).isoformat()
    recent = [s for s in sets if since <= s["date"] <= last]
    found = []
    for lift in MAIN:
        # 1) same weight × reps, RPE rising across 3 sessions in a row
        by_wr: dict[tuple, list[tuple[str, float]]] = {}
        for s in recent:
            if s["exercise_id"] == lift and s["set_type"] != "warmup" and s["rpe"] is not None and s["reps"]:
                by_wr.setdefault((s["weight_kg"], s["reps"]), []).append((s["date"], s["rpe"]))
        for (w, r), xs in by_wr.items():
            per_day = sorted({d: max(v for dd, v in xs if dd == d) for d, _ in xs}.items())
            if len(per_day) >= 3 and per_day[-3][1] < per_day[-2][1] < per_day[-1][1]:
                found.append(f"{lift}: 같은 {w:g}×{r}의 RPE가 2번 연속 상승 " + " → ".join(f"{d} @{v:g}" for d, v in per_day[-3:]))
        # 2) comparable top-set e1RM falling twice in a row (>1% each)
        best, _ = qualifying(sets, lift, last)
        ds = sorted(d for d in best if d >= since)
        if len(ds) >= 3:
            a, b, c = (best[d][1] for d in ds[-3:])
            if b < a * 0.99 and c < b * 0.99:
                found.append(f"{lift}: 탑세트 e1RM 2번 연속 하락 {a:.0f} → {b:.0f} → {c:.0f}")
        # 3) backoff reps below target in the last two sessions that had backoffs
        bo: dict[str, bool] = {}
        for s in recent:
            if s["exercise_id"] == lift and s["set_type"] == "backoff" and s["reps"] is not None and (s["target_reps"] or "").isdigit():
                bo[s["date"]] = bo.get(s["date"], False) or s["reps"] < int(s["target_reps"])
        bds = sorted(bo)
        if len(bds) >= 2 and bo[bds[-1]] and bo[bds[-2]]:
            found.append(f"{lift}: 최근 두 번 백오프 횟수 미달 ({bds[-2]}, {bds[-1]})")
    pain = [f"{x['date']}: {x['notes']}" for x in sessions if x["notes"] and since <= x["date"] <= last
            and any(k in x["notes"] for k in ("통증", "아프", "불편", "뻐근", "부상"))]
    pain += [f"{s['date']} {s['exercise_id']}: {s['note']}" for s in recent if s["note"]
             and any(k in s["note"] for k in ("통증", "아프", "불편", "뻐근", "부상"))]
    out = ["피로 신호: " + ("있음" if found else "없음 — 피로 경고·볼륨 감소를 하지 않는다")]
    out += [f"  - {f}" for f in found]
    if pain:
        out.append("통증·불편 메모 (사용자에게 확인):")
        out += [f"  - {p}" for p in pain]
    return "\n".join(out)
