"""Collect normalized workout entities without saving raw API responses."""

import argparse
from collections import Counter
import csv
from datetime import date, datetime, time, timedelta, timezone
import math
import os
from pathlib import Path
import tempfile
from uuid import UUID

from whoop_auth import AuthError
from whoop_entities import DATA_DIR, fetch_pages, recorded_zone, timestamp
from whoop_fetch import APIError, WhoopClient

ZONE_FIELDS = tuple(f"zone_{n}_milli" for n in ("zero", "one", "two", "three", "four", "five"))
SCORE_FIELDS = ("strain", "average_heart_rate", "max_heart_rate", "kilojoule",
                "percent_recorded", "distance_meter", "altitude_gain_meter", "altitude_change_meter")
SCHEMA = ("workout_id", "sport_name", "sport_id", "start", "end", "timezone_offset",
          "local_start", "local_end", "report_date", "duration_seconds", "created_at",
          "updated_at", "score_state", *SCORE_FIELDS, "kcal", *ZONE_FIELDS)


def utc(value):
    return timestamp(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def number(value, signed=False, integer=False):
    if value is None:
        return None
    if (type(value) not in (int, float) or not math.isfinite(value)
            or (not signed and value < 0) or (integer and value != int(value))):
        raise APIError("Invalid WHOOP workout measurement; no data saved.")
    return int(value) if integer else float(value)


def normalize_workout(record):
    """One UUID, original activity name, and local dates using its own offset."""
    if not isinstance(record, dict):
        raise APIError("Invalid WHOOP workout; no data saved.")
    try:
        wid = str(UUID(record["id"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise APIError("Invalid WHOOP workout UUID; no data saved.") from None
    sport = record.get("sport_name")
    if not isinstance(sport, str) or not sport.strip():
        raise APIError("Missing WHOOP workout activity name; no data saved.")
    start, end = timestamp(record.get("start")), timestamp(record.get("end"))
    if end < start:
        raise APIError("Invalid WHOOP workout interval; no data saved.")
    offset = record.get("timezone_offset")
    zone = recorded_zone(offset)
    local_start, local_end = start.astimezone(zone), end.astimezone(zone)
    state = record.get("score_state")
    if state not in ("SCORED", "PENDING_SCORE", "UNSCORABLE"):
        raise APIError("Invalid WHOOP workout score state; no data saved.")
    score = record.get("score") if state == "SCORED" else None
    if score is not None and not isinstance(score, dict):
        raise APIError("Invalid WHOOP workout score; no data saved.")
    score = score or {}
    zones = score.get("zone_durations")
    if zones is not None and not isinstance(zones, dict):
        raise APIError("Invalid WHOOP workout zones; no data saved.")
    zones = zones or {}
    row = dict(workout_id=wid, sport_name=sport,
               sport_id=number(record.get("sport_id"), signed=True, integer=True),
               start=utc(record.get("start")), end=utc(record.get("end")), timezone_offset=offset,
               local_start=local_start.isoformat(), local_end=local_end.isoformat(),
               report_date=local_start.date().isoformat(), duration_seconds=(end - start).total_seconds(),
               score_state=state)
    for field in ("created_at", "updated_at"):
        row[field] = utc(record[field]) if record.get(field) is not None else None
    for field in SCORE_FIELDS:
        row[field] = number(score.get(field), signed=field == "altitude_change_meter")
    row["kcal"] = row["kilojoule"] / 4.184 if row["kilojoule"] is not None else None
    row.update({field: number(zones.get(field), integer=True) for field in ZONE_FIELDS})
    return row


def upsert(rows, row):
    previous = rows.get(row["workout_id"])
    # Unknown update time must not overwrite a known newer version.
    if previous and previous["updated_at"] and (
            not row["updated_at"] or timestamp(previous["updated_at"]) > timestamp(row["updated_at"])):
        return
    rows[row["workout_id"]] = row


def collect_workouts(client, start, end):
    """Collect a half-open UTC start-time window [start, end), fully paginated."""
    start, end = timestamp(start), timestamp(end)
    if start >= end:
        raise APIError("Workout start must precede end.")
    rows = {}
    for record in fetch_pages(client, "/activity/workout", {"start": utc(start.isoformat()),
                              "end": utc(end.isoformat()), "limit": 25}):
        row = normalize_workout(record)
        upsert(rows, row)
    # Filter after resolving repeated IDs, so a corrected start can leave the window.
    return {wid: row for wid, row in rows.items() if start <= timestamp(row["start"]) < end}


def read_workouts(path):
    if not path.exists():
        return {}
    try:
        with path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file, strict=True)
            if reader.fieldnames != list(SCHEMA):
                raise ValueError
            rows = {}
            for raw in reader:
                if set(raw) != set(SCHEMA) or any(v is None for v in raw.values()):
                    raise ValueError
                row = {k: v if v != "" else None for k, v in raw.items()}
                for field in (*SCORE_FIELDS, *ZONE_FIELDS, "sport_id", "kcal", "duration_seconds"):
                    row[field] = float(row[field]) if row[field] is not None else None
                record = dict(row, id=row["workout_id"], score={f: row[f] for f in SCORE_FIELDS})
                record["score"]["zone_durations"] = {f: row[f] for f in ZONE_FIELDS}
                normalized = normalize_workout(record)
                # Reject corrupt derived fields, stale unscored metrics, and duplicate UUIDs.
                if normalized != row or row["workout_id"] in rows:
                    raise ValueError
                rows[row["workout_id"]] = normalized
            return rows
    except (ValueError, TypeError, OverflowError, csv.Error, UnicodeError, APIError):
        raise APIError("Existing workout CSV is invalid; no data saved.") from None


def save_workouts(collection, directory=DATA_DIR):
    """Cumulative UUID archive, atomic replacement, deterministic serialization."""
    directory = Path(directory)
    path = directory / "workouts.csv"
    if directory.is_symlink() or path.is_symlink():
        raise APIError("Workout data paths must not be symbolic links.")
    rows = read_workouts(path)
    for row in collection.values():
        upsert(rows, row)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", dir=directory,
                                         prefix="workouts.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=SCHEMA)
            writer.writeheader()
            writer.writerows(sorted(rows.values(), key=lambda r: (r["start"], r["workout_id"])))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(rows)


def query_window(days=None, start_date=None, end_date=None, now=None):
    """Default: trailing 30*24h. Explicit dates: inclusive UTC calendar dates."""
    try:
        if start_date is not None and days is not None:
            raise ValueError
        if start_date is not None and end_date is None:
            raise ValueError
        if days is not None and (type(days) is not int or days < 1):
            raise ValueError
        end = (datetime.combine(end_date + timedelta(days=1), time.min, timezone.utc)
               if end_date is not None else (now or datetime.now(timezone.utc)))
        if end.tzinfo is None:
            raise ValueError
        start = (datetime.combine(start_date, time.min, timezone.utc) if start_date is not None
                 else end - timedelta(days=days if days is not None else 30))
        if start >= end:
            raise ValueError
        return utc(start.isoformat()), utc(end.isoformat())
    except (ValueError, OverflowError):
        raise APIError("Use positive days, or ordered start/end dates; do not combine start-date with days.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, help="Trailing days (default 30)")
    parser.add_argument("--start-date", type=date.fromisoformat, help="First UTC date, requires end-date")
    parser.add_argument("--end-date", type=date.fromisoformat, help="Last UTC date, inclusive")
    args = parser.parse_args(argv)
    try:
        start, end = query_window(args.days, args.start_date, args.end_date)
        client = WhoopClient()
        if "read:workout" not in client.tokens.get("scope", "").split():
            raise AuthError("Reconnect with python whoop_auth.py to grant read:workout.")
        rows = collect_workouts(client, start, end)
        stored = save_workouts(rows)
    except (AuthError, APIError) as error:
        print("WHOOP workouts failed:", error)
        return 1
    except OSError:
        print("WHOOP workouts failed: could not read or save local files.")
        return 1
    except KeyboardInterrupt:
        print("\nWHOOP workouts cancelled.")
        return 1
    print(f"Collected {len(rows)} workouts for UTC [{start}, {end}).")
    print(f"Stored {stored} workouts in data/workouts.csv.")
    for sport, count in sorted(Counter(row["sport_name"] for row in rows.values()).items()):
        print(f"  {sport}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
