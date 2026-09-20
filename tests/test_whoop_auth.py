import contextlib
import io
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError

import whoop_auth as auth


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = {
            "WHOOP_CLIENT_ID": "test-client",
            "WHOOP_CLIENT_SECRET": "test-only-secret",
            "WHOOP_REDIRECT_URI": auth.REDIRECT_URI,
        }

    def test_configuration_and_exact_scopes(self):
        env = self.root / ".env"
        env.write_text("\n".join(f"{k}={v}" for k, v in self.config.items()))
        self.assertEqual(auth.read_config(env), self.config)
        url = auth.authorization_url(self.config, "state123")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["scope"], ["read:recovery read:cycles read:sleep read:workout offline"])
        self.assertEqual(query["redirect_uri"], [auth.REDIRECT_URI])
        self.assertNotIn(self.config["WHOOP_CLIENT_SECRET"], url)
        env.write_text("WHOOP_CLIENT_ID=test-client\n")
        with self.assertRaises(auth.AuthError):
            auth.read_config(env)
        env.write_text("\n".join(f"{k}={v}" for k, v in self.config.items()).replace(
            auth.REDIRECT_URI, "http://example.com/callback"))
        with self.assertRaises(auth.AuthError):
            auth.read_config(env)

    def simulate_callback(self, paths):
        """Use a real loopback server, but never launch a browser or contact WHOOP."""
        statuses = []
        failures = []

        def visit():
            try:
                for path in paths:
                    try:
                        with urlopen("http://127.0.0.1:8080" + path, timeout=5) as response:
                            statuses.append(response.status)
                            response.read()
                    except HTTPError as error:
                        statuses.append(error.code)
                        error.close()
            except Exception as error:
                failures.append(error)

        def open_browser(url, new):
            worker = threading.Thread(target=visit)
            worker.start()
            self.addCleanup(worker.join, 6)
            return True

        with patch.object(auth.webbrowser, "open", side_effect=open_browser):
            try:
                code = auth.receive_code("https://example.com", "state123", timeout=5)
            finally:
                # Verify context-manager cleanup even for denied authorization.
                with auth.HTTPServer(("127.0.0.1", 8080), auth.BaseHTTPRequestHandler):
                    pass
        self.assertFalse(failures)
        return code, statuses

    def test_callback_ignores_unrelated_and_forged_requests(self):
        code, statuses = self.simulate_callback([
            "/favicon.ico",
            "/callback?state=wrong&code=forged",
            "/callback?state=state123&state=duplicate&code=forged",
            "/callback?state=state123&code=accepted",
        ])
        self.assertEqual(code, "accepted")
        self.assertEqual(statuses, [404, 400, 400, 200])

    def test_denial_and_missing_code(self):
        for path in ("/callback?state=state123&error=access_denied",
                     "/callback?state=state123"):
            with self.subTest(path=path), self.assertRaises(auth.AuthError):
                self.simulate_callback([path])

    def test_timeout_and_browser_failure_close_server(self):
        for opened in (True, False):
            with patch.object(auth.webbrowser, "open", return_value=opened):
                with self.assertRaises(auth.AuthError):
                    auth.receive_code("https://example.com", "state123", timeout=0)
            with auth.HTTPServer(("127.0.0.1", 8080), auth.BaseHTTPRequestHandler):
                pass

    def response(self, payload, status=200):
        response = Mock(status_code=status)
        response.json.return_value = payload
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_exchange_and_save(self):
        payload = {"access_token": "test-access", "refresh_token": "test-refresh", "expires_in": 3600}
        with patch.object(auth.requests, "post", return_value=self.response(payload)) as post:
            tokens = auth.exchange_code(self.config, "test-code")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(post.call_args.kwargs["data"]["code"], "test-code")
        self.assertEqual(post.call_args.kwargs["data"]["client_secret"], self.config["WHOOP_CLIENT_SECRET"])
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        target = self.root / "whoop_tokens.json"
        auth.save_tokens(tokens, target)
        self.assertEqual(json.loads(target.read_text())["refresh_token"], "test-refresh")
        self.assertGreater(tokens["expires_at"], auth.time.time())
        original = target.read_bytes()
        with patch.object(auth.os, "replace", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                auth.save_tokens(tokens, target)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_exchange_failures_do_not_expose_response_or_secret(self):
        for response in (self.response({}, 401), self.response({}, 302),
                         self.response([]), self.response({"access_token": "test-access"})):
            with patch.object(auth.requests, "post", return_value=response):
                with self.assertRaises(auth.AuthError) as caught:
                    auth.exchange_code(self.config, "test-code")
                self.assertNotIn(self.config["WHOOP_CLIENT_SECRET"], str(caught.exception))
        with patch.object(auth.requests, "post", side_effect=auth.requests.RequestException("private data")):
            with self.assertRaises(auth.AuthError) as caught:
                auth.exchange_code(self.config, "test-code")
            self.assertNotIn("private data", str(caught.exception))

    def test_manual_csv_still_appends_one_header(self):
        script = self.root / "main.py"
        shutil.copyfile(auth.PROJECT_DIR / "main.py", script)
        for _ in range(2):
            with patch("builtins.input", side_effect=["80", "60", "55", "90", "12"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(str(script), run_name="__main__")
        rows = (self.root / "whoop_data.csv").read_text().splitlines()
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0].startswith("date,recovery_percentage,"))
        self.assertEqual(rows[1], rows[2])


if __name__ == "__main__":
    unittest.main()
