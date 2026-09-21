"""Synthetic sleep-product tests; never open private input or contact WHOOP."""

import copy
from datetime import timedelta
import socket
import unittest
from unittest.mock import patch

import requests
import whoop_daily_report as daily
from whoop_sleep_product import compose_sleep_product, freshness, measurements
from test_whoop_daily_report import D, entities, history, observation, report
from test_whoop_sleep import EXPECTED


class SleepProductTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(socket, 'socket', side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch.object(requests.Session, 'request', side_effect=AssertionError('HTTP forbidden')))
        self.source = entities()
        self.source['sleeps'][0].update(EXPECTED)

    def product(self, source=None, physiology=None):
        source = source or self.source
        r = report(source, physiology)
        return r, compose_sleep_product(r, source['sleeps'], today=D)

    def test_official_derived_complete_formulas_and_origins(self):
        before = copy.deepcopy(self.source)
        r, p = self.product()
        self.assertEqual(p.metrics['actual_sleep_ms'].value, 8500)
        self.assertEqual(p.metrics['restorative_sleep_ms'].value, 4500)
        self.assertEqual(p.metrics['total_need_ms'].value, 9000)
        self.assertEqual(p.metrics['actual_minus_need_ms'].value, -500)
        self.assertEqual(p.metrics['nap_adjustment_ms'].value, -1500)
        self.assertEqual(p.metrics['efficiency_pct'].value, EXPECTED['sleep_efficiency'])
        self.assertEqual(p.metrics['efficiency_pct'].origin, 'whoop')
        self.assertEqual(p.metrics['actual_sleep_ms'].origin, 'derived')
        self.assertEqual(p.report_date, r.report_date)
        self.assertEqual(p.provenance['sleep_id'], r.selection['sleep']['sleep_id'])
        self.assertEqual(before, self.source)

    def test_stage_percentage_denominators(self):
        _, p = self.product()
        self.assertAlmostEqual(p.metrics['light_pct_actual_sleep'].value, 100*4000/8500)
        self.assertAlmostEqual(p.metrics['deep_pct_actual_sleep'].value, 100*2000/8500)
        self.assertAlmostEqual(p.metrics['rem_pct_actual_sleep'].value, 100*2500/8500)
        self.assertAlmostEqual(p.metrics['restorative_pct_actual_sleep'].value, 100*4500/8500)
        self.assertEqual(p.metrics['awake_pct_in_bed'].value, 10)

    def test_missing_and_zero_operands(self):
        row = self.source['sleeps'][0]
        row['total_rem_sleep_time_ms'] = None
        m = measurements(row)
        self.assertIsNone(m['actual_sleep_ms'].value)
        self.assertIsNone(m['light_pct_actual_sleep'].value)
        row.update(total_rem_sleep_time_ms=0, total_light_sleep_time_ms=0, total_slow_wave_sleep_time_ms=0,
                   total_in_bed_time_ms=0, recent_nap_need_ms=None)
        m = measurements(row)
        self.assertEqual(m['actual_sleep_ms'].value, 0)
        self.assertIsNone(m['rem_pct_actual_sleep'].value)
        self.assertIsNone(m['awake_pct_in_bed'].value)
        self.assertIsNone(m['total_need_ms'].value)

    def test_naps_keep_independent_details_and_do_not_select_morning(self):
        nap = dict(self.source['sleeps'][0], sleep_id='synthetic-nap', nap='true', total_rem_sleep_time_ms=1200)
        self.source['sleeps'].append(nap)
        r, p = self.product()
        self.assertEqual(r.selection['sleep']['nap'], 'false')
        self.assertEqual(p.metrics['rem_ms'].value, 2500)
        self.assertEqual(p.naps[0]['metrics']['rem_ms'].value, 1200)
        self.assertEqual(len(p.trends['rem_ms']['daily']), 1)

    def test_selection_is_not_recomputed_and_ambiguity_withholds(self):
        r = report(self.source)
        with patch.object(daily, 'select_report_morning', side_effect=AssertionError('No second selector')):
            p = compose_sleep_product(r, self.source['sleeps'])
        self.assertEqual(p.report_date, D)
        self.source['sleeps'].append(dict(self.source['sleeps'][0], sleep_id='other'))
        _, p = self.product()
        self.assertEqual(p.status, 'ambiguous')
        self.assertTrue(all(m.value is None for m in p.metrics.values()))

    def test_pending_latest_stays_latest(self):
        self.source['sleeps'][0]['score_state'] = 'PENDING_SCORE'
        _, p = self.product()
        self.assertEqual(p.report_date, D)
        self.assertIsNone(p.metrics['actual_sleep_ms'].value)
        self.assertIn('sleep_not_scored', p.quality)

    def test_metric_conflict_and_structural_conflict(self):
        r, p = self.product(physiology=history()+[observation(D, sleep_performance=1)])
        self.assertIsNone(p.metrics['performance_pct'].value)
        self.assertEqual(p.metrics['actual_sleep_ms'].value, 8500)
        wrong = observation(D, sleep_performance=80)
        wrong['report_date'] = D-timedelta(days=1)
        _, p = self.product(physiology=[wrong])
        self.assertTrue(all(m.value is None for m in p.metrics.values()))
        self.assertIn('source_snapshot_conflict', p.quality)

    def test_trends_exclude_current_missing_dates_naps_and_preserve_offsets(self):
        for gap, light, offset in ((1, 1000, '+01:00'), (3, 3000, '+08:00'), (8, 5000, '+01:00')):
            row = entities(D-timedelta(days=gap))['sleeps'][0]
            row.update(EXPECTED)
            row.update(total_light_sleep_time_ms=light, timezone_offset=offset)
            self.source['sleeps'].append(row)
        _, p = self.product()
        trend = p.trends['actual_sleep_ms']
        self.assertEqual(trend['windows'][7]['days_available'], 2)
        self.assertEqual(trend['windows'][7]['average'], 6500)
        self.assertEqual(trend['windows'][14]['days_available'], 3)
        self.assertEqual(trend['windows'][30]['days_available'], 3)
        self.assertEqual({point['recorded_offset'] for point in trend['daily']}, {'+01:00', '+08:00'})

    def test_ambiguous_historical_dates_excluded_and_missing_values_not_zero(self):
        row = entities(D-timedelta(days=1))['sleeps'][0]
        row.update(EXPECTED)
        self.source['sleeps'] += [row, dict(row, sleep_id='other-past')]
        row = entities(D-timedelta(days=2))['sleeps'][0]
        row.update(EXPECTED)
        row['respiratory_rate'] = None
        self.source['sleeps'].append(row)
        _, p = self.product()
        self.assertEqual(p.trends['respiratory_rate']['windows'][7]['days_available'], 0)
        self.assertEqual(p.trends['deep_ms']['windows'][7]['days_available'], 1)
        self.assertIn('ambiguous_history_date', p.quality)

    def test_freshness_current_one_day_multiple_days_and_absent(self):
        for gap in (0, 1, 3):
            f = freshness(D-timedelta(days=gap), D)
            self.assertEqual(f['days_since_latest_available_morning'], gap)
            self.assertEqual(f['may_be_out_of_date'], gap>0)
            self.assertFalse(f['collection_time_known'])
        self.assertIsNone(freshness(None, D)['days_since_latest_available_morning'])
        self.assertFalse(freshness(None, D)['may_be_out_of_date'])

    def test_performance_reuses_report_duplicate_date_means_counts_and_bounds(self):
        past = D-timedelta(days=1)
        a = entities(past)['sleeps'][0]
        b = dict(a, sleep_id='second-past', cycle_id='second-cycle', sleep_performance=100)
        a['sleep_performance'] = 60
        self.source['sleeps'] += [a,b]
        observations = [observation(past, sleep_performance=60),
                        dict(observation(past, sleep_performance=100),
                             sleep_id=b['sleep_id'], cycle_id=b['cycle_id'])]
        r, p = self.product(physiology=observations)
        for window in (7,14,30):
            trend = p.trends['performance_pct']['windows'][window]
            baseline = r.baseline['sleep_performance'][window]
            self.assertEqual(trend, baseline)
            self.assertEqual(trend['average'],80)
            self.assertEqual(trend['days_available'],1)
            self.assertEqual(trend['window_start'],D-timedelta(days=window))
            self.assertEqual(trend['window_end'],D-timedelta(days=1))
        self.assertEqual(p.trends['performance_pct']['daily'][0]['value'],80)


if __name__ == '__main__':
    unittest.main()
