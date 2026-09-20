"""Reversible, UUID-scoped activity classification; never edit WHOOP entities."""

import csv
from pathlib import Path
from uuid import UUID

from whoop_entities import DATA_DIR
from whoop_fetch import APIError

VERSION = "activity-v1"
CLASSIFICATION_FILE = DATA_DIR / "workout_classifications.csv"
FIELDS = ("workout_id", "recorded_activity", "canonical_activity", "provenance", "classification_version")
PROVENANCE = {"original_label", "user_confirmed", "user_confirmed_category", "unresolved"}


def load_classifications(path=CLASSIFICATION_FILE):
    """Missing optional sidecar means no personal corrections, not global rules."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file, strict=True)
            if reader.fieldnames != list(FIELDS):
                raise ValueError
            result = {}
            for row in reader:
                if set(row) != set(FIELDS) or any(not isinstance(v, str) or not v.strip() for v in row.values()):
                    raise ValueError
                wid = row["workout_id"]
                if str(UUID(wid)) != wid or wid in result or row["provenance"] not in PROVENANCE:
                    raise ValueError
                if row["provenance"] == "unresolved" and row["canonical_activity"] != "Unknown":
                    raise ValueError
                result[wid] = row
            return result
    except (ValueError, TypeError, csv.Error, UnicodeError):
        raise APIError("Invalid activity classification file; check its schema and unique UUIDs.") from None


def classify_workouts(rows, overrides=None):
    """Return copies retaining original labels and explicit classification provenance.

    No source-label rule maps Pickleball/Squash to Badminton. Personal corrections
    come exclusively from an exact UUID and original-label match in the sidecar.
    Group IDs remain unset until a future explicit user-confirmed grouping layer.
    """
    overrides = overrides or {}
    result, seen = [], set()
    for row in rows:
        wid, label = row["workout_id"], row["sport_name"]
        if wid in seen:
            raise APIError("Duplicate workout UUID in analysis input.")
        seen.add(wid)
        unresolved = label.casefold() in {"activity", "unspecified", "unknown"}
        classification = dict(workout_id=wid, recorded_activity=label,
                              canonical_activity="Unknown" if unresolved else label.title(),
                              provenance="unresolved" if unresolved else "original_label",
                              classification_version=VERSION)
        if wid in overrides:
            override = overrides[wid]
            if override["workout_id"] != wid or override["recorded_activity"] != label:
                raise APIError("Reviewed activity label no longer matches the workout; review classification.")
            classification = dict(override)
        result.append({**row, **classification, "training_session_id": None,
                       "grouping_status": "unreviewed", "member_workout_ids": [wid]})
    return sorted(result, key=lambda r: (r["start"], r["workout_id"]))
