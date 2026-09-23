import type { Health, Metric, SleepMetrics, SleepResponse, TodayResponse } from '../types/api-v1';
import { ApiFailure } from './errors';

// Small structural validators for the frozen V1 boundary, not a calculation engine.
type Check = (value: unknown) => boolean;
const text: Check = v => typeof v === 'string';
const nonempty: Check = v => typeof v === 'string' && v.trim().length > 0;
const finite: Check = v => typeof v === 'number' && Number.isFinite(v);
const bool: Check = v => typeof v === 'boolean';
const oneOf = (...values: unknown[]): Check => v => values.includes(v);
const nullable = (check: Check): Check => v => v === null || check(v);
const array = (check: Check): Check => v => Array.isArray(v) && v.every(check);
const record = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const object = (shape: Record<string, Check>): Check => v => record(v) && Object.entries(shape).every(([k, check]) => Object.hasOwn(v, k) && check(v[k]));
const codes = array(text);
const date: Check = v => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v;
const timestamp: Check = v => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(v) && date(v.slice(0, 10)) && Number.isFinite(Date.parse(v));
const day = nullable(date);
const number = nullable(finite);
const metric = (unit: Metric['unit']): Check => object({ value: number, unit: oneOf(unit), origin: oneOf('whoop', 'derived'), availability: oneOf('available', 'missing', 'pending', 'withheld'), reason_codes: codes });
const baseline = object({ window_days: oneOf(7, 14, 30), window_start: day, window_end: day, anchor_date: day,
  includes_anchor: oneOf(false), observed_days: finite, required_days: number, average: number, difference: number,
  percentage_deviation: number, eligible: nullable(bool), coverage_status: oneOf('full', 'partial', 'insufficient', 'unavailable') });
const timing = object({ local_start: nullable(timestamp), local_end: nullable(timestamp), recorded_offset: nullable(v => typeof v === 'string' && /^(?:Z|[+-]\d{2}:\d{2})$/.test(v)), recorded_interval_ms: number });
const status = oneOf('available', 'partial', 'unavailable', 'ambiguous');
const signal = object({ state: oneOf('positive', 'negative', 'neutral', 'insufficient data'), reason_codes: codes });
const physiology = (unit: Metric['unit']) => object({ metric: metric(unit), baselines: array(baseline) });
const summary = object({ status, timing, actual_sleep: metric('ms'), sleep_need: metric('ms'), sleep_performance: metric('percent'), sleep_efficiency: metric('percent') });
const today = object({ latest_available_morning: day,
  physiology: object({ recovery_score: physiology('percent'), hrv_ms: physiology('ms'), resting_heart_rate_bpm: physiology('bpm'), sleep_performance: physiology('percent') }),
  interpretation: object({ overall_state: oneOf('strong positive', 'strong negative', 'generally positive', 'generally negative', 'mixed', 'insufficient data'), window_days: oneOf(14),
    signals: object({ recovery_score: signal, hrv_ms: signal, resting_heart_rate_bpm: signal, sleep_performance: signal }), reason_codes: codes }),
  last_sleep: summary, quality_codes: codes,
  yesterday_training: object({ availability: oneOf('available', 'missing'), start_date: day, end_date: day, record_count: number, observed_dates: number, duration_ms: number, calendar_coverage: oneOf('unknown'),
    activities: array(object({ activity: text, record_count: finite, observed_dates: finite, duration_ms: number })) }),
});
const sleepMetrics: Record<keyof SleepMetrics, Check> = {
  in_bed_ms: metric('ms'), awake_ms: metric('ms'), no_data_ms: metric('ms'), light_ms: metric('ms'), deep_ms: metric('ms'), rem_ms: metric('ms'),
  baseline_need_ms: metric('ms'), sleep_debt_need_ms: metric('ms'), recent_strain_need_ms: metric('ms'), nap_adjustment_ms: metric('ms'),
  performance_pct: metric('percent'), efficiency_pct: metric('percent'), consistency_pct: metric('percent'), respiratory_rate: metric('breaths/min'), sleep_cycle_count: metric('count'), disturbance_count: metric('count'),
  actual_sleep_ms: metric('ms'), restorative_sleep_ms: metric('ms'), total_need_ms: metric('ms'), actual_minus_need_ms: metric('ms'),
  light_pct_actual_sleep: metric('percent'), deep_pct_actual_sleep: metric('percent'), rem_pct_actual_sleep: metric('percent'), restorative_pct_actual_sleep: metric('percent'), awake_pct_in_bed: metric('percent'),
};
const trendUnits = { actual_sleep: 'ms', sleep_need: 'ms', sleep_performance: 'percent', sleep_efficiency: 'percent', sleep_consistency: 'percent', respiratory_rate: 'breaths/min', deep_sleep: 'ms', rem_sleep: 'ms', restorative_sleep: 'ms' } as const;
const trends = Object.fromEntries(Object.entries(trendUnits).map(([name, unit]) => [name, object({ metric: oneOf(name), anchor_date: day, window_days: nullable(oneOf(7, 14, 30)),
  daily_points: array(object({ report_date: date, recorded_offsets: codes, metric: metric(unit) })), prior_windows: array(baseline) })]));
const sleep = object({ report_date: day, status, timing, metrics: object(sleepMetrics), quality_codes: codes, naps: array(object({ timing, actual_sleep: metric('ms') })), trends: object(trends) });
const envelope = (data: Check) => object({ schema_version: oneOf('v1'), analysis_version: nonempty, snapshot_id: nonempty, generated_at: timestamp,
  report_date: day, latest_available_morning: day, data,
  collection_freshness: object({ status: oneOf('unknown'), days_since_latest_available_morning: number, may_be_out_of_date: bool, collection_time_known: oneOf(false) }),
  last_successful_sync_at: nullable(timestamp),
});
const todayEnvelope = envelope(today);
const sleepEnvelope = envelope(sleep);
function requireShape(value: unknown, check: Check): void {
  if (!check(value)) throw new ApiFailure('invalid_response', false);
}
export function validateToday(value: unknown): TodayResponse { requireShape(value, todayEnvelope); return value as TodayResponse; }
export function validateSleep(value: unknown): SleepResponse { requireShape(value, sleepEnvelope); return value as SleepResponse; }
export function validateHealth(value: unknown): Health { requireShape(value, object({ schema_version: oneOf('v1'), status: oneOf('ok') })); return value as Health; }
export function validateError(value: unknown): { code: string; message: string; retryable: boolean; request_id: string } {
  // Unknown future codes are accepted here and mapped to safe generic copy, never displayed.
  requireShape(value, object({ code: nonempty, message: text, retryable: bool, request_id: nonempty }));
  return value as ReturnType<typeof validateError>;
}
