"""Today composes existing physiology, interpretation, sleep and training outputs."""

from .common import baseline, envelope, metric
from .models import (ActivitySummary, Interpretation, Physiology, Signal, TodayReport, TrainingSummary)
from .sleep_service import summary


def training(section):
    def milliseconds(value):
        return None if value is None else round(value*1000)
    activities = [ActivitySummary(name,s['record_count'],s['distinct_training_dates'],milliseconds(s['duration_seconds']))
                  for name,s in section.get('activities',{}).items()]
    return TrainingSummary(section.get('start_date'),section.get('end_date'),section.get('record_count',0),
                           section.get('distinct_training_dates',0),milliseconds(section.get('duration_seconds')),
                           'unknown',activities)


def get_today(repository):
    snapshot = repository.read_snapshot()
    report = snapshot.load_daily_report()
    physiology = {}
    for name in ('recovery_score','hrv_ms','resting_heart_rate_bpm','sleep_performance'):
        current = report.physiology[name]
        blocked = report.selection['ambiguous_primary'] or bool(current['source_conflicts'])
        pending = not blocked and current['score_state']=='PENDING_SCORE'
        reasons = (['ambiguous_primary_sleep'] if report.selection['ambiguous_primary'] else
                   ['source_snapshot_conflict'] if blocked else
                   [('sleep' if name=='sleep_performance' else 'recovery')+'_not_scored'] if pending else
                   ['source_value_unavailable'])
        physiology[name] = Physiology(metric(current['value'],current['unit'],'whoop',
                                              withheld=blocked,pending=pending,reasons=reasons),
                                    [baseline(report.baseline[name][n],report.report_date,n) for n in (7,14,30)])
    state = report.interpretation
    interpretation = Interpretation(state['overall_state'],
                     {name:Signal(s['state'],s['explanation']) for name,s in state['signals'].items()},
                     list(state['explanations']))
    data = TodayReport(report.report_date,physiology,interpretation,summary(snapshot.load_sleep_product()),
                       training(snapshot.load_training_context()),
                       [flag['code'] for flag in report.data_quality if flag['value'] is True])
    return envelope(snapshot,data,report.report_date)
