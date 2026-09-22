"""Contract adapters; all measurements and statistics come from domain outputs."""

from datetime import timedelta
from .models import (Metric, Baseline, Timing, Freshness, ReportEnvelope,
                     SCHEMA_VERSION, ANALYSIS_VERSION)


# This projection is deliberately independent of additions to domain diagnostics.
QUALITY_CODES = {
    'missing_primary_sleep': 'primary_sleep_unavailable',
    'source_snapshot_conflict': 'conflicting_measurements',
    'missing_matching_daily_snapshot': 'measurements_unverified',
    'daily_snapshot_unverified': 'measurements_unverified',
    'missing_workout_dataset': 'training_data_unavailable',
    'newer_incomplete_primary': 'newer_sleep_incomplete',
    'incomplete_order_unknown': 'sleep_chronology_uncertain',
    'duration_accounting_difference': 'sleep_duration_inconsistent',
    'invalid_history_interval': 'sleep_history_incomplete',
    'ambiguous_history_date': 'ambiguous_sleep_history',
    **{name: name for name in ('ambiguous_primary_sleep', 'ambiguous_physiology',
       'recent_offset_transition', 'mixed_offset_date', 'baseline_offset_transition',
       'sleep_not_scored', 'missing_sleep_components', 'unrecorded_duration_present')},
}
SIGNAL_REASONS = frozenset(('latest_value_unavailable', 'insufficient_baseline',
                          'comparison_undefined', 'above_baseline', 'below_baseline', 'near_baseline'))
OVERALL_REASONS = frozenset(('conflicting_signals', 'insufficient_signal_coverage',
                           'no_directional_signal', 'signals_unavailable'))


def quality_codes(codes):
    return sorted({QUALITY_CODES[code] for code in codes if code in QUALITY_CODES})


def metric(value, unit, origin, *, withheld=False, pending=False, reasons=()):
    availability = 'withheld' if withheld else 'pending' if pending else 'missing' if value is None else 'available'
    return Metric(None if availability != 'available' else value, 'percent' if unit=='%' else unit, origin, availability,
                  list(reasons) if availability != 'available' else [])


def sleep_metric(product, name):
    source = product.metrics[name]
    structural = product.status in ('ambiguous','unavailable')
    conflict = name=='performance_pct' and 'source_snapshot_conflict' in product.quality
    blocked = structural or conflict
    pending = not blocked and product.provenance.get('score_state')=='PENDING_SCORE'
    reason = ('ambiguous_primary_sleep' if product.status=='ambiguous' else
              'conflicting_measurements' if 'source_snapshot_conflict' in product.quality else
              'primary_sleep_unavailable') if blocked else (
              'sleep_not_scored' if pending else 'sleep_component_unavailable')
    withheld = blocked and (product.status=='ambiguous' or 'source_snapshot_conflict' in product.quality)
    return metric(source.value,source.unit,source.origin,withheld=withheld,pending=pending,reasons=(reason,))


def baseline(stats, anchor, window):
    observed = stats['days_available']
    required = stats.get('required_count')
    eligible = stats.get('eligible')
    coverage = ('unavailable' if not observed else 'full' if observed == window else
                'insufficient' if eligible is False else 'partial')
    return Baseline(window, stats.get('window_start', anchor-timedelta(days=window) if anchor else None),
                    stats.get('window_end', anchor-timedelta(days=1) if anchor else None), anchor, False,
                    observed, required, stats['average'], stats.get('difference'),
                    stats.get('percentage_deviation'), eligible, coverage)


def timing(source):
    return Timing(source.get('local_start'), source.get('local_end'), source.get('recorded_offset'),
                  source.get('recorded_interval_ms'))


def envelope(snapshot, data, report_date):
    metadata = snapshot.load_snapshot_metadata()
    f = snapshot.load_sleep_product().freshness
    return ReportEnvelope(SCHEMA_VERSION, ANALYSIS_VERSION, metadata.snapshot_id, metadata.generated_at,
                          report_date, metadata.latest_available_morning,
                          Freshness('unknown', f['days_since_latest_available_morning'], f['may_be_out_of_date'], False),
                          None, data)
