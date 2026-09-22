"""Today composes existing physiology, interpretation, sleep and training outputs."""

from dataclasses import fields
from .common import baseline, envelope, metric, quality_codes, SIGNAL_REASONS, OVERALL_REASONS
from .models import (ActivitySummary, Interpretation, InterpretationSignals, Physiology,
                     Signal, TodayReport, TodayPhysiology, TrainingSummary)
from .sleep_service import summary


def training(section, dataset_available):
    def milliseconds(value):
        return None if value is None else round(value*1000)
    available = dataset_available and section.get('start_date') is not None
    activities = [ActivitySummary(name,s['record_count'],s['distinct_training_dates'],milliseconds(s['duration_seconds']))
                  for name,s in section.get('activities',{}).items() if available and s['record_count']]
    return TrainingSummary('available' if available else 'missing',
                           section.get('start_date'),section.get('end_date'),
                           section.get('record_count',0) if available else None,
                           section.get('distinct_training_dates',0) if available else None,
                           milliseconds(section.get('duration_seconds')) if available else None,
                           'unknown',activities)


def get_today(repository):
    snapshot = repository.read_snapshot()
    report = snapshot.load_daily_report()
    physiology = {}
    for field in fields(TodayPhysiology):
        name = field.name
        current = report.physiology[name]
        blocked = report.selection['ambiguous_primary'] or bool(current['source_conflicts'])
        pending = not blocked and current['score_state']=='PENDING_SCORE'
        reasons = (['ambiguous_primary_sleep'] if report.selection['ambiguous_primary'] else
                   ['conflicting_measurements'] if blocked else
                   [('sleep' if name=='sleep_performance' else 'recovery')+'_not_scored'] if pending else
                   ['measurement_unavailable'])
        physiology[name] = Physiology(metric(current['value'],current['unit'],'whoop',
                                              withheld=blocked,pending=pending,reasons=reasons),
                                    [baseline(report.baseline[name][n],report.report_date,n) for n in (7,14,30)])
    state = report.interpretation
    interpretation = Interpretation(state['overall_state'], state['window_days'],
                     InterpretationSignals(**{f.name: Signal(state['signals'][f.name]['state'],
                         [r for r in state['signals'][f.name]['reason_codes'] if r in SIGNAL_REASONS])
                         for f in fields(InterpretationSignals)}),
                     [r for r in state['reason_codes'] if r in OVERALL_REASONS])
    product = snapshot.load_sleep_product()
    flags = [flag['code'] for flag in report.data_quality if flag['value'] is True]
    data = TodayReport(report.report_date,TodayPhysiology(**physiology),interpretation,summary(product),
                       training(snapshot.load_training_context(), 'missing_workout_dataset' not in flags),
                       quality_codes([*flags, *product.quality]))
    return envelope(snapshot,data,report.report_date)
