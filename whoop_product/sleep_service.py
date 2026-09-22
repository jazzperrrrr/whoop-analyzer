"""Repackage SleepProductReport without reselecting a morning or calculating sleep."""

from dataclasses import fields
from .common import envelope, metric, timing, sleep_metric, quality_codes
from .models import Nap, SleepReport, SleepSummary, SleepMetrics, SleepTrends
from .trend_service import compose_trend


def sleep_metrics(product):
    return SleepMetrics(**{f.name: sleep_metric(product, f.name) for f in fields(SleepMetrics)})


def summary(product):
    values = sleep_metrics(product)
    return SleepSummary(product.status, timing(product.timing), values.actual_sleep_ms,
                        values.total_need_ms, values.performance_pct, values.efficiency_pct)


def compose_sleep(product):
    # Calendar scope uses each nap's recorded local end date, never host timezone.
    # This is a display scope, not attribution to the main sleep's need adjustment.
    naps = []
    for nap in product.naps:
        end = nap['timing'].get('local_end')
        if product.report_date is None or end is None or end.date() != product.report_date:
            continue
        m = nap['metrics']['actual_sleep_ms']
        pending = nap.get('score_state') == 'PENDING_SCORE'
        naps.append(Nap(timing(nap['timing']), metric(m.value, m.unit, m.origin, pending=pending,
                        reasons=('nap_not_scored' if pending else 'nap_value_unavailable',))))
    naps.sort(key=lambda n: n.timing.local_end, reverse=True)
    return SleepReport(product.report_date, product.status, timing(product.timing), sleep_metrics(product),
                       quality_codes(product.quality), naps,
                       SleepTrends(**{f.name: compose_trend(product, f.name) for f in fields(SleepTrends)}))


def get_sleep(repository):
    snapshot = repository.read_snapshot()
    product = snapshot.load_sleep_product()
    return envelope(snapshot, compose_sleep(product), product.report_date)
