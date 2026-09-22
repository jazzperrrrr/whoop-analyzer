"""Allowlisted mobile contracts. No domain entities, credentials or paths."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Generic, Literal, TypeVar

Availability = Literal['available', 'missing', 'pending', 'withheld']
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
    unit: str
    origin: Origin
    availability: Availability
    reason_codes: list[str]


@dataclass(frozen=True)
class Baseline:
    window_days: int
    window_start: date | None
    window_end: date | None
    anchor_date: date | None
    includes_anchor: bool
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
    collection_time_known: bool = False


@dataclass(frozen=True)
class ReportEnvelope(Generic[T]):
    schema_version: str
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
class Signal:
    state: SignalState
    explanation: str


@dataclass(frozen=True)
class Interpretation:
    overall_state: OverallState
    signals: dict[str, Signal]
    explanations: list[str]


@dataclass(frozen=True)
class ActivitySummary:
    activity: str
    record_count: int
    observed_dates: int
    duration_ms: int | None


@dataclass(frozen=True)
class TrainingSummary:
    start_date: date | None
    end_date: date | None
    record_count: int
    observed_dates: int
    duration_ms: int | None
    calendar_coverage: Literal['unknown']
    activities: list[ActivitySummary]


@dataclass(frozen=True)
class TodayReport:
    latest_available_morning: date | None
    physiology: dict[str, Physiology]
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
    metric: str
    anchor_date: date | None
    window_days: int | None
    daily_points: list[TrendPoint]
    prior_windows: list[Baseline]


@dataclass(frozen=True)
class Nap:
    timing: Timing
    metrics: dict[str, Metric]


@dataclass(frozen=True)
class SleepReport:
    report_date: date | None
    status: SleepStatus
    timing: Timing
    metrics: dict[str, Metric]
    quality_codes: list[str]
    naps: list[Nap]
    trends: dict[str, TrendReport]
