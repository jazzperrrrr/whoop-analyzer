# WHOOP Analyzer

A beginner-friendly Python project for recording WHOOP measurements and connecting
your WHOOP account.

## Manual CSV entry

Run `python main.py` and enter the five measurements when prompted. Each run appends
today's measurements to `whoop_data.csv`. This works without OAuth or extra packages.

## Connect your WHOOP account

1. Install the authentication dependencies using your Python environment:
   `python -m pip install -r requirements.txt`.
2. In the [WHOOP Developer Dashboard](https://developer.whoop.com/), register this
   exact redirect URI for your app: `http://localhost:8080/callback`.
3. Fill in the project's existing `.env` using the three variable names in
   `.env.example`. If `.env` does not exist, copy `.env.example` to `.env` first.
   Enter your own client ID and client secret, and set
   `WHOOP_REDIRECT_URI=http://localhost:8080/callback`. Never share the secret.
4. Run `python whoop_auth.py`. Your browser opens WHOOP's sign-in and consent page.
5. Complete authorization within three minutes, then check the terminal for success.

The temporary server listens only on your computer at port 8080 and closes after
the callback, timeout, or cancellation. If the port is busy, close the app using it
and retry. A failed or cancelled attempt does not overwrite existing tokens.

The requested permissions are `read:recovery`, `read:cycles`, `read:sleep`, and
`offline`. WHOOP requires `offline` to issue a refresh token. See the official
[scope reference](https://developer.whoop.com/api/) and
[OAuth documentation](https://developer.whoop.com/docs/developing/oauth/).

Tokens and expiry information are saved in `whoop_tokens.json` beside the scripts.
This is a local, unencrypted credential file: keep it private. `.env`, the token
file, temporary token files, and your measurement CSV are ignored by Git.

## Fetch your latest WHOOP measurements

Run `python whoop_fetch.py` after connecting your account. It prints recovery score,
HRV (ms), resting heart rate (bpm), day strain, and sleep performance percentage.
The command reads the existing token file and refreshes tokens near expiry or once
after a 401 response. Rotated access and refresh tokens are saved atomically.
Run one fetch/authentication process at a time because WHOOP rotates refresh tokens.

Using the current [official v2 API](https://developer.whoop.com/api/), it requests
`GET /developer/v2/cycle?limit=1`, then `/cycle/{cycleId}/recovery` and
`/cycle/{cycleId}/sleep` under the same v2 base. This uses only the already requested
permissions. It selects the newest physiological cycle, labels it by its start
date in WHOOP's recorded timezone, and matches recovery and sleep by their IDs.
It does not assume that today's calendar date has processed data. Pending or
missing scores display as `N/A`; an ongoing cycle's strain can still increase.
It never substitutes an older day's score for a missing measurement.

API responses stay in memory and only the summary is printed. The command does not
create or modify `whoop_data.csv`. If you manually save raw API responses, put them
in the Git-ignored `whoop_responses/` directory. Never add credentials or personal
responses as test fixtures. Errors omit response bodies and credentials.
If refresh is rejected, reconnect with `python whoop_auth.py`. Network or rate-limit
errors can be retried later. No additional dependencies are needed.

## Run checks

After installing the dependencies, run `python -m unittest discover -s tests -v`.
The checks use simulated WHOOP responses and never use your real credentials.
