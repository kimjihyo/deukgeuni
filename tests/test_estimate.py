"""Regression tests for the estimator / prescription / fatigue rules (wlcore/estimate.py).

The first case reproduces a real mistake: a 6-rep set and bench volume done after heavy squats
inflated the bench estimate, and the agent prescribed 140x3 @8 for a lifter whose 140x3 is ~@9.
Synthetic numbers with the same shape (no personal data)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from wlcore import estimate as E  # noqa: E402
from wlcore.model import SETS, coerce  # noqa: E402

B, S, D = "barbell_bench_press", "barbell_back_squat", "barbell_deadlift"


def st(date, ex, w, r, rpe, kind="top", target_rpe=None, target_reps=None, note=None, sid=None):
    return coerce(SETS, {"date": date, "session_id": sid or date, "exercise_id": ex, "set_no": 1, "set_type": kind,
                         "weight_kg": w, "reps": r, "rpe": rpe, "target_rpe": target_rpe, "target_reps": target_reps, "note": note})


BENCH_HISTORY = [
    st("2026-10-27", B, 140, 4, 9, target_rpe=8),
    st("2026-11-03", B, 145, 3, 9, target_rpe=8.5),
    st("2026-11-09", B, 150, 1, 9, target_rpe=9),
    st("2026-11-18", B, 135, 6, 9, target_rpe=8),                     # high-rep top set → inflated e1RM
    st("2026-11-21", S, 170, 4, 9, target_rpe=8),                     # heavy squat first…
    {**st("2026-11-21", B, 140, 3, 9, kind="work"), "set_no": 1},     # …then bench volume
]


class EstimateRules(unittest.TestCase):
    def test_bench_case_that_went_wrong(self):
        est = E.estimate(BENCH_HISTORY, B, as_of="2026-11-21")
        used = [s for _, s, _ in est.basis]
        self.assertNotIn("135x6@9", used)          # high reps excluded
        self.assertNotIn("140x3@9", used)          # after another lift's top set excluded
        self.assertLess(est.value, 161)
        out = E.prescribe(BENCH_HISTORY, B, 3, 8, as_of="2026-11-21")
        self.assertTrue(out.startswith("처방 barbell_bench_press: 135 kg × 3 @8"), out)
        self.assertGreaterEqual(E.predicted_rpe(140, 3, est.value), 8.5)  # 140x3 is no longer "an @8"

    def test_predicted_rpe_matches_chart(self):
        self.assertAlmostEqual(E.predicted_rpe(86.3, 5, 100), 10, places=1)
        self.assertAlmostEqual(E.predicted_rpe(86.3, 3, 100), 8, places=1)
        self.assertGreater(E.predicted_rpe(100, 3, 100), 10)   # heavier than possible → estimate too low

    def test_calibration_heavier_counts_fully_lighter_half(self):
        heavy = [st("2026-11-01", S, 150, 3, 8, target_rpe=8), st("2026-11-08", S, 150, 3, 9, target_rpe=8),
                 st("2026-11-15", S, 150, 3, 9.5, target_rpe=8)]
        bias, notes = E.calibration(heavy, S)
        self.assertGreater(bias, 0.3)
        self.assertEqual(len(notes), 2)
        light = [st("2026-11-01", S, 150, 3, 9, target_rpe=8), st("2026-11-08", S, 150, 3, 7, target_rpe=8),
                 st("2026-11-15", S, 150, 3, 6.5, target_rpe=8)]
        bias_l, _ = E.calibration(light, S)
        self.assertGreaterEqual(bias_l, -0.75)
        self.assertGreater(abs(bias), abs(bias_l))

    def test_no_basis_no_prescription(self):
        self.assertIn("처방 불가", E.prescribe([st("2026-11-01", B, 100, 10, 8)], B, 3, 8))


class FatigueRules(unittest.TestCase):
    def test_no_signal_on_normal_variation(self):
        out = E.fatigue(BENCH_HISTORY, [], as_of="2026-11-23")
        self.assertIn("피로 신호: 없음", out)

    def test_rpe_rising_at_same_load(self):
        xs = [st(d, S, 160, 3, r, kind="work") for d, r in (("2026-11-04", 7.5), ("2026-11-11", 8), ("2026-11-18", 8.5))]
        self.assertIn("RPE가 2번 연속 상승", E.fatigue(xs, []))

    def test_top_set_e1rm_falling_twice(self):
        xs = [st("2026-11-04", D, 200, 4, 8), st("2026-11-11", D, 200, 4, 9), st("2026-11-18", D, 200, 3, 9.5)]
        self.assertIn("2번 연속 하락", E.fatigue(xs, []))

    def test_backoff_collapse_and_pain_note(self):
        xs = [st("2026-11-11", S, 150, 2, 9, kind="backoff", target_reps="3"),
              st("2026-11-18", S, 150, 2, 9, kind="backoff", target_reps="3", note="무릎 통증")]
        out = E.fatigue(xs, [{"date": "2026-11-18", "notes": "허리 불편"}])
        self.assertIn("백오프 횟수 미달", out)
        self.assertIn("허리 불편", out)
        self.assertIn("무릎 통증", out)


class RealHistory(unittest.TestCase):
    """Optional: WL_TEST_DATA=<data folder> runs the same checks on real records."""

    def test_real_bench_as_of_2026_11_21(self):
        src = os.environ.get("WL_TEST_DATA")
        if not src or not Path(src).exists():
            self.skipTest("set WL_TEST_DATA to run")
        from wlcore.store import Store
        sets = Store(data_dir=src).rows("sets")
        est = E.estimate(sets, B, as_of="2026-11-21")
        self.assertGreaterEqual(E.predicted_rpe(140, 3, est.value), 8.5)
        self.assertIn("135 kg × 3 @8", E.prescribe(sets, B, 3, 8, as_of="2026-11-21"))


if __name__ == "__main__":
    unittest.main()
