import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import whoop_auth as auth
import whoop_fetch as fetch


def response(payload=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class FetchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.token_file = Path(directory.name) / "whoop_tokens.json"
        self.tokens = {"access_token": "fake-access", "refresh_token": "fake-refresh",
                       "expires_at": 9999999999}
        self.token_file.write_text(json.dumps(self.tokens))
        self.client = fetch.WhoopClient(self.token_file)

    def test_valid_access_and_request_security(self):
        with patch.object(fetch.requests, "get", return_value=response({"records": []})) as get:
            self.assertIsNone(fetch.latest_summary(self.client))
        self.assertEqual(get.call_args.args[0], fetch.API_URL + "/cycle")
        self.assertEqual(get.call_args.kwargs["params"], {"limit": 1})
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual(get.call_args.kwargs["timeout"], 30)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer fake-access")

    def test_refresh_rotates_and_persists_both_tokens(self):
        self.client.tokens["expires_at"] = 0
        payload = {"access_token": "rotated-access", "refresh_token": "rotated-refresh", "expires_in": 3600}
        config = {"WHOOP_CLIENT_ID": "fake-client", "WHOOP_CLIENT_SECRET": "fake-secret"}
        with patch.object(auth, "read_config", return_value=config), \
                patch.object(auth.requests, "post", return_value=response(payload)) as post, \
                patch.object(fetch.requests, "get", return_value=response({})) as get:
            self.client.get("/cycle")
        saved = json.loads(self.token_file.read_text())
        self.assertEqual(saved["access_token"], "rotated-access")
        self.assertEqual(saved["refresh_token"], "rotated-refresh")
        self.assertGreater(saved["expires_at"], auth.time.time())
        self.assertEqual(post.call_args.kwargs["data"], {
            "grant_type": "refresh_token", "refresh_token": "fake-refresh",
            "client_id": "fake-client", "client_secret": "fake-secret", "scope": "offline"})
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer rotated-access")

    def test_unknown_expiry_401_retries_once(self):
        self.client.tokens.pop("expires_at")
        for statuses in ([401, 200], [401, 401]):
            with self.subTest(statuses=statuses), \
                    patch.object(fetch.requests, "get", side_effect=[response({}, s) for s in statuses]) as get, \
                    patch.object(self.client, "refresh") as refresh:
                if statuses[-1] == 401:
                    with self.assertRaises(auth.AuthError):
                        self.client.get("/cycle")
                else:
                    self.assertEqual(self.client.get("/cycle"), {})
                self.assertEqual(get.call_count, 2)
                refresh.assert_called_once()

    def test_refresh_failure_preserves_file(self):
        original = self.token_file.read_bytes()
        for payload, status in (({"secret": "private"}, 400), ({}, 200), ([], 200)):
            with patch.object(auth, "read_config", return_value={"WHOOP_CLIENT_ID": "fake", "WHOOP_CLIENT_SECRET": "private"}), \
                    patch.object(auth.requests, "post", return_value=response(payload, status)):
                with self.assertRaises(auth.AuthError) as caught:
                    auth.refresh_tokens(self.tokens, self.token_file)
                self.assertNotIn("private", str(caught.exception))
                self.assertEqual(self.token_file.read_bytes(), original)

    def test_invalid_or_missing_token_file(self):
        for value in ("not json", "[]", '{}', '{"access_token": 123}'):
            self.token_file.write_text(value)
            with self.assertRaises(auth.AuthError):
                auth.load_tokens(self.token_file)
        with self.assertRaises(auth.AuthError):
            auth.load_tokens(self.token_file.parent / "missing.json")

    def records(self):
        return [
            {"records": [{"id": 12, "start": "2026-09-19T00:30:00Z", "timezone_offset": "-05:00",
                          "end": None, "score_state": "SCORED", "score": {"strain": 0}}]},
            {"cycle_id": 12, "sleep_id": "test-sleep", "score_state": "SCORED",
             "score": {"recovery_score": 80, "hrv_rmssd_milli": 60.25, "resting_heart_rate": 55}},
            {"id": "test-sleep", "cycle_id": 12, "nap": False, "score_state": "SCORED",
             "score": {"sleep_performance_percentage": 90}},
        ]

    def test_matching_latest_cycle_timezone_and_all_metrics(self):
        client = Mock()
        client.get.side_effect = self.records()
        summary = fetch.latest_summary(client)
        self.assertEqual(summary, {"date": "2026-09-18", "in_progress": True,
                                  "recovery": 80, "hrv": 60.25, "resting_heart_rate": 55,
                                  "day_strain": 0, "sleep_performance": 90})
        self.assertEqual([call.args[0] for call in client.get.call_args_list],
                         ["/cycle", "/cycle/12/recovery", "/cycle/12/sleep"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            fetch.print_summary(summary)
        for value in ("80%", "60.25 ms", "55 bpm", "0 / 21", "90%", "in progress"):
            self.assertIn(value, output.getvalue())

    def test_missing_pending_and_mismatched_data(self):
        records = self.records()
        records[1] = None
        records[2]["score_state"] = "PENDING_SCORE"
        client = Mock()
        client.get.side_effect = records
        summary = fetch.latest_summary(client)
        self.assertIsNone(summary["recovery"])
        self.assertIsNone(summary["sleep_performance"])
        self.assertEqual(summary["date"], "2026-09-18")
        for field, value in (("cycle_id", 99), ("id", "different-sleep"), ("nap", True)):
            records = self.records()
            records[2][field] = value
            client.get.side_effect = records
            with self.assertRaises(fetch.APIError):
                fetch.latest_summary(client)

    def test_safe_http_and_network_errors(self):
        for status in (302, 403, 429, 500):
            with patch.object(fetch.requests, "get", return_value=response({"private": "secret"}, status)), \
                    patch.object(self.client, "refresh") as refresh:
                with self.assertRaises(fetch.APIError) as caught:
                    self.client.get("/cycle")
                self.assertNotIn("secret", str(caught.exception))
                refresh.assert_not_called()
        with patch.object(fetch.requests, "get", side_effect=fetch.requests.RequestException("private secret")), \
                patch.object(fetch, "WhoopClient", return_value=self.client), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(fetch.main(), 1)
        self.assertNotIn("private secret", output.getvalue())

    def test_optional_404_and_invalid_json(self):
        with patch.object(fetch.requests, "get", return_value=response(status=404)):
            self.assertIsNone(self.client.get("/cycle/12/recovery", optional=True))
        invalid = response()
        invalid.json.side_effect = ValueError("private response")
        for result in (invalid, response([])):
            with patch.object(fetch.requests, "get", return_value=result):
                with self.assertRaises(fetch.APIError):
                    self.client.get("/cycle")

    def test_cli_does_not_write_csv_or_responses(self):
        csv = self.token_file.parent / "whoop_data.csv"
        csv.write_bytes(b"existing personal data\r\n")
        original = {p.name: p.read_bytes() for p in csv.parent.iterdir()}
        with patch.object(fetch, "WhoopClient", return_value=self.client), \
                patch.object(fetch.requests, "get", side_effect=[response(p) for p in self.records()]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fetch.main(), 0)
        self.assertEqual({p.name: p.read_bytes() for p in csv.parent.iterdir()}, original)


if __name__ == "__main__":
    unittest.main()
