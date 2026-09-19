"""Print the latest WHOOP cycle's measurements without writing health data."""

from datetime import datetime
import math
import time

import requests

from whoop_auth import AuthError, TOKEN_FILE, load_tokens, refresh_tokens


API_URL = "https://api.prod.whoop.com/developer/v2"


class APIError(Exception):
    """A safe error containing no raw response, request, or credentials."""


class WhoopClient:
    def __init__(self, token_file=TOKEN_FILE):
        self.token_file = token_file
        self.tokens = load_tokens(token_file)

    def refresh(self):
        self.tokens = refresh_tokens(self.tokens, self.token_file)

    def get(self, path, params=None, optional=False):
        expiry = self.tokens.get("expires_at")
        if (isinstance(expiry, (int, float)) and not isinstance(expiry, bool)
                and math.isfinite(expiry) and expiry <= time.time() + 60):
            self.refresh()
        # Unknown expiry is allowed: a 401 triggers one refresh and one retry.
        for attempt in range(2):
            try:
                with requests.get(
                    API_URL + path,
                    headers={"Authorization": "Bearer " + self.tokens["access_token"]},
                    params=params, timeout=30, allow_redirects=False,
                ) as response:
                    status = response.status_code
                    if status == 200:
                        payload = response.json()
                        if not isinstance(payload, dict):
                            raise APIError("WHOOP returned an unexpected data structure.")
                        return payload
                    if status == 404 and optional:
                        return None
                    if status != 401:
                        messages = {
                            403: "WHOOP denied access. Reconnect with recovery, cycle, and sleep permissions.",
                            429: "WHOOP rate limit reached. Please try again later.",
                        }
                        raise APIError(messages.get(status, "WHOOP data request failed. Please try again later."))
            except (requests.RequestException, ValueError):
                raise APIError("Could not read WHOOP data. Check your connection and retry.") from None
            if attempt == 0:
                self.refresh()
        raise AuthError("WHOOP rejected the refreshed access token. Run python whoop_auth.py to reconnect.")


def metric(record, field):
    if not record or record.get("score_state") != "SCORED":
        return None
    score = record.get("score")
    value = score.get(field) if isinstance(score, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def latest_summary(client):
    # The API orders cycles by start descending; limit=1 needs no pagination.
    payload = client.get("/cycle", params={"limit": 1})
    records = payload.get("records")
    if not isinstance(records, list):
        raise APIError("WHOOP returned an unexpected cycle collection.")
    if not records:
        return None
    return cycle_summary(client, records[0])


def cycle_summary(client, cycle):
    """Fetch matching recovery and primary sleep for one cycle."""
    if not isinstance(cycle, dict) or type(cycle.get("id")) is not int:
        raise APIError("WHOOP returned an invalid cycle.")
    cycle_id = cycle["id"]
    recovery = client.get(f"/cycle/{cycle_id}/recovery", optional=True)
    sleep = client.get(f"/cycle/{cycle_id}/sleep", optional=True)
    # Never silently combine measurements from unrelated cycles or naps.
    for record in (recovery, sleep):
        if record is not None and record.get("cycle_id") != cycle_id:
            raise APIError("WHOOP returned data for a different cycle. Please retry.")
    if sleep is not None and (sleep.get("nap") is not False or (
        recovery is not None and recovery.get("sleep_id") != sleep.get("id")
    )):
        raise APIError("WHOOP returned inconsistent sleep and recovery data. Please retry.")
    day = cycle_date(cycle)
    return {
        "date": day,
        "in_progress": cycle.get("end") is None,
        "recovery": metric(recovery, "recovery_score"),
        "hrv": metric(recovery, "hrv_rmssd_milli"),
        "resting_heart_rate": metric(recovery, "resting_heart_rate"),
        "day_strain": metric(cycle, "strain"),
        "sleep_performance": metric(sleep, "sleep_performance_percentage"),
    }


def cycle_date(cycle):
    """Use the physiological cycle's start in its recorded WHOOP timezone."""
    try:
        start = datetime.fromisoformat(cycle["start"].replace("Z", "+00:00"))
        offset = cycle["timezone_offset"]
        zone = datetime.fromisoformat("2000-01-01T00:00:00" + offset).tzinfo
        if start.tzinfo is None or zone is None:
            raise ValueError
        day = start.astimezone(zone).date().isoformat()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise APIError("WHOOP returned an invalid cycle date or timezone.") from None
    return day


def print_summary(summary):
    print(f"WHOOP - {summary['date']} (latest cycle, local date)")
    for label, key, unit in (
        ("Recovery score", "recovery", "%"),
        ("HRV", "hrv", " ms"),
        ("Resting heart rate", "resting_heart_rate", " bpm"),
        ("Day strain", "day_strain", " / 21"),
        ("Sleep performance", "sleep_performance", "%"),
    ):
        value = summary[key]
        formatted = f"{value:.2f}".rstrip("0").rstrip(".") + unit if value is not None else "N/A (not available or not scored yet)"
        print(f"  {label}: {formatted}")
    if summary["in_progress"]:
        print("Cycle is still in progress; day strain may increase.")


def main():
    try:
        summary = latest_summary(WhoopClient())
        if summary is None:
            print("No WHOOP cycles are available yet. Sync your WHOOP and try again.")
        else:
            print_summary(summary)
    except (AuthError, APIError) as error:
        print("WHOOP fetch failed:", error)
        return 1
    except OSError:
        print("WHOOP fetch failed: could not read local configuration. Check file permissions.")
        return 1
    except KeyboardInterrupt:
        print("\nWHOOP fetch cancelled.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
