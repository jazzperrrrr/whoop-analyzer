import contextlib
import copy
import csv
from datetime import date
import io
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import whoop_activity as activity
import whoop_workout_analysis as analysis
from whoop_entities import SCHEMAS
from whoop_fetch import APIError
from whoop_workouts import normalize_workout, save_workouts, ZONE_FIELDS
from test_whoop_workouts import workout


def record(n=1, label="badminton", day="2026-09-10", **changes):
    return normalize_workout(workout(n, sport_name=label, start=day + "T10:00:00Z",
                                     end=day + "T11:00:00Z", **changes))


def correction(row, canonical="Badminton", provenance="user_confirmed"):
    return dict(workout_id=row["workout_id"], recorded_activity=row["sport_name"],
                canonical_activity=canonical, provenance=provenance, classification_version="reviewed-v1")


def physiological(day=date(2026, 9, 11), **changes):
    result = dict(report_date=day, cycle_id="1", sleep_id="synthetic-sleep", hrv_ms=60,
                  resting_heart_rate_bpm=55, recovery_score=80, sleep_performance=90, day_strain=5,
                  recovery_present="true", cycle_present="true", recovery_score_state="SCORED",
                  sleep_score_state="SCORED", sleep_timezone_offset="+08:00", cycle_timezone_offset="+08:00",
                  sleep_start="2026-09-10T15:00:00Z", sleep_end="2026-09-10T23:00:00Z", cycle_end="")
    result.update(changes)
    return result


class WorkoutAnalysisTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def sidecar(self, rows):
        path = self.root / "classifications.csv"
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=activity.FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_scoped_classification_original_labels_and_reversibility(self):
        rows = [record(1, "pickleball"), record(2, "squash"), record(3, "pickleball"), record(4, "squash"), record(5, "activity")]
        original = copy.deepcopy(rows)
        overrides = activity.load_classifications(self.sidecar([correction(r) for r in rows[:2]]))
        classified = activity.classify_workouts(rows, overrides)
        self.assertEqual([r["canonical_activity"] for r in classified], ["Badminton", "Badminton", "Pickleball", "Squash", "Unknown"])
        self.assertEqual([r["recorded_activity"] for r in classified], [r["sport_name"] for r in rows])
        self.assertEqual([r["provenance"] for r in classified], ["user_confirmed", "user_confirmed", "original_label", "original_label", "unresolved"])
        self.assertEqual(activity.classify_workouts(rows)[0]["canonical_activity"], "Pickleball")
        self.assertEqual(rows, original)
        self.assertTrue(all(r["training_session_id"] is None for r in classified))
        self.assertTrue(all(r["member_workout_ids"] == [r["workout_id"]] for r in classified))

    def test_invalid_classifications_and_changed_original_label(self):
        r = record(1, "pickleball")
        c = correction(r)
        for rows in ([c, c], [{**c, "workout_id": "bad"}], [{**c, "provenance": "invalid"}]):
            with self.assertRaises(APIError):
                activity.load_classifications(self.sidecar(rows))
        r["sport_name"] = "golf"
        with self.assertRaises(APIError):
            activity.classify_workouts([r], {r["workout_id"]: c})
        with self.assertRaises(APIError):
            activity.classify_workouts([r, r])
        self.assertEqual(activity.load_classifications(self.root / "absent.csv"), {})

    def test_daily_multiple_records_no_merging_and_unknown_in_totals(self):
        rows = activity.classify_workouts([record(1), record(2), record(3, "activity")])
        result = analysis.aggregate_days(rows)
        self.assertEqual(len(result), 1)
        day = result["2026-09-10"]
        self.assertEqual(day["record_count"], 3)
        self.assertEqual(day["distinct_training_dates"], 1)
        self.assertEqual(day["canonical_activity_counts"], {"Badminton": 2, "Unknown": 1})
        self.assertEqual(len(day["workout_ids"]), 3)
        self.assertTrue(day["mixed_activity"])
        self.assertEqual(day["duration_seconds"], 10800)
        self.assertEqual(day["arithmetic_session_strain_sum"], 15)
        self.assertIsNone(day["real_world_session_count"])
        self.assertEqual(day["calendar_coverage"], "unknown")

    def test_null_and_zero_aggregation_and_zone_denominators(self):
        rows = [record(1), record(2)]
        for f in ("strain", "kilojoule", "average_heart_rate", "max_heart_rate", *ZONE_FIELDS):
            rows[0][f] = None
            rows[1][f] = 0
        report = analysis.describe(activity.classify_workouts(rows))
        self.assertEqual(report["kilojoule"], 0)
        self.assertEqual(report["kcal"], 0)
        self.assertEqual(report["strain_mean"], 0)
        self.assertEqual(report["zone_totals_milli"][ZONE_FIELDS[0]], 0)
        self.assertIsNone(report["zone_percentages"][ZONE_FIELDS[0]])
        self.assertEqual(report["observed_counts"]["strain"], 1)
        self.assertEqual(report["complete_zone_record_count"], 1)
        report = analysis.describe(activity.classify_workouts(rows[:1]))
        self.assertIsNone(report["kilojoule"])
        self.assertIsNone(report["strain_mean"])
        self.assertIsNone(analysis.describe([])["duration_seconds"])

    def test_timezone_midnight_mixed_offsets_and_overlaps(self):
        a = normalize_workout(workout(1))  # 23:50 to 00:10 at +08.
        b = normalize_workout(workout(2, timezone_offset="+01:00"))
        day = analysis.aggregate_days(activity.classify_workouts([a, b]))["2026-09-10"]
        self.assertTrue(day["mixed_offset"])
        self.assertTrue(day["midnight_crossing"])
        self.assertTrue(day["overlap"])
        self.assertEqual(day["utc_offsets"], ["+01:00", "+08:00"])
        c = normalize_workout(workout(3, start="2026-09-10T16:00:00Z", end="2026-09-10T16:30:00Z"))
        days = analysis.aggregate_days(activity.classify_workouts([a, c]))
        self.assertEqual(set(days), {"2026-09-10", "2026-09-11"})
        self.assertTrue(all(d["overlap"] for d in days.values()))

    def test_rolling_boundaries_exclude_future_and_no_synthetic_days(self):
        rows = activity.classify_workouts([record(i, day=day) for i, day in enumerate(
            ["2026-08-21", "2026-08-22", "2026-09-06", "2026-09-07", "2026-09-13", "2026-09-14", "2026-09-20", "2026-09-21"], 1)])
        report = analysis.build_report(rows, [], date(2026, 9, 20))
        self.assertEqual([report["rolling"][n]["record_count"] for n in (7, 14, 30)], [2, 4, 6])
        self.assertEqual(len(report["daily"]), 7)
        self.assertNotIn("2026-09-19", report["daily"])
        self.assertEqual(report["latest_workout_date"], "2026-09-20")
        self.assertTrue(all(s["calendar_coverage"] == "unknown" for s in report["rolling"].values()))

    def test_badminton_and_gym_summaries(self):
        raw = [record(1, "pickleball"), record(2, "squash"), record(3, day="2026-09-12"), record(4, "powerlifting")]
        overrides = {r["workout_id"]: correction(r) for r in raw[:2]}
        overrides[raw[3]["workout_id"]] = correction(raw[3], "Gym / Strength", "user_confirmed_category")
        report = analysis.build_report(activity.classify_workouts(raw, overrides), [])
        b = report["activities"]["Badminton"]
        self.assertEqual((b["record_count"], b["distinct_training_dates"]), (3, 2))
        self.assertEqual(b["date_spacing_days"], [2])
        self.assertEqual(b["duration_seconds"], 10800)
        self.assertEqual(b["duration_weighted_average_hr"], 100)
        self.assertEqual(b["max_hr"], 140)
        self.assertAlmostEqual(sum(b["zone_percentages"].values()), 100)
        self.assertEqual(b["zone_totals_milli"][ZONE_FIELDS[-1]], 15000)
        self.assertEqual(report["activities"]["Gym / Strength"]["record_count"], 1)
        self.assertIn("cardiovascular metrics do not quantify muscular workload", analysis.format_report(report))

    def test_exact_next_day_and_mixed_activity_provenance(self):
        rows = activity.classify_workouts([record(1), record(2, "golf")])
        pair = analysis.build_report(rows, [physiological()])["pairs"][0]
        self.assertEqual(pair["outcome_date"], "2026-09-11")
        self.assertEqual(len(pair["workout_ids"]), 2)
        self.assertTrue(pair["mixed_activity"])
        self.assertIsNone(pair["sport_attribution"])
        self.assertEqual(pair["outcomes"][0]["hrv_ms"], 60)
        self.assertEqual(pair["outcomes"][0]["sleep_id"], "synthetic-sleep")
        self.assertTrue(pair["outcomes"][0]["cycle_in_progress"])
        self.assertFalse(pair["outcomes"][0]["chronology_conflict"])

    def test_d_plus_two_never_substituted(self):
        rows = activity.classify_workouts([record()])
        pair = analysis.build_report(rows, [physiological(date(2026, 9, 12))])["pairs"][0]
        self.assertTrue(pair["missing_outcome"])
        self.assertEqual(pair["outcomes"], [])
        self.assertEqual(pair["outcome_date"], "2026-09-11")

    def test_pairing_unscored_missing_ambiguous_and_travel(self):
        rows = activity.classify_workouts([record()])
        p = physiological(sleep_timezone_offset="+01:00", hrv_ms=None, sleep_score_state="PENDING_SCORE",
                          recovery_present="false", sleep_start="2026-09-10T09:00:00Z")
        report = analysis.build_report(rows, [p, physiological(cycle_id="2", sleep_id="synthetic-other")])
        pair = report["pairs"][0]
        self.assertTrue(pair["ambiguous_outcome"])
        outcome = pair["outcomes"][0]
        self.assertTrue(outcome["offset_transition"])
        self.assertTrue(outcome["chronology_conflict"])
        self.assertEqual(len(outcome["missing_metrics"]), 4)
        p = physiological(sleep_timezone_offset=None, sleep_start=None)
        outcome = analysis.build_report(rows, [p])["pairs"][0]["outcomes"][0]
        self.assertTrue(outcome["offset_unknown"])
        self.assertTrue(outcome["chronology_unknown"])

    def test_cli_read_only_offline_synthetic_sources_unchanged(self):
        row = record(1, "pickleball")
        save_workouts({row["workout_id"]: row}, self.root)
        cp = self.sidecar([correction(row)])
        dp = self.root / "daily_metrics.csv"
        raw = {field: "" for field in SCHEMAS["daily_metrics"]}
        raw.update(physiological())
        with dp.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=SCHEMAS["daily_metrics"])
            writer.writeheader()
            writer.writerow(raw)
        (self.root / ".env").write_text("synthetic sentinel")
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        args = ["--workouts", str(self.root / "workouts.csv"), "--daily-metrics", str(dp), "--classifications", str(cp)]
        with patch.object(socket, "socket", side_effect=AssertionError("No network")), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(analysis.main(args), 0)
        self.assertIn("Badminton: 1 records / 1 dates", output.getvalue())
        self.assertNotIn(row["workout_id"], output.getvalue())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(analysis.main(["--workouts", str(self.root / "private-missing")]), 1)
        self.assertNotIn("private-missing", output.getvalue())

    def test_empty_report_and_input_objects_unchanged(self):
        rows = activity.classify_workouts([record()])
        daily = [physiological()]
        original = copy.deepcopy((rows, daily))
        analysis.build_report(rows, daily)
        self.assertEqual((rows, daily), original)
        text = analysis.format_report(analysis.build_report([], []))
        self.assertIn("0/0 workout dates", text)
        self.assertIn("latest workout date N/A", text)


if __name__ == "__main__":
    unittest.main()
