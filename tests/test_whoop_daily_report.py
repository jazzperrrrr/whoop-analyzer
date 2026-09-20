"""Synthetic-only tests for the local morning report."""

import contextlib
import copy
import csv
from datetime import date, datetime, timedelta
import io
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import whoop_analysis as analysis
import whoop_daily_report as daily
import whoop_entities
from whoop_activity import classify_workouts, FIELDS
from whoop_entities import SCHEMAS
from whoop_workouts import save_workouts
from test_whoop_workout_analysis import record, correction


D = date(2026, 9, 10)


def entities(day=D, **changes):
    sleep = dict(sleep_id="synthetic-sleep-" + str(day), cycle_id=str(day.toordinal()),
                 start=str(day - timedelta(days=1)) + "T22:00:00Z", end=str(day) + "T06:00:00Z",
                 timezone_offset="+01:00", nap="false", score_state="SCORED", sleep_performance=80)
    sleep.update(changes)
    recovery = dict(sleep_id=sleep["sleep_id"], cycle_id=sleep["cycle_id"], score_state="SCORED",
                    hrv_ms=60, resting_heart_rate_bpm=53, recovery_score=70)
    cycle = dict(cycle_id=sleep["cycle_id"], start=sleep["start"], end=None,
                 timezone_offset=sleep["timezone_offset"], score_state="SCORED", day_strain=15)
    return dict(sleeps=[sleep], recoveries=[recovery], cycles=[cycle])


def observation(day, **changes):
    row = dict(report_date=day, cycle_id=str(day.toordinal()), sleep_id="synthetic-sleep-" + str(day),
               hrv_ms=50, resting_heart_rate_bpm=50, recovery_score=60, sleep_performance=75,
               day_strain=5, sleep_score_state="SCORED", recovery_score_state="SCORED",
               cycle_score_state="SCORED", sleep_timezone_offset="+01:00", cycle_timezone_offset="+01:00",
               sleep_end=str(day) + "T06:00:00Z", cycle_start=str(day - timedelta(days=1)) + "T22:00:00Z")
    row.update(changes)
    return row


def history(n=14, **changes):
    return [observation(D - timedelta(days=i), **changes) for i in range(1, n + 1)]


def report(source=None, physiology=None, workouts=()):
    return daily.build_daily_report(**(entities() if source is None else source),
                                    physiology=history() if physiology is None else physiology,
                                    workouts=workouts)


def flags(result):
    return {f["code"]: f["value"] for f in result.data_quality}


class DailyReportTests(unittest.TestCase):
    def test_wake_date_not_cycle_start_host_date_or_latest_workout(self):
        source = entities(end="2026-09-09T23:30:00Z")
        class NoHostDate(date):
            @classmethod
            def today(cls):
                raise AssertionError("Host date must not be read")

        class ExplicitZoneDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                raise AssertionError("Host clock must not be read")

            def astimezone(self, tz=None):
                if tz is None:
                    raise AssertionError("Implicit host timezone conversion")
                return super().astimezone(tz)

        # Unlike changing TZ on Windows, these guards fail the exercised path
        # if it reads the host date/clock or requests an implicit local conversion.
        with patch.object(daily, "date", NoHostDate), patch.object(analysis, "date", NoHostDate), \
                patch.object(whoop_entities, "datetime", ExplicitZoneDatetime):
            a = report(source, workouts=classify_workouts([record(day="2030-01-01")]))
            b = report(source)
        self.assertEqual(a.report_date, D)
        self.assertEqual(a.report_date, b.report_date)
        self.assertEqual(a.sleep["local_end"].hour, 0)
        self.assertEqual(a.sleep["recorded_interval_minutes"], 90)

    def test_utc_ordering_wins_over_local_date_order(self):
        earlier = entities(end="2026-09-10T01:00:00Z", timezone_offset="+08:00")
        later = entities(end="2026-09-10T02:00:00Z", timezone_offset="-04:00", sleep_id="synthetic-later")
        s = daily.select_report_morning(earlier["sleeps"] + later["sleeps"])
        self.assertEqual(s["sleep"]["sleep_id"], "synthetic-later")
        self.assertEqual(s["report_date"], D - timedelta(days=1))

    def test_naps_excluded_and_no_primary_explicit(self):
        source = entities(nap="true")
        r = report(source)
        self.assertIsNone(r.report_date)
        self.assertTrue(flags(r)["missing_primary_sleep"])
        self.assertEqual(r.interpretation["overall_state"], "insufficient data")
        self.assertIn("unavailable", daily.format_daily_report(r))

    def test_pending_latest_not_replaced_by_older_scored(self):
        source = entities(score_state="PENDING_SCORE")
        source["recoveries"][0]["score_state"] = "PENDING_SCORE"
        older = entities(D - timedelta(days=1))
        for kind in source:
            source[kind] += older[kind]
        r = report(source)
        self.assertEqual(r.report_date, D)
        self.assertEqual(r.selection["status"], "pending_or_unscorable")
        self.assertTrue(all(p["value"] is None for p in r.physiology.values()))

    def test_invalid_or_incomplete_primary_is_explicit_not_silent(self):
        for changes in ({"end": None}, {"end": "2026-09-10T06:00:00"},
                        {"end": "2026-09-08T06:00:00Z"}, {"timezone_offset": None}):
            with self.subTest(changes=changes):
                r = report(entities(**changes))
                self.assertIsNone(r.report_date)
                self.assertTrue(flags(r)["chronology_or_association_issue"])
        source = entities(end=None)
        source["sleeps"] += entities(D - timedelta(days=1))["sleeps"]
        self.assertTrue(flags(report(source))["chronology_or_association_issue"])

    def test_duplicate_primary_withholds_current_values(self):
        source = entities()
        source["sleeps"].append({**source["sleeps"][0], "sleep_id": "synthetic-second"})
        r = report(source)
        self.assertEqual(r.selection["status"], "ambiguous")
        self.assertTrue(flags(r)["ambiguous_primary_sleep"])
        self.assertTrue(all(p["value"] is None for p in r.physiology.values()))
        self.assertEqual(len(r.selection["candidate_sleep_ids"]), 2)

    def test_exact_entity_matching_and_no_fallback(self):
        source = entities()
        source["recoveries"][0]["sleep_id"] = "synthetic-wrong"
        source["recoveries"] += entities(D - timedelta(days=1))["recoveries"]
        r = report(source)
        self.assertIsNone(r.physiology["hrv_ms"]["value"])
        self.assertEqual(r.physiology["sleep_performance"]["value"], 80)
        self.assertTrue(flags(r)["chronology_or_association_issue"])
        self.assertEqual(r.report_date, D)
        source = entities()
        source["cycles"][0]["cycle_id"] = "unrelated"
        self.assertIsNone(report(source).selection["cycle"])

    def test_duplicate_recovery_and_cycle_remain_visible(self):
        source = entities()
        source["recoveries"] *= 2
        source["cycles"] *= 2
        r = report(source)
        self.assertTrue(flags(r)["ambiguous_physiology"])
        self.assertIsNone(r.physiology["hrv_ms"]["value"])
        self.assertIsNone(r.selection["cycle"])

    def test_baseline_bounds_existing_algorithm_and_metric_coverage(self):
        h = history()
        h[-1]["hrv_ms"] = 64
        h[0]["sleep_performance"] = None
        h += [observation(D, hrv_ms=9999), observation(D + timedelta(days=1), hrv_ms=9999),
              observation(D - timedelta(days=15), hrv_ms=9999)]
        with patch.object(analysis, "rolling_baselines", wraps=analysis.rolling_baselines) as reused:
            r = report(physiology=h)
        reused.assert_called_once()
        self.assertEqual(r.baseline["hrv_ms"][14]["average"], 51)
        self.assertEqual(r.baseline["hrv_ms"][14]["days_available"], 14)
        self.assertEqual(r.baseline["sleep_performance"][14]["days_available"], 13)
        self.assertEqual(r.baseline["hrv_ms"][14]["window_start"], D - timedelta(days=14))
        self.assertEqual(set(r.baseline["hrv_ms"]), {7, 14, 30})

    def test_seven_observation_minimum_and_missing_days_not_zero(self):
        for n in (6, 7):
            r = report(physiology=history(n))
            self.assertEqual(r.baseline["hrv_ms"][14]["average"], 50)
            self.assertEqual(r.baseline["hrv_ms"][14]["required_count"], 7)
            self.assertEqual(r.baseline["hrv_ms"][14]["eligible"], n == 7)
            self.assertEqual(r.interpretation["overall_state"] == "insufficient data", n == 6)

    def test_deviations_zero_baseline_and_zero_values(self):
        r = report()
        self.assertAlmostEqual(r.baseline["hrv_ms"][14]["percentage_deviation"], 20)
        for metric, delta in (("resting_heart_rate_bpm", 3), ("recovery_score", 10), ("sleep_performance", 5)):
            self.assertEqual(r.baseline[metric][14]["difference"], delta)
        r = report(physiology=history(hrv_ms=0))
        self.assertIsNone(r.baseline["hrv_ms"][14]["percentage_deviation"])
        self.assertEqual(r.interpretation["signals"]["hrv_ms"]["state"], "insufficient data")
        source = entities()
        source["recoveries"][0].update(recovery_score=0, hrv_ms=None)
        r = report(source)
        self.assertEqual(r.physiology["recovery_score"]["value"], 0)
        self.assertIsNone(r.physiology["hrv_ms"]["value"])

    def test_pending_baseline_remnants_excluded(self):
        h = history()
        h[0]["recovery_score_state"] = "PENDING_SCORE"
        r = report(physiology=h)
        self.assertEqual(r.baseline["hrv_ms"][14]["days_available"], 13)
        self.assertEqual(r.baseline["sleep_performance"][14]["days_available"], 14)

    def test_equal_calendar_weighting_and_duplicates_visible(self):
        h = history()
        h.append({**h[0], "cycle_id": "synthetic-extra", "sleep_id": "synthetic-extra", "hrv_ms": 78})
        r = report(physiology=h)
        self.assertEqual(r.baseline["hrv_ms"][14]["average"], 51)
        self.assertTrue(flags(r)["ambiguous_physiology"])

    def test_readiness_reused_unchanged_and_strain_excluded(self):
        for source in (entities(), entities(sleep_performance=75)):
            with patch.object(analysis, "interpret_record", wraps=analysis.interpret_record) as reused:
                r = report(source)
            reused.assert_called_once()
            expected = analysis.interpret_record({"metrics": {
                m: dict(value=r.physiology[m]["value"], baselines=r.baseline[m]) for m in daily.METRICS}})
            self.assertEqual(r.interpretation, expected)
            self.assertNotIn("day_strain", r.interpretation["signals"])
        source = entities(sleep_performance=75)
        source["recoveries"][0].update(hrv_ms=50, resting_heart_rate_bpm=50, recovery_score=60)
        r = report(source)
        self.assertEqual(r.interpretation["overall_state"], "mixed")
        self.assertTrue(all(s["state"] == "neutral" for s in r.interpretation["signals"].values()))
        self.assertTrue(r.provenance["cycle_strain"]["provisional"])
        self.assertNotIn("strain", daily.format_daily_report(r).lower())

    def test_exact_training_bounds_canonical_records_not_sessions(self):
        raw = [record(i + 1, "pickleball", day=str(D - timedelta(days=i))) for i in range(5)]
        raw.append(record(6, "squash", day=str(D - timedelta(days=1))))
        rows = classify_workouts(raw, {r["workout_id"]: correction(r) for r in raw})
        r = report(workouts=rows)
        self.assertEqual(r.yesterday_training["record_count"], 2)
        self.assertEqual(r.yesterday_training["activities"]["Badminton"]["distinct_training_dates"], 1)
        self.assertEqual(r.recent_training["record_count"], 4)
        self.assertEqual(r.recent_training["activities"]["Badminton"]["distinct_training_dates"], 3)
        self.assertIsNone(r.recent_training["real_world_session_count"])
        self.assertEqual(rows[0]["recorded_activity"], "pickleball")
        self.assertNotIn("arithmetic_session_strain_sum", r.recent_training)

    def test_no_recorded_workouts_not_rest_or_zero_duration(self):
        r = report()
        self.assertIsNone(r.yesterday_training["duration_seconds"])
        self.assertEqual(r.yesterday_training["calendar_coverage"], "unknown")
        self.assertIsNone(r.context["mixed_activity_yesterday"])
        self.assertIn("No recorded workouts.", daily.format_daily_report(r))
        self.assertIn("no rest days inferred", daily.format_daily_report(r))

    def test_recent_mixed_and_baseline_offsets(self):
        h = history()
        h[0]["cycle_timezone_offset"] = "+08:00"
        r = report(physiology=h)
        self.assertTrue(r.context["recent_offset_transition"])
        self.assertTrue(r.context["baseline_offset_transition"])
        self.assertIn(str(D - timedelta(days=1)), r.context["mixed_offset_dates"])
        self.assertTrue(r.baseline["hrv_ms"][14]["eligible"])
        h = history()
        h[-1]["cycle_timezone_offset"] = "+08:00"
        r = report(physiology=h)
        self.assertFalse(r.context["recent_offset_transition"])
        self.assertTrue(r.context["baseline_offset_transition"])

    def test_baseline_future_chronology_excluded(self):
        h = history()
        h[0]["sleep_end"] = "2026-09-11T00:00:00Z"
        r = report(physiology=h)
        self.assertEqual(r.baseline["hrv_ms"][14]["days_available"], 13)
        self.assertTrue(flags(r)["chronology_or_association_issue"])

    def test_sleep_label_model_cli_and_identifiers_hidden(self):
        workouts = classify_workouts([record(day=str(D - timedelta(days=1)))])
        r = report(workouts=workouts)
        text = daily.format_daily_report(r)
        self.assertIn(str(r.report_date), text)
        self.assertIn("Recorded sleep interval: 480.0 min", text)
        self.assertIn("State: " + r.interpretation["overall_state"], text)
        self.assertEqual(r.yesterday_training["record_count"], 1)
        for term in (workouts[0]["workout_id"], r.provenance["sleep_id"], r.provenance["cycle_id"],
                     "time asleep", "time in bed", "sleep duration"):
            self.assertNotIn(term, text)

    def matching_snapshot(self, **changes):
        return observation(D, hrv_ms=60, resting_heart_rate_bpm=53, recovery_score=70,
                           sleep_performance=80, **changes)

    def test_explicit_missing_snapshot_withholds_stale_recovery_only(self):
        row = self.matching_snapshot()
        row.update(recovery_present="false", recovery_score_state="", hrv_ms=None,
                   resting_heart_rate_bpm=None, recovery_score=None)
        r = report(physiology=history() + [row])
        for m in ("hrv_ms", "resting_heart_rate_bpm", "recovery_score"):
            self.assertIsNone(r.physiology[m]["value"])
            self.assertIn("explicitly_missing_entity", r.physiology[m]["source_conflicts"])
        self.assertEqual(r.physiology["sleep_performance"]["value"], 80)
        self.assertTrue(flags(r)["source_snapshot_conflict"])
        self.assertIn("Source snapshot conflict; withheld current metrics", daily.format_daily_report(r))

    def test_absent_snapshot_is_distinct_from_explicit_missing(self):
        r = report()  # Only historical rows, no same-entity snapshot at all.
        self.assertEqual(r.provenance["snapshot_consistency"]["status"], "absent")
        self.assertEqual(r.physiology["hrv_ms"]["value"], 60)
        self.assertTrue(flags(r)["missing_matching_daily_snapshot"])
        self.assertFalse(flags(r)["source_snapshot_conflict"])
        self.assertIn("scored entity values are unverified", daily.format_daily_report(r))
        matched = report(physiology=history() + [self.matching_snapshot()])
        self.assertEqual(matched.provenance["snapshot_consistency"]["status"], "matched")
        self.assertFalse(flags(matched)["missing_matching_daily_snapshot"])

    def test_metric_value_and_missing_conflicts_are_independent(self):
        for value in (None, 0, 61):
            row = self.matching_snapshot()
            row["hrv_ms"] = value
            r = report(physiology=history() + [row])
            self.assertIsNone(r.physiology["hrv_ms"]["value"])
            for m in ("resting_heart_rate_bpm", "recovery_score", "sleep_performance"):
                self.assertTrue(r.physiology[m]["available"])
        row = self.matching_snapshot()
        row["sleep_performance"] = 79
        r = report(physiology=history() + [row])
        self.assertIsNone(r.sleep["sleep_performance"])
        self.assertEqual(r.physiology["hrv_ms"]["value"], 60)

    def test_pending_snapshot_and_duplicate_snapshot_are_conflicts(self):
        row = self.matching_snapshot()
        row["sleep_score_state"] = "PENDING_SCORE"
        r = report(physiology=history() + [row])
        self.assertIsNone(r.physiology["sleep_performance"]["value"])
        self.assertEqual(r.physiology["hrv_ms"]["value"], 60)
        row = self.matching_snapshot()
        r = report(physiology=history() + [row, dict(row)])
        self.assertTrue(all(not p["available"] for p in r.physiology.values()))

    def test_snapshot_wrong_date_and_association_not_silently_used(self):
        for change in ({"report_date": D + timedelta(days=1)}, {"cycle_id": "999999"}, {"sleep_id": "other-sleep"}):
            row = self.matching_snapshot()
            row.update(change)
            r = report(physiology=history() + [row])
            self.assertTrue(flags(r)["source_snapshot_conflict"])
            self.assertIsNone(r.physiology["hrv_ms"]["value"])
            self.assertEqual(r.report_date, D)

    def test_current_identity_exclusion_is_metric_specific(self):
        for identity, affected in (("sleep_id", {"sleep_performance"}),
                                   ("cycle_id", {"hrv_ms", "resting_heart_rate_bpm", "recovery_score"})):
            h = history()
            h[0][identity] = entities()["sleeps"][0][identity]
            for m in affected:
                h[0][m] = 900
            r = report(physiology=h)
            for m in daily.METRICS:
                b = r.baseline[m][14]
                self.assertEqual(b["days_available"], 13 if m in affected else 14)
                self.assertAlmostEqual(b["average"], observation(D)[m])
            self.assertTrue(flags(r)["current_identity_excluded_from_baseline"])
            self.assertTrue(flags(r)["source_snapshot_conflict"])

    def test_tied_utc_conflicting_dates_disable_windows_regardless_ids_or_order(self):
        for left_id, right_id in (("a", "z"), ("z", "a")):
            a = entities(end="2026-09-10T01:00:00Z", timezone_offset="+08:00", sleep_id=left_id)
            b = entities(end="2026-09-10T01:00:00Z", timezone_offset="-04:00", sleep_id=right_id)
            for sleeps in (a["sleeps"] + b["sleeps"], b["sleeps"] + a["sleeps"]):
                source = dict(sleeps=sleeps + entities(D - timedelta(days=2))["sleeps"], recoveries=[], cycles=[])
                r = report(source)
                self.assertIsNone(r.report_date)
                self.assertEqual(r.selection["status"], "ambiguous_date")
                self.assertEqual(r.selection["candidate_dates"], [D - timedelta(days=1), D])
                self.assertEqual(r.yesterday_training, {})
                self.assertEqual(r.recent_training, {})
                self.assertIsNone(r.baseline["hrv_ms"][14]["window_start"])
                self.assertEqual(r.baseline["hrv_ms"][14]["days_available"], 0)
                self.assertIn("date-dependent windows unavailable", daily.format_daily_report(r))

    def test_multiple_current_candidates_exclude_all_identities(self):
        for names in (("candidate-a", "candidate-b"), ("candidate-z", "candidate-a")):
            for a_end in ("2026-09-10T05:00:00Z", "2026-09-10T07:00:00Z"):
                a = entities(sleep_id=names[0], cycle_id="900001", end=a_end)["sleeps"][0]
                b = entities(sleep_id=names[1], cycle_id="900002")["sleeps"][0]
                for ordered in ([a, b], [b, a]):
                    with self.subTest(names=names, a_end=a_end, first=ordered[0]["sleep_id"]):
                        h = history()
                        for row, candidate in zip(h, (a, b)):
                            row.update(sleep_id=candidate["sleep_id"], cycle_id=candidate["cycle_id"],
                                       sleep_timezone_offset="+08:00", cycle_timezone_offset="+08:00")
                            row.update({m: 900 for m in daily.METRICS})
                        # A real, unrelated prior primary sleep and its non-default
                        # measurements must remain in the equally weighted history.
                        h[2].update(hrv_ms=30, resting_heart_rate_bpm=45, recovery_score=65, sleep_performance=85)
                        source = dict(sleeps=ordered + entities(D - timedelta(days=3))["sleeps"],
                                      recoveries=[], cycles=[])
                        r = report(source, h)
                        self.assertEqual(r.report_date, D)
                        self.assertTrue(r.selection["ambiguous_primary"])
                        self.assertTrue(all(not p["available"] for p in r.physiology.values()))
                        for m in daily.METRICS:
                            self.assertAlmostEqual(r.baseline[m][14]["average"], (11 * observation(D)[m] + h[2][m]) / 12)
                            self.assertEqual(r.baseline[m][14]["days_available"], 12)
                            self.assertEqual(r.provenance["baseline_identity_exclusions"][m], 2)
                        self.assertEqual(set(r.selection["candidate_sleep_ids"]), set(names))
                        self.assertEqual(set(r.selection["candidate_cycle_ids"]), {"900001", "900002"})
                        self.assertTrue(flags(r)["current_identity_excluded_from_baseline"])
                        self.assertFalse(r.context["baseline_offset_transition"])
                        text = daily.format_daily_report(r)
                        for identity in (*names, "900001", "900002"):
                            self.assertNotIn(identity, text)

    def test_multiple_current_candidates_filter_per_metric_and_count_removed_values(self):
        a = entities(sleep_id="candidate-a", cycle_id="900001", end="2026-09-10T05:00:00Z")["sleeps"][0]
        b = entities(sleep_id="candidate-b", cycle_id="900002")["sleeps"][0]
        h = history()
        # Only the sleep identity matches on row 0; recovery values must survive.
        h[0].update(sleep_id=a["sleep_id"], sleep_performance=900, hrv_ms=64)
        # Only the recovery identity matches on row 1; sleep must survive.
        h[1].update(cycle_id=b["cycle_id"], hrv_ms=900, resting_heart_rate_bpm=900,
                    recovery_score=900, sleep_performance=89)
        # Already missing values are not counted as measurements removed by identity.
        h[2].update(sleep_id=a["sleep_id"], cycle_id=a["cycle_id"])
        h[2].update({m: None for m in daily.METRICS})
        r = report(dict(sleeps=[a, b], recoveries=[], cycles=[]), h)
        expected = dict(hrv_ms=(11 * 50 + 64) / 12, resting_heart_rate_bpm=50,
                        recovery_score=60, sleep_performance=(11 * 75 + 89) / 12)
        for m in daily.METRICS:
            self.assertAlmostEqual(r.baseline[m][14]["average"], expected[m])
            self.assertEqual(r.baseline[m][14]["days_available"], 12)
            self.assertEqual(r.provenance["baseline_identity_exclusions"][m], 1)

    def test_ambiguous_date_with_incomplete_sleep_does_not_claim_selected_morning(self):
        a = entities(end="2026-09-10T01:00:00Z", timezone_offset="+08:00", sleep_id="tie-a")
        b = entities(end="2026-09-10T01:00:00Z", timezone_offset="-04:00", sleep_id="tie-b")
        for start, expected in (("2026-09-10T02:00:00Z", "newer incomplete primary sleep observed"),
                                (None, "incomplete primary sleep chronology is unknown")):
            c = entities(start=start, end=None, sleep_id="unfinished")
            r = report(dict(sleeps=a["sleeps"] + b["sleeps"] + c["sleeps"], recoveries=[], cycles=[]))
            self.assertIsNone(r.report_date)
            self.assertEqual(r.selection["status"], "ambiguous_date")
            self.assertEqual(r.recent_training, {})
            self.assertIsNone(r.baseline["hrv_ms"][14]["window_start"])
            text = daily.format_daily_report(r)
            self.assertIn("Ambiguous wake-up dates:", text)
            self.assertIn(expected, text)
            self.assertNotIn("Latest completed morning shown", text)
            self.assertIn("No completed morning selected", text)
            self.assertNotIn("unfinished", text)
        ordinary = entities(end=None)
        ordinary["sleeps"] += entities(D - timedelta(days=1))["sleeps"]
        r = report(ordinary)
        self.assertEqual(r.report_date, D - timedelta(days=1))
        self.assertIn("Latest completed morning shown; newer incomplete primary sleep observed", daily.format_daily_report(r))

    def test_newer_incomplete_primary_has_explicit_fallback(self):
        older = entities(D - timedelta(days=1))
        source = entities(end=None)
        source["sleeps"] += older["sleeps"]
        r = report(source)
        self.assertEqual(r.report_date, D - timedelta(days=1))
        self.assertEqual(r.selection["fallback_status"], "newer_incomplete_primary")
        self.assertTrue(r.selection["newer_incomplete_primary"])
        self.assertIsNone(r.selection["incomplete_candidates"][0]["report_date"])
        text = daily.format_daily_report(r)
        self.assertIn("newer incomplete primary sleep observed", text)
        self.assertIn("wake-up date is unknown", text)
        self.assertNotIn(source["sleeps"][0]["sleep_id"], text)

    def test_incomplete_chronology_unknown_when_start_unknown_or_earlier(self):
        for start in (None, "2026-09-01T00:00:00Z"):
            source = entities()
            source["sleeps"] += entities(start=start, end=None, sleep_id="unfinished")["sleeps"]
            r = report(source)
            self.assertEqual(r.report_date, D)
            self.assertIsNone(r.selection["newer_incomplete_primary"])
            self.assertEqual(r.selection["fallback_status"], "incomplete_order_unknown")
            self.assertIn("incomplete primary sleep chronology is unknown", daily.format_daily_report(r))

    def test_unscored_or_filtered_rows_do_not_change_baseline_offsets(self):
        for reason in ("unscored", "future_chronology", "current_identity"):
            h = history()
            h[-1].update(sleep_timezone_offset="+08:00", cycle_timezone_offset="+08:00")
            if reason == "unscored":
                h[-1].update(sleep_score_state="PENDING_SCORE", recovery_score_state="PENDING_SCORE")
            elif reason == "future_chronology":
                h[-1]["sleep_end"] = "2026-09-11T00:00:00Z"
            else:
                for key in ("sleep_id", "cycle_id"):
                    h[-1][key] = entities()["sleeps"][0][key]
            r = report(physiology=h)
            self.assertFalse(r.context["baseline_offset_transition"])
            self.assertEqual(r.baseline["hrv_ms"][14]["days_available"], 13)

    def test_no_date_mixed_offset_is_unknown(self):
        r = report(entities(nap="true"))
        self.assertIsNone(flags(r)["mixed_offset_date"])
        self.assertIsNone(r.context["mixed_offset_date"])
        self.assertIn("Mixed-offset date: unknown", daily.format_daily_report(r))

    def test_zone45_partial_missing_zero_and_badminton_denominator(self):
        for mode, expected, label in (("partial", 60000, "1/2 (partial coverage)"),
                                      ("missing", None, "0/2 (all missing)"),
                                      ("zero", 0, "2/2")):
            rows = [record(i, day=str(D - timedelta(days=1))) for i in (1, 2)]
            for r in rows:
                r.update(zone_four_milli=0, zone_five_milli=0)
            if mode == "partial":
                rows[0].update(zone_four_milli=60000)
                rows[1].update(zone_five_milli=None)
            elif mode == "missing":
                for r in rows:
                    r.update(zone_four_milli=None, zone_five_milli=None)
            rows.append(record(3, "golf", day=str(D - timedelta(days=1))))
            r = report(workouts=classify_workouts(rows))
            self.assertEqual(r.yesterday_training["badminton"]["zone45_milli"], expected)
            text = daily.format_daily_report(r)
            self.assertIn("available records: " + label, text)
            self.assertIn("Badminton Z4+5: " + ("N/A" if expected is None else "0.0" if expected == 0 else "1.0"), text)

    def test_build_does_not_mutate_inputs(self):
        source, h, workouts = entities(), history(), classify_workouts([record()])
        before = copy.deepcopy((source, h, workouts))
        report(source, h, workouts)
        self.assertEqual((source, h, workouts), before)

    def test_cli_synthetic_files_network_blocked_no_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = entities()
            for kind, rows in source.items():
                with (root / (kind + ".csv")).open("w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=SCHEMAS[kind])
                    writer.writeheader()
                    writer.writerows(rows)
            with (root / "daily_metrics.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=SCHEMAS["daily_metrics"])
                writer.writeheader()
                writer.writerows(history())
            raw = record()
            save_workouts({raw["workout_id"]: raw}, root)
            with (root / "workout_classifications.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerow(correction(raw))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            original_open = Path.open

            def readonly(path, mode="r", *args, **kwargs):
                self.assertFalse(any(c in mode for c in "wax+"))
                return original_open(path, mode, *args, **kwargs)

            output = io.StringIO()
            with patch.object(Path, "open", readonly), patch.object(socket, "socket", side_effect=AssertionError("network")), \
                    patch("whoop_fetch.WhoopClient", side_effect=AssertionError("API")), \
                    patch("whoop_auth.refresh_tokens", side_effect=AssertionError("OAuth")), \
                    contextlib.redirect_stdout(output):
                code = daily.main(["--data-dir", str(root)])
            self.assertEqual(code, 0, output.getvalue())
            self.assertIn(str(D), output.getvalue())
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})


if __name__ == "__main__":
    unittest.main()
