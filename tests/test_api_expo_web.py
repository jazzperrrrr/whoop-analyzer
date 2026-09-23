"""Opt-in Expo transport tests; synthetic snapshots and in-memory ASGI only."""
import unittest
from unittest.mock import Mock

from api.app import create_app, EXPO_WEB_ORIGINS
from asgi_transport import request
from product_fixtures import SyntheticInputs


class ExpoWebTests(SyntheticInputs, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_inputs()
        self.app = create_app(self.repo, expo_web=True)

    async def test_default_remains_same_origin_only(self):
        for origin in EXPO_WEB_ORIGINS:
            status, data, headers = await request(create_app(self.repo), '/api/v1/health',
                                                 headers=[(b'origin', origin.encode())])
            self.assertEqual(status, 403)
            self.assertEqual(data['code'], 'local_access_only')
            self.assertNotIn(b'access-control-allow-origin', headers)

    async def test_exact_origins_get_headers_and_unchanged_payloads(self):
        for origin in EXPO_WEB_ORIGINS:
            for path in ('/api/v1/health', '/api/v1/today', '/api/v1/sleep/latest', '/api/v1/trends?metric=actual_sleep'):
                with self.subTest(origin=origin, path=path):
                    status, body, headers = await request(self.app, path, headers=[
                        (b'origin', origin.encode()), (b'sec-fetch-site', b'cross-site')])
                    plain_status, plain_body, _ = await request(create_app(self.repo), path)
                    self.assertEqual((status, body), (plain_status, plain_body))
                    self.assertEqual(status, 200)
                    self.assertEqual(headers[b'access-control-allow-origin'], origin.encode())
                    self.assertEqual(headers[b'vary'], b'Origin')
                    self.assertEqual(headers[b'cache-control'], b'no-store')
                    self.assertNotIn(b'access-control-allow-credentials', headers)

    async def test_denies_other_ports_null_remote_and_duplicate_origins(self):
        for origins in ([b'http://localhost:8082'], [b'http://127.0.0.1:8082'], [b'null'],
                        [b'https://localhost:8081'], [b'http://192.168.1.2:8081'],
                        [b'http://evil.example'], [b'http://localhost:8081', b'http://localhost:8081']):
            status, _, headers = await request(self.app, '/api/v1/health', headers=[(b'origin', o) for o in origins])
            self.assertEqual(status, 403)
            self.assertNotIn(b'access-control-allow-origin', headers)

    async def test_explicit_origin_cannot_bypass_remote_client_or_bad_host(self):
        async def remote(scope, receive, send):
            await self.app({**scope, 'client': ('192.168.1.2', 12345)}, receive, send)
        async def bad_host(scope, receive, send):
            headers = [(k, b'evil.example' if k == b'host' else v) for k, v in scope['headers']]
            await self.app({**scope, 'headers': headers}, receive, send)
        for app in (remote, bad_host):
            status, _, headers = await request(app, '/api/v1/health', headers=[(b'origin', b'http://localhost:8081')])
            self.assertEqual(status, 403)
            self.assertNotIn(b'access-control-allow-origin', headers)

    async def test_allowance_is_get_only_and_frozen_paths_only(self):
        headers = [(b'origin', b'http://localhost:8081')]
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'):
            status, body, result = await request(self.app, '/api/v1/today', method, headers)
            self.assertEqual(status, 405)
            self.assertEqual(body['code'], 'read_only')
            self.assertNotIn(b'access-control-allow-origin', result)
        for path in ('/sync', '/oauth', '/.env', '/data/sleeps.csv'):
            status, _, result = await request(self.app, path, headers=headers)
            self.assertEqual(status, 403)
            self.assertNotIn(b'access-control-allow-origin', result)

    async def test_error_envelope_is_readable_without_exposing_details(self):
        repository = Mock()
        repository.read_snapshot.side_effect = RuntimeError('synthetic-private-path')
        status, body, headers = await request(create_app(repository, expo_web=True), '/api/v1/today',
                                              headers=[(b'origin', b'http://localhost:8081')])
        self.assertEqual(status, 500)
        self.assertEqual(set(body), {'code', 'message', 'retryable', 'request_id'})
        self.assertNotIn('synthetic-private-path', str(body))
        self.assertEqual(headers[b'access-control-allow-origin'], b'http://localhost:8081')

    async def test_opt_in_does_not_add_endpoints_or_change_openapi(self):
        self.assertEqual(self.app.openapi(), create_app(self.repo).openapi())
        repo = Mock()
        await request(create_app(repo, expo_web=True), '/api/v1/health', headers=[(b'origin', b'http://localhost:8081')])
        repo.read_snapshot.assert_not_called()
