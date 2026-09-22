"""Frozen V1 contract: synthetic archives, no live credentials or WHOOP calls."""

from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError
from api.app import create_app
from api.schemas import TodayResponse, SleepResponse, TrendResponse
from asgi_transport import request
from product_fixtures import SyntheticInputs, D, NOW, rewrite
import whoop_analysis as analysis
import whoop_daily_report as daily
import whoop_dashboard as dashboard
from whoop_product.common import metric, quality_codes
from whoop_product.repository import SnapshotUnavailable
from whoop_product.today_service import get_today
from whoop_product.sleep_service import get_sleep
from whoop_product.trend_service import get_trend
from test_whoop_daily_report import entities, history, report
from test_whoop_workout_analysis import record
from whoop_workouts import save_workouts


OPERATIONS = {
    '/api/v1/health': ('get_health', 'Health'),
    '/api/v1/today': ('get_today', 'TodayResponse'),
    '/api/v1/sleep/latest': ('get_latest_sleep', 'SleepResponse'),
    '/api/v1/trends': ('get_trends', 'TrendResponse'),
}
TREND_NAMES = ['actual_sleep', 'sleep_need', 'sleep_performance', 'sleep_efficiency',
               'sleep_consistency', 'respiratory_rate', 'deep_sleep', 'rem_sleep', 'restorative_sleep']
SCHEMAS = {'ActivitySummary', 'Baseline', 'Error', 'Freshness', 'Health', 'Interpretation',
           'InterpretationSignals', 'Metric', 'Nap', 'Physiology', 'Signal', 'SleepMetrics',
           'SleepReport', 'SleepResponse', 'SleepSummary', 'SleepTrends', 'Timing', 'TodayPhysiology',
           'TodayReport', 'TodayResponse', 'TrainingSummary', 'TrendPoint', 'TrendReport', 'TrendResponse'}
ENUMS = {
    ('Metric', 'availability'): ['available', 'missing', 'pending', 'withheld'],
    ('Metric', 'origin'): ['whoop', 'derived'],
    ('Metric', 'unit'): ['ms', 'percent', 'bpm', 'breaths/min', 'count'],
    ('Signal', 'state'): ['positive', 'negative', 'neutral', 'insufficient data'],
    ('Interpretation', 'overall_state'): ['strong positive', 'strong negative', 'generally positive',
                                        'generally negative', 'mixed', 'insufficient data'],
    ('Baseline', 'coverage_status'): ['full', 'partial', 'insufficient', 'unavailable'],
    ('Baseline', 'window_days'): [7, 14, 30],
    ('SleepReport', 'status'): ['available', 'partial', 'unavailable', 'ambiguous'],
    ('SleepSummary', 'status'): ['available', 'partial', 'unavailable', 'ambiguous'],
    ('TrainingSummary', 'availability'): ['available', 'missing'],
    ('TrendReport', 'metric'): TREND_NAMES,
    ('Error', 'code'): ['local_access_only', 'not_found', 'read_only', 'invalid_query',
                      'internal_error', 'snapshot_unavailable'],
}
QUALITY_MAPPING = {
    'missing_primary_sleep': 'primary_sleep_unavailable',
    'source_snapshot_conflict': 'conflicting_measurements',
    'missing_matching_daily_snapshot': 'measurements_unverified',
    'daily_snapshot_unverified': 'measurements_unverified',
    'missing_workout_dataset': 'training_data_unavailable',
    'newer_incomplete_primary': 'newer_sleep_incomplete',
    'incomplete_order_unknown': 'sleep_chronology_uncertain',
    'duration_accounting_difference': 'sleep_duration_inconsistent',
    'invalid_history_interval': 'sleep_history_incomplete',
    'ambiguous_history_date': 'ambiguous_sleep_history',
    **{name: name for name in ('ambiguous_primary_sleep', 'ambiguous_physiology',
       'recent_offset_transition', 'mixed_offset_date', 'baseline_offset_transition',
       'sleep_not_scored', 'missing_sleep_components', 'unrecorded_duration_present')},
}


def example_payloads(repo):
    """Complete golden payloads; only the platform-dependent input fingerprint is normalized."""
    payloads = {
        'today': TodayResponse.model_validate(asdict(get_today(repo))).model_dump(mode='json'),
        'sleep': SleepResponse.model_validate(asdict(get_sleep(repo))).model_dump(mode='json'),
        'trends': TrendResponse.model_validate(asdict(get_trend(repo, 'actual_sleep', window_days=7))).model_dump(mode='json'),
    }
    for payload in payloads.values():
        payload['snapshot_id'] = '0' * 64
    return payloads


class ContractTests(SyntheticInputs, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_inputs()
        self.app = create_app(self.repo)

    def test_openapi_exact_components_operations_and_error_responses(self):
        schema = self.app.openapi()
        self.assertEqual(schema['openapi'], '3.1.0')
        self.assertEqual(set(schema['components']['schemas']), SCHEMAS)
        self.assertEqual(set(schema['paths']), set(OPERATIONS))
        self.assertEqual({r.path: r.methods for r in self.app.routes}, {p: {'GET'} for p in OPERATIONS})
        for path, (operation, response) in OPERATIONS.items():
            self.assertEqual(set(schema['paths'][path]), {'get'})
            endpoint = schema['paths'][path]['get']
            self.assertEqual(endpoint['operationId'], operation)
            self.assertEqual(endpoint['responses']['200']['content']['application/json']['schema'],
                             {'$ref': '#/components/schemas/' + response})
            for status in ('403', '404', '405', '422', '500', '503'):
                self.assertEqual(endpoint['responses'][status]['content']['application/json']['schema'],
                                 {'$ref': '#/components/schemas/Error'})

    def test_openapi_exact_enums_units_constants_and_required_keys(self):
        schemas = self.app.openapi()['components']['schemas']
        for (model, name), values in ENUMS.items():
            self.assertEqual(schemas[model]['properties'][name]['enum'], values)
        for model, name, value in [('Health', 'status', 'ok'), ('Health', 'schema_version', 'v1'),
                                    ('Freshness', 'status', 'unknown'),
                                    ('Freshness', 'collection_time_known', False),
                                    ('TrainingSummary', 'calendar_coverage', 'unknown'),
                                    ('Interpretation', 'window_days', 14), ('Baseline', 'includes_anchor', False)]:
            self.assertEqual(schemas[model]['properties'][name]['const'], value)
        for name, schema in schemas.items():
            self.assertEqual(set(schema['properties']), set(schema['required']), name)
            for prop in schema['properties'].values():
                self.assertNotIsInstance(prop.get('additionalProperties'), dict, name)
        params = {p['name']: p for p in self.app.openapi()['paths']['/api/v1/trends']['get']['parameters']}
        self.assertEqual(params['metric']['schema']['enum'], TREND_NAMES)
        self.assertEqual(params['window_days']['schema']['enum'], ['7', '14', '30'])
        self.assertEqual(params['window_days']['schema']['default'], '14')
        self.assertTrue(params['metric']['required'])

    def test_dates_nullability_and_complete_golden_responses(self):
        schemas = self.app.openapi()['components']['schemas']
        for name in ('TodayResponse', 'SleepResponse', 'TrendResponse'):
            props = schemas[name]['properties']
            self.assertEqual(props['generated_at']['format'], 'date-time')
            for field, fmt in [('report_date', 'date'), ('latest_available_morning', 'date'),
                               ('last_successful_sync_at', 'date-time')]:
                self.assertEqual(props[field]['anyOf'], [{'type': 'string', 'format': fmt}, {'type': 'null'}])
        self.assertEqual(schemas['TrendPoint']['properties']['report_date']['format'], 'date')
        actual = example_payloads(self.repo)
        expected = json.loads((Path(__file__).parent / 'fixtures' / 'api_v1_examples.json').read_text(encoding='utf-8'))
        self.assertEqual(actual, expected)
        for schema, key in ((TodayResponse, 'today'), (SleepResponse, 'sleep'), (TrendResponse, 'trends')):
            valid = actual[key]
            for name in valid:
                with self.subTest(response=key, omitted=name), self.assertRaises(ValidationError):
                    schema.model_validate({k: v for k, v in valid.items() if k != name})

    def test_metric_zero_null_and_nonavailable_invariants(self):
        self.assertEqual(asdict(metric(0, 'percent', 'whoop')),
                         dict(value=0, unit='percent', origin='whoop', availability='available', reason_codes=[]))
        for kwargs, expected in (({}, 'missing'), ({'pending': True}, 'pending'), ({'withheld': True}, 'withheld')):
            m = metric(None, 'ms', 'derived', reasons=('sleep_component_unavailable',), **kwargs)
            self.assertIsNone(m.value)
            self.assertEqual(m.availability, expected)
        payload = example_payloads(self.repo)['today']
        target = payload['data']['physiology']['hrv_ms']['metric']
        for availability, value in [('missing', 0), ('pending', 1), ('withheld', 2), ('available', None)]:
            target.update(availability=availability, value=value)
            with self.assertRaises(ValidationError):
                TodayResponse.model_validate(payload)

    def test_internal_fields_and_quality_do_not_expand_public_contract(self):
        snapshot = self.repo.read_snapshot()
        before = example_payloads(Mock(read_snapshot=Mock(return_value=snapshot)))
        report = snapshot.load_daily_report()
        report.physiology['internal_csv_schema'] = {'secret': 'synthetic-secret'}
        report.interpretation['signals']['private_cycle_id'] = {'state': 'negative'}
        report.interpretation['reason_codes'].append('internal_only')
        report.interpretation['signals']['hrv_ms']['reason_codes'].append('internal_only')
        report.data_quality.append({'code': 'internal_only', 'value': True})
        report.sleep_product.quality.append('internal_only')
        report.sleep_product.metrics['internal_path'] = report.sleep_product.metrics['in_bed_ms']
        report.sleep_product.provenance['access_token'] = 'synthetic-secret'
        after = example_payloads(Mock(read_snapshot=Mock(return_value=snapshot)))
        self.assertEqual(before, after)

    def test_quality_mapping_and_internal_duplicates_are_filtered(self):
        for internal, public in QUALITY_MAPPING.items():
            self.assertEqual(quality_codes([internal]), [public])
        internal = ['baseline_hrv_ms', 'missing_or_unscored_hrv_ms', 'unfinished_cycle',
                    'unknown_workout_coverage', 'source_freshness_limitation',
                    'chronology_or_association_issue', 'current_identity_excluded_from_baseline',
                    'missing_classification_sidecar', 'future_private_flag']
        self.assertEqual(quality_codes(internal), [])
        self.assertEqual(quality_codes(['daily_snapshot_unverified', 'missing_matching_daily_snapshot']),
                         ['measurements_unverified'])
        snapshot = self.repo.read_snapshot()
        snapshot.load_sleep_product().quality.extend(['newer_incomplete_primary', 'incomplete_order_unknown'])
        today = get_today(Mock(read_snapshot=Mock(return_value=snapshot)))
        self.assertIn('newer_sleep_incomplete', today.data.quality_codes)
        self.assertIn('sleep_chronology_uncertain', today.data.quality_codes)

    async def test_naps_are_lightweight_bounded_by_recorded_end_date(self):
        def add(rows):
            original = rows[-1]
            for i, gap in enumerate((1, 60, 600, -1)):
                day = D - timedelta(days=gap)
                rows.append(dict(original, sleep_id=f'synthetic-old-nap-{i}',
                                 start=f'{day}T10:00:00Z', end=f'{day}T10:30:00Z'))
            # UTC yesterday but local end is report date; must be included.
            rows.append(dict(original, sleep_id='synthetic-midnight-nap',
                             start=f'{D-timedelta(days=1)}T23:00:00Z',
                             end=f'{D-timedelta(days=1)}T23:30:00Z'))
        rewrite(self.root, 'sleeps', add)
        status, response, _ = await request(self.app, '/api/v1/sleep/latest')
        self.assertEqual(status, 200)
        naps = response['data']['naps']
        self.assertEqual(len(naps), 2)
        for nap in naps:
            self.assertEqual(set(nap), {'timing', 'actual_sleep'})
            self.assertTrue(nap['timing']['local_end'].startswith(str(D)))
        self.assertGreater(naps[0]['timing']['local_end'], naps[1]['timing']['local_end'])

    def test_training_available_empty_and_missing_are_distinct(self):
        empty = get_today(self.repo).data.yesterday_training
        self.assertEqual((empty.availability, empty.record_count, empty.observed_dates), ('available', 0, 0))
        self.assertEqual(empty.calendar_coverage, 'unknown')
        self.assertIsNone(empty.duration_ms)
        row = record(day=str(D-timedelta(days=1)))
        save_workouts({row['workout_id']: row}, self.root)
        present = get_today(self.repo).data.yesterday_training
        self.assertEqual((present.availability, present.record_count), ('available', 1))
        self.assertEqual(present.duration_ms, 3600000)
        self.assertEqual(present.calendar_coverage, 'unknown')
        (self.root / 'workouts.csv').unlink()
        today = get_today(self.repo)
        missing = today.data.yesterday_training
        self.assertEqual(missing.availability, 'missing')
        self.assertIsNone(missing.record_count)
        self.assertIsNone(missing.observed_dates)
        self.assertIn('training_data_unavailable', today.data.quality_codes)

    async def test_strict_query_rejection_never_reads_snapshot(self):
        repository = Mock()
        app = create_app(repository)
        queries = ['metric=unknown', '', 'metric=actual_sleep&window_days=8',
                   'metric=actual_sleep&window_days=07', 'metric=actual_sleep&unexpected=x',
                   'metric=actual_sleep&metric=actual_sleep',
                   'metric=actual_sleep&anchor_date=2026-09-10&anchor_date=2026-09-10',
                   'metric=actual_sleep&window_days=7&window_days=14']
        queries += ['metric=actual_sleep&anchor_date=' + v for v in
                    ('2026-09-10T00:00:00', '1788998400', 'null', '2026-9-10', '2026-02-30', '', '20260910')]
        for query in queries:
            status, body, headers = await request(app, '/api/v1/trends?' + query)
            self.assertEqual(status, 422, query)
            self.assertEqual(body['code'], 'invalid_query')
            self.assertEqual(body['request_id'], headers[b'x-request-id'].decode())
        repository.read_snapshot.assert_not_called()

    async def test_trend_windows_defaults_gaps_and_unavailable_points(self):
        from whoop_sleep import EXTENDED_FIELDS
        rewrite(self.root, 'sleeps', lambda rows: rows[1].update(
            score_state='PENDING_SCORE', **{f: '' for f in EXTENDED_FIELDS}))
        for window in (7, 14, 30):
            status, response, _ = await request(self.app, f'/api/v1/trends?metric=actual_sleep&window_days={window}')
            self.assertEqual(status, 200)
            data = response['data']
            self.assertEqual(data['window_days'], window)
            self.assertEqual(len(data['prior_windows']), 1)
            self.assertFalse(data['prior_windows'][0]['includes_anchor'])
            dates = [date.fromisoformat(p['report_date']) for p in data['daily_points']]
            self.assertTrue(all(D-timedelta(days=window) <= d <= D for d in dates))
            self.assertNotIn(D-timedelta(days=2), dates)
            pending = next(p['metric'] for p in data['daily_points'] if p['report_date'] == str(D-timedelta(days=1)))
            self.assertEqual((pending['value'], pending['availability'], pending['reason_codes']),
                             (None, 'pending', ['sleep_not_scored']))
        self.assertEqual((await request(self.app, '/api/v1/trends?metric=actual_sleep'))[1]['data']['window_days'], 14)
        status, _, _ = await request(self.app, '/api/v1/trends?metric=actual_sleep&anchor_date=2026-09-08')
        self.assertEqual(status, 422)

    async def test_all_error_categories_match_schema_and_request_header(self):
        from api.schemas import Error
        for exception, status, code, retryable in (
                (SnapshotUnavailable('synthetic-secret'), 503, 'snapshot_unavailable', True),
                (RuntimeError('synthetic-secret'), 500, 'internal_error', False)):
            repository = Mock()
            repository.read_snapshot.side_effect = exception
            actual, body, headers = await request(create_app(repository), '/api/v1/today')
            self.assertEqual((actual, body['code'], body['retryable']), (status, code, retryable))
            self.assertEqual(body['request_id'], headers[b'x-request-id'].decode())
            self.assertNotIn('synthetic-secret', json.dumps(body))
            self.assertEqual(set(body), set(Error.model_fields))
            Error.model_validate(body)
        for path, method, headers, status, code in (
                ('/api/v1/today', 'GET', [(b'origin', b'null')], 403, 'local_access_only'),
                ('/unknown', 'GET', (), 404, 'not_found'),
                ('/api/v1/health', 'POST', (), 405, 'read_only'),
                ('/api/v1/trends', 'GET', (), 422, 'invalid_query')):
            actual, body, response_headers = await request(self.app, path, method, headers)
            self.assertEqual((actual, body['code']), (status, code))
            self.assertEqual(body['request_id'], response_headers[b'x-request-id'].decode())
            Error.model_validate(body)

    async def test_repository_programming_error_is_not_disguised_as_snapshot_failure(self):
        with patch.object(daily, 'load_daily_report', side_effect=RuntimeError('synthetic-private-path')):
            status, body, _ = await request(self.app, '/api/v1/today')
        self.assertEqual((status, body['code']), (500, 'internal_error'))
        self.assertNotIn('synthetic-private-path', json.dumps(body))
        (self.root / '.sync.lock').unlink()
        status, body, _ = await request(self.app, '/api/v1/today')
        self.assertEqual((status, body['code']), (503, 'snapshot_unavailable'))

    async def test_default_responses_have_no_internal_material(self):
        for path in ('/api/v1/today', '/api/v1/sleep/latest', '/api/v1/trends?metric=sleep_performance'):
            status, body, _ = await request(self.app, path)
            self.assertEqual(status, 200)
            text = json.dumps(body, allow_nan=False)
            for forbidden in ('sleep_id', 'cycle_id', 'workout_id', 'provenance', '.csv', '.env',
                              'access_token', 'refresh_token', 'client_secret', 'synthetic-sleep', str(self.root),
                              'missing_classification_sidecar', 'missing_matching_daily_snapshot'):
                self.assertNotIn(forbidden, text)


class InterpretationContractTests(unittest.TestCase):
    def test_signal_reason_branches_and_direction(self):
        for value, average, count, expected, state in (
                (None, 50, 14, 'latest_value_unavailable', 'insufficient data'),
                (60, 50, 6, 'insufficient_baseline', 'insufficient data'),
                (60, 0, 14, 'comparison_undefined', 'insufficient data'),
                (60, 50, 14, 'above_baseline', 'positive'),
                (40, 50, 14, 'below_baseline', 'negative'),
                (51, 50, 14, 'near_baseline', 'neutral')):
            entry = {'value': value, 'baselines': {14: dict(average=average, days_available=count,
                                                           **analysis.compare(value, average))}}
            result = analysis.classify_signal('hrv_ms', entry)
            self.assertEqual((result['state'], result['reason_codes']), (state, [expected]))
        entry = {'value': 50, 'baselines': {14: dict(average=55, days_available=14, **analysis.compare(50, 55))}}
        result = analysis.classify_signal('resting_heart_rate_bpm', entry)
        self.assertEqual((result['state'], result['reason_codes']), ('positive', ['below_baseline']))

    def test_overall_reasons_and_existing_dashboard_cli_prose_unchanged(self):
        scenarios = [
            (entities(), history(), 'conflicting_signals'),
            (entities(), [], 'insufficient_signal_coverage'),
            (entities(), [], 'signals_unavailable'),
        ]
        neutral = entities()
        neutral['recoveries'][0].update(hrv_ms=50, resting_heart_rate_bpm=50, recovery_score=60)
        neutral['sleeps'][0]['sleep_performance'] = 75
        scenarios.append((neutral, history(), 'no_directional_signal'))
        for source, observations, expected in scenarios:
            r = report(source=source, physiology=observations)
            from whoop_sleep_product import compose_sleep_product
            r.sleep_product = compose_sleep_product(r, source['sleeps'], today=D)
            self.assertIn(expected, r.interpretation['reason_codes'])
            rendered, cli = dashboard.render_report(r), daily.format_daily_report(r)
            r.interpretation.pop('reason_codes')
            r.interpretation.pop('window_days')
            for signal in r.interpretation['signals'].values():
                signal.pop('reason_codes')
            self.assertEqual(dashboard.render_report(r), rendered)
            self.assertEqual(daily.format_daily_report(r), cli)
