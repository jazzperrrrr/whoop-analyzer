"""Read-only sleep presentation model; selection belongs to DailyReport."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from whoop_analysis import prior_window_stats, daily_observations
from whoop_entities import recorded_zone, sleep_report_date, timestamp
from whoop_fetch import APIError
import whoop_sleep as sleep


@dataclass(frozen=True)
class Measurement:
    value: int | float | None
    unit: str
    origin: str
    source_fields: tuple[str, ...]


@dataclass
class SleepProductReport:
    report_date: date | None
    status: str
    timing: dict
    metrics: dict[str, Measurement]
    freshness: dict
    trends: dict = field(default_factory=dict)
    naps: list = field(default_factory=list)
    quality: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


SOURCE_FIELDS = {
    'in_bed_ms': ('total_in_bed_time_ms', 'ms'),
    'awake_ms': ('total_awake_time_ms', 'ms'),
    'no_data_ms': ('total_no_data_time_ms', 'ms'),
    'light_ms': ('total_light_sleep_time_ms', 'ms'),
    'deep_ms': ('total_slow_wave_sleep_time_ms', 'ms'),
    'rem_ms': ('total_rem_sleep_time_ms', 'ms'),
    'baseline_need_ms': ('baseline_sleep_need_ms', 'ms'),
    'sleep_debt_need_ms': ('sleep_debt_need_ms', 'ms'),
    'recent_strain_need_ms': ('recent_strain_need_ms', 'ms'),
    'nap_adjustment_ms': ('recent_nap_need_ms', 'ms'),
    'performance_pct': ('sleep_performance', 'percent'),
    'efficiency_pct': ('sleep_efficiency', 'percent'),
    'consistency_pct': ('sleep_consistency', 'percent'),
    'respiratory_rate': ('respiratory_rate', 'breaths/min'),
    'sleep_cycle_count': ('sleep_cycle_count', 'count'),
    'disturbance_count': ('disturbance_count', 'count'),
}
TREND_METRICS = ('actual_sleep_ms', 'total_need_ms', 'performance_pct', 'efficiency_pct',
                 'consistency_pct', 'respiratory_rate', 'deep_ms', 'rem_ms', 'restorative_sleep_ms')


def freshness(report_date, today=None):
    today = today or date.today()
    age = (today - report_date).days if report_date else None
    return {'computer_local_date': today, 'days_since_latest_available_morning': age,
            'may_be_out_of_date': age is not None and age > 0,
            'collection_time_known': False}


def measurements(row):
    """Source values and complete-operand derivations; zeros remain observations."""
    scored = row.get('score_state') == 'SCORED'
    clean = row if scored else {}
    result = {}
    for name, (source, unit) in SOURCE_FIELDS.items():
        value = clean.get(source)
        if name == 'performance_pct' and value is not None:
            value = float(value)
        result[name] = Measurement(value, unit, 'whoop', (source,))

    def derived(name, value, operands, unit='ms'):
        result[name] = Measurement(value, unit, 'derived', tuple(operands))

    actual = sleep.actual_sleep_ms(clean)
    restorative = sleep.restorative_sleep_ms(clean)
    need = sleep.derived_total_sleep_need_ms(clean)
    derived('actual_sleep_ms', actual, sleep.ACTUAL_FIELDS)
    derived('restorative_sleep_ms', restorative, sleep.RESTORATIVE_FIELDS)
    derived('total_need_ms', need, sleep.NEED_FIELDS.values())
    derived('actual_minus_need_ms', None if actual is None or need is None else actual - need,
            ('actual_sleep_ms', 'total_need_ms'))
    for name, numerator, denominator in (
        ('light_pct_actual_sleep', 'light_ms', 'actual_sleep_ms'),
        ('deep_pct_actual_sleep', 'deep_ms', 'actual_sleep_ms'),
        ('rem_pct_actual_sleep', 'rem_ms', 'actual_sleep_ms'),
        ('restorative_pct_actual_sleep', 'restorative_sleep_ms', 'actual_sleep_ms'),
        ('awake_pct_in_bed', 'awake_ms', 'in_bed_ms'),
    ):
        top, bottom = result[numerator].value, result[denominator].value
        value = None if top is None or bottom is None or bottom <= 0 else 100 * top / bottom
        derived(name, value, (numerator, denominator), 'percent')
    return result


def timing(row):
    if not row:
        return {}
    zone = recorded_zone(row['timezone_offset'])
    start, end = timestamp(row['start']), timestamp(row['end'])
    return {'local_start': start.astimezone(zone), 'local_end': end.astimezone(zone),
            'recorded_offset': row['timezone_offset'],
            'recorded_interval_ms': round((end - start).total_seconds() * 1000)}


def compose_sleep_product(report, sleeps, today=None):
    """Consume the already selected morning and its source consistency outcome."""
    selection = report.selection
    selected = selection.get('sleep')
    consistency = report.provenance.get('snapshot_consistency', {})
    conflicts = consistency.get('conflicts', {}).get('sleep_performance', [])
    structural = set(conflicts) - {'metric_availability_or_value'}
    blocked = not selected or selection['ambiguous_primary'] or bool(structural)
    row = {} if blocked else selected
    metrics = measurements(row)
    # Reuse the report's availability/consistency decision for this official score.
    official = report.physiology['sleep_performance']['value']
    metrics['performance_pct'] = Measurement(None if blocked else official, 'percent', 'whoop', ('sleep_performance',))
    status = 'ambiguous' if selection['ambiguous_primary'] else 'unavailable' if blocked else (
        'available' if all(m.value is not None for m in metrics.values()) else 'partial')
    product = SleepProductReport(report.report_date, status, timing(row), metrics,
                                 freshness(report.report_date, today))
    if selection['ambiguous_primary']:
        product.quality.append('ambiguous_primary_sleep')
    if conflicts:
        product.quality.append('source_snapshot_conflict')
    if consistency.get('status') == 'absent':
        product.quality.append('daily_snapshot_unverified')
    if selection.get('fallback_status') in ('newer_incomplete_primary', 'incomplete_order_unknown'):
        product.quality.append(selection['fallback_status'])
    if row and row.get('score_state') != 'SCORED':
        product.quality.append('sleep_not_scored')
    if row and any(row.get(f) is None for f in sleep.EXTENDED_FIELDS):
        product.quality.append('missing_sleep_components')
    if row.get('total_no_data_time_ms'):
        product.quality.append('unrecorded_duration_present')
    residual = sleep.duration_accounting_residual_ms(row)
    if residual not in (None, 0):
        product.quality.append('duration_accounting_difference')
    if report.context.get('baseline_offset_transition'):
        product.quality.append('baseline_offset_transition')
    product.provenance = {'sleep_id': (selected or {}).get('sleep_id'),
                          'score_state': (selected or {}).get('score_state'),
                          'source_updated_at': (selected or {}).get('updated_at'),
                          'accounting_residual_ms': residual,
                          'efficiency_diagnostics': sleep.efficiency_diagnostics(row)}
    rows = list(sleeps.values()) if isinstance(sleeps, dict) else list(sleeps)
    grouped = defaultdict(list)
    for candidate in rows:
        try:
            day = date.fromisoformat(sleep_report_date(candidate))
            if timestamp(candidate['end']) <= timestamp(candidate['start']):
                continue
            if candidate['nap'] in ('true', True):
                product.naps.append({'timing': timing(candidate), 'metrics': measurements(candidate)})
            elif candidate['nap'] in ('false', False):
                grouped[day].append(candidate)
        except (APIError, KeyError, ValueError):
            product.quality.append('invalid_history_interval')
    product.naps.sort(key=lambda nap: nap['timing']['local_end'], reverse=True)
    anchor = report.report_date
    if anchor is None:
        return product
    current_ids = set(selection['candidate_sleep_ids'])
    observations = []
    for day, candidates in sorted(grouped.items()):
        if not anchor - timedelta(days=30) <= day <= anchor:
            continue
        # Multiple primary sleeps on a date stay ambiguous, never averaged together.
        if len(candidates) != 1:
            product.quality.append('ambiguous_history_date')
            continue
        candidate = candidates[0]
        if day < anchor and (candidate['sleep_id'] in current_ids or (
                selected and timestamp(candidate['end']) >= timestamp(selected['end']))):
            continue
        values = metrics if day == anchor else measurements(candidate)
        observations.append({'report_date': day, 'recorded_offset': candidate['timezone_offset'], 'metrics': values})
    for name in TREND_METRICS:
        points = [{'report_date': o['report_date'], 'recorded_offset': o['recorded_offset'],
                   'value': o['metrics'][name].value} for o in observations]
        daily_values = {point['report_date']: point['value'] for point in points}
        product.trends[name] = {'unit': metrics[name].unit, 'daily': points,
                               'windows': {n: prior_window_stats(daily_values, anchor, n) for n in (7, 14, 30)}}
    # Performance shares the report's filtered, equal-calendar-date observations
    # and already computed windows, including multiple identities on one date.
    performance_days = daily_observations(report.baseline_observations)
    points = []
    for day, values in sorted(performance_days.items()):
        offsets = sorted({r['sleep_timezone_offset'] for r in report.baseline_observations
                          if r['report_date'] == day and r.get('sleep_timezone_offset')})
        points.append({'report_date': day, 'recorded_offset': ', '.join(offsets) or None,
                       'value': values['sleep_performance']})
    points.append({'report_date': anchor, 'recorded_offset': row.get('timezone_offset'),
                   'value': metrics['performance_pct'].value})
    product.trends['performance_pct'] = {'unit': 'percent', 'daily': points,
                                        'windows': {n: dict(report.baseline['sleep_performance'][n])
                                                    for n in (7, 14, 30)}}
    product.quality = sorted(set(product.quality))
    return product
