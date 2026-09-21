"""Synthetic fixtures only. HTTP checks stay on loopback; no WHOOP requests."""

import contextlib
import csv
from datetime import timedelta
from http.client import HTTPConnection
import io
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests
import whoop_dashboard as web
import whoop_daily_report as daily
from whoop_entities import SCHEMAS, read_entities
from whoop_sleep import EXTENDED_FIELDS
from whoop_activity import classify_workouts
from test_whoop_daily_report import D, entities, history, report
from test_whoop_workout_analysis import record
from test_whoop_sleep import EXPECTED


def write_inputs(root, extended=False):
    source = entities()
    if extended:
        source['sleeps'][0].update(EXPECTED)
    for kind, rows in {**source, 'daily_metrics': history()}.items():
        with (root / (kind + '.csv')).open('w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=SCHEMAS[kind])
            writer.writeheader()
            writer.writerows(rows)


class OfflineDashboardTestCase(unittest.TestCase):
    def setUp(self):
        for target in ('whoop_fetch.WhoopClient', 'whoop_auth.load_tokens',
                       'whoop_auth.read_config', 'whoop_auth.refresh_tokens'):
            self.enterContext(patch(target, side_effect=AssertionError('WHOOP/credentials forbidden')))
        self.enterContext(patch.object(requests.Session, 'request', side_effect=AssertionError('HTTP forbidden')))
        original_open = Path.open
        project = Path(web.__file__).resolve().parent

        def synthetic_inputs_only(path, *args, **kwargs):
            resolved = path.resolve()
            if resolved.is_relative_to(project / 'data') or (
                    resolved.parent == project and (resolved.name.startswith('.env') or 'token' in resolved.name.lower())):
                raise AssertionError('Private project input forbidden')
            return original_open(path, *args, **kwargs)

        self.enterContext(patch.object(Path, 'open', synthetic_inputs_only))
        original_connect = socket.socket.connect

        def loopback_only(sock, address):
            if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1'):
                raise AssertionError('Only loopback connections allowed')
            return original_connect(sock, address)

        self.enterContext(patch.object(socket.socket, 'connect', loopback_only))


class DashboardTests(OfflineDashboardTestCase):
    def test_populated_extended_archive_renders_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_inputs(root, extended=True)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with (root / 'sleeps.csv').open(newline='', encoding='utf-8') as file:
                self.assertEqual(next(csv.reader(file)), list(SCHEMAS['sleeps']))
            loaded = next(iter(read_entities(root / 'sleeps.csv', 'sleeps').values()))
            self.assertEqual({field: loaded[field] for field in EXTENDED_FIELDS}, EXPECTED)
            report = web.load_snapshot(root)
            self.assertEqual(report.selection['sleep'], loaded)
            html = web.render_report(report)
            for label in ('Recovery', 'HRV', 'Resting HR', 'Sleep Performance', 'Last Sleep'):
                self.assertIn(label, html)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})

    def test_default_data_root_and_loopback_launch_independent_of_cwd(self):
        expected = Path(__file__).resolve().parents[1] / 'data'
        self.assertEqual(web.DATA_DIR, expected)
        self.assertEqual(daily.DATA_DIR, expected)
        self.assertIs(web.load_daily_report, daily.load_daily_report)
        self.assertEqual(web.CSS, expected.parent / 'dashboard.css')
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with patch.object(web, 'LocalServer') as server, patch.object(web, 'make_handler') as handler, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(web.main([]), 0)
                handler.assert_called_once_with(expected)
                server.assert_called_once_with(('127.0.0.1', 8501), handler.return_value)
                server.return_value.__enter__.return_value.serve_forever.assert_called_once_with()
            finally:
                os.chdir(previous)

    def test_report_values_state_sleep_and_training_parity(self):
        r = report(workouts=classify_workouts([record(day=str(D - timedelta(days=1)))]))
        html = web.render_report(r)
        for metric in web.METRICS:
            self.assertIn(web.number(r.physiology[metric]['value']), html)
            self.assertIn(web.number(r.baseline[metric][14]['average']), html)
        self.assertIn(str(r.report_date), html)
        self.assertIn(r.interpretation['overall_state'].capitalize(), html)
        self.assertIn('8h 00m', html)
        self.assertIn('1 WHOOP records', html)
        self.assertIn('Badminton Z4+5', html)
        self.assertIn('60.0', html)
        for hidden in (r.provenance['sleep_id'], r.provenance['cycle_id'], r.yesterday_training['workout_ids'][0]):
            self.assertNotIn(hidden, html)
        self.assertNotIn('TODAY', html)

    def test_pending_zero_and_insufficient_baselines(self):
        source = entities(score_state='PENDING_SCORE')
        source['recoveries'][0]['score_state'] = 'PENDING_SCORE'
        html = web.render_report(report(source, physiology=[]))
        self.assertIn('Current value is missing or not scored', html)
        self.assertIn('Insufficient baseline', html)
        source = entities()
        source['recoveries'][0]['hrv_ms'] = 0
        r = report(source, history(hrv_ms=0))
        card = web.metric_card(r, 'hrv_ms', 'HRV')
        self.assertIn('0.0<span', card)
        self.assertIn('Deviation unavailable', card)

    def test_conflict_and_ambiguous_and_missing_date(self):
        from test_whoop_daily_report import observation
        r = report(physiology=history() + [observation(D, hrv_ms=999)])
        self.assertIn('Source snapshots conflict', web.render_report(r))
        source = entities()
        source['sleeps'].append({**source['sleeps'][0], 'sleep_id': 'synthetic-other'})
        html = web.render_report(report(source))
        self.assertIn('Ambiguous candidate interval', html)
        self.assertIn('Current physiology is withheld', html)
        html = web.render_report(report(entities(nap='true')))
        self.assertIn('No completed primary sleep', html)
        self.assertIn('Unavailable without a primary-sleep report date', html)

    def test_escaping_no_provenance_or_external_assets(self):
        row = record(label='<script>alert("secret")</script>', day=str(D - timedelta(days=1)))
        r = report(workouts=classify_workouts([row]))
        html = web.render_report(r)
        self.assertNotIn('<script', html.lower())
        self.assertIn('&lt;Script&gt;', html)
        self.assertNotIn(row['workout_id'], html)
        self.assertNotIn('https://', html)
        self.assertNotIn('http://', html)

    def test_loader_cli_parity_offline_readonly_and_optional_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_inputs(root)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            original_open = Path.open
            def readonly(path, mode='r', *args, **kwargs):
                self.assertFalse(any(c in mode for c in 'wax+'))
                self.assertNotIn(path.name, ('.env', 'whoop_tokens.json'))
                return original_open(path, mode, *args, **kwargs)
            output = io.StringIO()
            with patch.object(Path, 'open', readonly), \
                    patch.object(socket, 'socket', side_effect=AssertionError('Network')), \
                    patch('whoop_fetch.WhoopClient', side_effect=AssertionError('API')), \
                    patch('whoop_auth.load_tokens', side_effect=AssertionError('Credentials')), \
                    patch('whoop_auth.read_config', side_effect=AssertionError('Credentials')), \
                    patch('whoop_auth.refresh_tokens', side_effect=AssertionError('OAuth')):
                r = web.load_snapshot(root)
                with contextlib.redirect_stdout(output):
                    self.assertEqual(daily.main(['--data-dir', str(root)]), 0)
                self.assertEqual(output.getvalue().strip(), daily.format_daily_report(r))
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            html = web.render_report(r)
            self.assertIn('No workout archive available', html)
            self.assertIn('Original labels only', html)
            self.assertIn('No recorded workouts', html)
            (root / 'workouts.csv').write_text('invalid', encoding='utf-8')
            with self.assertRaises(web.APIError):
                web.load_snapshot(root)
            (root / 'daily_metrics.csv').write_text('invalid', encoding='utf-8')
            with self.assertRaises(web.analysis.AnalysisError):
                web.load_snapshot(root)

    def test_midload_change_is_not_rendered(self):
        with patch.object(web, 'fingerprint', side_effect=[('before',), ('after',)]), \
                patch.object(web, 'load_daily_report', return_value=report()):
            with self.assertRaises(web.SnapshotChanged):
                web.load_snapshot(Path('.'))


class LocalHTTPTests(OfflineDashboardTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_inputs(self.root)
        self.server = web.LocalServer(('127.0.0.1', 0), web.make_handler(self.root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path='/', method='GET', headers=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode('utf-8')
        finally:
            conn.close()

    def test_routes_security_headers_methods_and_refresh(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertIn(str(D), body)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.request('/dashboard.css')[0], 200)
        for path in ('/data/sleeps.csv', '/.env', '/whoop_tokens.json', '/../README.md', '/%2e%2e/README.md', '/?data-dir=other'):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request(headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request(headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
            self.assertEqual(self.request(method=method)[0], 405)
        self.assertEqual(self.request(method='HEAD')[2], '')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        (self.root / 'daily_metrics.csv').write_text('private-invalid-data', encoding='utf-8')
        status, _, body = self.request()
        self.assertEqual(status, 503)
        self.assertIn('Report unavailable', body)
        self.assertNotIn('private-invalid-data', body)
        write_inputs(self.root)
        self.assertEqual(self.request()[0], 200)


if __name__ == '__main__':
    unittest.main()
