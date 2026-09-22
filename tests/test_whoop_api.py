"""Synthetic API contracts; in-memory ASGI, no WHOOP/network/credentials."""

from dataclasses import asdict
import json
import unittest
from unittest.mock import Mock
from typing import get_args
from pydantic import ValidationError
from api.schemas import TodayResponse
from whoop_product.models import SignalState, OverallState, Availability, Origin, Coverage, SleepStatus

from api.app import create_app
from product_fixtures import SyntheticInputs, D, rewrite
from asgi_transport import request
from whoop_product.today_service import get_today
from whoop_product.sleep_service import get_sleep


class APITests(SyntheticInputs,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_inputs()
        self.app=create_app(self.repo)

    async def test_health_no_snapshot_access_or_metadata(self):
        repository=Mock()
        status,data,_=await request(create_app(repository),'/api/v1/health')
        self.assertEqual((status,data),(200,{'status':'ok','schema_version':'v1'}))
        repository.read_snapshot.assert_not_called()

    async def test_today_sleep_match_services_and_share_snapshot(self):
        status,today,headers=await request(self.app,'/api/v1/today')
        sleep_status,sleep,_=await request(self.app,'/api/v1/sleep/latest')
        self.assertEqual((status,sleep_status),(200,200))
        self.assertEqual(today['report_date'],str(D))
        self.assertEqual(today['snapshot_id'],sleep['snapshot_id'])
        self.assertEqual(today['data']['physiology']['hrv_ms']['metric']['value'],get_today(self.repo).data.physiology['hrv_ms'].metric.value)
        self.assertEqual(sleep['data']['metrics']['actual_sleep_ms']['value'],get_sleep(self.repo).data.metrics['actual_sleep_ms'].value)
        self.assertIsNone(today['last_successful_sync_at'])
        self.assertEqual(headers[b'cache-control'],b'no-store')

    async def test_trend_endpoint_valid_windows_and_history_anchor(self):
        for window in (7,14,30):
            status,data,_=await request(self.app,f'/api/v1/trends?metric=sleep_performance&window_days={window}')
            self.assertEqual(status,200)
            self.assertEqual(data['data']['prior_windows'][0]['window_days'],window)
            self.assertFalse(data['data']['prior_windows'][0]['includes_anchor'])
        status,data,_=await request(self.app,'/api/v1/trends?metric=actual_sleep&anchor_date=2026-09-03')
        self.assertEqual(status,200)
        self.assertEqual(data['report_date'],'2026-09-03')

    async def test_invalid_queries_sanitized(self):
        for query in ('metric=private-secret','metric=actual_sleep&anchor_date=invalid-secret',
                      'metric=actual_sleep&window_days=8','metric=actual_sleep&anchor_date=2026-09-08',''):
            status,data,_=await request(self.app,'/api/v1/trends?'+query)
            self.assertEqual(status,422)
            self.assertEqual(set(data),{'code','message','retryable','request_id'})
            self.assertNotIn('secret',json.dumps(data))

    async def test_internal_failure_is_sanitized_503(self):
        repository=Mock()
        repository.read_snapshot.side_effect=RuntimeError('private path / synthetic-secret')
        status,data,_=await request(create_app(repository),'/api/v1/today')
        self.assertEqual(status,503)
        self.assertTrue(data['retryable'])
        self.assertNotIn('synthetic-secret',json.dumps(data))
        self.assertNotIn('private path',json.dumps(data))

    async def test_no_write_methods_sync_or_arbitrary_routes(self):
        for method in ('POST','PUT','PATCH','DELETE','OPTIONS','HEAD'):
            self.assertEqual((await request(self.app,'/api/v1/today',method))[0],405)
        for path in ('/sync','/.env','/data/sleeps.csv','/../whoop_tokens.json'):
            self.assertEqual((await request(self.app,path))[0],404)

    async def test_host_and_cross_site_protection(self):
        for headers in ([(b'host',b'evil.example')],[(b'origin',b'null')],[(b'origin',b'http://evil.example')],[(b'sec-fetch-site',b'cross-site')]):
            self.assertEqual((await request(self.app,'/api/v1/today',headers=headers))[0],403)

    async def test_privacy_read_only_and_finite_json(self):
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        for path in ('/api/v1/today','/api/v1/sleep/latest','/api/v1/trends?metric=actual_sleep'):
            status,data,_=await request(self.app,path)
            self.assertEqual(status,200)
            rendered=json.dumps(data,allow_nan=False)
            for forbidden in ('synthetic-sleep','sleep_id','cycle_id','workout_id','access_token','refresh_token',str(self.root)):
                self.assertNotIn(forbidden,rendered)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})

    async def test_unavailable_data_is_200_not_server_error(self):
        from whoop_sleep import EXTENDED_FIELDS
        rewrite(self.root,'sleeps',lambda rows:rows[0].update(score_state='PENDING_SCORE',**{f:'' for f in EXTENDED_FIELDS}))
        status,data,_=await request(self.app,'/api/v1/sleep/latest')
        self.assertEqual(status,200)
        self.assertEqual(data['data']['metrics']['actual_sleep_ms']['availability'],'pending')
        self.assertIsNone(data['data']['metrics']['actual_sleep_ms']['value'])

    async def test_public_finite_states_are_typed_and_serialize_as_strings(self):
        status, data, _ = await request(self.app, '/api/v1/today')
        self.assertEqual(status, 200)
        definitions = TodayResponse.model_json_schema()['$defs']
        for model, field, states in (
                ('Signal', 'state', SignalState), ('Interpretation', 'overall_state', OverallState),
                ('Metric', 'availability', Availability), ('Metric', 'origin', Origin),
                ('Baseline', 'coverage_status', Coverage), ('SleepSummary', 'status', SleepStatus)):
            self.assertEqual(set(definitions[model]['properties'][field]['enum']), set(get_args(states)))
        interpretation = data['data']['interpretation']
        for overall in get_args(OverallState):
            for signal in get_args(SignalState):
                interpretation['overall_state'] = overall
                interpretation['signals']['hrv_ms']['state'] = signal
                serialized = TodayResponse.model_validate(data).model_dump(mode='json')
                self.assertEqual(serialized['data']['interpretation']['overall_state'], overall)
                self.assertEqual(serialized['data']['interpretation']['signals']['hrv_ms']['state'], signal)
        interpretation['overall_state'] = 'invented state'
        with self.assertRaises(ValidationError):
            TodayResponse.model_validate(data)
