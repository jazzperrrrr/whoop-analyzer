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

import whoop_analysis as analysis
from whoop_entities import SCHEMAS


DAY = date(2026, 5, 31)


def row(day=DAY, cid="1", sid="synthetic-sleep-1", value=50, **changes):
    result = {field: "" for field in SCHEMAS["daily_metrics"]}
    result.update(report_date=day.isoformat(), cycle_id=cid, sleep_id=sid,
                  recovery_present="true", cycle_present="true", recovery_score_state="SCORED",
                  sleep_score_state="SCORED", cycle_score_state="SCORED", cycle_end="2026-06-01T00:00:00Z")
    result.update({metric: value for metric in analysis.METRICS})
    result.update(changes)
    return result


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "daily_metrics.csv"

    def write(self, rows, header=None):
        with self.path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=header or SCHEMAS["daily_metrics"])
            writer.writeheader()
            writer.writerows(rows)
        return analysis.load_daily_metrics(self.path)

    def test_current_schema_missing_values_metadata_and_input_unchanged(self):
        rows = self.write([row(hrv_ms="", recovery_score=" ", day_strain=0,
                               sleep_timezone_offset="+08:00", cycle_timezone_offset="+01:00")])
        original = self.path.read_bytes()
        loaded = rows[0]
        self.assertIsNone(loaded["hrv_ms"])
        self.assertIsNone(loaded["recovery_score"])
        self.assertEqual(loaded["day_strain"], 0)
        self.assertEqual(loaded["sleep_timezone_offset"], "+08:00")
        self.assertEqual(loaded["cycle_timezone_offset"], "+01:00")
        self.assertEqual(loaded["report_date"], DAY)
        analysis.latest_report(rows)
        self.assertEqual(self.path.read_bytes(), original)

    def test_windows_use_previous_calendar_days_and_exclude_current_and_future(self):
        source = [row(DAY - timedelta(days=i), str(i + 1), f"sleep-{i}", i)
                  for i in range(1, 32)]
        source += [row(value=99), row(DAY + timedelta(days=1), "100", "future", 999)]
        loaded = self.write(source)
        result = analysis.rolling_baselines(loaded)
        for metric in analysis.METRICS:
            for window, expected in ((7, 4), (14, 7.5), (30, 15.5)):
                stats = result[DAY][metric][window]
                self.assertAlmostEqual(stats["average"], expected)
                self.assertEqual(stats["days_available"], window)
        self.assertEqual(result[min(result)]["hrv_ms"][7]["days_available"], 0)
        # All historical dates get their own rolling baselines, not just the latest.
        self.assertEqual(len(result), len(source))

    def test_sparse_calendar_window_does_not_take_last_seven_rows(self):
        rows = self.write([row(), row(DAY - timedelta(days=7), "2", "prior", 20),
                           row(DAY - timedelta(days=8), "3", "outside", 80)])
        stats = analysis.latest_report(rows)["records"][0]["metrics"]["hrv_ms"]["baselines"]
        self.assertEqual(stats[7]["average"], 20)
        self.assertEqual(stats[7]["days_available"], 1)
        self.assertEqual(stats[14]["average"], 50)

    def test_missing_values_have_independent_denominators_and_no_fill(self):
        rows = self.write([row(hrv_ms=""), row(DAY - timedelta(days=1), "2", "a", 20, hrv_ms=""),
                           row(DAY - timedelta(days=2), "3", "b", 40, recovery_score="")])
        metrics = analysis.latest_report(rows)["records"][0]["metrics"]
        self.assertIsNone(metrics["hrv_ms"]["value"])
        self.assertEqual(metrics["hrv_ms"]["baselines"][7]["average"], 40)
        self.assertEqual(metrics["hrv_ms"]["baselines"][7]["days_available"], 1)
        self.assertIsNone(metrics["hrv_ms"]["baselines"][7]["percentage_deviation"])
        self.assertEqual(metrics["recovery_score"]["baselines"][7]["average"], 20)
        self.assertEqual(metrics["day_strain"]["baselines"][7]["average"], 30)

    def test_no_history_and_all_missing_prior_values(self):
        rows = self.write([row(), row(DAY - timedelta(days=1), "2", "prior", "")])
        metrics = analysis.latest_report(rows)["records"][0]["metrics"]
        for entry in metrics.values():
            for stats in entry["baselines"].values():
                self.assertIsNone(stats["average"])
                self.assertEqual(stats["days_available"], 0)
                self.assertIsNone(stats["difference"])
        self.assertIn("no prior observations", analysis.format_report(analysis.latest_report(rows)))

    def test_latest_available_date_not_system_today_and_not_last_file_row(self):
        rows = self.write([row(DAY, "20", "latest", 70), row(DAY - timedelta(days=10), "1", "old", 10)])
        report = analysis.latest_report(rows)
        self.assertEqual(report["report_date"], DAY)
        self.assertEqual(report["records"][0]["cycle_id"], "20")
        self.assertEqual(report["records"][0]["metrics"]["hrv_ms"]["baselines"][14]["average"], 10)

    def test_multiple_cycles_same_date_daily_weighting_and_latest_preserved(self):
        rows = self.write([row(cid="1", sid="a", value=90), row(cid="2", sid="b", value=60),
                           row(DAY - timedelta(days=1), "3", "c", 20),
                           row(DAY - timedelta(days=1), "4", "d", 40),
                           row(DAY - timedelta(days=2), "5", "e", 60)])
        before = copy.deepcopy(rows)
        report = analysis.latest_report(rows)
        self.assertEqual(len(report["records"]), 2)
        for record in report["records"]:
            stats = record["metrics"]["hrv_ms"]["baselines"][7]
            self.assertEqual(stats["average"], 45)  # Mean of daily means 30 and 60.
            self.assertEqual(stats["days_available"], 2)
        self.assertEqual(rows, before)

    def test_cycle_values_not_duplicated_for_multiple_sleep_relationships(self):
        rows = self.write([row(), row(DAY - timedelta(days=1), "2", "a", 20),
                           row(DAY - timedelta(days=1), "2", "b", 20, sleep_performance=40),
                           row(DAY - timedelta(days=1), "3", "c", 80)])
        metrics = analysis.latest_report(rows)["records"][0]["metrics"]
        self.assertEqual(metrics["day_strain"]["baselines"][7]["average"], 50)
        self.assertAlmostEqual(metrics["sleep_performance"]["baselines"][7]["average"], 140 / 3)

    def test_conflicting_cycle_measurements_rejected(self):
        rows = self.write([row(), row(cid="1", sid="another-sleep", value=80)])
        with self.assertRaises(analysis.AnalysisError):
            analysis.latest_report(rows)

    def test_relative_deviation_sign_zero_and_missing(self):
        for current, baseline, delta, percentage in ((60, 50, 10, 20), (40, 50, -10, -20),
                                                     (50, 50, 0, 0), (10, 0, 10, None), (0, 0, 0, None),
                                                     (0, 50, -50, -100), (None, 50, None, None),
                                                     (50, None, None, None)):
            self.assertEqual(analysis.compare(current, baseline),
                             {"difference": delta, "percentage_deviation": percentage})

    def test_percentage_points_and_relative_percent_distinguished_in_output(self):
        rows = self.write([row(value=60, day_strain=10), row(DAY - timedelta(days=1), "2", "prior", 50, day_strain=0)])
        text = analysis.format_report(analysis.latest_report(rows), detailed=True)
        self.assertIn("difference +10.00 percentage points; relative +20.00%", text)
        self.assertIn("difference +10.00 ms; relative +20.00%", text)
        self.assertIn("relative N/A (zero baseline", text)
        self.assertIn("1/30 days", text)
        self.assertIn("[partial]", text)

    def test_provisional_strain_and_latest_missing_not_replaced_by_old_value(self):
        rows = self.write([row(day_strain="", cycle_end=""), row(DAY - timedelta(days=1), "2", "prior", 10)])
        text = analysis.format_report(analysis.latest_report(rows), detailed=True)
        self.assertIn("strain is provisional", text)
        self.assertIn("Day strain: N/A (missing)", text)
        self.assertIn("comparison N/A (latest value missing)", text)

    def test_deterministic_under_reordering(self):
        rows = self.write([row(), row(DAY - timedelta(days=1), "2", "prior", 10),
                           row(cid="3", sid="other", value=60)])
        self.assertEqual(analysis.latest_report(rows), analysis.latest_report(list(reversed(rows))))
        self.assertEqual(analysis.format_report(analysis.latest_report(rows)),
                         analysis.format_report(analysis.latest_report(rows)))

    def interpretation_report(self, days=14, historical=None, **current):
        historical = historical or {}
        source = [row(DAY - timedelta(days=i), str(i + 1), f"sleep-{i}", **historical)
                  for i in range(1, days + 1)]
        return analysis.latest_report(self.write(source + [row(**current)]))

    def test_overall_states_and_independent_signal_groups(self):
        cases = [
            ({"hrv_ms": 60, "resting_heart_rate_bpm": 45, "recovery_score": 65, "sleep_performance": 60}, "strong positive"),
            ({"hrv_ms": 40, "resting_heart_rate_bpm": 55, "recovery_score": 35, "sleep_performance": 40}, "strong negative"),
            ({"hrv_ms": 60}, "generally positive"),
            ({"resting_heart_rate_bpm": 55}, "generally negative"),
            ({"hrv_ms": 60, "resting_heart_rate_bpm": 55}, "mixed"),
            ({"hrv_ms": 60, "resting_heart_rate_bpm": 45, "sleep_performance": 40}, "mixed"),
            ({"hrv_ms": 60, "resting_heart_rate_bpm": 45, "recovery_score": 35}, "mixed"),
            ({}, "mixed"),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                report = self.interpretation_report(**changes)
                result = report["records"][0]["interpretation"]
                self.assertEqual(result["overall_state"], expected)
                text = analysis.format_report(report)
                for label in ("Overall state:", "Physiological signals:", "Sleep context:",
                              "WHOOP recovery context (separate signal):", "Current load"):
                    self.assertIn(label, text)
                if expected == "mixed":
                    self.assertIn("Conflicting signals" if changes else "no clear positive or negative direction", text)

    def test_missing_signals_stay_independent_and_limit_overall(self):
        for changes, expected in (({"hrv_ms": ""}, "insufficient data"),
                                  ({"resting_heart_rate_bpm": ""}, "insufficient data"),
                                  ({"recovery_score": "", "sleep_performance": ""}, "insufficient data"),
                                  ({"recovery_score": "", "hrv_ms": 60}, "generally positive"),
                                  ({"sleep_performance": "", "hrv_ms": 40}, "generally negative")):
            with self.subTest(changes=changes):
                result = self.interpretation_report(**changes)["records"][0]["interpretation"]
                self.assertEqual(result["overall_state"], expected)
                for metric, value in changes.items():
                    if value == "":
                        self.assertEqual(result["signals"][metric]["state"], "insufficient data")
        result = self.interpretation_report(value="")["records"][0]["interpretation"]
        self.assertTrue(all(s["state"] == "insufficient data" for s in result["signals"].values()))

    def test_insufficient_baseline_and_minimum_coverage_boundary(self):
        for days in (0, 1, 6, 7, 14):
            with self.subTest(days=days):
                result = self.interpretation_report(days=days, hrv_ms=60)["records"][0]["interpretation"]
                self.assertEqual(result["overall_state"], "insufficient data" if days < 7 else "generally positive")
        report = self.interpretation_report(historical={"hrv_ms": ""})
        result = report["records"][0]["interpretation"]
        self.assertEqual(result["signals"]["hrv_ms"]["state"], "insufficient data")
        self.assertEqual(result["signals"]["sleep_performance"]["state"], "neutral")
        result = self.interpretation_report(historical={"hrv_ms": 0})["records"][0]["interpretation"]
        self.assertIn("zero baseline", result["signals"]["hrv_ms"]["explanation"])

    def test_every_threshold_boundary_both_directions(self):
        # Independent expectations deliberately avoid deriving thresholds from the rules.
        for metric, delta, direction in (("hrv_ms", 5, 1), ("resting_heart_rate_bpm", 3, -1),
                                         ("recovery_score", 10, 1), ("sleep_performance", 5, 1)):
            for sign in (-1, 1):
                for offset in (-0.0001, 0, 0.0001):
                    value = 50 + sign * (delta + offset)
                    expected = "neutral" if offset < 0 else "positive" if sign * direction > 0 else "negative"
                    with self.subTest(metric=metric, sign=sign, offset=offset):
                        report = self.interpretation_report(**{metric: value})
                        self.assertEqual(report["records"][0]["interpretation"]["signals"][metric]["state"], expected)

    def test_strain_never_changes_interpretation_and_provisional_is_visible(self):
        baseline = None
        for strain in ("", 0, 10, 21):
            for cycle_end in ("", "2026-06-01T00:00:00Z"):
                report = self.interpretation_report(day_strain=strain, cycle_end=cycle_end, hrv_ms=60)
                result = report["records"][0]["interpretation"]
                if baseline is None:
                    baseline = result
                self.assertEqual(result, baseline)
                text = analysis.format_report(report)
                self.assertEqual("strain is provisional" in text, cycle_end == "")
                self.assertIn("excluded from overall state and morning readiness", text)

    def test_reject_invalid_numbers_dates_ids_and_duplicate_relationships_safely(self):
        for changes in ({"hrv_ms": "NaN"}, {"day_strain": "inf"}, {"recovery_score": "-1"},
                        {"sleep_performance": "private-value"}, {"report_date": "2026-02-30"},
                        {"report_date": "20260531"}, {"cycle_id": "01"}, {"cycle_id": ""},
                        {"sleep_id": ""}, {"sleep_id": "bad\nID"}):
            with self.subTest(changes=changes), self.assertRaises(analysis.AnalysisError) as caught:
                self.write([row(**changes)])
            self.assertNotIn("private-value", str(caught.exception))
        with self.assertRaises(analysis.AnalysisError):
            self.write([row(), row()])
        with self.assertRaises(analysis.AnalysisError):
            self.write([row(), row(DAY - timedelta(days=1))])

    def test_malformed_csv_headers_rows_encoding_and_missing_file(self):
        for content in ("", "wrong,columns\n", "report_date,report_date\n", "report_date,cycle_id,sleep_id,hrv_ms,resting_heart_rate_bpm,recovery_score,sleep_performance,day_strain\n2026-05-31,1,a,1,2,3,4\n",
                        "report_date,cycle_id,sleep_id,hrv_ms,resting_heart_rate_bpm,recovery_score,sleep_performance,day_strain\n2026-05-31,1,a,1,2,3,4,5,EXTRA\n"):
            self.path.write_text(content, encoding="utf-8")
            with self.assertRaises(analysis.AnalysisError):
                analysis.load_daily_metrics(self.path)
        self.path.write_bytes(b"\xff\xfe\xfd")
        with self.assertRaises(analysis.AnalysisError):
            analysis.load_daily_metrics(self.path)
        with self.assertRaises(analysis.AnalysisError):
            analysis.load_daily_metrics(self.root / "absent.csv")

    def test_empty_header_only_dataset_bom_and_minimum_schema(self):
        rows = self.write([])
        self.assertIsNone(analysis.latest_report(rows))
        self.assertIn("No daily metrics", analysis.format_report(None))
        minimal = {key: value for key, value in row().items() if key in analysis.REQUIRED}
        self.write([minimal], header=list(minimal))
        self.path.write_bytes(b"\xef\xbb\xbf" + self.path.read_bytes())
        self.assertEqual(len(analysis.load_daily_metrics(self.path)), 1)

    def test_cli_read_only_offline_and_default_independent_of_cwd(self):
        self.write([row()])
        (self.root / ".env").write_text("synthetic credential sentinel")
        (self.root / "whoop_history.csv").write_text("synthetic legacy sentinel")
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch.object(socket, "socket", side_effect=AssertionError("Network forbidden")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(analysis.main(["--input", str(self.path)]), 0)
        self.assertIn("WHOOP daily analysis - 2026-05-31", output.getvalue())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.assertEqual(analysis.DEFAULT_INPUT, Path(analysis.__file__).resolve().parent / "data" / "daily_metrics.csv")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(analysis.main(["--input", str(self.root / "private-missing-path")]), 1)
        self.assertNotIn("private-missing-path", output.getvalue())


if __name__ == "__main__":
    unittest.main()
