"""Read-only, deterministic personal baselines from the daily WHOOP report."""

import argparse
import csv
from datetime import date
import math
from pathlib import Path


DEFAULT_INPUT = Path(__file__).resolve().parent / "data" / "daily_metrics.csv"
WINDOWS = (7, 14, 30)
METRICS = {
    "hrv_ms": ("HRV", "ms", "cycle_id"),
    "resting_heart_rate_bpm": ("Resting heart rate", "bpm", "cycle_id"),
    "recovery_score": ("Recovery", "%", "cycle_id"),
    "sleep_performance": ("Sleep performance", "%", "sleep_id"),
    "day_strain": ("Day strain", "/21", "cycle_id"),
}
REQUIRED = {"report_date", "cycle_id", "sleep_id", *METRICS}

# Descriptive project policy, not clinical cutoffs. Boundaries are inclusive.
INTERPRETATION_WINDOW = 14
MIN_BASELINE_DAYS = 7
SIGNAL_RULES = {
    "hrv_ms": {"comparison": "percentage_deviation", "threshold": 10, "direction": 1, "unit": "%"},
    "resting_heart_rate_bpm": {"comparison": "difference", "threshold": 3, "direction": -1, "unit": "bpm"},
    "recovery_score": {"comparison": "difference", "threshold": 10, "direction": 1, "unit": "percentage points"},
    "sleep_performance": {"comparison": "difference", "threshold": 5, "direction": 1, "unit": "percentage points"},
}


def classify_signal(metric, entry):
    """Classify one measurement independently against its prior 14-day mean."""
    rule = SIGNAL_RULES[metric]
    stats = entry["baselines"][INTERPRETATION_WINDOW]
    coverage = f"{stats['days_available']}/{INTERPRETATION_WINDOW} prior days"
    if entry["value"] is None:
        return {"state": "insufficient data", "explanation": f"latest value missing; {coverage}"}
    if stats["days_available"] < MIN_BASELINE_DAYS or stats["average"] is None:
        return {"state": "insufficient data", "explanation":
                f"insufficient baseline: {coverage}; need {MIN_BASELINE_DAYS}; no prior observations" if not stats["days_available"] else
                f"insufficient baseline: {coverage}; need {MIN_BASELINE_DAYS}"}
    change = stats[rule["comparison"]]
    if change is None or not math.isfinite(change):
        return {"state": "insufficient data", "explanation": f"comparison undefined (zero baseline or numeric limit); {coverage}"}
    directed = change * rule["direction"]
    state = "positive" if directed >= rule["threshold"] else "negative" if directed <= -rule["threshold"] else "neutral"
    return {"state": state, "explanation":
            f"{change:+.2f} {rule['unit']} vs 14-day mean {stats['average']:.2f}; {coverage}"}


def interpret_record(record):
    """Keep physiology, sleep, and WHOOP Recovery distinct; never vote with strain.

    Both physiological signals plus at least one context signal are required.
    Opposing directions always remain mixed. Strong requires all four to agree;
    neutral-only is mixed with an explicit no-direction explanation.
    """
    signals = {metric: classify_signal(metric, record["metrics"][metric]) for metric in SIGNAL_RULES}
    states = [signal["state"] for signal in signals.values()]
    positive = [METRICS[m][0] for m, s in signals.items() if s["state"] == "positive"]
    negative = [METRICS[m][0] for m, s in signals.items() if s["state"] == "negative"]
    explanations = []
    if positive and negative:
        explanations.append(f"Conflicting signals: {', '.join(positive)} positive; {', '.join(negative)} negative. Directions are not averaged away.")
    if (any(signals[m]["state"] == "insufficient data" for m in ("hrv_ms", "resting_heart_rate_bpm"))
            or states.count("insufficient data") > 1):
        overall = "insufficient data"
        explanations.append("Overall requires both physiological signals and at least one context signal with sufficient baseline coverage.")
    elif positive and negative:
        overall = "mixed"
    elif len(positive) == 4:
        overall = "strong positive"
    elif len(negative) == 4:
        overall = "strong negative"
    elif positive:
        overall = "generally positive"
    elif negative:
        overall = "generally negative"
    else:
        overall = "mixed"
        explanations.append("All classified signals are near baseline; there is no clear positive or negative direction.")
    if "insufficient data" in states:
        explanations.append("Unavailable signals remain unclassified; no values are substituted.")
    return {"overall_state": overall, "signals": signals, "explanations": explanations}


class AnalysisError(Exception):
    """Safe errors omit file contents, measurements, and arbitrary input values."""


def load_daily_metrics(path=DEFAULT_INPUT):
    """Read the normalized schema, retaining IDs and the existing report date.

    Extra metadata columns are accepted. Empty measurements stay None; malformed
    numbers and duplicate identities fail explicitly instead of biasing a mean.
    """
    try:
        with Path(path).open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file, strict=True)
            header = reader.fieldnames
            if not header or len(header) != len(set(header)) or not REQUIRED <= set(header):
                raise AnalysisError("Daily metrics CSV has missing or duplicate columns.")
            rows, seen = [], set()
            for line, raw in enumerate(reader, start=2):
                try:
                    if set(raw) != set(header) or any(v is None for v in raw.values()):
                        raise ValueError
                    day = date.fromisoformat(raw["report_date"])
                    if day.isoformat() != raw["report_date"]:
                        raise ValueError
                    cid, sid = raw["cycle_id"], raw["sleep_id"]
                    if not cid.isascii() or not cid.isdigit() or str(int(cid)) != cid or int(cid) <= 0:
                        raise ValueError
                    if not sid or sid.strip() != sid or any(not (c.isascii() and (c.isalnum() or c == "-")) for c in sid):
                        raise ValueError
                    key = (cid, sid)
                    if key in seen:
                        raise ValueError
                    seen.add(key)
                    row = dict(raw, report_date=day)
                    for metric in METRICS:
                        value = raw[metric].strip()
                        row[metric] = float(value) if value else None
                        if row[metric] is not None and (not math.isfinite(row[metric]) or row[metric] < 0):
                            raise ValueError
                    rows.append(row)
                except (ValueError, OverflowError):
                    raise AnalysisError(f"Invalid daily metrics row {line}; check dates, IDs and numeric values.") from None
    except OSError:
        raise AnalysisError("Cannot read daily metrics CSV. Check that it exists and is readable.") from None
    except (csv.Error, UnicodeError):
        raise AnalysisError("Cannot parse daily metrics CSV. Check CSV format and UTF-8 encoding.") from None
    return sorted(rows, key=lambda r: (r["report_date"], int(r["cycle_id"]), r["sleep_id"]))


def daily_observations(rows):
    """Mean per reporting date/metric, deduplicating repeated cycle measurements.

    Entity rows are never changed or removed. These means are analytical summaries,
    not synthesized WHOOP cycles, recovery scores, or daily strain totals.
    """
    grouped = {}
    for row in rows:
        day = grouped.setdefault(row["report_date"], {metric: {} for metric in METRICS})
        for metric, (_, _, identity) in METRICS.items():
            value = row[metric]
            if value is None:
                continue
            values = day[metric]
            key = row[identity]
            if key in values and values[key] != value:
                raise AnalysisError("Conflicting measurements for the same entity and report date.")
            values[key] = value
    return {day: {metric: (math.fsum(v / len(values) for _, v in sorted(values.items())) if values else None)
                  for metric, values in metrics.items()} for day, metrics in grouped.items()}


def prior_window_stats(observations, day, window):
    """Descriptive mean of observed calendar dates in [D-window, D)."""
    values = [observations[d] for d in sorted(observations)
              if 0 < (day - d).days <= window and observations[d] is not None]
    return {"average": math.fsum(v / len(values) for v in values) if values else None,
            "days_available": len(values), "window_days": window}


def rolling_baselines(rows):
    """Return all dates' means/counts for [report_date - N days, report_date)."""
    observations = daily_observations(rows)
    result = {}
    for day in sorted(observations):
        result[day] = {}
        for metric in METRICS:
            result[day][metric] = {}
            for window in WINDOWS:
                result[day][metric][window] = prior_window_stats(
                    {d: values[metric] for d, values in observations.items()}, day, window)
    return result


def compare(value, baseline):
    """Relative deviation is undefined for a zero baseline or missing operands."""
    if value is None or baseline is None:
        return {"difference": None, "percentage_deviation": None}
    difference = value - baseline
    percentage = difference / baseline * 100 if baseline != 0 else None
    return {"difference": difference,
            "percentage_deviation": percentage if percentage is not None and math.isfinite(percentage) else None}


def latest_report(rows):
    if not rows:
        return None
    latest = max(row["report_date"] for row in rows)
    baselines = rolling_baselines(rows)[latest]
    records = []
    for row in sorted(rows, key=lambda r: (int(r["cycle_id"]), r["sleep_id"])):
        if row["report_date"] != latest:
            continue
        metrics = {}
        for metric in METRICS:
            metrics[metric] = {"value": row[metric], "baselines": {
                window: {**stats, **compare(row[metric], stats["average"])}
                for window, stats in baselines[metric].items()}}
        record = {"cycle_id": row["cycle_id"], "sleep_id": row["sleep_id"],
                        "cycle_in_progress": "cycle_end" in row and not row["cycle_end"]
                        and row.get("cycle_present") != "false", "metrics": metrics}
        record["interpretation"] = interpret_record(record)
        records.append(record)
    return {"report_date": latest, "records": records}


def format_report(report, detailed=False):
    if report is None:
        return "No daily metrics rows are available for analysis."
    lines = [f"WHOOP daily analysis - {report['report_date'].isoformat()} (latest available date)",
             "Baselines use previous calendar days only; missing values are excluded.",
             "Each available day has equal weight. Coverage below shows available days/window days.",
             f"Rules: 14-day baseline; minimum {MIN_BASELINE_DAYS} observed days per signal. Descriptive, non-clinical labels."]
    for metric, rule in SIGNAL_RULES.items():
        positive_direction = "increase" if rule["direction"] == 1 else "decrease"
        lines.append(f"  {METRICS[metric][0]}: positive at {positive_direction} >= {rule['threshold']} {rule['unit']}; negative at opposite change >= threshold; otherwise neutral.")
    lines.append("Overall: both physiological signals + at least one context required; all four agreeing = strong; one direction with neutral/missing = generally; opposing or all neutral = mixed.")
    for record in report["records"]:
        lines.append(f"\nCycle {record['cycle_id']} | Sleep {record['sleep_id']}")
        interpretation = record["interpretation"]
        lines.append(f"Overall state: {interpretation['overall_state']}")
        for heading, names in (("Physiological signals", ("hrv_ms", "resting_heart_rate_bpm")),
                               ("Sleep context", ("sleep_performance",)),
                               ("WHOOP recovery context (separate signal)", ("recovery_score",))):
            lines.append(heading + ":")
            for metric in names:
                signal = interpretation["signals"][metric]
                value = record["metrics"][metric]["value"]
                display = "N/A (missing)" if value is None else f"{value:.2f} {METRICS[metric][1]}"
                lines.append(f"  {METRICS[metric][0]}: {display} - {signal['state']}; {signal['explanation']}")
        strain = record["metrics"]["day_strain"]["value"]
        display = "N/A (missing)" if strain is None else f"{strain:.2f} /21"
        lines.append(f"Current load - Day strain: {display}; excluded from overall state and morning readiness.")
        if record["cycle_in_progress"]:
            lines.append("Cycle is in progress; current strain is provisional.")
        lines.extend(interpretation["explanations"])
        if not detailed:
            continue
        for metric, (label, unit, _) in METRICS.items():
            entry = record["metrics"][metric]
            value = entry["value"]
            lines.append(f"{label}: " + ("N/A (missing)" if value is None else f"{value:.2f} {unit}"))
            for window, stats in entry["baselines"].items():
                average = stats["average"]
                coverage = f"{stats['days_available']}/{window} days"
                if average is None:
                    lines.append(f"  {window}-day: N/A ({coverage}); no prior observations")
                    continue
                text = f"  {window}-day: {average:.2f} {unit} ({coverage})"
                if stats["days_available"] < window:
                    text += " [partial]"
                difference = stats["difference"]
                if difference is None:
                    text += "; comparison N/A (latest value missing)"
                else:
                    delta_unit = "percentage points" if unit == "%" else ("strain units" if metric == "day_strain" else unit)
                    text += f"; difference {difference:+.2f} {delta_unit}"
                    relative = stats["percentage_deviation"]
                    text += f"; relative {relative:+.2f}%" if relative is not None else "; relative N/A (zero baseline or numeric limit)"
                lines.append(text)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Daily metrics CSV (read only)")
    parser.add_argument("--details", action="store_true", help="Include all 7-, 14-, and 30-day numeric comparisons")
    args = parser.parse_args(argv)
    try:
        print(format_report(latest_report(load_daily_metrics(args.input)), detailed=args.details))
    except AnalysisError as error:
        print("WHOOP analysis failed:", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
