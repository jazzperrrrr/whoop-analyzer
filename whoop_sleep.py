"""Pure sleep measurement mapping and diagnostics; no I/O or source corrections."""

import math


STAGE_FIELDS = {
    "total_in_bed_time_milli": "total_in_bed_time_ms",
    "total_awake_time_milli": "total_awake_time_ms",
    "total_no_data_time_milli": "total_no_data_time_ms",
    "total_light_sleep_time_milli": "total_light_sleep_time_ms",
    "total_slow_wave_sleep_time_milli": "total_slow_wave_sleep_time_ms",
    "total_rem_sleep_time_milli": "total_rem_sleep_time_ms",
    "sleep_cycle_count": "sleep_cycle_count",
    "disturbance_count": "disturbance_count",
}
NEED_FIELDS = {
    "baseline_milli": "baseline_sleep_need_ms",
    "need_from_sleep_debt_milli": "sleep_debt_need_ms",
    "need_from_recent_strain_milli": "recent_strain_need_ms",
    "need_from_recent_nap_milli": "recent_nap_need_ms",
}
SCORE_FIELDS = {
    "sleep_efficiency_percentage": "sleep_efficiency",
    "sleep_consistency_percentage": "sleep_consistency",
    "respiratory_rate": "respiratory_rate",
}
INTEGER_FIELDS = (*STAGE_FIELDS.values(), *NEED_FIELDS.values())
EXTENDED_FIELDS = (*INTEGER_FIELDS, *SCORE_FIELDS.values())
ACTUAL_FIELDS = ("total_light_sleep_time_ms", "total_slow_wave_sleep_time_ms", "total_rem_sleep_time_ms")
RESTORATIVE_FIELDS = ACTUAL_FIELDS[1:]
ACCOUNTING_FIELDS = ("total_awake_time_ms", *ACTUAL_FIELDS, "total_no_data_time_ms")


def measurement(field, value):
    """Validate without rounding milliseconds or treating missing values as zero."""
    if value is None:
        return None
    if field in INTEGER_FIELDS:
        valid = type(value) is int
    else:
        valid = type(value) in (int, float) and math.isfinite(value)
    if not valid or (field != "recent_nap_need_ms" and value < 0):
        raise ValueError("Invalid sleep measurement.")
    return value if field in INTEGER_FIELDS else float(value)


def normalize_sleep_measurements(record):
    """Extract only supplied, scored measurements from the nested WHOOP response."""
    result = dict.fromkeys(EXTENDED_FIELDS)
    if record.get("score_state") != "SCORED":
        return result
    score = record.get("score")
    # Preserve the existing behavior for absent/non-object scores.
    if not isinstance(score, dict):
        return result
    for group, fields in (("stage_summary", STAGE_FIELDS), ("sleep_needed", NEED_FIELDS), (None, SCORE_FIELDS)):
        source = score if group is None else score.get(group)
        if source is None:
            continue
        if not isinstance(source, dict):
            raise ValueError("Invalid sleep measurement group.")
        for original, field in fields.items():
            result[field] = measurement(field, source.get(original))
    return result


def _complete_sum(row, fields):
    values = [measurement(field, row.get(field)) for field in fields]
    return None if any(value is None for value in values) else sum(values)


def actual_sleep_ms(row):
    """Derived light + slow-wave + REM; unavailable if any stage is missing."""
    return _complete_sum(row, ACTUAL_FIELDS)


def restorative_sleep_ms(row):
    """Derived slow-wave + REM, kept separate from official scores."""
    return _complete_sum(row, RESTORATIVE_FIELDS)


def derived_total_sleep_need_ms(row):
    """Add all four components, including the signed recent-nap contribution."""
    return _complete_sum(row, tuple(NEED_FIELDS.values()))


def duration_accounting_residual_ms(row):
    """In-bed minus all stages, awake and no-data; never correct the source."""
    in_bed = measurement("total_in_bed_time_ms", row.get("total_in_bed_time_ms"))
    accounted = _complete_sum(row, ACCOUNTING_FIELDS)
    return None if in_bed is None or accounted is None else in_bed - accounted


def efficiency_diagnostics(row):
    """Candidate ratios and official-minus-derived percentage-point differences.

    These are validation values, not a reproduction of WHOOP's algorithm.
    Nonpositive denominators and missing operands produce None.
    """
    actual = actual_sleep_ms(row)
    in_bed = measurement("total_in_bed_time_ms", row.get("total_in_bed_time_ms"))
    no_data = measurement("total_no_data_time_ms", row.get("total_no_data_time_ms"))
    official = measurement("sleep_efficiency", row.get("sleep_efficiency"))
    recorded = None if in_bed is None or no_data is None else in_bed - no_data
    result = {"official_efficiency_pct": official}
    for name, denominator in (("in_bed", in_bed), ("excluding_no_data", recorded)):
        ratio = None if actual is None or denominator is None or denominator <= 0 else 100 * actual / denominator
        result[name + "_derived_pct"] = ratio
        result[name + "_difference_pp"] = None if official is None or ratio is None else official - ratio
    return result
