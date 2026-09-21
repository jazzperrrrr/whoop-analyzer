"""Local, descriptive morning report. Reads normalized inputs; never saves reports."""

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta, timezone
import math
from pathlib import Path

import whoop_analysis as analysis
from whoop_activity import classify_workouts, load_classifications
from whoop_badminton_response import badminton_exposure
from whoop_entities import DATA_DIR, read_entities, recorded_zone, timestamp
from whoop_fetch import APIError
from whoop_workout_analysis import aggregate_days, describe
from whoop_workouts import read_workouts


METRICS = tuple(analysis.SIGNAL_RULES)
ACTIVITIES = ("Badminton", "Gym / Strength", "Golf", "Walking")


@dataclass
class DailyReport:
    schema_version: str = "daily-report-v1"
    report_date: date | None = None
    selection: dict = field(default_factory=dict)
    physiology: dict = field(default_factory=dict)
    baseline: dict = field(default_factory=dict)
    interpretation: dict = field(default_factory=dict)
    sleep: dict = field(default_factory=dict)
    yesterday_training: dict = field(default_factory=dict)
    recent_training: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    data_quality: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


def _rows(table):
    return list(table.values()) if isinstance(table, dict) else list(table)


def _scored(entity, metric):
    if entity.get("score_state") != "SCORED":
        return None
    value = entity.get(metric)
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def select_report_morning(sleeps, recoveries=(), cycles=()):
    """Latest completed primary sleep by UTC end, regardless of score readiness.

    Ambiguous candidates are retained internally, never averaged or backfilled.
    Invalid/incomplete intervals are flagged even when an older anchor exists.
    """
    candidates, invalid, incomplete = [], 0, []
    for sleep in _rows(sleeps):
        if sleep.get("nap") not in (False, "false"):
            continue
        if not sleep.get("end"):
            try:
                known_start = timestamp(sleep.get("start")).astimezone(timezone.utc)
            except APIError:
                known_start = None
            incomplete.append(dict(sleep_id=sleep.get("sleep_id"), cycle_id=sleep.get("cycle_id"),
                                   start_utc=known_start, report_date=None, newer_than_completed=None))
            invalid += 1
            continue
        try:
            start, end = timestamp(sleep.get("start")), timestamp(sleep.get("end"))
            zone = recorded_zone(sleep.get("timezone_offset"))
            if end <= start or not sleep.get("sleep_id") or not sleep.get("cycle_id"):
                raise ValueError
            candidates.append((end.astimezone(timezone.utc), end.astimezone(zone).date(), sleep))
        except (APIError, ValueError):
            invalid += 1
    result = dict(method="latest_primary_sleep_utc_end", report_date=None,
                  status="unavailable", invalid_primary_count=invalid,
                  ambiguous_primary=False, ambiguous_physiology=False,
                  association_issue=False, chronology_issue=bool(invalid),
                  sleep=None, recovery=None, cycle=None, candidate_sleep_ids=[], candidate_cycle_ids=[], candidate_dates=[],
                  incomplete_candidates=incomplete, newer_incomplete_primary=None if incomplete else False,
                  fallback_status="no_completed_anchor")
    if not candidates:
        return result
    latest = max(c[0] for c in candidates)
    for candidate in incomplete:
        # A start at/after the completed wake instant proves this is newer.
        # An earlier/unknown start cannot establish the unknown wake chronology.
        if candidate["start_utc"] is not None and candidate["start_utc"] >= latest:
            candidate["newer_than_completed"] = True
    newer = True if any(c["newer_than_completed"] for c in incomplete) else None if incomplete else False
    result.update(newer_incomplete_primary=newer,
                  fallback_status="newer_incomplete_primary" if newer else "incomplete_order_unknown" if incomplete else "none")
    latest_candidates = [c for c in candidates if c[0] == latest]
    result["candidate_dates"] = sorted({c[1] for c in latest_candidates})
    if len(result["candidate_dates"]) > 1:
        result.update(status="ambiguous_date", ambiguous_primary=True,
                      candidate_sleep_ids=sorted({c[2]["sleep_id"] for c in latest_candidates}),
                      candidate_cycle_ids=sorted({c[2]["cycle_id"] for c in latest_candidates}))
        return result
    # Tie-breaking is deterministic but does not resolve the ambiguity.
    chosen = sorted((c for c in candidates if c[0] == latest), key=lambda c: c[2]["sleep_id"])[0]
    _, day, sleep = chosen
    # All valid completed primary sleeps on the selected local morning remain
    # current candidates, regardless of which record represents the ambiguity.
    peers = [c[2] for c in candidates if c[1] == day or c[0] == latest]
    related_recovery = [r for r in _rows(recoveries)
                        if r.get("cycle_id") == sleep["cycle_id"] or r.get("sleep_id") == sleep["sleep_id"]]
    exact = [r for r in related_recovery if r.get("cycle_id") == sleep["cycle_id"]
             and r.get("sleep_id") == sleep["sleep_id"]]
    matching_cycles = [c for c in _rows(cycles) if c.get("cycle_id") == sleep["cycle_id"]]
    association_issue = len(exact) != len(related_recovery)
    recovery = exact[0] if len(exact) == 1 and not association_issue else None
    cycle = matching_cycles[0] if len(matching_cycles) == 1 else None
    chronology = bool(invalid)
    if cycle:
        try:
            start = timestamp(cycle["start"])
            end = timestamp(cycle["end"]) if cycle.get("end") else None
            chronology |= start > latest or (end is not None and end < start)
        except APIError:
            chronology = True
    ambiguous = len(peers) > 1
    result.update(report_date=day, sleep=sleep, recovery=recovery, cycle=cycle,
                  status="ambiguous" if ambiguous else ("scored" if sleep.get("score_state") == "SCORED" else "pending_or_unscorable"),
                  ambiguous_primary=ambiguous,
                  ambiguous_physiology=len(related_recovery) > 1 or len(matching_cycles) > 1,
                  association_issue=association_issue, chronology_issue=chronology,
                  candidate_sleep_ids=sorted({s["sleep_id"] for s in peers}),
                  candidate_cycle_ids=sorted({s["cycle_id"] for s in peers}))
    return result


def _snapshot_consistency(selection, physiology):
    """Absence is unverified, not an assertion of missing morning physiology.

    Explicit contradictions with an entity archive withhold only affected metrics.
    Daily rows never supply replacement current values or choose the anchor date.
    """
    conflicts = {m: [] for m in METRICS}
    sleep = selection.get("sleep")
    if not sleep or selection["report_date"] is None:
        return dict(status="unavailable", conflicts=conflicts)
    sid, cid = sleep["sleep_id"], sleep["cycle_id"]
    related = [r for r in physiology if r.get("sleep_id") == sid or r.get("cycle_id") == cid]
    if not related:
        return dict(status="absent", conflicts=conflicts)
    exact = [r for r in related if r.get("sleep_id") == sid and r.get("cycle_id") == cid
             and r["report_date"] == selection["report_date"]]
    for metric in METRICS:
        is_sleep = metric == "sleep_performance"
        entity = selection.get("sleep" if is_sleep else "recovery") or {}
        identity = "sleep_id" if is_sleep else "cycle_id"
        for row in related:
            if is_sleep and row.get("sleep_id") != sid:
                continue  # Another sleep linked to this cycle does not replace this sleep.
            if row.get("sleep_id") != sid or row.get("cycle_id") != cid:
                conflicts[metric].append("entity_association")
            if row.get(identity) == sleep[identity] and row["report_date"] != selection["report_date"]:
                conflicts[metric].append("report_date")
        if len(exact) > 1:
            conflicts[metric].append("ambiguous_daily_observation")
        if len(exact) == 1:
            row = exact[0]
            prefix = "sleep" if is_sleep else "recovery"
            state = row.get(prefix + "_score_state") or None
            present = row.get(prefix + "_present")
            snapshot = _scored({"score_state": state, metric: row.get(metric)}, metric)
            if present in (False, "false"):
                snapshot = None
            if state != (entity.get("score_state") or None):
                conflicts[metric].append("score_state")
            if present in (False, "false") and entity:
                conflicts[metric].append("explicitly_missing_entity")
            if snapshot != _scored(entity, metric):
                conflicts[metric].append("metric_availability_or_value")
        conflicts[metric] = sorted(set(conflicts[metric]))
    return dict(status="conflict" if any(conflicts.values()) else "matched", conflicts=conflicts)


def build_physiology_section(selection, consistency=None):
    result = {}
    for metric in METRICS:
        source = selection.get("sleep" if metric == "sleep_performance" else "recovery") or {}
        reasons = (consistency or {}).get("conflicts", {}).get(metric, [])
        value = None if selection["ambiguous_primary"] or reasons else _scored(source, metric)
        result[metric] = dict(value=value, unit=analysis.METRICS[metric][1],
                              score_state=source.get("score_state"), available=value is not None,
                              source_conflicts=reasons)
    return result


def _baselines(physiology, selection, current):
    """Prepare scored inputs, then delegate all calendar weighting to analysis."""
    day = selection["report_date"]
    history, duplicate_dates, chronology = [], [], False
    identity_exclusions = {m: 0 for m in METRICS}
    if day:
        current_ids = {"sleep_id": set(selection["candidate_sleep_ids"]),
                       "cycle_id": set(selection["candidate_cycle_ids"])}
        counts = Counter(r["report_date"] for r in physiology if day - timedelta(days=30) <= r["report_date"] <= day)
        duplicate_dates = sorted(d.isoformat() for d, n in counts.items() if n > 1)
        for row in physiology:
            if not day - timedelta(days=30) <= row["report_date"] < day:
                continue
            if row.get("sleep_end") and timestamp(row["sleep_end"]) >= timestamp(selection["sleep"]["end"]):
                chronology = True
                continue
            clean = dict(row)
            for metric in analysis.METRICS:
                prefix = "sleep" if metric == "sleep_performance" else "cycle" if metric == "day_strain" else "recovery"
                source = {"score_state": row.get(prefix + "_score_state"), metric: row.get(metric)}
                clean[metric] = None if row.get(prefix + "_present") == "false" else _scored(source, metric)
                identity = "sleep_id" if metric == "sleep_performance" else "cycle_id"
                if row.get(identity) in current_ids[identity]:
                    # Count scored, available measurements actually removed by
                    # identity, not already-null values or whole calendar days.
                    if metric in identity_exclusions and clean[metric] is not None:
                        identity_exclusions[metric] += 1
                    clean[metric] = None
            history.append(clean)
        # Null anchor requests the existing algorithm's windows even if D is absent in CSV.
        anchor = {"report_date": day, "cycle_id": "report-anchor", "sleep_id": "report-anchor",
                  **{m: None for m in analysis.METRICS}}
        raw = analysis.rolling_baselines(history + [anchor])[day]
    else:
        raw = {m: {w: dict(average=None, days_available=0, window_days=w) for w in analysis.WINDOWS} for m in METRICS}
    entries = {}
    for metric in METRICS:
        windows = {}
        for window, stats in raw[metric].items():
            windows[window] = {**stats, **analysis.compare(current[metric]["value"], stats["average"]),
                               "window_start": day - timedelta(days=window) if day else None,
                               "window_end": day - timedelta(days=1) if day else None,
                               "required_count": analysis.MIN_BASELINE_DAYS,
                               "eligible": stats["days_available"] >= analysis.MIN_BASELINE_DAYS}
        entries[metric] = {"value": current[metric]["value"], "baselines": windows}
    return entries, duplicate_dates, chronology, history, identity_exclusions


def build_sleep_context(selection):
    sleep = selection.get("sleep")
    if not sleep:
        return {}
    zone = recorded_zone(sleep["timezone_offset"])
    start, end = timestamp(sleep["start"]), timestamp(sleep["end"])
    return dict(start_utc=start.astimezone(timezone.utc), end_utc=end.astimezone(timezone.utc),
                local_start=start.astimezone(zone), local_end=end.astimezone(zone),
                recorded_offset=sleep["timezone_offset"],
                recorded_interval_minutes=(end - start).total_seconds() / 60,
                ambiguous=selection["ambiguous_primary"],
                sleep_performance=None if selection["ambiguous_primary"] else _scored(sleep, "sleep_performance"))


def _summary(rows):
    summary = describe(rows)
    summary.pop("arithmetic_session_strain_sum", None)
    return summary


def build_training_context(rows, day):
    """Use canonical records and existing aggregations, with explicit bounds."""
    if day is None:
        return {}, {}
    first, last = day - timedelta(days=3), day - timedelta(days=1)
    selected = [r for r in rows if first.isoformat() <= r["report_date"] <= last.isoformat()]
    daily = aggregate_days(selected)

    def context(records, start, end):
        result = _summary(records)
        result.update(start_date=start, end_date=end,
                      activities={name: _summary([r for r in records if r["canonical_activity"] == name])
                                  for name in sorted(set(ACTIVITIES) | {r["canonical_activity"] for r in records})},
                      badminton=badminton_exposure([r for r in records if r["canonical_activity"] == "Badminton"]))
        return result

    yesterday_rows = [r for r in selected if r["report_date"] == last.isoformat()]
    yesterday = context(yesterday_rows, last, last)
    yesterday["mixed_activity"] = daily.get(last.isoformat(), {}).get("mixed_activity")
    yesterday["daily_aggregation"] = {k: v for k, v in daily.get(last.isoformat(), {}).items()
                                       if k != "arithmetic_session_strain_sum"}
    return yesterday, context(selected, first, last)


def detect_offset_context(selection, physiology, workouts, baseline_rows=()):
    day = selection["report_date"]
    if day is None:
        return dict(recent_offset_transition=None, baseline_offset_transition=None,
                    mixed_offset_dates=[], mixed_offset_date=None)
    recent, baseline = defaultdict(set), set()
    events = []

    def add(d, offset, instant=None):
        if not offset:
            return
        # Compare numeric offsets; Z and +00:00 are the same offset.
        seconds = recorded_zone(offset).utcoffset(None).total_seconds()
        if day - timedelta(days=3) <= d <= day:
            recent[d].add(seconds)
            if instant:
                events.append((timestamp(instant), seconds))

    for row in physiology:
        d = row["report_date"]
        for prefix in ("sleep", "cycle"):
            offset = row.get(prefix + "_timezone_offset")
            add(d, offset, row.get(prefix + ("_end" if prefix == "sleep" else "_start")))
    # These rows have already passed scoring, date, chronology and identity filters.
    # Recovery has no independent offset; retain its sleep/cycle context only when
    # a recovery metric actually contributes. Sleep-only rows use sleep's offset.
    for row in baseline_rows:
        if not day - timedelta(days=14) <= row["report_date"] < day:
            continue
        prefixes = set()
        if row.get("sleep_performance") is not None:
            prefixes.add("sleep")
        if any(row.get(m) is not None for m in METRICS if m != "sleep_performance"):
            prefixes.update(("sleep", "cycle"))
        for prefix in prefixes:
            offset = row.get(prefix + "_timezone_offset")
            if offset:
                baseline.add(recorded_zone(offset).utcoffset(None).total_seconds())
    for prefix in ("sleep", "cycle"):
        entity = selection.get(prefix) or {}
        add(day, entity.get("timezone_offset"), entity.get("end" if prefix == "sleep" else "start"))
    for row in workouts:
        add(date.fromisoformat(row["report_date"]), row.get("timezone_offset"), row.get("start"))
    events.sort()
    offsets = {v for values in recent.values() for v in values}
    transition = any(a[1] != b[1] for a, b in zip(events, events[1:]))
    return dict(recent_offset_transition=(transition or len(offsets) > 1) if offsets else None,
                baseline_offset_transition=len(baseline) > 1 if baseline else None,
                mixed_offset_date=any(len(v) > 1 for v in recent.values()) if recent else None,
                mixed_offset_dates=[d.isoformat() for d, values in sorted(recent.items()) if len(values) > 1],
                observation_coverage="unknown; no transition observed does not establish no travel")


def build_daily_report(sleeps, recoveries, cycles, physiology, workouts):
    """Compose an in-memory report. Inputs are never mutated; workouts are classified."""
    selection = select_report_morning(sleeps, recoveries, cycles)
    consistency = _snapshot_consistency(selection, physiology)
    current = build_physiology_section(selection, consistency)
    entries, duplicates, chronology, baseline_rows, identity_exclusions = _baselines(physiology, selection, current)
    interpretation = analysis.interpret_record({"metrics": entries})
    yesterday, recent = build_training_context(workouts, selection["report_date"])
    context = detect_offset_context(selection, physiology, workouts, baseline_rows)
    context["mixed_activity_yesterday"] = yesterday.get("mixed_activity")
    cycle = selection.get("cycle") or {}
    quality = []

    def flag(code, value, detail):
        quality.append(dict(code=code, value=value, detail=detail))

    for metric, entry in entries.items():
        b = entry["baselines"][14]
        flag("baseline_" + metric, b["eligible"], f"{b['days_available']}/14 observations; need {analysis.MIN_BASELINE_DAYS}")
        flag("missing_or_unscored_" + metric, not current[metric]["available"], "Scored current value required; no substitution.")
    flag("missing_primary_sleep", not bool(selection["sleep"]), "No valid primary-sleep anchor." if not selection["sleep"] else "Primary sleep available.")
    flag("ambiguous_primary_sleep", selection["ambiguous_primary"], "Multiple primary-sleep candidates; current physiology withheld.")
    flag("ambiguous_physiology", selection["ambiguous_physiology"] or bool(duplicates), "Duplicate entities or daily observations remain explicit.")
    flag("unfinished_cycle", not bool(cycle.get("end")) if cycle else None, "Completion reflects stored snapshot, not live state.")
    flag("recent_offset_transition", context["recent_offset_transition"], "Recorded offsets only; no location inference.")
    flag("mixed_offset_date", context["mixed_offset_date"], "Multiple recorded offsets on a recent date.")
    flag("baseline_offset_transition", context["baseline_offset_transition"], "Offset changes do not invalidate baseline automatically.")
    flag("unknown_workout_coverage", True, "No recorded workouts does not establish rest.")
    flag("source_freshness_limitation", True, "Latest available morning only; collection time and live completeness unknown.")
    flag("chronology_or_association_issue", selection["chronology_issue"] or selection["association_issue"] or chronology,
         "Invalid/incomplete intervals, mismatched entities or future baseline observations were excluded/flagged.")
    conflict_names = [analysis.METRICS[m][0] for m, reasons in consistency["conflicts"].items() if reasons]
    flag("source_snapshot_conflict", bool(conflict_names),
         "Source snapshot conflict; withheld current metrics: " + ", ".join(conflict_names))
    flag("missing_matching_daily_snapshot", consistency["status"] == "absent",
         "No matching daily snapshot row; scored entity values are unverified, not backfilled from other dates.")
    flag("current_identity_excluded_from_baseline", any(identity_exclusions.values()),
         "Current-morning candidate identities found on prior dates; affected available baseline measurements excluded.")
    sleep_context = build_sleep_context(selection)
    if sleep_context:
        sleep_context["sleep_performance"] = current["sleep_performance"]["value"]
        sleep_context["performance_baseline_14d"] = entries["sleep_performance"]["baselines"][14]
    return DailyReport(report_date=selection["report_date"], selection=selection, physiology=current,
                       baseline={m: e["baselines"] for m, e in entries.items()}, interpretation=interpretation,
                       sleep=sleep_context, yesterday_training=yesterday, recent_training=recent,
                       context=context, data_quality=quality,
                       provenance=dict(duplicate_daily_dates=duplicates, snapshot_consistency=consistency,
                                       baseline_identity_exclusions=identity_exclusions,
                                       sleep_id=(selection["sleep"] or {}).get("sleep_id"), cycle_id=cycle.get("cycle_id"),
                                       source_updated_at={k: (selection.get(k) or {}).get("updated_at") for k in ("sleep", "recovery", "cycle")},
                                       cycle_strain=dict(value=_scored(cycle, "day_strain"), provisional=not bool(cycle.get("end")) if cycle else None)))


def _number(value, divisor=1, signed=False):
    return "N/A" if value is None else (f"{value / divisor:+.1f}" if signed else f"{value / divisor:.1f}")


def format_daily_report(report):
    """Allowlisted presentation; never print internal IDs or arbitrary provenance."""
    lines = ["WHOOP DAILY REPORT", f"Latest available morning: {report.report_date or 'unavailable'}",
             "\nPHYSIOLOGICAL STATE", "Metric                Current       14d baseline   Deviation"]
    if report.selection["status"] == "ambiguous_date":
        lines.insert(2, "Ambiguous wake-up dates: " + ", ".join(map(str, report.selection["candidate_dates"]))
                     + "; date-dependent windows unavailable.")
    fallback = report.selection["fallback_status"]
    morning_message = "Latest completed morning shown" if report.report_date is not None else "No completed morning selected"
    if fallback == "newer_incomplete_primary":
        lines.insert(2, morning_message + "; newer incomplete primary sleep observed. Its wake-up date is unknown.")
    elif fallback == "incomplete_order_unknown":
        lines.insert(2, morning_message + "; incomplete primary sleep chronology is unknown. No wake-up date inferred.")
    for metric in ("recovery_score", "hrv_ms", "resting_heart_rate_bpm", "sleep_performance"):
        label, unit, _ = analysis.METRICS[metric]
        b = report.baseline[metric][14]
        delta_key = "percentage_deviation" if metric == "hrv_ms" else "difference"
        delta_unit = "%" if metric == "hrv_ms" else "bpm" if metric == "resting_heart_rate_bpm" else "pp"
        lines.append(f"{label:<22}{_number(report.physiology[metric]['value']) + ' ' + unit:<14}"
                     f"{_number(b['average']) + ' ' + unit:<15}{_number(b[delta_key], signed=True)} {delta_unit}")
    lines.append("State: " + report.interpretation["overall_state"])
    lines.extend(report.interpretation["explanations"])
    lines.append("Signals: " + "; ".join(f"{analysis.METRICS[m][0]} {s['state']}" for m, s in report.interpretation["signals"].items()))
    lines.append("\nLAST SLEEP")
    if report.sleep:
        s = report.sleep
        lines += [f"Local interval: {s['local_start']:%Y-%m-%d %H:%M} to {s['local_end']:%Y-%m-%d %H:%M} ({s['recorded_offset']})",
                  f"Recorded sleep interval: {_number(s['recorded_interval_minutes'])} min" + (" (ambiguous candidate)" if s['ambiguous'] else ""),
                  f"Sleep Performance: {_number(s['sleep_performance'])}%"]
    else:
        lines.append("Primary sleep unavailable; no morning date inferred.")
    for heading, section in (("YESTERDAY", report.yesterday_training), ("PREVIOUS 3 DAYS", report.recent_training)):
        lines.append("\n" + heading)
        if not section:
            lines.append("Unavailable without a primary-sleep report date.")
            continue
        lines.append(f"{section['start_date']} through {section['end_date']}")
        if not section["record_count"]:
            lines.append("No recorded workouts.")
        else:
            for name, activity in section["activities"].items():
                if activity["record_count"]:
                    lines.append(f"{name}: {activity['distinct_training_dates']} training dates; "
                                 f"{_number(activity['duration_seconds'], 60)} recorded min; {activity['record_count']} WHOOP records")
            badminton = section["badminton"]
            available, count = badminton["zone45_record_count"], badminton["record_count"]
            coverage = f"; available records: {available}/{count}"
            if available < count:
                coverage += " (all missing)" if available == 0 else " (partial coverage)"
            lines += [f"Badminton Z4+5: {_number(badminton['zone45_milli'], 60000)} min{coverage}",
                      f"Total: {section['record_count']} WHOOP records; {_number(section['duration_seconds'], 60)} recorded min"]
        lines.append("Collection coverage unknown; no rest days inferred. Records are not confirmed sessions.")
    lines.append("\nCONTEXT / DATA QUALITY")
    for metric in METRICS:
        b = report.baseline[metric][14]
        lines.append(f"14d {analysis.METRICS[metric][0]} baseline: {b['days_available']}/14; "
                     + ("eligible" if b["eligible"] else "insufficient coverage"))
    quality = {f["code"]: f for f in report.data_quality}
    lines.append("Primary sleep: " + report.selection["status"])
    lines.append("Morning metrics: " + "; ".join(
        f"{analysis.METRICS[m][0]} {'scored' if p['available'] else 'missing/unscored/ambiguous/conflicting'}"
        for m, p in report.physiology.items()))
    unfinished = quality["unfinished_cycle"]["value"]
    lines.append("Cycle: " + ("unknown" if unfinished is None else "unfinished in stored snapshot" if unfinished else "complete in stored snapshot"))
    for key, label in (("recent_offset_transition", "Recent offset transition"),
                       ("baseline_offset_transition", "14d baseline offset transition")):
        value = report.context[key]
        text = "unknown" if value is None else "yes" if value else "no transition observed"
        lines.append(f"{label}: {text}")
    mixed = report.context["mixed_activity_yesterday"]
    offset_mixed = report.context["mixed_offset_date"]
    lines.append("Mixed-offset date: " + ("unknown" if offset_mixed is None else "yes" if offset_mixed else "no, among observed offsets"))
    lines.append("Mixed activity yesterday: " + ("unknown" if mixed is None else "yes" if mixed else "no, among recorded workouts"))
    lines.append("Offsets do not establish location or prove absence of travel. Workout coverage: unknown.")
    for flag in report.data_quality:
        if flag["value"] and flag["code"] in {"ambiguous_primary_sleep", "ambiguous_physiology", "mixed_offset_date",
                                               "chronology_or_association_issue", "missing_classification_sidecar", "missing_workout_dataset",
                                               "source_snapshot_conflict", "missing_matching_daily_snapshot", "current_identity_excluded_from_baseline"}:
            lines.append(f"Flag: {flag['code'].replace('_', ' ')}. {flag['detail']}")
    lines.append("Latest available local snapshot only; collection time and live completeness unknown.")
    return "\n".join(lines)


def load_daily_report(data_dir=DATA_DIR):
    """Read local inputs for CLI and dashboard without changing source data."""
    root = Path(data_dir)
    entities = {kind: read_entities(root / (kind + ".csv"), kind) for kind in ("sleeps", "recoveries", "cycles")}
    physiology = analysis.load_daily_metrics(root / "daily_metrics.csv")
    classified = classify_workouts(read_workouts(root / "workouts.csv").values(),
                                   load_classifications(root / "workout_classifications.csv"))
    report = build_daily_report(**entities, physiology=physiology, workouts=classified)
    if not (root / "workout_classifications.csv").exists():
        report.data_quality.append(dict(code="missing_classification_sidecar", value=True, detail="Original labels only."))
    if not (root / "workouts.csv").exists():
        report.data_quality.append(dict(code="missing_workout_dataset", value=True, detail="No workout archive available."))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Normalized local datasets (read only)")
    args = parser.parse_args(argv)
    try:
        report = load_daily_report(args.data_dir)
        print(format_daily_report(report))
    except (APIError, analysis.AnalysisError, OSError, ValueError):
        print("Daily report unavailable: invalid or unreadable local inputs; no files changed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
