import contextlib
import copy
import csv
from datetime import date
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import whoop_entities as entities
import whoop_history as history
from whoop_fetch import APIError

TODAY = date(2026, 9, 19)


def cycle(cid=1, start="2026-09-18T22:31:00Z", offset="+01:00", **extra):
    return dict(id=cid, start=start, end=None, timezone_offset=offset,
                score_state="SCORED", score={"strain": 5}, **extra)


def sleep(cid=1, sid="sleep-1", start="2026-09-18T22:31:00Z",
          end="2026-09-19T05:19:00Z", offset="+01:00", nap=False):
    return dict(id=sid, cycle_id=cid, start=start, end=end, timezone_offset=offset,
                nap=nap, score_state="SCORED", score={"sleep_performance_percentage": 90})


def recovery(cid=1, sid="sleep-1"):
    return dict(cycle_id=cid, sleep_id=sid, score_state="SCORED",
                score={"recovery_score": 80, "hrv_rmssd_milli": 60, "resting_heart_rate": 55})


class Client:
    def __init__(self, cycles=(), sleeps=(), recoveries=(), related=None, page_size=25):
        self.records = {"/cycle": list(cycles), "/activity/sleep": list(sleeps), "/recovery": list(recoveries)}
        self.related = related or {}
        self.calls = []
        self.page_size = page_size

    def get(self, path, params=None, optional=False):
        self.calls.append((path, copy.deepcopy(params), optional))
        if path in self.records:
            start = int(params.get("nextToken", "0"))
            end = start + self.page_size
            records = self.records[path]
            return {"records": copy.deepcopy(records[start:end]),
                    "next_token": str(end) if end < len(records) else None}
        return copy.deepcopy(self.related.get(path))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data"

    def collect(self, cycles=None, sleeps=None, recoveries=None, **kwargs):
        client = Client([cycle()] if cycles is None else cycles,
                        [sleep()] if sleeps is None else sleeps,
                        [recovery()] if recoveries is None else recoveries, **kwargs)
        return entities.collect_history(client, TODAY), client

    def test_midnight_china_uk_and_travel_use_sleep_offset(self):
        for offset, end, expected, cycle_offset in (
            ("+01:00", "2026-09-19T05:19:00Z", "2026-09-19", "+01:00"),
            ("+08:00", "2026-09-18T23:19:00Z", "2026-09-19", "+08:00"),
            ("+08:00", "2026-09-18T23:19:00Z", "2026-09-19", "+01:00"),
            ("+01:00", "2026-09-18T23:19:00Z", "2026-09-19", "+08:00"),
        ):
            with self.subTest(offset=offset, cycle_offset=cycle_offset):
                batch, _ = self.collect(cycles=[cycle(offset=cycle_offset)], sleeps=[sleep(offset=offset, end=end)])
                row = batch["daily_metrics"][0]
                self.assertEqual(row["report_date"], expected)
                self.assertEqual(row["sleep_timezone_offset"], offset)
                self.assertEqual(row["cycle_timezone_offset"], cycle_offset)
                self.assertEqual(row["recovery_score"], 80)

    def test_same_start_date_and_same_report_date_keep_distinct_ids(self):
        for end in ("2026-09-18T06:00:00Z", "2026-09-19T04:00:00Z"):
            cs = [cycle(1, "2026-09-18T00:00:00Z"), cycle(2)]
            ss = [sleep(1, start=cs[0]["start"], end=end), sleep(2, "sleep-2")]
            batch, _ = self.collect(cs, ss, [recovery(), recovery(2, "sleep-2")])
            self.assertEqual(len(batch["entities"]["cycles"]), 2)
            self.assertEqual(len(batch["daily_metrics"]), 2)
            self.assertEqual({r["cycle_id"] for r in batch["daily_metrics"]}, {"1", "2"})

    def test_missing_recovery_preserves_sleep_and_null_metrics(self):
        batch, _ = self.collect(recoveries=[])
        row = batch["daily_metrics"][0]
        self.assertEqual(row["recovery_present"], "false")
        self.assertTrue(all(row[k] is None for k in entities.RECOVERY_METRICS))
        self.assertEqual(row["day_strain"], 5)
        self.assertEqual(row["sleep_performance"], 90)

    def test_missing_sleep_preserves_cycle_and_recovery_without_guessed_date(self):
        batch, _ = self.collect(sleeps=[])
        self.assertEqual(len(batch["entities"]["cycles"]), 1)
        self.assertEqual(len(batch["entities"]["recoveries"]), 1)
        self.assertEqual(batch["daily_metrics"], [])
        batch, _ = self.collect(sleeps=[], recoveries=[])
        self.assertEqual(len(batch["entities"]["cycles"]), 1)
        self.assertEqual(batch["daily_metrics"], [])

    def test_missing_cycle_preserves_relationship_and_null_strain(self):
        batch, _ = self.collect(cycles=[])
        row = batch["daily_metrics"][0]
        self.assertIsNone(row["day_strain"])
        self.assertEqual(row["cycle_present"], "false")
        self.assertEqual(row["recovery_score"], 80)

    def test_naps_are_separate_and_never_create_daily_recovery(self):
        batch, _ = self.collect(sleeps=[sleep(), sleep(sid="nap-1", nap=True)])
        self.assertEqual(len(batch["entities"]["sleeps"]), 2)
        self.assertEqual(len(batch["daily_metrics"]), 1)
        batch, _ = self.collect(sleeps=[sleep(sid="nap-1", nap=True)], recoveries=[])
        self.assertEqual(batch["daily_metrics"], [])
        self.assertEqual(len(batch["entities"]["sleeps"]), 1)

    def test_pagination_all_endpoints_and_window_after_wake_date(self):
        cs = [cycle(1, "2026-08-20T13:00:00Z", "+08:00"), cycle(2), cycle(3), cycle(4, "2026-08-19T13:00:00Z")]
        ss = [sleep(1, start=cs[0]["start"], end="2026-08-20T23:00:00Z", offset="+08:00"),
              sleep(2, "sleep-2"), sleep(3, "sleep-3", end="2026-09-19T23:30:00Z"),
              sleep(4, "sleep-4", start=cs[3]["start"], end="2026-08-20T00:00:00Z")]
        rs = [recovery(i, "sleep-" + str(i)) for i in range(1, 5)]
        batch, client = self.collect(cs, ss, rs, page_size=1)
        self.assertEqual([r["report_date"] for r in batch["daily_metrics"]], ["2026-08-21", "2026-09-19"])
        self.assertEqual(len(batch["entities"]["cycles"]), 4)
        for path in client.records:
            calls = [c for c in client.calls if c[0] == path]
            self.assertEqual(len(calls), 4)
            self.assertEqual(calls[1][1]["nextToken"], "1")
            self.assertEqual(calls[0][1]["start"], "2026-08-20T00:00:00+00:00")
            self.assertEqual(calls[0][1]["end"], "2026-09-21T00:00:00+00:00")

    def test_complete_boundary_relationships_by_id(self):
        batch, client = self.collect(cycles=[], sleeps=[], recoveries=[recovery()], related={
            "/cycle/1": cycle(), "/activity/sleep/sleep-1": sleep()})
        self.assertEqual(len(batch["daily_metrics"]), 1)
        self.assertIn(("/cycle/1", None, True), client.calls)
        self.assertIn(("/activity/sleep/sleep-1", None, True), client.calls)
        batch, _ = self.collect(sleeps=[], recoveries=[], related={
            "/cycle/1/recovery": recovery(), "/activity/sleep/sleep-1": sleep()})
        self.assertEqual(len(batch["daily_metrics"]), 1)
        batch, _ = self.collect(sleeps=[], recoveries=[], related={"/cycle/1/sleep": sleep()})
        self.assertEqual(batch["daily_metrics"][0]["recovery_present"], "false")

    def test_mismatched_ids_and_recovery_from_nap_rejected(self):
        for ss, rs in (([sleep(cid=2)], [recovery()]), ([sleep(nap=True)], [recovery()])):
            with self.assertRaises(APIError):
                self.collect(sleeps=ss, recoveries=rs)
        with self.assertRaises(APIError):
            self.collect(sleeps=[], related={"/activity/sleep/sleep-1": sleep(sid="wrong")})

    def test_unscored_invalid_and_zero_measurements(self):
        c, s, r = cycle(), sleep(), recovery()
        c["score"] = {"strain": 0}
        s["score_state"] = "PENDING_SCORE"
        r["score"] = {"recovery_score": True, "hrv_rmssd_milli": float("nan"), "resting_heart_rate": "private"}
        batch, _ = self.collect([c], [s], [r])
        row = batch["daily_metrics"][0]
        self.assertEqual(row["day_strain"], 0)
        self.assertTrue(all(row[k] is None for k in (*entities.RECOVERY_METRICS, "sleep_performance")))

    def test_duplicate_pages_and_newest_update(self):
        newer = cycle(updated_at="2026-09-19T10:00:00Z")
        older = cycle(updated_at="2026-09-19T09:00:00Z")
        older["score"]["strain"] = 1
        batch, _ = self.collect([newer, older], [sleep(), sleep()], [recovery(), recovery()], page_size=1)
        self.assertEqual(len(batch["daily_metrics"]), 1)
        self.assertEqual(batch["daily_metrics"][0]["day_strain"], 5)

    def test_idempotent_storage_updates_dates_without_duplicates_and_preserves_legacy(self):
        for name in ("whoop_history.csv", "whoop_data.csv"):
            (self.root / name).write_bytes(b"unchanged legacy data\r\n")
        batch, _ = self.collect()
        entities.save_history(batch, self.data)
        original = {p.name: p.read_bytes() for p in self.data.iterdir()}
        entities.save_history(batch, self.data)
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.data.iterdir()})
        changed, _ = self.collect(cycles=[cycle(start="2026-09-17T22:00:00Z")],
                                  sleeps=[sleep(start="2026-09-17T22:00:00Z", end="2026-09-18T05:00:00Z")])
        entities.save_history(changed, self.data)
        with (self.data / "daily_metrics.csv").open(newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["report_date"], "2026-09-18")
        for name in ("whoop_history.csv", "whoop_data.csv"):
            self.assertEqual((self.root / name).read_bytes(), b"unchanged legacy data\r\n")
        newer, _ = self.collect([cycle(2)], [sleep(2, "sleep-2")], [recovery(2, "sleep-2")])
        counts = entities.save_history(newer, self.data)
        self.assertEqual(counts, {"cycles": 2, "sleeps": 2, "recoveries": 2, "daily_metrics": 1})
        self.assertEqual(set(original), {k + ".csv" for k in entities.SCHEMAS})

    def test_changed_recovery_sleep_relationship_and_missing_recovery_no_stale_values(self):
        batch, _ = self.collect()
        entities.save_history(batch, self.data)
        changed, _ = self.collect(sleeps=[sleep(sid="replacement")], recoveries=[recovery(sid="replacement")])
        counts = entities.save_history(changed, self.data)
        self.assertEqual(counts["recoveries"], 1)
        self.assertEqual(counts["daily_metrics"], 1)
        changed, _ = self.collect(recoveries=[])
        entities.save_history(changed, self.data)
        with (self.data / "daily_metrics.csv").open(newline="") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["recovery_score"], "")

    def test_invalid_existing_files_and_failed_staging_do_not_overwrite(self):
        batch, _ = self.collect()
        entities.save_history(batch, self.data)
        original = {p.name: p.read_bytes() for p in self.data.iterdir()}
        with patch.object(entities.os, "replace", side_effect=OSError("private")):
            with self.assertRaises(OSError):
                entities.save_history(batch, self.data)
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.data.iterdir()})
        (self.data / "cycles.csv").write_text("broken CSV")
        with self.assertRaises(APIError):
            entities.save_history(batch, self.data)
        self.assertEqual((self.data / "cycles.csv").read_text(), "broken CSV")

    def test_invalid_pagination_dates_and_safe_cli_failure(self):
        for payload in ({"records": None}, {"records": [], "next_token": 5},
                        {"records": [], "next_token": "repeat"}):
            client = Mock()
            client.get.return_value = payload
            with self.assertRaises(APIError):
                entities.collect_history(client, TODAY)
        for bad in ("private", "2026-09-19T01:00:00"):
            with self.assertRaises(APIError) as caught:
                self.collect(sleeps=[sleep(end=bad)])
            self.assertNotIn("private", str(caught.exception))
        with patch.object(history, "WhoopClient") as constructor, \
                patch.object(history, "collect_history", side_effect=APIError("Safe failure")), \
                patch.object(history, "save_history") as save, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(history.main(["--end-date", "2026-09-19"]), 1)
            save.assert_not_called()
        with patch.object(history, "WhoopClient") as constructor, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(history.main(["--days", "2"]), 1)
            constructor.assert_not_called()

    def test_empty_collection_and_cli_success(self):
        batch, _ = self.collect([], [], [])
        counts = entities.save_history(batch, self.data)
        self.assertTrue(all(n == 0 for n in counts.values()))
        with patch.object(history, "WhoopClient"), patch.object(history, "collect_history", return_value=batch), \
                patch.object(history, "save_history", return_value=counts), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(history.main(["--end-date", "2026-09-19"]), 0)
        self.assertIn("data/daily_metrics.csv", output.getvalue())


if __name__ == "__main__":
    unittest.main()
