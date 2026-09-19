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

This first integration authenticates and saves tokens only. Automatic token refresh
and API data downloads are not implemented yet; manual CSV entry remains available.

## Run checks

After installing the dependencies, run `python -m unittest discover -s tests -v`.
The checks use simulated WHOOP responses and never use your real credentials.
