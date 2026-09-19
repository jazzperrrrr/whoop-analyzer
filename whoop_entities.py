"""Normalized WHOOP entities and a separate sleep-end-dated reporting layer."""

import csv
from datetime import date, datetime, time, timedelta, timezone
import math
import os
from pathlib import Path
import re
import tempfile

from whoop_auth import PROJECT_DIR
from whoop_fetch import APIError, metric

DATA_DIR = PROJECT_DIR / "data"
TIME_FIELDS = ("start", "end", "timezone_offset", "created_at", "updated_at")
RECOVERY_METRICS = ("recovery_score", "hrv_ms", "resting_heart_rate_bpm")
SCHEMAS = {
    "cycles": ("cycle_id", *TIME_FIELDS, "score_state", "day_strain"),
    "sleeps": ("sleep_id", "cycle_id", *TIME_FIELDS, "nap", "score_state", "sleep_performance"),
    "recoveries": ("cycle_id", "sleep_id", "created_at", "updated_at", "score_state", *RECOVERY_METRICS),
    "daily_metrics": ("report_date", "cycle_id", "sleep_id", *RECOVERY_METRICS,
                      "day_strain", "sleep_performance", "sleep_start", "sleep_end",
                      "sleep_timezone_offset", "cycle_start", "cycle_end", "cycle_timezone_offset",
                      "recovery_present", "cycle_present", "recovery_score_state", "sleep_score_state",
                      "cycle_score_state"),
}
KEYS = {"cycles": ("cycle_id",), "sleeps": ("sleep_id",),
        "recoveries": ("cycle_id",), "daily_metrics": ("cycle_id", "sleep_id")}
METRICS = {"cycles": {"strain": "day_strain"},
           "sleeps": {"sleep_performance_percentage": "sleep_performance"},
           "recoveries": dict(zip(("recovery_score", "hrv_rmssd_milli", "resting_heart_rate"), RECOVERY_METRICS))}


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError):
        raise APIError("Invalid WHOOP timestamp; no data saved.") from None


def recorded_zone(value):
    if not isinstance(value, str) or not re.fullmatch(r"Z|[+-]\d{2}:\d{2}", value):
        raise APIError("Invalid WHOOP timezone offset; no data saved.")
    return timestamp("2000-01-01T00:00:00" + value).tzinfo


def sleep_report_date(sleep):
    """A reporting label, never an entity identity or join key."""
    return timestamp(sleep["end"]).astimezone(recorded_zone(sleep["timezone_offset"])).date().isoformat()


def normalize(kind, record):
    """Allowlist metadata and requested measurements; never persist raw JSON."""
    if not isinstance(record, dict):
        raise APIError("Invalid WHOOP entity; no data saved.")
    cid = record.get("id" if kind == "cycles" else "cycle_id")
    if type(cid) is not int or cid <= 0:
        raise APIError("Invalid WHOOP cycle ID; no data saved.")
    row = {field: record.get(field) for field in SCHEMAS[kind]}
    row["cycle_id"] = str(cid)
    if kind != "cycles":
        sid = record.get("id" if kind == "sleeps" else "sleep_id")
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9-]+", sid):
            raise APIError("Invalid WHOOP sleep ID; no data saved.")
        row["sleep_id"] = sid
    for field in ("created_at", "updated_at"):
        if row[field] is not None:
            timestamp(row[field])
    if row["score_state"] not in ("SCORED", "PENDING_SCORE", "UNSCORABLE"):
        raise APIError("Invalid WHOOP score state; no data saved.")
    if kind in ("cycles", "sleeps"):
        start = timestamp(row["start"])
        recorded_zone(row["timezone_offset"])
        if row["end"] is not None:
            if timestamp(row["end"]) < start:
                raise APIError("Invalid WHOOP time interval; no data saved.")
        elif kind == "sleeps":
            raise APIError("Missing WHOOP sleep end; no data saved.")
    if kind == "sleeps":
        if type(record.get("nap")) is not bool:
            raise APIError("Invalid WHOOP sleep type; no data saved.")
        row["nap"] = "true" if record["nap"] else "false"
    for source, field in METRICS[kind].items():
        row[field] = metric(record, source)
    return row


def entity_key(kind, row):
    return tuple(row[field] for field in KEYS[kind])


def insert(entities, kind, record):
    row = normalize(kind, record)
    key = entity_key(kind, row)
    previous = entities[kind].get(key)
    if previous and previous.get("updated_at") and row.get("updated_at"):
        if timestamp(previous["updated_at"]) > timestamp(row["updated_at"]):
            return  # Overlapping pages: retain the newer version of this ID.
    entities[kind][key] = row


def fetch_pages(client, path, params):
    params = dict(params)
    seen = set()
    while True:
        payload = client.get(path, params=dict(params))
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise APIError("Invalid WHOOP collection; no data saved.")
        yield from payload["records"]
        token = payload.get("next_token")
        if token is None or token == "":
            return
        if not isinstance(token, str) or token in seen:
            raise APIError("Invalid WHOOP pagination; no data saved.")
        seen.add(token)
        params["nextToken"] = token


def derive_daily(entities, first, last):
    """One row per primary sleep, with nullable recovery and cycle measurements.

    Entities without primary sleep have no known wake date; keep them in the
    entity tables rather than inventing a date. Naps never generate daily rows.
    """
    cycles, sleeps, recoveries = (entities[k] for k in ("cycles", "sleeps", "recoveries"))
    for recovery in recoveries.values():
        sleep = sleeps.get((recovery["sleep_id"],))
        if sleep and (sleep["cycle_id"] != recovery["cycle_id"] or sleep["nap"] != "false"):
            raise APIError("Inconsistent WHOOP recovery/sleep relationship; no data saved.")
    rows = []
    for sleep in sleeps.values():
        if sleep["nap"] != "false":
            continue
        day = sleep_report_date(sleep)
        if not first.isoformat() <= day <= last.isoformat():
            continue
        cid = sleep["cycle_id"]
        cycle = cycles.get((cid,), {})
        recovery = recoveries.get((cid,), {})
        if recovery.get("sleep_id") != sleep["sleep_id"]:
            recovery = {}
        row = {"report_date": day, "cycle_id": cid, "sleep_id": sleep["sleep_id"],
               **{field: recovery.get(field) for field in RECOVERY_METRICS},
               "day_strain": cycle.get("day_strain"), "sleep_performance": sleep["sleep_performance"],
               "recovery_present": "true" if recovery else "false",
               "cycle_present": "true" if cycle else "false",
               "recovery_score_state": recovery.get("score_state"),
               "sleep_score_state": sleep["score_state"], "cycle_score_state": cycle.get("score_state")}
        for prefix, entity in (("sleep", sleep), ("cycle", cycle)):
            for field in ("start", "end", "timezone_offset"):
                row[prefix + "_" + field] = entity.get(field)
        rows.append(row)
    return sorted(rows, key=lambda r: (r["report_date"], r["cycle_id"], r["sleep_id"]))


def collect_history(client, today=None, days=30):
    last = today or date.today()
    if type(days) is not int or days < 30:
        raise APIError("History requires at least 30 days.")
    first = last - timedelta(days=days - 1)
    params = {"limit": 25,
              "start": datetime.combine(first - timedelta(days=1), time.min, timezone.utc).isoformat(),
              "end": datetime.combine(last + timedelta(days=2), time.min, timezone.utc).isoformat()}
    entities = {kind: {} for kind in ("cycles", "sleeps", "recoveries")}
    for kind, path in (("cycles", "/cycle"), ("sleeps", "/activity/sleep"), ("recoveries", "/recovery")):
        for record in fetch_pages(client, path, params):
            insert(entities, kind, record)

    def related(kind, path, expected):
        record = client.get(path, optional=True)
        if record is not None:
            row = normalize(kind, record)
            if any(row.get(k) != v for k, v in expected.items()):
                raise APIError("Inconsistent WHOOP entity ID; no data saved.")
            insert(entities, kind, record)

    # Endpoint time semantics differ. Complete relationships by ID, even when
    # the related record begins before the padded collection bounds.
    cids = {r["cycle_id"] for kind in ("sleeps", "recoveries") for r in entities[kind].values()}
    for cid in sorted(cids):
        if (cid,) not in entities["cycles"]:
            related("cycles", f"/cycle/{cid}", {"cycle_id": cid})
    for (cid,) in list(entities["cycles"]):
        if (cid,) not in entities["recoveries"]:
            related("recoveries", f"/cycle/{cid}/recovery", {"cycle_id": cid})
    for recovery in list(entities["recoveries"].values()):
        sid = recovery["sleep_id"]
        if (sid,) not in entities["sleeps"]:
            related("sleeps", f"/activity/sleep/{sid}",
                    {"sleep_id": sid, "cycle_id": recovery["cycle_id"]})
    primary_cids = {s["cycle_id"] for s in entities["sleeps"].values() if s["nap"] == "false"}
    for (cid,) in entities["cycles"]:
        if cid not in primary_cids:
            related("sleeps", f"/cycle/{cid}/sleep", {"cycle_id": cid, "nap": "false"})
    return {"first": first, "last": last, "entities": entities,
            "daily_metrics": derive_daily(entities, first, last)}


def read_entities(path, kind):
    if not path.exists():
        return {}
    try:
        with path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file, strict=True)
            if reader.fieldnames != list(SCHEMAS[kind]):
                raise ValueError
            rows = {}
            for raw in reader:
                if set(raw) != set(SCHEMAS[kind]) or any(v is None for v in raw.values()):
                    raise ValueError
                row = {k: v if v != "" else None for k, v in raw.items()}
                key = entity_key(kind, row)
                if not all(key) or key in rows:
                    raise ValueError
                record = dict(row, cycle_id=int(row["cycle_id"]))
                if kind == "cycles":
                    record["id"] = int(row["cycle_id"])
                elif kind == "sleeps":
                    record["id"] = row["sleep_id"]
                    if row["nap"] not in ("true", "false"):
                        raise ValueError
                    record["nap"] = row["nap"] == "true"
                record["score"] = {}
                for source, field in METRICS[kind].items():
                    if row[field] is not None:
                        value = float(row[field])
                        if not math.isfinite(value):
                            raise ValueError
                        record["score"][source] = value
                normalize(kind, record)
                rows[key] = row
            return rows
    except (ValueError, TypeError, csv.Error, UnicodeError, APIError):
        raise APIError("Existing entity CSV is invalid; no data saved.") from None


def save_history(collection, directory=DATA_DIR):
    """Upsert entity archives; replace daily report for the requested window.

    Stage all files first; replacement is atomic per file, not across all four.
    Run one collector at a time. Rerun after an interrupted filesystem write.
    """
    directory = Path(directory)
    if directory.is_symlink():
        raise APIError("Data directory must not be a symbolic link.")
    paths = {kind: directory / (kind + ".csv") for kind in SCHEMAS}
    if any(path.is_symlink() for path in paths.values()):
        raise APIError("Entity CSVs must not be symbolic links.")
    tables = {}
    for kind in ("cycles", "sleeps", "recoveries"):
        rows = read_entities(paths[kind], kind)
        rows.update(collection["entities"][kind])
        tables[kind] = sorted(rows.values(), key=lambda r: entity_key(kind, r))
    # Derive from this collection, not potentially stale archived measurements.
    tables["daily_metrics"] = derive_daily(collection["entities"], collection["first"], collection["last"])
    directory.mkdir(parents=True, exist_ok=True)
    staged = []
    try:
        for kind, rows in tables.items():
            with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", dir=directory,
                                             prefix=kind + ".", suffix=".tmp", delete=False) as file:
                staged.append((Path(file.name), paths[kind]))
                writer = csv.DictWriter(file, fieldnames=SCHEMAS[kind])
                writer.writeheader()
                writer.writerows(rows)
                file.flush()
                os.fsync(file.fileno())
        for temporary, destination in staged:
            os.replace(temporary, destination)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    return {kind: len(rows) for kind, rows in tables.items()}
