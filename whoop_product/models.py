"""Allowlisted mobile contracts. No domain entities, credentials or paths."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Generic, Literal, TypeVar

Availability = Literal['available', 'missing', 'pending', 'withheld']
Unit = Literal['ms', 'percent', 'bpm', 'breaths/min', 'count']
Window = Literal[7, 14, 30]
TrendName = Literal['actual_sleep', 'sleep_need', 'sleep_performance', 'sleep_efficiency',
                    'sleep_consistency', 'respiratory_rate', 'deep_sleep', 'rem_sleep', 'restorative_sleep']
Origin = Literal['whoop', 'derived']
Coverage = Literal['full', 'partial', 'insufficient', 'unavailable']
SignalState = Literal['positive', 'negative', 'neutral', 'insufficient data']
OverallState = Literal['strong positive', 'strong negative', 'generally positive',
                       'generally negative', 'mixed', 'insufficient data']
SleepStatus = Literal['available', 'partial', 'unavailable', 'ambiguous']
SCHEMA_VERSION = 'v1'
ANALYSIS_VERSION = 'phase5c-v1'
T = TypeVar('T')


@dataclass(frozen=True)
class Metric:
    value: int | float | None
    unit: Unit
    origin: Origin
    availability: Availability
    reason_codes: list[str]

    def __post_init__(self):
        if (self.availability == 'available') != (self.value is not None):
            raise ValueError('Metric value and availability disagree.')


@dataclass(frozen=True)
class Baseline:
    window_days: Window
    window_start: date | None
    window_end: date | None
    anchor_date: date | None
    includes_anchor: Literal[False]
    observed_days: int
    required_days: int | None
    average: float | None
    difference: float | None
    percentage_deviation: float | None
    eligible: bool | None
    coverage_status: Coverage


@dataclass(frozen=True)
class Freshness:
    status: Literal['unknown']
    days_since_latest_available_morning: int | None
    may_be_out_of_date: bool
    collection_time_known: Literal[False]


@dataclass(frozen=True)
class ReportEnvelope(Generic[T]):
    schema_version: Literal['v1']
    analysis_version: str
    snapshot_id: str
    generated_at: datetime
    report_date: date | None
    latest_available_morning: date | None
    collection_freshness: Freshness
    last_successful_sync_at: datetime | None
    data: T


@dataclass(frozen=True)
class Timing:
    local_start: datetime | None
    local_end: datetime | None
    recorded_offset: str | None
    recorded_interval_ms: int | None


@dataclass(frozen=True)
class SleepSummary:
    status: SleepStatus
    timing: Timing
    actual_sleep: Metric
    sleep_need: Metric
    sleep_performance: Metric
    sleep_efficiency: Metric


@dataclass(frozen=True)
class Physiology:
    metric: Metric
    baselines: list[Baseline]


@dataclass(frozen=True)
class TodayPhysiology:
    recovery_score: Physiology
    hrv_ms: Physiology
    resting_heart_rate_bpm: Physiology
    sleep_performance: Physiology


@dataclass(frozen=True)
class Signal:
    state: SignalState
    reason_codes: list[str]


@dataclass(frozen=True)
class InterpretationSignals:
    recovery_score: Signal
    hrv_ms: Signal
    resting_heart_rate_bpm: Signal
    sleep_performance: Signal


@dataclass(frozen=True)
class Interpretation:
    overall_state: OverallState
    window_days: Literal[14]
    signals: InterpretationSignals
    reason_codes: list[str]


@dataclass(frozen=True)
class ActivitySummary:
    activity: str
    record_count: int
    observed_dates: int
    duration_ms: int | None


@dataclass(frozen=True)
class TrainingSummary:
    availability: Literal['available', 'missing']
    start_date: date | None
    end_date: date | None
    record_count: int | None
    observed_dates: int | None
    duration_ms: int | None
    calendar_coverage: Literal['unknown']
    activities: list[ActivitySummary]


@dataclass(frozen=True)
class TodayReport:
    latest_available_morning: date | None
    physiology: TodayPhysiology
    interpretation: Interpretation
    last_sleep: SleepSummary
    yesterday_training: TrainingSummary
    quality_codes: list[str]


@dataclass(frozen=True)
class TrendPoint:
    report_date: date
    recorded_offsets: list[str]
    metric: Metric


@dataclass(frozen=True)
class TrendReport:
    metric: TrendName
    anchor_date: date | None
    window_days: Window | None
    daily_points: list[TrendPoint]
    prior_windows: list[Baseline]


@dataclass(frozen=True)
class Nap:
    timing: Timing
    actual_sleep: Metric


@dataclass(frozen=True)
class SleepMetrics:
    """Fixed V1 measurements, including explicit need and stage breakdowns."""
    in_bed_ms: Metric
    awake_ms: Metric
    no_data_ms: Metric
    light_ms: Metric
    deep_ms: Metric
    rem_ms: Metric
    baseline_need_ms: Metric
    sleep_debt_need_ms: Metric
    recent_strain_need_ms: Metric
    nap_adjustment_ms: Metric
    performance_pct: Metric
    efficiency_pct: Metric
    consistency_pct: Metric
    respiratory_rate: Metric
    sleep_cycle_count: Metric
    disturbance_count: Metric
    actual_sleep_ms: Metric
    restorative_sleep_ms: Metric
    total_need_ms: Metric
    actual_minus_need_ms: Metric
    light_pct_actual_sleep: Metric
    deep_pct_actual_sleep: Metric
    rem_pct_actual_sleep: Metric
    restorative_pct_actual_sleep: Metric
    awake_pct_in_bed: Metric


@dataclass(frozen=True)
class SleepTrends:
    actual_sleep: TrendReport
    sleep_need: TrendReport
    sleep_performance: TrendReport
    sleep_efficiency: TrendReport
    sleep_consistency: TrendReport
    respiratory_rate: TrendReport
    deep_sleep: TrendReport
    rem_sleep: TrendReport
    restorative_sleep: TrendReport


@dataclass(frozen=True)
class SleepReport:
    report_date: date | None
    status: SleepStatus
    timing: Timing
    metrics: SleepMetrics
    quality_codes: list[str]
    naps: list[Nap]
    trends: SleepTrends
