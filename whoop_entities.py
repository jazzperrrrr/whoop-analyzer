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
from whoop_sleep import (EXTENDED_FIELDS, INTEGER_FIELDS, STAGE_FIELDS, NEED_FIELDS,
                         SCORE_FIELDS, measurement, normalize_sleep_measurements)

DATA_DIR = PROJECT_DIR / "data"
TIME_FIELDS = ("start", "end", "timezone_offset", "created_at", "updated_at")
RECOVERY_METRICS = ("recovery_score", "hrv_ms", "resting_heart_rate_bpm")
LEGACY_SLEEP_SCHEMA = ("sleep_id", "cycle_id", *TIME_FIELDS, "nap", "score_state", "sleep_performance")
SCHEMAS = {
    "cycles": ("cycle_id", *TIME_FIELDS, "score_state", "day_strain"),
    "sleeps": (*LEGACY_SLEEP_SCHEMA, *EXTENDED_FIELDS),
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
    if kind == "sleeps":
        try:
            row.update(normalize_sleep_measurements(record))
        except (ValueError, OverflowError):
            raise APIError("Invalid WHOOP sleep measurements; no data saved.") from None
    return row


def entity_key(kind, row):
    return tuple(row[field] for field in KEYS[kind])


def effective_version(previous, candidate):
    """Resolve a completed batch's chosen version against the stored archive.

    Known update times beat unknown times. Equal-time archive refreshes retain
    incoming-update semantics. Same-batch resolution is handled separately.
    """
    if previous is None:
        return candidate
    old_time, new_time = previous.get("updated_at"), candidate.get("updated_at")
    if old_time and not new_time:
        return previous
    if new_time and not old_time:
        return candidate
    if old_time and new_time:
        old_time, new_time = timestamp(old_time), timestamp(new_time)
        if old_time != new_time:
            return previous if old_time > new_time else candidate
    return candidate


def highest_precedence_candidates(candidates):
    """Keep only the newest known update group, or all candidates if undated."""
    dated = [(timestamp(row["updated_at"]), row) for row in candidates if row.get("updated_at")]
    if not dated:
        return list(candidates)
    latest = max(updated for updated, _ in dated)
    return [row for updated, row in dated if updated == latest]


def resolve_entity_candidates(candidates):
    """Select one actual normalized response from a complete same-ID batch.

    A winning-timestamp candidate must cover every supplied value in its group.
    Missing means None, never zero. Disjoint partial records are not merged, and
    conflicts fail identically regardless of pagination or candidate order.
    """
    winners = highest_precedence_candidates(candidates)
    for candidate in winners:
        if all(value is None or candidate.get(key) == value
               for other in winners for key, value in other.items()):
            return candidate
    raise APIError("Conflicting WHOOP entity versions without a covering source record; no data saved.")


def insert(candidates, kind, record):
    """Accumulate normalized responses by ID; do not resolve streaming prefixes."""
    row = normalize(kind, record)
    key = entity_key(kind, row)
    candidates[kind].setdefault(key, []).append(row)


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
    return collect_window(client, first, last)


def collect_window(client, first, last):
    """Collect an explicit reporting window, retaining existing padding/ID lookups."""
    if not isinstance(first, date) or not isinstance(last, date) or first > last:
        raise APIError("Invalid collection window; no data saved.")
    params = {"limit": 25,
              "start": datetime.combine(first - timedelta(days=1), time.min, timezone.utc).isoformat(),
              "end": datetime.combine(last + timedelta(days=2), time.min, timezone.utc).isoformat()}
    candidates = {kind: {} for kind in ("cycles", "sleeps", "recoveries")}
    for kind, path in (("cycles", "/cycle"), ("sleeps", "/activity/sleep"), ("recoveries", "/recovery")):
        for record in fetch_pages(client, path, params):
            insert(candidates, kind, record)

    def related(kind, path, expected):
        record = client.get(path, optional=True)
        if record is not None:
            row = normalize(kind, record)
            if any(row.get(k) != v for k, v in expected.items()):
                raise APIError("Inconsistent WHOOP entity ID; no data saved.")
            insert(candidates, kind, record)

    def references(kind, fields):
        # Relationship lookups need IDs, not a prematurely selected measurement
        # record. Ignore superseded timestamps and retain all winning references.
        return sorted({tuple(row[field] for field in fields)
                       for group in candidates[kind].values()
                       for row in highest_precedence_candidates(group)})

    # Endpoint time semantics differ. Complete relationships by ID, even when
    # the related record begins before the padded collection bounds.
    cids = set(references("sleeps", ("cycle_id",))) | set(references("recoveries", ("cycle_id",)))
    for (cid,) in sorted(cids):
        if (cid,) not in candidates["cycles"]:
            related("cycles", f"/cycle/{cid}", {"cycle_id": cid})
    for (cid,) in sorted(candidates["cycles"]):
        if (cid,) not in candidates["recoveries"]:
            related("recoveries", f"/cycle/{cid}/recovery", {"cycle_id": cid})
    for cid, sid in references("recoveries", ("cycle_id", "sleep_id")):
        if (sid,) not in candidates["sleeps"]:
            related("sleeps", f"/activity/sleep/{sid}",
                    {"sleep_id": sid, "cycle_id": cid})
    primary_cids = {cid for cid, nap in references("sleeps", ("cycle_id", "nap")) if nap == "false"}
    for (cid,) in sorted(candidates["cycles"]):
        if cid not in primary_cids:
            related("sleeps", f"/cycle/{cid}/sleep", {"cycle_id": cid, "nap": "false"})
    # All pages and related responses have arrived before any final selection.
    entities = {kind: {key: resolve_entity_candidates(groups[key]) for key in sorted(groups)}
                for kind, groups in candidates.items()}
    return {"first": first, "last": last, "entities": entities,
            "daily_metrics": derive_daily(entities, first, last)}


def read_entities(path, kind):
    """Read exact current schemas or the explicit ten-column legacy sleep schema.

    Legacy sleep details expand to None in memory only. New measurements are
    typed; existing columns retain their historical read behavior.
    """
    if not path.exists():
        return {}
    try:
        with path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file, strict=True)
            header = reader.fieldnames
            accepted = [list(SCHEMAS[kind])]
            if kind == "sleeps":
                accepted.append(list(LEGACY_SLEEP_SCHEMA))
            if header not in accepted:
                raise ValueError
            rows = {}
            for raw in reader:
                if set(raw) != set(header) or any(v is None for v in raw.values()):
                    raise ValueError
                row = {k: raw.get(k) if raw.get(k) != "" else None for k in SCHEMAS[kind]}
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
                if kind == "sleeps":
                    for group, fields in (("stage_summary", STAGE_FIELDS), ("sleep_needed", NEED_FIELDS), (None, SCORE_FIELDS)):
                        target = record["score"] if group is None else record["score"].setdefault(group, {})
                        for source, field in fields.items():
                            value = row[field]
                            if value is not None:
                                if field in INTEGER_FIELDS:
                                    if not re.fullmatch(r"-?[0-9]+", value):
                                        raise ValueError
                                    value = int(value)
                                else:
                                    value = float(value)
                                value = measurement(field, value)
                                if row["score_state"] != "SCORED":
                                    raise ValueError
                            row[field] = value
                            target[source] = value
                normalize(kind, record)
                rows[key] = row
            return rows
    except (ValueError, TypeError, OverflowError, csv.Error, UnicodeError, APIError):
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
    tables, effective_batch = {}, {}
    for kind in ("cycles", "sleeps", "recoveries"):
        rows = read_entities(paths[kind], kind)
        effective_batch[kind] = {}
        for key, row in collection["entities"][kind].items():
            selected = effective_version(rows.get(key), row)
            rows[key] = selected
            effective_batch[kind][key] = selected
        tables[kind] = sorted(rows.values(), key=lambda r: entity_key(kind, r))
    # Only incoming identities participate, using the same versions as the archive.
    # Relationship validation completes before staging or replacing any files.
    tables["daily_metrics"] = derive_daily(effective_batch, collection["first"], collection["last"])
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
