"""Expose existing descriptive sleep trends; never recompute their baselines."""

from datetime import timedelta
from .common import baseline, envelope, metric, sleep_metric
from .models import TrendPoint, TrendReport

METRICS = {'actual_sleep':'actual_sleep_ms', 'sleep_need':'total_need_ms',
           'sleep_performance':'performance_pct', 'sleep_efficiency':'efficiency_pct',
           'sleep_consistency':'consistency_pct', 'respiratory_rate':'respiratory_rate',
           'deep_sleep':'deep_ms', 'rem_sleep':'rem_ms', 'restorative_sleep':'restorative_sleep_ms'}
DERIVED = {'actual_sleep','sleep_need','restorative_sleep'}


class InvalidTrend(Exception):
    pass


def compose_trend(product, name, window_days=None):
    if name not in METRICS or window_days not in (None,7,14,30):
        raise InvalidTrend('Unsupported trend query.')
    source = product.trends.get(METRICS[name])
    points, windows = [], []
    if source:
        for point in source['daily']:
            if window_days is not None and not product.report_date-timedelta(days=window_days) <= point['report_date'] <= product.report_date:
                continue
            offset = point['recorded_offset']
            pending = point.get('score_state')=='PENDING_SCORE'
            value = (sleep_metric(product,METRICS[name]) if point['report_date']==product.report_date else
                     metric(point['value'], source['unit'], 'derived' if name in DERIVED else 'whoop',
                            pending=pending,reasons=('sleep_not_scored' if pending else 'historical_value_unavailable',)))
            points.append(TrendPoint(point['report_date'], offset.split(', ') if offset else [],value))
        windows = [baseline(source['windows'][n],product.report_date,n) for n in (7,14,30)
                   if window_days is None or window_days==n]
    return TrendReport(name, product.report_date, window_days, points, windows)


def get_trend(repository, name, anchor_date=None, window_days=14):
    if name not in METRICS or window_days not in (7,14,30):
        raise InvalidTrend('Unsupported trend query.')
    snapshot = repository.read_snapshot()
    product = snapshot.load_sleep_product(anchor_date)
    return envelope(snapshot, compose_trend(product,name,window_days), product.report_date)
