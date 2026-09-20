import contextlib
import copy
import csv
from datetime import date, datetime, timezone
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

import whoop_workouts as workouts
from whoop_fetch import APIError


def workout(n=1, **changes):
    record = dict(id=str(UUID(int=n)), sport_name="activity", sport_id=-1,
                  start="2026-09-10T15:50:00Z", end="2026-09-10T16:10:00Z",
                  timezone_offset="+08:00", created_at="2026-09-10T16:11:00Z",
                  updated_at="2026-09-10T16:12:00Z", score_state="SCORED",
                  score=dict(strain=5, average_heart_rate=100, max_heart_rate=140,
                             kilojoule=418.4, percent_recorded=0.99, distance_meter=None,
                             altitude_gain_meter=None, altitude_change_meter=-3,
                             zone_durations={f: i * 1000 for i, f in enumerate(workouts.ZONE_FIELDS)}))
    record.update(changes)
    return record


class WorkoutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "workouts.csv"

    def collect(self, records):
        client = Mock()
        client.get.return_value = {"records": records}
        return workouts.collect_workouts(client, "2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z")

    def test_timezone_offsets_utc_conversion_dates_and_midnight(self):
        for offset, local_start, local_end, day in (
                ("+08:00", "2026-09-10T23:50:00+08:00", "2026-09-11T00:10:00+08:00", "2026-09-10"),
                ("+01:00", "2026-09-10T16:50:00+01:00", "2026-09-10T17:10:00+01:00", "2026-09-10")):
            with self.subTest(offset=offset):
                row = workouts.normalize_workout(workout(timezone_offset=offset))
                self.assertEqual((row["local_start"], row["local_end"], row["report_date"]),
                                 (local_start, local_end, day))
                self.assertEqual(row["duration_seconds"], 1200)
        row = workouts.normalize_workout(workout(start="2026-09-10T17:00:00Z", end="2026-09-10T17:20:00Z"))
        self.assertEqual(row["report_date"], "2026-09-11")
        row = workouts.normalize_workout(workout(start="2026-09-10T23:50:00+08:00"))
        self.assertEqual(row["start"], "2026-09-10T15:50:00Z")

    def test_original_fields_kcal_all_zones_and_no_classification(self):
        source = workout()
        before = copy.deepcopy(source)
        row = workouts.normalize_workout(source)
        self.assertEqual(source, before)
        self.assertEqual(set(row), set(workouts.SCHEMA))
        self.assertEqual(row["sport_name"], "activity")
        self.assertEqual(row["sport_id"], -1)
        self.assertEqual(row["kilojoule"], 418.4)
        self.assertAlmostEqual(row["kcal"], 100)
        self.assertEqual(row["percent_recorded"], 0.99)
        self.assertEqual(row["altitude_change_meter"], -3)
        self.assertEqual([row[f] for f in workouts.ZONE_FIELDS], [0, 1000, 2000, 3000, 4000, 5000])
        for name in ("badminton", "powerlifting", "weightlifting", "strength trainer"):
            self.assertEqual(workouts.normalize_workout(workout(sport_name=name))["sport_name"], name)

    def test_missing_null_zero_and_unscored(self):
        for score in (None, {}, {"kilojoule": None, "zone_durations": None}):
            row = workouts.normalize_workout(workout(score=score, sport_id=None))
            self.assertTrue(all(row[f] is None for f in (*workouts.SCORE_FIELDS, *workouts.ZONE_FIELDS, "kcal", "sport_id")))
        zero_score = {f: 0 for f in workouts.SCORE_FIELDS}
        zero_score["zone_durations"] = {f: 0 for f in workouts.ZONE_FIELDS}
        row = workouts.normalize_workout(workout(score=zero_score))
        self.assertTrue(all(row[f] == 0 for f in (*workouts.SCORE_FIELDS, *workouts.ZONE_FIELDS, "kcal")))
        for state in ("PENDING_SCORE", "UNSCORABLE"):
            row = workouts.normalize_workout(workout(score_state=state))
            self.assertEqual(row["score_state"], state)
            self.assertTrue(all(row[f] is None for f in (*workouts.SCORE_FIELDS, *workouts.ZONE_FIELDS, "kcal")))

    def test_pagination_duplicates_latest_update_and_window_bounds(self):
        newer = workout(sport_name="badminton")
        older = workout(updated_at="2026-09-10T16:11:00Z")
        client = Mock()
        client.get.side_effect = [
            {"records": [newer, workout(2)], "next_token": "synthetic-cursor"},
            {"records": [older, workout(3, start="2026-09-09T10:00:00Z")], "next_token": None}]
        rows = workouts.collect_workouts(client, "2026-09-10T15:50:00Z", "2026-09-10T16:00:00Z")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[newer["id"]]["sport_name"], "badminton")
        self.assertEqual(client.get.call_args_list[1].kwargs["params"]["nextToken"], "synthetic-cursor")
        self.assertEqual(client.get.call_args_list[0].args, ("/activity/workout",))
        client.get.side_effect = None
        client.get.return_value = {"records": [workout()]}
        self.assertFalse(workouts.collect_workouts(client, "2026-09-10T15:00:00Z", "2026-09-10T15:50:00Z"))

    def test_invalid_pagination_and_partial_network_failure(self):
        for payload in ({"records": None}, {"records": [], "next_token": 3},
                        {"records": [], "next_token": "repeat"}):
            client = Mock()
            client.get.return_value = payload
            with self.assertRaises(APIError):
                workouts.collect_workouts(client, "2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z")
        client.get.side_effect = [{"records": [workout()], "next_token": "next"}, APIError("Safe failure")]
        with patch.object(workouts, "WhoopClient", return_value=client), patch.object(workouts, "save_workouts") as save:
            client.tokens = {"scope": "read:workout"}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(workouts.main([]), 1)
            save.assert_not_called()

    def test_invalid_records_fail_without_leaking_values(self):
        for changes in ({"id": "private-invalid"}, {"start": "private-invalid"}, {"timezone_offset": "private-invalid"},
                        {"end": "2026-09-01T00:00:00Z"}, {"score_state": "private-invalid"},
                        {"score": {"strain": float("nan")}}, {"score": {"strain": True}},
                        {"score": {"kilojoule": -1}}, {"score": {"zone_durations": {"zone_zero_milli": 1.5}}}):
            with self.subTest(changes=changes), self.assertRaises(APIError) as caught:
                workouts.normalize_workout(workout(**changes))
            self.assertNotIn("private-invalid", str(caught.exception))

    def test_idempotent_archive_updates_and_other_files_unchanged(self):
        for name in ("cycles.csv", "whoop_data.csv", ".env", "whoop_tokens.json"):
            (self.root / name).write_text("synthetic sentinel")
        rows = self.collect([workout()])
        self.assertEqual(workouts.save_workouts(rows, self.root), 1)
        original = self.path.read_bytes()
        self.assertEqual(workouts.save_workouts(self.collect([workout(), workout()]), self.root), 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(workouts.read_workouts(self.path), rows)
        newer = workout(updated_at="2026-09-11T00:00:00Z", score_state="UNSCORABLE")
        workouts.save_workouts(self.collect([newer, workout(2)]), self.root)
        workouts.save_workouts(rows, self.root)  # Older snapshot cannot roll back a newer record.
        stored = workouts.read_workouts(self.path)
        self.assertEqual(len(stored), 2)
        self.assertIsNone(stored[newer["id"]]["strain"])
        for name in ("cycles.csv", "whoop_data.csv", ".env", "whoop_tokens.json"):
            self.assertEqual((self.root / name).read_text(), "synthetic sentinel")
        with self.path.open(newline="") as file:
            raw = list(csv.DictReader(file))
        self.assertEqual(raw[0]["strain"], "")
        self.assertEqual(raw[1]["zone_zero_milli"], "0")

    def test_corrupt_archive_and_failed_replace_preserve_output(self):
        rows = self.collect([workout()])
        workouts.save_workouts(rows, self.root)
        original = self.path.read_bytes()
        with patch.object(workouts.os, "replace", side_effect=OSError("synthetic")), self.assertRaises(OSError):
            workouts.save_workouts(rows, self.root)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        text = original.decode()
        variants = ["bad header\n", text + text.splitlines()[-1] + "\n",
                    text.replace("2026-09-10T23:50:00+08:00", "2026-09-11T23:50:00+08:00")]
        for malformed in variants:
            self.path.write_text(malformed)
            before = self.path.read_bytes()
            with self.assertRaises(APIError):
                workouts.save_workouts(rows, self.root)
            self.assertEqual(self.path.read_bytes(), before)

    def test_windows_and_cli(self):
        now = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
        self.assertEqual(workouts.query_window(now=now), ("2026-08-21T12:00:00Z", "2026-09-20T12:00:00Z"))
        self.assertEqual(workouts.query_window(2, end_date=date(2026, 9, 20)),
                         ("2026-09-19T00:00:00Z", "2026-09-21T00:00:00Z"))
        self.assertEqual(workouts.query_window(start_date=date(2026, 9, 20), end_date=date(2026, 9, 20)),
                         ("2026-09-20T00:00:00Z", "2026-09-21T00:00:00Z"))
        for kwargs in ({"days": 0}, {"start_date": date(2026, 9, 20)},
                       {"days": 30, "start_date": date(2026, 9, 20), "end_date": date(2026, 9, 21)}):
            with self.assertRaises(APIError):
                workouts.query_window(**kwargs)
        with patch.object(workouts, "WhoopClient") as client, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(workouts.main(["--days", "0"]), 1)
            client.assert_not_called()
        with patch.object(workouts, "WhoopClient") as client, patch.object(workouts, "collect_workouts", return_value={}), \
                patch.object(workouts, "save_workouts", return_value=0), contextlib.redirect_stdout(io.StringIO()) as output:
            client.return_value.tokens = {"scope": "read:workout"}
            self.assertEqual(workouts.main(["--days", "30"]), 0)
        self.assertIn("Stored 0 workouts", output.getvalue())
        self.assertEqual(workouts.save_workouts({}, self.root), 0)
        self.assertEqual(workouts.read_workouts(self.path), {})


if __name__ == "__main__":
    unittest.main()
