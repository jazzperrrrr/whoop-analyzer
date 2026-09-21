"""Synthetic-only coverage for the extended, backward-compatible sleep layer."""

import copy
import csv
from datetime import date
from itertools import permutations
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import requests
import whoop_daily_report as daily
import whoop_entities as entities
from whoop_fetch import APIError
import whoop_sleep as sleep
from test_whoop_history import Client, cycle, recovery


LEGACY_HEADER = ("sleep_id", "cycle_id", "start", "end", "timezone_offset",
                 "created_at", "updated_at", "nap", "score_state", "sleep_performance")
DAY = date(2026, 5, 10)
EXPECTED = {
    "total_in_bed_time_ms": 10000,
    "total_awake_time_ms": 1000,
    "total_no_data_time_ms": 500,
    "total_light_sleep_time_ms": 4000,
    "total_slow_wave_sleep_time_ms": 2000,
    "total_rem_sleep_time_ms": 2500,
    "sleep_cycle_count": 3,
    "disturbance_count": 4,
    "baseline_sleep_need_ms": 9000,
    "sleep_debt_need_ms": 1000,
    "recent_strain_need_ms": 500,
    "recent_nap_need_ms": -1500,
    "sleep_efficiency": 85.0,
    "sleep_consistency": 90.0,
    "respiratory_rate": 14.25,
}


def response(sid="synthetic-primary", nap=False):
    return dict(id=sid, cycle_id=1, start="2026-05-09T22:00:00Z",
                end="2026-05-10T06:00:00Z", timezone_offset="+01:00",
                created_at="2026-05-10T06:01:00Z", updated_at="2026-05-10T06:05:00Z",
                nap=nap, score_state="SCORED", score={
                    "sleep_performance_percentage": 88,
                    "stage_summary": {
                        "total_in_bed_time_milli": 10000, "total_awake_time_milli": 1000,
                        "total_no_data_time_milli": 500, "total_light_sleep_time_milli": 4000,
                        "total_slow_wave_sleep_time_milli": 2000, "total_rem_sleep_time_milli": 2500,
                        "sleep_cycle_count": 3, "disturbance_count": 4},
                    "sleep_needed": {"baseline_milli": 9000, "need_from_sleep_debt_milli": 1000,
                                     "need_from_recent_strain_milli": 500, "need_from_recent_nap_milli": -1500},
                    "sleep_efficiency_percentage": 85, "sleep_consistency_percentage": 90,
                    "respiratory_rate": 14.25})


class SleepTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "sleeps.csv"
        # These tests cannot instantiate a real client, refresh tokens or use a network.
        for target in ((socket, "socket"), (requests.sessions.Session, "request")):
            self.enterContext(patch.object(*target, side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch("whoop_fetch.WhoopClient", side_effect=AssertionError("API forbidden")))
        self.enterContext(patch("whoop_auth.refresh_tokens", side_effect=AssertionError("OAuth forbidden")))

    def write(self, rows, header=None):
        with self.path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=entities.SCHEMAS["sleeps"] if header is None else header)
            writer.writeheader()
            writer.writerows(rows)

    def normalized(self, **changes):
        record = response()
        record.update(changes)
        return entities.normalize("sleeps", record)

    def legacy(self, **changes):
        row = self.normalized(**changes)
        return {key: row[key] for key in LEGACY_HEADER}

    def collect(self, records):
        return entities.collect_history(Client(
            cycles=[cycle(start="2026-05-09T22:00:00Z")], sleeps=records,
            recoveries=[recovery(sid="synthetic-primary")]), DAY)

    def test_maps_every_extended_field_and_preserves_source(self):
        original = response()
        before = copy.deepcopy(original)
        row = entities.normalize("sleeps", original)
        self.assertEqual({key: row[key] for key in EXPECTED}, EXPECTED)
        self.assertEqual(set(row), set(entities.SCHEMAS["sleeps"]))
        self.assertEqual(row["sleep_performance"], 88)
        self.assertEqual(original, before)
        self.assertTrue(all(type(row[field]) is int for field in sleep.INTEGER_FIELDS))

    def test_legacy_header_read_only_expansion(self):
        self.write([self.legacy()], LEGACY_HEADER)
        before = self.path.read_bytes()
        original_open = Path.open

        def readonly(path, mode="r", *args, **kwargs):
            self.assertFalse(any(c in mode for c in "wax+"))
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", readonly):
            row = next(iter(entities.read_entities(self.path, "sleeps").values()))
        self.assertEqual(set(row), set(entities.SCHEMAS["sleeps"]))
        self.assertTrue(all(row[field] is None for field in EXPECTED))
        self.assertEqual(row["sleep_performance"], "88")
        self.assertEqual(self.path.read_bytes(), before)

    def test_extended_archive_round_trip_and_integer_precision(self):
        row = self.normalized()
        row["total_light_sleep_time_ms"] = 2**53 + 1
        self.write([row])
        loaded = next(iter(entities.read_entities(self.path, "sleeps").values()))
        self.assertEqual(loaded["total_light_sleep_time_ms"], 2**53 + 1)
        self.assertIs(type(loaded["total_light_sleep_time_ms"]), int)
        for field in EXPECTED:
            self.assertEqual(loaded[field], row[field])

    def test_unknown_partial_reordered_and_duplicate_headers_rejected(self):
        headers = [(*LEGACY_HEADER, "unknown"), entities.SCHEMAS["sleeps"][:-1],
                   tuple(reversed(LEGACY_HEADER)), (*LEGACY_HEADER[:-1], "nap"), ()]
        for header in headers:
            with self.subTest(header=header):
                self.write([], header)
                with self.assertRaises(APIError):
                    entities.read_entities(self.path, "sleeps")

    def test_malformed_rows_and_duplicate_ids_rejected(self):
        for header, row in [(LEGACY_HEADER, self.legacy()), (entities.SCHEMAS["sleeps"], self.normalized())]:
            self.write([row, row], header)
            with self.assertRaises(APIError):
                entities.read_entities(self.path, "sleeps")
            self.write([row], header)
            text = self.path.read_text()
            for malformed in (text.rstrip() + ",extra\n", text[:text.rfind(",")] + "\n"):
                self.path.write_text(malformed)
                with self.assertRaises(APIError):
                    entities.read_entities(self.path, "sleeps")

    def test_bad_extended_csv_numbers_rejected(self):
        for field, values in (("total_rem_sleep_time_ms", ["1.5", "1.0", "NaN", "true", "-1"]),
                              ("sleep_cycle_count", ["-1", "2.5"]),
                              ("sleep_efficiency", ["nan", "inf", "-1", "private"]),
                              ("recent_nap_need_ms", ["-1.5"])):
            for value in values:
                with self.subTest(field=field, value=value):
                    row = self.normalized()
                    row[field] = value
                    self.write([row])
                    with self.assertRaises(APIError) as caught:
                        entities.read_entities(self.path, "sleeps")
                    self.assertNotIn("private", str(caught.exception))

    def test_missing_nested_objects_and_fields_stay_null(self):
        for score in (None, {}, {"stage_summary": None, "sleep_needed": None},
                      {"sleep_performance_percentage": 88}):
            row = self.normalized(score=score)
            self.assertTrue(all(row[field] is None for field in EXPECTED))
        record = response()
        del record["score"]
        self.assertTrue(all(entities.normalize("sleeps", record)[field] is None for field in EXPECTED))
        record = response()
        del record["score"]["stage_summary"]["total_rem_sleep_time_milli"]
        row = entities.normalize("sleeps", record)
        self.assertIsNone(row["total_rem_sleep_time_ms"])
        self.assertEqual(row["total_light_sleep_time_ms"], 4000)

    def test_zero_is_not_missing_and_survives_csv(self):
        record = response()
        for group in (record["score"]["stage_summary"], record["score"]["sleep_needed"]):
            for key in group:
                group[key] = 0
        record["score"].update(sleep_efficiency_percentage=0, sleep_consistency_percentage=0, respiratory_rate=0)
        row = entities.normalize("sleeps", record)
        self.assertTrue(all(row[field] == 0 for field in EXPECTED))
        row["total_no_data_time_ms"] = None
        self.write([row])
        loaded = next(iter(entities.read_entities(self.path, "sleeps").values()))
        self.assertIsNone(loaded["total_no_data_time_ms"])
        self.assertEqual(sleep.actual_sleep_ms(loaded), 0)
        self.assertIsNone(sleep.duration_accounting_residual_ms(loaded))

    def test_invalid_api_measurements_and_groups_rejected(self):
        for value in (True, "4000", 4000.5, -1, float("nan"), float("inf")):
            record = response()
            record["score"]["stage_summary"]["total_light_sleep_time_milli"] = value
            with self.assertRaises(APIError):
                entities.normalize("sleeps", record)
        for group in ("stage_summary", "sleep_needed"):
            record = response()
            record["score"][group] = []
            with self.assertRaises(APIError):
                entities.normalize("sleeps", record)

    def test_unscored_measurements_unavailable_and_performance_unchanged(self):
        for state in ("PENDING_SCORE", "UNSCORABLE"):
            row = self.normalized(score_state=state)
            self.assertIsNone(row["sleep_performance"])
            self.assertTrue(all(row[field] is None for field in EXPECTED))
            self.write([row])
            entities.read_entities(self.path, "sleeps")
            row["total_rem_sleep_time_ms"] = 1
            self.write([row])
            with self.assertRaises(APIError):
                entities.read_entities(self.path, "sleeps")
        for value in (0, 88, None, True, "88", float("nan")):
            record = response()
            record["score"]["sleep_performance_percentage"] = value
            expected = value if type(value) is int else None
            self.assertEqual(entities.normalize("sleeps", record)["sleep_performance"], expected)

    def test_actual_and_restorative_sleep(self):
        row = self.normalized()
        self.assertEqual(sleep.actual_sleep_ms(row), 8500)
        self.assertEqual(sleep.restorative_sleep_ms(row), 4500)
        for field in sleep.ACTUAL_FIELDS:
            changed = dict(row, **{field: None})
            self.assertIsNone(sleep.actual_sleep_ms(changed))
            if field in sleep.RESTORATIVE_FIELDS:
                self.assertIsNone(sleep.restorative_sleep_ms(changed))
            else:
                self.assertEqual(sleep.restorative_sleep_ms(changed), 4500)
            del changed[field]
            self.assertIsNone(sleep.actual_sleep_ms(changed))

    def test_signed_sleep_need_and_each_missing_operand(self):
        row = self.normalized()
        self.assertEqual(sleep.derived_total_sleep_need_ms(row), 9000)
        self.assertEqual(sleep.derived_total_sleep_need_ms(dict(row, recent_nap_need_ms=0)), 10500)
        self.assertEqual(sleep.derived_total_sleep_need_ms(dict(row, recent_nap_need_ms=100)), 10600)
        for field in sleep.NEED_FIELDS.values():
            self.assertIsNone(sleep.derived_total_sleep_need_ms(dict(row, **{field: None})))

    def test_accounting_includes_no_data_and_keeps_signed_residual(self):
        row = self.normalized()
        self.assertEqual(sleep.duration_accounting_residual_ms(row), 0)
        self.assertEqual(sleep.duration_accounting_residual_ms(dict(row, total_no_data_time_ms=0)), 500)
        self.assertEqual(sleep.duration_accounting_residual_ms(dict(row, total_in_bed_time_ms=9900)), -100)
        for field in ("total_in_bed_time_ms", *sleep.ACCOUNTING_FIELDS):
            self.assertIsNone(sleep.duration_accounting_residual_ms(dict(row, **{field: None})))

    def test_efficiency_diagnostics_do_not_replace_official_score(self):
        row = self.normalized()
        result = sleep.efficiency_diagnostics(row)
        self.assertEqual(result["official_efficiency_pct"], 85)
        self.assertEqual(result["in_bed_derived_pct"], 85)
        self.assertEqual(result["in_bed_difference_pp"], 0)
        self.assertAlmostEqual(result["excluding_no_data_derived_pct"], 100 * 8500 / 9500)
        self.assertAlmostEqual(result["excluding_no_data_difference_pp"], 85 - 100 * 8500 / 9500)
        for in_bed in (None, 0):
            result = sleep.efficiency_diagnostics(dict(row, total_in_bed_time_ms=in_bed))
            self.assertIsNone(result["in_bed_derived_pct"])
        result = sleep.efficiency_diagnostics(dict(row, total_no_data_time_ms=None))
        self.assertEqual(result["in_bed_derived_pct"], 85)
        self.assertIsNone(result["excluding_no_data_derived_pct"])
        result = sleep.efficiency_diagnostics(dict(row, sleep_efficiency=None))
        self.assertIsNone(result["in_bed_difference_pp"])
        self.assertEqual(row["sleep_efficiency"], 85)

    def test_helpers_do_not_mutate_source_and_empty_inputs_stay_unavailable(self):
        row = self.normalized()
        before = copy.deepcopy(row)
        for helper in (sleep.actual_sleep_ms, sleep.restorative_sleep_ms, sleep.derived_total_sleep_need_ms,
                       sleep.duration_accounting_residual_ms, sleep.efficiency_diagnostics):
            helper(row)
        self.assertEqual(row, before)
        for helper in (sleep.actual_sleep_ms, sleep.restorative_sleep_ms, sleep.derived_total_sleep_need_ms,
                       sleep.duration_accounting_residual_ms):
            self.assertIsNone(helper({}))

    def test_collection_retains_naps_separately_and_daily_schema_unchanged(self):
        nap = response("synthetic-nap", True)
        nap.update(start="2026-05-10T12:00:00Z", end="2026-05-10T13:00:00Z")
        nap["score"]["stage_summary"].update(total_rem_sleep_time_milli=100, total_slow_wave_sleep_time_milli=200)
        nap["score"]["sleep_needed"]["baseline_milli"] = 300
        nap["score"]["sleep_efficiency_percentage"] = 60
        nap_expected = dict(EXPECTED, total_rem_sleep_time_ms=100, total_slow_wave_sleep_time_ms=200,
                            baseline_sleep_need_ms=300, sleep_efficiency=60)
        batch = self.collect([response(), nap])
        records = batch["entities"]["sleeps"]
        self.assertEqual(len(records), 2)
        for sid, expected in (("synthetic-primary", EXPECTED), ("synthetic-nap", nap_expected)):
            self.assertEqual({key: records[(sid,)][key] for key in EXPECTED}, expected)
        self.assertEqual(len(batch["daily_metrics"]), 1)
        self.assertEqual(batch["daily_metrics"][0]["sleep_id"], "synthetic-primary")
        self.assertFalse(set(EXPECTED) & set(entities.SCHEMAS["daily_metrics"]))
        self.assertEqual(batch["daily_metrics"][0]["sleep_performance"], 88)
        selection = daily.select_report_morning(records)
        self.assertEqual(selection["sleep"]["sleep_id"], "synthetic-primary")
        with self.assertRaises(APIError):
            self.collect([response("synthetic-primary", True)])

    def test_pending_latest_primary_not_replaced_by_nap_or_older_scored_sleep(self):
        older = self.normalized(id="synthetic-older", end="2026-05-10T05:00:00Z")
        pending = self.normalized(score_state="PENDING_SCORE")
        nap = self.normalized(id="synthetic-nap", nap=True, end="2026-05-10T13:00:00Z")
        selected = daily.select_report_morning([older, pending, nap])
        self.assertEqual(selected["sleep"]["sleep_id"], pending["sleep_id"])
        self.assertEqual(selected["status"], "ambiguous")  # Both primary candidates remain visible.
        selected = daily.select_report_morning([pending, nap])
        self.assertEqual(selected["status"], "pending_or_unscorable")

    def test_daily_report_and_readiness_identical_with_extended_measurements(self):
        from test_whoop_daily_report import entities as source, history
        original = source()
        extended = copy.deepcopy(original)
        extended["sleeps"][0].update(EXPECTED)
        before = daily.build_daily_report(**original, physiology=history(), workouts=[])
        after = daily.build_daily_report(**extended, physiology=history(), workouts=[])
        self.assertEqual(daily.format_daily_report(before), daily.format_daily_report(after))
        self.assertEqual(before.interpretation, after.interpretation)
        self.assertEqual(before.baseline, after.baseline)

    def test_future_save_expands_legacy_without_manufacturing_or_duplicates(self):
        legacy = [self.legacy(), self.legacy(id="synthetic-unfetched", cycle_id=2)]
        self.write(legacy, LEGACY_HEADER)
        batch = self.collect([response()])
        counts = entities.save_history(batch, self.root)
        self.assertEqual(counts["sleeps"], 2)
        loaded = entities.read_entities(self.path, "sleeps")
        fetched, unfetched = loaded[("synthetic-primary",)], loaded[("synthetic-unfetched",)]
        self.assertEqual({key: fetched[key] for key in EXPECTED}, EXPECTED)
        self.assertTrue(all(unfetched[field] is None for field in EXPECTED))
        for field in LEGACY_HEADER:
            self.assertEqual(unfetched[field], str(legacy[1][field]))
        self.assertEqual(fetched["created_at"], legacy[0]["created_at"])
        self.assertEqual(fetched["updated_at"], legacy[0]["updated_at"])
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        entities.save_history(batch, self.root)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_newer_response_can_clear_details_without_filling_from_old_record(self):
        entities.save_history(self.collect([response()]), self.root)
        record = response()
        record.update(updated_at="2026-05-10T07:00:00Z", score_state="PENDING_SCORE")
        entities.save_history(self.collect([record]), self.root)
        row = next(iter(entities.read_entities(self.path, "sleeps").values()))
        self.assertTrue(all(row[field] is None for field in EXPECTED))
        self.assertIsNone(row["sleep_performance"])

    def test_older_or_undated_response_does_not_overwrite_newer_archive(self):
        entities.save_history(self.collect([response()]), self.root)
        before = self.path.read_bytes()
        for updated in (None, "2026-05-10T06:02:00Z"):
            record = response()
            record.update(updated_at=updated, score={"sleep_performance_percentage": 88})
            entities.save_history(self.collect([record]), self.root)
            self.assertEqual(self.path.read_bytes(), before)

    def daily_rows(self):
        with (self.root / "daily_metrics.csv").open(newline="", encoding="utf-8") as file:
            return list(csv.DictReader(file))

    def test_stale_sleep_snapshot_uses_retained_archive_performance(self):
        entities.save_history(self.collect([response()]), self.root)
        for updated in ("2026-05-10T06:02:00Z", None):
            with self.subTest(updated=updated):
                stale = response()
                stale["updated_at"] = updated
                stale["score"]["sleep_performance_percentage"] = 40
                batch = self.collect([stale])
                before = copy.deepcopy(batch)
                entities.save_history(batch, self.root)
                archive = entities.read_entities(self.path, "sleeps")[("synthetic-primary",)]
                snapshot = self.daily_rows()[0]
                self.assertEqual(snapshot["sleep_performance"], "88")
                self.assertEqual(snapshot["sleep_performance"], archive["sleep_performance"])
                self.assertEqual(archive["updated_at"], response()["updated_at"])
                self.assertEqual(batch, before)

    def test_stale_timing_snapshot_uses_effective_date_and_window(self):
        entities.save_history(self.collect([response()]), self.root)
        stale = response()
        stale.update(updated_at="2026-05-10T06:02:00Z", start="2026-05-08T22:00:00Z",
                     end="2026-05-09T06:00:00Z")
        batch = self.collect([stale])
        entities.save_history(batch, self.root)
        snapshot = self.daily_rows()[0]
        archive = entities.read_entities(self.path, "sleeps")[("synthetic-primary",)]
        self.assertEqual(snapshot["report_date"], "2026-05-10")
        self.assertEqual(snapshot["sleep_start"], archive["start"])
        self.assertEqual(snapshot["sleep_end"], archive["end"])
        # The rejected version is in this window, but the effective version is not.
        batch["first"] = batch["last"] = date(2026, 5, 9)
        entities.save_history(batch, self.root)
        self.assertEqual(self.daily_rows(), [])

    def test_effective_relationship_conflict_rejected_before_any_write(self):
        entities.save_history(self.collect([response()]), self.root)
        stale = response()
        stale.update(updated_at="2026-05-10T06:02:00Z", cycle_id=2)
        batch = entities.collect_history(Client(
            cycles=[cycle(2, start="2026-05-09T22:00:00Z")], sleeps=[stale],
            recoveries=[recovery(2, "synthetic-primary")]), DAY)
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch.object(entities.tempfile, "NamedTemporaryFile", side_effect=AssertionError("No staging")):
            with self.assertRaises(APIError):
                entities.save_history(batch, self.root)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_stale_cycle_and_recovery_snapshot_use_effective_versions(self):
        batch = self.collect([response()])
        for kind in ("cycles", "recoveries"):
            batch["entities"][kind][("1",)]["updated_at"] = "2026-05-10T07:00:00Z"
        entities.save_history(batch, self.root)
        stale = copy.deepcopy(batch)
        for kind in ("cycles", "recoveries"):
            stale["entities"][kind][("1",)]["updated_at"] = "2026-05-10T06:00:00Z"
        stale["entities"]["cycles"][("1",)]["day_strain"] = 1
        stale["entities"]["recoveries"][("1",)]["recovery_score"] = 1
        entities.save_history(stale, self.root)
        snapshot = self.daily_rows()[0]
        for kind, field in (("cycles", "day_strain"), ("recoveries", "recovery_score")):
            archive = entities.read_entities(self.root / (kind + ".csv"), kind)[("1",)]
            self.assertEqual(snapshot[field], archive[field])
            self.assertEqual(float(snapshot[field]), batch["entities"][kind][("1",)][field])

    def test_archive_only_records_do_not_leak_into_snapshot(self):
        unrelated = response("synthetic-unrelated")
        unrelated["cycle_id"] = 2
        entities.save_history(self.collect([response(), unrelated]), self.root)
        stale = response()
        stale["updated_at"] = None
        batch = self.collect([stale])
        batch["entities"]["recoveries"] = {}
        batch["entities"]["cycles"] = {}
        entities.save_history(batch, self.root)
        rows = self.daily_rows()
        self.assertEqual([r["sleep_id"] for r in rows], ["synthetic-primary"])
        self.assertEqual(rows[0]["recovery_present"], "false")
        self.assertEqual(rows[0]["cycle_present"], "false")
        self.assertEqual(rows[0]["recovery_score"], "")
        self.assertEqual(rows[0]["day_strain"], "")
        self.assertEqual(len(entities.read_entities(self.path, "sleeps")), 2)
        entities.save_history(self.collect([]), self.root)
        self.assertEqual(self.daily_rows(), [])

    def assert_both_orders(self, preferred, other):
        expected = entities.normalize("sleeps", preferred)
        results = []
        before = copy.deepcopy((preferred, other))
        for pair in ((preferred, other), (other, preferred)):
            tables = {"sleeps": {}}
            for record in pair:
                entities.insert(tables, "sleeps", record)
            results.append(entities.resolve_entity_candidates(tables["sleeps"][(preferred["id"],)]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], expected)
        self.assertEqual((preferred, other), before)

    def test_duplicate_known_timestamp_beats_missing_in_both_orders(self):
        known, missing = response(), response()
        missing.update(updated_at=None, score={"sleep_performance_percentage": 40})
        self.assert_both_orders(known, missing)

    def test_duplicate_newer_timestamp_beats_older_in_both_orders(self):
        newer, older = response(), response()
        older["updated_at"] = "2026-05-10T06:02:00Z"
        older["score"].update(sleep_performance_percentage=40, sleep_efficiency_percentage=50)
        older["score"]["stage_summary"]["total_rem_sleep_time_milli"] = 20
        older["score"]["sleep_needed"]["baseline_milli"] = 30
        self.assert_both_orders(newer, older)

    def test_equal_timestamp_duplicates_keep_richer_record_and_real_zeros(self):
        for updated in (response()["updated_at"], None):
            rich, sparse = response(), response()
            rich["updated_at"] = sparse["updated_at"] = updated
            rich["score"].update(sleep_performance_percentage=0, sleep_efficiency_percentage=0)
            rich["score"]["stage_summary"]["total_rem_sleep_time_milli"] = 0
            rich["score"]["sleep_needed"]["baseline_milli"] = 0
            sparse["score"] = {}
            self.assert_both_orders(rich, sparse)

    def test_conflicting_equal_timestamp_duplicates_fail_in_both_orders(self):
        first, second = response(), response()
        second["score"]["sleep_performance_percentage"] = 40
        for pair in ((first, second), (second, first)):
            tables = {"sleeps": {}}
            entities.insert(tables, "sleeps", pair[0])
            entities.insert(tables, "sleeps", pair[1])
            with self.assertRaises(APIError):
                entities.resolve_entity_candidates(tables["sleeps"][(first["id"],)])

    def partial_candidates(self):
        rem, need, complete = response(), response(), response()
        rem["score"] = {"stage_summary": {"total_rem_sleep_time_milli": 2500}}
        need["score"] = {"sleep_needed": {"baseline_milli": 3000}}
        complete["score"]["sleep_needed"]["baseline_milli"] = 3000
        return rem, need, complete

    def assert_candidate_permutations(self, records, expected=None):
        before = copy.deepcopy(records)
        expected_row = entities.normalize("sleeps", expected) if expected is not None else None
        errors = []
        count = 0
        for ordered in permutations(records):
            count += 1
            with self.subTest(permutation=count):
                grouped = {"sleeps": {}}
                for record in ordered:
                    entities.insert(grouped, "sleeps", record)
                candidates = grouped["sleeps"][(records[0]["id"],)]
                if expected_row is None:
                    with self.assertRaises(APIError) as caught:
                        entities.resolve_entity_candidates(candidates)
                    errors.append(str(caught.exception))
                else:
                    chosen = entities.resolve_entity_candidates(candidates)
                    self.assertEqual(chosen, expected_row)
                    self.assertTrue(any(chosen is candidate for candidate in candidates))
                # Exercise the real collector too, with duplicates across pages.
                client = Client(cycles=[cycle(start="2026-05-09T22:00:00Z")], sleeps=ordered,
                                recoveries=[recovery(sid="synthetic-primary")], page_size=1)
                if expected_row is None:
                    with self.assertRaises(APIError) as caught:
                        entities.collect_history(client, DAY)
                    errors.append(str(caught.exception))
                else:
                    batch = entities.collect_history(client, DAY)
                    self.assertEqual(batch["entities"]["sleeps"][(records[0]["id"],)], expected_row)
                    self.assertEqual(batch["daily_metrics"][0]["sleep_performance"], expected_row["sleep_performance"])
        if errors:
            self.assertEqual(len(set(errors)), 1)
        self.assertEqual(records, before)
        return count

    def test_three_compatible_candidates_all_six_permutations(self):
        for updated in (response()["updated_at"], None):
            records = self.partial_candidates()
            for record in records:
                record["updated_at"] = updated
            self.assertEqual(self.assert_candidate_permutations(records, records[2]), 6)

    def test_disjoint_candidates_without_cover_reject_all_permutations(self):
        rem, need, _ = self.partial_candidates()
        self.assertEqual(self.assert_candidate_permutations((rem, need)), 2)

    def test_conflicting_rem_candidates_reject_all_permutations(self):
        first, _, _ = self.partial_candidates()
        second = copy.deepcopy(first)
        second["score"]["stage_summary"]["total_rem_sleep_time_milli"] = 2600
        self.assertEqual(self.assert_candidate_permutations((first, second)), 2)

    def test_three_timestamp_groups_all_six_permutations(self):
        newer, older, missing = response(), response(), response()
        older["updated_at"] = "2026-05-10T06:02:00Z"
        missing["updated_at"] = None
        older["score"]["stage_summary"]["total_rem_sleep_time_milli"] = 2600
        missing["score"]["sleep_needed"]["baseline_milli"] = 1234
        self.assertEqual(self.assert_candidate_permutations((older, missing, newer), newer), 6)

    def test_lower_timestamp_conflicts_ignored_in_all_permutations(self):
        older_a, older_b, newer = response(), response(), response()
        for updated in ("2026-05-10T06:02:00Z", None):
            older_a["updated_at"] = older_b["updated_at"] = updated
            older_b["score"]["stage_summary"]["total_rem_sleep_time_milli"] = 2600
            self.assertEqual(self.assert_candidate_permutations((older_a, older_b, newer), newer), 6)

    def test_older_cover_cannot_fill_winning_timestamp_group(self):
        rem, need, older_cover = self.partial_candidates()
        older_cover["updated_at"] = "2026-05-10T06:02:00Z"
        self.assertEqual(self.assert_candidate_permutations((rem, need, older_cover)), 6)

    def test_zero_is_required_information_in_all_permutations(self):
        rem, need, complete = self.partial_candidates()
        rem["score"]["stage_summary"]["total_rem_sleep_time_milli"] = 0
        complete["score"]["stage_summary"]["total_rem_sleep_time_milli"] = 0
        self.assertEqual(self.assert_candidate_permutations((rem, need, complete), complete), 6)
        self.assertEqual(self.assert_candidate_permutations((rem, need)), 2)
        complete["score"]["stage_summary"]["total_rem_sleep_time_milli"] = 2500
        self.assertEqual(self.assert_candidate_permutations((rem, need, complete)), 6)


if __name__ == "__main__":
    unittest.main()
