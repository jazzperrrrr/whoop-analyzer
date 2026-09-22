"""Repackage SleepProductReport without reselecting a morning or calculating sleep."""

from .common import envelope, metric, timing, sleep_metric
from .models import Nap, SleepReport, SleepSummary
from .trend_service import METRICS, compose_trend


def sleep_metrics(product):
    return {name:sleep_metric(product,name) for name in product.metrics}


def summary(product):
    values = sleep_metrics(product)
    return SleepSummary(product.status, timing(product.timing), values['actual_sleep_ms'],
                        values['total_need_ms'], values['performance_pct'], values['efficiency_pct'])


def compose_sleep(product):
    naps = [Nap(timing(n['timing']), {name:metric(m.value,m.unit,m.origin,
                                    pending=n.get('score_state')=='PENDING_SCORE',
                                    reasons=('nap_not_scored' if n.get('score_state')=='PENDING_SCORE' else 'nap_value_unavailable',))
                                    for name,m in n['metrics'].items()}) for n in product.naps]
    return SleepReport(product.report_date, product.status, timing(product.timing), sleep_metrics(product),
                       list(product.quality), naps, {name:compose_trend(product,name) for name in METRICS})


def get_sleep(repository):
    snapshot = repository.read_snapshot()
    product = snapshot.load_sleep_product()
    return envelope(snapshot, compose_sleep(product), product.report_date)
