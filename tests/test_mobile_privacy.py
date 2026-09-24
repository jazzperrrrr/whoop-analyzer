"""Synthetic artifact tests; no personal paths or payloads are stored in fixtures."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('mobile_privacy', Path(__file__).resolve().parents[1] / 'apps/mobile/scripts/privacy.py')
privacy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(privacy)


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        # Construct an invented path without making this source itself an artifact leak.
        self.path = chr(67) + ':' + '\\Users\\Fictional\\Workspace\\app.tsx'

    def test_plain_escaped_percent_encoded_and_utf16_paths(self):
        from urllib.parse import quote
        values = [self.path.encode(), json.dumps({'source': self.path}).encode(),
                  quote(self.path).encode(), self.path.encode('utf-16-le'),
                  self.path.replace(':', '\\u003a').encode()]
        for data in values:
            scanner = privacy.Scanner(); scanner.scan(data, 'bundle', 'development')
            self.assertEqual(scanner.findings[0]['kind'], 'absolute-path')
            self.assertNotIn(self.path, json.dumps(scanner.findings))

    def test_relative_paths_and_http_origins_are_not_absolute_workspace_paths(self):
        scanner = privacy.Scanner()
        scanner.scan(b'{"sources":["src/app.tsx"],"origin":"https://device.synthetic.test"}', 'source.ts', 'source')
        self.assertEqual(scanner.findings, [])

    def test_categories_are_distinct(self):
        scanner = privacy.Scanner()
        for category in ['source', 'development', 'distribution']:
            scanner.scan(self.path.encode(), 'artifact', category)
        self.assertEqual([f['category'] for f in scanner.findings], ['source', 'development', 'distribution'])

    def test_inline_source_map(self):
        data = base64.b64encode(json.dumps({'sources': [self.path]}).encode())
        scanner = privacy.Scanner()
        scanner.scan(b'//# sourceMappingURL=data:application/json;charset=utf-8;base64,' + data, 'bundle.js', 'development')
        self.assertTrue(any(f['artifact'].endswith('#inline-map') for f in scanner.findings))

    def test_zip_nested_metadata_and_excluded_upload_files(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('app/source.json', json.dumps({'sourceRoot': self.path}))
            archive.writestr('data/synthetic.csv', 'fictional')
            archive.writestr('.env', 'FICTIONAL=only')
        scanner = privacy.Scanner(); scanner.scan(buffer.getvalue(), 'upload.zip', 'distribution')
        self.assertTrue(any(f['kind'] == 'absolute-path' for f in scanner.findings))
        self.assertTrue(any(f['kind'] == 'excluded-upload-artifact' for f in scanner.findings))

    def test_tar_is_scanned_without_extraction(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            data = self.path.encode(); entry = tarfile.TarInfo('source.js'); entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
        scanner = privacy.Scanner(); scanner.scan(buffer.getvalue(), 'upload.tar.gz', 'distribution')
        self.assertTrue(scanner.findings)

    def test_design_tokens_are_safe_inside_upload_archives(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('src/design/tokens.ts', 'export const spacing = 8;')
        scanner = privacy.Scanner(); scanner.scan(buffer.getvalue(), 'upload.zip', 'distribution')
        self.assertEqual(scanner.findings, [])

    def test_unreadable_and_scan_limits_fail_closed(self):
        scanner = privacy.Scanner(); scanner.scan(b'broken', 'app.ipa', 'distribution')
        self.assertEqual(scanner.findings[0]['kind'], 'unreadable-archive')
        scanner = privacy.Scanner(); scanner.scan(b'compressed', 'bundle.br', 'distribution')
        self.assertEqual(scanner.findings[0]['kind'], 'unsupported-compression')
        with patch.object(privacy, 'LIMIT', 8):
            scanner = privacy.Scanner(); scanner.scan(b'long synthetic content', 'bundle', 'distribution')
            self.assertEqual(scanner.findings[0]['kind'], 'scan-limit-exceeded')

    def test_unsafe_archive_paths(self):
        scanner = privacy.Scanner()
        scanner.archive_name('../outside', 'distribution', 'upload.zip')
        self.assertEqual(scanner.findings[0]['kind'], 'unsafe-archive-path')

    def test_upload_allowlist_excludes_sensitive_and_generated_material(self):
        for name in ['package.json', 'package-lock.json', 'app/index.tsx', 'src/api/http.ts', 'src/design/tokens.ts']:
            self.assertTrue(privacy.upload_candidate(name))
        for name in ['data/sleeps.csv', '.env', 'whoop_tokens.json', 'src/.env.local',
                     'src/data/synthetic.csv', 'src/classifications.json', 'src/logs/log.txt',
                     'src/screenshots/image.png', 'src/debug.bundle', 'src/debug.map',
                     'node_modules/pkg/index.js', '.expo/settings.json', '.jest-cache/cache',
                     'src/__pycache__/module.pyc', 'tests/test.ts', 'README.md', 'scripts/privacy.py']:
            self.assertFalse(privacy.upload_candidate(name), name)

    def test_network_scan_refuses_api_remote_and_redirect_targets(self):
        for url in ['http://localhost:8000/api/v1/today', 'https://example.com/index.bundle',
                    'http://localhost:8081/sync', 'http://user:secret@localhost:8081/index.bundle']:
            scanner = privacy.Scanner()
            with patch.object(privacy, 'build_opener') as opener:
                privacy.development_url(scanner, url)
                opener.return_value.open.assert_not_called()
            self.assertEqual(scanner.findings[0]['kind'], 'refused-non-artifact-url')

    def test_development_scan_follows_real_map_comments_only(self):
        bundle = b'const example = "sourceMappingURL=not-a-url";\n//# sourceMappingURL=index.map\n'
        responses = [io.BytesIO(bundle), io.BytesIO(b'{"sources":["src/index.ts"]}')]
        with patch.object(privacy, 'build_opener') as opener:
            opener.return_value.open.side_effect = responses
            scanner = privacy.Scanner()
            privacy.development_url(scanner, 'http://localhost:8081/index.bundle')
            self.assertEqual(opener.return_value.open.call_count, 2)
            self.assertEqual(scanner.findings, [])


class ContentPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.secret = '6a' * 32  # Predictable synthetic counterexample, never a credential.

    def scan(self, source, name='src/api/config.ts', category='source'):
        scanner = privacy.Scanner()
        scanner.scan(source.encode() if isinstance(source, str) else source, name, category)
        return scanner

    def assert_rule(self, source, rule, **kwargs):
        scanner = self.scan(source, **kwargs)
        self.assertIn(rule, [finding['kind'] for finding in scanner.findings])
        # Do not include the secret as an assertion operand that could be printed.
        self.assertTrue(self.secret not in json.dumps(scanner.findings))
        return scanner

    def test_credential_callbacks_with_literal_secret(self):
        for form in ["credential: async () => '%s'", 'const credential = () => { return "%s"; }',
                     'credential: async (): Promise<string> => "%s"',
                     'credential: function() { return "%s"; }', 'credential() { return "%s"; }']:
            self.assert_rule(form % self.secret, 'hardcoded_credential_literal')

    def test_named_credential_assignments(self):
        for name in ['access_token', 'refresh_token', 'client_secret', 'device_token',
                     'bearer_token', 'authorization', 'credential', 'secret', 'clientSecret']:
            self.assert_rule(json.dumps({name: self.secret}), 'hardcoded_credential_literal')
        self.assert_rule('const device_token: string = "' + self.secret + '";', 'hardcoded_credential_literal')

    def test_literal_bearer_authorization_forms(self):
        for form in ['Authorization: "Bearer %s"', "{'authorization': 'Bearer %s'}",
                     'headers.Authorization = `Bearer %s`;', 'headers["Authorization"] = "Bearer %s";',
                     'headers.set("Authorization", "Bearer %s");']:
            self.assert_rule(form % self.secret, 'hardcoded_bearer_credential')

    def test_relative_private_backend_reference(self):
        self.assert_rule('const file = "../../data/sleeps.csv";', 'private_backend_reference')

    def test_private_reference_normalization(self):
        from urllib.parse import quote
        path = '..\\..\\data\\sleeps.csv'
        for source in [json.dumps({'file': path}), json.dumps(json.dumps({'file': path})),
                       'const file = "' + quote('../../data/sleeps.csv', safe='') + '";',
                       r'const file = "..\x2f..\u002fdata\u002fsleeps.csv";',
                       r'const file = "..\/..\/data\/sleeps.csv";']:
            self.assert_rule(source, 'private_backend_reference')

    def test_known_private_files_and_sidecars(self):
        for path in ['whoop_data.csv', 'whoop_history.csv', 'whoop_tokens.json',
                     'workout_classifications.csv', '.env', '.env.local', '../../.env',
                     '../../classifications/workouts.json', '../../data/cache/sleep.json',
                     '../../workout_classifications.json']:
            self.assert_rule('const source = ' + json.dumps(path), 'private_backend_reference')
        self.assert_rule('const file = `../../data/sleeps.csv`;', 'private_backend_reference')

    def test_sensitive_upload_candidate_archive_members(self):
        name = 'src/api/config.ts'
        self.assertTrue(privacy.upload_candidate(name))
        source = ('credential: () => "' + self.secret + '";\nAuthorization: "Bearer '
                  + self.secret + '";\nconst source = "../../data/sleeps.csv";').encode()
        expected = {'hardcoded_credential_literal', 'hardcoded_bearer_credential', 'private_backend_reference'}
        archives = []
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, source)
        archives.append(('upload.zip', buffer.getvalue()))
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            member = tarfile.TarInfo(name); member.size = len(source)
            archive.addfile(member, io.BytesIO(source))
        archives.append(('upload.tgz', buffer.getvalue()))
        for archive_name, data in archives:
            scanner = self.scan(data, archive_name, 'distribution')
            member_rules = {f['kind'] for f in scanner.findings if f['artifact'].endswith('!' + name)}
            self.assertTrue(expected.issubset(member_rules))
            self.assertTrue(self.secret not in json.dumps(scanner.findings))

    def test_public_snapshot_ids_and_hash_literals_are_allowed(self):
        source = json.dumps({'snapshot_id': self.secret, 'sha256': self.secret, 'hash': self.secret})
        self.assertEqual(self.scan(source).findings, [])
        self.assertEqual(self.scan('credential: () => token;\nconst hash = "' + self.secret + '";').findings, [])

    def test_design_tokens_and_color_values_are_allowed(self):
        self.assertEqual(self.scan('const tokens = { green: "#00aa88", radius: 12 };',
                                   'src/design/tokens.ts').findings, [])

    def test_documented_exclusions_and_basename_filters_are_not_file_references(self):
        for source in ['Exclude `data/`, `.env`, token files and CSVs.', "base.startswith('.env')"]:
            self.assertEqual(self.scan(source).findings, [])

    def test_runtime_authorization_and_credentials_are_allowed(self):
        for source in ['Authorization: `Bearer ${token}`', 'Authorization: "Bearer " + token',
                       'headers.set("Authorization", `Bearer ${await credential()}`)',
                       'credential: async () => token', 'const secret = runtimeValue',
                       'access_token: process.env.ACCESS_TOKEN']:
            self.assertEqual(self.scan(source).findings, [])

    def test_explicit_dummy_placeholders_only_are_allowed(self):
        for literal in ['dummy-token', 'test-only-secret', 'synthetic-placeholder', 'test-access']:
            self.assertEqual(self.scan(json.dumps({'Authorization': 'Bearer ' + literal})).findings, [])
        # Test filenames/comments do not whitelist credential-like contents.
        self.assert_rule('// synthetic test only\ncredential: () => "' + self.secret + '"',
                         'hardcoded_credential_literal', name='tests/security.test.ts')

    def test_existing_approved_synthetic_fixtures_are_accepted(self):
        for name in ['api_v1_examples.json', 'visual_demo.json']:
            scanner = privacy.Scanner()
            scanner.file(privacy.MOBILE / 'src/api/fixtures' / name, 'distribution', 'src/api/fixtures/' + name)
            self.assertEqual(scanner.findings, [])

    def test_safe_output_contains_only_category_label_and_rule(self):
        source = 'Authorization: "Bearer ' + self.secret + '";'
        scanner = self.assert_rule(source, 'hardcoded_bearer_credential')
        for finding in scanner.findings:
            self.assertEqual(set(finding), {'category', 'artifact', 'kind'})
            self.assertEqual(finding['artifact'], 'src/api/config.ts')
        output = json.dumps(scanner.findings)
        self.assertTrue(self.secret not in output and source not in output and 'Bearer ' not in output)

    def test_encoded_credentials_and_utf16_content(self):
        source = 'credential: () => "' + self.secret + '"'
        for encoded in [json.dumps(source), json.dumps(json.dumps(source)), source.encode('utf-16-le'),
                        source.replace('6', r'\x36').replace('a', r'\u0061')]:
            self.assert_rule(encoded, 'hardcoded_credential_literal')
