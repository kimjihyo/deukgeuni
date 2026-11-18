"""RTS (Tuchscherer) RPE chart and e1RM."""

# RPE 10 row, reps 1..16 (past 12 extended linearly). Each 0.5 RPE below 10 = +0.5 "effective rep";
# half-rep values come from the RPE 9.5 row.
_R10 = [100, 95.5, 92.2, 89.2, 86.3, 83.7, 81.1, 78.6, 76.2, 73.9, 70.7, 68.0, 65.3, 62.6, 59.9, 57.4]
_R95 = [97.8, 93.9, 90.7, 87.8, 85.0, 82.4, 79.9, 77.4, 75.1, 72.3, 69.4, 66.7, 64.0, 61.3, 58.6]


def pct(reps: float, rpe: float) -> float:
    """% of 1RM for reps @ RPE."""
    k = round((reps + (10 - rpe)) * 2) / 2
    i = int(k)
    return _R10[i - 1] if k == i else _R95[i - 1]


def e1rm(weight: float, reps: float, rpe: float) -> float:
    return weight / (pct(reps, rpe) / 100)


def usable(s: dict) -> bool:
    """e1RM 대상: 워밍업 제외, 1–10회, RPE ≥ 6.5, 무게 > 0. 실패 세트(reps 0, RPE 없음)는 자동 제외."""
    w, r, rpe = s.get("weight_kg"), s.get("reps"), s.get("rpe")
    return (s.get("set_type") != "warmup" and None not in (w, r, rpe)
            and 1 <= r <= 10 and rpe >= 6.5 and w > 0)
