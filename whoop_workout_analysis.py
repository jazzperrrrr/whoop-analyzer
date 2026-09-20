"""Read-only observed workout summaries and exact D+1 descriptive outcome pairs."""

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import statistics

from whoop_activity import CLASSIFICATION_FILE, classify_workouts, load_classifications
from whoop_analysis import AnalysisError, load_daily_metrics
from whoop_entities import DATA_DIR, recorded_zone, timestamp
from whoop_fetch import APIError
from whoop_workouts import ZONE_FIELDS, read_workouts

OUTCOMES = ("hrv_ms", "resting_heart_rate_bpm", "recovery_score", "sleep_performance")


def values(rows, field):
    return [r[field] for r in rows if r.get(field) is not None]


def total(rows, field):
    observed = values(rows, field)
    return sum(observed) if observed else None


def describe(rows):
    """Available-value summaries, with denominators; no zero-filled calendar."""
    days = sorted({r["report_date"] for r in rows})
    strain = values(rows, "strain")
    duration = values(rows, "duration_seconds")
    hr = [r for r in rows if r.get("average_heart_rate") is not None and r.get("duration_seconds", 0) > 0]
    hr_seconds = sum(r["duration_seconds"] for r in hr)
    complete_zones = [r for r in rows if all(r.get(f) is not None for f in ZONE_FIELDS)]
    zone_sum = sum(sum(r[f] for f in ZONE_FIELDS) for r in complete_zones)
    energy = total(rows, "kilojoule")
    return {
        "workout_ids": [r["workout_id"] for r in rows], "record_count": len(rows),
        "training_dates": days, "distinct_training_dates": len(days),
        "canonical_activity_counts": dict(sorted(Counter(r["canonical_activity"] for r in rows).items())),
        "recorded_activity_counts": dict(sorted(Counter(r["recorded_activity"] for r in rows).items())),
        "distinct_canonical_activity_count": len({r["canonical_activity"] for r in rows}),
        "classification_versions": sorted({r["classification_version"] for r in rows}),
        "classification_provenance_counts": dict(Counter(r["provenance"] for r in rows)),
        "duration_seconds": total(rows, "duration_seconds"),
        "mean_record_duration_seconds": statistics.mean(duration) if duration else None,
        "kilojoule": energy, "kcal": energy / 4.184 if energy is not None else None,
        "strain_mean": statistics.mean(strain) if strain else None,
        "strain_median": statistics.median(strain) if strain else None,
        "strain_max": max(strain) if strain else None,
        "arithmetic_session_strain_sum": sum(strain) if strain else None,
        "duration_weighted_average_hr": (sum(r["average_heart_rate"] * r["duration_seconds"] for r in hr)
                                          / hr_seconds if hr_seconds else None),
        "hr_weighted_duration_seconds": hr_seconds,
        "max_hr": max(values(rows, "max_heart_rate"), default=None),
        "zone_totals_milli": {f: total(rows, f) for f in ZONE_FIELDS},
        "zone_percentages": {f: sum(r[f] for r in complete_zones) / zone_sum * 100 if zone_sum else None
                             for f in ZONE_FIELDS},
        "complete_zone_record_count": len(complete_zones),
        "observed_counts": {f: len(values(rows, f)) for f in
                            ("duration_seconds", "kilojoule", "strain", "average_heart_rate", "max_heart_rate", *ZONE_FIELDS)},
        "date_spacing_days": [(date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(days, days[1:])],
        "calendar_coverage": "unknown", "real_world_session_count": None,
    }


def aggregate_days(rows):
    grouped = defaultdict(list)
    for row in rows:
        # Recompute from UTC + recorded offset; never trust host timezone.
        day = timestamp(row["start"]).astimezone(recorded_zone(row["timezone_offset"])).date().isoformat()
        if day != row["report_date"]:
            raise APIError("Workout local report date is inconsistent.")
        grouped[day].append(row)
    result = {}
    for day, records in sorted(grouped.items()):
        summary = describe(records)
        offsets = sorted({r["timezone_offset"] for r in records})
        summary.update(report_date=day, utc_offsets=offsets, mixed_offset=len(offsets) > 1,
                       mixed_activity=summary["distinct_canonical_activity_count"] > 1,
                       latest_end_utc=max((timestamp(r["end"]) for r in records)).isoformat(),
                       midnight_crossing=any(timestamp(r["local_start"]).date() != timestamp(r["local_end"]).date()
                                             for r in records),
                       overlap=False)
        result[day] = summary
    # Check UTC overlaps across all records, including different local date labels.
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if timestamp(a["start"]) < timestamp(b["end"]) and timestamp(b["start"]) < timestamp(a["end"]):
                result[a["report_date"]]["overlap"] = result[b["report_date"]]["overlap"] = True
    return result


def rolling_summaries(rows, anchor):
    result = {}
    for n in (7, 14, 30):
        first = anchor - timedelta(days=n - 1)
        selected = [r for r in rows if first <= date.fromisoformat(r["report_date"]) <= anchor]
        result[n] = {**describe(selected), "first_date": first.isoformat(), "last_date": anchor.isoformat(),
                     "window_days": n, "anchor_day_complete": "unknown"}
    return result


def pair_next_day(days, physiology):
    """Keep ambiguous outcomes separate and never substitute the next available date."""
    bydate = defaultdict(list)
    for row in physiology:
        bydate[row["report_date"]].append(row)
    pairs = []
    for day, exposure in days.items():
        target = date.fromisoformat(day) + timedelta(days=1)
        candidates = bydate.get(target, [])
        outcomes = []
        for row in candidates:
            metrics = {m: row.get(m) for m in OUTCOMES}
            # Scoring metadata, if supplied, takes precedence over numeric remnants.
            for metric in OUTCOMES:
                prefix = "sleep" if metric == "sleep_performance" else "recovery"
                if row.get(prefix + "_score_state") not in (None, "", "SCORED") or (
                        prefix == "recovery" and row.get("recovery_present") == "false"):
                    metrics[metric] = None
            sleep_offset = row.get("sleep_timezone_offset") or None
            cycle_offset = row.get("cycle_timezone_offset") or None
            offset_unknown = sleep_offset is None
            transition = (exposure["mixed_offset"] or (sleep_offset is not None and sleep_offset not in exposure["utc_offsets"])
                          or bool(cycle_offset and sleep_offset and cycle_offset != sleep_offset))
            sleep_start = row.get("sleep_start")
            chronology_unknown = not sleep_start
            chronology_conflict = bool(sleep_start and timestamp(exposure["latest_end_utc"]) > timestamp(sleep_start))
            outcomes.append({**metrics, "cycle_id": row.get("cycle_id"), "sleep_id": row.get("sleep_id"),
                             "sleep_start": sleep_start, "sleep_end": row.get("sleep_end"),
                             "sleep_timezone_offset": sleep_offset, "cycle_timezone_offset": cycle_offset,
                             "offset_transition": transition, "offset_unknown": offset_unknown,
                             "chronology_unknown": chronology_unknown, "chronology_conflict": chronology_conflict,
                             "cycle_in_progress": "cycle_end" in row and not row["cycle_end"] and row.get("cycle_present") != "false",
                             "missing_metrics": [m for m, v in metrics.items() if v is None]})
        pairs.append({"workout_date": day, "outcome_date": target.isoformat(),
                      "workout_ids": exposure["workout_ids"],
                      "canonical_activity_counts": exposure["canonical_activity_counts"],
                      "mixed_activity": exposure["mixed_activity"], "mixed_offset": exposure["mixed_offset"],
                      "missing_outcome": not outcomes, "ambiguous_outcome": len(outcomes) > 1,
                      "outcomes": outcomes, "sport_attribution": None,
                      "classification_versions": exposure["classification_versions"]})
    return pairs


def build_report(rows, physiology, anchor=None):
    if anchor is None:
        anchor = max((date.fromisoformat(r["report_date"]) for r in rows), default=None)
    selected = [r for r in rows if anchor and date.fromisoformat(r["report_date"]) <= anchor]
    days = aggregate_days(selected)
    activities = {name: describe([r for r in selected if r["canonical_activity"] == name])
                  for name in sorted({r["canonical_activity"] for r in selected})}
    return {"anchor": anchor, "latest_workout_date": max((r["report_date"] for r in selected), default=None),
            "records": selected, "daily": days, "activities": activities,
            "rolling": rolling_summaries(selected, anchor) if anchor else {},
            "pairs": pair_next_day(days, physiology)}


def display(value, divisor=1):
    return "N/A" if value is None else f"{value / divisor:.2f}"


def summary_line(s):
    return (f"{s['record_count']} WHOOP records / {s['distinct_training_dates']} dates; "
            f"{display(s['duration_seconds'], 3600)} h; {display(s['kilojoule'])} kJ / {display(s['kcal'])} kcal; "
            f"strain mean/median/max {display(s['strain_mean'])}/{display(s['strain_median'])}/{display(s['strain_max'])}")


def format_report(report):
    lines = [f"Workout analysis: latest workout date {report['latest_workout_date'] or 'N/A'}; anchor {report['anchor'] or 'N/A'}",
             "Observed records only. Calendar coverage and final-day completeness unknown; no rest days inferred.",
             "WHOOP records are not confirmed real-world sessions; no automatic merging.",
             "Metric totals use available values; missing is not zero. Session Strain is not additive Day Strain."]
    for n, summary in report["rolling"].items():
        lines += [f"{n}-day: {summary_line(summary)}", f"  Canonical counts: {summary['canonical_activity_counts']}",
                  "  HR zones 0-5 minutes: " + ", ".join(display(summary['zone_totals_milli'][f], 60000) for f in ZONE_FIELDS)]
    lines.append("Canonical counts through anchor:")
    for name, summary in report["activities"].items():
        lines.append(f"  {name}: {summary['record_count']} records / {summary['distinct_training_dates']} dates")
    for name in ("Badminton", "Gym / Strength"):
        summary = report["activities"].get(name, describe([]))
        lines += [f"{name}: {summary_line(summary)}",
                  f"  Mean recorded duration: {display(summary['mean_record_duration_seconds'], 60)} min; "
                  f"duration-weighted HR (approximate): {display(summary['duration_weighted_average_hr'])}; max HR: {display(summary['max_hr'])}",
                  "  Zone minutes: " + ", ".join(display(summary['zone_totals_milli'][f], 60000) for f in ZONE_FIELDS),
                  "  Zone percentages (complete-zone records only): " + ", ".join(display(summary['zone_percentages'][f]) for f in ZONE_FIELDS),
                  f"  Gaps between distinct training dates (days): {summary['date_spacing_days'] or 'N/A'}"]
    lines.append("Gym / Strength: cardiovascular metrics do not quantify muscular workload.")
    pairs = report["pairs"]
    matched = sum(not p["missing_outcome"] for p in pairs)
    complete = sum(len(p["outcomes"]) == 1 and not p["outcomes"][0]["missing_metrics"] for p in pairs)
    flagged = sum(p["mixed_offset"] or p["ambiguous_outcome"] or any(
        o["offset_transition"] or o["offset_unknown"] or o["chronology_conflict"] or o["chronology_unknown"] for o in p["outcomes"]) for p in pairs)
    lines += [f"Exact D+1 outcomes: {matched}/{len(pairs)} workout dates; {complete} unambiguous complete-metric pairs; {flagged} timing/offset/ambiguity flags.",
              "Descriptive pairing only, no causation or sport attribution on mixed-activity days. Missing D+1 stays missing.",
              f"Unresolved Unknown: {report['activities'].get('Unknown', {}).get('record_count', 0)} records (included in overall totals)."]
    return "\n".join(lines)


def main(argv=None):
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workouts", type=Path, default=DATA_DIR / "workouts.csv")
    parser.add_argument("--daily-metrics", type=Path, default=DATA_DIR / "daily_metrics.csv")
    parser.add_argument("--classifications", type=Path, default=CLASSIFICATION_FILE)
    parser.add_argument("--as-of", type=date.fromisoformat)
    args = parser.parse_args(argv)
    try:
        if not args.workouts.is_file():
            raise APIError("Workout dataset is missing.")
        corrections = load_classifications(args.classifications)
        rows = classify_workouts(read_workouts(args.workouts).values(), corrections)
        physiology = load_daily_metrics(args.daily_metrics)
        if not args.classifications.exists():
            print("No classification sidecar: using original labels; no personal corrections applied.")
        print(format_report(build_report(rows, physiology, args.as_of)))
    except (APIError, AnalysisError) as error:
        print("Workout analysis failed:", error)
        return 1
    except OSError:
        print("Workout analysis failed: cannot read local inputs.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
