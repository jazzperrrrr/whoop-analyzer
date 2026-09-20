"""Run `python whoop_auth.py` to connect your WHOOP account once."""

import json
import math
import os
from pathlib import Path
import secrets
import tempfile
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from dotenv import dotenv_values


PROJECT_DIR = Path(__file__).resolve().parent
TOKEN_FILE = PROJECT_DIR / "whoop_tokens.json"
REDIRECT_URI = "http://localhost:8080/callback"
AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = "read:recovery read:cycles read:sleep read:workout offline"


class AuthError(Exception):
    """A safe, user-facing error that never includes credentials or tokens."""


def read_config(env_file=None):
    # Read this project's .env, regardless of the terminal's working directory.
    # Disable interpolation so characters in a secret are preserved literally.
    values = dotenv_values(env_file or PROJECT_DIR / ".env", interpolate=False)
    names = ("WHOOP_CLIENT_ID", "WHOOP_CLIENT_SECRET", "WHOOP_REDIRECT_URI")
    missing = [name for name in names if not values.get(name)]
    if missing:
        raise AuthError("Set these values in .env: " + ", ".join(missing))
    if values["WHOOP_REDIRECT_URI"] != REDIRECT_URI:
        raise AuthError("WHOOP_REDIRECT_URI must be " + REDIRECT_URI)
    return {name: values[name] for name in names}


def authorization_url(config, state):
    return AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": config["WHOOP_CLIENT_ID"],
        "redirect_uri": config["WHOOP_REDIRECT_URI"],
        "scope": SCOPES,
        "state": state,
    })


def receive_code(url, state, timeout=180):
    """Listen only on loopback; unrelated or invalid callbacks cannot log in."""
    result = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, format, *args):
            # Default HTTP logs include the callback URL and authorization code.
            pass

        def reply(self, status, message):
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path != "/callback":
                self.reply(404, "Not found.")
                return
            params = parse_qs(parsed.query, keep_blank_values=True)
            states = params.get("state", [])
            if len(states) != 1 or not secrets.compare_digest(
                states[0].encode("utf-8"), state.encode("utf-8")
            ):
                self.reply(400, "Invalid OAuth state. Return to the WHOOP sign-in page.")
                return
            if "error" in params:
                result["error"] = "WHOOP authorization was denied or failed. Run the command to retry."
                self.reply(400, result["error"])
                return
            codes = params.get("code", [])
            if len(codes) != 1 or not codes[0]:
                result["error"] = "WHOOP did not return an authorization code. Please retry."
                self.reply(400, result["error"])
                return
            result["code"] = codes[0]
            self.reply(200, "Authorization received. Return to the terminal to check the result.")

    try:
        server = HTTPServer(("127.0.0.1", 8080), CallbackHandler)
    except OSError:
        raise AuthError("Cannot listen on localhost:8080. Close any app using port 8080 and retry.") from None

    with server:
        server.timeout = 0.5
        deadline = time.monotonic() + timeout
        print("Opening WHOOP sign-in. Waiting up to 3 minutes; press Ctrl+C to cancel.")
        try:
            opened = webbrowser.open(url, new=2)
        except webbrowser.Error:
            opened = False
        if not opened:
            raise AuthError("Could not open a browser. Configure a default browser and retry.")
        while not result and time.monotonic() < deadline:
            server.handle_request()

    if "error" in result:
        raise AuthError(result["error"])
    if "code" not in result:
        raise AuthError("Sign-in timed out. Run the command again to retry.")
    return result["code"]


def exchange_code(config, code):
    try:
        # Never put the secret in a URL, log, or browser request.
        with requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config["WHOOP_REDIRECT_URI"],
                "client_id": config["WHOOP_CLIENT_ID"],
                "client_secret": config["WHOOP_CLIENT_SECRET"],
            },
            timeout=30,
            allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise AuthError("WHOOP rejected the token exchange. Check your app settings and retry sign-in.")
            tokens = response.json()
    except (requests.RequestException, ValueError):
        # Exception text and response bodies may contain sensitive information.
        raise AuthError("Could not retrieve tokens from WHOOP. Check your connection and retry.") from None
    return normalize_tokens(tokens)


def normalize_tokens(tokens):
    """Validate and retain only the documented token fields."""
    if not isinstance(tokens, dict) or any(
        not isinstance(tokens.get(key), str) or not tokens[key]
        for key in ("access_token", "refresh_token")
    ):
        raise AuthError("WHOOP did not return access and refresh tokens. Retry with offline access enabled.")
    expiry = tokens.get("expires_in")
    if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
            or not math.isfinite(expiry) or expiry <= 0):
        raise AuthError("WHOOP returned an invalid token expiry. Please retry.")
    # Save only expected fields, never arbitrary response content.
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "token_type": tokens.get("token_type", "bearer"),
        "scope": tokens.get("scope", SCOPES),
        "expires_in": expiry,
        "expires_at": time.time() + expiry,
    }


def load_tokens(token_file=TOKEN_FILE):
    try:
        with token_file.open(encoding="utf-8") as file:
            tokens = json.load(file)
    except (OSError, ValueError):
        raise AuthError("Could not read whoop_tokens.json. Run python whoop_auth.py to connect.") from None
    if not isinstance(tokens, dict) or any(
        not isinstance(tokens.get(key), str) or not tokens[key].strip()
        for key in ("access_token", "refresh_token")
    ):
        raise AuthError("Invalid saved tokens. Run python whoop_auth.py to reconnect.")
    return tokens


def refresh_tokens(tokens, token_file=TOKEN_FILE):
    config = read_config()
    try:
        with requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": config["WHOOP_CLIENT_ID"],
                "client_secret": config["WHOOP_CLIENT_SECRET"],
                "scope": "offline",
            },
            timeout=30,
            allow_redirects=False,
        ) as response:
            if response.status_code in (400, 401):
                raise AuthError("WHOOP rejected token refresh. Run python whoop_auth.py to reconnect.")
            if response.status_code != 200:
                raise AuthError("WHOOP token refresh failed. Please try again later.")
            refreshed = normalize_tokens(response.json())
    except (requests.RequestException, ValueError):
        raise AuthError("Could not refresh WHOOP tokens. Check your connection and retry.") from None
    # WHOOP rotates BOTH tokens. Persist before using the new access token.
    try:
        save_tokens(refreshed, token_file)
    except OSError:
        raise AuthError("Could not save refreshed tokens. Check file permissions and reconnect with python whoop_auth.py.") from None
    return refreshed


def save_tokens(tokens, token_file=TOKEN_FILE):
    # Atomic replacement preserves existing tokens if writing fails.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=token_file.parent,
            prefix=token_file.name + ".", suffix=".tmp", delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(tokens, file, indent=2)
            file.write("\n")
        os.replace(temporary_path, token_file)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    try:
        config = read_config()
        # WHOOP documents an eight-character state; generate it securely.
        state = secrets.token_urlsafe(6)
        code = receive_code(authorization_url(config, state), state)
        save_tokens(exchange_code(config, code))
    except AuthError as error:
        print("Sign-in failed:", error)
        return 1
    except OSError:
        print("Sign-in failed: could not read configuration or save tokens. Check file permissions.")
        return 1
    except KeyboardInterrupt:
        print("\nSign-in cancelled.")
        return 1
    print("WHOOP connected. Tokens saved locally in whoop_tokens.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
