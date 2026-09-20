"""Read-only badminton training-date cases; descriptive responses, never predictions."""

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import math
from pathlib import Path
import statistics

from whoop_activity import CLASSIFICATION_FILE, classify_workouts, load_classifications
from whoop_analysis import AnalysisError, load_daily_metrics
from whoop_entities import DATA_DIR, recorded_zone, timestamp
from whoop_fetch import APIError
from whoop_workout_analysis import OUTCOMES, aggregate_days, describe, pair_next_day
from whoop_workouts import ZONE_FIELDS, read_workouts

MINIMUMS = {7: 5, 14: 10, 30: 21}
PRIMARY_WINDOW = 14


def valid_metric(row, metric):
    prefix = "sleep" if metric == "sleep_performance" else "recovery"
    value = row.get(metric)
    if (row.get(prefix + "_score_state") not in (None, "", "SCORED")
            or row.get(prefix + "_present") == "false"
            or type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        return None
    return value


def pretraining_baselines(physiology, training_date, first_workout_start):
    """Per-date means of distinct entities, then equal-weight means across dates.

    Conflicting copies of one entity invalidate that date for that metric. No
    forward fill, day D, or future outcome can contribute to any window.
    """
    result = {}
    for window, minimum in MINIMUMS.items():
        first, last = training_date - timedelta(days=window), training_date - timedelta(days=1)
        relevant = [r for r in physiology if first <= r["report_date"] <= last]
        result[window] = {}
        for metric in OUTCOMES:
            grouped = defaultdict(dict)
            conflicts, multiple, offsets = set(), set(), set()
            chronology_excluded = 0
            chronology_unknown = False
            for r in relevant:
                end = r.get("sleep_end")
                if end and timestamp(end) >= timestamp(first_workout_start):
                    chronology_excluded += 1
                    continue
                value = valid_metric(r, metric)
                if value is None:
                    continue
                chronology_unknown |= not bool(end)
                day = r["report_date"]
                identity = r.get("sleep_id" if metric == "sleep_performance" else "cycle_id")
                if not identity:
                    conflicts.add(day)
                    continue
                if identity in grouped[day] and grouped[day][identity] != value:
                    conflicts.add(day)
                grouped[day][identity] = value
                if r.get("sleep_timezone_offset"):
                    offsets.add(r["sleep_timezone_offset"])
            observations = []
            for day, entities in sorted(grouped.items()):
                if day in conflicts:
                    continue
                if len(entities) > 1:
                    multiple.add(day)
                observations.append(statistics.mean(entities.values()))
            result[window][metric] = {
                "mean": statistics.mean(observations) if observations else None,
                "median": statistics.median(observations) if observations else None,
                "valid_count": len(observations), "required_count": minimum,
                "eligible": len(observations) >= minimum,
                "window_start": first.isoformat(), "window_end": last.isoformat(),
                "utc_offsets": sorted(offsets), "conflicting_dates": sorted(str(d) for d in conflicts),
                "multiple_entity_dates": sorted(str(d) for d in multiple),
                "chronology_excluded_count": chronology_excluded,
                "chronology_unknown": chronology_unknown,
            }
    return result


def badminton_exposure(rows):
    summary = describe(rows)
    complete_zones = [r for r in rows if all(r.get(f) is not None for f in ZONE_FIELDS)]
    high_zones = [r for r in rows if all(r.get(f) is not None for f in ZONE_FIELDS[4:])]
    denominator = sum(sum(r[f] for f in ZONE_FIELDS) for r in complete_zones)
    # Deliberately omit Phase 1's optional arithmetic strain sum from Phase 2.
    return {k: summary[k] for k in (
        "workout_ids", "record_count", "duration_seconds", "kilojoule", "kcal",
        "duration_weighted_average_hr", "max_hr", "zone_totals_milli", "observed_counts",
        "strain_mean", "strain_median", "strain_max", "classification_versions") } | {
        "zone45_milli": sum(sum(r[f] for f in ZONE_FIELDS[4:]) for r in high_zones) if high_zones else None,
        "zone45_record_count": len(high_zones), "complete_zone_record_count": len(complete_zones),
        "zone45_percent": (100 * sum(sum(r[f] for f in ZONE_FIELDS[4:]) for r in complete_zones) / denominator
                           if denominator else None),
        "individual_strains": [{"workout_id": r["workout_id"], "strain": r.get("strain")} for r in rows],
        "real_world_session_count": None,
    }


def recent_training_context(training_date, grouped):
    result = []
    for lag in (1, 2, 3):
        day = training_date - timedelta(days=lag)
        rows = grouped.get(day.isoformat(), [])
        badminton = [r for r in rows if r["canonical_activity"] == "Badminton"]
        summary = describe(rows)
        result.append({"date": day.isoformat(), "lag_days": lag,
                       "recorded_workout_count": len(rows), "canonical_activities": summary["canonical_activity_counts"],
                       "badminton_duration_seconds": (sum(r["duration_seconds"] for r in badminton)
                                                       if rows else None),
                       "overall_duration_seconds": summary["duration_seconds"],
                       "zone_totals_milli": summary["zone_totals_milli"],
                       "utc_offsets": sorted({r["timezone_offset"] for r in rows}),
                       "coverage_status": "unknown", "confirmed_rest_day": False,
                       "record_status": "observed_workouts" if rows else "no_recorded_workouts"})
    return result


def baseline_responses(baselines, pair):
    result = {}
    outcome = pair["outcomes"][0] if len(pair["outcomes"]) == 1 else None
    for metric in OUTCOMES:
        baseline = baselines[metric]
        reasons = []
        if not baseline["eligible"]:
            reasons.append("insufficient_baseline_coverage")
        if pair["missing_outcome"]:
            reasons.append("missing_exact_D+1_outcome")
        elif pair["ambiguous_outcome"]:
            reasons.append("ambiguous_physiological_observation")
        elif outcome["chronology_conflict"]:
            reasons.append("outcome_chronology_conflict")
        elif valid_metric(outcome, metric) is None:
            reasons.append("missing_or_invalid_outcome_metric")
        difference = None if reasons else outcome[metric] - baseline["mean"]
        relative = None
        if metric == "hrv_ms" and not reasons:
            if baseline["mean"] == 0:
                reasons.append("zero_HRV_baseline")
            else:
                relative = 100 * (outcome[metric] / baseline["mean"] - 1)
        result[metric] = {"eligible": not reasons, "reasons": reasons or ["eligible"],
                          "absolute_difference": difference, "relative_percent": relative,
                          "primary_response": relative if metric == "hrv_ms" else difference,
                          "primary_unit": "%" if metric == "hrv_ms" else "bpm" if metric == "resting_heart_rate_bpm" else "percentage points",
                          "absolute_unit": "ms" if metric == "hrv_ms" else "bpm" if metric == "resting_heart_rate_bpm" else "percentage points"}
    return result


def reference_inventory(grouped):
    categories = ("Badminton-only", "Badminton + Gym", "Golf-only", "Gym-only", "Walking-only",
                  "No recorded workouts", "Other mixed")
    result = {name: {"dates": [], "coverage_status": "unknown"} for name in categories}
    if not grouped:
        return result
    first, last = date.fromisoformat(min(grouped)), date.fromisoformat(max(grouped))
    for i in range((last - first).days + 1):
        day = (first + timedelta(days=i)).isoformat()
        activities = {r["canonical_activity"] for r in grouped.get(day, [])}
        if activities == {"Badminton"}:
            category = "Badminton-only"
        elif "Badminton" in activities and "Gym / Strength" in activities:
            category = "Badminton + Gym"
        else:
            category = {frozenset({"Golf"}): "Golf-only", frozenset({"Gym / Strength"}): "Gym-only",
                        frozenset({"Walking"}): "Walking-only", frozenset(): "No recorded workouts"}.get(frozenset(activities), "Other mixed")
        result[category]["dates"].append(day)
    return result


def build_cases(rows, physiology):
    if len({r["workout_id"] for r in rows}) != len(rows):
        raise APIError("Duplicate workout UUID in response analysis.")
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["report_date"]].append(row)
    days = aggregate_days(rows)
    pairs = {p["workout_date"]: p for p in pair_next_day(days, physiology)}
    cases = []
    for day, all_rows in sorted(grouped.items()):
        badminton = [r for r in all_rows if r["canonical_activity"] == "Badminton"]
        if not badminton:
            continue
        D = date.fromisoformat(day)
        first_start = min(all_rows, key=lambda r: timestamp(r["start"]))["start"]
        baseline = pretraining_baselines(physiology, D, first_start)
        pair, daily = pairs[day], days[day]
        activities = set(daily["canonical_activity_counts"])
        recent = recent_training_context(D, grouped)
        current_offsets = {recorded_zone(o).utcoffset(None) for o in daily["utc_offsets"]}
        history_offsets = {recorded_zone(o).utcoffset(None) for m in baseline[14].values() for o in m["utc_offsets"]}
        recent_offsets = {recorded_zone(o).utcoffset(None) for r in recent for o in r["utc_offsets"]}
        travel = (len(current_offsets) > 1 or bool((history_offsets | recent_offsets) - current_offsets)
                  or any(o["offset_transition"] for o in pair["outcomes"]))
        flags = []
        if len(activities) > 1:
            flags.append("mixed_activity")
        if travel:
            flags.append("travel_or_offset_transition")
        for flag in ("overlap", "midnight_crossing"):
            if daily[flag]:
                flags.append(flag)
        for flag in ("offset_unknown", "chronology_unknown", "chronology_conflict"):
            if any(o[flag] for o in pair["outcomes"]):
                flags.append(flag)
        if any(m["conflicting_dates"] or m["multiple_entity_dates"] for m in baseline[14].values()):
            flags.append("baseline_observation_ambiguity")
        if any(m["chronology_unknown"] or m["chronology_excluded_count"] for m in baseline[14].values()):
            flags.append("baseline_chronology_issue")
        responses = {n: baseline_responses(metrics, pair) for n, metrics in baseline.items()}
        eligible = all(r["eligible"] for r in responses[14].values())
        reasons = sorted({reason for r in responses[14].values() if not r["eligible"] for reason in r["reasons"]})
        cases.append({"training_date": day, "exposure": badminton_exposure(badminton),
                      "context": {"canonical_activities": daily["canonical_activity_counts"],
                                  "gym_present": "Gym / Strength" in activities, "golf_present": "Golf" in activities,
                                  "walking_present": "Walking" in activities,
                                  "mixed_structured_training": len(activities - {"Walking", "Unknown"}) > 1,
                                  "utc_offsets": daily["utc_offsets"], "travel_or_offset_transition": travel},
                      "baselines": baseline, "pair": pair, "responses": responses, "recent_training": recent,
                      "primary_baseline_eligible": all(m["eligible"] for m in baseline[14].values()),
                      "primary_response_eligible": eligible, "quality_flags": flags,
                      "eligibility_reasons": reasons or ["eligible"],
                      "clean_case": eligible and activities == {"Badminton"} and not flags})
    return {"cases": cases, "reference_inventory": reference_inventory(grouped)}


def distribution(values):
    available = [v for v in values if v is not None]
    return {"count": len(available), "median": statistics.median(available) if available else None,
            "min": min(available) if available else None, "max": max(available) if available else None}


def descriptive_summaries(cases, clean_only=False):
    selected = [c for c in cases if c["clean_case"]] if clean_only else [c for c in cases if c["primary_response_eligible"]]
    exposures = {field: distribution(c["exposure"][field] for c in selected) for field in
                 ("record_count", "duration_seconds", "kilojoule", "duration_weighted_average_hr", "max_hr",
                  "zone45_milli", "zone45_percent", "strain_mean", "strain_median", "strain_max")}
    exposures.update({f: distribution(c["exposure"]["zone_totals_milli"][f] for c in selected) for f in ZONE_FIELDS})
    # Each metric keeps its own eligible sample even when other metrics are missing.
    responses = {m: distribution(c["responses"][14][m]["primary_response"] for c in cases
                                 if c["responses"][14][m]["eligible"] and (not clean_only or c["clean_case"])) for m in OUTCOMES}
    return {"exposures": exposures, "responses": responses}


def fmt(value, divisor=1):
    return "--" if value is None else f"{value / divisor:.2f}"


def format_report(report, clean_only=False):
    cases = report["cases"]
    complete = sum(len(c["pair"]["outcomes"]) == 1 and all(valid_metric(c["pair"]["outcomes"][0], m) is not None for m in OUTCOMES) for c in cases)
    lines = ["Badminton training-date response cases (descriptive only)",
             f"Dates: {len(cases)}; complete exact D+1: {complete}; 14d baseline eligible: {sum(c['primary_baseline_eligible'] for c in cases)}; "
             f"14d response eligible: {sum(c['primary_response_eligible'] for c in cases)}; clean eligible: {sum(c['clean_case'] for c in cases)}.",
             f"Mixed-activity dates: {sum(c['context']['gym_present'] or len(c['context']['canonical_activities']) > 1 for c in cases)}; "
             f"travel/offset flagged dates: {sum(c['context']['travel_or_offset_transition'] for c in cases)}.",
             "Baselines exclude D and D+1. Coverage order: HRV/RHR/Recovery/Sleep; required 10/14 each.",
             "HRV response is relative %; RHR is bpm; Recovery/Sleep are percentage points. -- means unavailable.",
             "Record counts are not real-world session counts. Coverage unknown; no-recorded-workout dates are not confirmed rest days.",
             "Date | min | Z4+5 min | Z4+5 % | Avg HR | Max HR | 14d n | HRV % | RHR bpm | Rec pp | Sleep pp | Flags/reasons"]
    for c in cases:
        if clean_only and not c["clean_case"]:
            continue
        e = c["exposure"]
        response = c["responses"][14]
        notes = c["quality_flags"] + [r for r in c["eligibility_reasons"] if r != "eligible"]
        lines.append(" | ".join([c["training_date"], fmt(e["duration_seconds"], 60), fmt(e["zone45_milli"], 60000),
                                  fmt(e["zone45_percent"]), fmt(e["duration_weighted_average_hr"]), fmt(e["max_hr"]),
                                  "/".join(str(c["baselines"][14][m]["valid_count"]) for m in OUTCOMES),
                                  *(fmt(response[m]["primary_response"]) for m in OUTCOMES), ", ".join(notes) or "eligible"]))
    summaries = descriptive_summaries(cases, clean_only)
    lines.append("Descriptive summaries: n; median [min, max]. Exposure sample requires all four responses; response n is metric-specific.")
    for group, metrics in summaries.items():
        for metric, stats in metrics.items():
            divisor = 1
            label = metric
            if group == "responses":
                label = {"hrv_ms": "HRV relative %", "resting_heart_rate_bpm": "RHR difference bpm",
                         "recovery_score": "Recovery difference pp", "sleep_performance": "Sleep difference pp"}[metric]
            elif metric == "duration_seconds":
                label, divisor = "duration minutes", 60
            elif metric.endswith("_milli"):
                label, divisor = metric.replace("_milli", " minutes"), 60000
            lines.append(f"  {group}/{label}: {stats['count']}; {fmt(stats['median'], divisor)} "
                         f"[{fmt(stats['min'], divisor)}, {fmt(stats['max'], divisor)}]")
    lines.append("Reference inventory only (unknown coverage): " + "; ".join(f"{k}: {len(v['dates'])}" for k, v in report["reference_inventory"].items()))
    lines.append("Mixed/travel cases are retained with flags. No causal attribution, combined response score, or training recommendations.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workouts", type=Path, default=DATA_DIR / "workouts.csv")
    parser.add_argument("--daily-metrics", type=Path, default=DATA_DIR / "daily_metrics.csv")
    parser.add_argument("--classifications", type=Path, default=CLASSIFICATION_FILE)
    parser.add_argument("--clean-only", action="store_true", help="Show only clean eligible cases and their distributions")
    args = parser.parse_args(argv)
    try:
        if not args.workouts.is_file():
            raise APIError("Workout dataset is missing.")
        rows = classify_workouts(read_workouts(args.workouts).values(), load_classifications(args.classifications))
        if not args.classifications.exists():
            print("No classification sidecar: personal corrections are not applied.")
        print(format_report(build_cases(rows, load_daily_metrics(args.daily_metrics)), args.clean_only))
    except (APIError, AnalysisError) as error:
        print("Badminton response analysis failed:", error)
        return 1
    except OSError:
        print("Badminton response analysis failed: cannot read local inputs.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
