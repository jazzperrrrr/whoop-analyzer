"""Device gate tests use only synthetic secrets/snapshots and in-memory ASGI."""
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from api.app import create_app
from api.device_security import DeviceProfile, configured_device_profile
from api.schemas import Error, TodayResponse, SleepResponse
from asgi_transport import request
from product_fixtures import SyntheticInputs, NOW

TOKEN = 'ab' * 32  # Deliberately predictable synthetic test secret, never a credential.
HOST = 'device.synthetic.test'


class DeviceSecurityTests(SyntheticInputs, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_inputs()
        self.verifier = self.root / 'device-verifier.json'
        self.write_verifier()
        self.profile = DeviceProfile(HOST, self.verifier, clock=lambda: NOW)
        self.app = create_app(self.repo, device_profile=self.profile)

    def write_verifier(self, token=TOKEN, expires=NOW + timedelta(hours=1)):
        self.verifier.write_text(json.dumps({'version': 1,
            'sha256': hashlib.sha256(token.encode()).hexdigest(),
            'expires_at': expires.strftime('%Y-%m-%dT%H:%M:%SZ')}), encoding='utf-8')

    async def call(self, path='/api/v1/health', method='GET', token=TOKEN,
                   host=HOST, peer='127.0.0.1', headers=(), app=None):
        async def transport(scope, receive, send):
            incoming = list(scope['headers'])
            incoming[0] = (b'host', host.encode())
            await (app or self.app)({**scope, 'headers': incoming, 'client': (peer, 12345)}, receive, send)
        auth = [] if token is None else [(b'authorization', ('Bearer ' + token).encode())]
        return await request(transport, path, method, [*auth, *headers])

    def denied(self, result):
        status, body, headers = result
        self.assertEqual(status, 403)
        Error.model_validate(body)
        self.assertEqual({k: v for k, v in body.items() if k != 'request_id'}, {
            'code': 'local_access_only', 'message': 'Local access only.', 'retryable': False})
        self.assertNotIn('access-control-allow-origin'.encode(), headers)
        self.assertNotIn(TOKEN, json.dumps(body))
        self.assertNotIn(str(self.verifier), json.dumps(body))

    async def test_default_disabled_and_token_cannot_authorize_device_access(self):
        self.assertIsNone(configured_device_profile({}))
        with patch.object(Path, 'open', side_effect=AssertionError('Must not read verifier')):
            self.assertIsNone(configured_device_profile({'WHOOP_DEVICE_VERIFIER_FILE': 'unused'}))
        self.denied(await self.call(app=create_app(self.repo)))

    async def test_missing_token(self):
        self.denied(await self.call(token=None))

    async def test_wrong_token(self):
        self.denied(await self.call(token='cd' * 32))

    async def test_correct_token_all_three_routes_and_frozen_payloads(self):
        for path, model in [('/api/v1/health', None), ('/api/v1/today', TodayResponse), ('/api/v1/sleep/latest', SleepResponse)]:
            status, body, headers = await self.call(path)
            plain_status, plain_body, _ = await request(create_app(self.repo), path)
            self.assertEqual((status, body), (plain_status, plain_body))
            self.assertEqual(status, 200)
            if model:
                model.model_validate(body)
            self.assertNotIn(TOKEN, json.dumps(body))
            self.assertNotIn(b'access-control-allow-credentials', headers)

    async def test_expiry_including_exact_deadline(self):
        for expiry in [NOW, NOW - timedelta(seconds=1)]:
            self.write_verifier(expires=expiry)
            self.denied(await self.call())

    async def test_revocation_by_deletion_and_replacement(self):
        self.assertEqual((await self.call())[0], 200)
        self.verifier.unlink()
        self.denied(await self.call())
        self.write_verifier(token='cd' * 32)
        self.denied(await self.call())
        self.assertEqual((await self.call(token='cd' * 32))[0], 200)

    async def test_no_local_host_bypass_in_device_profile(self):
        for host in ['localhost:8000', '127.0.0.1:8000', '[::1]:8000', 'other.synthetic.test', HOST + ':443', HOST + '.']:
            self.denied(await self.call(host=host))

    async def test_methods_fail_with_same_generic_envelope(self):
        for method in ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD']:
            self.denied(await self.call(method=method))

    async def test_paths_queries_and_browser_origins_are_not_device_access(self):
        for path in ['/api/v1/trends', '/sync', '/oauth', '/token', '/.env', '/api/v1/today/',
                     '/api/v1/health?token=' + TOKEN, '/api/v1/health?anything=1']:
            self.denied(await self.call(path))
        self.denied(await self.call(headers=[(b'origin', b'http://localhost:8081')]))

    async def test_query_cannot_replace_authorization(self):
        self.denied(await self.call('/api/v1/health?token=' + TOKEN, token=None))

    async def test_remote_peer_and_spoofed_forwarded_headers(self):
        self.denied(await self.call(peer='192.0.2.1', headers=[(b'x-forwarded-for', b'127.0.0.1'),
            (b'x-forwarded-host', HOST.encode()), (b'forwarded', b'for=127.0.0.1')]))
        self.assertEqual((await self.call(peer='::1'))[0], 200)

    async def test_duplicate_headers_rejected(self):
        self.denied(await self.call(headers=[(b'host', HOST.encode())]))
        self.denied(await self.call(headers=[(b'authorization', ('Bearer ' + TOKEN).encode())]))

    async def test_malformed_or_unreadable_verifier_is_generic(self):
        for content in ['not json', '{}', '[]', 'x' * 4097,
                        json.dumps({'version': 1, 'sha256': 'bad', 'expires_at': 'never'})]:
            self.verifier.write_text(content, encoding='utf-8')
            self.denied(await self.call())
        with patch.object(Path, 'open', side_effect=PermissionError('synthetic detail')):
            self.denied(await self.call())

    async def test_no_logs_or_token_detail_on_denial(self):
        with patch('logging.Logger._log') as log, patch('builtins.print') as output:
            self.denied(await self.call(token='invalid-' + TOKEN))
        log.assert_not_called()
        output.assert_not_called()

    async def test_uses_constant_time_verifier_comparison(self):
        import hmac
        with patch('api.device_security.hmac.compare_digest', wraps=hmac.compare_digest) as compare:
            self.assertEqual((await self.call())[0], 200)
            self.assertEqual(compare.call_count, 1)
            self.assertEqual(len(compare.call_args.args[0]), 64)

    async def test_local_web_profile_and_trends_unchanged(self):
        local = create_app(self.repo, expo_web=True)
        for path in ['/api/v1/health', '/api/v1/today', '/api/v1/sleep/latest', '/api/v1/trends?metric=actual_sleep']:
            status, _, headers = await request(local, path, headers=[(b'origin', b'http://localhost:8081')])
            self.assertEqual(status, 200)
            self.assertEqual(headers[b'access-control-allow-origin'], b'http://localhost:8081')

    async def test_no_schema_or_endpoint_changes_and_denials_do_not_read_health(self):
        self.assertEqual(self.app.openapi(), create_app(self.repo).openapi())
        repo = Mock()
        self.denied(await self.call(token=None, app=create_app(repo, device_profile=self.profile)))
        repo.read_snapshot.assert_not_called()

    def test_explicit_configuration_and_outside_repository_boundary(self):
        configured = configured_device_profile({'WHOOP_API_PROFILE': 'native-device',
            'WHOOP_DEVICE_HOST': HOST, 'WHOOP_DEVICE_VERIFIER_FILE': str(self.verifier)})
        self.assertEqual(configured.host, HOST)
        for host in ['localhost', '127.0.0.1', '*.' + HOST, 'https://' + HOST, HOST + '/path']:
            with self.assertRaisesRegex(ValueError, '^Invalid device profile configuration$'):
                DeviceProfile(host, self.verifier)
        with self.assertRaises(ValueError):
            DeviceProfile(HOST, Path(__file__).resolve().parent / 'verifier.json')
        with self.assertRaises(ValueError):
            DeviceProfile(HOST, Path('relative.json'))
        for env in [{'WHOOP_API_PROFILE': 'invalid'}, {'WHOOP_API_PROFILE': 'native-device'}]:
            with self.assertRaisesRegex(ValueError, '^Invalid device profile configuration$'):
                configured_device_profile(env)
        with self.assertRaises(ValueError):
            create_app(self.repo, expo_web=True, device_profile=self.profile)
