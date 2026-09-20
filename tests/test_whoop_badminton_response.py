import contextlib
import copy
import csv
from datetime import date, timedelta
import io
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import whoop_badminton_response as response
import whoop_analysis as readiness
from whoop_activity import classify_workouts, FIELDS
from whoop_entities import SCHEMAS
from whoop_workouts import save_workouts, ZONE_FIELDS
from test_whoop_workout_analysis import record, correction

D = date(2026, 9, 10)


def physiology(day, **changes):
    row = dict(report_date=day, cycle_id=str(day.toordinal()), sleep_id=f"synthetic-{day}",
               hrv_ms=50, resting_heart_rate_bpm=50, recovery_score=60, sleep_performance=80, day_strain=5,
               recovery_present="true", cycle_present="true", recovery_score_state="SCORED", sleep_score_state="SCORED",
               sleep_timezone_offset="+08:00", cycle_timezone_offset="+08:00",
               sleep_start=(day - timedelta(days=1)).isoformat() + "T21:00:00Z",
               sleep_end=day.isoformat() + "T06:00:00Z", cycle_end=day.isoformat() + "T20:00:00Z")
    row.update(changes)
    return row


def history(n=14, **changes):
    return [physiology(D - timedelta(days=i), **changes) for i in range(1, n + 1)]


def outcomes(**changes):
    return physiology(D + timedelta(days=1), hrv_ms=60, resting_heart_rate_bpm=53,
                      recovery_score=70, sleep_performance=75, **changes)


class BadmintonResponseTests(unittest.TestCase):
    def case(self, phys=None, rows=None):
        return response.build_cases(classify_workouts(rows or [record()]),
                                    phys if phys is not None else history() + [outcomes()])["cases"][0]

    def test_multiple_canonical_records_one_date_no_merging_or_strain_sum(self):
        raw = [record(1, "pickleball"), record(2, "squash")]
        overrides = {r["workout_id"]: correction(r) for r in raw}
        rows = classify_workouts(raw, overrides)
        cases = response.build_cases(rows, history() + [outcomes()])["cases"]
        self.assertEqual(len(cases), 1)
        exposure = cases[0]["exposure"]
        self.assertEqual(exposure["record_count"], 2)
        self.assertEqual(exposure["duration_seconds"], 7200)
        self.assertEqual(len(exposure["workout_ids"]), 2)
        self.assertEqual(len(exposure["individual_strains"]), 2)
        self.assertIsNone(exposure["real_world_session_count"])
        self.assertNotIn("arithmetic_session_strain_sum", exposure)
        self.assertEqual(response.build_cases(classify_workouts(raw), history())["cases"], [])

    def test_baseline_bounds_exclude_training_outcome_and_older_dates(self):
        data = history(14)
        data[-1]["hrv_ms"] = 64  # D-14 included.
        data += [physiology(D, hrv_ms=9999), physiology(D + timedelta(days=1), hrv_ms=9999),
                 physiology(D - timedelta(days=15), hrv_ms=9999)]
        b = response.pretraining_baselines(data, D, record()["start"])[14]["hrv_ms"]
        self.assertEqual(b["valid_count"], 14)
        self.assertEqual(b["mean"], 51)
        self.assertEqual(b["median"], 50)
        self.assertEqual(b["window_start"], str(D - timedelta(days=14)))
        self.assertEqual(b["window_end"], str(D - timedelta(days=1)))

    def test_each_minimum_boundary_and_no_zero_fill(self):
        for window, minimum in ((7, 5), (14, 10), (30, 21)):
            for n in (minimum - 1, minimum):
                with self.subTest(window=window, n=n):
                    b = response.pretraining_baselines(history(n), D, record()["start"])[window]["hrv_ms"]
                    self.assertEqual(b["valid_count"], n)
                    self.assertEqual(b["required_count"], minimum)
                    self.assertEqual(b["eligible"], n >= minimum)
                    self.assertEqual(b["mean"], 50)

    def test_metric_specific_missingness_and_independent_responses(self):
        h = history(10)
        h[0]["hrv_ms"] = None
        c = self.case(h + [outcomes()])
        self.assertEqual(c["baselines"][14]["hrv_ms"]["valid_count"], 9)
        self.assertFalse(c["responses"][14]["hrv_ms"]["eligible"])
        self.assertTrue(c["responses"][14]["recovery_score"]["eligible"])
        self.assertFalse(c["primary_response_eligible"])
        summary = response.descriptive_summaries([c])
        self.assertEqual(summary["responses"]["recovery_score"]["count"], 1)
        self.assertEqual(summary["responses"]["hrv_ms"]["count"], 0)

    def test_equal_date_weights_entity_deduplication_and_conflicts(self):
        h = history(10)
        duplicate = copy.deepcopy(h[0])
        extra = {**h[0], "cycle_id": "999", "sleep_id": "synthetic-extra", "hrv_ms": 70}
        b = response.pretraining_baselines(h + [duplicate, extra], D, record()["start"])[14]["hrv_ms"]
        self.assertEqual(b["valid_count"], 10)
        self.assertEqual(b["mean"], 51)  # One day's mean is 60, nine are 50.
        conflict = {**duplicate, "hrv_ms": 70}
        b = response.pretraining_baselines(h + [conflict], D, record()["start"])[14]["hrv_ms"]
        self.assertEqual(b["valid_count"], 9)
        self.assertEqual(len(b["conflicting_dates"]), 1)

    def test_response_units_and_formulas(self):
        r = self.case()["responses"][14]
        self.assertAlmostEqual(r["hrv_ms"]["primary_response"], 20)
        self.assertEqual(r["hrv_ms"]["absolute_difference"], 10)
        self.assertEqual(r["resting_heart_rate_bpm"]["primary_response"], 3)
        self.assertEqual(r["recovery_score"]["primary_response"], 10)
        self.assertEqual(r["sleep_performance"]["primary_response"], -5)
        self.assertEqual(r["recovery_score"]["primary_unit"], "percentage points")
        self.assertEqual(r["resting_heart_rate_bpm"]["primary_unit"], "bpm")

    def test_hrv_zero_baseline_retains_absolute_difference(self):
        c = self.case(history(hrv_ms=0) + [outcomes()])
        r = c["responses"][14]["hrv_ms"]
        self.assertIsNone(r["primary_response"])
        self.assertEqual(r["absolute_difference"], 60)
        self.assertIn("zero_HRV_baseline", r["reasons"])
        self.assertFalse(c["primary_response_eligible"])

    def test_exact_d1_only_missing_and_duplicate_outcomes(self):
        c = self.case(history() + [physiology(D + timedelta(days=2))])
        self.assertTrue(c["pair"]["missing_outcome"])
        self.assertIn("missing_exact_D+1_outcome", c["eligibility_reasons"])
        c = self.case(history() + [outcomes(), outcomes(cycle_id="999", sleep_id="synthetic-duplicate")])
        self.assertTrue(c["pair"]["ambiguous_outcome"])
        self.assertIn("ambiguous_physiological_observation", c["eligibility_reasons"])

    def test_mixed_gym_retained_but_not_clean(self):
        raw = [record(1), record(2, "powerlifting")]
        rows = classify_workouts(raw, {raw[1]["workout_id"]: correction(raw[1], "Gym / Strength", "user_confirmed_category")})
        c = response.build_cases(rows, history() + [outcomes()])["cases"][0]
        self.assertTrue(c["primary_response_eligible"])
        self.assertFalse(c["clean_case"])
        self.assertTrue(c["context"]["gym_present"])
        self.assertTrue(c["context"]["mixed_structured_training"])
        self.assertIn("mixed_activity", c["quality_flags"])

    def test_travel_baseline_and_outcome_offset_flags(self):
        c = self.case(rows=[record(timezone_offset="+01:00")])
        self.assertTrue(c["context"]["travel_or_offset_transition"])
        self.assertTrue(c["primary_response_eligible"])
        self.assertFalse(c["clean_case"])
        h = history()
        h[0]["sleep_timezone_offset"] = "+01:00"
        c = self.case(h + [outcomes()])
        self.assertTrue(c["context"]["travel_or_offset_transition"])

    def test_previous_days_unknown_coverage_not_rest(self):
        raw = [record(), record(2, "golf", str(D - timedelta(days=1))), record(3, "badminton", str(D - timedelta(days=3)))]
        cases = response.build_cases(classify_workouts(raw), history() + [outcomes()])["cases"]
        recent = cases[-1]["recent_training"]
        self.assertEqual([r["lag_days"] for r in recent], [1, 2, 3])
        self.assertEqual(recent[0]["canonical_activities"], {"Golf": 1})
        self.assertEqual(recent[1]["recorded_workout_count"], 0)
        self.assertIsNone(recent[1]["overall_duration_seconds"])
        self.assertFalse(recent[1]["confirmed_rest_day"])
        self.assertEqual(recent[2]["badminton_duration_seconds"], 3600)
        self.assertTrue(all(r["coverage_status"] == "unknown" for r in recent))

    def test_unfinished_cycle_and_invalid_or_unscored_metrics(self):
        c = self.case(history() + [outcomes(cycle_end="")])
        self.assertTrue(c["pair"]["outcomes"][0]["cycle_in_progress"])
        self.assertTrue(c["primary_response_eligible"])
        h = history(10)
        h[0]["recovery_score_state"] = "UNSCORABLE"
        self.assertEqual(self.case(h + [outcomes()])["baselines"][14]["hrv_ms"]["valid_count"], 9)
        self.assertIsNone(response.valid_metric({"hrv_ms": float("nan")}, "hrv_ms"))
        self.assertIsNone(response.valid_metric({"hrv_ms": True}, "hrv_ms"))

    def test_chronology_baseline_exclusion_and_outcome_conflict(self):
        h = history(10)
        h[0]["sleep_end"] = "2026-09-10T12:00:00Z"
        c = self.case(h + [outcomes()])
        self.assertEqual(c["baselines"][14]["hrv_ms"]["valid_count"], 9)
        self.assertIn("baseline_chronology_issue", c["quality_flags"])
        c = self.case(history() + [outcomes(sleep_start="2026-09-10T09:00:00Z")])
        self.assertFalse(c["primary_response_eligible"])
        self.assertIn("outcome_chronology_conflict", c["eligibility_reasons"])

    def test_zone_zero_and_missing_values(self):
        r = record()
        for f in ZONE_FIELDS:
            r[f] = 0
        c = self.case(rows=[r])
        self.assertEqual(c["exposure"]["zone45_milli"], 0)
        self.assertIsNone(c["exposure"]["zone45_percent"])
        for f in ZONE_FIELDS:
            r[f] = None
        c = self.case(rows=[r])
        self.assertIsNone(c["exposure"]["zone45_milli"])

    def test_reference_inventory_and_clean_view(self):
        raw = [record(), record(2, "golf", "2026-09-12"), record(3, "walking", "2026-09-13")]
        report = response.build_cases(classify_workouts(raw), history() + [outcomes()])
        self.assertEqual(report["reference_inventory"]["No recorded workouts"]["dates"], ["2026-09-11"])
        self.assertEqual(report["reference_inventory"]["Golf-only"]["dates"], ["2026-09-12"])
        self.assertIn("clean eligible: 1", response.format_report(report, clean_only=True))
        self.assertIn("Dates: 0", response.format_report(response.build_cases([], [])))

    def test_cli_read_only_inputs_readiness_unchanged_and_no_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            r = record()
            save_workouts({r["workout_id"]: r}, root)
            cp, dp = root / "classifications.csv", root / "daily_metrics.csv"
            with cp.open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=FIELDS)
                writer.writeheader(); writer.writerow(correction(r))
            with dp.open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=SCHEMAS["daily_metrics"])
                writer.writeheader()
                for row in history() + [outcomes()]:
                    writer.writerow({**{k: "" for k in SCHEMAS["daily_metrics"]}, **row})
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            rules = copy.deepcopy(readiness.SIGNAL_RULES)
            with patch.object(socket, "socket", side_effect=AssertionError("No network")), \
                    patch.object(readiness, "interpret_record", side_effect=AssertionError("No readiness changes")), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(response.main(["--workouts", str(root / "workouts.csv"), "--daily-metrics", str(dp), "--classifications", str(cp)]), 0)
            self.assertNotIn(r["workout_id"], output.getvalue())
            self.assertEqual(rules, readiness.SIGNAL_RULES)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
        rows, daily = classify_workouts([record()]), history() + [outcomes()]
        original = copy.deepcopy((rows, daily))
        response.build_cases(rows, daily)
        self.assertEqual((rows, daily), original)


if __name__ == "__main__":
    unittest.main()
